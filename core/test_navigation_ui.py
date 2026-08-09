from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import TaskForm
from core.models import AuditLog, Board, BoardAssignment, BoardComponent, Scope, Task, TaskBoard, User


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
            description="Navigation board description",
            created_by=self.admin,
        )
        self.component = BoardComponent.objects.create(
            board=self.board,
            name="Main",
            link_url="https://boards.example.test/navigation",
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
                "description": self.board.description,
                "revision": self.board.updated_at.isoformat(),
                "components-TOTAL_FORMS": "1",
                "components-INITIAL_FORMS": "1",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-id": str(self.component.pk),
                "components-0-name": self.component.name,
                "components-0-link_url": self.component.link_url,
                "next": return_url,
            },
        )
        self.assertRedirects(post_response, return_url, fetch_redirect_response=False)

    def test_assignment_and_archive_return_to_filtered_board_referer(self):
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

        archive_response = self.client.post(
            reverse("board_archive", args=[self.board.pk]),
            HTTP_REFERER=referer,
        )
        self.assertEqual(archive_response.headers["Location"], referer)
        self.board.refresh_from_db()
        self.assertTrue(self.board.is_archived)

        archived_response = self.client.get(reverse("board_archive_list"))
        self.assertContains(archived_response, self.board.name)
        restore_response = self.client.post(reverse("board_restore", args=[self.board.pk]))
        self.assertRedirects(restore_response, reverse("boards"), fetch_redirect_response=False)
        self.board.refresh_from_db()
        self.assertFalse(self.board.is_archived)
        self.assertFalse(BoardAssignment.objects.filter(
            board=self.board,
            released_at__isnull=True,
        ).exists())

    def test_unused_board_is_deleted_permanently_and_barcode_can_be_reused(self):
        AuditLog.objects.create(
            actor=self.admin,
            entity_type="Board",
            entity_id=self.board.pk,
            action="Board created",
        )

        response = self.client.post(reverse("board_delete", args=[self.board.pk]))

        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)
        self.assertFalse(Board.objects.filter(pk=self.board.pk).exists())
        self.assertFalse(AuditLog.objects.filter(entity_type="Board", entity_id=self.board.pk).exists())

        recreate = self.client.post(
            reverse("board_add"),
            {
                "name": "Recreated Navigation Board",
                "barcode": "NAV-001",
                "description": "Recreated after permanent deletion",
                "components-TOTAL_FORMS": "1",
                "components-INITIAL_FORMS": "0",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-name": "Main",
                "components-0-link_url": "https://boards.example.test/recreated",
            },
        )

        self.assertRedirects(recreate, reverse("boards"), fetch_redirect_response=False)
        self.assertTrue(Board.objects.filter(barcode="NAV-001", is_archived=False).exists())

    def test_used_board_is_deleted_with_links_assignments_components_and_history(self):
        board_pk = self.board.pk
        component_pk = self.component.pk
        task = Task.objects.create(
            title="Board-linked task",
            link_url="https://tasks.example.test/board-linked",
            created_by=self.admin,
        )
        task_link = TaskBoard.objects.create(
            task=task,
            board=self.board,
            added_by=self.admin,
        )
        assignment = BoardAssignment.objects.create(
            board=self.board,
            user=self.member,
            source=BoardAssignment.Source.TASK,
            task=task,
            assigned_by=self.admin,
        )
        AuditLog.objects.create(
            actor=self.admin,
            entity_type="Board",
            entity_id=board_pk,
            action="Board changed",
        )

        response = self.client.post(reverse("board_delete", args=[board_pk]))

        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)
        self.assertFalse(Board.objects.filter(pk=board_pk).exists())
        self.assertFalse(BoardComponent.objects.filter(pk=component_pk).exists())
        self.assertFalse(TaskBoard.objects.filter(pk=task_link.pk).exists())
        self.assertFalse(BoardAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertFalse(AuditLog.objects.filter(entity_type="Board", entity_id=board_pk).exists())
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_archived_unused_board_can_be_deleted_permanently(self):
        self.board.is_archived = True
        self.board.save()

        response = self.client.post(reverse("board_purge", args=[self.board.pk]))

        self.assertRedirects(response, reverse("board_archive_list"), fetch_redirect_response=False)
        self.assertFalse(Board.objects.filter(pk=self.board.pk).exists())

    def test_archived_used_board_can_be_deleted_permanently_with_history(self):
        board_pk = self.board.pk
        assignment = BoardAssignment.objects.create(
            board=self.board,
            user=self.member,
            source=BoardAssignment.Source.MANUAL,
            assigned_by=self.admin,
            released_at=timezone.now(),
        )
        self.board.is_archived = True
        self.board.save()
        AuditLog.objects.create(
            actor=self.admin,
            entity_type="Board",
            entity_id=board_pk,
            action="Board archived",
        )

        response = self.client.post(reverse("board_purge", args=[board_pk]))

        self.assertRedirects(response, reverse("board_archive_list"), fetch_redirect_response=False)
        self.assertFalse(Board.objects.filter(pk=board_pk).exists())
        self.assertFalse(BoardAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertFalse(AuditLog.objects.filter(entity_type="Board", entity_id=board_pk).exists())

    def test_duplicate_barcode_identifies_active_and_archived_board(self):
        payload = {
            "name": "Duplicate",
            "barcode": self.board.barcode,
            "description": "",
            "components-TOTAL_FORMS": "0",
            "components-INITIAL_FORMS": "0",
            "components-MIN_NUM_FORMS": "0",
            "components-MAX_NUM_FORMS": "1000",
        }
        active_response = self.client.post(reverse("board_add"), payload)
        self.assertContains(active_response, "An active Board already uses this barcode.")
        self.assertContains(active_response, "Active Board found")

        self.board.is_archived = True
        self.board.save()
        archived_response = self.client.post(reverse("board_add"), payload)
        self.assertContains(archived_response, "An archived Board already uses this barcode.")
        self.assertContains(archived_response, "Archived Board found")
        self.assertContains(archived_response, reverse("board_archive_list"))

    def test_external_board_return_url_falls_back_to_board_list(self):
        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": self.board.name,
                "barcode": self.board.barcode,
                "description": self.board.description,
                "revision": self.board.updated_at.isoformat(),
                "components-TOTAL_FORMS": "1",
                "components-INITIAL_FORMS": "1",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-id": str(self.component.pk),
                "components-0-name": self.component.name,
                "components-0-link_url": self.component.link_url,
                "next": "https://evil.example/steal",
            },
        )

        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)

    def test_board_supports_multiple_named_components_and_description_history(self):
        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": self.board.name,
                "barcode": self.board.barcode,
                "description": "Board with separate SoM and Carrier references.",
                "revision": self.board.updated_at.isoformat(),
                "components-TOTAL_FORMS": "2",
                "components-INITIAL_FORMS": "1",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-id": str(self.component.pk),
                "components-0-name": "SoM",
                "components-0-link_url": "https://boards.example.test/som",
                "components-1-name": "Carrier",
                "components-1-link_url": "https://boards.example.test/carrier",
            },
        )

        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)
        self.board.refresh_from_db()
        self.assertEqual(
            list(self.board.components.values_list("name", "link_url")),
            [
                ("SoM", "https://boards.example.test/som"),
                ("Carrier", "https://boards.example.test/carrier"),
            ],
        )
        audit = AuditLog.objects.filter(entity_type="Board", entity_id=self.board.pk).first()
        self.assertIn("description", audit.details["changes"])
        self.assertIn("components", audit.details["changes"])

        history = self.client.get(reverse("board_history", args=[self.board.pk]))
        self.assertContains(history, "Board with separate SoM and Carrier references.")
        self.assertContains(history, "SoM")
        self.assertContains(history, "Carrier")
        self.assertContains(history, "https://boards.example.test/som")

    def test_board_rejects_duplicate_component_names_case_insensitively(self):
        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": self.board.name,
                "barcode": self.board.barcode,
                "description": self.board.description,
                "revision": self.board.updated_at.isoformat(),
                "components-TOTAL_FORMS": "2",
                "components-INITIAL_FORMS": "1",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-id": str(self.component.pk),
                "components-0-name": "SoM",
                "components-0-link_url": "https://boards.example.test/som",
                "components-1-name": "som",
                "components-1-link_url": "https://boards.example.test/duplicate",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Component names must be unique within a Board.")
        self.assertEqual(list(self.board.components.values_list("name", flat=True)), ["Main"])

    def test_board_can_rename_component_to_name_released_in_same_save(self):
        carrier = BoardComponent.objects.create(
            board=self.board,
            name="Carrier",
            link_url="https://boards.example.test/carrier",
            position=1,
        )

        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": self.board.name,
                "barcode": self.board.barcode,
                "description": self.board.description,
                "revision": self.board.updated_at.isoformat(),
                "components-TOTAL_FORMS": "2",
                "components-INITIAL_FORMS": "2",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-id": str(self.component.pk),
                "components-0-name": "Carrier",
                "components-0-link_url": self.component.link_url,
                "components-1-id": str(carrier.pk),
                "components-1-name": carrier.name,
                "components-1-link_url": carrier.link_url,
                "components-1-DELETE": "on",
            },
        )

        self.assertRedirects(response, reverse("boards"), fetch_redirect_response=False)
        self.assertEqual(list(self.board.components.values_list("name", flat=True)), ["Carrier"])

    def test_stale_board_edit_is_rejected(self):
        stale_revision = self.board.updated_at.isoformat()
        self.board.description = "Saved by another editor"
        self.board.save()

        response = self.client.post(
            reverse("board_edit", args=[self.board.pk]),
            {
                "name": self.board.name,
                "barcode": self.board.barcode,
                "description": "Stale overwrite",
                "revision": stale_revision,
                "components-TOTAL_FORMS": "1",
                "components-INITIAL_FORMS": "1",
                "components-MIN_NUM_FORMS": "0",
                "components-MAX_NUM_FORMS": "1000",
                "components-0-id": str(self.component.pk),
                "components-0-name": self.component.name,
                "components-0-link_url": self.component.link_url,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This Board changed after you opened it.")
        self.board.refresh_from_db()
        self.assertEqual(self.board.description, "Saved by another editor")

    def test_board_list_searches_component_names_and_urls(self):
        by_name = self.client.get(reverse("boards"), {"q": "Main"})
        by_url = self.client.get(reverse("boards"), {"q": "navigation"})

        self.assertContains(by_name, self.board.name)
        self.assertContains(by_url, self.board.name)


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
