from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_occurrence_writer_rotation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="actionitem",
            name="is_cancelled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="actionitem",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="actionitem",
            name="cancelled_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cancelled_action_items",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveIndex(model_name="actionitem", name="action_task_state_idx"),
        migrations.RemoveIndex(model_name="actionitem", name="action_owner_state_idx"),
        migrations.AddIndex(
            model_name="actionitem",
            index=models.Index(
                fields=["task", "published_at", "is_cancelled", "is_completed"],
                name="action_task_state_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="actionitem",
            index=models.Index(
                fields=["assignee", "published_at", "is_cancelled", "is_completed"],
                name="action_owner_state_idx",
            ),
        ),
    ]
