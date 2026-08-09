import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_active_board_assignment_constraints"),
    ]

    operations = [
        migrations.CreateModel(
            name="MinuteWriterRotation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("anchor_date", models.DateField(help_text="The first writer is assigned for the week containing this date.")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_minute_writer_rotations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="MinuteWriterRotationMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "rotation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="writer_members",
                        to="core.minutewriterrotation",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="minute_writer_rotations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["position", "pk"]},
        ),
        migrations.AddConstraint(
            model_name="minutewriterrotationmember",
            constraint=models.UniqueConstraint(fields=("rotation", "user"), name="unique_rotation_writer"),
        ),
        migrations.AddConstraint(
            model_name="minutewriterrotationmember",
            constraint=models.UniqueConstraint(fields=("rotation", "position"), name="unique_rotation_writer_position"),
        ),
        migrations.AddField(
            model_name="meeting",
            name="writer_assignment",
            field=models.CharField(
                choices=[("automatic", "Automatic Rotation"), ("manual", "Manual")],
                default="manual",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="meeting",
            name="writer_rotation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="meetings",
                to="core.minutewriterrotation",
            ),
        ),
    ]
