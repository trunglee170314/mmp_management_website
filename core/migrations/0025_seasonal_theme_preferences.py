from django.db import migrations, models


def migrate_theme_preferences(apps, schema_editor):
    user_model = apps.get_model("core", "User")
    mappings = {
        "system": "auto",
        "light": "morning",
        "dark": "evening",
    }
    for old_value, new_value in mappings.items():
        user_model.objects.filter(theme_preference=old_value).update(theme_preference=new_value)


def reverse_theme_preferences(apps, schema_editor):
    user_model = apps.get_model("core", "User")
    mappings = {
        "auto": "system",
        "morning": "light",
        "midday": "light",
        "afternoon": "light",
        "evening": "dark",
    }
    for old_value, new_value in mappings.items():
        user_model.objects.filter(theme_preference=old_value).update(theme_preference=new_value)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_relationship_map_recovery"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="theme_preference",
            field=models.CharField(
                choices=[
                    ("auto", "Auto"),
                    ("morning", "Morning"),
                    ("midday", "Midday"),
                    ("afternoon", "Afternoon"),
                    ("evening", "Evening"),
                ],
                default="auto",
                max_length=9,
            ),
        ),
        migrations.RunPython(migrate_theme_preferences, reverse_theme_preferences),
    ]
