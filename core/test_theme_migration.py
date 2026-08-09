from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ThemePreferenceMigrationTests(TransactionTestCase):
    migrate_from = ("core", "0024_relationship_map_recovery")
    migrate_to = ("core", "0025_seasonal_theme_preferences")

    def test_theme_values_migrate_forward_and_reverse(self):
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([self.migrate_from])
            old_apps = executor.loader.project_state([self.migrate_from]).apps
            old_user = old_apps.get_model("core", "User")
            users = {
                value: old_user.objects.create(
                    username=f"theme-{value}",
                    display_name=f"Theme {value}",
                    theme_preference=value,
                ).pk
                for value in ("system", "light", "dark")
            }

            executor = MigrationExecutor(connection)
            executor.migrate([self.migrate_to])
            new_apps = executor.loader.project_state([self.migrate_to]).apps
            new_user = new_apps.get_model("core", "User")
            self.assertEqual(new_user.objects.get(pk=users["system"]).theme_preference, "auto")
            self.assertEqual(new_user.objects.get(pk=users["light"]).theme_preference, "morning")
            self.assertEqual(new_user.objects.get(pk=users["dark"]).theme_preference, "evening")
            afternoon_id = new_user.objects.create(
                username="theme-afternoon",
                display_name="Theme afternoon",
                theme_preference="afternoon",
            ).pk

            executor = MigrationExecutor(connection)
            executor.migrate([self.migrate_from])
            rolled_back_apps = executor.loader.project_state([self.migrate_from]).apps
            rolled_back_user = rolled_back_apps.get_model("core", "User")
            self.assertEqual(
                rolled_back_user.objects.get(pk=afternoon_id).theme_preference,
                "light",
            )
        finally:
            MigrationExecutor(connection).migrate([self.migrate_to])
