from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_user_theme_preference"),
    ]

    operations = [
        migrations.AddField(
            model_name="relationshipedge",
            name="line_style",
            field=models.CharField(
                choices=[("solid", "Solid"), ("dashed", "Dashed")],
                default="solid",
                max_length=12,
            ),
        ),
    ]
