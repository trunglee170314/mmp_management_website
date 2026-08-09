from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_actionitem_cancellation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RelationshipMap",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_relationship_maps", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name", "pk"]},
        ),
        migrations.CreateModel(
            name="RelationshipGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("color", models.CharField(default="#eaf4ee", max_length=7)),
                ("x", models.FloatField(default=80)),
                ("y", models.FloatField(default=80)),
                ("width", models.FloatField(default=360)),
                ("height", models.FloatField(default=260)),
                ("is_collapsed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("relationship_map", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="groups", to="core.relationshipmap")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.CreateModel(
            name="RelationshipNode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                ("node_type", models.CharField(blank=True, max_length=80)),
                ("status", models.CharField(blank=True, max_length=40)),
                ("description", models.TextField(blank=True)),
                ("color", models.CharField(default="#ffffff", max_length=7)),
                ("external_url", models.URLField(blank=True, max_length=500)),
                ("x", models.FloatField(default=160)),
                ("y", models.FloatField(default=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("group", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="nodes", to="core.relationshipgroup")),
                ("linked_board", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="relationship_nodes", to="core.board")),
                ("linked_task", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="relationship_nodes", to="core.task")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="relationship_nodes", to=settings.AUTH_USER_MODEL)),
                ("relationship_map", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nodes", to="core.relationshipmap")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.CreateModel(
            name="RelationshipEdge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("relation_type", models.CharField(blank=True, max_length=80)),
                ("label", models.CharField(blank=True, max_length=120)),
                ("direction", models.CharField(choices=[("none", "No direction"), ("forward", "Forward"), ("bidirectional", "Bidirectional")], default="none", max_length=16)),
                ("color", models.CharField(default="#aeb7b1", max_length=7)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("relationship_map", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="edges", to="core.relationshipmap")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outgoing_relationships", to="core.relationshipnode")),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="incoming_relationships", to="core.relationshipnode")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.AddConstraint(model_name="relationshipgroup", constraint=models.UniqueConstraint(fields=("relationship_map", "name"), name="unique_relationship_group_name")),
        migrations.AddIndex(model_name="relationshipnode", index=models.Index(fields=["relationship_map", "node_type"], name="rel_node_type_idx")),
        migrations.AddConstraint(model_name="relationshipedge", constraint=models.CheckConstraint(condition=models.Q(("source", models.F("target")), _negated=True), name="relationship_edge_distinct_nodes")),
        migrations.AddIndex(model_name="relationshipedge", index=models.Index(fields=["relationship_map", "source", "target"], name="rel_edge_path_idx")),
    ]
