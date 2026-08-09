from django.test import TestCase
from django.urls import reverse

from core.models import User


class MilestoneTaskPickerUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="picker-ui", display_name="Picker UI", role="admin", account_status="active"
        )
        self.client.force_login(self.user)

    def test_milestone_task_picker_reuses_single_searchable_picker(self):
        response = self.client.get(reverse("task_timeline"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="milestone-task-picker-toggle"')
        self.assertContains(response, 'id="milestone-task-picker-options"')
        self.assertContains(response, 'data-single-task-search')
        self.assertNotContains(response, 'data-milestone-task-search')
        self.assertContains(response, 'data-single-task-options')

    def test_base_uses_current_shared_styles_and_picker_script(self):
        response = self.client.get(reverse("tasks"))
        self.assertContains(response, '/static/core/styles.css?v=13')
        self.assertContains(response, '/static/core/task-filters.css?v=17')
        self.assertContains(response, '/static/core/app.js?v=41')
