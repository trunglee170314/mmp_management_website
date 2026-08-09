"""Persistent Timeline order and explicit tree moves, separate from Meeting order.

HTML pages keep their PK cursors. The client sorts lightweight descriptors by
position (nulls last) then PK, and groups descendants underneath their parent.
Calendar reorder changes sibling order only. The separate Parent Task endpoint
handles explicit tree drops; neither accepts a client-supplied whole-Group list
that could drop hidden or unscheduled members.
"""
import hashlib
import json
import re

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .models import Task, TaskHistory, TimelineGroup, User
from .services import (lock_responsibility_transfer_mutex, log_task_changes,
                       record_audit, touch_draft_meeting_entries_for_task)
from .timeline_interactions import revision_error, task_data


def group_key(group_id):
    return str(group_id or "")


def position_key(task):
    """One explicit NULL policy on both SQLite and PostgreSQL."""
    return (task.timeline_position is None, task.timeline_position or 0, task.pk)


def effective_parents(members):
    by_id = {task.pk: task for task in members}
    return {
        task.pk: task.parent_task_id
        if task.parent_task_id in by_id and task.parent_task_id != task.pk
        and by_id[task.parent_task_id].timeline_group_id == task.timeline_group_id else None
        for task in members
    }


def order_revision(members, group_id):
    """Membership, parent edits and rank edits invalidate old order gestures/Undo.

    Dates/status are deliberately excluded: the moved Task's own updated_at
    separately protects it, while another Task's date edit need not block a
    presentation-only reorder.
    """
    snapshot = [group_key(group_id), [
        [task.pk, task.parent_task_id, task.timeline_position]
        for task in sorted(members, key=lambda task: task.pk)
    ]]
    return hashlib.sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()


def group_order_snapshots(group_ids):
    """One query includes filtered-out and unscheduled active Group members."""
    keys = {group_key(value) for value in group_ids}
    if not keys:
        return {}
    numeric_ids = [int(key) for key in keys if key]
    query = Q(timeline_group_id__in=numeric_ids)
    if "" in keys:
        query |= Q(timeline_group_id__isnull=True)
    grouped = {key: [] for key in keys}
    for task in Task.objects.filter(query, is_archived=False).only(
        "pk", "parent_task_id", "timeline_group_id", "timeline_position"
    ).order_by("pk"):
        grouped[group_key(task.timeline_group_id)].append(task)
    return {key: {"revision": order_revision(members, key), "parents": effective_parents(members),
                  "positions": {task.pk: task.timeline_position for task in members},
                  "raw_parents": {task.pk: task.parent_task_id for task in members}}
            for key, members in grouped.items()}


def canonical_order(members):
    """Keep sibling rank order while ensuring each subtree is one block.

    Legacy missing/cyclic relations cannot make Tasks disappear. A gesture that
    actually involves a cycle is rejected separately before writing.
    """
    ordered = sorted(members, key=position_key)
    parents = effective_parents(ordered)
    children, roots = {}, []
    for task in ordered:
        parent = parents[task.pk]
        if parent is None:
            roots.append(task)
        else:
            children.setdefault(parent, []).append(task)
    result, seen = [], set()
    # Iterative DFS also handles imported hierarchies deeper than Python's
    # recursion limit, without changing the existing sibling ordering.
    for seed in [*roots, *ordered]:
        pending = [seed]
        while pending:
            task = pending.pop()
            if task.pk in seen:
                continue
            seen.add(task.pk)
            result.append(task)
            pending.extend(reversed(children.get(task.pk, [])))
    return result


def _assert_acyclic(task_id, parents):
    seen = set()
    while task_id is not None:
        if task_id in seen:
            raise ValueError("Fix the cyclic Parent Task relationship before changing this order.")
        seen.add(task_id)
        task_id = parents.get(task_id)


def _descendants(task_id, parents):
    children = {}
    for child, parent in parents.items():
        if parent is not None:
            children.setdefault(parent, []).append(child)
    result, pending = set(), [task_id]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(children.get(current, []))
    return result


