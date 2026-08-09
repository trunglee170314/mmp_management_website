from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_relationship_map"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="theme_preference",
            field=models.CharField(
                choices=[
                    ("system", "System"),
                    ("light", "Light"),
                    ("dark", "Dark"),
                ],
                default="system",
                max_length=8,
            ),
        ),
    ]
