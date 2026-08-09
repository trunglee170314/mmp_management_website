import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_rotation_cursors(apps, schema_editor):
    Meeting = apps.get_model("core", "Meeting")
    Rotation = apps.get_model("core", "MinuteWriterRotation")
    RotationMember = apps.get_model("core", "MinuteWriterRotationMember")

    for rotation in Rotation.objects.all().iterator():
        last_writer_id = (
            Meeting.objects.filter(
                writer_rotation_id=rotation.pk,
                writer_assignment="automatic",
                minute_taker_id__isnull=False,
            )
            .order_by("-created_at", "-pk")
            .values_list("minute_taker_id", flat=True)
            .first()
        )
        if last_writer_id and RotationMember.objects.filter(
            rotation_id=rotation.pk,
            user_id=last_writer_id,
        ).exists():
            Rotation.objects.filter(pk=rotation.pk).update(
                last_assigned_writer_id=last_writer_id,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_timelinemilestone"),
    ]

    operations = [
        migrations.AddField(
            model_name="minutewriterrotation",
            name="last_assigned_writer",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="last_assigned_writer_rotations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="minutewriterrotation",
            name="anchor_date",
            field=models.DateField(
                help_text="The first automatic meeting on or after this date uses the first writer.",
            ),
        ),
        migrations.RunPython(seed_rotation_cursors, migrations.RunPython.noop),
    ]
