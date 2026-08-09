"""PostgreSQL regressions for Timeline edits that overlap other workflows."""

import threading
import time
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import timeline_interactions, views
from .models import Meeting, MeetingTask, Scope, SystemSetting, Task, TimelineGroup, User
from .services import deactivate_user_and_transfer


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class TimelineConcurrencyTests(TransactionTestCase):
    def setUp(self):
        SystemSetting.load()
        self.admin = User.objects.create_user(
            username="timeline-lock-admin", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member = User.objects.create_user(
            username="timeline-lock-member", account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Concurrency scope")
        self.group = TimelineGroup.objects.create(name="Original group")
        self.destination = TimelineGroup.objects.create(name="Destination group")
        self.task = Task.objects.create(
            title="Concurrent timeline task", assignee=self.member,
            timeline_group=self.group, link_url="https://example.test/concurrency",
            created_by=self.admin,
        )
        self.task.scopes.add(self.scope)
        self.meeting = Meeting.objects.create(
            title="Concurrent meeting", meeting_date=timezone.localdate(),
            host=self.admin, minute_taker=self.admin, created_by=self.admin,
        )
        # Authenticate before racing the requests so last_login writes are not
        # mistaken for the operational locks that these regressions exercise.
        self.editor = Client()
        self.editor.force_login(self.admin)
        self.manager = Client()
        self.manager.force_login(self.admin)

    def task_payload(self, **changes):
        payload = {
            "title": self.task.title, "description": "",
            "scopes": [self.scope.pk], "assignee": self.member.pk,
            "status": Task.Status.TODO, "priority": Task.Priority.MEDIUM,
            "timeline_group": self.group.pk, "link_url": self.task.link_url,
            "revision": self.task.updated_at.isoformat(), "modal": "1",
        }
        payload.update(changes)
        return payload

    def run_concurrently(self, **operations):
        results, errors = {}, {}

        def execute(name, operation):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '6s'")
                results[name] = operation()
            except Exception as error:
                errors[name] = repr(error)
            finally:
                connection.close()

        workers = [
            threading.Thread(target=execute, args=(name, operation), daemon=True)
            for name, operation in operations.items()
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        self.assertFalse(any(worker.is_alive() for worker in workers), "Concurrent edit did not finish")
        self.assertEqual(errors, {}, errors)
        return results

    def test_status_edit_and_user_deactivation_use_meeting_then_task_lock_order(self):
        reached_sync, transfer_started = threading.Event(), threading.Event()
        original_sync = views.sync_task_to_draft_meetings

        def delayed_sync(*args, **kwargs):
            reached_sync.set()
            if not transfer_started.wait(timeout=5):
                raise TimeoutError("The responsibility transfer did not start")
            # The transfer has time to reach its Meeting lock. The form already
            # owns that Meeting before owning the Task, so it can still commit.
            time.sleep(0.25)
            return original_sync(*args, **kwargs)

        def edit_task():
            return self.editor.post(
                reverse("task_edit", args=[self.task.pk]),
                self.task_payload(status=Task.Status.IN_PROGRESS),
            ).status_code

        def transfer_user():
            if not reached_sync.wait(timeout=5):
                raise TimeoutError("The Task edit did not reach meeting synchronization")
            transfer_started.set()
            deactivate_user_and_transfer(
                User.objects.get(pk=self.member.pk), User.objects.get(pk=self.admin.pk),
                User.objects.get(pk=self.admin.pk),
            )
            return "committed"

        with patch("core.views.sync_task_to_draft_meetings", delayed_sync):
            results = self.run_concurrently(edit=edit_task, transfer=transfer_user)

        self.assertEqual(results, {"edit": 200, "transfer": "committed"})
        self.task.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)
        self.assertEqual(self.task.assignee_id, self.admin.pk)
        self.assertEqual(self.member.account_status, User.AccountStatus.INACTIVE)
        self.assertTrue(MeetingTask.objects.filter(meeting=self.meeting, task=self.task).exists())

    def test_group_delete_rechecks_membership_after_waiting_for_concurrent_task_move(self):
        task_saved, group_locked = threading.Event(), threading.Event()
        original_log = views.log_task_changes
        original_revision_error = timeline_interactions.revision_error

        def delayed_history(*args, **kwargs):
            # Task.save() has changed A to C but the form transaction still owns
            # the Task row. Another transaction continues to observe group A.
            task_saved.set()
            if not group_locked.wait(timeout=5):
                raise TimeoutError("The Group deletion did not acquire its Group lock")
            # Let deletion reach the membership query while this row remains
            # uncommitted. An unlocked ID snapshot would incorrectly capture A.
            time.sleep(0.25)
            return original_log(*args, **kwargs)

        def observe_group_lock(request, instance, *args, **kwargs):
            if isinstance(instance, TimelineGroup) and instance.pk == self.group.pk:
                group_locked.set()
            return original_revision_error(request, instance, *args, **kwargs)

        def move_task():
            return self.editor.post(
                reverse("task_edit", args=[self.task.pk]),
                self.task_payload(timeline_group=self.destination.pk),
            ).status_code

        def delete_group():
            if not task_saved.wait(timeout=5):
                raise TimeoutError("The Task move did not save before deleting the Group")
            return self.manager.post(
                reverse("timeline_group_delete", args=[self.group.pk]),
                {"revision": self.group.updated_at.isoformat()},
                HTTP_ACCEPT="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            ).status_code

        with patch("core.views.log_task_changes", delayed_history), patch(
            "core.timeline_interactions.revision_error", observe_group_lock,
        ):
            results = self.run_concurrently(edit=move_task, delete=delete_group)

        self.assertEqual(results, {"edit": 200, "delete": 200})
        self.task.refresh_from_db()
        self.assertEqual(self.task.timeline_group_id, self.destination.pk)
        self.assertFalse(TimelineGroup.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(self.task.history.filter(event="Timeline Group removed").exists())

    def test_concurrent_destination_group_delete_returns_conflict_not_server_error(self):
        task_saved, destination_deleted = threading.Event(), threading.Event()
        original_log = views.log_task_changes

        def wait_for_destination_delete(*args, **kwargs):
            task_saved.set()
            if not destination_deleted.wait(timeout=5):
                raise TimeoutError("The destination Group deletion did not finish")
            return original_log(*args, **kwargs)

        def move_task():
            return self.editor.post(
                reverse("task_edit", args=[self.task.pk]),
                self.task_payload(timeline_group=self.destination.pk),
            ).status_code

        def delete_destination():
            if not task_saved.wait(timeout=5):
                raise TimeoutError("The Task move did not reach its deferred FK check")
            try:
                return self.manager.post(
                    reverse("timeline_group_delete", args=[self.destination.pk]),
                    {"revision": self.destination.updated_at.isoformat()},
                    HTTP_ACCEPT="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                ).status_code
            finally:
                destination_deleted.set()

        with patch("core.views.log_task_changes", wait_for_destination_delete):
            results = self.run_concurrently(edit=move_task, delete=delete_destination)

        self.assertEqual(results, {"edit": 409, "delete": 200})
        self.task.refresh_from_db()
        self.assertEqual(self.task.timeline_group_id, self.group.pk)
        self.assertFalse(TimelineGroup.objects.filter(pk=self.destination.pk).exists())
        self.assertFalse(self.task.history.filter(event="Timeline Group changed").exists())
