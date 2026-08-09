"""Parent collapse can identify visible milestone owners without extra Task rows."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Task, TimelineGroup, TimelineMilestone, User
from .timeline_interactions import milestone_data, milestone_task_relations


class TimelineMilestoneAncestryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="ancestry-owner", account_status="active")
        cls.other = User.objects.create_user(username="ancestry-admin", account_status="active", role="admin")
        cls.group = TimelineGroup.objects.create(name="Ancestry Group")
        cls.other_group = TimelineGroup.objects.create(name="Other ancestry Group")
        cls.today = timezone.localdate()
        cls.parent = Task.objects.create(
            title="Ancestry visible parent", timeline_group=cls.group,
            timeline_start_date=cls.today, due_date=cls.today, link_url="https://example.test/root",
        )
        cls.middle = Task.objects.create(
            title="Filtered intermediary", timeline_group=cls.group, parent_task=cls.parent,
            link_url="https://example.test/intermediary",
        )
        cls.child = Task.objects.create(
            title="Ancestry unscheduled child", timeline_group=cls.group, parent_task=cls.middle,
            link_url="https://example.test/child",
        )
        cls.marker = TimelineMilestone.objects.create(
            name="Visible child milestone", task=cls.child, date=cls.today, created_by=cls.owner,
        )

    def setUp(self):
        self.client.force_login(self.other)

    def test_unscheduled_owner_carries_same_group_ancestry_through_filtered_parent(self):
        response = self.client.get(reverse("task_timeline"), {"q": "Ancestry"})
        self.assertEqual(response.status_code, 200)
        item = next(item for item in response.context["milestones_data_json"] if item["id"] == self.marker.pk)
        self.assertFalse(item["task_has_schedule"])
        self.assertEqual(item["task_parent"], self.middle.pk)
        self.assertEqual(item["task_ancestors"], [self.middle.pk, self.parent.pk])
        scheduled = [item["id"] for item in response.context["tasks_data_json"]]
        self.assertEqual(scheduled, [self.parent.pk])
        self.assertNotIn(self.child.pk, scheduled)
        self.assertNotIn(self.middle.pk, scheduled)

    def test_archived_cross_group_and_cyclic_parents_stop_ancestry_without_losing_marker(self):
        cross_group = Task.objects.create(
            title="Cross Group parent", timeline_group=self.other_group, link_url="https://example.test/cross",
        )
        archived = Task.objects.create(
            title="Archived parent", timeline_group=self.group, is_archived=True, link_url="https://example.test/archived",
        )
        for parent in (cross_group, archived, self.child):
            with self.subTest(parent=parent.pk):
                Task.objects.filter(pk=self.child.pk).update(parent_task=parent)
                item = TimelineMilestone.objects.select_related("task").get(pk=self.marker.pk)
                data = milestone_data(item, self.owner, task_relations=milestone_task_relations())
                self.assertEqual(data["task_parent"], parent.pk)
                self.assertEqual(data["task_ancestors"], [])
        Task.objects.filter(pk=self.child.pk).update(parent_task=self.middle)
        Task.objects.filter(pk=self.middle.pk).update(parent_task=self.child)
        item = TimelineMilestone.objects.select_related("task").get(pk=self.marker.pk)
        data = milestone_data(item, self.owner, task_relations=milestone_task_relations())
        self.assertEqual(data["task_ancestors"], [self.middle.pk])
        self.assertNotIn(self.child.pk, data["task_ancestors"])

    def test_reusing_one_relation_map_does_not_add_a_query_per_milestone(self):
        TimelineMilestone.objects.bulk_create([
            TimelineMilestone(name=f"Child milestone {index}", task=self.child, date=self.today, created_by=self.owner)
            for index in range(30)
        ])
        items = list(TimelineMilestone.objects.select_related("task").prefetch_related("scopes"))
        with self.assertNumQueries(1):
            relations = milestone_task_relations()
        with self.assertNumQueries(0):
            data = [milestone_data(item, self.owner, task_relations=relations) for item in items]
        self.assertEqual(len(data), 31)
        self.assertTrue(all(item["task_ancestors"] == [self.middle.pk, self.parent.pk] for item in data))

    def test_private_milestone_is_removed_before_ancestry_metadata_reaches_another_admin(self):
        secret = TimelineMilestone.objects.create(
            name="PRIVATE_ANCESTRY_SECRET", task=self.child, date=self.today,
            created_by=self.owner, visibility="private",
        )
        response = self.client.get(reverse("task_timeline"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret.pk, [item["id"] for item in response.context["milestones_data_json"]])
        self.assertNotContains(response, secret.name)
        shared = next(item for item in response.context["milestones_data_json"] if item["id"] == self.marker.pk)
        self.assertEqual(shared["task_ancestors"], [self.middle.pk, self.parent.pk])
