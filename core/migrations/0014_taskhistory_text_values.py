from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_actionitem_published_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="taskhistory",
            name="old_value",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="taskhistory",
            name="new_value",
            field=models.TextField(blank=True),
        ),
    ]
