from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0028_interactive_timeline_and_map")]

    operations = [
        migrations.AddField(
            model_name="task",
            name="timeline_position",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, default=None, null=True),
        ),
    ]
