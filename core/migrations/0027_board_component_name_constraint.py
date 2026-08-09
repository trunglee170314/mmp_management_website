from django.db import migrations, models
import django.db.models.functions.text


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_board_components"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="boardcomponent",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"),
                models.F("board"),
                name="unique_board_component_name_ci",
            ),
        ),
    ]
