from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_generic_links_subtasks_action_sources"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="related_tasks",
            field=models.ManyToManyField(blank=True, symmetrical=True, to="core.task"),
        ),
    ]
