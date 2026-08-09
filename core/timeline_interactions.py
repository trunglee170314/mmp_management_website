"""Timeline mutations share normal Task rules and enforce milestone privacy server-side."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from functools import wraps
from django.db.models import Q, Max
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from .forms import TaskForm, TimelineGroupForm, TimelineHolidayForm, TimelineMilestoneForm, TimelineTaskForm
from .models import AuditLog, SystemSetting, Task, TaskHistory, TimelineGroup, TimelineHoliday, TimelineMilestone, User
from .services import (log_related_task_changes, log_scope_changes, record_audit,
                       release_all_task_boards, sync_task_boards, sync_task_to_draft_meetings,
                       touch_draft_meeting_entries_for_task)


def wants_json(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")


def active_error(request):
    if request.user.account_status != User.AccountStatus.ACTIVE:
        return JsonResponse({"error": "Only active users can edit the timeline."}, status=403)


def group_conflict_guard(view):
    """Catch a concurrent Group deletion at the OUTER transaction commit."""
    @wraps(view)
    def guarded(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except IntegrityError as error:
            cause = error.__cause__
            constraint = getattr(getattr(cause, "diag", None), "constraint_name", "") or ""
            if getattr(cause, "sqlstate", "") == "23503" and "timeline_group_id" in constraint:
                return JsonResponse({"error": "This Group changed or was deleted. Reload before saving."}, status=409)
            raise
    return guarded


def task_data(task):
    return {"id": task.pk, "title": task.title, "start": task.timeline_start_date.isoformat() if task.timeline_start_date else "",
            "due": task.due_date.isoformat() if task.due_date else "", "group": task.timeline_group_id or "",
            "revision": task.updated_at.isoformat(), "status": task.status,
            "position": task.timeline_position,
            "url": reverse("task_history", args=[task.pk])}


def milestone_task_relations():
    """One lightweight lookup for ancestry, including unscheduled/filtered parents.

    Only IDs are needed. Archived or cross-Group parents stop a visible Timeline
    branch; cycles in old/imported data must not hang rendering.
    """
    return {
        task_id: (parent_id, group_id)
        for task_id, parent_id, group_id in Task.objects.filter(is_archived=False).values_list(
            "pk", "parent_task_id", "timeline_group_id",
        )
    }


def milestone_data(item, user, task_relations=None):
    scopes = item.scope_list
    task_parent, task_ancestors = "", []
    if item.task_id:
        task_parent = item.task.parent_task_id or ""
        relations = milestone_task_relations() if task_relations is None else task_relations
        current, visited = task_parent, {item.task_id}
        while current and current not in visited:
            relation = relations.get(current)
            if relation is None or relation[1] != item.task.timeline_group_id:
                break
            task_ancestors.append(current)
            visited.add(current)
            current = relation[0]
    return {"id": item.pk, "name": item.name, "date": item.date.isoformat(), "color": item.color,
            "display_color": item.display_color, "notes": item.notes, "task": item.task_id or "",
            "timeline_group": item.timeline_group_id or "", "visibility": item.visibility,
            "is_important": item.is_important, "revision": item.updated_at.isoformat(),
            "scopes": [scope.pk for scope in scopes], "can_manage": item.can_edit(user),
            "scope_options": [{"id": scope.pk, "name": scope.name} for scope in scopes], "task_name": item.task.title if item.task_id else "",
            "task_has_schedule": bool(item.task_id and (item.task.timeline_start_date or item.task.due_date)),
            "task_group": item.task.timeline_group_id if item.task_id else None,
            "task_parent": task_parent, "task_ancestors": task_ancestors,
            "can_change_visibility": item.created_by_id == user.pk,
            "edit_url": reverse("timeline_milestone_edit", args=[item.pk]),
            "delete_url": reverse("timeline_milestone_delete", args=[item.pk])}


@login_required
@never_cache
@require_POST
@transaction.atomic
def timeline_admin_item(request, kind, pk=None, delete=False):
    if active_error(request) or not request.user.is_admin:
        return JsonResponse({"error": "Only an Admin can manage Groups and Holidays."}, status=403)
    model, form_class = (TimelineGroup, TimelineGroupForm) if kind == "group" else (TimelineHoliday, TimelineHolidayForm)
    item = get_object_or_404(model.objects.select_for_update(), pk=pk) if pk else None
    if item and wants_json(request):
        conflict = revision_error(request, item, kind, lambda obj: {"id": obj.pk, "revision": obj.updated_at.isoformat()})
        if conflict:
            return conflict
    if delete:
        if not item:
            return JsonResponse({"error": "Item not found."}, status=404)
        record_audit(request.user, item, f"Timeline {kind} deleted")
        if kind == "group":
            task_ids = list(Task.objects.select_for_update(no_key=True).filter(timeline_group=item).order_by("pk").values_list("pk", flat=True))
            changed_at = timezone.now()
            Task.objects.filter(pk__in=task_ids).update(timeline_group=None, updated_at=changed_at)
            TaskHistory.objects.bulk_create([TaskHistory(task_id=task_id, actor=request.user, event="Timeline Group removed", old_value=item.name, new_value="Ungrouped") for task_id in task_ids])
            # Do not expose private milestone metadata in the shared Group log.
            TimelineMilestone.objects.filter(timeline_group=item).update(timeline_group=None, updated_at=changed_at)
        item.delete()
    else:
        form = form_class(request.POST, instance=item)
        if not form.is_valid():
            return form_error(request, form)
        item = form.save(commit=False)
        if not pk:
            item.created_by = request.user
            if kind == "group":
                item.position = (TimelineGroup.objects.aggregate(value=Max("position"))["value"] or 0) + 1
        item.save()
        record_audit(request.user, item, f"Timeline {kind} {'updated' if pk else 'added'}")
    if wants_json(request):
        return JsonResponse({"deleted": bool(delete), "saved": not delete})
    from .views import timeline_redirect
    return timeline_redirect(request)


def form_error(request, form):
    errors = form.errors.get_json_data()
    if wants_json(request):
        return JsonResponse({"error": "Please check the form.", "errors": errors}, status=400)
    messages.error(request, " ".join(str(error) for values in form.errors.values() for error in values))
    return redirect("task_timeline")


def revision_error(request, instance, key, serializer):
    revision = request.POST.get("revision", "")
    if not revision:
        return JsonResponse({"error": "Revision required. Reload before saving."}, status=428)
    try:
        parsed = parse_datetime(revision)
    except (ValueError, TypeError):
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        return JsonResponse({"error": "Invalid revision. Reload before saving."}, status=400)
    if parsed != instance.updated_at:
        return JsonResponse({"error": "Someone changed this item. Reload before saving.", key: serializer(instance)}, status=409)


@login_required
@never_cache
@require_POST
@group_conflict_guard
@transaction.atomic
def timeline_task_update(request, pk):
    denied = active_error(request)
    if denied:
        return denied
    task = get_object_or_404(Task.objects.select_for_update(of=("self",)).select_related("timeline_group"), pk=pk, is_archived=False)
    conflict = revision_error(request, task, "task", task_data)
    if conflict:
        return conflict
    data = request.POST.copy()
    if "timeline_group" not in data:
        data["timeline_group"] = task.timeline_group_id or ""
    old_start, old_due, old_group = task.timeline_start_date, task.due_date, task.timeline_group_id
    old_label = f"Group: {task.timeline_group or 'Ungrouped'}; Start: {old_start or 'None'}; Due: {old_due or 'None'}"
    form = TimelineTaskForm(data, instance=task)
    if not form.is_valid():
        return form_error(request, form)
    task = form.save()
    if (old_start, old_due, old_group) != (task.timeline_start_date, task.due_date, task.timeline_group_id):
        new_label = f"Group: {task.timeline_group or 'Ungrouped'}; Start: {task.timeline_start_date or 'None'}; Due: {task.due_date or 'None'}"
        TaskHistory.objects.create(task=task, actor=request.user, event="Timeline schedule updated", old_value=old_label, new_value=new_label)
        record_audit(request.user, task, "Timeline schedule updated", {"changes": {
            "timeline_start_date": {"from": str(old_start or ""), "to": str(task.timeline_start_date or "")},
            "due_date": {"from": str(old_due or ""), "to": str(task.due_date or "")},
            "timeline_group": {"from": old_group, "to": task.timeline_group_id}}})
        # Release the Task lock before waking MM editors (which lock Meeting first).
        # In this non-atomic callback each UPDATE commits independently, avoiding
        # an entry -> Meeting lock cycle as well. A wake failure must not turn a
        # committed schedule change into an ambiguous failed save for the client.
        transaction.on_commit(lambda: touch_draft_meeting_entries_for_task(task.pk, request.user), robust=True)
    if wants_json(request):
        return JsonResponse({"updated": True, "task": task_data(task)})
    messages.success(request, "Task schedule updated.")
    from .views import timeline_redirect
    return timeline_redirect(request)


@login_required
@never_cache
@require_POST
@group_conflict_guard
@transaction.atomic
def timeline_task_create(request):
    denied = active_error(request)
    if denied:
        return denied
    form = TaskForm(request.POST, score_enabled=SystemSetting.load().task_score_enabled)
    if not form.is_valid():
        return form_error(request, form)
    group_id = request.POST.get("timeline_group", "")
    if group_id and (not group_id.isdecimal() or int(group_id) < 1):
        return JsonResponse({"error": "Select a valid Timeline Group."}, status=400)
    group = get_object_or_404(TimelineGroup, pk=group_id) if group_id else None
    task = form.save(commit=False)
    task.created_by = request.user
    task.timeline_group = group
    if task.status == Task.Status.DONE:
        task.completed_by = task.assignee
        task.completed_at = timezone.now()
    task.save()
    form.save_m2m()
    TaskHistory.objects.create(task=task, actor=request.user, event="Task created")
    if task.status in {Task.Status.DONE, Task.Status.CANCELLED}:
        TaskHistory.objects.create(task=task, actor=request.user, event="Status changed", new_value=task.get_status_display(), note=form.cleaned_data.get("status_note", ""))
    if task.parent_task_id:
        TaskHistory.objects.create(task=task, actor=request.user, event="Parent Task added", new_value=task.parent_task.title)
    log_scope_changes(task, request.user, set())
    log_related_task_changes(task, request.user, set())
    if task.status == Task.Status.IN_PROGRESS:
        sync_task_to_draft_meetings(task, request.user)
    if task.status in {Task.Status.DONE, Task.Status.CANCELLED}:
        release_all_task_boards(task, request.user, "Task completed")
    else:
        sync_task_boards(task, form.cleaned_data.get("boards", []), request.user)
    record_audit(request.user, task, "Task created", {"status": task.status})
    return JsonResponse({"created": True, "task": task_data(task)}, status=201)


def milestone_audit(request, item, action):
    private = item.visibility == TimelineMilestone.Visibility.PRIVATE
    if private:
        # Retrospective shared history must not become a backdoor after privatizing.
        AuditLog.objects.filter(entity_type="TimelineMilestone", entity_id=item.pk).update(is_private=True, private_owner=item.created_by)
    AuditLog.objects.create(actor=request.user, entity_type="TimelineMilestone", entity_id=item.pk,
                            action=action, is_private=private, private_owner=item.created_by if private else None,
                            details=milestone_data(item, request.user))


def save_milestone(request, pk=None):
    denied = active_error(request)
    if denied:
        return denied
    item = get_object_or_404(TimelineMilestone.objects.visible_to(request.user).select_for_update(), pk=pk) if pk else None
    old_visibility = item.visibility if item else "shared"
    if item and not item.can_edit(request.user):
        return HttpResponseForbidden("Only the creator or an Admin can edit a Shared milestone.")
    if item:
        conflict = revision_error(request, item, "milestone", lambda obj: milestone_data(obj, request.user))
        if conflict:
            return conflict
    data = request.POST.copy()
    if item:
        # Date-only drag does not silently erase a milestone's other settings.
        for field in ("name", "date", "color", "notes", "task", "timeline_group", "visibility"):
            if field not in data:
                data[field] = (getattr(item, field + "_id", None) or "") if field in {"task", "timeline_group"} else getattr(item, field)
        if "is_important" not in data and data.get("date_only") == "1":
            data["is_important"] = "on" if item.is_important else ""
        if "scopes" not in data and data.get("date_only") == "1":
            data.setlist("scopes", [str(value) for value in item.scopes.values_list("pk", flat=True)])
    form = TimelineMilestoneForm(data, instance=item)
    if not form.is_valid():
        return form_error(request, form)
    if item and item.created_by_id is None and form.cleaned_data.get("visibility") == "private":
        form.add_error("visibility", "This legacy milestone has no creator and must remain Shared.")
        return form_error(request, form)
    if item and item.created_by_id != request.user.pk and form.cleaned_data["visibility"] != old_visibility:
        return JsonResponse({"error": "Only the creator can change milestone visibility."}, status=403)
    item = form.save(commit=False)
    if not pk:
        item.created_by = request.user
    item.save()
    form.save_m2m()
    milestone_audit(request, item, "Timeline milestone updated" if pk else "Timeline milestone added")
    if wants_json(request):
        return JsonResponse({"milestone": milestone_data(item, request.user)}, status=200 if pk else 201)
    messages.success(request, "Milestone saved.")
    from .views import timeline_redirect
    return timeline_redirect(request)


@login_required
@never_cache
@require_POST
@group_conflict_guard
@transaction.atomic
def timeline_milestone_add(request):
    return save_milestone(request)


@login_required
@never_cache
@require_POST
@group_conflict_guard
@transaction.atomic
def timeline_milestone_edit(request, pk):
    return save_milestone(request, pk)


@login_required
@never_cache
@require_POST
@transaction.atomic
def timeline_milestone_delete(request, pk):
    denied = active_error(request)
    if denied:
        return denied
    item = get_object_or_404(TimelineMilestone.objects.visible_to(request.user).select_for_update(), pk=pk)
    if not item.can_edit(request.user):
        return HttpResponseForbidden("Only the creator or an Admin can delete a Shared milestone.")
    conflict = revision_error(request, item, "milestone", lambda obj: milestone_data(obj, request.user))
    if conflict:
        return conflict
    milestone_audit(request, item, "Timeline milestone deleted")
    item.delete()
    if wants_json(request):
        return JsonResponse({"deleted": True})
    from .views import timeline_redirect
    return timeline_redirect(request)


@login_required
@never_cache
def timeline_milestone_history(request, pk):
    item = get_object_or_404(TimelineMilestone.objects.visible_to(request.user), pk=pk)
    logs = AuditLog.objects.filter(entity_type="TimelineMilestone", entity_id=item.pk).filter(
        Q(is_private=False) | Q(private_owner=request.user)
    ).select_related("actor")[:100]
    return JsonResponse({"history": [{"action": entry.action, "actor": str(entry.actor or "System"),
                                      "date": entry.created_at.isoformat(), "details": entry.details} for entry in logs]})
