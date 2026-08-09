from datetime import timedelta, timezone as datetime_timezone

from django.contrib import admin
from django.db import transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Meeting, MeetingTask, Scope, Task, TimelineGroup, TimelineHoliday, TimelineMilestone, User


class InteractiveTimelineTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", display_name="Owner", account_status="active")
        self.other = User.objects.create_user(username="other", display_name="Other", account_status="active")
        self.admin = User.objects.create_user(username="manager", display_name="Manager", account_status="active", role="admin")
        self.superuser = User.objects.create_superuser(username="super", email="super@example.test", password="testing-password", account_status="active")
        self.day = timezone.localdate() + timedelta(days=3)
        self.scope = Scope.objects.create(name="Example scope")
        self.group = TimelineGroup.objects.create(name="Milestone Group")
        self.task = Task.objects.create(title="Drag task", link_url="https://example.test/task", created_by=self.owner,
                                        timeline_start_date=self.day, due_date=self.day + timedelta(days=2))
        self.task.scopes.add(self.scope)
        self.client.force_login(self.owner)

    def post(self, name, payload, pk=None, user=None):
        if user:
            self.client.force_login(user)
        return self.client.post(reverse(name, args=[pk] if pk else []), payload, HTTP_ACCEPT="application/json")

    def milestone(self, **overrides):
        values = dict(name="SECRET milestone", date=self.day, visibility="private", created_by=self.owner)
        values.update(overrides)
        return TimelineMilestone.objects.create(**values)

    def test_member_can_create_shared_and_private_important_milestones(self):
        response = self.post("timeline_milestone_add", {"name": "Shared launch", "date": self.day, "color": "#3B82F6"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(TimelineMilestone.objects.get(pk=response.json()["milestone"]["id"]).visibility, "shared")
        response = self.post("timeline_milestone_add", {"name": "Private launch", "date": self.day, "visibility": "private", "is_important": "on"})
        self.assertEqual(response.status_code, 201, response.content)
        item = TimelineMilestone.objects.get(pk=response.json()["milestone"]["id"])
        self.assertTrue(item.is_important)
        self.assertEqual(item.created_by, self.owner)
        self.assertTrue(AuditLog.objects.get(entity_type="TimelineMilestone", entity_id=item.pk).is_private)

    def test_private_milestones_hidden_from_members_admins_and_superusers(self):
        baseline = self.client.get(reverse("task_timeline"))
        baseline_window = (baseline.context["window_start"], baseline.context["window_end"])
        baseline_groups = [section["id"] for section in baseline.context["sections"]]
        item = self.milestone(date=self.day.replace(year=self.day.year + 5), timeline_group=self.group)
        for user in (self.other, self.admin, self.superuser):
            with self.subTest(user=user):
                self.client.force_login(user)
                response = self.client.get(reverse("task_timeline"))
                self.assertNotContains(response, item.name)
                self.assertEqual((response.context["window_start"], response.context["window_end"]), baseline_window)
                self.assertEqual([section["id"] for section in response.context["sections"]], baseline_groups)
                self.assertNotIn(item.pk, [entry["id"] for entry in response.context["milestones_data_json"]])
                for endpoint in ("timeline_milestone_edit", "timeline_milestone_delete"):
                    self.assertEqual(self.post(endpoint, {"date": self.day, "revision": item.updated_at.isoformat()}, item.pk).status_code, 404)
                self.assertEqual(self.client.get(reverse("timeline_milestone_history", args=[item.pk])).status_code, 404)

    def test_private_task_milestone_does_not_create_extra_lane_for_others(self):
        self.milestone(task=self.task)
        self.client.force_login(self.other)
        response = self.client.get(reverse("task_timeline"))
        row = next(row for section in response.context["sections"] for row in section["rows"] if row["task"].pk == self.task.pk)
        self.assertEqual(row["milestones"], [])
        self.assertEqual(response.context["milestones_data_json"], [])

    def test_owner_can_move_milestone_with_offset_revision_and_keep_other_fields(self):
        item = self.milestone(is_important=True, notes="Owner notes", color="#8B5CF6", task=self.task)
        item.scopes.add(self.scope)
        revision = item.updated_at.astimezone(datetime_timezone(timedelta(hours=7))).isoformat()
        response = self.post("timeline_milestone_edit", {"date": self.day + timedelta(days=1), "revision": revision, "date_only": "1"}, item.pk)
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertTrue(item.is_important)
        self.assertEqual(item.notes, "Owner notes")
        self.assertEqual(item.task_id, self.task.pk)
        self.assertEqual(list(item.scopes.all()), [self.scope])

    def test_stale_and_missing_milestone_revisions_rejected(self):
        item = self.milestone()
        self.assertEqual(self.post("timeline_milestone_edit", {"date": self.day}, item.pk).status_code, 428)
        self.assertEqual(self.post("timeline_milestone_delete", {"revision": self.task.updated_at.isoformat()}, item.pk).status_code, 409)
        self.assertTrue(TimelineMilestone.objects.filter(pk=item.pk).exists())

    def test_shared_to_private_hides_previous_audit_and_private_history_stays_private_when_shared(self):
        response = self.post("timeline_milestone_add", {"name": "Shared first", "date": self.day})
        item = TimelineMilestone.objects.get(pk=response.json()["milestone"]["id"])
        self.post("timeline_milestone_edit", {"visibility": "private", "revision": item.updated_at.isoformat()}, item.pk)
        self.assertFalse(AuditLog.objects.filter(entity_type="TimelineMilestone", entity_id=item.pk, is_private=False).exists())
        item.refresh_from_db()
        self.post("timeline_milestone_edit", {"visibility": "shared", "revision": item.updated_at.isoformat()}, item.pk)
        self.client.force_login(self.other)
        history = self.client.get(reverse("timeline_milestone_history", args=[item.pk])).json()["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["details"]["visibility"], "shared")
        request = RequestFactory().get("/")
        request.user = self.superuser
        visible_logs = admin.site._registry[AuditLog].get_queryset(request)
        self.assertFalse(visible_logs.filter(entity_id=item.pk, entity_type="TimelineMilestone", is_private=True).exists())

    def test_deleted_private_milestone_audit_denied_even_via_django_admin(self):
        response = self.post("timeline_milestone_add", {"name": "Private delete", "date": self.day, "visibility": "private"})
        item = TimelineMilestone.objects.get(pk=response.json()["milestone"]["id"])
        self.post("timeline_milestone_delete", {"revision": item.updated_at.isoformat()}, item.pk)
        log = AuditLog.objects.filter(entity_type="TimelineMilestone", entity_id=item.pk).first()
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("admin:core_auditlog_change", args=[log.pk])).status_code, 302)
        request = RequestFactory().get("/")
        request.user = self.superuser
        self.assertFalse(admin.site._registry[AuditLog].get_queryset(request).filter(pk=log.pk).exists())

    def test_deleting_owner_does_not_make_private_logs_or_milestones_public(self):
        response = self.post("timeline_milestone_add", {"name": "Owner deleted secret", "date": self.day, "visibility": "private"})
        pk = response.json()["milestone"]["id"]
        self.owner.delete()
        self.assertFalse(TimelineMilestone.objects.visible_to(self.superuser).filter(pk=pk).exists())
        self.assertTrue(AuditLog.objects.filter(entity_type="TimelineMilestone", entity_id=pk, is_private=True, private_owner=None).exists())

    def test_admin_cannot_privatize_someone_elses_shared_milestone(self):
        item = self.milestone(visibility="shared")
        response = self.post("timeline_milestone_edit", {"visibility": "private", "revision": item.updated_at.isoformat()}, item.pk, self.admin)
        self.assertEqual(response.status_code, 403)

    def test_palette_rejects_custom_but_allows_unchanged_legacy(self):
        response = self.post("timeline_milestone_add", {"name": "Not allowed", "date": self.day, "color": "#123456"})
        self.assertEqual(response.status_code, 400)
        item = self.milestone(color="#123456")
        response = self.post("timeline_milestone_edit", {"revision": item.updated_at.isoformat(), "date": self.day, "date_only": "1"}, item.pk)
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.color, "#123456")

    def test_group_and_holiday_palettes_enforced_and_legacy_colors_preserved(self):
        self.client.force_login(self.admin)
        for kind, model, extra in (
            ("group", TimelineGroup, {}),
            ("holiday", TimelineHoliday, {"start_date": self.day, "end_date": self.day}),
        ):
            with self.subTest(kind=kind):
                payload = {"name": f"Preset {kind}", "color": "#123456", **extra}
                response = self.post(f"timeline_{kind}_add", payload)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(model.objects.filter(name=payload["name"]).exists())
                payload["color"] = "#14b8a6"
                response = self.post(f"timeline_{kind}_add", payload)
                self.assertEqual(response.status_code, 200, response.content)
                item = model.objects.get(name=payload["name"])
                self.assertEqual(item.color, "#14B8A6")
                item.color = "#123456"
                item.save()
                payload.update(color="#123456", revision=item.updated_at.isoformat())
                response = self.post(f"timeline_{kind}_edit", payload, item.pk)
                self.assertEqual(response.status_code, 200, response.content)
                item.refresh_from_db()
                self.assertEqual(item.color, "#123456")
                payload.update(color="#654321", revision=item.updated_at.isoformat())
                self.assertEqual(self.post(f"timeline_{kind}_edit", payload, item.pk).status_code, 400)
                item.refresh_from_db()
                self.assertEqual(item.color, "#123456")

    def test_vertical_group_transfer_and_undo_keep_dates_and_milestone_date(self):
        item = self.milestone(task=self.task, visibility="shared")
        start, due = self.task.timeline_start_date, self.task.due_date
        for group_id in (self.group.pk, ""):
            self.task.refresh_from_db()
            response = self.post("timeline_task_update", {
                "timeline_start_date": start, "due_date": due, "timeline_group": group_id,
                "revision": self.task.updated_at.isoformat(),
            }, self.task.pk)
            self.assertEqual(response.status_code, 200, response.content)
            self.task.refresh_from_db()
            self.assertEqual(self.task.timeline_group_id, group_id or None)
            self.assertEqual((self.task.timeline_start_date, self.task.due_date), (start, due))
            item.refresh_from_db()
            self.assertEqual(item.date, self.day)
            page = self.client.get(reverse("task_timeline"))
            marker = next(value for value in page.context["milestones_data_json"] if value["id"] == item.pk)
            self.assertEqual(marker["task_group"], group_id or None)
        history = self.task.history.filter(event="Timeline schedule updated")
        self.assertEqual(history.count(), 2)
        self.assertTrue(history.filter(new_value__contains="Milestone Group").exists())
        self.assertTrue(history.filter(new_value__contains="Ungrouped").exists())

    def test_admin_color_controls_are_eight_presets_without_freeform_picker(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("task_timeline"))
        self.assertEqual(response.content.count(b"data-admin-color="), 16)
        self.assertNotContains(response, 'type="color"')

    def test_unscheduled_task_milestone_is_visible_without_an_empty_task_row(self):
        self.task.timeline_start_date = None
        self.task.due_date = None
        self.task.save()
        item = self.milestone(task=self.task)
        response = self.client.get(reverse("task_timeline"))
        self.assertFalse(any(row["task"].pk == self.task.pk for section in response.context["sections"] for row in section["rows"]))
        self.assertIn(self.task, response.context["unscheduled_tasks"])
        self.assertEqual([marker.pk for marker in response.context["global_milestone_items"]], [item.pk])

    def test_schedule_drag_records_history_and_stale_undo_is_rejected(self):
        original_revision = self.task.updated_at.isoformat()
        payload = {"timeline_start_date": self.day + timedelta(days=2), "due_date": self.day + timedelta(days=4), "revision": original_revision}
        response = self.post("timeline_task_update", payload, self.task.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.task.history.filter(event="Timeline schedule updated").count(), 1)
        self.assertEqual(self.post("timeline_task_update", payload, self.task.pk).status_code, 409)
        payload.update(timeline_start_date=self.day, due_date=self.day + timedelta(days=2), revision=response.json()["task"]["revision"])
        self.assertEqual(self.post("timeline_task_update", payload, self.task.pk).status_code, 200)

    def test_missing_or_invalid_dates_do_not_change_task(self):
        self.assertEqual(self.post("timeline_task_update", {"due_date": self.day}, self.task.pk).status_code, 428)
        response = self.post("timeline_task_update", {"timeline_start_date": self.day + timedelta(days=2), "due_date": self.day, "revision": self.task.updated_at.isoformat()}, self.task.pk)
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.timeline_start_date, self.day)

    def test_quick_create_reuses_normal_task_validation(self):
        payload = {"title": "Quick task", "timeline_start_date": self.day, "due_date": self.day + timedelta(days=1), "status": "todo", "priority": "medium", "scopes": [self.scope.pk]}
        self.assertEqual(self.post("timeline_task_create", payload).status_code, 400)  # Link remains required.
        payload["link_url"] = "https://example.test/quick"
        payload["timeline_group"] = "not-a-number"
        self.assertEqual(self.post("timeline_task_create", payload).status_code, 400)
        payload["timeline_group"] = self.group.pk
        response = self.post("timeline_task_create", payload)
        self.assertEqual(response.status_code, 201, response.content)
        task = Task.objects.get(pk=response.json()["task"]["id"])
        self.assertEqual(task.timeline_group, self.group)
        self.assertTrue(task.history.filter(event="Task created").exists())

    def test_timeline_is_separate_sidebar_entry(self):
        response = self.client.get(reverse("task_timeline"))
        self.assertContains(response, f'<a href="{reverse("task_timeline")}" class="active">Timeline</a>', html=True)
        self.assertNotContains(self.client.get(reverse("tasks")), "timeline-entry-button")

    def test_stale_normal_task_edit_cannot_overwrite_a_timeline_drag(self):
        stale_revision = self.task.updated_at.isoformat()
        self.post("timeline_task_update", {"revision": stale_revision, "timeline_start_date": self.day + timedelta(days=2), "due_date": self.day + timedelta(days=4)}, self.task.pk)
        response = self.client.post(reverse("task_edit", args=[self.task.pk]), {
            "revision": stale_revision, "title": "Old editor", "scopes": [self.scope.pk],
            "timeline_start_date": self.day, "due_date": self.day + timedelta(days=2),
            "status": "todo", "priority": "medium", "link_url": self.task.link_url,
        })
        self.assertEqual(response.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual(self.task.timeline_start_date, self.day + timedelta(days=2))

    def test_past_all_manager_keeps_visible_milestones_outside_chart(self):
        item = self.milestone(date=self.day.replace(year=self.day.year - 3))
        response = self.client.get(reverse("task_timeline"))
        self.assertIn(item.pk, [entry["id"] for entry in response.context["milestones_data_json"]])
        self.assertNotIn(item.pk, [entry.pk for entry in response.context["calendar_milestones"]])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_invalid_revision_values_return_400_instead_of_server_error(self):
        response = self.post("timeline_task_update", {"revision": "2026-99-99T00:00:00+00:00"}, self.task.pk)
        self.assertEqual(response.status_code, 400)

    def test_draft_notification_is_after_commit_and_finalized_snapshot_stays_unchanged(self):
        draft = Meeting.objects.create(title="Live MM", meeting_date=self.day, host=self.owner, minute_taker=self.owner, created_by=self.owner)
        frozen = Meeting.objects.create(title="Frozen MM", meeting_date=self.day, host=self.owner, minute_taker=self.owner, created_by=self.owner, status="finalized", finalized_at=timezone.now())
        live_entry = MeetingTask.objects.create(meeting=draft, task=self.task, position=1)
        frozen_entry = MeetingTask.objects.create(meeting=frozen, task=self.task, position=1, snapshot={"frozen": "unchanged"})
        initial_live_time, initial_frozen_time = live_entry.updated_at, frozen_entry.updated_at
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.post("timeline_task_update", {"revision": self.task.updated_at.isoformat(), "timeline_start_date": self.day + timedelta(days=1), "due_date": self.day + timedelta(days=3)}, self.task.pk)
            self.assertEqual(response.status_code, 200)
            live_entry.refresh_from_db()
            self.assertEqual(live_entry.updated_at, initial_live_time)
        self.assertEqual(len(callbacks), 1)
        live_entry.refresh_from_db()
        frozen_entry.refresh_from_db()
        self.assertGreater(live_entry.updated_at, initial_live_time)
        self.assertEqual(frozen_entry.updated_at, initial_frozen_time)
        self.assertEqual(frozen_entry.snapshot, {"frozen": "unchanged"})

    def test_rolled_back_schedule_does_not_send_draft_notifications(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.post("timeline_task_update", {"revision": self.task.updated_at.isoformat(), "timeline_start_date": self.day + timedelta(days=1), "due_date": self.day + timedelta(days=3)}, self.task.pk)
                    raise RuntimeError("Rollback test transaction")
        self.assertEqual(callbacks, [])
        self.task.refresh_from_db()
        self.assertEqual(self.task.timeline_start_date, self.day)

    def test_ungrouped_filter_does_not_include_other_groups_milestone_lanes(self):
        self.milestone(name="Grouped marker", visibility="shared", timeline_group=self.group)
        self.milestone(name="Ungrouped task marker", visibility="shared", task=self.task)
        response = self.client.get(reverse("task_timeline"), {"group": "ungrouped"})
        self.assertNotIn("Grouped marker", [item.name for item in response.context["calendar_milestones"]])
        self.assertIn("Ungrouped task marker", [item.name for item in response.context["calendar_milestones"]])
