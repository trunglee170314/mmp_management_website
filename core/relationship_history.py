from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    Board,
    RelationshipEdge,
    RelationshipGroup,
    RelationshipMap,
    RelationshipMapRevision,
    RelationshipNode,
    Task,
    User,
)


RELATIONSHIP_TRASH_RETENTION_DAYS = 30
RELATIONSHIP_MAX_REVISIONS = 500
RELATIONSHIP_LAYOUT_COALESCE_SECONDS = 300


def relationship_map_snapshot(relationship_map):
    return {
        "schema_version": 1,
        "map": {
            "name": relationship_map.name,
            "description": relationship_map.description,
        },
        "groups": list(relationship_map.groups.order_by("pk").values(
            "id", "name", "color", "x", "y", "width", "height", "is_collapsed",
        )),
        "nodes": list(relationship_map.nodes.order_by("pk").values(
            "id", "group_id", "name", "node_type", "status", "description", "color",
            "owner_id", "linked_task_id", "linked_board_id", "external_url", "x", "y",
        )),
        "edges": list(relationship_map.edges.order_by("pk").values(
            "id", "source_id", "target_id", "relation_type", "label", "direction",
            "line_style", "color", "notes",
        )),
    }


def save_relationship_map_revision(relationship_map, actor, action, *, coalesce_seconds=0):
    snapshot = relationship_map_snapshot(relationship_map)
    latest = relationship_map.revisions.first()
    if latest and latest.actor_id == getattr(actor, "pk", None) and latest.action == action:
        if latest.snapshot == snapshot:
            return latest
        if coalesce_seconds and latest.created_at >= timezone.now() - timedelta(seconds=coalesce_seconds):
            return latest
    revision = RelationshipMapRevision.objects.create(
        relationship_map=relationship_map,
        actor=actor,
        action=action[:180],
        snapshot=snapshot,
    )
    newest_ids = list(
        relationship_map.revisions.order_by("-created_at", "-pk")
        .values_list("pk", flat=True)[:RELATIONSHIP_MAX_REVISIONS]
    )
    relationship_map.revisions.exclude(pk__in=newest_ids).delete()
    return revision


@transaction.atomic
def restore_relationship_map_revision(relationship_map, revision, actor):
    snapshot = revision.snapshot
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("This history entry uses an unsupported snapshot format.")
    map_data = snapshot.get("map") or {}
    restored_name = str(map_data.get("name") or "").strip()
    if not restored_name:
        raise ValueError("This history entry does not contain a valid Map name.")
    if RelationshipMap.objects.exclude(pk=relationship_map.pk).filter(name__iexact=restored_name).exists():
        raise ValueError(f'A Map named “{restored_name}” already exists.')

    save_relationship_map_revision(relationship_map, actor, "Before history restore")
    relationship_map.edges.all().delete()
    relationship_map.nodes.all().delete()
    relationship_map.groups.all().delete()

    group_ids = {}
    for item in snapshot.get("groups") or []:
        group = RelationshipGroup.objects.create(
            relationship_map=relationship_map,
            name=str(item.get("name") or "")[:120],
            color=item.get("color") or "#eaf4ee",
            x=item.get("x", 80),
            y=item.get("y", 80),
            width=item.get("width", 360),
            height=item.get("height", 260),
            is_collapsed=bool(item.get("is_collapsed", False)),
        )
        group_ids[item.get("id")] = group.pk

    node_items = snapshot.get("nodes") or []
    owner_ids = set(User.objects.filter(
        pk__in=[item.get("owner_id") for item in node_items if item.get("owner_id")],
    ).values_list("pk", flat=True))
    task_ids = set(Task.objects.filter(
        pk__in=[item.get("linked_task_id") for item in node_items if item.get("linked_task_id")],
    ).values_list("pk", flat=True))
    board_ids = set(Board.objects.filter(
        pk__in=[item.get("linked_board_id") for item in node_items if item.get("linked_board_id")],
    ).values_list("pk", flat=True))
    node_ids = {}
    for item in node_items:
        node = RelationshipNode.objects.create(
            relationship_map=relationship_map,
            group_id=group_ids.get(item.get("group_id")),
            name=str(item.get("name") or "")[:160],
            node_type=str(item.get("node_type") or "")[:80],
            status=str(item.get("status") or "")[:40],
            description=str(item.get("description") or ""),
            color=item.get("color") or "#ffffff",
            owner_id=item.get("owner_id") if item.get("owner_id") in owner_ids else None,
            linked_task_id=item.get("linked_task_id") if item.get("linked_task_id") in task_ids else None,
            linked_board_id=item.get("linked_board_id") if item.get("linked_board_id") in board_ids else None,
            external_url=str(item.get("external_url") or "")[:500],
            x=item.get("x", 160),
            y=item.get("y", 160),
        )
        node_ids[item.get("id")] = node.pk

    for item in snapshot.get("edges") or []:
        source_id = node_ids.get(item.get("source_id"))
        target_id = node_ids.get(item.get("target_id"))
        if not source_id or not target_id:
            continue
        RelationshipEdge.objects.create(
            relationship_map=relationship_map,
            source_id=source_id,
            target_id=target_id,
            relation_type=str(item.get("relation_type") or "")[:80],
            label=str(item.get("label") or "")[:120],
            direction=item.get("direction") or RelationshipEdge.Direction.NONE,
            line_style=item.get("line_style") or RelationshipEdge.LineStyle.SOLID,
            color=item.get("color") or "#aeb7b1",
            notes=str(item.get("notes") or ""),
        )

    relationship_map.name = restored_name[:120]
    relationship_map.description = str(map_data.get("description") or "")
    relationship_map.save(update_fields=["name", "description", "updated_at"])
    return relationship_map


def purge_expired_relationship_maps(now=None, batch_size=100):
    cutoff = (now or timezone.now()) - timedelta(days=RELATIONSHIP_TRASH_RETENTION_DAYS)
    with transaction.atomic():
        expired = list(
            RelationshipMap.objects.select_for_update(skip_locked=True)
            .filter(is_deleted=True, deleted_at__lte=cutoff)
            .order_by("deleted_at", "pk")[:batch_size]
        )
        for relationship_map in expired:
            relationship_map.delete()
    return len(expired)
