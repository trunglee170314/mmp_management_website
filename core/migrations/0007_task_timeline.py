import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_freeze_finalized_meeting_actions"),
    ]

    operations = [
        migrations.CreateModel(
            name="TimelineGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("color", models.CharField(default="#16835E", max_length=7)),
                ("position", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_timeline_groups", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={"ordering": ["position", "name"]},
        ),
        migrations.CreateModel(
            name="TimelineHoliday",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("repeat_annually", models.BooleanField(default=False)),
                ("notes", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_timeline_holidays", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={"ordering": ["start_date", "name"]},
        ),
        migrations.AddField(
            model_name="task",
            name="timeline_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="core.timelinegroup",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="timeline_start_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
