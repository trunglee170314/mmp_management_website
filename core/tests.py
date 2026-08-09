import csv
import os
import tempfile
from threading import Event, Thread
from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from core.models import ActionItem, Board, Meeting, MeetingTask, Scope, Task, TaskBoard, TaskHistory, User
from core.services import meeting_entries_action_groups, task_snapshot


def create_task(*, scopes, **kwargs):
    """Create a task and attach scopes without hiding M2M setup in every test."""
    task = Task.objects.create(**kwargs)
    task.scopes.set(scopes)
    return task


class ImportTasksCsvTests(TestCase):
    fieldnames = [
        "Link", "Status", "Priority", "Subject", "Author", "Assignee",
        "Start date", "Due date", "Complete date",
    ]

    def setUp(self):
        self.active_user = User.objects.create_user(
            username="trungvanle",
            password="not-used-in-this-test",
            display_name="Lê Văn Trung",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )

    def make_csv(self, rows):
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False,
        )
        with handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def test_import_handles_missing_due_date_legacy_users_and_done_metadata(self):
        path = self.make_csv([
            {
                "Link": "https://redmine.example.test/issues/1",
                "Status": "To Do",
                "Priority": "Medium",
                "Subject": "Task without due date",
                "Author": "Trung Van Le",
                "Assignee": "Former Member",
                "Start date": "8/10/2026",
                "Due date": "",
                "Complete date": "",
            },
            {
                "Link": "https://redmine.example.test/issues/2",
                "Status": "Done",
                "Priority": "High",
                "Subject": "Completed task",
                "Author": "Trung Van Le",
                "Assignee": "Trung Van Le",
                "Start date": "1/1/2025",
                "Due date": "1/10/2025",
                "Complete date": "1/8/2025",
            },
        ])

        output = StringIO()
        call_command("import_tasks_csv", path, stdout=output)

        self.assertEqual(Task.objects.count(), 2)
        open_task = Task.objects.get(link_url="https://redmine.example.test/issues/1")
        self.assertIsNone(open_task.due_date)
        self.assertEqual(open_task.created_by, self.active_user)
        self.assertEqual(list(open_task.scopes.values_list("name", flat=True)), ["Uncategorized"])
        self.assertEqual(open_task.assignee.account_status, User.AccountStatus.INACTIVE)
        self.assertFalse(open_task.assignee.has_usable_password())

        done_task = Task.objects.get(link_url="https://redmine.example.test/issues/2")
        self.assertEqual(done_task.assignee, self.active_user)
        self.assertEqual(done_task.completed_by, self.active_user)
        self.assertEqual(done_task.completed_at.date(), date(2025, 1, 8))
        self.assertEqual(User.objects.filter(display_name="Former Member").count(), 1)

        second_output = StringIO()
        call_command("import_tasks_csv", path, stdout=second_output)
        self.assertEqual(Task.objects.count(), 2)
        self.assertIn("2 existing tasks skipped", second_output.getvalue())

    def test_dry_run_rolls_back_tasks_scope_and_legacy_users(self):
        path = self.make_csv([{
            "Link": "https://redmine.example.test/issues/3",
            "Status": "In Progress",
            "Priority": "Low",
            "Subject": "Dry run task",
            "Author": "Former Author",
            "Assignee": "Former Assignee",
            "Start date": "2026-08-10",
            "Due date": "",
            "Complete date": "",
        }])

        output = StringIO()
        call_command(
            "import_tasks_csv", path, scope="Dry Run Scope", dry_run=True, stdout=output,
        )

        self.assertFalse(Task.objects.exists())
        self.assertFalse(Scope.objects.filter(name="Dry Run Scope").exists())
        self.assertFalse(User.objects.filter(username__startswith="legacy-").exists())
        self.assertIn("DRY RUN (rolled back)", output.getvalue())


class MeetingContributionTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username="host",
            password="test-password",
            display_name="Meeting Host",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.contributor = User.objects.create_user(
            username="contributor",
            password="test-password",
            display_name="Contributor",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Core", color="#16835E")
        self.task = create_task(
            title="Parent task",
            scopes=[self.scope],
            assignee=self.contributor,
            link_url="https://tasks.example.test/1",
            created_by=self.host,
            due_date=date(2026, 8, 31),
        )
        self.meeting = Meeting.objects.create(
            title="Weekly Meeting",
            meeting_date=date(2026, 8, 18),
            host=self.host,
            minute_taker=self.host,
            created_by=self.host,
        )
        self.entry = MeetingTask.objects.create(meeting=self.meeting, task=self.task, position=1)
        self.client.force_login(self.contributor)

    def test_any_active_user_can_write_to_draft_meeting(self):
        progress_response = self.client.post(
            reverse("meeting_task_progress_save", args=[self.meeting.pk, self.entry.pk]),
            {"weekly_progress": "Updated by another member"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(progress_response.status_code, 200)

        action_response = self.client.post(
            reverse("meeting_action_add", args=[self.meeting.pk, self.entry.pk]),
            {"new_action_item": "Follow up", "action_assignee": self.contributor.pk},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(action_response.status_code, 200)
        item = ActionItem.objects.get(content="Follow up")
        self.assertIsNone(item.published_at)
        self.assertFalse(TaskHistory.objects.filter(task=self.task, new_value="Follow up").exists())

        task_response = self.client.get(reverse("task_history", args=[self.task.pk]))
        actions_response = self.client.get(f'{reverse("tasks")}?tab=actions')
        self.assertNotContains(task_response, "Follow up")
        self.assertNotContains(actions_response, "Follow up")

    def test_finalized_meeting_is_read_only_for_contributors(self):
        self.meeting.status = Meeting.Status.FINALIZED
        self.meeting.save(update_fields=["status"])
        response = self.client.post(
            reverse("meeting_task_progress_save", args=[self.meeting.pk, self.entry.pk]),
            {"weekly_progress": "Must not save"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_draft_action_item_is_deleted_with_meeting(self):
        item = ActionItem.objects.create(
            task=self.task,
            meeting=self.meeting,
            source_meeting_title=self.meeting.title,
            source_meeting_date=self.meeting.meeting_date,
            content="Persistent Action Item",
            assignee=self.contributor,
            created_by=self.host,
        )
        self.host.role = User.Role.ADMIN
        self.host.save(update_fields=["role"])
        self.client.force_login(self.host)

        self.client.post(reverse("meeting_delete", args=[self.meeting.pk]))

        self.assertFalse(ActionItem.objects.filter(pk=item.pk).exists())

    def test_finalize_publishes_action_once_and_finalized_delete_preserves_it(self):
        item = ActionItem.objects.create(
            task=self.task,
            meeting=self.meeting,
            source_meeting_title=self.meeting.title,
            source_meeting_date=self.meeting.meeting_date,
            content="Publish after review",
            assignee=self.contributor,
            due_date=date(2026, 8, 25),
            created_by=self.contributor,
        )
        self.entry.review_state = MeetingTask.ReviewState.REVIEWED
        self.entry.save(update_fields=["review_state"])
        self.client.force_login(self.host)

        response = self.client.post(reverse("meeting_finalize", args=[self.meeting.pk]))

        self.assertRedirects(response, reverse("meeting_detail", args=[self.meeting.pk]))
        item.refresh_from_db()
        self.assertIsNotNone(item.published_at)
        history = TaskHistory.objects.filter(
            task=self.task,
            event="Meeting Minute Action Item added",
            new_value="Publish after review",
        )
        self.assertEqual(history.count(), 1)
        self.assertIn("Weekly Meeting", history.get().note)

        task_response = self.client.get(reverse("task_history", args=[self.task.pk]))
        self.assertContains(task_response, "Publish after review")

        self.host.role = User.Role.ADMIN
        self.host.save(update_fields=["role"])
        self.client.post(reverse("meeting_delete", args=[self.meeting.pk]))
        item.refresh_from_db()
        self.assertIsNone(item.meeting_id)
        self.assertEqual(item.source_meeting_title, "Weekly Meeting")
        self.assertEqual(item.source_meeting_date, date(2026, 8, 18))
        self.assertEqual(history.count(), 1)

    def test_reopen_only_publishes_new_draft_actions(self):
        first = ActionItem.objects.create(
            task=self.task,
            meeting=self.meeting,
            content="Already published",
            assignee=self.contributor,
            created_by=self.host,
            published_at=timezone.now(),
        )
        self.meeting.status = Meeting.Status.FINALIZED
        self.meeting.finalized_at = timezone.now()
        self.meeting.save(update_fields=["status", "finalized_at"])
        self.host.role = User.Role.ADMIN
        self.host.save(update_fields=["role"])
        self.client.force_login(self.host)
        self.client.post(reverse("meeting_reopen", args=[self.meeting.pk]))
        second = ActionItem.objects.create(
            task=self.task,
            meeting=self.meeting,
            content="New after reopen",
            assignee=self.contributor,
            created_by=self.host,
        )
        self.entry.review_state = MeetingTask.ReviewState.REVIEWED
        self.entry.save(update_fields=["review_state"])

        self.client.post(reverse("meeting_finalize", args=[self.meeting.pk]))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.published_at)
        self.assertIsNotNone(second.published_at)
        self.assertFalse(TaskHistory.objects.filter(task=self.task, new_value="Already published").exists())
        self.assertEqual(TaskHistory.objects.filter(task=self.task, new_value="New after reopen").count(), 1)

    def test_completed_and_reopened_action_events_are_visible_in_task_history(self):
        item = ActionItem.objects.create(
            task=self.task,
            content="Verify history visibility",
            assignee=self.contributor,
            created_by=self.host,
            published_at=timezone.now(),
        )

        self.client.post(reverse("action_item_toggle", args=[item.pk]))
        completed_response = self.client.get(reverse("task_history", args=[self.task.pk]))
        self.assertContains(completed_response, "Action item completed")
        self.assertContains(completed_response, "Contributor")

        self.client.post(reverse("action_item_toggle", args=[item.pk]))
        reopened_response = self.client.get(reverse("task_history", args=[self.task.pk]))
        self.assertContains(reopened_response, "Action item reopened")

    def test_task_snapshot_contains_due_date_generic_link_and_board_barcode(self):
        board = Board.objects.create(
            name="RZ/G2N",
            barcode="XYZ-001",
            link_url="https://boards.example.test/xyz-001",
            created_by=self.host,
        )
        TaskBoard.objects.create(task=self.task, board=board, added_by=self.host)
        snapshot = task_snapshot(self.task)
        self.assertEqual(snapshot["link_url"], "https://tasks.example.test/1")
        self.assertEqual(snapshot["due_date_label"], "31 Aug 2026")
        self.assertEqual(snapshot["boards"], ["RZ/G2N (XYZ-001)"])

    def test_meeting_detail_exposes_contributor_controls_and_task_due_date(self):
        response = self.client.get(reverse("meeting_detail", args=[self.meeting.pk]))
        self.assertContains(response, 'name="weekly_progress"')
        self.assertContains(response, "data-action-item-form")
        self.assertContains(response, "31 Aug 2026")
        self.assertNotContains(response, "review-choice-buttons")

        self.client.force_login(self.host)
        host_response = self.client.get(reverse("meeting_detail", args=[self.meeting.pk]))
        self.assertContains(host_response, "review-choice-buttons")
        self.assertContains(host_response, 'name="review_state" value="reviewed"')
        self.assertNotContains(host_response, '<select name="review_state"')

    def test_meeting_list_is_sorted_by_meeting_date(self):
        older = Meeting.objects.create(
            title="Older", meeting_date=date(2026, 8, 17), host=self.host,
            minute_taker=self.host, created_by=self.host,
        )
        newer = Meeting.objects.create(
            title="Newer", meeting_date=date(2026, 8, 19), host=self.host,
            minute_taker=self.host, created_by=self.host,
        )
        response = self.client.get(reverse("meetings"))
        self.assertEqual(
            [meeting.pk for meeting in response.context["meetings"]],
            [newer.pk, self.meeting.pk, older.pk],
        )


class TaskRelationshipTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="relationship-user",
            password="test-password",
            display_name="Relationship User",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Graphics", color="#38B66A")
        self.client.force_login(self.user)

    def task_data(self, task, **overrides):
        data = {
            "title": task.title,
            "description": task.description,
            "scopes": [self.scope.pk],
            "parent_task": task.parent_task_id or "",
            "assignee": "",
            "status": Task.Status.TODO,
            "priority": Task.Priority.MEDIUM,
            "timeline_start_date": "",
            "due_date": "",
            "link_url": task.link_url,
            "status_note": "",
        }
        data.update(overrides)
        return data

    def test_task_supports_parent_and_subtasks(self):
        parent = create_task(title="Parent", scopes=[self.scope], link_url="https://tasks.example.test/parent")
        child = create_task(
            title="Child",
            scopes=[self.scope],
            parent_task=parent,
            link_url="https://tasks.example.test/child",
        )
        self.assertEqual(child.parent_task, parent)
        self.assertEqual(list(parent.subtasks.all()), [child])

    def test_related_tasks_are_explicit_and_symmetric(self):
        first = create_task(title="First", scopes=[self.scope], link_url="https://tasks.example.test/first")
        second = create_task(title="Second", scopes=[self.scope], link_url="https://tasks.example.test/second")

        response = self.client.post(
            reverse("task_edit", args=[first.pk]),
            self.task_data(first, related_tasks=[second.pk]),
        )

        self.assertRedirects(response, reverse("tasks"))
        self.assertEqual(list(first.related_tasks.all()), [second])
        self.assertEqual(list(second.related_tasks.all()), [first])
        self.assertTrue(TaskHistory.objects.filter(task=first, event="Related Tasks added", new_value="Second").exists())
        self.assertTrue(TaskHistory.objects.filter(task=second, event="Related Task added", new_value="First").exists())

    def test_many_long_related_task_titles_are_recorded_without_truncation(self):
        task = create_task(title="Primary", scopes=[self.scope], link_url="https://tasks.example.test/primary")
        related = [
            create_task(
                title=f"Related task {index} " + ("long title " * 12),
                scopes=[self.scope],
                link_url=f"https://tasks.example.test/related-{index}",
            )
            for index in range(4)
        ]

        response = self.client.post(
            reverse("task_edit", args=[task.pk]),
            self.task_data(task, related_tasks=[item.pk for item in related]),
        )

        self.assertRedirects(response, reverse("tasks"))
        history = TaskHistory.objects.get(task=task, event="Related Tasks added")
        self.assertGreater(len(history.new_value), 300)

    def test_parent_add_change_and_remove_are_all_recorded(self):
        first_parent = create_task(title="First Parent", scopes=[self.scope], link_url="https://tasks.example.test/parent-1")
        second_parent = create_task(title="Second Parent", scopes=[self.scope], link_url="https://tasks.example.test/parent-2")
        child = create_task(title="Child", scopes=[self.scope], link_url="https://tasks.example.test/child")

        self.client.post(
            reverse("task_edit", args=[child.pk]),
            self.task_data(child, parent_task=first_parent.pk),
        )
        child.refresh_from_db()
        self.client.post(
            reverse("task_edit", args=[child.pk]),
            self.task_data(child, parent_task=second_parent.pk),
        )
        child.refresh_from_db()
        self.client.post(
            reverse("task_edit", args=[child.pk]),
            self.task_data(child, parent_task=""),
        )

        events = list(TaskHistory.objects.filter(task=child).values_list("event", flat=True))
        self.assertIn("Parent Task added", events)
        self.assertIn("Parent Task changed", events)
        self.assertIn("Parent Task removed", events)

    def test_task_form_contains_searchable_parent_and_related_pickers(self):
        task = create_task(title="Task", scopes=[self.scope], link_url="https://tasks.example.test/task")
        create_task(title="Other Task", scopes=[self.scope], link_url="https://tasks.example.test/other")
        response = self.client.get(reverse("task_edit", args=[task.pk]))
        self.assertContains(response, "data-single-task-picker")
        self.assertContains(response, "Search tasks by any words", count=2)
        self.assertContains(response, "Select related tasks")


class UserStatusActionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="status-admin",
            password="test-password",
            display_name="Status Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member = User.objects.create_user(
            username="inactive-member",
            password="test-password",
            display_name="Inactive Member",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.INACTIVE,
        )
        self.client.force_login(self.admin)

    def test_activate_and_deactivate_can_update_without_page_reload(self):
        url = reverse("user_action", args=[self.member.pk])
        activate = self.client.post(
            url,
            {"action": "activate"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(activate.status_code, 200)
        self.assertEqual(activate.json()["status"], User.AccountStatus.ACTIVE)
        self.assertEqual(activate.json()["next_action"], "deactivate")

        deactivate = self.client.post(
            url,
            {"action": "deactivate", "transfer_to": self.admin.pk},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(deactivate.status_code, 200)
        self.assertEqual(deactivate.json()["status"], User.AccountStatus.INACTIVE)
        self.assertEqual(deactivate.json()["next_action"], "activate")


class MeetingOrderTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username="order-host",
            password="test-password",
            display_name="Order Host",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        first_scope = Scope.objects.create(name="Alpha", position=1)
        second_scope = Scope.objects.create(name="Zulu", position=2)
        first_task = create_task(
            title="Alpha task", scopes=[first_scope], link_url="https://tasks.example.test/alpha",
        )
        second_task = create_task(
            title="Zulu task", scopes=[second_scope], link_url="https://tasks.example.test/zulu",
        )
        self.meeting = Meeting.objects.create(
            title="Ordering", meeting_date=date(2026, 8, 18), host=self.host,
            minute_taker=self.host, created_by=self.host,
        )
        self.second_entry = MeetingTask.objects.create(
            meeting=self.meeting, task=second_task, position=1,
        )
        self.first_entry = MeetingTask.objects.create(
            meeting=self.meeting, task=first_task, position=2,
        )
        self.client.force_login(self.host)

    def test_scope_order_returns_and_persists_sorted_entries(self):
        response = self.client.post(
            reverse("meeting_order", args=[self.meeting.pk]),
            {"order_by": "scope"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "scope")
        self.assertEqual(
            [item["id"] for item in response.json()["entries"]],
            [self.first_entry.pk, self.second_entry.pk],
        )
        self.first_entry.refresh_from_db()
        self.second_entry.refresh_from_db()
        self.assertEqual((self.first_entry.position, self.second_entry.position), (1, 2))
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.task_order, Meeting.TaskOrder.SCOPE)

    def test_manual_move_persists_manual_order_mode(self):
        response = self.client.post(
            reverse("meeting_task_move", args=[self.meeting.pk, self.second_entry.pk]),
            {"direction": "down"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["moved"])
        self.assertEqual(response.json()["mode"], Meeting.TaskOrder.MANUAL)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.task_order, Meeting.TaskOrder.MANUAL)

    def test_invalid_order_mode_is_rejected_without_changing_positions(self):
        response = self.client.post(
            reverse("meeting_order", args=[self.meeting.pk]),
            {"order_by": "unknown"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            list(self.meeting.task_entries.values_list("position", flat=True)),
            [1, 2],
        )

    def test_detail_uses_persisted_order_mode_and_has_apply_button(self):
        self.meeting.task_order = Meeting.TaskOrder.MANUAL
        self.meeting.save(update_fields=["task_order"])

        response = self.client.get(reverse("meeting_detail", args=[self.meeting.pk]))

        self.assertContains(response, 'value="manual" selected')
        self.assertContains(response, '>Apply</button>')

    def test_live_updates_include_order_mode(self):
        self.meeting.task_order = Meeting.TaskOrder.ASSIGNEE
        self.meeting.save(update_fields=["task_order"])

        response = self.client.get(
            reverse("meeting_live_updates", args=[self.meeting.pk]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["order_mode"], Meeting.TaskOrder.ASSIGNEE)


class ServerPaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pagination-user",
            password="test-password",
            display_name="Pagination User",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Pagination", color="#16835E")
        self.client.force_login(self.user)

    def test_task_list_is_paginated_at_fifty_rows(self):
        for index in range(55):
            create_task(
                title=f"Paginated task {index:02d}",
                scopes=[self.scope],
                link_url=f"https://tasks.example.test/page-{index}",
                created_by=self.user,
            )

        first = self.client.get(reverse("tasks"))
        second = self.client.get(reverse("tasks"), {"page": 2})

        self.assertEqual(len(first.context["tasks"]), 50)
        self.assertEqual(len(second.context["tasks"]), 5)
        self.assertEqual(second.context["page_obj"].paginator.count, 55)

    def test_meeting_list_is_paginated_at_thirty_rows(self):
        Meeting.objects.bulk_create([
            Meeting(
                title=f"Meeting {index:02d}",
                meeting_date=date(2026, 8, 18),
                host=self.user,
                minute_taker=self.user,
                created_by=self.user,
            )
            for index in range(35)
        ])

        first = self.client.get(reverse("meetings"))
        second = self.client.get(reverse("meetings"), {"page": 2})

        self.assertEqual(len(first.context["meetings"]), 30)
        self.assertEqual(len(second.context["meetings"]), 5)
        self.assertEqual(second.context["page_obj"].paginator.count, 35)


class MeetingQueryBatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="query-user",
            password="test-password",
            display_name="Query User",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Query Scope", color="#16835E")
        self.meeting = Meeting.objects.create(
            title="Query Meeting",
            meeting_date=date(2026, 8, 18),
            host=self.user,
            minute_taker=self.user,
            created_by=self.user,
        )
        self.entries = []
        for index in range(8):
            task = create_task(
                title=f"Query task {index}",
                scopes=[self.scope],
                link_url=f"https://tasks.example.test/query-{index}",
                created_by=self.user,
            )
            entry = MeetingTask.objects.create(
                meeting=self.meeting,
                task=task,
                position=index + 1,
            )
            self.entries.append(entry)
            ActionItem.objects.create(
                task=task,
                meeting=self.meeting,
                content=f"New action {index}",
                created_by=self.user,
            )
            previous = ActionItem.objects.create(
                task=task,
                content=f"Previous action {index}",
                created_by=self.user,
                published_at=timezone.now(),
            )
            ActionItem.objects.filter(pk=previous.pk).update(
                created_at=self.meeting.created_at - timedelta(days=1),
            )

    def test_action_groups_use_constant_query_count(self):
        with self.assertNumQueries(2):
            grouped = meeting_entries_action_groups(
                self.meeting,
                self.entries,
                completed_after=self.meeting.created_at,
            )

        self.assertEqual(len(grouped), 8)
        self.assertTrue(all(len(groups[0]) == 1 for groups in grouped.values()))
        self.assertTrue(all(len(groups[2]) == 1 for groups in grouped.values()))


class MeetingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @skipUnlessDBFeature("has_select_for_update")
    def test_progress_waiting_on_finalize_cannot_write_after_commit(self):
        host = User.objects.create_user(
            username="concurrency-host",
            password="test-password",
            display_name="Concurrency Host",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        scope = Scope.objects.create(name="Concurrency", color="#16835E")
        task = create_task(
            title="Concurrent task",
            scopes=[scope],
            link_url="https://tasks.example.test/concurrent",
            created_by=host,
        )
        meeting = Meeting.objects.create(
            title="Concurrent Meeting",
            meeting_date=date(2026, 8, 18),
            host=host,
            minute_taker=host,
            created_by=host,
        )
        entry = MeetingTask.objects.create(meeting=meeting, task=task, position=1)
        client = Client()
        client.force_login(host)
        started = Event()
        lock_attempted = Event()
        result = {}

        def save_progress():
            close_old_connections()
            started.set()

            def capture_meeting_lock(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper() and "core_meeting" in sql.lower():
                    lock_attempted.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(capture_meeting_lock):
                    result["response"] = client.post(
                        reverse("meeting_task_progress_save", args=[meeting.pk, entry.pk]),
                        {"weekly_progress": "Must not be written"},
                        HTTP_ACCEPT="application/json",
                    )
            finally:
                connection.close()

        with transaction.atomic():
            locked = Meeting.objects.select_for_update().get(pk=meeting.pk)
            worker = Thread(target=save_progress)
            worker.start()
            self.assertTrue(started.wait(timeout=5))
            self.assertTrue(lock_attempted.wait(timeout=5))
            locked.status = Meeting.Status.FINALIZED
            locked.finalized_at = timezone.now()
            locked.save(update_fields=["status", "finalized_at", "updated_at"])

        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["response"].status_code, 403)
        entry.refresh_from_db()
        self.assertEqual(entry.weekly_progress, "")