def moved_order(members, task_id, anchor_id, placement):
    """Return the new canonical order, inverse sibling anchor and moved block."""
    by_id = {task.pk: task for task in members}
    if task_id not in by_id or anchor_id not in by_id:
        raise ValueError("The target Task must be in the same Group.")
    if task_id == anchor_id:
        raise ValueError("Choose another Task as the drop target.")
    if placement not in {"before", "after"}:
        raise ValueError("Placement must be before or after the target Task.")
    parents = effective_parents(members)
    _assert_acyclic(task_id, parents)
    _assert_acyclic(anchor_id, parents)
    block_ids = _descendants(task_id, parents)
    if anchor_id in block_ids:
        raise ValueError("A Task cannot be moved inside its own subtree.")
    if parents[task_id] != parents[anchor_id]:
        raise ValueError("Reorder relative to a sibling Task; this action does not change Parent Task.")
    original = canonical_order(members)
    siblings = [task.pk for task in original if parents[task.pk] == parents[task_id]]
    index = siblings.index(task_id)
    undo = ({"anchor_id": siblings[index - 1], "placement": "after"} if index
            else {"anchor_id": siblings[index + 1], "placement": "before"})
    block = [task for task in original if task.pk in block_ids]
    remaining = [task for task in original if task.pk not in block_ids]
    insertion = next(index for index, task in enumerate(remaining) if task.pk == anchor_id)
    if placement == "after":
        anchor_block = _descendants(anchor_id, parents)
        insertion = max(index for index, task in enumerate(remaining) if task.pk in anchor_block) + 1
    return remaining[:insertion] + block + remaining[insertion:], undo, block_ids


def _response(task, members, group_id, undo=None, updated=False):
    signature = order_revision(members, group_id)
    payload = task_data(task)
    payload.update(position=task.timeline_position, order_revision=signature,
                   order_parent=effective_parents(members)[task.pk] or "", parent=task.parent_task_id or "")
    return JsonResponse({"updated": updated, "task": payload, "order_revision": signature,
                         "order": [{"id": item.pk, "position": item.timeline_position}
                                   for item in canonical_order(members)], "undo": undo})


@login_required
@never_cache
@require_POST
@transaction.atomic
def timeline_task_order(request, pk):
    if request.user.account_status != User.AccountStatus.ACTIVE:
        return JsonResponse({"error": "Only active users can change Timeline order."}, status=403)
    requested_signature = request.POST.get("order_revision", "")
    if not requested_signature:
        return JsonResponse({"error": "Order revision required. Reload before reordering."}, status=428)
    if not re.fullmatch(r"[a-f0-9]{64}", requested_signature):
        return JsonResponse({"error": "Invalid order revision. Reload before reordering."}, status=400)
    try:
        anchor_id = int(request.POST.get("anchor_id", ""))
        if anchor_id < 1:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse({"error": "Choose a valid target Task."}, status=400)
    placement = request.POST.get("placement", "")
    if placement not in {"before", "after"}:
        return JsonResponse({"error": "Placement must be before or after."}, status=400)

    # Same portable mutex/order as responsibility transfers. No Meeting locks or
    # Meeting synchronization occurs here. NO KEY UPDATE permits FK key shares
    # from simultaneous ordinary Task/parent/Group edits without lock cycles.
    lock_responsibility_transfer_mutex()
    group_id = Task.objects.filter(pk=pk, is_archived=False).values_list("timeline_group_id", flat=True).first()
    if not Task.objects.filter(pk=pk, is_archived=False).exists():
        return JsonResponse({"error": "This Task is no longer available."}, status=404)
    group = TimelineGroup.objects.select_for_update(no_key=True).filter(pk=group_id).first() if group_id else None
    if group_id and group is None:
        return JsonResponse({"error": "This Group was deleted. Reload before reordering."}, status=409)
    members = list(Task.objects.select_for_update(no_key=True).filter(
        timeline_group_id=group_id, is_archived=False
    ).order_by("pk"))
    task = next((item for item in members if item.pk == pk), None)
    if task is None:
        return JsonResponse({"error": "This Task moved or was archived. Reload before reordering."}, status=409)
    conflict = revision_error(request, task, "task", task_data)
    if conflict:
        return conflict
    current_signature = order_revision(members, group_id)
    if current_signature != requested_signature:
        return JsonResponse({"error": "Task order or relationships changed. Reload before reordering.",
                             "order_revision": current_signature}, status=409)
    try:
        reordered, undo, moved_ids = moved_order(members, pk, anchor_id, placement)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    original = canonical_order(members)
    if [item.pk for item in original] == [item.pk for item in reordered]:
        return _response(task, members, group_id)

    before_ids = [item.pk for item in original]
    changed = []
    for index, item in enumerate(reordered):
        if item.timeline_position != index:
            item.timeline_position = index
            changed.append(item)
    Task.objects.bulk_update(changed, ["timeline_position"])
    # Only the explicitly moved Task's revision/activity changes. Other members
    # may receive normalized rank values; their date/status edits remain valid.
    task.updated_at = timezone.now()
    task.save(update_fields=["updated_at"])
    group_name = group.name if group else "Ungrouped"
    TaskHistory.objects.create(task=task, actor=request.user, event="Timeline order changed",
                               old_value=f"Group: {group_name}; Position: {before_ids.index(pk) + 1}",
                               new_value=f"Group: {group_name}; Position: {[item.pk for item in reordered].index(pk) + 1}")
    record_audit(request.user, task, "Timeline order changed", {
        "group": group_id, "parent": effective_parents(members)[pk],
        "from": before_ids, "to": [item.pk for item in reordered], "moved_ids": sorted(moved_ids),
    })
    return _response(task, reordered, group_id, undo=undo, updated=True)


