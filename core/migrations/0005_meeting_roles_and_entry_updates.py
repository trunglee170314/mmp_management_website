import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def populate_meeting_hosts(apps, schema_editor):
    Meeting = apps.get_model("core", "Meeting")
    for meeting in Meeting.objects.all().iterator():
        meeting.host_id = meeting.created_by_id or meeting.minute_taker_id
        meeting.save(update_fields=["host"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0004_board_barcode_and_duplicate_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="host",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="hosted_meetings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(populate_meeting_hosts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="meeting",
            name="host",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="hosted_meetings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="meetingtask",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meetingtask",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="updated_meeting_tasks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
