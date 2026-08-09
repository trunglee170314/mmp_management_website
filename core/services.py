from datetime import timedelta
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .models import ActionItem, AuditLog, Board, BoardAssignment, Meeting, MeetingTask, MinuteWriterRotation, MinuteWriterRotationMember, Scope, SystemSetting, Task, TaskBoard, TaskHistory, User


def record_audit(actor, entity, action, details=None):
    AuditLog.objects.create(actor=actor, entity_type=entity.__class__.__name__, entity_id=entity.pk, action=action, details=details or {})


def lock_responsibility_transfer_mutex():
    """Serialize meeting writer assignment with user responsibility transfer."""
    setting = SystemSetting.load()
    return SystemSetting.objects.select_for_update().get(pk=setting.pk)


def user_responsibility_counts(user):
    """Return the operational records that must move before an account is disabled."""
    return {
        "tasks": user.assigned_tasks.filter(is_archived=False).exclude(
            status__in=[Task.Status.DONE, Task.Status.CANCELLED]
        ).count(),
        "action_items": user.action_items.filter(is_completed=False).count(),
        "board_assignments": user.board_assignments.filter(released_at__isnull=True).count(),
        "meetings": Meeting.objects.filter(status=Meeting.Status.DRAFT).filter(
            Q(host=user) | Q(minute_taker=user)
        ).distinct().count(),
        "rotations": user.minute_writer_rotations.count(),
    }