PARENT_UNDO_SALT = "core.timeline.parent.undo.v1"


def parent_move(members, task_id, parent_id, anchor_id=None, placement="after"):
    """Move a visible subtree; the caller validates the full cross-Group graph."""
    by_id = {item.pk: item for item in members}
    task = by_id[task_id]
    if placement not in {"before", "after"}:
        raise ValueError("Placement must be before or after the target Task.")
    original = canonical_order(members)
    block_ids = _descendants(task_id, effective_parents(members))
    if parent_id in block_ids or anchor_id in block_ids:
        raise ValueError("A Task cannot be placed inside its own subtree.")
    if parent_id is not None and parent_id not in by_id:
        raise ValueError("Choose a Parent Task in the same Group.")
    if anchor_id is not None and anchor_id not in by_id:
        raise ValueError("Choose a target Task in the same Group.")
    task.parent_task_id = parent_id
    parents = effective_parents(members)
    block = [item for item in original if item.pk in block_ids]
    remaining = [item for item in original if item.pk not in block_ids]
    if anchor_id is not None:
        if parents[anchor_id] != parent_id:
            raise ValueError("The target Task is not under the selected parent.")
        insertion = next(index for index, item in enumerate(remaining) if item.pk == anchor_id)
        if placement == "after":
            anchor_block = _descendants(anchor_id, parents)
            insertion = max(index for index, item in enumerate(remaining) if item.pk in anchor_block) + 1
    elif parent_id is not None:
        parent_block = _descendants(parent_id, parents) - block_ids
        insertion = max(index for index, item in enumerate(remaining) if item.pk in parent_block) + 1
    else:
        insertion = len(remaining)
    return remaining[:insertion] + block + remaining[insertion:], block_ids


