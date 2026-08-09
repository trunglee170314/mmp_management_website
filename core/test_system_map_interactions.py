import json

from django.test import TestCase
from django.urls import reverse

from .models import RelationshipEdge, RelationshipGroup, RelationshipMap, RelationshipNode, User
from .views import relationship_map_data
from .relationship_history import save_relationship_map_revision, restore_relationship_map_revision


class SystemMapDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="system-editor", display_name="System Editor", account_status="active")

    def setUp(self):
        self.client.force_login(self.user)
        self.map = RelationshipMap.objects.create(name="System interaction map", created_by=self.user)
        self.group = RelationshipGroup.objects.create(relationship_map=self.map, name="Runtime", x=20, y=20, width=700, height=300, color="#abcdef")
        self.first = RelationshipNode.objects.create(relationship_map=self.map, group=self.group, name="Decoder", x=60, y=100, color="#336699", description="Search description phrase", node_type="Codec", owner=self.user)
        self.second = RelationshipNode.objects.create(relationship_map=self.map, group=self.group, name="Renderer", x=400, y=100, color="#3B82F6")
        self.edge = RelationshipEdge.objects.create(relationship_map=self.map, source=self.first, target=self.second, relation_type="runs", label="Legacy relation", direction="forward", color="#ff7700", notes="Keep this note")

    def document(self):
        self.map.refresh_from_db()
        return relationship_map_data(self.map)

    def post(self, document, revision=None, **extra):
        if revision is None:
            self.map.refresh_from_db()
            revision = self.map.updated_at.isoformat()
        return self.client.post(reverse("relationship_map_api"), {"action": "save_document", "map_id": self.map.pk, "base_revision": revision, "document": json.dumps(document), **extra})

    def test_batch_move_pin_and_legacy_metadata_are_preserved(self):
        document = self.document()
        document["nodes"][0].update(x=80, keep_position=True)
        response = self.post(document)
        self.assertEqual(response.status_code, 200, response.content)
        self.first.refresh_from_db()
        self.assertEqual(self.first.x, 80)
        self.assertTrue(self.first.keep_position)
        self.assertEqual((self.first.color, self.first.node_type, self.first.owner), ("#336699", "Codec", self.user))
        self.edge.refresh_from_db()
        self.assertEqual((self.edge.label, self.edge.color, self.edge.notes), ("Legacy relation", "#ff7700", "Keep this note"))
        self.assertTrue(self.map.revisions.exists())

    def test_new_negative_ids_remap_groups_nodes_and_edges(self):
        document = self.document()
        document["groups"].append({"id": -1, "name": "New group", "color": "#14B8A6", "x": 900, "y": 100, "width": 500, "height": 300, "is_collapsed": False})
        document["nodes"].append({"id": -2, "name": "New node", "description": "", "external_url": "https://example.test", "color": "#3B82F6", "x": 950, "y": 180, "group_id": -1, "keep_position": False})
        document["edges"].append({"id": -3, "source_id": self.second.pk, "target_id": -2, "direction": "forward", "line_style": "solid"})
        response = self.post(document)
        self.assertEqual(response.status_code, 200, response.content)
        node = RelationshipNode.objects.get(name="New node")
        self.assertGreater(node.pk, 0)
        self.assertEqual(node.group.name, "New group")
        self.assertTrue(self.map.edges.filter(source=self.second, target=node).exists())

    def test_undo_deleted_objects_restores_only_server_authorized_own_ids(self):
        before = self.document()
        changed = self.document()
        changed["nodes"] = [node for node in changed["nodes"] if node["id"] != self.first.pk]
        changed["edges"] = []
        self.assertEqual(self.post(changed).status_code, 200)
        self.assertFalse(RelationshipNode.objects.filter(pk=self.first.pk).exists())
        response = self.post(before, label="Undo edit")
        self.assertEqual(response.status_code, 200, response.content)
        restored = RelationshipNode.objects.get(pk=self.first.pk)
        self.assertEqual(restored.owner, self.user)
        self.assertEqual(restored.node_type, "Codec")
        self.assertEqual(RelationshipEdge.objects.get(pk=self.edge.pk).notes, "Keep this note")

    def test_missing_positive_id_without_own_history_is_rejected(self):
        document = self.document()
        document["nodes"][0]["id"] = 999999
        document["edges"] = []
        response = self.post(document)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.map.nodes.count(), 2)
        self.assertFalse(self.map.revisions.exists())

    def test_foreign_object_id_and_group_reference_are_rejected(self):
        other = RelationshipMap.objects.create(name="Other map")
        foreign = RelationshipNode.objects.create(relationship_map=other, name="Private object")
        document = self.document()
        document["nodes"][0]["id"] = foreign.pk
        document["edges"] = []
        self.assertEqual(self.post(document).status_code, 400)
        foreign.refresh_from_db()
        self.assertEqual(foreign.name, "Private object")
        self.assertEqual(self.map.nodes.count(), 2)

    def test_stale_document_cannot_overwrite_another_editor(self):
        document = self.document()
        stale = document["revision"]
        document["nodes"][0]["name"] = "First save"
        self.assertEqual(self.post(document).status_code, 200)
        document["nodes"][0]["name"] = "Stale overwrite"
        self.assertEqual(self.post(document, revision=stale).status_code, 409)
        self.first.refresh_from_db()
        self.assertEqual(self.first.name, "First save")

    def test_new_custom_color_and_unsafe_link_are_rejected_atomically(self):
        for changed in ({"color": "#123456"}, {"external_url": "javascript:alert(1)"}, {"x": float("nan")}, {"keep_position": "false"}):
            with self.subTest(changed=changed):
                document = self.document()
                document["nodes"][0].update(changed)
                self.assertEqual(self.post(document).status_code, 400)
                self.assertEqual(self.map.nodes.count(), 2)
                self.assertFalse(self.map.revisions.exists())

    def test_legacy_parallel_connections_and_long_descriptions_survive(self):
        self.first.description = "Long note " * 1300
        self.first.save()
        RelationshipEdge.objects.bulk_create([RelationshipEdge(relationship_map=self.map, source=self.second, target=self.first, relation_type="monitors", label="Second relation")])
        response = self.post(self.document())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.map.edges.count(), 2)
        self.first.refresh_from_db()
        self.assertEqual(len(self.first.description), 13000)

    def test_new_duplicate_connection_is_rejected(self):
        document = self.document()
        document["edges"].append({"id": -1, "source_id": self.second.pk, "target_id": self.first.pk, "direction": "none", "line_style": "solid"})
        self.assertEqual(self.post(document).status_code, 400)
        self.assertEqual(self.map.edges.count(), 1)

    def test_copied_edge_cannot_duplicate_original_endpoints(self):
        document = self.document()
        document["edges"].append({**document["edges"][0], "id": -1, "_copied_from": self.edge.pk})
        self.assertEqual(self.post(document).status_code, 400)
        self.assertEqual(self.map.edges.count(), 1)

    def test_clone_preserves_legacy_colors_notes_and_parallel_relations(self):
        self.first.description = "Legacy long description " * 600
        self.first.save()
        RelationshipEdge.objects.bulk_create([RelationshipEdge(
            relationship_map=self.map, source=self.second, target=self.first,
            relation_type="reverse", label="Parallel legacy", notes="Other note",
        )])
        document = self.document()
        copies = {}
        for index, original in enumerate(list(document["nodes"])):
            clone = {**original, "id": -index - 1, "_copied_from": original["id"], "group_id": None, "x": original["x"] + 900}
            copies[original["id"]] = clone["id"]
            document["nodes"].append(clone)
        for index, original in enumerate(list(document["edges"])):
            document["edges"].append({**original, "id": -100 - index, "_copied_from": original["id"], "source_id": copies[original["source_id"]], "target_id": copies[original["target_id"]]})
        response = self.post(document)
        self.assertEqual(response.status_code, 200, response.content)
        clone = self.map.nodes.exclude(pk=self.first.pk).get(name=self.first.name)
        self.assertEqual(clone.color, self.first.color)
        self.assertEqual(clone.description, self.first.description)
        self.assertEqual(clone.owner, self.first.owner)
        self.assertEqual(self.map.edges.count(), 4)
        self.assertEqual(self.map.edges.filter(label="Legacy relation", notes="Keep this note").count(), 2)

    def test_bad_copy_provenance_returns_400_not_500(self):
        for source in ([], {}, True, "1", 999999):
            with self.subTest(source=source):
                document = self.document()
                document["nodes"].append({**document["nodes"][0], "id": -1, "group_id": None, "_copied_from": source})
                response = self.post(document)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.map.nodes.count(), 2)

    def test_document_edit_and_undo_restore_map_details(self):
        before = self.document()
        after = self.document()
        after.update(name="Renamed map", description="New map notes")
        self.assertEqual(self.post(after).status_code, 200)
        self.map.refresh_from_db()
        self.assertEqual(self.map.name, "Renamed map")
        self.assertEqual(self.post(before).status_code, 200)
        self.map.refresh_from_db()
        self.assertEqual(self.map.name, before["name"])

    def test_clipboard_survives_deletion_of_original_group_cluster(self):
        self.first.description = "Legacy notes " * 1200
        self.first.save()
        RelationshipEdge.objects.bulk_create([RelationshipEdge(relationship_map=self.map, source=self.second, target=self.first, label="Parallel", notes="Server note")])
        copied = self.document()
        emptied = self.document()
        emptied.update(nodes=[], groups=[], edges=[])
        self.assertEqual(self.post(emptied).status_code, 200)
        document = self.document()
        group = {**copied["groups"][0], "id": -1, "_copied_from": self.group.pk}
        document["groups"].append(group)
        node_ids = {}
        for index, original in enumerate(copied["nodes"]):
            node_ids[original["id"]] = -10-index
            document["nodes"].append({**original, "id": -10-index, "_copied_from": original["id"], "group_id": -1, "owner_id": None})
        for index, original in enumerate(copied["edges"]):
            document["edges"].append({**original, "id": -100-index, "_copied_from": original["id"], "source_id": node_ids[original["source_id"]], "target_id": node_ids[original["target_id"]], "notes": "Untrusted browser note"})
        response = self.post(document)
        self.assertEqual(response.status_code, 200, response.content)
        clone = self.map.nodes.get(name=self.first.name)
        self.assertEqual(clone.owner, self.user)
        self.assertEqual(clone.description, self.first.description)
        self.assertEqual(clone.color, self.first.color)
        self.assertEqual(self.map.edges.count(), 2)
        self.assertTrue(self.map.edges.filter(notes="Server note").exists())
        self.assertFalse(self.map.edges.filter(notes="Untrusted browser note").exists())

    def test_foreign_clipboard_source_is_rejected_even_with_forged_metadata(self):
        other_map = RelationshipMap.objects.create(name="Other map", created_by=self.user)
        foreign = RelationshipNode.objects.create(relationship_map=other_map, name="Foreign", color="#123456")
        document = self.document()
        document["nodes"].append({**document["nodes"][0], "id": -1, "_copied_from": foreign.pk, "group_id": None})
        self.assertEqual(self.post(document).status_code, 400)
        self.assertEqual(self.map.nodes.count(), 2)

    def test_history_restore_preserves_legacy_parallel_connections(self):
        RelationshipEdge.objects.bulk_create([RelationshipEdge(relationship_map=self.map, source=self.second, target=self.first, label="Reverse", line_style="dashed", direction="bidirectional")])
        revision = save_relationship_map_revision(self.map, self.user, "Legacy snapshot")
        self.map.edges.all().delete()
        restore_relationship_map_revision(self.map, revision, self.user)
        self.assertEqual(self.map.edges.count(), 2)
        self.assertTrue(self.map.edges.filter(label="Reverse", line_style="dashed", direction="bidirectional").exists())

    def _copy_then_edit_source_and_paste(self, *, include_revision, change_visible_values=True):
        self.first.description = "Original legacy description " * 600
        self.first.save()
        copied = self.document()
        original = next(node for node in copied["nodes"] if node["id"] == self.first.pk)
        edited = self.document()
        if change_visible_values:
            node = next(node for node in edited["nodes"] if node["id"] == self.first.pk)
            node.update(color="#3B82F6", description="Changed source description")
        self.assertEqual(self.post(edited).status_code, 200)
        new_owner = User.objects.create_user(username="new-copy-owner", account_status="active")
        RelationshipNode.objects.filter(pk=self.first.pk).update(owner=new_owner)
        document = self.document()
        clone = {**original, "id": -1, "_copied_from": self.first.pk, "group_id": None, "x": 1200, "owner_id": new_owner.pk}
        if include_revision:
            clone["_copied_revision"] = copied["revision"]
        document["nodes"].append(clone)
        response = self.post(document)
        self.assertEqual(response.status_code, 200, response.content)
        pasted = self.map.nodes.exclude(pk=self.first.pk).get(name=original["name"])
        self.assertEqual(pasted.color, original["color"])
        self.assertEqual(pasted.description, original["description"])
        self.assertEqual(pasted.owner, self.user)
        self.first.refresh_from_db()
        self.assertEqual(self.first.owner, new_owner)

    def test_clipboard_revision_preserves_old_legacy_ui_and_hidden_metadata(self):
        self._copy_then_edit_source_and_paste(include_revision=True)

    def test_old_clipboard_client_matches_legacy_values_in_own_history(self):
        self._copy_then_edit_source_and_paste(include_revision=False)

    def test_clipboard_revision_preserves_metadata_when_visible_fields_are_unchanged(self):
        self._copy_then_edit_source_and_paste(include_revision=True, change_visible_values=False)

    def test_invalid_clipboard_revision_is_rejected_without_partial_writes(self):
        for revision in ([], {}, True, 123):
            document = self.document()
            document["nodes"].append({**document["nodes"][0], "id": -1, "_copied_from": self.first.pk, "_copied_revision": revision, "group_id": None})
            self.assertEqual(self.post(document).status_code, 400)
            self.assertEqual(self.map.nodes.count(), 2)

    def test_details_search_shortcuts_and_preset_palette_are_present(self):
        response = self.client.get(reverse("relationship_map"), {"map": self.map.pk})
        self.assertContains(response, 'data-node-details')
        self.assertContains(response, 'Search node names and descriptions')
        self.assertContains(response, 'data-shortcut-dialog')
        self.assertContains(response, 'data-color-palette')
        self.assertNotContains(response, 'type="color"')
