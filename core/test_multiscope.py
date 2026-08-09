from datetime import datetime
from importlib import import_module

from django.core.management import call_command
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Meeting, MeetingTask, Scope, Task, TaskHistory, User
from core.services import task_snapshot


def create_task(*, scopes, **kwargs):
    task = Task.objects.create(**kwargs)
    task.scopes.set(scopes)
    return task


class TaskMultiScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="multi-scope-user",
            password="test-password",
            display_name="Multi Scope User",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.gst = Scope.objects.create(name="GST", color="#4D36E9", position=1)
        self.codec = Scope.objects.create(name="CODEC", color="#BF7D2B", position=2)
        self.other = Scope.objects.create(name="Other", color="#777777", position=3)
        self.client.force_login(self.user)

    def task_form_data(self, **overrides):
        data = {
            "title": "Multi-scope task",
            "description": "",
            "scopes": [self.gst.pk, self.codec.pk],
            "parent_task": "",
            "related_tasks": [],
            "assignee": "",
            "status": Task.Status.TODO,
            "priority": Task.Priority.MEDIUM,
            "timeline_start_date": "",
            "due_date": "",
            "boards": [],
            "link_url": "https://tasks.example.test/multi-scope",
            "status_note": "",
        }
        data.update(overrides)
        return data

    def test_task_form_saves_multiple_scopes(self):
        response = self.client.post(reverse("task_add"), self.task_form_data())

        self.assertRedirects(response, reverse("tasks"))
        task = Task.objects.get(title="Multi-scope task")
        self.assertEqual(
            list(task.scopes.order_by("position").values_list("name", flat=True)),
            ["GST", "CODEC"],
        )

    def test_task_form_requires_at_least_one_scope(self):
        response = self.client.post(
            reverse("task_add"),
            self.task_form_data(scopes=[]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "scopes", "This field is required.")
        self.assertFalse(Task.objects.filter(title="Multi-scope task").exists())

    def test_scope_filter_returns_multi_scope_task_once(self):
        task = create_task(
            title="GST and CODEC",
            scopes=[self.gst, self.codec],
            link_url="https://tasks.example.test/gst-codec",
            created_by=self.user,
        )
        create_task(
            title="Other only",
            scopes=[self.other],
            link_url="https://tasks.example.test/other",
            created_by=self.user,
        )

        response = self.client.get(reverse("tasks"), {"scope": self.codec.pk})

        self.assertEqual([row.pk for row in response.context["tasks"]], [task.pk])
        self.assertContains(response, "GST and CODEC")

    def test_multi_status_filter_uses_or_semantics(self):
        todo = create_task(
            title="Todo result",
            scopes=[self.gst],
            status=Task.Status.TODO,
            link_url="https://tasks.example.test/todo-result",
            created_by=self.user,
        )
        done = create_task(
            title="Done result",
            scopes=[self.gst],
            status=Task.Status.DONE,
            link_url="https://tasks.example.test/done-result",
            created_by=self.user,
        )
        create_task(
            title="Paused exclusion",
            scopes=[self.gst],
            status=Task.Status.PAUSED,
            link_url="https://tasks.example.test/paused-exclusion",
            created_by=self.user,
        )

        response = self.client.get(
            reverse("tasks"),
            [("status", Task.Status.TODO), ("status", Task.Status.DONE)],
        )

        self.assertEqual(
            {task.pk for task in response.context["tasks"]},
            {todo.pk, done.pk},
        )

    def test_filter_categories_are_combined_with_and_semantics(self):
        expected = create_task(
            title="High priority todo",
            scopes=[self.gst],
            status=Task.Status.TODO,
            priority=Task.Priority.HIGH,
            link_url="https://tasks.example.test/high-todo",
            created_by=self.user,
        )
        create_task(
            title="Low priority todo",
            scopes=[self.gst],
            status=Task.Status.TODO,
            priority=Task.Priority.LOW,
            link_url="https://tasks.example.test/low-todo",
            created_by=self.user,
        )
        create_task(
            title="High priority paused",
            scopes=[self.gst],
            status=Task.Status.PAUSED,
            priority=Task.Priority.HIGH,
            link_url="https://tasks.example.test/high-paused",
            created_by=self.user,
        )

        response = self.client.get(
            reverse("tasks"),
            {"status": Task.Status.TODO, "priority": Task.Priority.HIGH},
        )

        self.assertEqual([task.pk for task in response.context["tasks"]], [expected.pk])

    def test_multiple_scope_matches_do_not_duplicate_a_task(self):
        task = create_task(
            title="Two matching scopes",
            scopes=[self.gst, self.codec],
            link_url="https://tasks.example.test/two-matching-scopes",
            created_by=self.user,
        )

        response = self.client.get(
            reverse("tasks"),
            [("scope", self.gst.pk), ("scope", self.codec.pk)],
        )

        self.assertEqual([row.pk for row in response.context["tasks"]], [task.pk])
        self.assertIn("scope=%s" % self.gst.pk, response.context["pagination_query"])
        self.assertIn("scope=%s" % self.codec.pk, response.context["pagination_query"])

    def test_snapshot_preserves_all_current_scope_labels(self):
        task = create_task(
            title="Snapshot task",
            scopes=[self.codec, self.gst],
            link_url="https://tasks.example.test/snapshot",
        )

        snapshot = task_snapshot(task)

        self.assertEqual(snapshot["scope"], "GST / CODEC")
        self.assertEqual(
            [scope["name"] for scope in snapshot["scopes"]],
            ["GST", "CODEC"],
        )

    def test_edit_retains_an_archived_scope_already_attached_to_task(self):
        archived = Scope.objects.create(
            name="Retired Scope", color="#555555", position=4, is_active=False,
        )
        task = create_task(
            title="Keep archived scope",
            scopes=[self.gst, archived],
            link_url="https://tasks.example.test/keep-archived-scope",
            created_by=self.user,
        )

        get_response = self.client.get(reverse("task_edit", args=[task.pk]))
        self.assertIn(archived, get_response.context["form"].fields["scopes"].queryset)

        post_response = self.client.post(
            reverse("task_edit", args=[task.pk]),
            self.task_form_data(
                title=task.title,
                link_url=task.link_url,
                scopes=[self.gst.pk, archived.pk],
            ),
        )

        self.assertRedirects(post_response, reverse("tasks"))
        self.assertEqual(
            set(task.scopes.values_list("pk", flat=True)),
            {self.gst.pk, archived.pk},
        )

    def test_editing_scopes_records_one_structured_history_entry(self):
        task = create_task(
            title="Scope history task",
            scopes=[self.gst],
            link_url="https://tasks.example.test/scope-history",
            created_by=self.user,
        )

        response = self.client.post(
            reverse("task_edit", args=[task.pk]),
            self.task_form_data(
                title=task.title,
                link_url=task.link_url,
                scopes=[self.gst.pk, self.codec.pk],
            ),
        )

        self.assertRedirects(response, reverse("tasks"))
        history = TaskHistory.objects.get(task=task, event="Scopes changed")
        self.assertEqual(history.old_value, "GST")
        self.assertEqual(history.new_value, "GST, CODEC")

    def test_seeded_tasks_always_receive_a_scope(self):
        call_command("seed_test_tasks", count=4, seed=7)

        self.assertEqual(Task.objects.count(), 4)
        self.assertFalse(Task.objects.filter(scopes__isnull=True).exists())


class MeetingMultiScopeOrderTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            username="multi-order-host",
            password="test-password",
            display_name="Multi Order Host",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.alpha = Scope.objects.create(name="Alpha", position=1)
        self.beta = Scope.objects.create(name="Beta", position=2)
        self.zulu = Scope.objects.create(name="Zulu", position=3)
        beta_task = create_task(
            title="Alpha then Beta",
            scopes=[self.alpha, self.beta],
            link_url="https://tasks.example.test/alpha-beta",
        )
        zulu_task = create_task(
            title="Alpha then Zulu",
            scopes=[self.alpha, self.zulu],
            link_url="https://tasks.example.test/alpha-zulu",
        )
        self.meeting = Meeting.objects.create(
            title="Multi-scope order",
            meeting_date=timezone.localdate(),
            host=self.host,
            minute_taker=self.host,
            created_by=self.host,
        )
        self.zulu_entry = MeetingTask.objects.create(
            meeting=self.meeting, task=zulu_task, position=1,
        )
        self.beta_entry = MeetingTask.objects.create(
            meeting=self.meeting, task=beta_task, position=2,
        )
        self.client.force_login(self.host)

    def test_scope_sort_uses_the_complete_ordered_scope_set(self):
        response = self.client.post(
            reverse("meeting_order", args=[self.meeting.pk]),
            {"order_by": Meeting.TaskOrder.SCOPE},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["id"] for entry in response.json()["entries"]],
            [self.beta_entry.pk, self.zulu_entry.pk],
        )


class InactiveDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="inactive-admin",
            password="test-password",
            display_name="Inactive Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.gst = Scope.objects.create(name="GST", color="#4D36E9", position=1)
        self.codec = Scope.objects.create(name="CODEC", color="#BF7D2B", position=2)
        self.client.force_login(self.admin)

    @staticmethod
    def set_history_time(history, year):
        moment = timezone.make_aware(datetime(year, 6, 15, 12, 0, 0))
        TaskHistory.objects.filter(pk=history.pk).update(created_at=moment)
        return moment

    def inactive_task(self, *, title, status, year, scopes):
        task = create_task(
            title=title,
            scopes=scopes,
            status=status,
            assignee=self.admin,
            completed_by=self.admin if status == Task.Status.DONE else None,
            completed_at=timezone.now() if status == Task.Status.DONE else None,
            link_url=f"https://tasks.example.test/{title.lower().replace(' ', '-')}",
            created_by=self.admin,
        )
        history = TaskHistory.objects.create(
            task=task,
            actor=self.admin,
            event="Status changed",
            old_value="In Progress",
            new_value=task.get_status_display(),
        )
        self.set_history_time(history, year)
        return task

    def test_inactive_total_includes_cancelled_but_scope_chart_counts_only_done(self):
        self.inactive_task(
            title="Done in two scopes",
            status=Task.Status.DONE,
            year=2025,
            scopes=[self.gst, self.codec],
        )
        self.inactive_task(
            title="Cancelled in GST",
            status=Task.Status.CANCELLED,
            year=2025,
            scopes=[self.gst],
        )

        response = self.client.get(reverse("dashboard"), {"view": "team", "year": 2025})

        self.assertEqual(response.context["inactive_task_count"], 2)
        status_counts = {
            row["status"]: row["count"] for row in response.context["inactive_status_rows"]
        }
        self.assertEqual(status_counts, {Task.Status.DONE: 1, Task.Status.CANCELLED: 1})
        scope_counts = {
            row["name"]: row["count"] for row in response.context["scope_chart_rows"]
        }
        self.assertEqual(scope_counts, {"GST": 1, "CODEC": 1})

    def test_reopen_removes_the_old_inactive_year_and_reclose_moves_to_new_year(self):
        task = self.inactive_task(
            title="Reopened task",
            status=Task.Status.DONE,
            year=2025,
            scopes=[self.gst],
        )
        task.status = Task.Status.IN_PROGRESS
        task.completed_at = None
        task.completed_by = None
        task.save(update_fields=["status", "completed_at", "completed_by"])
        reopened = TaskHistory.objects.create(
            task=task,
            actor=self.admin,
            event="Status changed",
            old_value="Done",
            new_value="In Progress",
        )
        self.set_history_time(reopened, 2026)

        old_year = self.client.get(reverse("dashboard"), {"view": "team", "year": 2025})
        self.assertEqual(old_year.context["inactive_task_count"], 0)

        task.status = Task.Status.CANCELLED
        task.save(update_fields=["status"])
        reclosed = TaskHistory.objects.create(
            task=task,
            actor=self.admin,
            event="Status changed",
            old_value="In Progress",
            new_value="Cancelled",
        )
        self.set_history_time(reclosed, 2026)

        new_year = self.client.get(reverse("dashboard"), {"view": "team", "year": 2026})
        self.assertEqual(new_year.context["inactive_task_count"], 1)
        self.assertEqual(new_year.context["inactive_status_rows"][0]["status"], Task.Status.CANCELLED)

    def test_legacy_done_task_without_status_history_falls_back_to_completed_at(self):
        completed_at = timezone.make_aware(datetime(2024, 11, 3, 9, 30, 0))
        create_task(
            title="Legacy imported done task",
            scopes=[self.gst],
            status=Task.Status.DONE,
            assignee=self.admin,
            completed_by=self.admin,
            completed_at=completed_at,
            link_url="https://tasks.example.test/legacy-imported-done",
            created_by=self.admin,
        )

        response = self.client.get(reverse("dashboard"), {"view": "team", "year": 2024})

        self.assertEqual(response.context["inactive_task_count"], 1)

    def test_scope_reporting_uses_current_scopes_without_a_snapshot(self):
        task = self.inactive_task(
            title="Scope changed after done",
            status=Task.Status.DONE,
            year=2025,
            scopes=[self.gst],
        )
        task.scopes.set([self.codec])

        response = self.client.get(reverse("dashboard"), {"view": "team", "year": 2025})

        scope_counts = {
            row["name"]: row["count"] for row in response.context["scope_chart_rows"]
        }
        self.assertEqual(scope_counts, {"CODEC": 1})

    def test_inactive_assignee_remains_available_and_filterable_for_selected_year(self):
        resigned = User.objects.create_user(
            username="resigned-member",
            password="test-password",
            display_name="Resigned Member",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.INACTIVE,
        )
        task = self.inactive_task(
            title="Completed by resigned member",
            status=Task.Status.DONE,
            year=2025,
            scopes=[self.gst],
        )
        task.assignee = resigned
        task.completed_by = resigned
        task.save(update_fields=["assignee", "completed_by"])

        response = self.client.get(
            reverse("dashboard"),
            {"view": "team", "year": 2025, "member": resigned.pk},
        )

        self.assertEqual(response.context["selected_member"], resigned)
        self.assertIn(resigned, response.context["member_filter_options"])
        self.assertEqual(response.context["inactive_task_count"], 1)
        resigned_row = next(
            row for row in response.context["member_rows"] if row.pk == resigned.pk
        )
        self.assertEqual(resigned_row.inactive_count, 1)

    def test_active_status_ignores_inactive_year_query_parameter(self):
        active = create_task(
            title="Still active",
            scopes=[self.gst],
            status=Task.Status.IN_PROGRESS,
            assignee=self.admin,
            link_url="https://tasks.example.test/still-active",
            created_by=self.admin,
        )
        self.inactive_task(
            title="Inactive in another year",
            status=Task.Status.DONE,
            year=2024,
            scopes=[self.gst],
        )

        response = self.client.get(
            reverse("tasks"),
            {"status": "active", "inactive_year": 2025},
        )

        self.assertEqual([task.pk for task in response.context["tasks"]], [active.pk])


