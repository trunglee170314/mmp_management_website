from django.test import TestCase
from django.urls import reverse

from .models import User


class ThemePreferenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="theme-user",
            password="test-password",
            display_name="Theme User",
            account_status=User.AccountStatus.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_account_menu_exposes_all_theme_choices(self):
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, 'data-theme-preference="auto"')
        self.assertContains(response, 'data-theme-choice="auto"')
        self.assertContains(response, 'data-theme-choice="morning"')
        self.assertContains(response, 'data-theme-choice="midday"')
        self.assertContains(response, 'data-theme-choice="afternoon"')
        self.assertContains(response, 'data-theme-choice="evening"')
        self.assertContains(response, 'class="theme-swatch theme-swatch-auto"')
        self.assertContains(response, reverse("theme_preference"))

    def test_user_can_save_theme_preference(self):
        response = self.client.post(
            reverse("theme_preference"),
            {"theme": User.ThemePreference.AFTERNOON},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"updated": True, "theme": "afternoon"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme_preference, User.ThemePreference.AFTERNOON)

        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'data-theme-preference="afternoon"')
        self.assertContains(page, 'data-theme-choice="afternoon" aria-label="Afternoon appearance" aria-checked="true"')

    def test_invalid_theme_is_rejected_without_changing_user(self):
        response = self.client.post(
            reverse("theme_preference"),
            {"theme": "neon"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme_preference, User.ThemePreference.AUTO)

    def test_theme_endpoint_requires_authentication(self):
        self.client.logout()

        response = self.client.post(reverse("theme_preference"), {"theme": "morning"})

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
