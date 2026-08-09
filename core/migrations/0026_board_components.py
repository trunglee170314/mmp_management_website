from django.db import migrations, models
import django.db.models.deletion


def migrate_board_links(apps, schema_editor):
    Board = apps.get_model("core", "Board")
    BoardComponent = apps.get_model("core", "BoardComponent")
    components = [
        BoardComponent(
            board_id=board.pk,
            name="Main",
            link_url=board.link_url,
            position=0,
        )
        for board in Board.objects.exclude(link_url="")
    ]
    BoardComponent.objects.bulk_create(components)


def restore_board_links(apps, schema_editor):
    Board = apps.get_model("core", "Board")
    BoardComponent = apps.get_model("core", "BoardComponent")
    board_components = {}
    for component in BoardComponent.objects.order_by("position", "pk").iterator():
        board_components.setdefault(component.board_id, []).append(component)
    if any(len(components) > 1 for components in board_components.values()):
        raise RuntimeError(
            "Cannot reverse Board components while a Board has multiple links. "
            "Remove extra components or restore from backup first."
        )
    if any(len(board.description or "") > 300 for board in Board.objects.all().iterator()):
        raise RuntimeError(
            "Cannot reverse Board descriptions longer than the legacy 300-character Notes limit."
        )
    for board in Board.objects.all().iterator():
        components = board_components.get(board.pk, [])
        board.link_url = components[0].link_url if components else ""
        board.save(update_fields=["link_url"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_seasonal_theme_preferences"),
    ]

    operations = [
        migrations.RenameField(
            model_name="board",
            old_name="notes",
            new_name="description",
        ),
        migrations.AlterField(
            model_name="board",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="BoardComponent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("link_url", models.URLField(max_length=500)),
                ("position", models.PositiveIntegerField(default=0)),
                ("board", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="components", to="core.board")),
            ],
            options={"ordering": ["position", "pk"]},
        ),
        migrations.AlterField(
            model_name="board",
            name="link_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
        # The reverse function fails before modifying data when the legacy
        # single-link/300-character schema cannot represent the current data.
        migrations.RunPython(migrate_board_links, restore_board_links),
        migrations.RemoveField(
            model_name="board",
            name="link_url",
        ),
    ]