class TaskMultiScopeMigrationTests(SimpleTestCase):
    """Exercise the migration's data-copy contract without reversing a lossy M2M."""

    def test_forward_copy_batches_every_legacy_scope_link(self):
        migration = import_module("core.migrations.0017_task_multiple_scopes")
        rows = [(index, index + 10000) for index in range(2005)]

        class FakeRows:
            def iterator(self, *, chunk_size):
                self.chunk_size = chunk_size
                return iter(rows)

        class FakeTaskManager:
            def __init__(self):
                self.filtered_with = None
                self.rows = FakeRows()

            def filter(self, **kwargs):
                self.filtered_with = kwargs
                return self

            def values_list(self, *fields):
                self.fields = fields
                return self.rows

        class FakeThroughManager:
            def __init__(self):
                self.batches = []

            def bulk_create(self, batch, *, batch_size):
                self.batches.append((list(batch), batch_size))

        class FakeThrough:
            objects = FakeThroughManager()

            def __init__(self, *, task_id, scope_id):
                self.task_id = task_id
                self.scope_id = scope_id

        class FakeTask:
            objects = FakeTaskManager()
            scopes = type("Scopes", (), {"through": FakeThrough})

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                self.assertEqual((app_label, model_name), ("core", "Task"))
                return FakeTask

        migration.copy_scope_links(FakeApps(), schema_editor=None)

        self.assertEqual(FakeTask.objects.filtered_with, {"scope_id__isnull": False})
        self.assertEqual(FakeTask.objects.fields, ("pk", "scope_id"))
        self.assertEqual(FakeTask.objects.rows.chunk_size, 1000)
        self.assertEqual(
            [len(batch) for batch, batch_size in FakeThrough.objects.batches],
            [1000, 1000, 5],
        )
        self.assertTrue(all(size == 1000 for _, size in FakeThrough.objects.batches))
        copied_pairs = [
            (link.task_id, link.scope_id)
            for batch, _ in FakeThrough.objects.batches
            for link in batch
        ]
        self.assertEqual(copied_pairs, rows)

    def test_reverse_rejects_a_lossy_multi_scope_downgrade(self):
        migration = import_module("core.migrations.0017_task_multiple_scopes")

        class FakeQuery:
            def annotate(self, **kwargs):
                return self

            def exclude(self, **kwargs):
                return self

            @staticmethod
            def exists():
                return True

        class FakeTask:
            objects = FakeQuery()

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return FakeTask

        with self.assertRaises(IrreversibleError):
            migration.restore_single_scope(apps=FakeApps(), schema_editor=None)


class TaskMultiScopeMigrationIntegrationTests(TransactionTestCase):
    """The real schema migration preserves every legacy Task-to-Scope link."""

    migrate_from = ("core", "0016_meeting_task_order")
    migrate_to = ("core", "0017_task_multiple_scopes")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        LegacyScope = old_apps.get_model("core", "Scope")
        LegacyTask = old_apps.get_model("core", "Task")
        legacy_scope = LegacyScope.objects.create(
            name="Legacy Scope",
            color="#16835E",
        )
        self.task_id = LegacyTask.objects.create(
            title="Legacy task",
            scope=legacy_scope,
            link_url="https://tasks.example.test/legacy-scope",
        ).pk
        self.scope_id = legacy_scope.pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_legacy_scope_is_copied_to_scopes_relation(self):
        MigratedTask = self.apps.get_model("core", "Task")
        task = MigratedTask.objects.get(pk=self.task_id)

        self.assertEqual(
            list(task.scopes.values_list("pk", flat=True)),
            [self.scope_id],
        )
