from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditLog, Scope, Task, TimelineGroup, TimelineMilestone, User


class TimelineMilestoneTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="timeline-admin",
            password="test-password",
            display_name="Timeline Admin",
            role=User.Role.ADMIN,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.member = User.objects.create_user(
            username="timeline-member",
            password="test-password",
            display_name="Timeline Member",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.ACTIVE,
        )
        self.scope_a = Scope.objects.create(name="Scope A", color="#123456")
        self.scope_b = Scope.objects.create(name="Scope B", color="#654321")
        self.group_a = TimelineGroup.objects.create(name="Group A", created_by=self.admin)
        self.group_b = TimelineGroup.objects.create(name="Group B", created_by=self.admin)
        self.day = timezone.localdate() + timedelta(days=7)

    def create_milestone(self, name, *, group=None, scopes=(), color="#7C3AED"):
        milestone = TimelineMilestone.objects.create(
            name=name,
            date=self.day,
            color=color,
            timeline_group=group,
            created_by=self.admin,
        )
        milestone.scopes.set(scopes)
        return milestone

    def test_admin_can_create_edit_and_delete_milestone_with_audit(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("timeline_milestone_add"), {
            "name": "Prototype ready",
            "date": self.day.isoformat(),
            "color": "#AABBCC",
            "timeline_group": self.group_a.pk,
            "scopes": [self.scope_a.pk, self.scope_b.pk],
            "notes": "Gate review",
        })
        self.assertRedirects(response, reverse("task_timeline"))
        milestone = TimelineMilestone.objects.get(name="Prototype ready")
        self.assertEqual(milestone.created_by, self.admin)
        self.assertEqual(milestone.timeline_group, self.group_a)
        self.assertEqual(set(milestone.scopes.values_list("pk", flat=True)), {self.scope_a.pk, self.scope_b.pk})
        self.assertTrue(AuditLog.objects.filter(
            entity_type="TimelineMilestone", entity_id=milestone.pk,
            action="Timeline milestone added",
        ).exists())

        response = self.client.post(reverse("timeline_milestone_edit", args=[milestone.pk]), {
            "name": "Prototype approved",
            "date": (self.day + timedelta(days=1)).isoformat(),
            "color": "#112233",
            "timeline_group": "",
            "scopes": [self.scope_b.pk],
            "notes": "Approved",
        })
        self.assertRedirects(response, reverse("task_timeline"))
        milestone.refresh_from_db()
        self.assertEqual(milestone.name, "Prototype approved")
        self.assertIsNone(milestone.timeline_group)
        self.assertEqual(list(milestone.scopes.values_list("pk", flat=True)), [self.scope_b.pk])
        self.assertTrue(AuditLog.objects.filter(
            entity_type="TimelineMilestone", entity_id=milestone.pk,
            action="Timeline milestone updated",
        ).exists())

        milestone_pk = milestone.pk
        response = self.client.post(reverse("timeline_milestone_delete", args=[milestone.pk]))
        self.assertRedirects(response, reverse("task_timeline"))
        self.assertFalse(TimelineMilestone.objects.filter(pk=milestone_pk).exists())
        self.assertTrue(AuditLog.objects.filter(
            entity_type="TimelineMilestone", entity_id=milestone_pk,
            action="Timeline milestone deleted",
        ).exists())

    def test_milestone_writes_are_admin_only_post_endpoints(self):
        milestone = self.create_milestone("Protected")
        self.client.force_login(self.member)
        endpoints = [
            (reverse("timeline_milestone_add"), {
                "name": "Denied", "date": self.day.isoformat(), "color": "#112233",
            }),
            (reverse("timeline_milestone_edit", args=[milestone.pk]), {
                "name": "Denied edit", "date": self.day.isoformat(), "color": "#112233",
            }),
            (reverse("timeline_milestone_delete", args=[milestone.pk]), {}),
        ]
        for url, payload in endpoints:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, payload).status_code, 403)

        self.client.force_login(self.admin)
        self.assertRedirects(
            self.client.get(reverse("timeline_milestone_edit", args=[milestone.pk])),
            reverse("task_timeline"),
        )
        milestone.refresh_from_db()
        self.assertEqual(milestone.name, "Protected")

    def test_invalid_color_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("timeline_milestone_add"), {
            "name": "Invalid color",
            "date": self.day.isoformat(),
            "color": "purple",
        })
        self.assertRedirects(response, reverse("task_timeline"))
        self.assertFalse(TimelineMilestone.objects.filter(name="Invalid color").exists())

    def test_scope_filter_includes_global_and_matching_milestones(self):
        self.create_milestone("Global")
        self.create_milestone("Scope A milestone", scopes=[self.scope_a])
        self.create_milestone("Scope B milestone", scopes=[self.scope_b])
        self.client.force_login(self.member)

        response = self.client.get(reverse("task_timeline"), {"scope": self.scope_a.pk})
        day = next(day for day in response.context["days"] if day["date"] == self.day)
        self.assertEqual(day["milestone"]["count"], 2)
        self.assertIn("Global", day["milestone"]["tooltip"])
        self.assertIn("Scope A milestone", day["milestone"]["tooltip"])
        self.assertNotIn("Scope B milestone", day["milestone"]["tooltip"])
        self.assertEqual(len(response.context["global_milestone_lines"]), 1)

    def test_group_filter_includes_global_and_matching_group_line(self):
        self.create_milestone("Global")
        self.create_milestone("Group A milestone", group=self.group_a)
        self.create_milestone("Group B milestone", group=self.group_b)
        self.client.force_login(self.member)

        response = self.client.get(reverse("task_timeline"), {"group": self.group_a.pk})
        day = next(day for day in response.context["days"] if day["date"] == self.day)
        self.assertEqual(day["milestone"]["count"], 2)
        self.assertIn("Global", day["milestone"]["tooltip"])
        self.assertIn("Group A milestone", day["milestone"]["tooltip"])
        self.assertNotIn("Group B milestone", day["milestone"]["tooltip"])
        sections = response.context["sections"]
        self.assertEqual([section["name"] for section in sections], ["Group A"])
        self.assertEqual(len(sections[0]["milestone_lines"]), 1)

    def test_same_date_milestones_are_aggregated_and_settings_menu_is_rendered(self):
        self.create_milestone("Release candidate")
        self.create_milestone("Customer review", group=self.group_a)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("task_timeline"))
        self.assertContains(response, "Timeline Settings")
        self.assertContains(response, 'id="timeline-milestones-dialog"')
        day = next(day for day in response.context["days"] if day["date"] == self.day)
        self.assertEqual(day["milestone"]["count"], 2)
        self.assertIn("Release candidate", day["milestone"]["tooltip"])
        self.assertIn("Customer review", day["milestone"]["tooltip"])

    def test_non_admin_does_not_load_milestone_manager_dataset(self):
        self.create_milestone("Visible marker")
        self.client.force_login(self.member)

        response = self.client.get(reverse("task_timeline"))

        self.assertEqual(response.context["milestones"], [])
        day = next(day for day in response.context["days"] if day["date"] == self.day)
        self.assertEqual(day["milestone"]["count"], 1)

    def test_timeline_filter_options_include_inactive_values_used_by_tasks(self):
        inactive_scope = Scope.objects.create(
            name="Archived Scope", color="#333333", is_active=False,
        )
        inactive_member = User.objects.create_user(
            username="inactive-timeline-member",
            password="test-password",
            display_name="Inactive Timeline Member",
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.INACTIVE,
        )
        task = Task.objects.create(
            title="Historical timeline task",
            status=Task.Status.DONE,
            assignee=inactive_member,
            timeline_start_date=self.day,
            due_date=self.day,
            link_url="https://example.test/historical",
            created_by=self.admin,
        )
        task.scopes.add(inactive_scope)
        self.client.force_login(self.member)

        response = self.client.get(reverse("task_timeline"), {
            "scope": inactive_scope.pk,
            "assignee": inactive_member.pk,
            "status": Task.Status.DONE,
        })

        self.assertIn(inactive_scope, response.context["filter_scopes"])
        self.assertIn(inactive_member, response.context["users"])
        self.assertContains(response, "Historical timeline task")

    def test_future_milestone_extends_the_timeline_year_bounds(self):
        future_day = self.day.replace(year=self.day.year + 3)
        TimelineMilestone.objects.create(
            name="Future launch",
            date=future_day,
            color="#7C3AED",
            created_by=self.admin,
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse("task_timeline"))

        self.assertEqual(response.context["window_end"].year, future_day.year)
        day = next(day for day in response.context["days"] if day["date"] == future_day)
        self.assertEqual(day["milestone"]["count"], 1)
        self.assertIn("Future launch", day["milestone"]["tooltip"])

    def test_deleting_group_makes_its_milestones_global(self):
        milestone = self.create_milestone("Group gate", group=self.group_a)
        self.client.force_login(self.admin)
        self.client.post(reverse("timeline_group_delete", args=[self.group_a.pk]))
        milestone.refresh_from_db()
        self.assertIsNone(milestone.timeline_group)