@transaction.atomic
def deactivate_user_and_transfer(user, target, actor):
    """Transfer current responsibilities to an Admin and deactivate ``user`` atomically."""
    lock_responsibility_transfer_mutex()
    # Serialize the active-Admin invariant first, then lock any remaining source/
    # target rows in primary-key order. This prevents concurrent deactivations from
    # both deciding that another active Admin will remain.
    locked_users = {
        member.pk: member
        for member in User.objects.select_for_update(no_key=True).filter(
            Q(role=User.Role.ADMIN) | Q(is_superuser=True),
            account_status=User.AccountStatus.ACTIVE,
        ).order_by("pk")
    }
    remaining_ids = {user.pk, target.pk} - set(locked_users)
    if remaining_ids:
        locked_users.update({
            member.pk: member
            for member in User.objects.select_for_update(no_key=True).filter(
                pk__in=remaining_ids,
            ).order_by("pk")
        })
    user = locked_users[user.pk]
    target = locked_users[target.pk]
    if user.pk == target.pk:
        raise ValueError("The transfer Admin must be a different user.")
    if actor and actor.pk == user.pk:
        raise ValueError("Ask another Admin to deactivate your account.")
    if user.account_status != User.AccountStatus.ACTIVE:
        raise ValueError("Only an active user can be deactivated.")
    if target.account_status != User.AccountStatus.ACTIVE or not target.is_admin:
        raise ValueError("Responsibilities must be transferred to an active Admin.")
    if user.is_admin and not any(
        member.pk != user.pk
        for member in locked_users.values()
        if member.is_admin and member.account_status == User.AccountStatus.ACTIVE
    ):
        raise ValueError("The last active Admin cannot be deactivated.")

    counts = user_responsibility_counts(user)
    now = timezone.now()

    # Global mutation lock order: User -> Meeting -> Rotation -> Task -> ActionItem.
    # Every meeting/action workflow uses the same order so user deactivation
    # cannot deadlock with writer allocation, meeting finalization, or AI edits.
    rotation_ids = list(
        MinuteWriterRotationMember.objects.filter(user=user)
        .order_by()
        .values_list("rotation_id", flat=True)
    )
    meeting_ids = list(
        Meeting.objects.filter(
            Q(status=Meeting.Status.DRAFT)
            | Q(action_items__assignee=user)
        ).distinct().order_by().values_list("pk", flat=True)
    )
    locked_meetings = list(
        Meeting.objects.select_for_update().filter(pk__in=meeting_ids).order_by("pk")
    )
    active_task_ids = list(
        Task.objects.filter(assignee=user, is_archived=False).exclude(
            status__in=[Task.Status.DONE, Task.Status.CANCELLED]
        ).order_by().values_list("pk", flat=True)
    )
    open_action_ids = list(
        ActionItem.objects.filter(assignee=user, is_completed=False)
        .order_by().values_list("pk", flat=True)
    )
    locked_rotations = {
        rotation.pk: rotation
        for rotation in MinuteWriterRotation.objects.select_for_update().filter(
            pk__in=rotation_ids,
        ).order_by("pk")
    }
    locked_rotation_members = list(
        MinuteWriterRotationMember.objects.select_for_update().filter(
            rotation_id__in=rotation_ids,
        ).order_by("rotation_id", "position", "pk")
    )
    members_by_rotation = {}
    for member in locked_rotation_members:
        members_by_rotation.setdefault(member.rotation_id, []).append(member)
    source_memberships = [member for member in locked_rotation_members if member.user_id == user.pk]
    for meeting in locked_meetings:
        changed_roles = []
        if meeting.status == Meeting.Status.DRAFT and meeting.host_id == user.pk:
            meeting.host = target
            changed_roles.append("host")
        if meeting.status == Meeting.Status.DRAFT and meeting.minute_taker_id == user.pk:
            meeting.minute_taker = target
            changed_roles.append("minute_taker")
        if not changed_roles:
            continue
        meeting.save(update_fields=[*changed_roles, "updated_at"])
        record_audit(actor, meeting, "Meeting responsibility transferred", {
            "roles": changed_roles,
            "from_user_id": user.pk,
            "to_user_id": target.pk,
        })

    active_tasks = list(
        Task.objects.select_for_update().filter(pk__in=active_task_ids).order_by("pk")
    )
    for task in active_tasks:
        task.assignee = target
        task.save(update_fields=["assignee", "updated_at"])
        TaskHistory.objects.create(
            task=task,
            actor=actor,
            event="Assignee changed",
            old_value=str(user),
            new_value=str(target),
            note="Transferred during user deactivation.",
        )
        record_audit(actor, task, "Task responsibility transferred", {
            "from_user_id": user.pk,
            "to_user_id": target.pk,
            "reason": "User deactivation",
        })

    open_actions = list(
        ActionItem.objects.select_for_update().filter(pk__in=open_action_ids).order_by("pk")
    )
    for item in open_actions:
        item.assignee = target
        item.save(update_fields=["assignee"])
        if item.task_id:
            TaskHistory.objects.create(
                task_id=item.task_id,
                actor=actor,
                event="Action item reassigned",
                old_value=str(user),
                new_value=str(target),
                note=f"{item.content} · User deactivation",
            )
        record_audit(actor, item, "Action item responsibility transferred", {
            "from_user_id": user.pk,
            "to_user_id": target.pk,
            "reason": "User deactivation",
        })

    assignments = list(
        BoardAssignment.objects.select_for_update().filter(
            user=user,
            released_at__isnull=True,
        ).select_related("board")
    )
    for assignment in assignments:
        duplicate_filter = {
            "board_id": assignment.board_id,
            "user": target,
            "source": assignment.source,
            "released_at__isnull": True,
        }
        if assignment.source == BoardAssignment.Source.TASK:
            duplicate_filter["task_id"] = assignment.task_id
        duplicate = BoardAssignment.objects.filter(**duplicate_filter).exclude(pk=assignment.pk).exists()
        assignment.released_at = now
        assignment.release_reason = (
            f"Merged into {target} during user deactivation"
            if duplicate
            else f"Transferred to {target} during user deactivation"
        )
        assignment.save(update_fields=["released_at", "release_reason"])
        recipient_assignment = None
        if duplicate:
            result = "merged"
        else:
            recipient_assignment = BoardAssignment.objects.create(
                board=assignment.board,
                user=target,
                source=assignment.source,
                task_id=assignment.task_id if assignment.source == BoardAssignment.Source.TASK else None,
                assigned_by=actor,
            )
            result = "transferred"
        record_audit(actor, assignment.board, "Board responsibility transferred", {
            "assignment_id": assignment.pk,
            "recipient_assignment_id": recipient_assignment.pk if recipient_assignment else None,
            "source": assignment.source,
            "task_id": assignment.task_id,
            "from_user_id": user.pk,
            "to_user_id": target.pk,
            "result": result,
        })

    for source_member in source_memberships:
        rotation = locked_rotations[source_member.rotation_id]
        members = members_by_rotation[rotation.pk]
        member_ids = [member.user_id for member in members]
        target_member = next((member for member in members if member.user_id == target.pk), None)
        if target_member:
            source_member.delete()
        else:
            source_member.user = target
            source_member.save(update_fields=["user"])
        if rotation.last_assigned_writer_id == user.pk:
            if target_member:
                source_index = member_ids.index(user.pk)
                successor_ids = member_ids[source_index + 1:] + member_ids[:source_index]
                remaining_ids = [member_id for member_id in member_ids if member_id != user.pk]
                next_writer_id = next(
                    (member_id for member_id in successor_ids if member_id in remaining_ids),
                    remaining_ids[0],
                )
                next_index = remaining_ids.index(next_writer_id)
                rotation.last_assigned_writer_id = remaining_ids[next_index - 1]
            else:
                rotation.last_assigned_writer = target
            rotation.save(update_fields=["last_assigned_writer", "updated_at"])
        remaining = list(
            MinuteWriterRotationMember.objects.filter(rotation=rotation).order_by("position", "pk")
        )
        # Move positions out of the unique range before compacting them.
        for offset, member in enumerate(remaining, start=1):
            member.position = 1_000_000 + offset
            member.save(update_fields=["position"])
        for position, member in enumerate(remaining, start=1):
            member.position = position
            member.save(update_fields=["position"])
        record_audit(actor, rotation, "Minute writer responsibility transferred", {
            "from_user_id": user.pk,
            "to_user_id": target.pk,
            "merged_existing_member": bool(target_member),
        })

    user.account_status = User.AccountStatus.INACTIVE
    user.reviewed_by = actor
    user.reviewed_at = now
    user.save(update_fields=["account_status", "reviewed_by", "reviewed_at", "is_active"])
    record_audit(actor, user, "User deactivated and responsibilities transferred", {
        "transfer_to_id": target.pk,
        **counts,
    })
    return counts


