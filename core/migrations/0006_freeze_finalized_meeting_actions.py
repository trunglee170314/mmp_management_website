from datetime import timedelta

from django.db import migrations


REVIEW_LABELS = {
    "pending": "Pending",
    "reviewed": "Reviewed",
    "no_update": "No Update",
    "skipped": "Skipped",
}


def user_label(user):
    if not user:
        return ""
    return getattr(user, "display_name", "") or getattr(user, "username", "")


def action_snapshot(item):
    return {
        "id": item.pk,
        "content": item.content,
        "assignee": user_label(item.assignee) or "Unassigned",
        "assignee_id": item.assignee_id,
        "due_date": item.due_date.isoformat() if item.due_date else "",
        "is_completed": item.is_completed,
        "completed_by": user_label(item.completed_by),
        "completed_at": item.completed_at.isoformat() if item.completed_at else "",
        "created_at": item.created_at.isoformat() if item.created_at else "",
    }


def freeze_existing_finalized_meetings(apps, schema_editor):
    Meeting = apps.get_model("core", "Meeting")
    MeetingTask = apps.get_model("core", "MeetingTask")
    ActionItem = apps.get_model("core", "ActionItem")

    for meeting in Meeting.objects.filter(status="finalized").order_by("meeting_date", "created_at"):
        previous = Meeting.objects.exclude(pk=meeting.pk).filter(
            meeting_date__lt=meeting.meeting_date,
        ).order_by("-meeting_date", "-created_at").first()
        completed_after = previous.created_at if previous else meeting.created_at - timedelta(days=36500)

        for entry in MeetingTask.objects.filter(meeting=meeting).order_by("position"):
            snapshot = dict(entry.snapshot or {})
            if entry.task_id:
                open_previous = list(ActionItem.objects.filter(
                    task_id=entry.task_id,
                    is_completed=False,
                    created_at__lt=meeting.created_at,
                ).exclude(meeting_id=meeting.pk).select_related("assignee", "completed_by").order_by("created_at", "pk"))
                recent_completed = list(ActionItem.objects.filter(
                    task_id=entry.task_id,
                    is_completed=True,
                    completed_at__gte=completed_after,
                    created_at__lt=meeting.created_at,
                ).exclude(meeting_id=meeting.pk).select_related("assignee", "completed_by").order_by("-completed_at", "-pk")[:4])
                new_actions = list(ActionItem.objects.filter(
                    task_id=entry.task_id,
                    meeting_id=meeting.pk,
                ).select_related("assignee", "completed_by").order_by("created_at", "pk"))
            else:
                open_previous = []
                recent_completed = []
                new_actions = []

            snapshot.update({
                "snapshot_version": 2,
                "weekly_progress": entry.weekly_progress,
                "review_state": entry.review_state,
                "review_label": REVIEW_LABELS.get(entry.review_state, entry.review_state),
                "previous_actions": [action_snapshot(item) for item in open_previous + recent_completed],
                "new_actions": [action_snapshot(item) for item in new_actions],
            })
            entry.snapshot = snapshot
            entry.save(update_fields=["snapshot"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_meeting_roles_and_entry_updates"),
    ]

    operations = [
        migrations.RunPython(freeze_existing_finalized_meetings, migrations.RunPython.noop),
    ]
