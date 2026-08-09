"""Moving calendar windows, shared task forms, and presentation regressions."""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import TaskForm
from .models import AuditLog, Board, Scope, Task, TaskBoard, TimelineGroup, TimelineHoliday, TimelineMilestone, User


class CompactTimelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="compact-admin", account_status="active", role="admin")
        cls.member = User.objects.create_user(username="compact-member", account_status="active")
        cls.resigned = User.objects.create_user(username="compact-resigned", account_status="inactive")
        cls.scope = Scope.objects.create(name="Compact scope")
        cls.group = TimelineGroup.objects.create(name="Compact group")
        cls.today = timezone.localdate()
        cls.task = Task.objects.create(title="Compact task", timeline_start_date=cls.today, due_date=cls.today + timedelta(days=2), link_url="https://example.test/compact", timeline_group=cls.group, created_by=cls.admin)
        cls.task.scopes.add(cls.scope)

    def setUp(self):
        self.client.force_login(self.admin)

    def chart(self, **query):
        return self.client.get(reverse("task_timeline"), query)

    def rows(self, response):
        return [row for section in response.context["sections"] for row in section["rows"]]

    def payload(self, **changes):
        data = {"modal": "1", "revision": Task.objects.get(pk=self.task.pk).updated_at.isoformat(), "title": self.task.title, "description": "", "scopes": [self.scope.pk], "assignee": "", "parent_task": "", "related_tasks": [], "status": "todo", "priority": "medium", "timeline_start_date": self.today.isoformat(), "due_date": (self.today + timedelta(days=2)).isoformat(), "timeline_group": self.group.pk, "link_url": self.task.link_url}
        data.update(changes)
        return data

    def test_initial_window_is_bounded_and_controls_are_compact(self):
        response = self.chart()
        self.assertEqual(response.context["day_count"], 241)
        self.assertLessEqual(response.context["window_start"], self.today)
        self.assertGreaterEqual(response.context["window_end"], self.today)
        for obsolete in ("Timeline Settings", "Visible rows", "Past dates do not mean completed", "Click an empty day", 'name="year"'):
            self.assertNotContains(response, obsolete)
        for kind in ("task", "milestone", "group", "holiday"):
            self.assertContains(response, f'data-create-kind="{kind}"')
        self.assertContains(response, 'class="timeline-overviews"')

    def test_explicit_window_can_visit_past_and_future_without_year_filters(self):
        for start in (date(1900, 1, 1), date(2060, 12, 20)):
            with self.subTest(start=start):
                response = self.chart(start=start.isoformat(), end=(start + timedelta(days=20)).isoformat(), fragment="chart")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["window_start"], start)
                self.assertEqual(response.context["day_count"], 21)
                self.assertNotContains(response, "data-shared-task-form")
                self.assertContains(response, "chart-milestone-data")

    def test_invalid_and_oversized_ranges_return_400_before_rendering(self):
        for query in ({"start": "2026-01-01"}, {"start": "bad", "end": "2026-01-01"}, {"start": "2026-02-30", "end": "2026-03-01"}, {"start": "2026-02-01", "end": "2026-01-01"}, {"start": "0000-01-01", "end": "2026-01-01"}, {"start": "9999-01-01", "end": "10000-01-01"}, {"start": "2026-01-01", "end": "2030-01-01"}):
            with self.subTest(query=query):
                self.assertEqual(self.chart(**query).status_code, 400)

    def test_annual_holiday_at_calendar_extremes_does_not_overflow(self):
        TimelineHoliday.objects.create(name="Annual leap", start_date=date(2024, 2, 29), end_date=date(2024, 3, 2), repeat_annually=True)
        TimelineHoliday.objects.create(name="Annual year end", start_date=date(2025, 12, 31), end_date=date(2026, 1, 2), repeat_annually=True)
        for start, end in (("0001-02-27", "0001-03-03"), ("9999-12-29", "9999-12-31")):
            response = self.chart(start=start, end=end)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(any(day["is_holiday"] for day in response.context["days"]))

    def test_leap_day_and_month_segments_are_clipped_to_window(self):
        response = self.chart(start="2024-02-28", end="2024-03-02")
        self.assertEqual([day["date"] for day in response.context["days"]], [date(2024, 2, 28) + timedelta(days=index) for index in range(4)])
        self.assertEqual([(month["left"], month["span"]) for month in response.context["months"]], [(1, 2), (3, 2)])

    def test_rows_remain_stable_when_date_window_moves(self):
        first = self.chart()
        far = self.chart(start="2040-01-01", end="2040-02-01")
        self.assertEqual([row["task"].pk for row in self.rows(first)], [row["task"].pk for row in self.rows(far)])
        self.assertFalse(self.rows(far)[0]["in_range"])
        self.assertContains(far, "data-jump-task-date")

    def test_scheduled_and_unscheduled_cursors_are_independent(self):
        Task.objects.bulk_create([Task(title=f"Paging U {index}", link_url="https://example.test/u") for index in range(55)])
        scheduled = Task.objects.bulk_create([Task(title=f"Paging S {index}", timeline_start_date=self.today, due_date=self.today, link_url="https://example.test/s") for index in range(52)])
        Task.objects.filter(pk=scheduled[-1].pk).update(created_at=timezone.now() - timedelta(days=500))
        first = self.chart(q="Paging", page_size=50)
        self.assertEqual(len(self.rows(first)), 50)
        self.assertEqual(len(first.context["unscheduled_tasks"]), 50)
        second = self.chart(q="Paging", page_size=50, after=first.context["next_cursor"], fragment="chart")
        self.assertEqual([row["task"].pk for row in self.rows(first)] + [row["task"].pk for row in self.rows(second)], [task.pk for task in scheduled])
        remaining = self.chart(q="Paging", fragment="unscheduled", unscheduled_after=first.context["unscheduled_next"])
        self.assertEqual(len(remaining.context["unscheduled_tasks"]), 5)
        self.assertNotContains(remaining, "timeline-calendar")

    def test_newly_saved_focus_task_does_not_advance_the_row_cursor(self):
        scheduled = Task.objects.bulk_create([Task(title=f"Focus {index}", due_date=self.today, link_url="https://example.test/f") for index in range(105)])
        response = self.chart(q="Focus", page_size=50, focus_task=scheduled[-1].pk)
        self.assertEqual(response.context["next_cursor"], scheduled[49].pk)
        self.assertEqual(len(self.rows(response)), 51)
        self.assertIn(scheduled[-1].pk, [row["task"].pk for row in self.rows(response)])

    def test_private_metadata_absent_even_in_range_fragments_for_admin(self):
        secret = TimelineMilestone.objects.create(name="Never disclose private caption", date=self.today, visibility="private", created_by=self.member, task=self.task)
        response = self.chart(fragment="chart")
        self.assertNotContains(response, secret.name)
        self.assertNotIn(secret.pk, [item["id"] for item in response.context["milestones_data_json"]])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_task_milestone_not_duplicated_in_header_before_its_row_loads(self):
        tasks = Task.objects.bulk_create([Task(title=f"Later {index}", due_date=self.today, link_url="https://example.test/l") for index in range(51)])
        marker = TimelineMilestone.objects.create(name="Later task marker", date=self.today, task=tasks[-1], created_by=self.admin)
        first = self.chart(q="Later", page_size=50)
        self.assertNotIn(marker.pk, [item.pk for item in first.context["global_milestone_items"]])
        self.assertFalse(any(marker in section["milestone_items"] for section in first.context["sections"]))
        second = self.chart(q="Later", page_size=50, after=first.context["next_cursor"], fragment="chart")
        self.assertEqual([item.pk for row in self.rows(second) for item in row["milestones"]], [marker.pk])

    def test_shared_task_modal_has_real_task_pickers_and_prefilled_dates(self):
        response = self.client.get(reverse("task_add"), {"modal": "1", "timeline_start_date": "2027-01-02", "due_date": "2027-01-05", "timeline_group": self.group.pk})
        self.assertContains(response, "data-shared-task-form")
        self.assertContains(response, 'value="2027-01-02"')
        self.assertContains(response, "Devices (Optional)")
        self.assertContains(response, 'data-empty-label="Select devices"')
        self.assertContains(response, "data-single-task-picker")
        self.assertNotContains(response, 'class="helptext"')
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_modal_validation_and_revision_failures_never_save_partial_changes(self):
        response = self.client.post(reverse("task_edit", args=[self.task.pk]), self.payload(title=""))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "data-shared-task-form", status_code=400)
        response = self.client.post(reverse("task_edit", args=[self.task.pk]), self.payload(revision="2020-01-01T00:00:00Z", title="Stale name"))
        self.assertEqual(response.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Compact task")

    def test_existing_inactive_and_archived_choices_survive_shared_edit(self):
        parent = Task.objects.create(title="Archived parent", is_archived=True, link_url="https://example.test/parent")
        related = Task.objects.create(title="Archived related", is_archived=True, link_url="https://example.test/related")
        device = Board.objects.create(name="Legacy device", barcode="PRESERVE-001", is_archived=True)
        self.task.assignee = self.resigned
        self.task.parent_task = parent
        self.task.save()
        self.task.related_tasks.add(related)
        TaskBoard.objects.create(task=self.task, board=device, added_by=self.admin)
        form = TaskForm(instance=self.task)
        self.assertIn(self.resigned, form.fields["assignee"].queryset)
        self.assertIn(parent, form.fields["parent_task"].queryset)
        response = self.client.post(reverse("task_edit", args=[self.task.pk]), self.payload(assignee=self.resigned.pk, parent_task=parent.pk, related_tasks=[related.pk], boards=[device.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee_id, self.resigned.pk)
        self.assertEqual(self.task.parent_task_id, parent.pk)
        self.assertTrue(self.task.related_tasks.filter(pk=related.pk).exists())
        self.assertTrue(TaskBoard.objects.filter(task=self.task, board=device, released_at__isnull=True).exists())

    def test_modal_description_and_group_changes_are_recorded(self):
        other_group = TimelineGroup.objects.create(name="Other compact group")
        response = self.client.post(reverse("task_edit", args=[self.task.pk]), self.payload(description="Updated description", timeline_group=other_group.pk))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.task.history.filter(event="Description changed", new_value="Updated description").exists())
        self.assertTrue(self.task.history.filter(event="Timeline Group changed", new_value=other_group.name).exists())

    def test_context_managers_keep_admin_permission_and_revisions(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(reverse("timeline_group_add"), {"name": "Forbidden"}, HTTP_ACCEPT="application/json").status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("timeline_group_edit", args=[self.group.pk]), {"name": "Stale", "color": self.group.color}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 428)
        self.assertEqual(self.client.post(reverse("timeline_holiday_add"), {"name": "Invalid dates", "start_date": "2027-01-02", "end_date": "2027-01-01"}, HTTP_ACCEPT="application/json").status_code, 400)

    def test_group_delete_bumps_revisions_and_records_task_history_without_private_metadata(self):
        marker = TimelineMilestone.objects.create(name="Private group marker", date=self.today, timeline_group=self.group, created_by=self.member, visibility="private")
        old_task_revision, old_marker_revision = self.task.updated_at, marker.updated_at
        response = self.client.post(reverse("timeline_group_delete", args=[self.group.pk]), {"revision": self.group.updated_at.isoformat()}, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db(); marker.refresh_from_db()
        self.assertIsNone(self.task.timeline_group_id)
        self.assertGreater(self.task.updated_at, old_task_revision)
        self.assertGreater(marker.updated_at, old_marker_revision)
        self.assertTrue(self.task.history.filter(event="Timeline Group removed").exists())
        self.assertFalse(AuditLog.objects.filter(entity_type="TimelineGroup", details__icontains=marker.name).exists())

    def test_device_rename_preserves_bindings_urls_and_storage(self):
        device = Board.objects.create(name="Hardware A", barcode="HW-123", created_by=self.admin)
        response = self.client.get(reverse("boards"))
        self.assertContains(response, "Devices")
        self.assertContains(response, 'title="Hardware A"')
        self.assertContains(response, 'title="HW-123"')
        self.assertContains(response, "Delete Hardware A (HW-123) permanently?")
        self.assertEqual(Board._meta.db_table, "core_board")
        self.assertEqual(reverse("board_edit", args=[device.pk]), f"/boards/{device.pk}/edit/")
