from django.db import migrations, models


def populate_board_barcodes(apps, schema_editor):
    Board = apps.get_model("core", "Board")
    for board in Board.objects.all().iterator():
        if not board.barcode:
            board.barcode = f"MMP-{board.pk:06d}"
            board.save(update_fields=["barcode"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_passwordresetrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="board",
            name="barcode",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="board",
            name="name",
            field=models.CharField(max_length=100),
        ),
        migrations.RunPython(populate_board_barcodes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="board",
            name="barcode",
            field=models.CharField(max_length=100, unique=True),
        ),
        migrations.AlterModelOptions(
            name="board",
            options={"ordering": ["name", "barcode"]},
        ),
    ]