def touch_board(board, actor, activity):
    Board.objects.filter(pk=board.pk).update(updated_by=actor, last_activity=activity, updated_at=timezone.now())
    record_audit(actor, board, activity)


@transaction.atomic
def set_manual_board_user(board, user, actor, assign=True):
    if assign:
        _, created = BoardAssignment.objects.get_or_create(
            board=board,
            user=user,
            source=BoardAssignment.Source.MANUAL,
            released_at=None,
            defaults={"assigned_by": actor},
        )
        if created:
            touch_board(board, actor, f"{user} manually assigned")
    else:
        qs = BoardAssignment.objects.filter(board=board, user=user, source=BoardAssignment.Source.MANUAL, released_at__isnull=True)
        if qs.update(released_at=timezone.now(), release_reason="Manual release"):
            touch_board(board, actor, f"{user} manually released")


@transaction.atomic
def sync_task_boards(task, desired_boards, actor, old_assignee=None, reason="Task updated"):
    desired = {board.pk: board for board in desired_boards}
    active_links = list(TaskBoard.objects.select_related("board").filter(task=task, released_at__isnull=True))
    active_ids = {link.board_id for link in active_links}

    for link in active_links:
        if link.board_id not in desired:
            link.released_at = timezone.now()
            link.release_reason = reason
            link.save(update_fields=["released_at", "release_reason"])
            BoardAssignment.objects.filter(task=task, board=link.board, source=BoardAssignment.Source.TASK, released_at__isnull=True).update(
                released_at=timezone.now(), release_reason=reason
            )
            touch_board(link.board, actor, f"Released from {task.title}")

    for board_id, board in desired.items():
        if board_id not in active_ids:
            _, created = TaskBoard.objects.get_or_create(
                task=task,
                board=board,
                released_at=None,
                defaults={"added_by": actor},
            )
            if created:
                touch_board(board, actor, f"Assigned through {task.title}")

    active_links = list(TaskBoard.objects.select_related("board").filter(task=task, released_at__isnull=True))
    if old_assignee and old_assignee.pk != getattr(task.assignee, "pk", None):
        BoardAssignment.objects.filter(task=task, source=BoardAssignment.Source.TASK, released_at__isnull=True).update(
            released_at=timezone.now(), release_reason="Task assignee changed"
        )
    for link in active_links:
        if task.assignee:
            BoardAssignment.objects.get_or_create(
                task=task,
                board=link.board,
                user=task.assignee,
                source=BoardAssignment.Source.TASK,
                released_at=None,
                defaults={"assigned_by": actor},
            )


