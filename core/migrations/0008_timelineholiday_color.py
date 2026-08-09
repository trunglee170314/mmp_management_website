from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_task_timeline"),
    ]

    operations = [
        migrations.AddField(
            model_name="timelineholiday",
            name="color",
            field=models.CharField(default="#E07849", max_length=7),
        ),
    ]
