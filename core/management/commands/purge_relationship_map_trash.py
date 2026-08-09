from django.core.management.base import BaseCommand

from core.relationship_history import (
    RELATIONSHIP_TRASH_RETENTION_DAYS,
    purge_expired_relationship_maps,
)


class Command(BaseCommand):
    help = "Permanently delete System Maps that have remained in Trash past retention."

    def handle(self, *args, **options):
        count = 0
        while True:
            purged = purge_expired_relationship_maps()
            count += purged
            if purged < 100:
                break
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {count} System Map(s) older than {RELATIONSHIP_TRASH_RETENTION_DAYS} days."
            )
        )
