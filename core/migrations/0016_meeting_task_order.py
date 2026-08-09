from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_query_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="meeting",
            name="task_order",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("previous", "Previous Meeting"),
                    ("scope", "Scope"),
                    ("assignee", "Assignee"),
                ],
                default="manual",
                max_length=12,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="meeting",
            name="task_order",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("previous", "Previous Meeting"),
                    ("scope", "Scope"),
                    ("assignee", "Assignee"),
                ],
                default="scope",
                max_length=12,
            ),
        ),
    ]