def release_all_task_boards(task, actor, reason):
    sync_task_boards(task, [], actor, old_assignee=task.assignee, reason=reason)


def log_task_changes(task, actor, old_values, status_note=""):
    labels = {"title": "Title", "parent_task_id": "Parent Task", "assignee_id": "Assignee", "status": "Status", "priority": "Priority", "timeline_start_date": "Start Date", "due_date": "Due Date", "link_url": "Link"}
    status_labels = dict(Task.Status.choices)
    priority_labels = dict(Task.Priority.choices)

    def display(field, value):
        if value in (None, ""):
            return "Unassigned" if field == "assignee_id" else ""
        if field == "assignee_id":
            return str(User.objects.filter(pk=value).first() or value)
        if field == "parent_task_id":
            return Task.objects.filter(pk=value).values_list("title", flat=True).first() or str(value)
        if field == "status":
            return status_labels.get(value, value)
        if field == "priority":
            return priority_labels.get(value, value)
        return str(value)

    for field, label in labels.items():
        old = old_values.get(field)
        new = getattr(task, field)
        if old != new:
            event = f"{label} changed"
            if field == "parent_task_id":
                if old in (None, ""):
                    event = "Parent Task added"
                elif new in (None, ""):
                    event = "Parent Task removed"
            TaskHistory.objects.create(task=task, actor=actor, event=event, old_value=display(field, old), new_value=display(field, new), note=status_note if field == "status" else "")


def log_scope_changes(task, actor, old_scope_ids):
    old_ids = set(old_scope_ids)
    new_ids = set(task.scopes.values_list("pk", flat=True))
    if old_ids == new_ids:
        return
    old_names = Scope.objects.filter(pk__in=old_ids).order_by("position", "name").values_list("name", flat=True)
    new_names = task.scopes.order_by("position", "name").values_list("name", flat=True)
    TaskHistory.objects.create(
        task=task,
        actor=actor,
        event="Scopes changed",
        old_value=", ".join(old_names),
        new_value=", ".join(new_names),
    )


def log_related_task_changes(task, actor, old_related_ids):
    """Record both sides of explicit symmetric task relationships."""
    old_ids = set(old_related_ids)
    new_ids = set(task.related_tasks.values_list("pk", flat=True))
    added = list(Task.objects.filter(pk__in=new_ids - old_ids).order_by("created_at", "pk"))
    removed = list(Task.objects.filter(pk__in=old_ids - new_ids).order_by("created_at", "pk"))

    if added:
        TaskHistory.objects.create(
            task=task,
            actor=actor,
            event="Related Tasks added",
            new_value=", ".join(related.title for related in added),
        )
        for related in added:
            TaskHistory.objects.create(
                task=related,
                actor=actor,
                event="Related Task added",
                new_value=task.title,
            )

    if removed:
        TaskHistory.objects.create(
            task=task,
            actor=actor,
            event="Related Tasks removed",
            old_value=", ".join(related.title for related in removed),
        )
        for related in removed:
            TaskHistory.objects.create(
                task=related,
                actor=actor,
                event="Related Task removed",
                old_value=task.title,
            )


def task_snapshot(task):
    prefetched_links = getattr(task, "_snapshot_board_links", None)
    if prefetched_links is None:
        boards = Board.objects.filter(
            task_links__task=task,
            task_links__released_at__isnull=True,
        ).values_list("name", "barcode")
    else:
        boards = ((link.board.name, link.board.barcode) for link in prefetched_links)
    scopes = task.scope_list
    return {
        "title": task.title,
        "scopes": [{"name": scope.name, "color": scope.color} for scope in scopes],
        "scope": " / ".join(scope.name for scope in scopes) or "Unscoped",
        "scope_color": scopes[0].color if scopes else "#777777",
        "assignee": str(task.assignee) if task.assignee else "Unassigned",
        "status": task.get_status_display(),
        "status_key": task.status,
        "priority": task.get_priority_display(),
        "link_url": task.link_url,
        "due_date": task.due_date.isoformat() if task.due_date else "",
        "due_date_label": task.due_date.strftime("%d %b %Y") if task.due_date else "",
        "boards": [
            f"{name} ({barcode})"
            for name, barcode in boards
        ],
    }


