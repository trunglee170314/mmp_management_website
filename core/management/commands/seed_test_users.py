from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import User


class Command(BaseCommand):
    help = "Create or update 2 Admin and 18 Member accounts for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Password assigned to every test account.")

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]
        if len(password) < 8:
            raise CommandError("--password must contain at least 8 characters.")

        account_specs = [
            (f"test_admin_{index:02d}", f"Test Admin {index:02d}", User.Role.ADMIN)
            for index in range(1, 3)
        ] + [
            (f"test_user_{index:02d}", f"Test User {index:02d}", User.Role.MEMBER)
            for index in range(1, 19)
        ]

        created_count = 0
        updated_count = 0
        for username, display_name, role in account_specs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"display_name": display_name},
            )
            user.display_name = display_name
            user.email = f"{username}@example.local"
            user.role = role
            user.account_status = User.AccountStatus.ACTIVE
            user.is_staff = role == User.Role.ADMIN
            user.is_superuser = False
            user.set_password(password)
            user.save()
            created_count += int(created)
            updated_count += int(not created)

        self.stdout.write(self.style.SUCCESS(
            f"Test accounts ready: {created_count} created, {updated_count} updated "
            "(2 Admins, 18 Members)."
        ))
