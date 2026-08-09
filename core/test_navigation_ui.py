from django.test import TestCase
from django.urls import reverse

from core.forms import TaskForm
from core.models import Board, BoardAssignment, Scope, Task, User


class TaskReturnNavigationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="navigation-admin",
            password="test-password",
            display_name="Navigation Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Navigation", color="#247357", position=1)
        self.task = Task.objects.create(
            title="Overdue navigation task",
            link_url="https://tasks.example.test/navigation",
            created_by=self.user,
        )
        self.task.scopes.add(self.scope)
        self.client.force_login(self.user)

    def form_data(self, **overrides):
        data = {
            "title": self.task.title,
            "description": "",
            "scopes": [self.scope.pk],
            "parent_task": "",
            "related_tasks": [],
            "assignee": "",
            "status": Task.Status.TODO,
            "timeline_start_date": "",
            "due_date": "",
            "priority": Task.Priority.MEDIUM,
            "boards": [],
            "link_url": self.task.link_url,
            "status_note": "",
        }
        data.update(overrides)
        return data

    def test_save_returns_to_valid_filtered_task_url(self):
        return_url = f'{reverse("tasks")}?status=overdue&assignee=unassigned&page=2'

        response = self.client.post(
            reverse("task_edit", args=[self.task.pk]),
            self.form_data(next=return_url),
        )

        self.assertRedirects(response, return_url, fetch_redirect_response=False)

    def test_external_return_url_falls_back_to_tasks(self):
        response = self.client.post(
            reverse("task_edit", args=[self.task.pk]),
            self.form_data(next="https://evil.example/steal"),
        )

        self.assertRedirects(response, reverse("tasks"), fetch_redirect_response=False)

    def test_invalid_form_preserves_safe_return_url_for_cancel_and_retry(self):
        return_url = f'{reverse("tasks")}?status=overdue'

        response = self.client.post(
            reverse("task_edit", args=[self.task.pk]),
            self.form_data(title="", next=return_url),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["return_url"], return_url)
        self.assertContains(response, f'name="next" value="{return_url}"')
        self.assertContains(response, f'href="{return_url}"')

    def test_task_history_propagates_validated_return_url_to_edit(self):
        return_url = f'{reverse("tasks")}?status=overdue'

        response = self.client.get(
            reverse("task_history", args=[self.task.pk]),
            {"next": return_url},
        )

        self.assertEqual(response.context["return_url"], return_url)
        self.assertContains(response, "next=/tasks/%3Fstatus%3Doverdue")

    def test_edit_view_history_link_preserves_filtered_return_url(self):
        return_url = f'{reverse("tasks")}?status=overdue&assignee=unassigned'

        response = self.client.get(
            reverse("task_edit", args=[self.task.pk]),
            {"next": return_url},
        )

        history_url = reverse("task_history", args=[self.task.pk])
        self.assertContains(
            response,
            f'href="{history_url}?next=/tasks/%3Fstatus%3Doverdue%26assignee%3Dunassigned"',
        )

    def test_delete_returns_to_filtered_task_referer(self):
        return_url = f'{reverse("tasks")}?status=overdue&page=2'

        response = self.client.post(
            reverse("task_delete", args=[self.task.pk]),
            HTTP_REFERER=f"http://testserver{return_url}",
        )

        self.assertEqual(response.headers["Location"], f"http://testserver{return_url}")
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_archived)


class BoardReturnNavigationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="board-navigation-admin",
            password="test-password",
            display_name="Board Navigation Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member = User.objects.create_user(
            username="board-navigation-member",
            password="test-password",
            display_name="Board Navigation Member",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.board = Board.objects.create(
            name="Navigation Board",
            barcode="NAV-001",
            link_url="https://boards.example.test/navigation",
            created_by=self.admin,
        )
        self.client.force_login(self.admin)

    def test_edit_save_and_cancel_preserve_filtered_board_url(self):
        return_url = f'{reverse("boards")}?assignment=unassigned&page=2'

        get_response = self.client.get(
            reverse("board_edit", args=[self.board.pk]),
            {"next": return_url},
        )
        self.assertEqual(get_response.context["return_url"], return_url)
        escaped_return_url = return_url.replace("&", "&amp;")
        self.assertContains(get_response, f'name="next" value="{escaped_return_url}"')
        self.assertContains(get_response, f'href="{escaped_return_url}"')

        post_response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": "Renamed Navigation Board",
                "barcode": self.board.barcode,
                "link_url": self.board.link_url,
                "notes": "",
                "next": return_url,
            },
        )
        self.assertRedirects(post_response, return_url, fetch_redirect_response=False)

    def test_assignment_and_delete_return_to_filtered_board_referer(self):
        return_url = f'{reverse("boards")}?assignment=unassigned'
        referer = f"http://testserver{return_url}"

        assignment_response = self.client.post(
            reverse("board_user_action", args=[self.board.pk]),
            {"action": "add", "user_id": self.member.pk},
            HTTP_REFERER=referer,
        )
        self.assertEqual(assignment_response.headers["Location"], referer)
        self.assertTrue(BoardAssignment.objects.filter(
            board=self.board,
            user=self.member,
            released_at__isnull=True,
        ).exists())

        delete_response = self.client.post(
            reverse("board_delete", args=[self.board.pk]),
            HTTP_REFERER=referer,
        )
        self.assertEqual(delete_response.headers["Location"], referer)
        self.board.refresh_from_db()
        self.assertTrue(self.board.is_archived)

    def test_external_board_return_url_falls_back_to_board_list(self):
        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": self.board.name,
                "barcode": self.board.barcode,
                "link_url": self.board.link_url,
                "notes": "",
                "next": "https://evil.example/steal",
            },
        )

        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)


class UnifiedFilterUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="filter-ui-admin",
            password="test-password",
            display_name="Filter UI Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_task_filter_button_reflects_server_applied_state(self):
        response = self.client.get(reverse("tasks"))
        self.assertContains(response, ">Apply</button>", html=False)
        self.assertNotContains(response, "Apply Filters")

        response = self.client.get(reverse("tasks"), {"status": Task.Status.TODO})
        self.assertContains(response, "data-filter-active=\"true\"")
        self.assertContains(response, ">Filtered</button>", html=False)

    def test_board_and_timeline_filters_use_the_same_labels(self):
        for url_name in ("boards", "task_timeline"):
            response = self.client.get(reverse(url_name))
            self.assertContains(response, ">Apply</button>", html=False)
            self.assertNotContains(response, "Apply Filters")

    def test_writer_rotation_page_has_no_back_to_meetings_button(self):
        response = self.client.get(reverse("minute_writer_rotations"))

        self.assertNotContains(response, "Back to Meetings")

    def test_task_form_places_start_and_due_dates_next_to_each_other(self):
        fields = list(TaskForm().fields)

        self.assertEqual(
            fields[fields.index("timeline_start_date") + 1],
            "due_date",
        )
