import os
from django.core.management.base import BaseCommand
from core.models import Scope, User


class Command(BaseCommand):
    help = "Create or update the initial Admin account from environment variables."

    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD")
        display_name = os.getenv("ADMIN_DISPLAY_NAME", "System Admin")
        reset_password = os.getenv("ADMIN_RESET_PASSWORD", "0") == "1"
        if not password:
            self.stdout.write(self.style.WARNING("ADMIN_PASSWORD is not set; the Admin account was not created."))
            return
        user, created = User.objects.get_or_create(username=username, defaults={"display_name": display_name})
        user.display_name = display_name
        user.role = User.Role.ADMIN
        user.account_status = User.AccountStatus.ACTIVE
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        if created or reset_password:
            user.set_password(password)
        user.save()
        Scope.objects.get_or_create(name="Uncategorized", defaults={"description": "Tasks awaiting final scope classification.", "color": "#8A8591"})
        password_state = "password initialized" if created else (
            "password reset" if reset_password else "password preserved"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Admin account {'created' if created else 'updated'}: {username} ({password_state})"
            )
        )
