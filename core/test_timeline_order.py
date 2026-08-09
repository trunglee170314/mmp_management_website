"""Persistent order edits preserve all Group members and Task business fields."""
import threading
import time
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import timeline_order
from .models import AuditLog, Meeting, MeetingTask, Scope, SystemSetting, Task, TaskHistory, TimelineGroup, User


class TimelineTaskOrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="order-member", display_name="Order Member", account_status="active")
        self.group = TimelineGroup.objects.create(name="Ordered Group")
        self.other_group = TimelineGroup.objects.create(name="Other Group")
        self.scope = Scope.objects.create(name="Order scope")
        self.day = timezone.localdate()
        self.tasks = [self.make_task(name, index) for index, name in enumerate(["A visible", "B hidden", "C visible"])]
        self.a, self.b, self.c = self.tasks
        self.client.force_login(self.user)

    def make_task(self, title, index=0, **extra):
        task = Task.objects.create(title=title, timeline_group=self.group, created_by=self.user,
                                   timeline_start_date=self.day + timedelta(days=index * 3),
                                   due_date=self.day + timedelta(days=index * 3 + 1),
                                   link_url="https://example.test/order", **extra)
        task.scopes.add(self.scope)
        return task

    def members(self, group=None):
        return list(Task.objects.filter(timeline_group=self.group if group is None else group, is_archived=False).order_by("pk"))

    def signature(self, group=None):
        members = self.members(group)
        return timeline_order.order_revision(members, members[0].timeline_group_id if members else None)

    def payload(self, task, anchor, placement="after", **extra):
        task.refresh_from_db()
        data = {"revision": task.updated_at.isoformat(), "order_revision": timeline_order.order_revision(
            list(Task.objects.filter(timeline_group_id=task.timeline_group_id, is_archived=False).order_by("pk")), task.timeline_group_id),
            "anchor_id": anchor.pk, "placement": placement}
        data.update(extra)
        return data

    def post(self, task, anchor, placement="after", **extra):
        return self.client.post(reverse("timeline_task_order", args=[task.pk]), self.payload(task, anchor, placement, **extra), HTTP_ACCEPT="application/json")

    def order(self):
        return [task.pk for task in timeline_order.canonical_order(self.members())]

    def business_fields(self):
        return list(Task.objects.filter(pk__in=[task.pk for task in self.tasks]).order_by("pk").values_list(
            "pk", "timeline_start_date", "due_date", "timeline_group_id", "parent_task_id", "status", "assignee_id"))

    def test_active_member_reorders_and_persists_without_changing_business_fields(self):
        before = self.business_fields()
        response = self.post(self.c, self.a, timeline_start_date="2099-01-01", timeline_group=self.other_group.pk, parent_task=self.b.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order(), [self.a.pk, self.c.pk, self.b.pk])
        self.assertEqual(self.business_fields(), before)
        self.assertEqual(response.json()["undo"], {"anchor_id": self.b.pk, "placement": "after"})
        self.assertEqual(response.json()["order"], [{"id": task_id, "position": index} for index, task_id in enumerate(self.order())])
        self.c.refresh_from_db()
        self.assertEqual(response.json()["task"]["revision"], self.c.updated_at.isoformat())
        self.assertEqual(response.json()["task"]["position"], 1)
        history = self.c.history.get(event="Timeline order changed")
        self.assertEqual(history.actor, self.user)
        self.assertIn("Position: 3", history.old_value)
        self.assertIn("Position: 2", history.new_value)
        audit = AuditLog.objects.get(entity_type="Task", entity_id=self.c.pk, action="Timeline order changed")
        self.assertEqual(audit.details["from"], [self.a.pk, self.b.pk, self.c.pk])
        self.assertEqual(audit.details["to"], self.order())

    def test_undo_inverse_anchor_restores_previous_order_and_records_history(self):
        moved = self.post(self.c, self.a).json()
        undo = self.client.post(reverse("timeline_task_order", args=[self.c.pk]), {
            "revision": moved["task"]["revision"], "order_revision": moved["order_revision"], **moved["undo"],
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertEqual(self.order(), [self.a.pk, self.b.pk, self.c.pk])
        self.assertEqual(self.c.history.filter(event="Timeline order changed").count(), 2)

    def test_stale_undo_rejected_after_another_member_moves_even_if_source_revision_unchanged(self):
        moved = self.post(self.c, self.a).json()
        self.assertEqual(self.post(self.b, self.a, "before").status_code, 200)
        self.c.refresh_from_db()
        self.assertEqual(self.c.updated_at.isoformat(), moved["task"]["revision"])
        undo = self.client.post(reverse("timeline_task_order", args=[self.c.pk]), {
            "revision": moved["task"]["revision"], "order_revision": moved["order_revision"], **moved["undo"],
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(undo.status_code, 409, undo.content)
        self.assertEqual(self.order(), [self.b.pk, self.a.pk, self.c.pk])

    def test_missing_invalid_and_stale_revision_do_not_write(self):
        valid = self.payload(self.c, self.a)
        for field in ("revision", "order_revision"):
            payload = valid.copy(); payload.pop(field)
            self.assertEqual(self.client.post(reverse("timeline_task_order", args=[self.c.pk]), payload).status_code, 428)
        for field, invalid in [("revision", "2026-01-01T00:00:00"), ("order_revision", "bad"), ("anchor_id", "invalid"), ("placement", "inside")]:
            payload = {**valid, field: invalid}
            self.assertEqual(self.client.post(reverse("timeline_task_order", args=[self.c.pk]), payload).status_code, 400)
        self.c.title = "Concurrent title edit"; self.c.save()
        self.assertEqual(self.client.post(reverse("timeline_task_order", args=[self.c.pk]), valid).status_code, 409)
        self.assertEqual(self.order(), [self.a.pk, self.b.pk, self.c.pk])
        self.assertFalse(TaskHistory.objects.filter(event="Timeline order changed").exists())

    def test_hidden_filtered_and_unscheduled_members_keep_their_relative_order(self):
        self.c.timeline_start_date = None; self.c.due_date = None; self.c.save()
        d = self.make_task("D visible", 4)
        e = self.make_task("E hidden", 5)
        page = self.client.get(reverse("task_timeline"), {"q": "visible"})
        descriptors = page.context["tasks_data_json"]
        self.assertEqual([item["id"] for item in descriptors], [self.a.pk, d.pk])
        expected_token = self.signature()
        self.assertTrue(all(item["order_revision"] == expected_token for item in descriptors))
        moved = self.post(d, self.a, order="malicious partial Group list")
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(self.order(), [self.a.pk, d.pk, self.b.pk, self.c.pk, e.pk])
        self.assertEqual([task_id for task_id in self.order() if task_id != d.pk], [self.a.pk, self.b.pk, self.c.pk, e.pk])

    def test_parent_moves_with_its_entire_same_group_subtree(self):
        child = self.make_task("A child", 4, parent_task=self.a)
        grandchild = self.make_task("A grandchild", 5, parent_task=child)
        unscheduled = self.make_task("A unscheduled", 6, parent_task=self.a)
        unscheduled.timeline_start_date = None; unscheduled.due_date = None; unscheduled.save()
        b_child = self.make_task("B child", 7, parent_task=self.b)
        parents_before = dict(Task.objects.values_list("pk", "parent_task_id"))
        response = self.post(self.a, self.b)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order(), [self.b.pk, b_child.pk, self.a.pk, child.pk, grandchild.pk, unscheduled.pk, self.c.pk])
        self.assertEqual(dict(Task.objects.values_list("pk", "parent_task_id")), parents_before)
        self.assertEqual(response.json()["undo"], {"anchor_id": self.b.pk, "placement": "before"})
        audit = AuditLog.objects.get(entity_id=self.a.pk, action="Timeline order changed")
        self.assertEqual(audit.details["moved_ids"], sorted([self.a.pk, child.pk, grandchild.pk, unscheduled.pk]))

    def test_child_reorders_only_among_siblings_and_cannot_reparent(self):
        first = self.make_task("First child", 4, parent_task=self.a)
        second = self.make_task("Second child", 5, parent_task=self.a)
        grandchild = self.make_task("Grandchild", 6, parent_task=first)
        self.assertEqual(self.post(second, first, "before").status_code, 200)
        self.assertEqual(self.order(), [self.a.pk, second.pk, first.pk, grandchild.pk, self.b.pk, self.c.pk])
        self.assertEqual(self.post(second, self.b).status_code, 400)
        self.assertEqual(self.post(self.a, first).status_code, 400)
        self.assertEqual(self.post(self.a, self.a).status_code, 400)
        second.refresh_from_db(); self.assertEqual(second.parent_task_id, self.a.pk)

    def test_cross_group_target_and_archived_source_are_rejected(self):
        other = Task.objects.create(title="Other Group target", timeline_group=self.other_group, link_url="https://example.test/other")
        self.assertEqual(self.post(self.c, other).status_code, 400)
        self.c.is_archived = True; self.c.save()
        self.assertEqual(self.post(self.c, self.a).status_code, 404)
        self.assertFalse(TaskHistory.objects.filter(event="Timeline order changed").exists())

    def test_parent_membership_and_rank_changes_invalidate_old_group_signature(self):
        for change in ("parent", "membership", "rank"):
            with self.subTest(change=change):
                payload = self.payload(self.c, self.a)
                if change == "parent":
                    self.b.parent_task = self.a; self.b.save()
                elif change == "membership":
                    self.make_task("New unscheduled phantom", 8).save()
                else:
                    Task.objects.filter(pk=self.b.pk).update(timeline_position=99)
                response = self.client.post(reverse("timeline_task_order", args=[self.c.pk]), payload)
                self.assertEqual(response.status_code, 409, response.content)

    def test_missing_and_archived_parents_are_effective_roots_even_when_a_filter_hides_real_parent(self):
        self.b.parent_task = self.a; self.b.save()
        page = self.client.get(reverse("task_timeline"), {"q": "B hidden"})
        self.assertEqual(page.context["tasks_data_json"][0]["order_parent"], self.a.pk)
        self.assertEqual(self.post(self.b, self.c).status_code, 400)
        self.a.is_archived = True; self.a.save()
        page = self.client.get(reverse("task_timeline"), {"q": "B hidden"})
        self.assertEqual(page.context["tasks_data_json"][0]["order_parent"], "")
        self.assertEqual(self.post(self.c, self.b, "before").status_code, 200)

    def test_ungrouped_reorders_and_null_positions_append_with_deterministic_duplicate_ties(self):
        Task.objects.filter(pk__in=[task.pk for task in self.tasks]).update(timeline_group=None)
        response = self.post(self.c, self.a)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["task"]["group"], "")
        self.assertEqual([item["id"] for item in response.json()["order"]], [self.a.pk, self.c.pk, self.b.pk])
        Task.objects.filter(pk=self.a.pk).update(timeline_position=5)
        Task.objects.filter(pk=self.b.pk).update(timeline_position=5)
        Task.objects.filter(pk=self.c.pk).update(timeline_position=None)
        members = list(Task.objects.filter(timeline_group=None))
        self.assertEqual([task.pk for task in timeline_order.canonical_order(members)], [self.a.pk, self.b.pk, self.c.pk])

    def test_dates_status_and_cross_group_transfer_undo_keep_rank_values(self):
        self.assertEqual(self.post(self.c, self.a).status_code, 200)
        self.c.refresh_from_db(); position = self.c.timeline_position
        for group in (self.other_group, self.group):
            self.c.refresh_from_db()
            response = self.client.post(reverse("timeline_task_update", args=[self.c.pk]), {
                "revision": self.c.updated_at.isoformat(), "timeline_group": group.pk,
                "timeline_start_date": self.c.timeline_start_date, "due_date": self.c.due_date,
            }, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.c.refresh_from_db(); self.assertEqual(self.c.timeline_position, position)
        self.assertEqual(self.order(), [self.a.pk, self.c.pk, self.b.pk])

    def test_other_member_date_status_changes_are_preserved_and_do_not_invalidate_order(self):
        payload = self.payload(self.c, self.a)
        self.b.due_date += timedelta(days=20); self.b.status = Task.Status.DONE; self.b.save()
        response = self.client.post(reverse("timeline_task_order", args=[self.c.pk]), payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.b.refresh_from_db()
        self.assertEqual(self.b.due_date, self.day + timedelta(days=24))
        self.assertEqual(self.b.status, Task.Status.DONE)

    def test_group_snapshot_is_one_query_and_new_unranked_task_appends(self):
        self.assertEqual(self.post(self.c, self.a).status_code, 200)
        newest = self.make_task("New default rank", 8)
        self.assertIsNone(newest.timeline_position)
        self.assertEqual(self.order(), [self.a.pk, self.c.pk, self.b.pk, newest.pk])
        with self.assertNumQueries(1):
            snapshot = timeline_order.group_order_snapshots([self.group.pk, self.other_group.pk, ""])
        self.assertEqual(set(snapshot), {str(self.group.pk), str(self.other_group.pk), ""})

    def test_deep_imported_parent_chain_does_not_hit_python_recursion_limit(self):
        members = [Task(pk=index + 100, title=str(index), timeline_group=self.group,
                        parent_task_id=index + 99 if index else None) for index in range(1200)]
        self.assertEqual([task.pk for task in timeline_order.canonical_order(members)], list(range(100, 1300)))

    def test_ordinary_task_form_and_meeting_quick_edit_do_not_erase_position(self):
        Task.objects.filter(pk=self.c.pk).update(timeline_position=7)
        form = {"title": self.c.title, "description": "", "scopes": [self.scope.pk], "parent_task": "", "related_tasks": [],
                "assignee": "", "status": "todo", "priority": "medium", "timeline_start_date": self.c.timeline_start_date,
                "due_date": self.c.due_date, "timeline_group": self.other_group.pk, "link_url": self.c.link_url, "modal": "1"}
        self.c.refresh_from_db(); form["revision"] = self.c.updated_at.isoformat()
        response = self.client.post(reverse("task_edit", args=[self.c.pk]), form)
        self.assertEqual(response.status_code, 200, response.content)
        self.c.refresh_from_db(); self.assertEqual(self.c.timeline_position, 7)
        meeting = Meeting.objects.create(title="Order Draft", meeting_date=self.day, host=self.user, minute_taker=self.user, created_by=self.user)
        entry = MeetingTask.objects.create(meeting=meeting, task=self.c, position=1, included=True)
        form.update(revision=self.c.updated_at.isoformat(), timeline_group=self.group.pk)
        response = self.client.post(reverse("meeting_task_edit", args=[meeting.pk, entry.pk]), form)
        self.assertEqual(response.status_code, 200, response.content)
        self.c.refresh_from_db(); self.assertEqual(self.c.timeline_position, 7)

    def test_reorder_never_changes_draft_or_finalized_meeting_review_positions_or_snapshots(self):
        for status in (Meeting.Status.DRAFT, Meeting.Status.FINALIZED):
            meeting = Meeting.objects.create(title=f"Order {status}", meeting_date=self.day, host=self.user, minute_taker=self.user,
                                             created_by=self.user, status=status)
            MeetingTask.objects.create(meeting=meeting, task=self.c, position=27, snapshot={"title": "Frozen order"})
        before = list(MeetingTask.objects.order_by("pk").values_list("pk", "position", "snapshot", "updated_at"))
        self.assertEqual(self.post(self.c, self.a).status_code, 200)
        self.assertEqual(list(MeetingTask.objects.order_by("pk").values_list("pk", "position", "snapshot", "updated_at")), before)

    def test_noop_is_not_a_change_and_cyclic_relations_are_safe(self):
        before_revision = self.b.updated_at
        response = self.post(self.b, self.a)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["updated"]); self.assertIsNone(response.json()["undo"])
        self.b.refresh_from_db(); self.assertEqual(self.b.updated_at, before_revision)
        self.assertFalse(TaskHistory.objects.filter(event="Timeline order changed").exists())
        Task.objects.filter(pk=self.a.pk).update(parent_task=self.b)
        Task.objects.filter(pk=self.b.pk).update(parent_task=self.a)
        self.assertEqual(self.post(self.a, self.c).status_code, 400)

    def test_unauthenticated_inactive_and_nonpost_requests_cannot_reorder(self):
        endpoint = reverse("timeline_task_order", args=[self.c.pk])
        self.assertEqual(self.client.get(endpoint).status_code, 405)
        self.client.logout(); self.assertEqual(self.client.post(endpoint).status_code, 302)
        self.user.account_status = User.AccountStatus.INACTIVE
        request = RequestFactory().post(endpoint, self.payload(self.c, self.a)); request.user = self.user
        self.assertEqual(timeline_order.timeline_task_order(request, self.c.pk).status_code, 403)


class TimelineTaskOrderMigrationTests(TransactionTestCase):
    def test_additive_position_upgrade_keeps_legacy_dates_group_parent_and_identity(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("core", "0028_interactive_timeline_and_map")])
        old = executor.loader.project_state([("core", "0028_interactive_timeline_and_map")]).apps
        try:
            group = old.get_model("core", "TimelineGroup").objects.create(name="Legacy order Group")
            parent = old.get_model("core", "Task").objects.create(title="Legacy parent", link_url="https://example.test/parent", timeline_group_id=group.pk)
            child = old.get_model("core", "Task").objects.create(title="Legacy ordered child", link_url="https://example.test/child",
                                                                 parent_task_id=parent.pk, timeline_group_id=group.pk,
                                                                 timeline_start_date="2026-01-01", due_date="2026-01-02")
            executor = MigrationExecutor(connection)
            executor.migrate([("core", "0029_task_timeline_position")])
            new = executor.loader.project_state([("core", "0029_task_timeline_position")]).apps
            current = new.get_model("core", "Task").objects.get(pk=child.pk)
            self.assertIsNone(current.timeline_position)
            self.assertEqual((current.parent_task_id, current.timeline_group_id), (parent.pk, group.pk))
            self.assertEqual((current.timeline_start_date.isoformat(), current.due_date.isoformat()), ("2026-01-01", "2026-01-02"))
            self.assertEqual(current.title, "Legacy ordered child")
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class TimelineTaskOrderConcurrencyTests(TransactionTestCase):
    def setUp(self):
        SystemSetting.load()
        self.user = User.objects.create_user(username="order-lock-member", account_status="active", role="admin")
        self.group = TimelineGroup.objects.create(name="Concurrent order")
        self.tasks = [Task.objects.create(title=f"Order {index}", timeline_group=self.group, link_url="https://example.test/order") for index in range(3)]
        self.a, self.b, self.c = self.tasks
        self.signature = timeline_order.order_revision(self.tasks, self.group.pk)
        self.first, self.second = Client(), Client(); self.first.force_login(self.user); self.second.force_login(self.user)

    def request(self, client, task, anchor, placement="after"):
        return client.post(reverse("timeline_task_order", args=[task.pk]), {
            "revision": task.updated_at.isoformat(), "order_revision": self.signature,
            "anchor_id": anchor.pk, "placement": placement,
        }).status_code

    def race(self, **operations):
        results, errors = {}, {}
        def run(name, operation):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '6s'")
                results[name] = operation()
            except Exception as error:
                errors[name] = repr(error)
            finally:
                connection.close()
        workers = [threading.Thread(target=run, args=(name, operation), daemon=True) for name, operation in operations.items()]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=15)
        self.assertFalse(any(worker.is_alive() for worker in workers), "Concurrent order request hung")
        self.assertEqual(errors, {}, errors)
        return results

    def test_two_reorders_with_one_snapshot_yield_one_success_and_one_conflict(self):
        results = self.race(first=lambda: self.request(self.first, self.c, self.a),
                            second=lambda: self.request(self.second, self.b, self.a, "before"))
        self.assertEqual(sorted(results.values()), [200, 409])
        self.assertEqual(TaskHistory.objects.filter(event="Timeline order changed").count(), 1)

    def test_parent_edit_fk_key_share_does_not_deadlock_reorder_no_key_locks(self):
        higher_locked, reorder_query = threading.Event(), threading.Event()
        def edit_parent():
            with transaction.atomic():
                higher = Task.objects.select_for_update().get(pk=self.b.pk)
                higher_locked.set()
                if not reorder_query.wait(timeout=5): raise TimeoutError("Reorder did not reach Task locks")
                time.sleep(.2)
                higher.parent_task_id = self.a.pk; higher.save(update_fields=["parent_task", "updated_at"])
            return "committed"
        def reorder():
            if not higher_locked.wait(timeout=5): raise TimeoutError("Parent editor did not acquire its Task")
            def observe(execute, sql, params, many, context):
                if '"core_task"' in sql and "FOR NO KEY UPDATE" in sql:
                    reorder_query.set()
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe):
                return self.request(self.first, self.c, self.a)
        results = self.race(edit=edit_parent, reorder=reorder)
        self.assertEqual(results, {"edit": "committed", "reorder": 409})
        self.b.refresh_from_db(); self.assertEqual(self.b.parent_task_id, self.a.pk)

    def test_group_delete_serializes_after_locked_reorder_without_data_loss(self):
        reorder_locked, delete_started = threading.Event(), threading.Event()
        original_move = timeline_order.moved_order
        def pause_move(*args, **kwargs):
            reorder_locked.set()
            if not delete_started.wait(timeout=5): raise TimeoutError("Group delete did not start")
            time.sleep(.2)
            return original_move(*args, **kwargs)
        def delete_group():
            if not reorder_locked.wait(timeout=5): raise TimeoutError("Reorder did not acquire its locks")
            delete_started.set()
            return self.second.post(reverse("timeline_group_delete", args=[self.group.pk]), {
                "revision": self.group.updated_at.isoformat(),
            }, HTTP_ACCEPT="application/json").status_code
        with patch("core.timeline_order.moved_order", pause_move):
            results = self.race(reorder=lambda: self.request(self.first, self.c, self.a), delete=delete_group)
        self.assertEqual(results, {"reorder": 200, "delete": 200})
        self.assertFalse(TimelineGroup.objects.filter(pk=self.group.pk).exists())
        self.assertEqual(Task.objects.filter(pk__in=[task.pk for task in self.tasks], timeline_group=None).count(), 3)
