from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    ActionItem,
    AuditLog,
    Board,
    BoardAssignment,
    Meeting,
    MinuteWriterRotation,
    MinuteWriterRotationMember,
    Scope,
    Task,
    TaskHistory,
    User,
)
from .services import deactivate_user_and_transfer


def active_user(username, *, role=User.Role.MEMBER):
    return User.objects.create_user(
        username=username,
        password="test-password",
        display_name=username.replace("-", " ").title(),
        role=role,
        account_status=User.AccountStatus.ACTIVE,
    )


class DashboardOperationsTests(TestCase):
    def setUp(self):
        self.admin = active_user("dashboard-admin", role=User.Role.ADMIN)
        self.client.force_login(self.admin)

    def test_team_dashboard_uses_five_metrics_and_groups_active_boards_by_exact_name(self):
        shared_board = Board.objects.create(name="G2H", barcode="G2H-1", link_url="https://example.test/1")
        Board.objects.create(name="G2H", barcode="G2H-2", link_url="https://example.test/2")
        Board.objects.create(name="G2N", barcode="G2N-1", link_url="https://example.test/3")
        Board.objects.create(
            name="G2H", barcode="G2H-ARCHIVED", link_url="https://example.test/4", is_archived=True,
        )
        second_member = active_user("board-member")
        BoardAssignment.objects.create(
            board=shared_board,
            user=self.admin,
            source=BoardAssignment.Source.MANUAL,
            assigned_by=self.admin,
        )
        BoardAssignment.objects.create(
            board=shared_board,
            user=second_member,
            source=BoardAssignment.Source.MANUAL,
            assigned_by=self.admin,
        )

        response = self.client.get(reverse("dashboard"), {"view": "team"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BOARDS IN USE")
        self.assertNotContains(response, "Needs Attention")
        self.assertContains(response, "Boards by Name")
        rows = {row["name"]: row["count"] for row in response.context["board_chart_rows"]}
        self.assertEqual(rows, {"G2H": 2, "G2N": 1})
        self.assertEqual(response.context["board_chart_total"], 3)
        self.assertEqual(response.context["board_availability"], {
            "available": 2,
            "assigned": 0,
            "shared": 1,
        })
        self.assertContains(response, "Board Availability")
        content = response.content.decode()
        self.assertLess(content.index("OVERDUE"), content.index("PAUSED TASKS"))
        self.assertLess(content.index("PAUSED TASKS"), content.index("OPEN ACTION ITEMS"))

    def test_member_summary_keeps_all_members_in_scrollable_region(self):
        for number in range(12):
            active_user(f"member-{number:02d}")

        response = self.client.get(reverse("dashboard"), {"view": "team"})

        self.assertEqual(len(response.context["member_rows"]), 13)
        self.assertContains(response, "member-summary-scroll")
        self.assertContains(response, "Member 11")

    def test_dashboard_switch_is_rendered_as_separate_links(self):
        response = self.client.get(reverse("dashboard"), {"view": "team"})
        self.assertContains(response, 'class="dashboard-mode-switch"')
        self.assertContains(response, ">Team Overview</a>")
        self.assertContains(response, ">My Work</a>")

    def test_completed_scope_chart_includes_uncategorized_tasks(self):
        scope = Scope.objects.create(name="Reported Scope", color="#123456")
        scoped = Task.objects.create(
            title="Scoped completion",
            status=Task.Status.DONE,
            completed_at=timezone.now(),
            link_url="https://example.test/scoped",
            created_by=self.admin,
        )
        scoped.scopes.add(scope)
        Task.objects.create(
            title="Uncategorized completion",
            status=Task.Status.DONE,
            completed_at=timezone.now(),
            link_url="https://example.test/uncategorized",
            created_by=self.admin,
        )

        response = self.client.get(reverse("dashboard"), {
            "view": "team",
            "year": timezone.localdate().year,
        })

        rows = {row["name"]: row["count"] for row in response.context["scope_chart_rows"]}
        self.assertEqual(rows, {"Reported Scope": 1, "Uncategorized": 1})


class UserDeactivationTransferTests(TestCase):
    def setUp(self):
        self.actor = active_user("actor-admin", role=User.Role.ADMIN)
        self.target = active_user("target-admin", role=User.Role.ADMIN)
        self.member = active_user("departing-member")
        self.scope = Scope.objects.create(name="Transfer Scope")
        self.client.force_login(self.actor)

    def make_task(self, title, *, assignee=None, status=Task.Status.TODO):
        task = Task.objects.create(
            title=title,
            assignee=assignee,
            status=status,
            priority=Task.Priority.MEDIUM,
            link_url=f"https://example.test/{title.replace(' ', '-').lower()}",
            created_by=self.member,
            completed_by=self.member if status == Task.Status.DONE else None,
            completed_at=timezone.now() if status == Task.Status.DONE else None,
        )
        task.scopes.add(self.scope)
        return task

    def test_deactivation_page_requires_admin_and_shows_responsibility_counts(self):
        self.make_task("Owned active task", assignee=self.member)
        ActionItem.objects.create(content="Open item", assignee=self.member, created_by=self.actor)

        response = self.client.get(reverse("user_deactivate", args=[self.member.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Responsibilities to transfer")
        self.assertContains(response, "Active tasks")
        self.assertContains(response, self.target.display_name)
        self.assertEqual(response.context["responsibility_counts"]["tasks"], 1)
        self.assertEqual(response.context["responsibility_counts"]["action_items"], 1)

    def test_deactivation_transfers_current_work_and_preserves_historical_owners(self):
        today = timezone.localdate()
        active_task = self.make_task("Active task", assignee=self.member)
        completed_task = self.make_task("Completed task", assignee=self.member, status=Task.Status.DONE)
        action = ActionItem.objects.create(
            task=active_task, content="Follow up", assignee=self.member, created_by=self.member,
        )
        board = Board.objects.create(name="G2H", barcode="TRANSFER-1", link_url="https://example.test/board")
        source_manual = BoardAssignment.objects.create(
            board=board, user=self.member, source=BoardAssignment.Source.MANUAL, assigned_by=self.actor,
        )
        BoardAssignment.objects.create(
            board=board, user=self.target, source=BoardAssignment.Source.MANUAL, assigned_by=self.actor,
        )
        task_assignment = BoardAssignment.objects.create(
            board=board, user=self.member, source=BoardAssignment.Source.TASK,
            task=active_task, assigned_by=self.actor,
        )

        draft = Meeting.objects.create(
            title="Draft meeting", meeting_date=today - timedelta(days=5), host=self.member,
            minute_taker=self.member, created_by=self.member,
        )
        historical = Meeting.objects.create(
            title="Historical meeting", meeting_date=today - timedelta(days=10), host=self.member,
            minute_taker=self.member, created_by=self.member, status=Meeting.Status.FINALIZED,
            finalized_at=timezone.now(),
        )
        future = Meeting.objects.create(
            title="Future meeting", meeting_date=today + timedelta(days=7), host=self.member,
            minute_taker=self.member, created_by=self.member, status=Meeting.Status.FINALIZED,
            finalized_at=timezone.now(),
        )
        future_draft = Meeting.objects.create(
            title="Future draft", meeting_date=today + timedelta(days=14), host=self.member,
            minute_taker=self.member, created_by=self.member,
        )
        rotation = MinuteWriterRotation.objects.create(
            name="Weekly", anchor_date=today, created_by=self.member,
        )
        MinuteWriterRotationMember.objects.create(rotation=rotation, user=self.member, position=1)
        MinuteWriterRotationMember.objects.create(rotation=rotation, user=self.target, position=2)
        rotation.last_assigned_writer = self.member
        rotation.save(update_fields=["last_assigned_writer"])

        response = self.client.post(
            reverse("user_deactivate", args=[self.member.pk]),
            {"transfer_to": self.target.pk},
        )

        self.assertRedirects(response, reverse("users"))
        self.member.refresh_from_db()
        active_task.refresh_from_db()
        completed_task.refresh_from_db()
        action.refresh_from_db()
        source_manual.refresh_from_db()
        task_assignment.refresh_from_db()
        draft.refresh_from_db()
        historical.refresh_from_db()
        future.refresh_from_db()
        future_draft.refresh_from_db()
        self.assertEqual(self.member.account_status, User.AccountStatus.INACTIVE)
        self.assertFalse(self.member.is_active)
        self.assertEqual(active_task.assignee, self.target)
        self.assertEqual(active_task.created_by, self.member)
        self.assertEqual(completed_task.assignee, self.member)
        self.assertEqual(completed_task.completed_by, self.member)
        self.assertEqual(action.assignee, self.target)
        self.assertIsNotNone(source_manual.released_at)
        self.assertEqual(task_assignment.user, self.member)
        self.assertIsNotNone(task_assignment.released_at)
        self.assertTrue(BoardAssignment.objects.filter(
            board=board,
            user=self.target,
            source=BoardAssignment.Source.TASK,
            task=active_task,
            released_at__isnull=True,
        ).exclude(pk=task_assignment.pk).exists())
        self.assertEqual((draft.host, draft.minute_taker), (self.target, self.target))
        self.assertEqual((future_draft.host, future_draft.minute_taker), (self.target, self.target))
        self.assertEqual((future.host, future.minute_taker), (self.member, self.member))
        self.assertEqual((historical.host, historical.minute_taker), (self.member, self.member))
        self.assertEqual(
            list(rotation.writer_members.values_list("user_id", "position")),
            [(self.target.pk, 1)],
        )
        rotation.refresh_from_db()
        self.assertEqual(rotation.last_assigned_writer_id, self.target.pk)
        self.assertTrue(TaskHistory.objects.filter(
            task=active_task, event="Assignee changed", old_value=str(self.member), new_value=str(self.target),
        ).exists())
        self.assertTrue(AuditLog.objects.filter(
            entity_type="User", entity_id=self.member.pk,
            action="User deactivated and responsibilities transferred",
        ).exists())

    def test_invalid_transfer_target_and_last_admin_are_protected(self):
        ordinary_member = active_user("ordinary-target")
        response = self.client.post(
            reverse("user_deactivate", args=[self.member.pk]),
            {"transfer_to": ordinary_member.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.account_status, User.AccountStatus.ACTIVE)

        self.target.account_status = User.AccountStatus.INACTIVE
        self.target.save()
        response = self.client.post(
            reverse("user_deactivate", args=[self.actor.pk]),
            {"transfer_to": self.member.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.actor.refresh_from_db()
        self.assertEqual(self.actor.account_status, User.AccountStatus.ACTIVE)

    def test_deactivation_merge_preserves_existing_target_as_next_writer(self):
        rotation = MinuteWriterRotation.objects.create(
            name="Occurrence merge",
            anchor_date=timezone.localdate(),
            created_by=self.actor,
            last_assigned_writer=self.member,
        )
        MinuteWriterRotationMember.objects.bulk_create([
            MinuteWriterRotationMember(rotation=rotation, user=self.actor, position=1),
            MinuteWriterRotationMember(rotation=rotation, user=self.member, position=2),
            MinuteWriterRotationMember(rotation=rotation, user=self.target, position=3),
        ])

        deactivate_user_and_transfer(self.member, self.target, self.actor)

        rotation.refresh_from_db()
        self.assertEqual(rotation.writer_for(timezone.localdate()), self.target)

    def test_generic_user_edit_cannot_change_account_status(self):
        response = self.client.get(reverse("user_edit", args=[self.member.pk]))
        self.assertNotContains(response, 'name="account_status"')

        response = self.client.post(reverse("user_edit", args=[self.member.pk]), {
            "display_name": self.member.display_name,
            "username": self.member.username,
            "email": self.member.email,
            "role": self.member.role,
            "account_status": User.AccountStatus.INACTIVE,
        })
        self.assertRedirects(response, reverse("users"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.account_status, User.AccountStatus.ACTIVE)

    def test_user_table_does_not_include_active_task_column(self):
        response = self.client.get(reverse("users"))
        self.assertNotContains(response, "<th>Active Tasks</th>", html=True)

    def test_transfer_rolls_back_every_change_when_any_step_fails(self):
        task = self.make_task("Rollback task", assignee=self.member)

        with patch("core.services.record_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                deactivate_user_and_transfer(self.member, self.target, self.actor)

        task.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(task.assignee, self.member)
        self.assertEqual(self.member.account_status, User.AccountStatus.ACTIVE)
        self.assertFalse(TaskHistory.objects.filter(
            task=task,
            event="Assignee changed",
            new_value=str(self.target),
        ).exists())

    def test_registration_actions_cannot_bypass_active_user_transfer_flow(self):
        response = self.client.post(
            reverse("user_action", args=[self.member.pk]),
            {"action": "reject"},
        )
        self.assertRedirects(response, reverse("users"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.account_status, User.AccountStatus.ACTIVE)