def previous_meeting_completed_after(meeting):
    previous = Meeting.objects.exclude(pk=meeting.pk).filter(
        meeting_date__lt=meeting.meeting_date,
    ).order_by("-meeting_date", "-created_at").first()
    return previous.created_at if previous else meeting.created_at - timedelta(days=36500)


def meeting_entry_action_groups(meeting, entry, completed_after=None):
    return meeting_entries_action_groups(
        meeting, [entry], completed_after=completed_after,
    ).get(entry.pk, ([], [], []))


def meeting_entries_action_groups(meeting, entries, completed_after=None):
    """Load action groups for every entry with two bounded query sets.

    The detail page, PDF exporter and finalizer all need the same data. Loading
    it here avoids three queries per MeetingTask while preserving the existing
    per-entry result shape.
    """
    entries = list(entries)
    result = {entry.pk: ([], [], []) for entry in entries}
    entry_by_task = {entry.task_id: entry for entry in entries if entry.task_id}
    task_ids = list(entry_by_task)
    if not task_ids:
        return result

    completed_after = completed_after or previous_meeting_completed_after(meeting)
    open_by_task = {task_id: [] for task_id in task_ids}
    completed_by_task = {task_id: [] for task_id in task_ids}
    previous_actions = ActionItem.objects.filter(
        task_id__in=task_ids,
        published_at__isnull=False,
        created_at__lte=meeting.created_at,
    ).exclude(meeting=meeting).filter(
        Q(is_completed=False)
        | Q(is_completed=True, completed_at__gte=completed_after)
    ).select_related("assignee", "completed_by", "meeting", "task").order_by(
        "task_id", "created_at", "pk",
    )
    for item in previous_actions:
        if item.is_completed:
            completed_by_task[item.task_id].append(item)
        else:
            open_by_task[item.task_id].append(item)

    new_by_task = {task_id: [] for task_id in task_ids}
    current_actions = ActionItem.objects.filter(
        task_id__in=task_ids,
        meeting=meeting,
    ).select_related("assignee", "completed_by", "task").order_by("task_id", "created_at", "pk")
    for item in current_actions:
        new_by_task[item.task_id].append(item)

    for task_id, entry in entry_by_task.items():
        recent_completed = sorted(
            completed_by_task[task_id],
            key=lambda item: (item.completed_at, item.pk),
            reverse=True,
        )[:4]
        result[entry.pk] = (
            open_by_task[task_id],
            recent_completed,
            new_by_task[task_id],
        )
    return result


@transaction.atomic
def assign_next_minute_writer(rotation_id, meeting_date):
    """Atomically consume the next eligible writer for one meeting occurrence."""
    lock_responsibility_transfer_mutex()
    rotation = MinuteWriterRotation.objects.select_for_update().filter(pk=rotation_id).first()
    if not rotation or not rotation.is_active:
        return None
    return rotation.assign_next_writer(meeting_date)


@transaction.atomic
def publish_meeting_actions(meeting, actor, published_at=None):
    """Publish each draft AI exactly once and record it in its task history."""
    published_at = published_at or timezone.now()
    item_ids = list(
        meeting.action_items.select_for_update()
        .filter(published_at__isnull=True)
        .values_list("pk", flat=True)
    )
    items = list(
        ActionItem.objects.filter(pk__in=item_ids)
        .select_related("task", "assignee")
        .order_by("created_at", "pk")
    )
    for item in items:
        item.published_at = published_at
        item.source_meeting_title = meeting.title
        item.source_meeting_date = meeting.meeting_date
    if items:
        ActionItem.objects.bulk_update(
            items,
            ["published_at", "source_meeting_title", "source_meeting_date"],
        )
        histories = []
        for item in items:
            if not item.task_id:
                continue
            due_label = (
                f"Due: {item.due_date:%d %b %Y}"
                if item.due_date
                else "No due date"
            )
            histories.append(TaskHistory(
                task=item.task,
                actor=actor,
                event="Meeting Minute Action Item added",
                new_value=item.content,
                note=(
                    f"Published from {meeting.title} · {meeting.meeting_date:%d %b %Y}"
                    f" · Assignee: {item.assignee or 'Unassigned'} · {due_label}"
                ),
            ))
        TaskHistory.objects.bulk_create(histories)
    return len(items)


