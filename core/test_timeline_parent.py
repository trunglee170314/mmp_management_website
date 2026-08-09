"""Explicit tree drops change only parent/order, with atomic, conflict-safe Undo."""
import threading
import time
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import timeline_order
from .models import AuditLog, Meeting, MeetingTask, SystemSetting, Task, TaskHistory, TimelineGroup, TimelineMilestone, User


class TimelineTaskParentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tree-member", account_status="active")
        self.other = User.objects.create_user(username="tree-other", account_status="active", role="admin")
        self.group = TimelineGroup.objects.create(name="Tree Group")
        self.other_group = TimelineGroup.objects.create(name="Other Tree Group")
        self.day = timezone.localdate()
        self.a, self.b, self.c = [self.make_task(name) for name in "ABC"]
        self.client.force_login(self.user)

    def make_task(self, title, **extra):
        return Task.objects.create(title=title, timeline_group=extra.pop("timeline_group", self.group),
                                   timeline_start_date=self.day, due_date=self.day + timedelta(days=2),
                                   link_url="https://example.test/tree", **extra)

    def members(self):
        return list(Task.objects.filter(timeline_group=self.group, is_archived=False).order_by("pk"))

    def payload(self, task, parent=None, **extra):
        task.refresh_from_db()
        members = list(Task.objects.filter(timeline_group_id=task.timeline_group_id, is_archived=False).order_by("pk"))
        return {"revision": task.updated_at.isoformat(), "order_revision": timeline_order.order_revision(members, task.timeline_group_id),
                "parent_id": parent.pk if parent else "", **extra}

    def post(self, task, parent=None, **extra):
        return self.client.post(reverse("timeline_task_parent", args=[task.pk]), self.payload(task, parent, **extra))

    def order(self):
        return [item.pk for item in timeline_order.canonical_order(self.members())]

    def undo(self, task, response):
        data = response.json()
        return self.client.post(reverse("timeline_task_parent", args=[task.pk]), {
            "revision": data["task"]["revision"], "order_revision": data["order_revision"], **data["undo"],
        })

    def fields(self):
        return list(Task.objects.order_by("pk").values_list("pk", "title", "description", "timeline_group_id", "timeline_start_date", "due_date", "assignee_id", "status", "priority", "link_url"))

    def test_active_member_can_attach_change_remove_parent_with_history_and_audit(self):
        before = self.fields()
        response = self.post(self.c, self.a)
        self.assertEqual(response.status_code, 200, response.content)
        self.c.refresh_from_db(); self.assertEqual(self.c.parent_task_id, self.a.pk)
        self.assertEqual(self.order(), [self.a.pk, self.c.pk, self.b.pk])
        self.assertEqual(response.json()["task"]["parent"], self.a.pk)
        response = self.post(self.c, self.b)
        self.assertEqual(response.status_code, 200, response.content)
        self.c.refresh_from_db(); self.assertEqual(self.c.parent_task_id, self.b.pk)
        response = self.post(self.c)
        self.assertEqual(response.status_code, 200, response.content)
        self.c.refresh_from_db(); self.assertIsNone(self.c.parent_task_id)
        self.assertEqual(self.fields(), before)
        for event in ("Parent Task added", "Parent Task changed", "Parent Task removed"):
            self.assertEqual(self.c.history.filter(event=event, actor=self.user).count(), 1)
        changed = self.c.history.get(event="Parent Task changed")
        self.assertEqual((changed.old_value, changed.new_value), ("A", "B"))
        self.assertEqual(AuditLog.objects.filter(entity_id=self.c.pk, action="Timeline parent changed").count(), 3)

    def test_move_parent_keeps_entire_subtree_hidden_unscheduled_members_and_flags(self):
        child = self.make_task("Hidden child", parent_task=self.a)
        grand = self.make_task("Grandchild", parent_task=child)
        unscheduled = self.make_task("Unscheduled child", parent_task=child)
        unscheduled.timeline_start_date = unscheduled.due_date = None; unscheduled.save()
        flag = TimelineMilestone.objects.create(name="Child flag", task=grand, date=self.day, created_by=self.user)
        before = self.fields()
        response = self.post(self.a, self.b)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order(), [self.b.pk, self.a.pk, child.pk, grand.pk, unscheduled.pk, self.c.pk])
        child.refresh_from_db(); grand.refresh_from_db(); flag.refresh_from_db()
        self.assertEqual((child.parent_task_id, grand.parent_task_id, flag.task_id), (self.a.pk, child.pk, grand.pk))
        self.assertEqual(self.fields(), before)

    def test_edge_drop_adopts_anchor_parent_and_places_before_or_after_whole_subtree(self):
        child = self.make_task("A child", parent_task=self.a)
        grand = self.make_task("Grandchild", parent_task=child)
        response = self.post(self.b, self.a, anchor_id=child.pk, placement="after")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order(), [self.a.pk, child.pk, grand.pk, self.b.pk, self.c.pk])
        response = self.post(self.c, self.a, anchor_id=child.pk, placement="before")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order(), [self.a.pk, self.c.pk, child.pk, grand.pk, self.b.pk])
        response = self.post(child, None, anchor_id=self.a.pk, placement="after")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order(), [self.a.pk, self.c.pk, self.b.pk, child.pk, grand.pk])

    def test_signed_undo_restores_parent_and_exact_previous_order_not_dates_or_other_fields(self):
        self.c.parent_task = self.a; self.c.save()
        original = self.order()
        response = self.post(self.c, self.b)
        self.assertEqual(response.status_code, 200)
        self.a.description = "Concurrent description survives"; self.a.due_date += timedelta(days=5)
        self.a.save(update_fields=["description", "due_date", "updated_at"])
        undone = self.undo(self.c, response)
        self.assertEqual(undone.status_code, 200, undone.content)
        self.c.refresh_from_db(); self.a.refresh_from_db()
        self.assertEqual(self.c.parent_task_id, self.a.pk); self.assertEqual(self.order(), original)
        self.assertEqual(self.a.description, "Concurrent description survives")
        self.assertEqual(self.a.due_date, self.day + timedelta(days=7))
        self.assertEqual(self.c.history.filter(event="Parent Task changed").count(), 2)

    def test_undo_can_restore_original_archived_cross_group_parent(self):
        external = self.make_task("Original external parent", timeline_group=self.other_group, is_archived=True)
        self.c.parent_task = external; self.c.save()
        response = self.post(self.c)
        self.assertEqual(response.status_code, 200)
        undone = self.undo(self.c, response)
        self.assertEqual(undone.status_code, 200, undone.content)
        self.c.refresh_from_db(); self.assertEqual(self.c.parent_task_id, external.pk)
        self.assertEqual(undone.json()["task"]["order_parent"], "")

    def test_undo_token_is_actor_task_bound_and_tamper_resistant(self):
        response = self.post(self.c, self.a)
        token = response.json()["undo"]["restore"]
        self.assertEqual(self.post(self.c, restore=token + "tampered").status_code, 400)
        self.assertEqual(self.post(self.b, restore=token).status_code, 400)
        self.client.force_login(self.other)
        self.assertEqual(self.post(self.c, restore=token).status_code, 400)
        self.c.refresh_from_db(); self.assertEqual(self.c.parent_task_id, self.a.pk)

    def test_undo_cannot_restore_a_deleted_external_parent(self):
        external = self.make_task("External original parent", timeline_group=self.other_group)
        self.c.parent_task = external; self.c.save()
        response = self.post(self.c)
        external.delete()
        self.assertEqual(self.undo(self.c, response).status_code, 409)
        self.c.refresh_from_db(); self.assertIsNone(self.c.parent_task_id)

    def test_stale_source_or_group_snapshot_rejects_drop_and_undo(self):
        payload = self.payload(self.c, self.a)
        self.c.description = "Concurrent edit"; self.c.save()
        self.assertEqual(self.client.post(reverse("timeline_task_parent", args=[self.c.pk]), payload).status_code, 409)
        response = self.post(self.c, self.a)
        self.assertEqual(response.status_code, 200)
        self.make_task("New group member")
        self.assertEqual(self.undo(self.c, response).status_code, 409)
        payload = self.payload(self.c, self.b)
        self.b.parent_task = self.a; self.b.save()
        self.assertEqual(self.client.post(reverse("timeline_task_parent", args=[self.c.pk]), payload).status_code, 409)

    def test_self_and_deep_descendant_cycles_are_rejected_even_across_groups_and_archived_nodes(self):
        self.b.parent_task = self.a; self.b.save()
        self.assertEqual(self.post(self.a, self.a).status_code, 400)
        self.assertEqual(self.post(self.a, self.b).status_code, 400)
        bridge = self.make_task("Archived cross Group bridge", timeline_group=self.other_group, parent_task=self.a, is_archived=True)
        self.c.parent_task = bridge; self.c.save()
        self.assertEqual(self.post(self.a, self.c).status_code, 400)
        self.a.refresh_from_db(); self.assertIsNone(self.a.parent_task_id)
        self.assertFalse(TaskHistory.objects.filter(event__startswith="Parent Task").exists())

    def test_missing_archived_other_group_or_related_parent_is_rejected(self):
        archived = self.make_task("Archived", is_archived=True)
        external = self.make_task("External", timeline_group=self.other_group)
        for parent in [archived, external]:
            self.assertEqual(self.post(self.c, parent).status_code, 400)
        self.assertEqual(self.post(self.c, parent_id="999999").status_code, 400)
        self.c.related_tasks.add(self.a)
        self.assertEqual(self.post(self.c, self.a).status_code, 400)
        self.assertTrue(self.c.related_tasks.filter(pk=self.a.pk).exists())

    def test_invalid_anchor_parent_or_own_subtree_anchor_is_rejected(self):
        self.b.parent_task = self.a; self.b.save()
        self.assertEqual(self.post(self.c, None, anchor_id=self.b.pk).status_code, 400)
        self.assertEqual(self.post(self.a, None, anchor_id=self.b.pk).status_code, 400)
        self.assertEqual(self.post(self.c, None, anchor_id=999999).status_code, 400)
        self.c.refresh_from_db(); self.assertIsNone(self.c.parent_task_id)

    def test_authentication_post_active_and_revision_requirements(self):
        endpoint = reverse("timeline_task_parent", args=[self.c.pk])
        valid = self.payload(self.c, self.a)
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        for field in ["revision", "order_revision"]:
            payload = valid.copy(); payload.pop(field)
            self.assertEqual(self.client.post(endpoint, payload).status_code, 428)
        for field, value in [("revision", "bad"), ("order_revision", "bad"), ("parent_id", "-1"), ("parent_id", "bad"), ("anchor_id", "bad"), ("placement", "inside")]:
            self.assertEqual(self.client.post(endpoint, {**valid, field: value}).status_code, 400)
        payload = valid.copy(); payload.pop("parent_id")
        self.assertEqual(self.client.post(endpoint, payload).status_code, 400)
        self.client.logout(); self.assertEqual(self.client.post(endpoint, valid).status_code, 302)
        self.user.account_status = "inactive"
        request = RequestFactory().post(endpoint, valid); request.user = self.user
        self.assertEqual(timeline_order.timeline_task_parent(request, self.c.pk).status_code, 403)

    def test_noop_root_drop_does_not_write_history_or_revision(self):
        revision = self.c.updated_at
        response = self.post(self.c)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["updated"]); self.assertIsNone(response.json()["undo"])
        self.c.refresh_from_db(); self.assertEqual(self.c.updated_at, revision)
        self.assertFalse(TaskHistory.objects.exists())

    def test_ungrouped_tasks_support_parent_and_root_moves(self):
        Task.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(timeline_group=None)
        response = self.post(self.b, self.a)
        self.assertEqual(response.status_code, 200, response.content)
        self.b.refresh_from_db(); self.assertEqual(self.b.parent_task_id, self.a.pk); self.assertIsNone(self.b.timeline_group_id)
        self.assertEqual(self.post(self.b).status_code, 200)
        self.b.refresh_from_db(); self.assertIsNone(self.b.parent_task_id); self.assertIsNone(self.b.timeline_group_id)

    def test_archived_or_missing_source_is_not_mutable(self):
        payload = self.payload(self.c, self.a)
        self.c.is_archived = True; self.c.save()
        self.assertEqual(self.client.post(reverse("timeline_task_parent", args=[self.c.pk]), payload).status_code, 404)
        self.assertEqual(self.client.post(reverse("timeline_task_parent", args=[999999]), payload).status_code, 404)

    def test_failure_rolls_back_parent_rank_and_history_together(self):
        before = list(Task.objects.order_by("pk").values_list("pk", "parent_task_id", "timeline_position", "updated_at"))
        with patch("core.timeline_order.record_audit", side_effect=RuntimeError("Audit failed")):
            with self.assertRaises(RuntimeError): self.post(self.c, self.a)
        self.assertEqual(list(Task.objects.order_by("pk").values_list("pk", "parent_task_id", "timeline_position", "updated_at")), before)
        self.assertFalse(TaskHistory.objects.exists())

    def test_parent_changes_wake_draft_meeting_after_commit_but_never_rewrite_snapshots(self):
        for status in [Meeting.Status.DRAFT, Meeting.Status.FINALIZED]:
            meeting = Meeting.objects.create(title="Frozen meeting order", meeting_date=self.day, host=self.user, minute_taker=self.user, created_by=self.user, status=status)
            MeetingTask.objects.create(meeting=meeting, task=self.c, position=42, snapshot={"title": "Frozen parent"})
        before = list(MeetingTask.objects.order_by("pk").values_list("pk", "position", "snapshot", "updated_at"))
        with patch("core.timeline_order.touch_draft_meeting_entries_for_task") as wake:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.post(self.c, self.a).status_code, 200)
                wake.assert_not_called()
            wake.assert_called_once_with(self.c.pk, self.user)
        self.assertEqual(list(MeetingTask.objects.order_by("pk").values_list("pk", "position", "snapshot", "updated_at")), before)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class TimelineParentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        SystemSetting.load()
        self.user = User.objects.create_user(username="tree-race", account_status="active")
        self.group = TimelineGroup.objects.create(name="Concurrent tree")
        self.a, self.b = [Task.objects.create(title=name, timeline_group=self.group, link_url="https://example.test/tree") for name in "AB"]
        self.signature = timeline_order.order_revision([self.a, self.b], self.group.pk)
        self.clients = [Client(), Client()]
        for client in self.clients: client.force_login(self.user)

    def post(self, client, source, parent):
        return client.post(reverse("timeline_task_parent", args=[source.pk]), {
            "revision": source.updated_at.isoformat(), "order_revision": self.signature, "parent_id": parent.pk,
        }).status_code

    def race(self, *operations):
        results, errors = [], []
        def run(operation):
            close_old_connections()
            try:
                with connection.cursor() as cursor: cursor.execute("SET statement_timeout = '6s'")
                results.append(operation())
            except Exception as error: errors.append(repr(error))
            finally: connection.close()
        workers = [threading.Thread(target=run, args=[operation], daemon=True) for operation in operations]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=15)
        self.assertFalse(any(worker.is_alive() for worker in workers)); self.assertEqual(errors, [])
        return results

    def test_simultaneous_inverse_parent_moves_cannot_create_a_cycle(self):
        results = self.race(lambda: self.post(self.clients[0], self.a, self.b), lambda: self.post(self.clients[1], self.b, self.a))
        self.assertEqual(sorted(results), [200, 409])
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertFalse(self.a.parent_task_id == self.b.pk and self.b.parent_task_id == self.a.pk)

    def test_ordinary_parent_editor_fk_key_share_does_not_deadlock_full_graph_lock(self):
        locked, graph_query = threading.Event(), threading.Event()
        def editor():
            with transaction.atomic():
                task = Task.objects.select_for_update().get(pk=self.b.pk)
                locked.set()
                if not graph_query.wait(5): raise TimeoutError("Graph lock was not attempted")
                time.sleep(.2); task.parent_task = self.a; task.save(update_fields=["parent_task", "updated_at"])
            return 200
        def move():
            if not locked.wait(5): raise TimeoutError("Editor did not lock Task")
            def observe(execute, sql, params, many, context):
                if '"core_task"' in sql and "FOR NO KEY UPDATE" in sql: graph_query.set()
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe): return self.post(self.clients[0], self.a, self.b)
        self.assertEqual(sorted(self.race(editor, move)), [200, 409])
