"""Authoritative lightweight geometry for retained/virtualized Timeline views.

HTML remains paged; geometry and permission-filtered milestone metadata must not
depend on which HTML page or internal calendar window happened to be requested.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Scope, Task, TimelineGroup, TimelineMilestone, User


class RetainedTimelineMetadataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="retained-owner", display_name="Retained Owner", account_status="active",
        )
        cls.other = User.objects.create_user(
            username="retained-other", display_name="Retained Other", account_status="active", role="admin",
        )
        cls.scope_a = Scope.objects.create(name="Retained A")
        cls.scope_b = Scope.objects.create(name="Retained B")
        cls.group_a = TimelineGroup.objects.create(name="Retained Group A")
        cls.group_b = TimelineGroup.objects.create(name="Retained Group B")
        cls.today = timezone.localdate()
        statuses = [Task.Status.TODO, Task.Status.IN_PROGRESS, Task.Status.DONE, Task.Status.PAUSED]
        cls.tasks = Task.objects.bulk_create([
            Task(
                title=f"Geometry item {index:03d}",
                description="descriptionneedle" if index == 0 else "",
                timeline_start_date=cls.today + timedelta(days=index * 9),
                due_date=cls.today + timedelta(days=index * 9 + 2),
                timeline_group=cls.group_a if index < 53 else cls.group_b,
                assignee=cls.owner if index % 2 == 0 else cls.other,
                status=statuses[index % len(statuses)],
                link_url="https://example.test/retained-geometry", created_by=cls.owner,
            ) for index in range(106)
        ])
        Task.scopes.through.objects.bulk_create([
            Task.scopes.through(task_id=task.pk, scope_id=cls.scope_a.pk if index % 2 == 0 else cls.scope_b.pk)
            for index, task in enumerate(cls.tasks)
        ])
        cls.tasks[0].scopes.add(cls.scope_b)
        cls.archived_parent = Task.objects.create(
            title="ARCHIVED_PARENT_NOT_IN_GEOMETRY", is_archived=True,
            timeline_start_date=cls.today, due_date=cls.today, link_url="https://example.test/archived",
        )
        Task.objects.filter(pk=cls.tasks[-1].pk).update(parent_task=cls.archived_parent)
        cls.unscheduled = Task.objects.create(
            title="Geometry unscheduled", link_url="https://example.test/unscheduled",
        )
        cls.shared = TimelineMilestone.objects.create(
            name="Visible late-page milestone", date=cls.today + timedelta(days=950),
            task=cls.tasks[-1], created_by=cls.owner,
        )
        cls.private = TimelineMilestone.objects.create(
            name="PRIVATE_GEOMETRY_SECRET", date=cls.today - timedelta(days=800),
            task=cls.tasks[0], created_by=cls.owner, visibility="private", is_important=True,
        )
        cls.archived_marker = TimelineMilestone.objects.create(
            name="ARCHIVED_TASK_MARKER", date=cls.today, task=cls.archived_parent, created_by=cls.owner,
        )

    def setUp(self):
        self.client.force_login(self.other)

    def chart(self, **query):
        response = self.client.get(reverse("task_timeline"), query)
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response

    def metadata(self, response):
        return response.context["tasks_data_json"]

    def html_ids(self, response):
        return [row["task"].pk for section in response.context["sections"] for row in section["rows"]]

    def test_full_geometry_is_independent_of_html_page_and_internal_date_window(self):
        first = self.chart(page_size=50)
        self.assertEqual(len(self.html_ids(first)), 50)
        expected_ids = [task.pk for task in self.tasks]
        self.assertEqual([item["id"] for item in self.metadata(first)], expected_ids)
        self.assertEqual(first.context["task_count"], len(expected_ids))
        self.assertNotIn(self.tasks[-1].pk, self.html_ids(first))
        self.assertIn(self.shared.pk, [item["id"] for item in first.context["milestones_data_json"]])
        second = self.chart(page_size=50, after=first.context["next_cursor"], fragment="chart")
        self.assertEqual(len(self.html_ids(second)), 50)
        self.assertEqual(self.metadata(second), self.metadata(first))
        far = self.chart(page_size=50, fragment="chart", start="2040-01-01", end="2040-01-31")
        self.assertEqual(self.metadata(far), self.metadata(first))
        self.assertEqual(far.context["day_count"], 31)
        self.assertContains(second, 'id="chart-task-data"')
        self.assertContains(first, 'id="timeline-task-data"')

    def test_geometry_applies_the_same_combined_filters_without_duplicate_tasks(self):
        query = {
            "q": "Geometry item", "scope": [self.scope_a.pk, self.scope_b.pk],
            "assignee": self.owner.pk, "group": self.group_a.pk, "status": Task.Status.TODO,
        }
        response = self.chart(**query)
        expected = list(Task.objects.filter(
            title__icontains=query["q"], is_archived=False, scopes__pk__in=query["scope"],
            assignee=self.owner, timeline_group=self.group_a, status=Task.Status.TODO,
        ).distinct().order_by("pk").values_list("pk", flat=True))
        ids = [item["id"] for item in self.metadata(response)]
        self.assertTrue(ids)
        self.assertEqual(ids, expected)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), response.context["task_count"])
        description = self.chart(q="descriptionneedle")
        self.assertEqual([item["id"] for item in self.metadata(description)], [self.tasks[0].pk])

    def test_archived_and_unscheduled_tasks_do_not_add_geometry_or_restore_archived_parent(self):
        response = self.chart()
        data = {item["id"]: item for item in self.metadata(response)}
        self.assertNotIn(self.archived_parent.pk, data)
        self.assertNotIn(self.unscheduled.pk, data)
        self.assertEqual(data[self.tasks[-1].pk]["parent"], self.archived_parent.pk)
        self.assertNotIn(self.archived_parent.title, str(list(data.values())))
        markers = response.context["milestones_data_json"]
        self.assertNotIn(self.archived_marker.pk, [item["id"] for item in markers])
        filtered = self.chart(q="Geometry item 105", group=self.group_b.pk)
        self.assertEqual([item["id"] for item in self.metadata(filtered)], [self.tasks[-1].pk])
        self.assertEqual(self.metadata(filtered)[0]["parent"], self.archived_parent.pk)

    def test_private_markers_do_not_affect_admin_geometry_counts_window_or_owner_fields(self):
        with_private = self.chart(page_size=50)
        markers = with_private.context["milestones_data_json"]
        self.assertNotIn(self.private.pk, [item["id"] for item in markers])
        self.assertNotContains(with_private, self.private.name)
        before = self.metadata(with_private)
        window = (with_private.context["window_start"], with_private.context["window_end"])
        self.private.delete()
        without_private = self.chart(page_size=50)
        self.assertEqual(self.metadata(without_private), before)
        self.assertEqual((without_private.context["window_start"], without_private.context["window_end"]), window)
        self.assertEqual(without_private.context["task_count"], with_private.context["task_count"])
        for item in before:
            self.assertEqual(set(item), {"id", "title", "start", "due", "group", "parent", "revision", "status", "position", "order_revision", "order_parent"})

    def test_geometry_schedule_and_revision_are_refreshed_together_after_task_mutation(self):
        task = Task.objects.get(pk=self.tasks[0].pk)
        before = next(item for item in self.metadata(self.chart()) if item["id"] == task.pk)
        response = self.client.post(reverse("timeline_task_update", args=[task.pk]), {
            "timeline_start_date": self.today + timedelta(days=600),
            "due_date": self.today + timedelta(days=604), "timeline_group": self.group_b.pk,
            "revision": task.updated_at.isoformat(),
        }, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        task.refresh_from_db()
        after = next(item for item in self.metadata(self.chart(fragment="chart")) if item["id"] == task.pk)
        self.assertNotEqual(after["revision"], before["revision"])
        self.assertEqual(after["revision"], response.json()["task"]["revision"])
        self.assertEqual(after["revision"], task.updated_at.isoformat())
        self.assertEqual((after["start"], after["due"], after["group"]), (
            task.timeline_start_date.isoformat(), task.due_date.isoformat(), task.timeline_group_id,
        ))
        self.assertEqual((after["title"], after["status"], after["parent"]), (task.title, task.status, ""))