def action_item_snapshot(item):
    return {
        "id": item.pk,
        "content": item.content,
        "assignee": str(item.assignee) if item.assignee else "Unassigned",
        "assignee_id": item.assignee_id,
        "due_date": item.due_date.isoformat() if item.due_date else "",
        "is_completed": item.is_completed,
        "completed_by": str(item.completed_by) if item.completed_by else "",
        "completed_at": item.completed_at.isoformat() if item.completed_at else "",
        "created_at": item.created_at.isoformat() if item.created_at else "",
    }


def action_item_from_snapshot(data):
    return SimpleNamespace(
        pk=data.get("id"),
        content=data.get("content", ""),
        assignee=data.get("assignee") or "Unassigned",
        assignee_id=data.get("assignee_id"),
        due_date=parse_date(data.get("due_date", "")) if data.get("due_date") else None,
        is_completed=bool(data.get("is_completed")),
        completed_by=data.get("completed_by", ""),
        completed_at=parse_datetime(data.get("completed_at", "")) if data.get("completed_at") else None,
        created_at=parse_datetime(data.get("created_at", "")) if data.get("created_at") else None,
    )


def freeze_meeting_entry(entry, meeting, completed_after=None, action_groups=None):
    if action_groups is None:
        action_groups = meeting_entry_action_groups(
            meeting, entry, completed_after=completed_after,
        )
    open_previous, recent_completed, new_actions = action_groups
    snapshot = task_snapshot(entry.task) if entry.task_id else dict(entry.snapshot or {})
    snapshot.update({
        "snapshot_version": 2,
        "weekly_progress": entry.weekly_progress,
        "review_state": entry.review_state,
        "review_label": entry.get_review_state_display(),
        "previous_actions": [
            action_item_snapshot(item) for item in open_previous + recent_completed
        ],
        "new_actions": [action_item_snapshot(item) for item in new_actions],
    })
    entry.snapshot = snapshot
    return snapshot


def frozen_meeting_entry_actions(entry):
    snapshot = entry.snapshot or {}
    previous = [action_item_from_snapshot(item) for item in snapshot.get("previous_actions", [])]
    new_actions = [action_item_from_snapshot(item) for item in snapshot.get("new_actions", [])]
    return (
        [item for item in previous if not item.is_completed],
        [item for item in previous if item.is_completed],
        new_actions,
    )


@transaction.atomic
def initialize_meeting_tasks(meeting):
    active = list(
        Task.objects.filter(is_archived=False)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .annotate(has_open_action_items=Exists(
            ActionItem.objects.filter(task_id=OuterRef("pk"), is_completed=False)
        ))
        .select_related("assignee")
        .prefetch_related("scopes")
        .prefetch_related(Prefetch(
            "board_links",
            queryset=TaskBoard.objects.filter(released_at__isnull=True).select_related("board"),
            to_attr="_snapshot_board_links",
        ))
    )
    def scope_sort_key(item):
        scopes = tuple(
            (scope.position, scope.name.lower(), scope.pk)
            for scope in item.scope_list
        )
        return (0 if scopes else 1, scopes, item.title.lower())

    ordered = sorted(active, key=scope_sort_key)
    MeetingTask.objects.bulk_create([
        MeetingTask(
            meeting=meeting,
            task=task,
            position=index,
            review_state=(
                MeetingTask.ReviewState.SKIPPED
                if task.status == Task.Status.TODO and not task.has_open_action_items
                else MeetingTask.ReviewState.PENDING
            ),
            snapshot=task_snapshot(task),
        )
        for index, task in enumerate(ordered, start=1)
    ])
