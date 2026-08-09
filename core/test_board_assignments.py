from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog, Board, BoardAssignment, Task, User
from core.services import set_manual_board_user


class BoardAssignmentUiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="board-admin", display_name="Board Admin",
            password="test-password", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member = User.objects.create_user(
            username="board-member", display_name="Manual Member",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.task_member = User.objects.create_user(
            username="task-member", display_name="Task Member",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.board = Board.objects.create(name="Assignment Board", barcode="ASSIGN-001")
        self.task = Task.objects.create(
            title="Linked Task", assignee=self.task_member,
            link_url="https://example.test/task", created_by=self.admin,
        )
        self.task_assignment = BoardAssignment.objects.create(
            board=self.board, user=self.task_member, task=self.task,
            source=BoardAssignment.Source.TASK, assigned_by=self.admin,
        )
        self.client.force_login(self.admin)

    def edit_payload(self, **overrides):
        self.board.refresh_from_db()
        payload = {
            "name": self.board.name, "barcode": self.board.barcode,
            "description": self.board.description,
            "revision": self.board.updated_at.isoformat(),
            "manual_users_present": "1",
            "components-TOTAL_FORMS": "0", "components-INITIAL_FORMS": "0",
        }
        payload.update(overrides)
        return payload

    def test_inline_add_returns_updated_partial_with_assigned_user(self):
        response = self.client.post(
            reverse("board_user_action", args=[self.board.pk]),
            {"action": "add", "user_id": self.member.pk}, follow=True,
            HTTP_X_MMP_PARTIAL="board-workspace",
            HTTP_REFERER="http://testserver/boards/",
        )
        self.assertContains(response, 'id="board-workspace"')
        members = response.context["boards"][0].active_user_list
        member = next(member for member in members if member.pk == self.member.pk)
        self.assertEqual(member.manual_count, 1)
        self.assertContains(response, f"Release manual assignment for {self.member}")
        self.assertEqual(BoardAssignment.objects.filter(
            board=self.board, user=self.member, released_at__isnull=True,
        ).count(), 1)

    def test_edit_shows_manual_selection_and_separate_task_assignments(self):
        set_manual_board_user(self.board, self.member, self.admin)
        response = self.client.get(reverse("board_edit", args=[self.board.pk]))
        self.assertEqual(response.context["form"].initial["manual_users"], [self.member.pk])
        self.assertContains(response, 'name="manual_users"')
        self.assertContains(response, "Assigned through Tasks")
        self.assertEqual(list(response.context["task_assigned_users"]), [self.task_member])

    def test_edit_adds_manual_users_and_records_history(self):
        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            self.edit_payload(manual_users=[self.member.pk]),
        )
        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)
        self.assertTrue(BoardAssignment.objects.filter(
            board=self.board, user=self.member, source="manual", released_at__isnull=True,
        ).exists())
        log = AuditLog.objects.filter(entity_type="Board", entity_id=self.board.pk).first()
        self.assertEqual(log.details["changes"]["manual_users"], {"from": "", "to": str(self.member)})
        self.assertContains(self.client.get(reverse("board_history", args=[self.board.pk])), "Assigned Users")

    def test_edit_clears_manual_users_without_releasing_task_assignments(self):
        set_manual_board_user(self.board, self.member, self.admin)
        self.client.post(reverse("board_edit", args=[self.board.pk]), self.edit_payload())
        self.assertFalse(BoardAssignment.objects.filter(
            board=self.board, source="manual", released_at__isnull=True,
        ).exists())
        self.task_assignment.refresh_from_db()
        self.assertIsNone(self.task_assignment.released_at)

    def test_old_edit_payload_preserves_manual_users(self):
        set_manual_board_user(self.board, self.member, self.admin)
        payload = self.edit_payload(description="Old client edit")
        payload.pop("manual_users_present")
        self.client.post(reverse("board_edit", args=[self.board.pk]), payload)
        self.assertTrue(BoardAssignment.objects.filter(
            board=self.board, user=self.member, source="manual", released_at__isnull=True,
        ).exists())

    def test_newly_assigned_user_makes_existing_edit_stale(self):
        payload = self.edit_payload()
        set_manual_board_user(self.board, self.member, self.admin)
        response = self.client.post(reverse("board_edit", args=[self.board.pk]), payload)
        self.assertContains(response, "This Board changed after you opened it.")
        self.assertTrue(BoardAssignment.objects.filter(
            board=self.board, user=self.member, released_at__isnull=True,
        ).exists())

    def test_inactive_manual_user_can_be_preserved_or_released(self):
        set_manual_board_user(self.board, self.member, self.admin)
        self.member.account_status = User.AccountStatus.INACTIVE
        self.member.save()
        response = self.client.get(reverse("board_edit", args=[self.board.pk]))
        self.assertIn(self.member, response.context["form"].fields["manual_users"].queryset)
        self.client.post(reverse("board_user_action", args=[self.board.pk]),
                         {"action": "remove", "user_id": self.member.pk})
        self.assertFalse(BoardAssignment.objects.filter(
            board=self.board, user=self.member, released_at__isnull=True,
        ).exists())

    def test_edit_rejects_new_inactive_user_without_saving_board(self):
        self.member.account_status = User.AccountStatus.INACTIVE
        self.member.save()
        response = self.client.post(reverse("board_edit", args=[self.board.pk]),
                                   self.edit_payload(manual_users=[self.member.pk], description="Invalid"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("manual_users", response.context["form"].errors)
        self.board.refresh_from_db()
        self.assertEqual(self.board.description, "")

    def test_username_is_displayed_when_display_name_is_empty(self):
        self.member.display_name = ""
        self.member.save()
        set_manual_board_user(self.board, self.member, self.admin)
        response = self.client.get(reverse("boards"))
        self.assertContains(response, f"Release manual assignment for {self.member.username}")
