from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


def release_duplicate_active_links(apps, schema_editor):
    TaskBoard = apps.get_model("core", "TaskBoard")
    BoardAssignment = apps.get_model("core", "BoardAssignment")
    released_at = timezone.now()

    duplicate_task_links = (
        TaskBoard.objects.filter(released_at__isnull=True)
        .values("task_id", "board_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicate_task_links:
        duplicates = TaskBoard.objects.filter(
            task_id=row["task_id"],
            board_id=row["board_id"],
            released_at__isnull=True,
        ).order_by("added_at", "pk")
        keep_id = duplicates.values_list("pk", flat=True).first()
        duplicates.exclude(pk=keep_id).update(
            released_at=released_at,
            release_reason="Duplicate active link cleaned during migration",
        )

    duplicate_manual_assignments = (
        BoardAssignment.objects.filter(source="manual", released_at__isnull=True)
        .values("board_id", "user_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicate_manual_assignments:
        duplicates = BoardAssignment.objects.filter(
            board_id=row["board_id"],
            user_id=row["user_id"],
            source="manual",
            released_at__isnull=True,
        ).order_by("assigned_at", "pk")
        keep_id = duplicates.values_list("pk", flat=True).first()
        duplicates.exclude(pk=keep_id).update(
            released_at=released_at,
            release_reason="Duplicate active assignment cleaned during migration",
        )

    duplicate_task_assignments = (
        BoardAssignment.objects.filter(source="task", released_at__isnull=True)
        .values("board_id", "user_id", "task_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicate_task_assignments:
        duplicates = BoardAssignment.objects.filter(
            board_id=row["board_id"],
            user_id=row["user_id"],
            task_id=row["task_id"],
            source="task",
            released_at__isnull=True,
        ).order_by("assigned_at", "pk")
        keep_id = duplicates.values_list("pk", flat=True).first()
        duplicates.exclude(pk=keep_id).update(
            released_at=released_at,
            release_reason="Duplicate active assignment cleaned during migration",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_timelineholiday_color"),
    ]

    operations = [
        migrations.RunPython(
            release_duplicate_active_links,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="taskboard",
            constraint=models.UniqueConstraint(
                condition=models.Q(released_at__isnull=True),
                fields=("task", "board"),
                name="unique_active_task_board",
            ),
        ),
        migrations.AddConstraint(
            model_name="boardassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(source="manual", released_at__isnull=True),
                fields=("board", "user"),
                name="unique_active_manual_board_assignment",
            ),
        ),
        migrations.AddConstraint(
            model_name="boardassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(source="task", released_at__isnull=True),
                fields=("board", "user", "task"),
                name="unique_active_task_board_assignment",
            ),
        ),
    ]
