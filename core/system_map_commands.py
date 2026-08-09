"""Atomic graph edits for multi-selection, clipboard and local undo/redo.

The view locks the Map and checks base_revision before dispatching here. Numeric
IDs never authorize cross-map edits; deleted IDs can only be revived from this
Map's own server-side history, preserving legacy metadata not exposed in the UI.
"""
import json
import math
from uuid import uuid4

from django.core.validators import URLValidator
from django.db import transaction

from .colors import valid_palette_color
from .models import RelationshipEdge, RelationshipGroup, RelationshipNode
from .relationship_history import save_relationship_map_revision


def _identity(value):
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        raise ValueError("Every map object needs a non-zero integer ID.")
    return value


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Invalid {label}; maximum length is {limit}.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label} is required.")
    return value


def _number(value, label, minimum=-20000, maximum=20000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {label}.")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside the map limits.")
    return float(value)


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"Invalid {label}.")
    return value


def _url(value):
    value = _text(value, "URL", 500)
    if value:
        URLValidator(schemes=["http", "https"])(value)
    return value


@transaction.atomic
def save_system_map_document(relationship_map, actor, raw_document, label="Edit map"):
    if not isinstance(raw_document, str) or len(raw_document.encode("utf-8")) > 2_000_000:
        raise ValueError("Map document is too large.")
    try:
        document = json.loads(raw_document)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid map document.") from error
    if not isinstance(document, dict):
        raise ValueError("Invalid map document.")
    map_name = _text(document.get("name", relationship_map.name), "Map name", 120, True)
    map_description = document.get("description", relationship_map.description)
    if map_description != relationship_map.description:
        map_description = _text(map_description, "Map description", 10000)
    if type(relationship_map).objects.exclude(pk=relationship_map.pk).filter(name__iexact=map_name).exists():
        raise ValueError("A Map with this name already exists.")

    collections = {}
    models = {"groups": RelationshipGroup, "nodes": RelationshipNode, "edges": RelationshipEdge}
    limits = {"groups": 200, "nodes": 2000, "edges": 6000}
    current = {key: {item.pk: item for item in getattr(relationship_map, key).all()} for key in models}
    historical = {key: {} for key in models}
    for key, maximum in limits.items():
        items = document.get(key)
        if not isinstance(items, list) or (len(items) > maximum and len(items) > len(current[key])) or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"Invalid {key} collection.")
        ids = [_identity(item.get("id")) for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {key} IDs.")
        collections[key] = {item["id"]: item for item in items}
        missing = {identity for identity in ids if identity > 0 and identity not in current[key]}
        if missing:
            if models[key].objects.filter(pk__in=missing).exists():
                raise ValueError("An object belongs to another System Map.")
            for revision in relationship_map.revisions.order_by("-created_at", "-pk"):
                snapshot = revision.snapshot
                if not isinstance(snapshot, dict):
                    continue
                for item in snapshot.get(key, []):
                    if isinstance(item, dict) and item.get("id") in missing:
                        historical[key].setdefault(item["id"], item)
                if missing.issubset(historical[key]):
                    break
            if not missing.issubset(historical[key]):
                raise ValueError("A deleted object cannot be restored without this Map's history.")

    def prior(key, identity, field, default=None):
        obj = current[key].get(identity)
        return getattr(obj, field, default) if obj else historical[key].get(identity, {}).get(field, default)

    # Clipboard provenance survives deletion, but only this Map's server-side
    # snapshots may authorize legacy colors and metadata (never browser fields).
    copied_sources = {key: {} for key in models}
    for key, items in collections.items():
        requests = {identity: item for identity, item in items.items()
                    if identity < 0 and "_copied_from" in item}
        wanted = {_identity(item["_copied_from"]) for item in requests.values()}
        if models[key].objects.filter(pk__in=wanted - current[key].keys()).exists():
            raise ValueError("Copied objects must come from this Map.")
        fallback = {}

        def visible_values_match(item, candidate):
            # Geometry/group IDs and copied Group names deliberately change on
            # Paste. Hidden metadata is never accepted as browser authority.
            fields = {"nodes": ("name", "description", "color", "external_url"),
                      "groups": ("color",), "edges": ("direction", "line_style")}[key]
            return all(str(item.get(field, "")).upper() == str(candidate.get(field, "")).upper()
                       if field == "color" else item.get(field, "") == candidate.get(field, "")
                       for field in fields)

        for identity, item in requests.items():
            requested_revision = item.get("_copied_revision")
            if requested_revision is not None and (not isinstance(requested_revision, str) or len(requested_revision) > 80):
                raise ValueError("Invalid clipboard revision.")
            obj = current[key].get(item["_copied_from"])
            if obj is not None:
                candidate = {field.attname: getattr(obj, field.attname) for field in obj._meta.concrete_fields}
                if requested_revision == relationship_map.updated_at.isoformat() or (not requested_revision and visible_values_match(item, candidate)):
                    copied_sources[key][identity] = candidate
                elif visible_values_match(item, candidate):
                    fallback[identity] = candidate
        if len(copied_sources[key]) != len(requests):
            historical_fallback = set()
            for revision in relationship_map.revisions.order_by("-created_at", "-pk").iterator(chunk_size=10):
                snapshot = revision.snapshot
                if not isinstance(snapshot, dict):
                    continue
                candidates = {candidate.get("id"): candidate for candidate in snapshot.get(key, []) if isinstance(candidate, dict)}
                snapshot_revision = (snapshot.get("map") or {}).get("revision")
                for identity, item in requests.items():
                    if identity in copied_sources[key]:
                        continue
                    candidate = candidates.get(item["_copied_from"])
                    if candidate is None:
                        continue
                    requested_revision = item.get("_copied_revision")
                    if requested_revision and requested_revision == snapshot_revision:
                        copied_sources[key][identity] = candidate
                    elif visible_values_match(item, candidate):
                        if not requested_revision:
                            copied_sources[key][identity] = candidate
                        elif identity not in historical_fallback:
                            fallback[identity] = candidate
                            historical_fallback.add(identity)
                if len(copied_sources[key]) == len(requests):
                    break
        for identity, candidate in fallback.items():
            copied_sources[key].setdefault(identity, candidate)
        if len(copied_sources[key]) != len(requests):
            raise ValueError("Copied objects must come from this Map's available history.")

    def copied_source(key, identity, item):
        return copied_sources[key].get(identity) if identity < 0 else None

    def color_before(key, identity, item):
        if identity > 0:
            return prior(key, identity, "color")
        source_id = item.get("_copied_from")
        if source_id is None:
            return None
        source = copied_source(key, identity, item)
        if source is None:
            raise ValueError("Copied objects must come from this Map.")
        return source["color"]

    groups = {}
    names = set()
    for identity, item in collections["groups"].items():
        name = _text(item.get("name"), "Group name", 120, True)
        if name.casefold() in names:
            raise ValueError("Group names must be unique within the Map.")
        names.add(name.casefold())
        groups[identity] = {
            "name": name,
            "color": valid_palette_color(item.get("color"), current=color_before("groups", identity, item)),
            "x": _number(item.get("x"), "Group x"),
            "y": _number(item.get("y"), "Group y"),
            "width": _number(item.get("width"), "Group width", 220, 8000),
            "height": _number(item.get("height"), "Group height", 140, 8000),
            "is_collapsed": _boolean(item.get("is_collapsed", False), "collapsed state"),
        }
    nodes = {}
    for identity, item in collections["nodes"].items():
        group_id = item.get("group_id")
        if group_id is not None and (_identity(group_id) not in groups):
            raise ValueError("Select a Group from this Map.")
        description = item.get("description", "")
        copied_node = copied_source("nodes", identity, item)
        previous_description = copied_node.get("description", "") if copied_node else prior("nodes", identity, "description", "")
        if description != previous_description:
            description = _text(description, "Description", 10000)
        elif not isinstance(description, str):
            raise ValueError("Invalid Description.")
        node = {
            "name": _text(item.get("name"), "Node name", 160, True),
            "description": description,
            "external_url": _url(item.get("external_url", "")),
            "color": valid_palette_color(item.get("color"), current=color_before("nodes", identity, item)),
            "x": _number(item.get("x"), "Node x"),
            "y": _number(item.get("y"), "Node y"),
            "keep_position": _boolean(item.get("keep_position", False), "Keep position"),
            "group_id": group_id,
        }
        if group_id is not None:
            group = groups[group_id]
            if not (group["x"] <= node["x"] and group["y"] + 42 <= node["y"]
                    and node["x"] + 190 <= group["x"] + group["width"]
                    and node["y"] + 76 <= group["y"] + group["height"]):
                raise ValueError("Every grouped Node must remain inside its Group.")
        nodes[identity] = node

    edges = {}
    pairs = {}
    for identity, item in collections["edges"].items():
        source, target = _identity(item.get("source_id")), _identity(item.get("target_id"))
        if source not in nodes or target not in nodes or source == target:
            raise ValueError("Connections need two different Nodes from this Map.")
        pair = tuple(sorted((source, target)))
        copied_edge = copied_source("edges", identity, item)
        if identity < 0 and item.get("_copied_from") is not None and copied_edge is None:
            raise ValueError("Copied connections must come from this Map.")
        old_pair = tuple(sorted((prior("edges", identity, "source_id", 0), prior("edges", identity, "target_id", 0))))
        unchanged_pair = identity > 0 and old_pair == pair
        if copied_edge:
            if source >= 0 or target >= 0:
                raise ValueError("Copied connections must join newly copied Nodes.")
            original_pair = tuple(sorted((collections["nodes"][source].get("_copied_from", source), collections["nodes"][target].get("_copied_from", target))))
            unchanged_pair = original_pair == tuple(sorted((copied_edge["source_id"], copied_edge["target_id"])))
            if not unchanged_pair:
                raise ValueError("Copied connection endpoints do not match their source.")
        if pair in pairs and not (pairs[pair] and unchanged_pair):
            raise ValueError("These Nodes are already connected.")
        pairs[pair] = unchanged_pair
        direction, line = item.get("direction", "none"), item.get("line_style", "solid")
        if direction not in RelationshipEdge.Direction.values or line not in RelationshipEdge.LineStyle.values:
            raise ValueError("Invalid connection style.")
        edges[identity] = {"source_id": source, "target_id": target, "direction": direction, "line_style": line}

    # No writes, snapshots or partial deletions occur until ALL input is validated.
    save_relationship_map_revision(relationship_map, actor, _text(str(label), "Action", 180) or "Edit map")
    relationship_map.edges.exclude(pk__in=[i for i in edges if i > 0]).delete()
    relationship_map.nodes.exclude(pk__in=[i for i in nodes if i > 0]).delete()
    relationship_map.groups.exclude(pk__in=[i for i in groups if i > 0]).delete()
    # Free existing unique names to support group-name swaps in one transaction.
    for identity, group in current["groups"].items():
        if identity in groups:
            temporary_name = f"__saving_{uuid4().hex}"
            while temporary_name.casefold() in names or relationship_map.groups.filter(name=temporary_name).exists():
                temporary_name = f"__saving_{uuid4().hex}"
            RelationshipGroup.objects.filter(pk=identity).update(name=temporary_name)

    remapped = {key: {} for key in models}
    for key, values in (("groups", groups), ("nodes", nodes), ("edges", edges)):
        for identity, fields in values.items():
            fields = dict(fields)
            if key == "nodes":
                fields["group_id"] = remapped["groups"].get(fields["group_id"])
            if key == "edges":
                fields["source_id"] = remapped["nodes"][fields["source_id"]]
                fields["target_id"] = remapped["nodes"][fields["target_id"]]
            obj = current[key].get(identity)
            if obj is None:
                legacy = historical[key].get(identity, {})
                copied = copied_sources[key].get(identity) if identity < 0 else None
                if copied:
                    legacy = copied
                preserved = {}
                # Hidden legacy metadata must survive undo; the browser cannot edit it.
                hidden = ("node_type", "status", "owner_id", "linked_task_id", "linked_board_id") if key == "nodes" else (("relation_type", "label", "color", "notes") if key == "edges" else ())
                for field in hidden:
                    if field in legacy:
                        preserved[field] = legacy[field]
                for field in ("owner_id", "linked_task_id", "linked_board_id"):
                    if preserved.get(field):
                        fk = RelationshipNode._meta.get_field(field[:-3])
                        if not fk.related_model.objects.filter(pk=preserved[field]).exists():
                            preserved[field] = None
                obj = models[key](relationship_map=relationship_map, **preserved)
                if identity > 0:
                    obj.pk = identity
            for field, value in fields.items():
                setattr(obj, field, value)
            if key == "edges" and identity in current[key]:
                # Legacy graphs may contain parallel relations rejected by newer
                # model.clean(); unchanged server-authorized pairs remain valid.
                models[key].objects.filter(pk=obj.pk).update(**fields)
            elif key == "edges":
                models[key].objects.bulk_create([obj])
            else:
                obj.save()
            remapped[key][identity] = obj.pk
    relationship_map.name = map_name
    relationship_map.description = map_description
    relationship_map.save(update_fields=["name", "description", "updated_at"])
    relationship_map.refresh_from_db()
    return remapped
