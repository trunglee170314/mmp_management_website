from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_taskhistory_text_values"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                fields=["is_archived", "status", "due_date"],
                name="task_active_due_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                fields=["is_archived", "status", "completed_at"],
                name="task_completed_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="taskhistory",
            index=models.Index(
                fields=["task", "-created_at"],
                name="taskhist_latest_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="meeting",
            index=models.Index(
                fields=["-meeting_date", "-created_at"],
                name="meeting_latest_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="meetingtask",
            index=models.Index(
                fields=["meeting", "position"],
                name="meetingtask_order_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="actionitem",
            index=models.Index(
                fields=["task", "published_at", "is_completed"],
                name="action_task_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="actionitem",
            index=models.Index(
                fields=["assignee", "published_at", "is_completed"],
                name="action_owner_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["entity_type", "entity_id", "-created_at"],
                name="audit_entity_latest_idx",
            ),
        ),
    ]
