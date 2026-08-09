from django.db import migrations, models
from django.db.models import F, Q


def mark_existing_published_actions(apps, schema_editor):
    ActionItem = apps.get_model("core", "ActionItem")
    ActionItem.objects.filter(
        Q(meeting__isnull=True) | Q(meeting__status="finalized")
    ).update(published_at=F("created_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_task_related_tasks"),
    ]

    operations = [
        migrations.AddField(
            model_name="actionitem",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_published_actions, migrations.RunPython.noop),
    ]