@login_required
@never_cache
@require_POST
@transaction.atomic
def timeline_task_parent(request, pk):
    """An explicit tree drop changes parent and order together, never dates/Group.

    Global parent links may cross Groups, unlike visible Timeline branches. Lock
    the lightweight full graph in PK order so simultaneous inverse moves cannot
    introduce a cycle. NO KEY UPDATE still allows normal FK key-share checks.
    Undo is signed and actor/Task-bound; it restores only parent and rank order,
    including an original parent outside this Group, never other Task fields.
    """
    if request.user.account_status != User.AccountStatus.ACTIVE:
        return JsonResponse({"error": "Only active users can change Parent Task."}, status=403)
    signature = request.POST.get("order_revision", "")
    if not signature:
        return JsonResponse({"error": "Order revision required. Reload before moving."}, status=428)
    if not re.fullmatch(r"[a-f0-9]{64}", signature):
        return JsonResponse({"error": "Invalid order revision. Reload before moving."}, status=400)
    restore = None
    try:
        if request.POST.get("restore"):
            restore = signing.loads(request.POST["restore"], salt=PARENT_UNDO_SALT)
            if restore["task"] != pk or restore["actor"] != request.user.pk:
                raise ValueError
            parent_id = restore["parent"]
            anchor_id, placement = None, "after"
        else:
            if "parent_id" not in request.POST:
                raise ValueError
            parent_id = int(request.POST["parent_id"]) if request.POST["parent_id"] else None
            anchor_id = int(request.POST["anchor_id"]) if request.POST.get("anchor_id") else None
            placement = request.POST.get("placement", "after")
        if (parent_id is not None and parent_id < 1) or (anchor_id is not None and anchor_id < 1) or placement not in {"before", "after"}:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        return JsonResponse({"error": "Invalid Parent Task move. Reload before trying again."}, status=400)

    lock_responsibility_transfer_mutex()
    reference = Task.objects.filter(pk=pk, is_archived=False).values("timeline_group_id").first()
    if reference is None:
        return JsonResponse({"error": "This Task is no longer available."}, status=404)
    group_id = reference["timeline_group_id"]
    group = TimelineGroup.objects.select_for_update(no_key=True).filter(pk=group_id).first() if group_id else None
    if group_id and group is None:
        return JsonResponse({"error": "This Group changed. Reload before moving."}, status=409)
    graph = {item.pk: item for item in Task.objects.select_for_update(no_key=True).only(
        "pk", "parent_task_id", "timeline_group_id", "is_archived"
    ).order_by("pk")}
    source = graph.get(pk)
    if source is None or source.is_archived or source.timeline_group_id != group_id:
        return JsonResponse({"error": "This Task changed. Reload before moving."}, status=409)
    members = list(Task.objects.filter(timeline_group_id=group_id, is_archived=False).order_by("pk"))
    task = next(item for item in members if item.pk == pk)
    conflict = revision_error(request, task, "task", task_data)
    if conflict:
        return conflict
    if order_revision(members, group_id) != signature:
        return JsonResponse({"error": "Task order or relationships changed. Reload before moving."}, status=409)
    if restore and (restore["group"] != group_id or restore["after_parent"] != task.parent_task_id
                    or set(restore["order"]) != {item.pk for item in members}):
        return JsonResponse({"error": "The original Task tree changed. Reload before moving."}, status=409)
    parent = graph.get(parent_id) if parent_id is not None else None
    if restore and parent_id is not None and parent is None:
        return JsonResponse({"error": "The original Parent Task was deleted. Reload before moving."}, status=409)
    if parent_id is not None and (parent is None or not restore and (parent.is_archived or parent.timeline_group_id != group_id)):
        return JsonResponse({"error": "Choose an active Parent Task in the same Group."}, status=400)
    # Follow ACTUAL parents, including archived/cross-Group intermediate nodes.
    parents = {item.pk: item.parent_task_id for item in graph.values()}
    parents[pk] = parent_id
    try:
        _assert_acyclic(pk, parents)
    except ValueError:
        return JsonResponse({"error": "A Task cannot be its own parent or descendant."}, status=400)
    if parent_id is not None and task.related_tasks.filter(pk=parent_id).exists():
        return JsonResponse({"error": "Remove the Related Task link before making it the Parent Task."}, status=409 if restore else 400)

    original = canonical_order(members)
    before_ids = [item.pk for item in original]
    old_parent = task.parent_task_id
    if restore:
        block_ids = _descendants(pk, effective_parents(members))
        task.parent_task_id = parent_id
        by_id = {item.pk: item for item in members}
        reordered = [by_id[item_id] for item_id in restore["order"]]
    else:
        try:
            reordered, block_ids = parent_move(members, pk, parent_id, anchor_id, placement)
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)
    after_ids = [item.pk for item in reordered]
    if old_parent == parent_id and before_ids == after_ids:
        return _response(task, members, group_id)
    changed = []
    for position, item in enumerate(reordered):
        if item.timeline_position != position:
            item.timeline_position = position
            changed.append(item)
    Task.objects.bulk_update(changed, ["timeline_position"])
    task.save(update_fields=["parent_task", "updated_at"])
    log_task_changes(task, request.user, {"parent_task_id": old_parent})
    if before_ids != after_ids:
        TaskHistory.objects.create(task=task, actor=request.user, event="Timeline order changed",
                                   old_value=f"Position: {before_ids.index(pk) + 1}", new_value=f"Position: {after_ids.index(pk) + 1}")
    record_audit(request.user, task, "Timeline parent changed" if old_parent != parent_id else "Timeline order changed", {
        "group": group_id, "parent": {"from": old_parent, "to": parent_id},
        "from": before_ids, "to": after_ids, "moved_ids": sorted(block_ids),
    })
    if old_parent != parent_id:
        transaction.on_commit(lambda: touch_draft_meeting_entries_for_task(task.pk, request.user), robust=True)
    undo = {"restore": signing.dumps({"task": pk, "actor": request.user.pk, "group": group_id,
             "parent": old_parent, "after_parent": parent_id, "order": before_ids}, salt=PARENT_UNDO_SALT, compress=True)}
    return _response(task, reordered, group_id, undo=undo, updated=True)
