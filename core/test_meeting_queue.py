from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import ActionItem, Meeting, MeetingTask, Scope, Task, User
from .services import initialize_meeting_tasks


class MeetingReviewQueueTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username="queue-host",
            password="test-password",
            display_name="Queue Host",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.assignee = User.objects.create_user(
            username="queue-assignee",
            password="test-password",
            display_name="Queue Assignee",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.meeting = Meeting.objects.create(
            title="Queue Test",
            meeting_date=timezone.localdate(),
            host=self.host,
            minute_taker=self.host,
            created_by=self.host,
        )
        self.client.force_login(self.host)

    def create_task(self, title, *, status, assignee=None):
        return Task.objects.create(
            title=title,
            status=status,
            assignee=assignee,
            link_url=f"https://example.com/{title.lower().replace(' ', '-')}",
            created_by=self.host,
        )

    def test_todo_without_open_action_is_included_as_skipped(self):
        unassigned_todo = self.create_task("Unassigned todo", status=Task.Status.TODO)
        assigned_todo = self.create_task(
            "Assigned todo", status=Task.Status.TODO, assignee=self.assignee,
        )
        todo_with_open_action = self.create_task(
            "Todo with open action", status=Task.Status.TODO, assignee=self.assignee,
        )
        todo_with_completed_action = self.create_task(
            "Todo with completed action", status=Task.Status.TODO, assignee=self.assignee,
        )
        ActionItem.objects.create(
            task=todo_with_open_action,
            content="Still open",
            created_by=self.host,
        )
        ActionItem.objects.create(
            task=todo_with_completed_action,
            content="Already complete",
            is_completed=True,
            completed_by=self.host,
            completed_at=timezone.now(),
            created_by=self.host,
        )
        unassigned_in_progress = self.create_task(
            "Unassigned in progress", status=Task.Status.IN_PROGRESS,
        )
        paused = self.create_task("Paused", status=Task.Status.PAUSED)
        self.create_task("Completed", status=Task.Status.DONE, assignee=self.assignee)
        cancelled = self.create_task("Cancelled", status=Task.Status.CANCELLED)

        initialize_meeting_tasks(self.meeting)

        entries = {
            entry.task_id: entry.review_state
            for entry in self.meeting.task_entries.all()
        }
        self.assertEqual(entries, {
            unassigned_todo.pk: MeetingTask.ReviewState.SKIPPED,
            assigned_todo.pk: MeetingTask.ReviewState.SKIPPED,
            todo_with_open_action.pk: MeetingTask.ReviewState.PENDING,
            todo_with_completed_action.pk: MeetingTask.ReviewState.SKIPPED,
            unassigned_in_progress.pk: MeetingTask.ReviewState.PENDING,
            paused.pk: MeetingTask.ReviewState.PENDING,
        })
        self.assertNotIn(cancelled.pk, entries)

    def test_scope_order_places_scoped_tasks_before_uncategorized_tasks(self):
        scope = Scope.objects.create(name="Alpha Scope", position=1)
        uncategorized = self.create_task(
            "Uncategorized", status=Task.Status.IN_PROGRESS, assignee=self.assignee,
        )
        scoped = self.create_task(
            "Scoped", status=Task.Status.IN_PROGRESS, assignee=self.assignee,
        )
        scoped.scopes.add(scope)

        initialize_meeting_tasks(self.meeting)

        self.assertEqual(
            list(self.meeting.task_entries.values_list("task_id", flat=True)),
            [scoped.pk, uncategorized.pk],
        )

    def test_review_auto_advance_skips_entries_already_marked_skipped(self):
        first_task = self.create_task(
            "First", status=Task.Status.IN_PROGRESS, assignee=self.assignee,
        )
        skipped_task = self.create_task("Skipped", status=Task.Status.TODO)
        next_task = self.create_task(
            "Next", status=Task.Status.IN_PROGRESS, assignee=self.assignee,
        )
        first = MeetingTask.objects.create(
            meeting=self.meeting, task=first_task, position=1,
        )
        MeetingTask.objects.create(
            meeting=self.meeting, task=skipped_task, position=2,
            review_state=MeetingTask.ReviewState.SKIPPED,
        )
        expected_next = MeetingTask.objects.create(
            meeting=self.meeting, task=next_task, position=3,
        )

        response = self.client.post(
            reverse("meeting_task_review", args=[self.meeting.pk, first.pk]),
            {"review_state": MeetingTask.ReviewState.NO_UPDATE},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next_entry_id"], expected_next.pk)

    def test_initialization_query_count_is_constant_as_task_count_grows(self):
        self.create_task("First", status=Task.Status.TODO, assignee=self.assignee)
        with CaptureQueriesContext(connection) as small_queries:
            initialize_meeting_tasks(self.meeting)

        larger_meeting = Meeting.objects.create(
            title="Larger queue",
            meeting_date=timezone.localdate(),
            host=self.host,
            minute_taker=self.host,
            created_by=self.host,
        )
        for number in range(20):
            task = self.create_task(
                f"Extra {number}", status=Task.Status.TODO, assignee=self.assignee,
            )
            ActionItem.objects.create(task=task, content=f"Open {number}", created_by=self.host)
        with CaptureQueriesContext(connection) as larger_queries:
            initialize_meeting_tasks(larger_meeting)

        self.assertEqual(len(larger_queries), len(small_queries))
