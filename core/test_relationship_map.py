import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Board,
    RelationshipEdge,
    RelationshipGroup,
    RelationshipMap,
    RelationshipMapRevision,
    RelationshipNode,
    Task,
    User,
)


class RelationshipMapTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="map-member",
            password="test-password",
            display_name="Map Member",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.admin = User.objects.create_user(
            username="map-admin",
            password="test-password",
            display_name="Map Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.client.force_login(self.member)
        self.api_url = reverse("relationship_map_api")

    def post(self, action, **data):
        payload = {"action": action, **data}
        map_id = payload.get("map_id")
        if action != "create_map" and map_id and "base_revision" not in payload:
            relationship_map = RelationshipMap.objects.filter(pk=map_id).first()
            if relationship_map:
                payload["base_revision"] = relationship_map.updated_at.isoformat()
        return self.client.post(
            self.api_url,
            payload,
            HTTP_ACCEPT="application/json",
        )

    def test_active_member_can_manage_complete_map(self):
        response = self.post("create_map", name="Platform", description="Runtime relationships")
        self.assertEqual(response.status_code, 200)
        map_id = response.json()["map"]["id"]

        response = self.post(
            "create_group", map_id=map_id, name="Hardware", color="#dcecff",
            x="20", y="30", width="500", height="300",
        )
        self.assertEqual(response.status_code, 200)
        group_id = response.json()["map"]["groups"][0]["id"]

        first = self.post(
            "create_node", map_id=map_id, group_id=group_id, name="Device A",
            node_type="Device", status="Active", color="#336699",
            external_url="https://example.com/device-a", x="100", y="120",
        )
        second = self.post(
            "create_node", map_id=map_id, name="Codec Engine",
            node_type="Codec", color="#ff7700", x="500", y="120",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        source_id = first.json()["map"]["nodes"][0]["id"]
        target_id = next(
            node["id"] for node in second.json()["map"]["nodes"]
            if node["name"] == "Codec Engine"
        )

        edge = self.post(
            "create_edge", map_id=map_id, source_id=source_id,
            target_id=target_id, relation_type="runs", label="Runtime",
            direction=RelationshipEdge.Direction.BIDIRECTIONAL, color="#ff1744",
        )
        self.assertEqual(edge.status_code, 200)
        payload = edge.json()["map"]
        self.assertEqual(len(payload["nodes"]), 2)
        self.assertEqual(payload["edges"][0]["direction"], "bidirectional")
        self.assertEqual(payload["edges"][0]["line_style"], "solid")
        self.assertEqual(RelationshipMap.objects.get(pk=map_id).created_by, self.member)

    def test_node_references_are_validated(self):
        relationship_map = RelationshipMap.objects.create(name="Map A", created_by=self.member)
        other_map = RelationshipMap.objects.create(name="Map B", created_by=self.member)
        other_group = RelationshipGroup.objects.create(relationship_map=other_map, name="Other")
        inactive = User.objects.create_user(
            username="inactive-owner", password="test-password", display_name="Inactive",
            account_status=User.AccountStatus.INACTIVE,
        )
        archived_task = Task.objects.create(
            title="Archived", link_url="https://example.com/task", is_archived=True,
            created_by=self.member,
        )
        archived_board = Board.objects.create(
            name="Archived", barcode="ARCH",
            is_archived=True, created_by=self.member,
        )
        invalid_cases = [
            ({"group_id": other_group.pk}, "Group"),
            ({"owner_id": inactive.pk}, "active owner"),
            ({"linked_task_id": archived_task.pk}, "active Task"),
            ({"linked_board_id": archived_board.pk}, "active Board"),
            ({"external_url": "javascript:alert(1)"}, "http:// or https://"),
        ]
        for extra, message in invalid_cases:
            with self.subTest(extra=extra):
                response = self.post(
                    "create_node", map_id=relationship_map.pk, name="Invalid", **extra,
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()["error"])
        self.assertFalse(relationship_map.nodes.exists())

    def test_edges_cannot_cross_maps_or_connect_node_to_itself(self):
        first_map = RelationshipMap.objects.create(name="First", created_by=self.member)
        second_map = RelationshipMap.objects.create(name="Second", created_by=self.member)
        first = RelationshipNode.objects.create(relationship_map=first_map, name="First")
        second = RelationshipNode.objects.create(relationship_map=second_map, name="Second")

        cross_map = self.post(
            "create_edge", map_id=first_map.pk, source_id=first.pk,
            target_id=second.pk, direction="none",
        )
        self.assertEqual(cross_map.status_code, 404)
        self_connection = self.post(
            "create_edge", map_id=first_map.pk, source_id=first.pk,
            target_id=first.pk, direction="none",
        )
        self.assertEqual(self_connection.status_code, 400)
        self.assertFalse(RelationshipEdge.objects.exists())

    def test_duplicate_connection_is_rejected_in_either_direction(self):
        relationship_map = RelationshipMap.objects.create(name="No duplicates", created_by=self.member)
        first = RelationshipNode.objects.create(relationship_map=relationship_map, name="First")
        second = RelationshipNode.objects.create(relationship_map=relationship_map, name="Second")
        RelationshipEdge.objects.create(relationship_map=relationship_map, source=first, target=second)

        response = self.post(
            "create_edge", map_id=relationship_map.pk,
            source_id=second.pk, target_id=first.pk, direction="none",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already connected", response.json()["error"])
        self.assertEqual(relationship_map.edges.count(), 1)
        with self.assertRaises(ValidationError):
            RelationshipEdge.objects.create(
                relationship_map=relationship_map, source=second, target=first,
            )

    def test_layout_group_deletion_and_map_trash_are_supported(self):
        relationship_map = RelationshipMap.objects.create(name="Mutable", created_by=self.member)
        group = RelationshipGroup.objects.create(
            relationship_map=relationship_map, name="Group", width=800, height=500,
        )
        node = RelationshipNode.objects.create(
            relationship_map=relationship_map, group=group, name="Movable",
        )
        moved = self.post(
            "layout_entities", map_id=relationship_map.pk,
            groups="[]",
            nodes=json.dumps([{"id": node.pk, "x": 420.5, "y": 210.25, "group_id": group.pk}]),
        )
        self.assertEqual(moved.status_code, 200)
        node.refresh_from_db()
        self.assertEqual((node.x, node.y), (420.5, 210.25))

        deleted_group = self.post(
            "delete_group", map_id=relationship_map.pk, group_id=group.pk,
        )
        self.assertEqual(deleted_group.status_code, 200)
        node.refresh_from_db()
        self.assertIsNone(node.group)

        denied = self.post("delete_map", map_id=relationship_map.pk)
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.admin)
        deleted_map = self.post("delete_map", map_id=relationship_map.pk)
        self.assertEqual(deleted_map.status_code, 200)
        relationship_map.refresh_from_db()
        self.assertTrue(relationship_map.is_deleted)
        self.assertEqual(relationship_map.deleted_by, self.admin)
        self.assertTrue(relationship_map.nodes.filter(pk=node.pk).exists())

    def test_history_restore_recovers_previous_map_state(self):
        created = self.post("create_map", name="Recoverable")
        relationship_map = RelationshipMap.objects.get(pk=created.json()["map"]["id"])
        added = self.post("create_node", map_id=relationship_map.pk, name="Original")
        node_id = added.json()["map"]["nodes"][0]["id"]
        updated = self.post(
            "update_node", map_id=relationship_map.pk, node_id=node_id, name="Changed",
        )
        self.assertEqual(updated.status_code, 200)
        restore_point = RelationshipMapRevision.objects.get(
            relationship_map=relationship_map,
            action="Before Node updated",
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse(
            "relationship_map_revision_restore",
            args=[relationship_map.pk, restore_point.pk],
        ))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(relationship_map.nodes.values_list("name", flat=True)), ["Original"])
        self.assertTrue(relationship_map.revisions.filter(action="Before history restore").exists())

    def test_trash_is_admin_only_and_map_can_be_restored(self):
        relationship_map = RelationshipMap.objects.create(name="Trash me", created_by=self.member)
        self.assertEqual(self.client.get(reverse("relationship_map_trash")).status_code, 403)
        self.client.force_login(self.admin)
        deleted = self.post("delete_map", map_id=relationship_map.pk)
        self.assertEqual(deleted.status_code, 200)
        page = self.client.get(reverse("relationship_map"))
        self.assertNotContains(page, "Trash me")
        trash = self.client.get(reverse("relationship_map_trash"))
        self.assertContains(trash, "Trash me")

        restored = self.client.post(reverse("relationship_map_trash_restore", args=[relationship_map.pk]))
        self.assertEqual(restored.status_code, 302)
        relationship_map.refresh_from_db()
        self.assertFalse(relationship_map.is_deleted)
        self.assertIsNone(relationship_map.deleted_at)

    def test_expired_trash_is_purged(self):
        relationship_map = RelationshipMap.objects.create(
            name="Expired trash",
            created_by=self.member,
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=31),
        )
        self.client.get(reverse("relationship_map"))
        self.assertTrue(RelationshipMap.objects.filter(pk=relationship_map.pk).exists())
        call_command("purge_relationship_map_trash")
        self.assertFalse(RelationshipMap.objects.filter(pk=relationship_map.pk).exists())

    def test_compose_defines_automatic_backup_and_maintenance(self):
        compose = (settings.BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8")
        worker = (settings.BASE_DIR / "deploy/backup-worker.sh").read_text(encoding="utf-8")
        self.assertIn("  backup:", compose)
        self.assertIn("pg_dump", worker)
        self.assertIn("backup_data:/backups", compose)
        self.assertIn("umask 077", worker)
        self.assertIn(".last-success", worker)
        self.assertIn("  maintenance:", compose)
        self.assertIn("purge_relationship_map_trash", compose)

    def test_layout_history_is_coalesced_within_one_editing_session(self):
        relationship_map = RelationshipMap.objects.create(name="Coalesced", created_by=self.member)
        node = RelationshipNode.objects.create(relationship_map=relationship_map, name="Movable")
        for x in (300, 500):
            response = self.post(
                "layout_entities", map_id=relationship_map.pk,
                groups="[]",
                nodes=json.dumps([{"id": node.pk, "x": x, "y": 300, "group_id": None}]),
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(
            relationship_map.revisions.filter(action="Before layout changed").count(),
            1,
        )

    def test_group_node_and_connection_can_all_be_updated(self):
        relationship_map = RelationshipMap.objects.create(name="Editable", created_by=self.member)
        group = RelationshipGroup.objects.create(relationship_map=relationship_map, name="Old Group")
        first = RelationshipNode.objects.create(relationship_map=relationship_map, name="First")
        second = RelationshipNode.objects.create(relationship_map=relationship_map, name="Second")
        edge = RelationshipEdge.objects.create(
            relationship_map=relationship_map,
            source=first,
            target=second,
        )

        updated_group = self.post(
            "update_group", map_id=relationship_map.pk, group_id=group.pk,
            name="New Group", color="#112233", x="120", y="130",
            width="640", height="420", is_collapsed="true",
        )
        self.assertEqual(updated_group.status_code, 200)
        group.refresh_from_db()
        self.assertEqual(
            (group.name, group.color, group.x, group.y, group.width, group.height, group.is_collapsed),
            ("New Group", "#112233", 120.0, 130.0, 640.0, 420.0, True),
        )

        updated_node = self.post(
            "update_node", map_id=relationship_map.pk, node_id=first.pk,
            group_id=group.pk, name="Updated First", node_type="Device",
            status="Active", color="#334455", x="180", y="210",
        )
        self.assertEqual(updated_node.status_code, 200)
        first.refresh_from_db()
        self.assertEqual((first.name, first.group_id, first.x, first.y), ("Updated First", group.pk, 180.0, 210.0))

        updated_edge = self.post(
            "update_edge", map_id=relationship_map.pk, edge_id=edge.pk,
            source_id=first.pk, target_id=second.pk, relation_type="depends on",
            label="Runtime", direction="forward", line_style="dashed",
            color="#556677", notes="Updated",
        )
        self.assertEqual(updated_edge.status_code, 200)
        edge.refresh_from_db()
        self.assertEqual(
            (edge.relation_type, edge.label, edge.direction, edge.line_style),
            ("depends on", "Runtime", "forward", "dashed"),
        )

    def test_line_style_update_preserves_legacy_bidirectional_direction(self):
        relationship_map = RelationshipMap.objects.create(name="Legacy direction", created_by=self.member)
        first = RelationshipNode.objects.create(relationship_map=relationship_map, name="First")
        second = RelationshipNode.objects.create(relationship_map=relationship_map, name="Second")
        edge = RelationshipEdge.objects.create(
            relationship_map=relationship_map,
            source=first,
            target=second,
            direction=RelationshipEdge.Direction.BIDIRECTIONAL,
        )

        response = self.post(
            "update_edge", map_id=relationship_map.pk, edge_id=edge.pk,
            source_id=first.pk, target_id=second.pk, line_style="dashed",
        )

        self.assertEqual(response.status_code, 200)
        edge.refresh_from_db()
        self.assertEqual(edge.direction, RelationshipEdge.Direction.BIDIRECTIONAL)
        self.assertEqual(edge.line_style, RelationshipEdge.LineStyle.DASHED)

    def test_layout_entities_moves_groups_and_nodes_and_updates_membership(self):
        relationship_map = RelationshipMap.objects.create(name="Layout", created_by=self.member)
        group = RelationshipGroup.objects.create(relationship_map=relationship_map, name="Container")
        node = RelationshipNode.objects.create(relationship_map=relationship_map, name="Movable")

        response = self.post(
            "layout_entities",
            map_id=relationship_map.pk,
            groups=json.dumps([{
                "id": group.pk, "x": 240, "y": 180, "width": 520,
                "height": 360, "is_collapsed": True,
            }]),
            nodes=json.dumps([{
                "id": node.pk, "x": 310, "y": 260, "group_id": group.pk,
            }]),
        )
        self.assertEqual(response.status_code, 200)
        group.refresh_from_db()
        node.refresh_from_db()
        self.assertEqual((group.x, group.y, group.width, group.height, group.is_collapsed), (240.0, 180.0, 520.0, 360.0, True))
        self.assertEqual((node.x, node.y, node.group_id), (310.0, 260.0, group.pk))

    def test_layout_entities_preserves_multi_node_group_move(self):
        relationship_map = RelationshipMap.objects.create(name="Batch layout", created_by=self.member)
        group = RelationshipGroup.objects.create(
            relationship_map=relationship_map, name="Container",
            x=100, y=100, width=500, height=300,
        )
        first = RelationshipNode.objects.create(
            relationship_map=relationship_map, group=group, name="First",
            x=120, y=160,
        )
        second = RelationshipNode.objects.create(
            relationship_map=relationship_map, group=group, name="Second",
            x=330, y=160,
        )

        response = self.post(
            "layout_entities", map_id=relationship_map.pk,
            groups=json.dumps([{
                "id": group.pk, "x": 300, "y": 260,
                "width": 500, "height": 300,
            }]),
            nodes=json.dumps([
                {"id": first.pk, "x": 320, "y": 320, "group_id": group.pk},
                {"id": second.pk, "x": 530, "y": 320, "group_id": group.pk},
            ]),
        )

        self.assertEqual(response.status_code, 200)
        group.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((group.x, group.y), (300.0, 260.0))
        self.assertEqual((first.x, first.y), (320.0, 320.0))
        self.assertEqual((second.x, second.y), (530.0, 320.0))

    def test_invalid_multi_node_layout_rolls_back_group_and_nodes(self):
        relationship_map = RelationshipMap.objects.create(name="Atomic layout", created_by=self.member)
        group = RelationshipGroup.objects.create(
            relationship_map=relationship_map, name="Container",
            x=100, y=100, width=500, height=300,
        )
        first = RelationshipNode.objects.create(
            relationship_map=relationship_map, group=group, name="First",
            x=120, y=160,
        )
        second = RelationshipNode.objects.create(
            relationship_map=relationship_map, group=group, name="Second",
            x=330, y=160,
        )

        response = self.post(
            "layout_entities", map_id=relationship_map.pk,
            groups=json.dumps([{
                "id": group.pk, "x": 300, "y": 260,
                "width": 500, "height": 300,
            }]),
            nodes=json.dumps([
                {"id": first.pk, "x": 320, "y": 320, "group_id": group.pk},
                {"id": second.pk, "x": 320, "y": 320, "group_id": group.pk},
            ]),
        )

        self.assertEqual(response.status_code, 400)
        group.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((group.x, group.y), (100.0, 100.0))
        self.assertEqual((first.x, first.y), (120.0, 160.0))
        self.assertEqual((second.x, second.y), (330.0, 160.0))

    def test_layout_entities_rejects_cross_map_group_without_partial_update(self):
        relationship_map = RelationshipMap.objects.create(name="Safe Layout", created_by=self.member)
        other_map = RelationshipMap.objects.create(name="Other Layout", created_by=self.member)
        group = RelationshipGroup.objects.create(relationship_map=relationship_map, name="Container")
        other_group = RelationshipGroup.objects.create(relationship_map=other_map, name="Other")
        node = RelationshipNode.objects.create(relationship_map=relationship_map, name="Movable", x=10, y=20)

        response = self.post(
            "layout_entities",
            map_id=relationship_map.pk,
            groups=json.dumps([{"id": group.pk, "x": 500, "y": 400, "width": 500, "height": 300}]),
            nodes=json.dumps([{"id": node.pk, "x": 600, "y": 450, "group_id": other_group.pk}]),
        )
        self.assertEqual(response.status_code, 400)
        group.refresh_from_db()
        node.refresh_from_db()
        self.assertEqual((group.x, group.y), (80.0, 80.0))
        self.assertEqual((node.x, node.y, node.group_id), (10.0, 20.0, None))

    def test_legacy_node_links_are_preserved_but_not_exposed_in_map_payload(self):
        relationship_map = RelationshipMap.objects.create(name="Links", created_by=self.member)
        task = Task.objects.create(
            title="Linked Task", link_url="https://example.com/task", created_by=self.member,
        )
        board = Board.objects.create(
            name="Linked Board", barcode="LINK", created_by=self.member,
        )
        RelationshipNode.objects.create(
            relationship_map=relationship_map, name="Linked", linked_task=task, linked_board=board,
        )
        response = self.client.get(reverse("relationship_map"), {"map": relationship_map.pk})
        self.assertNotContains(response, "https://example.com/task")
        self.assertNotContains(response, "https://example.com/board")
        node = relationship_map.nodes.get()
        self.assertEqual((node.linked_task_id, node.linked_board_id), (task.pk, board.pk))

    def test_canvas_layers_do_not_block_groups_or_connections(self):
        css = (settings.BASE_DIR / "assets" / "core" / "relationship-map.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r"\.relationship-edges,\s*\.relationship-groups,\s*\.relationship-nodes\s*\{[^}]*pointer-events:\s*none",
        )
        self.assertIn(".relationship-group-resize", css)

    def test_empty_page_initializes_map_creation_before_canvas_guard(self):
        RelationshipMap.objects.all().delete()
        response = self.client.get(reverse("relationship_map"))
        self.assertContains(response, "data-new-map")
        javascript = (settings.BASE_DIR / "assets" / "core" / "relationship-map.js").read_text(encoding="utf-8")
        self.assertLess(
            javascript.index("mapForm.addEventListener('submit'"),
            javascript.index("if (!graph)"),
        )

    def test_archived_links_and_inactive_owner_survive_node_edit(self):
        relationship_map = RelationshipMap.objects.create(name="Historical Links", created_by=self.member)
        inactive = User.objects.create_user(
            username="old-owner", password="test-password", display_name="Old Owner",
            account_status=User.AccountStatus.INACTIVE,
        )
        task = Task.objects.create(
            title="Archived Task", link_url="https://example.com/archived-task",
            is_archived=True, created_by=self.member,
        )
        board = Board.objects.create(
            name="Archived Board", barcode="OLD",
            is_archived=True, created_by=self.member,
        )
        node = RelationshipNode.objects.create(
            relationship_map=relationship_map, name="Historical Node", owner=inactive,
            linked_task=task, linked_board=board,
        )

        response = self.post(
            "update_node", map_id=relationship_map.pk, node_id=node.pk,
            name="Renamed Historical Node", owner_id=inactive.pk,
            linked_task_id=task.pk, linked_board_id=board.pk,
        )
        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual((node.owner_id, node.linked_task_id, node.linked_board_id), (inactive.pk, task.pk, board.pk))

        other = self.post(
            "create_node", map_id=relationship_map.pk, name="Invalid historical assignment",
            owner_id=inactive.pk, linked_task_id=task.pk, linked_board_id=board.pk,
        )
        self.assertEqual(other.status_code, 400)

    def test_child_mutation_updates_revision_and_rejects_stale_write(self):
        relationship_map = RelationshipMap.objects.create(name="Revisioned", created_by=self.member)
        stale_revision = relationship_map.updated_at.isoformat()
        created = self.post(
            "create_group", map_id=relationship_map.pk, name="Current",
            base_revision=stale_revision,
        )
        self.assertEqual(created.status_code, 200)
        current_revision = created.json()["map"]["revision"]
        self.assertNotEqual(current_revision, stale_revision)

        stale = self.post(
            "update_map", map_id=relationship_map.pk, name="Overwritten",
            base_revision=stale_revision,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale["Content-Type"], "application/json")
        relationship_map.refresh_from_db()
        self.assertEqual(relationship_map.name, "Revisioned")

    def test_api_validates_names_and_boolean_values(self):
        relationship_map = RelationshipMap.objects.create(name="Validation", created_by=self.member)
        too_long = self.post("create_group", map_id=relationship_map.pk, name="x" * 121)
        self.assertEqual(too_long.status_code, 400)

        group = RelationshipGroup.objects.create(relationship_map=relationship_map, name="Boolean")
        false_value = self.post(
            "layout_entities", map_id=relationship_map.pk, nodes="[]",
            groups=json.dumps([{"id": group.pk, "is_collapsed": "false"}]),
        )
        self.assertEqual(false_value.status_code, 200)
        group.refresh_from_db()
        self.assertFalse(group.is_collapsed)

    def test_mutation_requires_revision(self):
        relationship_map = RelationshipMap.objects.create(name="Revision Required", created_by=self.member)
        response = self.client.post(
            self.api_url,
            {"action": "create_group", "map_id": relationship_map.pk, "name": "Missing Revision"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 428)
        self.assertFalse(relationship_map.groups.exists())

    def test_page_renders_canvas_and_neon_interaction_assets(self):
        relationship_map = RelationshipMap.objects.create(name="Visible", created_by=self.member)
        response = self.client.get(reverse("relationship_map"), {"map": relationship_map.pk})
        self.assertContains(response, "System Relationship Map")
        self.assertContains(response, "data-map-viewport")
        self.assertContains(response, "relationship-map.js")

    def test_assigning_group_places_node_inside_and_expands_group(self):
        relationship_map = RelationshipMap.objects.create(name="Auto place", created_by=self.member)
        group = RelationshipGroup.objects.create(
            relationship_map=relationship_map, name="Container",
            x=100, y=100, width=240, height=140,
        )
        first = RelationshipNode.objects.create(
            relationship_map=relationship_map, group=group, name="Existing",
            x=115, y=152,
        )
        second = RelationshipNode.objects.create(
            relationship_map=relationship_map, name="Move me", node_type="Legacy Type",
            x=900, y=600,
        )

        response = self.post(
            "update_node", map_id=relationship_map.pk, node_id=second.pk,
            group_id=group.pk, name=second.name,
        )
        self.assertEqual(response.status_code, 200)
        second.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(second.group_id, group.pk)
        self.assertNotEqual((second.x, second.y), (first.x, first.y))
        self.assertGreaterEqual(second.x, group.x)
        self.assertGreaterEqual(second.y, group.y + 52)
        self.assertLessEqual(second.x + 190, group.x + group.width)
        self.assertLessEqual(second.y + 76, group.y + group.height)
        self.assertGreater(group.height, 140)
        self.assertEqual(second.node_type, "Legacy Type")

    def test_group_at_canvas_bottom_moves_and_expands_without_overlapping_nodes(self):
        relationship_map = RelationshipMap.objects.create(name="Bottom group", created_by=self.member)
        group = RelationshipGroup.objects.create(
            relationship_map=relationship_map, name="Bottom",
            x=1380, y=860, width=220, height=140,
        )
        nodes = []
        for number in range(10):
            response = self.post(
                "create_node", map_id=relationship_map.pk, group_id=group.pk,
                name=f"Node {number}",
            )
            self.assertEqual(response.status_code, 200)
            nodes.append(RelationshipNode.objects.get(relationship_map=relationship_map, name=f"Node {number}"))

        group.refresh_from_db()
        positions = {(node.x, node.y) for node in nodes}
        self.assertEqual(len(positions), len(nodes))
        self.assertGreater(group.width, 220)
        self.assertLess(group.y, 860)
        for node in nodes:
            self.assertTrue(node.group_id == group.pk and node.x >= group.x and node.y >= group.y)
            self.assertLessEqual(node.x + 190, group.x + group.width)
            self.assertLessEqual(node.y + 76, group.y + group.height)

    def test_system_map_ui_uses_groups_without_node_type_controls(self):
        relationship_map = RelationshipMap.objects.create(name="Simplified", created_by=self.member)
        response = self.client.get(reverse("relationship_map"), {"map": relationship_map.pk})
        self.assertNotContains(response, "data-type-filter")
        self.assertNotContains(response, 'name="node_type"')
        self.assertNotContains(response, "data-map-inspector")
        self.assertNotContains(response, 'name="status"')
        self.assertNotContains(response, 'name="owner_id"')
        self.assertNotContains(response, 'name="linked_task_id"')
        self.assertNotContains(response, 'name="linked_board_id"')
        self.assertContains(response, "data-map-context-menu")
        self.assertContains(response, "data-entity-toolbar")
        self.assertContains(response, "data-edge-toolbar")
        self.assertContains(response, "data-line-style-popover")
        self.assertContains(response, "data-arrow-popover")
        self.assertContains(response, 'data-arrow-direction="bidirectional"')
        self.assertContains(response, "data-group-draw-preview")
        self.assertContains(response, "Right-click the canvas to add the first Node.")
        self.assertContains(response, 'aria-label="Select map"')
        self.assertNotContains(response, '<span class="sr-only">Map</span>')
        self.assertNotContains(response, "Switch to Edit")
        javascript = (settings.BASE_DIR / "assets" / "core" / "relationship-map.js").read_text(encoding="utf-8")
        css = (settings.BASE_DIR / "assets" / "core" / "relationship-map.css").read_text(encoding="utf-8")
        self.assertIn("data-connect-handle", javascript)
        self.assertIn("keyboardConnectionSourceId", javascript)
        self.assertIn("cancelGroupDrawing", javascript)
        self.assertIn("event.button !== 0", javascript)
        self.assertIn("Resize Group with arrow keys", javascript)
        self.assertNotIn("data-inspector-edge-form", javascript)
        self.assertIn("data-line-style", javascript)
        self.assertIn("data-arrow-direction", javascript)
        self.assertIn("marker-start", javascript)
        self.assertNotIn("bidirectional: 'none'", javascript)
        self.assertNotIn("entityToolbar.innerHTML = `<strong>", javascript)
        self.assertIn(".map-empty[hidden]", css)
        self.assertIn(".map-selector {", css)

    def test_inactive_user_cannot_access_or_mutate(self):
        self.member.account_status = User.AccountStatus.INACTIVE
        self.member.save(update_fields=["account_status"])
        self.assertEqual(self.client.get(reverse("relationship_map")).status_code, 302)
        response = self.post("create_map", name="Denied")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RelationshipMap.objects.exists())


class HelpDocumentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="help-user", password="test-password", display_name="Help User",
            account_status=User.AccountStatus.ACTIVE,
        )

    def test_help_pages_require_login(self):
        for name in ("help", "release_notes"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)

    def test_guide_and_release_notes_render_versioned_content(self):
        self.client.force_login(self.user)
        guide = self.client.get(reverse("help"))
        self.assertContains(guide, "A new Meeting starts with every current")
        self.assertContains(guide, "appended to every active Draft")
        self.assertContains(guide, "Arrow Up")
        self.assertContains(guide, "neon red")
        release = self.client.get(reverse("release_notes"))
        self.assertContains(release, "Version 1.0.0")
        self.assertContains(release, "28 September 2026")
