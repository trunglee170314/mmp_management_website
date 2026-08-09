from django.db import migrations, models
import django.db.models.deletion


def preserve_action_item_sources(apps, schema_editor):
    ActionItem = apps.get_model("core", "ActionItem")
    for item in ActionItem.objects.select_related("meeting").filter(meeting__isnull=False).iterator():
        item.source_meeting_title = item.meeting.title
        item.source_meeting_date = item.meeting.meeting_date
        item.save(update_fields=["source_meeting_title", "source_meeting_date"])


class Migration(migrations.Migration):
    dependencies = [("core", "0010_minute_writer_rotations")]

    operations = [
        migrations.RenameField(model_name="board", old_name="redmine_url", new_name="link_url"),
        migrations.RenameField(model_name="task", old_name="redmine_url", new_name="link_url"),
        migrations.AddField(
            model_name="task",
            name="parent_task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="subtasks",
                to="core.task",
            ),
        ),
        migrations.AddField(
            model_name="actionitem",
            name="source_meeting_title",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="actionitem",
            name="source_meeting_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="actionitem",
            name="meeting",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="action_items",
                to="core.meeting",
            ),
        ),
        migrations.RunPython(preserve_action_item_sources, migrations.RunPython.noop),
    ]
