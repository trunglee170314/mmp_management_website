from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Board,
    BoardAssignment,
    Scope,
    Task,
    TimelineGroup,
    TimelineMilestone,
    User,
)


class MultiSelectFilterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="filter-admin",
            password="test-password",
            display_name="Filter Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member_a = User.objects.create_user(
            username="filter-a",
            password="test-password",
            display_name="Filter A",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member_b = User.objects.create_user(
            username="filter-b",
            password="test-password",
            display_name="Filter B",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.client.force_login(self.admin)

    def create_board(self, name, barcode):
        return Board.objects.create(
            name=name,
            barcode=barcode,
            link_url=f"https://example.com/{barcode.lower()}",
            created_by=self.admin,
            updated_by=self.admin,
        )

    def board_names(self, response):
        return {board.name for board in response.context["boards"]}

    def test_board_filters_support_or_within_category_and_and_across_categories(self):
        board_a = self.create_board("Alpha", "ALPHA")
        board_b = self.create_board("Beta", "BETA")
        self.create_board("Gamma", "GAMMA")
        BoardAssignment.objects.create(
            board=board_a,
            user=self.member_a,
            source=BoardAssignment.Source.MANUAL,
            assigned_by=self.admin,
        )
        BoardAssignment.objects.create(
            board=board_b,
            user=self.member_b,
            source=BoardAssignment.Source.MANUAL,
            assigned_by=self.admin,
        )

        response = self.client.get(reverse("boards"), {
            "board": ["Alpha", "Beta"],
            "assigned_user": [self.member_a.pk, self.member_b.pk],
        })
        self.assertEqual(self.board_names(response), {"Alpha", "Beta"})
        self.assertEqual(response.context["selected_filter_count"], 4)

        response = self.client.get(reverse("boards"), {
            "board": ["Alpha"],
            "assigned_user": [self.member_b.pk],
        })
        self.assertEqual(self.board_names(response), set())

    def test_selecting_both_assignment_states_returns_assigned_and_unassigned_boards(self):
        assigned = self.create_board("Assigned", "ASSIGNED")
        self.create_board("Available", "AVAILABLE")
        BoardAssignment.objects.create(
            board=assigned,
            user=self.member_a,
            source=BoardAssignment.Source.MANUAL,
            assigned_by=self.admin,
        )

        response = self.client.get(reverse("boards"), {
            "assignment": ["assigned", "unassigned"],
        })
        self.assertEqual(self.board_names(response), {"Assigned", "Available"})
        self.assertContains(response, "Clear All")

        response = self.client.get(reverse("boards"), {"assignment": ["assigned"]})
        self.assertEqual(self.board_names(response), {"Assigned"})

        response = self.client.get(reverse("boards"), {"assignment": ["unassigned"]})
        self.assertEqual(self.board_names(response), {"Available"})

    def create_task(self, title, *, status, scope=None, assignee=None, group=None):
        task = Task.objects.create(
            title=title,
            status=status,
            assignee=assignee,
            timeline_group=group,
            timeline_start_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=2),
            link_url=f"https://example.com/tasks/{title.lower()}",
            created_by=self.admin,
        )
        if scope:
            task.scopes.add(scope)
        return task

    def timeline_task_titles(self, response):
        return {
            row["task"].title
            for section in response.context["sections"]
            for row in section["rows"]
        }

    def test_timeline_filters_support_multiple_values_and_cross_category_and(self):
        scope_a = Scope.objects.create(name="Scope A", color="#111111")
        scope_b = Scope.objects.create(name="Scope B", color="#222222")
        group_a = TimelineGroup.objects.create(name="Group A", created_by=self.admin)
        group_b = TimelineGroup.objects.create(name="Group B", created_by=self.admin)
        self.create_task(
            "Todo A", status=Task.Status.TODO, scope=scope_a,
            assignee=self.member_a, group=group_a,
        )
        self.create_task(
            "Done B", status=Task.Status.DONE, scope=scope_b,
            assignee=self.member_b, group=group_b,
        )
        self.create_task("Paused ungrouped", status=Task.Status.PAUSED)

        response = self.client.get(reverse("task_timeline"), {
            "status": [Task.Status.TODO, Task.Status.DONE],
            "scope": [scope_a.pk, scope_b.pk],
            "group": [group_a.pk, group_b.pk],
        })
        self.assertEqual(self.timeline_task_titles(response), {"Todo A", "Done B"})
        self.assertEqual(response.context["selected_filter_count"], 6)

        response = self.client.get(reverse("task_timeline"), {
            "assignee": [self.member_a.pk, "unassigned"],
        })
        self.assertEqual(self.timeline_task_titles(response), {"Todo A", "Paused ungrouped"})
        self.assertContains(response, "Clear All")

    def test_timeline_multiple_scope_filter_keeps_global_and_each_matching_milestone(self):
        scope_a = Scope.objects.create(name="Scope A", color="#111111")
        scope_b = Scope.objects.create(name="Scope B", color="#222222")
        scope_c = Scope.objects.create(name="Scope C", color="#333333")
        milestone_day = timezone.localdate() + timedelta(days=4)
        TimelineMilestone.objects.create(
            name="Global gate", date=milestone_day, created_by=self.admin,
        )
        for name, scope in (("Gate A", scope_a), ("Gate B", scope_b), ("Gate C", scope_c)):
            milestone = TimelineMilestone.objects.create(
                name=name, date=milestone_day, created_by=self.admin,
            )
            milestone.scopes.add(scope)

        response = self.client.get(reverse("task_timeline"), {
            "scope": [scope_a.pk, scope_b.pk],
        })
        day = next(day for day in response.context["days"] if day["date"] == milestone_day)
        self.assertEqual(day["milestone"]["count"], 3)
        self.assertIn("Global gate", day["milestone"]["tooltip"])
        self.assertIn("Gate A", day["milestone"]["tooltip"])
        self.assertIn("Gate B", day["milestone"]["tooltip"])
        self.assertNotIn("Gate C", day["milestone"]["tooltip"])
