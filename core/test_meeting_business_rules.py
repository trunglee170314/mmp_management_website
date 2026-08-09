from datetime import date
from threading import Barrier, Event, Thread

from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .admin import AdminActionItemForm, AdminTaskForm
from .models import (
    ActionItem,
    Meeting,
    MeetingTask,
    MinuteWriterRotation,
    MinuteWriterRotationMember,
    Scope,
    Task,
    User,
)
from .services import deactivate_user_and_transfer


class TaskActionItemInvariantTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="rules-admin",
            password="test-password",
            display_name="Rules Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member = User.objects.create_user(
            username="rules-member",
            password="test-password",
            display_name="Rules Member",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope = Scope.objects.create(name="Rules")
        self.client.force_login(self.admin)

    def create_task(self, *, status=Task.Status.IN_PROGRESS):
        task = Task.objects.create(
            title="Invariant task",
            status=status,
            assignee=self.member,
            priority=Task.Priority.MEDIUM,
            link_url="https://example.com/invariant",
            created_by=self.admin,
        )
        task.scopes.add(self.scope)
        return task

    def task_payload(self, task, status):
        return {
            "title": task.title,
            "description": task.description,
            "scopes": [self.scope.pk],
            "assignee": self.member.pk,
            "status": status,
            "priority": task.priority,
            "link_url": task.link_url,
            "status_note": "Required status note",
        }

    def test_open_published_or_draft_action_blocks_transition_to_done(self):
        for published_at in (None, timezone.now()):
            with self.subTest(published=published_at is not None):
                task = self.create_task()
                ActionItem.objects.create(
                    task=task,
                    content="Open item",
                    created_by=self.admin,
                    published_at=published_at,
                )

                response = self.client.post(
                    reverse("task_edit", args=[task.pk]),
                    self.task_payload(task, Task.Status.DONE),
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Complete 1 open Action Item(s)")
                task.refresh_from_db()
                self.assertEqual(task.status, Task.Status.IN_PROGRESS)

    def test_task_can_be_done_after_all_actions_are_completed(self):
        task = self.create_task()
        ActionItem.objects.create(
            task=task,
            content="Completed item",
            is_completed=True,
            completed_by=self.member,
            completed_at=timezone.now(),
            created_by=self.admin,
            published_at=timezone.now(),
        )

        response = self.client.post(
            reverse("task_edit", args=[task.pk]),
            self.task_payload(task, Task.Status.DONE),
        )

        self.assertRedirects(response, reverse("tasks"))
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.DONE)

    def test_cancelled_task_is_not_blocked_by_open_actions(self):
        task = self.create_task()
        ActionItem.objects.create(task=task, content="Open item", created_by=self.admin)

        response = self.client.post(
            reverse("task_edit", args=[task.pk]),
            self.task_payload(task, Task.Status.CANCELLED),
        )

        self.assertRedirects(response, reverse("tasks"))
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)

    def test_cannot_add_open_action_to_done_task(self):
        task = self.create_task(status=Task.Status.DONE)
        meeting = Meeting.objects.create(
            title="Done task meeting",
            meeting_date=date(2026, 8, 25),
            host=self.admin,
            minute_taker=self.admin,
            created_by=self.admin,
        )
        entry = MeetingTask.objects.create(meeting=meeting, task=task, position=1)

        response = self.client.post(
            reverse("meeting_action_add", args=[meeting.pk, entry.pk]),
            {"new_action_item": "Must not be added"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Reopen the task", response.json()["error"])
        self.assertFalse(ActionItem.objects.filter(task=task).exists())

    def test_cannot_reopen_completed_action_while_task_is_done(self):
        task = self.create_task(status=Task.Status.DONE)
        item = ActionItem.objects.create(
            task=task,
            content="Completed item",
            is_completed=True,
            completed_by=self.member,
            completed_at=timezone.now(),
            created_by=self.admin,
            published_at=timezone.now(),
        )

        response = self.client.post(
            reverse("action_item_toggle", args=[item.pk]),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Reopen the task", response.json()["error"])
        item.refresh_from_db()
        self.assertTrue(item.is_completed)

    def test_admin_forms_cannot_bypass_task_action_invariant(self):
        task = self.create_task()
        ActionItem.objects.create(task=task, content="Open item", created_by=self.admin)
        task_form = AdminTaskForm(data={
            "title": task.title,
            "description": "",
            "scopes": [self.scope.pk],
            "parent_task": "",
            "related_tasks": [],
            "assignee": self.member.pk,
            "status": Task.Status.DONE,
            "priority": task.priority,
            "due_date": "",
            "timeline_start_date": "",
            "timeline_group": "",
            "link_url": task.link_url,
            "score": "",
            "created_by": self.admin.pk,
            "completed_by": "",
            "completed_at": "",
            "is_archived": "",
        }, instance=task)
        self.assertFalse(task_form.is_valid())
        self.assertIn("Complete 1 open Action Item", task_form.errors["status"][0])

        task.status = Task.Status.DONE
        task.save(update_fields=["status"])
        item_form = AdminActionItemForm(data={
            "task": task.pk,
            "meeting": "",
            "source_meeting_title": "",
            "source_meeting_date": "",
            "content": "Admin open item",
            "assignee": self.member.pk,
            "due_date": "",
            "is_completed": "",
            "completed_by": "",
            "completed_at": "",
            "created_by": self.admin.pk,
            "published_at": "",
        })
        self.assertFalse(item_form.is_valid())
        self.assertIn("Reopen the task", item_form.errors["is_completed"][0])

    def test_completed_done_actions_do_not_render_reopen_or_add_queries_per_item(self):
        task = self.create_task(status=Task.Status.DONE)
        ActionItem.objects.create(
            task=task, content="First completed", is_completed=True,
            completed_by=self.member, completed_at=timezone.now(),
            created_by=self.admin, published_at=timezone.now(),
        )
        with CaptureQueriesContext(connection) as one_item_queries:
            one_item_response = self.client.get(reverse("task_history", args=[task.pk]))

        for number in range(15):
            ActionItem.objects.create(
                task=task, content=f"Completed {number}", is_completed=True,
                completed_by=self.member, completed_at=timezone.now(),
                created_by=self.admin, published_at=timezone.now(),
            )
        with CaptureQueriesContext(connection) as many_item_queries:
            many_item_response = self.client.get(reverse("task_history", args=[task.pk]))

        self.assertNotContains(one_item_response, "Reopen action item")
        self.assertNotContains(many_item_response, "Reopen action item")
        self.assertLessEqual(len(many_item_queries), len(one_item_queries))


class OccurrenceWriterRotationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="rotation-admin",
            password="test-password",
            display_name="Rotation Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.writers = [
            User.objects.create_user(
                username=f"writer-{number}",
                password="test-password",
                display_name=f"Writer {number}",
                account_status=User.AccountStatus.ACTIVE,
            )
            for number in range(1, 4)
        ]
        self.rotation = MinuteWriterRotation.objects.create(
            name="Occurrence rotation",
            anchor_date=date(2026, 8, 25),
            created_by=self.admin,
        )
        MinuteWriterRotationMember.objects.bulk_create([
            MinuteWriterRotationMember(rotation=self.rotation, user=writer, position=position)
            for position, writer in enumerate(self.writers, start=1)
        ])
        self.client.force_login(self.admin)

    def automatic_payload(self, title, meeting_date, rotation=None):
        return {
            "title": title,
            "meeting_date": meeting_date.isoformat(),
            "host": self.admin.pk,
            "writer_assignment": Meeting.WriterAssignment.AUTOMATIC,
            "writer_rotation": (rotation or self.rotation).pk,
            "minute_taker": "",
        }

    def create_automatic_meeting(self, title, meeting_date):
        response = self.client.post(
            reverse("meeting_add"),
            self.automatic_payload(title, meeting_date),
        )
        self.assertEqual(response.status_code, 302)
        return Meeting.objects.get(title=title)

    def test_rotation_advances_by_meeting_occurrence_not_calendar_week(self):
        first = self.create_automatic_meeting("First", date(2026, 8, 25))
        second = self.create_automatic_meeting("After skipped weeks", date(2026, 10, 20))
        third = self.create_automatic_meeting("Third", date(2026, 10, 21))

        self.assertEqual(
            [first.minute_taker_id, second.minute_taker_id, third.minute_taker_id],
            [writer.pk for writer in self.writers],
        )

    def test_manual_meeting_does_not_advance_rotation(self):
        response = self.client.post(reverse("meeting_add"), {
            "title": "Manual",
            "meeting_date": "2026-09-01",
            "host": self.admin.pk,
            "writer_assignment": Meeting.WriterAssignment.MANUAL,
            "writer_rotation": self.rotation.pk,
            "minute_taker": self.admin.pk,
        })
        self.assertEqual(response.status_code, 302)

        automatic = self.create_automatic_meeting("Automatic", date(2026, 9, 8))

        self.assertEqual(automatic.minute_taker_id, self.writers[0].pk)

    def test_edit_same_automatic_rotation_preserves_writer_and_cursor(self):
        meeting = self.create_automatic_meeting("Original", date(2026, 8, 25))

        response = self.client.post(
            reverse("meeting_edit", args=[meeting.pk]),
            self.automatic_payload("Edited", date(2026, 11, 1)),
        )

        self.assertEqual(response.status_code, 302)
        meeting.refresh_from_db()
        self.rotation.refresh_from_db()
        self.assertEqual(meeting.minute_taker_id, self.writers[0].pk)
        self.assertEqual(self.rotation.last_assigned_writer_id, self.writers[0].pk)
        following = self.create_automatic_meeting("Following", date(2026, 11, 8))
        self.assertEqual(following.minute_taker_id, self.writers[1].pk)

    def test_edit_automatic_meeting_before_start_date_is_rejected(self):
        meeting = self.create_automatic_meeting("Original", date(2026, 8, 25))

        response = self.client.post(
            reverse("meeting_edit", args=[meeting.pk]),
            self.automatic_payload("Too early", date(2026, 8, 24)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has not started")
        meeting.refresh_from_db()
        self.assertEqual(meeting.meeting_date, date(2026, 8, 25))

    def test_existing_automatic_meeting_survives_anchor_moved_after_its_date(self):
        meeting = self.create_automatic_meeting("Existing before moved anchor", date(2026, 8, 25))
        self.rotation.anchor_date = date(2026, 9, 1)
        self.rotation.save(update_fields=["anchor_date"])

        response = self.client.post(
            reverse("meeting_edit", args=[meeting.pk]),
            self.automatic_payload("Still existing", date(2026, 8, 25)),
        )
        preview = self.client.get(reverse("minute_writer_preview"), {
            "rotation": self.rotation.pk,
            "meeting_date": "2026-08-25",
            "meeting": meeting.pk,
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["writer_id"], meeting.minute_taker_id)

    def test_deleting_draft_does_not_reassign_existing_meetings(self):
        first = self.create_automatic_meeting("First", date(2026, 8, 25))
        second = self.create_automatic_meeting("Second", date(2026, 9, 1))

        self.client.post(reverse("meeting_delete", args=[first.pk]))
        third = self.create_automatic_meeting("Third", date(2026, 9, 8))

        second.refresh_from_db()
        self.assertEqual(second.minute_taker_id, self.writers[1].pk)
        self.assertEqual(third.minute_taker_id, self.writers[2].pk)

    def test_reordering_members_continues_after_last_assigned_writer(self):
        self.create_automatic_meeting("First", date(2026, 8, 25))

        response = self.client.post(
            reverse("minute_writer_rotation_edit", args=[self.rotation.pk]),
            {
                "name": self.rotation.name,
                "anchor_date": self.rotation.anchor_date.isoformat(),
                "is_active": "on",
                "writers_order": ",".join(str(writer.pk) for writer in [
                    self.writers[2], self.writers[0], self.writers[1],
                ]),
            },
        )

        self.assertRedirects(response, reverse("minute_writer_rotations"))
        following = self.create_automatic_meeting("Following reorder", date(2026, 9, 1))
        self.assertEqual(following.minute_taker_id, self.writers[1].pk)

    def test_removing_last_assigned_writer_preserves_next_position(self):
        self.create_automatic_meeting("First", date(2026, 8, 25))

        response = self.client.post(
            reverse("minute_writer_rotation_edit", args=[self.rotation.pk]),
            {
                "name": self.rotation.name,
                "anchor_date": self.rotation.anchor_date.isoformat(),
                "is_active": "on",
                "writers_order": f"{self.writers[1].pk},{self.writers[2].pk}",
            },
        )

        self.assertRedirects(response, reverse("minute_writer_rotations"))
        following = self.create_automatic_meeting("Following removal", date(2026, 9, 1))
        self.assertEqual(following.minute_taker_id, self.writers[1].pk)

    def test_combined_remove_and_reorder_preserves_old_retained_successor(self):
        writer_four = User.objects.create_user(
            username="writer-4",
            password="test-password",
            display_name="Writer 4",
            account_status=User.AccountStatus.ACTIVE,
        )
        MinuteWriterRotationMember.objects.create(
            rotation=self.rotation, user=writer_four, position=4,
        )
        self.rotation.last_assigned_writer = self.writers[2]
        self.rotation.save(update_fields=["last_assigned_writer"])

        response = self.client.post(
            reverse("minute_writer_rotation_edit", args=[self.rotation.pk]),
            {
                "name": self.rotation.name,
                "anchor_date": self.rotation.anchor_date.isoformat(),
                "is_active": "on",
                "writers_order": f"{writer_four.pk},{self.writers[0].pk},{self.writers[1].pk}",
            },
        )

        self.assertRedirects(response, reverse("minute_writer_rotations"))
        following = self.create_automatic_meeting("Following combined edit", date(2026, 9, 1))
        self.assertEqual(following.minute_taker_id, writer_four.pk)

    def test_preview_preserves_writer_for_same_automatic_meeting(self):
        meeting = self.create_automatic_meeting("Preview existing", date(2026, 8, 25))

        response = self.client.get(reverse("minute_writer_preview"), {
            "rotation": self.rotation.pk,
            "meeting_date": "2026-09-01",
            "meeting": meeting.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["writer_id"], self.writers[0].pk)
        self.assertTrue(response.json()["preserved"])

    def test_preview_rejects_inactive_rotation_except_for_existing_assignment(self):
        meeting = self.create_automatic_meeting("Preview inactive", date(2026, 8, 25))
        self.rotation.is_active = False
        self.rotation.save(update_fields=["is_active"])

        rejected = self.client.get(reverse("minute_writer_preview"), {
            "rotation": self.rotation.pk,
            "meeting_date": "2026-09-01",
        })
        preserved = self.client.get(reverse("minute_writer_preview"), {
            "rotation": self.rotation.pk,
            "meeting_date": "2026-09-01",
            "meeting": meeting.pk,
        })

        self.assertEqual(rejected.status_code, 400)
        self.assertIn("inactive", rejected.json()["error"])
        self.assertEqual(preserved.status_code, 200)
        self.assertEqual(preserved.json()["writer_id"], meeting.minute_taker_id)

    def test_inactive_writer_is_skipped_without_losing_rotation_order(self):
        self.create_automatic_meeting("First", date(2026, 8, 25))
        self.writers[1].account_status = User.AccountStatus.INACTIVE
        self.writers[1].save(update_fields=["account_status"])

        following = self.create_automatic_meeting("After inactive", date(2026, 9, 1))

        self.assertEqual(following.minute_taker_id, self.writers[2].pk)

    def test_manual_meeting_cannot_switch_to_an_inactive_rotation(self):
        meeting = Meeting.objects.create(
            title="Manual draft",
            meeting_date=date(2026, 8, 25),
            host=self.admin,
            minute_taker=self.admin,
            writer_assignment=Meeting.WriterAssignment.MANUAL,
            writer_rotation=self.rotation,
            created_by=self.admin,
        )
        self.rotation.is_active = False
        self.rotation.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("meeting_edit", args=[meeting.pk]),
            self.automatic_payload("Automatic draft", date(2026, 8, 25)),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select an active Writer Rotation")
        meeting.refresh_from_db()
        self.assertEqual(meeting.writer_assignment, Meeting.WriterAssignment.MANUAL)
        self.assertEqual(meeting.minute_taker_id, self.admin.pk)


class WriterRotationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_automatic_meetings_receive_different_writers(self):
        admin = User.objects.create_user(
            username="concurrent-rotation-admin",
            password="test-password",
            display_name="Concurrent Rotation Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        writers = [
            User.objects.create_user(
                username=f"concurrent-writer-{number}",
                password="test-password",
                display_name=f"Concurrent Writer {number}",
                account_status=User.AccountStatus.ACTIVE,
            )
            for number in range(1, 3)
        ]
        rotation = MinuteWriterRotation.objects.create(
            name="Concurrent rotation",
            anchor_date=date(2026, 8, 25),
            created_by=admin,
        )
        MinuteWriterRotationMember.objects.bulk_create([
            MinuteWriterRotationMember(rotation=rotation, user=writer, position=position)
            for position, writer in enumerate(writers, start=1)
        ])
        rotation_lock_attempted = Event()
        results = {}

        def create_meeting(index):
            close_old_connections()
            client = Client()
            client.force_login(admin)

            def capture_rotation_lock(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper() and "core_minutewriterrotation" in sql.lower():
                    rotation_lock_attempted.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(capture_rotation_lock):
                    results[index] = client.post(reverse("meeting_add"), {
                        "title": f"Concurrent {index}",
                        "meeting_date": "2026-08-25",
                        "host": admin.pk,
                        "writer_assignment": Meeting.WriterAssignment.AUTOMATIC,
                        "writer_rotation": rotation.pk,
                        "minute_taker": "",
                    }).status_code
            finally:
                connection.close()

        with transaction.atomic():
            MinuteWriterRotation.objects.select_for_update().get(pk=rotation.pk)
            workers = [Thread(target=create_meeting, args=[index]) for index in range(2)]
            for worker in workers:
                worker.start()
            self.assertTrue(rotation_lock_attempted.wait(timeout=5))

        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, {0: 302, 1: 302})
        self.assertEqual(
            set(Meeting.objects.values_list("minute_taker_id", flat=True)),
            {writer.pk for writer in writers},
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_user_deactivation_waits_for_rotation_lock_before_replacing_writer(self):
        actor = User.objects.create_user(
            username="deactivation-actor",
            password="test-password",
            display_name="Deactivation Actor",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        target = User.objects.create_user(
            username="deactivation-target",
            password="test-password",
            display_name="Deactivation Target",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        source = User.objects.create_user(
            username="deactivation-source",
            password="test-password",
            display_name="Deactivation Source",
            account_status=User.AccountStatus.ACTIVE,
        )
        rotation = MinuteWriterRotation.objects.create(
            name="Deactivation rotation",
            anchor_date=date(2026, 8, 25),
            last_assigned_writer=source,
            created_by=actor,
        )
        MinuteWriterRotationMember.objects.create(rotation=rotation, user=source, position=1)
        MinuteWriterRotationMember.objects.create(rotation=rotation, user=target, position=2)
        lock_attempted = Event()
        result = {}

        def deactivate_source():
            close_old_connections()

            def capture_rotation_lock(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper() and "core_minutewriterrotation" in sql.lower():
                    lock_attempted.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(capture_rotation_lock):
                    deactivate_user_and_transfer(source, target, actor)
                result["completed"] = True
            finally:
                connection.close()

        with transaction.atomic():
            MinuteWriterRotation.objects.select_for_update().get(pk=rotation.pk)
            worker = Thread(target=deactivate_source)
            worker.start()
            self.assertTrue(lock_attempted.wait(timeout=5))

        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result.get("completed"))
        rotation.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(rotation.last_assigned_writer_id, target.pk)
        self.assertEqual(source.account_status, User.AccountStatus.INACTIVE)

    @skipUnlessDBFeature("has_select_for_update")
    def test_automatic_create_racing_deactivation_never_assigns_inactive_writer(self):
        actor = User.objects.create_user(
            username="writer-race-actor", password="test-password",
            display_name="Writer Race Actor", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        target = User.objects.create_user(
            username="writer-race-target", password="test-password",
            display_name="Writer Race Target", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        source = User.objects.create_user(
            username="writer-race-source", password="test-password",
            display_name="Writer Race Source", account_status=User.AccountStatus.ACTIVE,
        )
        rotation = MinuteWriterRotation.objects.create(
            name="Writer deactivation race", anchor_date=date(2026, 8, 25),
            created_by=actor,
        )
        MinuteWriterRotationMember.objects.bulk_create([
            MinuteWriterRotationMember(rotation=rotation, user=source, position=1),
            MinuteWriterRotationMember(rotation=rotation, user=target, position=2),
        ])
        results = {}
        start_together = Barrier(2)

        def create_meeting():
            close_old_connections()
            client = Client()
            client.force_login(actor)
            try:
                start_together.wait(timeout=5)
                results["create"] = client.post(reverse("meeting_add"), {
                    "title": "Racing automatic meeting",
                    "meeting_date": "2026-08-25",
                    "host": actor.pk,
                    "writer_assignment": Meeting.WriterAssignment.AUTOMATIC,
                    "writer_rotation": rotation.pk,
                    "minute_taker": "",
                }).status_code
            finally:
                connection.close()

        def deactivate_writer():
            close_old_connections()
            try:
                start_together.wait(timeout=5)
                deactivate_user_and_transfer(source, target, actor)
                results["deactivate"] = True
            finally:
                connection.close()

        workers = [Thread(target=create_meeting), Thread(target=deactivate_writer)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive())

        self.assertEqual(results.get("create"), 302)
        self.assertTrue(results.get("deactivate"))
        meeting = Meeting.objects.get(title="Racing automatic meeting")
        source.refresh_from_db()
        self.assertEqual(source.account_status, User.AccountStatus.INACTIVE)
        self.assertEqual(meeting.minute_taker_id, target.pk)


class TaskDoneConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @skipUnlessDBFeature("has_select_for_update")
    def test_open_action_committed_before_task_lock_release_blocks_done_transition(self):
        admin = User.objects.create_user(
            username="done-concurrency-admin",
            password="test-password",
            display_name="Done Concurrency Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        member = User.objects.create_user(
            username="done-concurrency-member",
            password="test-password",
            display_name="Done Concurrency Member",
            account_status=User.AccountStatus.ACTIVE,
        )
        scope = Scope.objects.create(name="Done concurrency")
        task = Task.objects.create(
            title="Concurrent completion",
            status=Task.Status.IN_PROGRESS,
            assignee=member,
            priority=Task.Priority.MEDIUM,
            link_url="https://example.com/concurrent-completion",
            created_by=admin,
        )
        task.scopes.add(scope)
        lock_attempted = Event()
        result = {}

        def mark_done():
            close_old_connections()
            client = Client()
            client.force_login(admin)

            def capture_task_lock(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper() and "core_task" in sql.lower():
                    lock_attempted.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(capture_task_lock):
                    result["response"] = client.post(reverse("task_edit", args=[task.pk]), {
                        "title": task.title,
                        "description": "",
                        "scopes": [scope.pk],
                        "parent_task": "",
                        "related_tasks": [],
                        "assignee": member.pk,
                        "status": Task.Status.DONE,
                        "timeline_start_date": "",
                        "due_date": "",
                        "priority": Task.Priority.MEDIUM,
                        "boards": [],
                        "link_url": task.link_url,
                        "status_note": "",
                    })
            finally:
                connection.close()

        with transaction.atomic():
            Task.objects.select_for_update().get(pk=task.pk)
            worker = Thread(target=mark_done)
            worker.start()
            self.assertTrue(lock_attempted.wait(timeout=5))
            ActionItem.objects.create(
                task=task,
                content="Committed before completion",
                created_by=admin,
            )

        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["response"].status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)

    @skipUnlessDBFeature("has_select_for_update")
    def test_action_toggle_waits_for_meeting_before_locking_task(self):
        admin = User.objects.create_user(
            username="toggle-order-admin", password="test-password",
            display_name="Toggle Order Admin", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        task = Task.objects.create(
            title="Toggle lock order", status=Task.Status.IN_PROGRESS,
            link_url="https://example.com/toggle-order", created_by=admin,
        )
        meeting = Meeting.objects.create(
            title="Toggle meeting", meeting_date=date(2026, 8, 25),
            host=admin, minute_taker=admin, created_by=admin,
        )
        item = ActionItem.objects.create(
            task=task, meeting=meeting, content="Toggle me", created_by=admin,
            published_at=timezone.now(),
        )
        meeting_lock = Event()
        task_lock = Event()
        result = {}

        def toggle_item():
            close_old_connections()
            client = Client()
            client.force_login(admin)

            def capture_locks(execute, sql, params, many, context):
                lowered = sql.lower()
                if "for update" in lowered and "core_meeting" in lowered:
                    meeting_lock.set()
                if "for update" in lowered and "core_task" in lowered:
                    task_lock.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(capture_locks):
                    result["status"] = client.post(
                        reverse("action_item_toggle", args=[item.pk]),
                    ).status_code
            finally:
                connection.close()

        with transaction.atomic():
            Meeting.objects.select_for_update().get(pk=meeting.pk)
            worker = Thread(target=toggle_item)
            worker.start()
            self.assertTrue(meeting_lock.wait(timeout=5))
            self.assertFalse(task_lock.wait(timeout=0.25))

        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["status"], 302)
        item.refresh_from_db()
        self.assertTrue(item.is_completed)

    @skipUnlessDBFeature("has_select_for_update")
    def test_deactivation_waits_for_meeting_before_locking_task(self):
        actor = User.objects.create_user(
            username="transfer-order-actor", password="test-password",
            display_name="Transfer Order Actor", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        target = User.objects.create_user(
            username="transfer-order-target", password="test-password",
            display_name="Transfer Order Target", role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        source = User.objects.create_user(
            username="transfer-order-source", password="test-password",
            display_name="Transfer Order Source", account_status=User.AccountStatus.ACTIVE,
        )
        task = Task.objects.create(
            title="Transfer lock order", status=Task.Status.IN_PROGRESS,
            assignee=source, link_url="https://example.com/transfer-order",
            created_by=actor,
        )
        meeting = Meeting.objects.create(
            title="Transfer meeting", meeting_date=date(2026, 8, 25),
            host=actor, minute_taker=actor, created_by=actor,
        )
        MeetingTask.objects.create(meeting=meeting, task=task, position=1)
        meeting_lock = Event()
        task_lock = Event()
        result = {}

        def deactivate_source():
            close_old_connections()

            def capture_locks(execute, sql, params, many, context):
                lowered = sql.lower()
                if "for update" in lowered and "core_meeting" in lowered:
                    meeting_lock.set()
                if "for update" in lowered and "core_task" in lowered:
                    task_lock.set()
                return execute(sql, params, many, context)

            try:
                with connection.execute_wrapper(capture_locks):
                    deactivate_user_and_transfer(source, target, actor)
                result["completed"] = True
            finally:
                connection.close()

        with transaction.atomic():
            Meeting.objects.select_for_update().get(pk=meeting.pk)
            worker = Thread(target=deactivate_source)
            worker.start()
            self.assertTrue(meeting_lock.wait(timeout=5))
            self.assertFalse(task_lock.wait(timeout=0.25))

        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result.get("completed"))
        task.refresh_from_db()
        self.assertEqual(task.assignee_id, target.pk)
