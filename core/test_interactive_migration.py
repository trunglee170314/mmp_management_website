from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class InteractiveMigrationCompatibilityTests(TransactionTestCase):
    def test_additive_upgrade_preserves_legacy_identity_links_dates_and_colors(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0027_board_component_name_constraint")])
        old = executor.loader.project_state([("core", "0027_board_component_name_constraint")]).apps
        try:
            owner = old.get_model("core", "User").objects.create(username="migration-owner", account_status="active")
            task = old.get_model("core", "Task").objects.create(title="Legacy task", link_url="https://example.test/legacy", timeline_start_date=date(2026, 1, 1), due_date=date(2026, 1, 5), created_by=owner)
            marker = old.get_model("core", "TimelineMilestone").objects.create(name="Legacy gate", date=date(2026, 1, 6), color="#AB12CD", created_by=owner)
            graph = old.get_model("core", "RelationshipMap").objects.create(name="Legacy graph", created_by=owner)
            node = old.get_model("core", "RelationshipNode").objects.create(relationship_map=graph, name="Legacy node", color="#FDFDFD", linked_task_id=task.pk, owner_id=owner.pk, description="Preserved notes", x=123, y=234)
            executor = MigrationExecutor(connection)
            executor.migrate([("core", "0028_interactive_timeline_and_map")])
            new = executor.loader.project_state([("core", "0028_interactive_timeline_and_map")]).apps
            current = new.get_model("core", "TimelineMilestone").objects.get(pk=marker.pk)
            self.assertEqual((current.color, current.date, current.created_by_id), ("#AB12CD", date(2026, 1, 6), owner.pk))
            self.assertEqual(current.visibility, "shared")
            self.assertFalse(current.is_important)
            self.assertIsNone(current.task_id)
            current_node = new.get_model("core", "RelationshipNode").objects.get(pk=node.pk)
            self.assertEqual((current_node.color, current_node.linked_task_id, current_node.owner_id), ("#FDFDFD", task.pk, owner.pk))
            self.assertEqual((current_node.x, current_node.y, current_node.description), (123, 234, "Preserved notes"))
            self.assertFalse(current_node.keep_position)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
