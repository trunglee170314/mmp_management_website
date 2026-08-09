import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_task_multiple_scopes"),
    ]

    operations = [
        migrations.CreateModel(
            name="TimelineMilestone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("date", models.DateField(db_index=True)),
                ("color", models.CharField(default="#7C3AED", max_length=7)),
                ("notes", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_timeline_milestones",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "timeline_group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="milestones",
                        to="core.timelinegroup",
                    ),
                ),
                (
                    "scopes",
                    models.ManyToManyField(blank=True, related_name="timeline_milestones", to="core.scope"),
                ),
            ],
            options={"ordering": ["date", "name", "pk"]},
        ),
    ]
