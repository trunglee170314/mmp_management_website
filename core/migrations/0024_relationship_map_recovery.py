from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_relationship_map_revisions(apps, schema_editor):
    RelationshipMap = apps.get_model("core", "RelationshipMap")
    RelationshipMapRevision = apps.get_model("core", "RelationshipMapRevision")
    for relationship_map in RelationshipMap.objects.all().iterator():
        snapshot = {
            "schema_version": 1,
            "map": {
                "name": relationship_map.name,
                "description": relationship_map.description,
            },
            "groups": list(relationship_map.groups.order_by("pk").values(
                "id", "name", "color", "x", "y", "width", "height", "is_collapsed",
            )),
            "nodes": list(relationship_map.nodes.order_by("pk").values(
                "id", "group_id", "name", "node_type", "status", "description", "color",
                "owner_id", "linked_task_id", "linked_board_id", "external_url", "x", "y",
            )),
            "edges": list(relationship_map.edges.order_by("pk").values(
                "id", "source_id", "target_id", "relation_type", "label", "direction",
                "line_style", "color", "notes",
            )),
        }
        RelationshipMapRevision.objects.create(
            relationship_map_id=relationship_map.pk,
            actor_id=relationship_map.created_by_id,
            action="Initial history",
            snapshot=snapshot,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_relationshipedge_line_style"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="relationshipmap",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="relationshipmap",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="relationshipmap",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deleted_relationship_maps",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name="relationshipmap",
            index=models.Index(fields=["is_deleted", "deleted_at"], name="rel_map_trash_expiry_idx"),
        ),
        migrations.AddConstraint(
            model_name="relationshipmap",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(is_deleted=True, deleted_at__isnull=False)
                    | models.Q(is_deleted=False, deleted_at__isnull=True, deleted_by__isnull=True)
                ),
                name="relationship_map_deletion_state_valid",
            ),
        ),
        migrations.CreateModel(
            name="RelationshipMapRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=180)),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="relationship_map_revisions", to=settings.AUTH_USER_MODEL)),
                ("relationship_map", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="core.relationshipmap")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddIndex(
            model_name="relationshipmaprevision",
            index=models.Index(fields=["relationship_map", "-created_at"], name="rel_map_revision_latest_idx"),
        ),
        migrations.RunPython(seed_relationship_map_revisions, migrations.RunPython.noop),
    ]
