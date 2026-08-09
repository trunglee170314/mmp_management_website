import csv
import hashlib
import unicodedata
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core.models import AuditLog, Scope, Task, TaskHistory, User


REQUIRED_COLUMNS = {
    "Link",
    "Status",
    "Priority",
    "Subject",
    "Author",
    "Assignee",
    "Start date",
    "Due date",
    "Complete date",
}
STATUS_MAP = {
    "to do": Task.Status.TODO,
    "in progress": Task.Status.IN_PROGRESS,
    "paused": Task.Status.PAUSED,
    "done": Task.Status.DONE,
    "cancelled": Task.Status.CANCELLED,
    "canceled": Task.Status.CANCELLED,
}
PRIORITY_MAP = {
    "low": Task.Priority.LOW,
    "medium": Task.Priority.MEDIUM,
    "high": Task.Priority.HIGH,
}


def collapse_spaces(value):
    return " ".join((value or "").split())


def normalize_identity(value):
    normalized = unicodedata.normalize("NFKD", collapse_spaces(value))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def identity_keys(value):
    normalized = normalize_identity(value)
    tokens = normalized.split()
    return {normalized, " ".join(sorted(tokens))} if normalized else set()


class UserResolver:
    def __init__(self, explicit_mappings, unknown_user_policy):
        self.explicit_mappings = explicit_mappings
        self.unknown_user_policy = unknown_user_policy
        self.cache = {}
        self.created_legacy_users = []
        self.users = list(User.objects.all())
        self.index = {}
        for user in self.users:
            for key in identity_keys(user.username) | identity_keys(user.display_name):
                self.index.setdefault(key, []).append(user)

    def resolve(self, external_name, row_number, role_label):
        display_name = collapse_spaces(external_name)
        if not display_name:
            return None
        cache_key = normalize_identity(display_name)
        if cache_key in self.cache:
            return self.cache[cache_key]

        mapped_username = self.explicit_mappings.get(cache_key)
        if mapped_username:
            user = User.objects.filter(username__iexact=mapped_username).first()
            if not user:
                raise CommandError(
                    f"Row {row_number}: mapped username '{mapped_username}' for "
                    f"{role_label} '{display_name}' does not exist."
                )
            self.cache[cache_key] = user
            return user

        candidates = {}
        for key in identity_keys(display_name):
            for user in self.index.get(key, []):
                candidates[user.pk] = user
        if len(candidates) == 1:
            user = next(iter(candidates.values()))
            self.cache[cache_key] = user
            return user
        if len(candidates) > 1:
            usernames = ", ".join(sorted(user.username for user in candidates.values()))
            raise CommandError(
                f"Row {row_number}: {role_label} '{display_name}' matches multiple users "
                f"({usernames}). Use --map-user '{display_name}=USERNAME'."
            )
        if self.unknown_user_policy == "error":
            raise CommandError(
                f"Row {row_number}: no account matches {role_label} '{display_name}'. "
                "Add --map-user or allow inactive legacy users."
            )

        user = self.create_legacy_user(display_name)
        self.cache[cache_key] = user
        return user

    def create_legacy_user(self, display_name):
        normalized = normalize_identity(display_name)
        base = slugify(normalized) or "member"
        base = f"legacy-{base}"[:140].rstrip("-")
        username = base
        existing = User.objects.filter(username=username).first()
        if existing and normalize_identity(existing.display_name) == normalized:
            return existing
        if existing:
            suffix = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
            username = f"{base[:140 - len(suffix) - 1]}-{suffix}"
            counter = 2
            while User.objects.filter(username=username).exists():
                counter_suffix = f"-{counter}"
                username = f"{base[:150 - len(counter_suffix)]}{counter_suffix}"
                counter += 1

        user = User(
            username=username,
            display_name=display_name,
            role=User.Role.MEMBER,
            account_status=User.AccountStatus.INACTIVE,
            request_note="Legacy account created by the CSV task importer.",
        )
        user.set_unusable_password()
        user.save()
        self.created_legacy_users.append(user)
        for key in identity_keys(user.username) | identity_keys(user.display_name):
            self.index.setdefault(key, []).append(user)
        return user


class Command(BaseCommand):
    help = "Import tasks from a CSV export without creating duplicates."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Path to the task CSV export.")
        parser.add_argument(
            "--scope",
            default="Uncategorized",
            help="Scope assigned to imported tasks (default: Uncategorized).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report changes, then roll back the transaction.",
        )
        parser.add_argument(
            "--map-user",
            action="append",
            default=[],
            metavar="CSV_NAME=USERNAME",
            help="Map a CSV person name to an existing username. May be repeated.",
        )
        parser.add_argument(
            "--unknown-users",
            choices=["inactive", "error"],
            default="inactive",
            help="Create inactive legacy accounts or fail on unknown users (default: inactive).",
        )
        parser.add_argument(
            "--link-base-url", "--redmine-base-url", dest="link_base_url",
            help="Replace the scheme and host while preserving each issue path.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"]).expanduser()
        rows = self.load_rows(csv_path)
        mappings = self.parse_user_mappings(options["map_user"])
        link_base = self.parse_link_base(options.get("link_base_url"))
        dry_run = options["dry_run"]

        with transaction.atomic():
            scope, scope_created = self.get_scope(options["scope"])
            resolver = UserResolver(mappings, options["unknown_users"])
            created_tasks = 0
            skipped_tasks = 0

            for row_number, row in rows:
                source_url = row["Link"]
                link_url = self.rewrite_url(source_url, link_base)
                existing_count = Task.objects.filter(link_url=link_url).count()
                if existing_count > 1:
                    raise CommandError(
                        f"Row {row_number}: multiple existing tasks use Link '{link_url}'."
                    )
                if existing_count == 1:
                    skipped_tasks += 1
                    continue

                status = self.map_choice(row["Status"], STATUS_MAP, "Status", row_number)
                priority = self.map_choice(row["Priority"], PRIORITY_MAP, "Priority", row_number)
                start_date = self.parse_date(row["Start date"], "Start date", row_number)
                due_date = self.parse_date(row["Due date"], "Due date", row_number)
                complete_date = self.parse_date(row["Complete date"], "Complete date", row_number)
                if start_date and due_date and due_date < start_date:
                    raise CommandError(f"Row {row_number}: Due date cannot be earlier than Start date.")
                if start_date and complete_date and complete_date < start_date:
                    raise CommandError(f"Row {row_number}: Complete date cannot be earlier than Start date.")
                if status == Task.Status.DONE and not complete_date:
                    raise CommandError(f"Row {row_number}: a Done task requires Complete date.")
                if status != Task.Status.DONE and complete_date:
                    raise CommandError(f"Row {row_number}: only Done tasks may have Complete date.")

                author = resolver.resolve(row["Author"], row_number, "Author")
                assignee = resolver.resolve(row["Assignee"], row_number, "Assignee")
                if status == Task.Status.DONE and not assignee:
                    raise CommandError(f"Row {row_number}: a Done task requires an Assignee.")

                task = Task.objects.create(
                    title=row["Subject"],
                    description="",
                    assignee=assignee,
                    status=status,
                    priority=priority,
                    timeline_start_date=start_date,
                    due_date=due_date,
                    link_url=link_url,
                    created_by=author,
                    completed_by=assignee if status == Task.Status.DONE else None,
                    completed_at=self.date_to_datetime(complete_date) if complete_date else None,
                )
                task.scopes.add(scope)
                TaskHistory.objects.create(
                    task=task,
                    actor=author,
                    event="Task imported from CSV",
                    new_value=source_url,
                    note=f"CSV row {row_number}; original author: {row['Author']}; original assignee: {row['Assignee']}",
                )
                if status in {Task.Status.DONE, Task.Status.CANCELLED}:
                    status_history = TaskHistory.objects.create(
                        task=task,
                        actor=author,
                        event="Status changed",
                        new_value=task.get_status_display(),
                        note="Imported status",
                    )
                    if task.completed_at:
                        TaskHistory.objects.filter(pk=status_history.pk).update(created_at=task.completed_at)
                AuditLog.objects.create(
                    actor=author,
                    entity_type="Task",
                    entity_id=task.pk,
                    action="Task imported from CSV",
                    details={"csv_row": row_number, "source_url": source_url},
                )
                created_tasks += 1

            legacy_names = [user.display_name for user in resolver.created_legacy_users]
            if dry_run:
                transaction.set_rollback(True)

        prefix = "DRY RUN (rolled back)" if dry_run else "IMPORT COMPLETE"
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}: {len(rows)} rows; {created_tasks} tasks created; "
            f"{skipped_tasks} existing tasks skipped."
        ))
        scope_action = "would be created" if dry_run and scope_created else (
            "created" if scope_created else "reused"
        )
        self.stdout.write(f"Scope '{scope.name}' {scope_action}.")
        if legacy_names:
            verb = "would be created" if dry_run else "created"
            self.stdout.write(self.style.WARNING(
                f"{len(legacy_names)} inactive legacy users {verb}: " + ", ".join(sorted(legacy_names))
            ))

    def load_rows(self, csv_path):
        if not csv_path.is_file():
            raise CommandError(f"CSV file not found: {csv_path}")
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = set(reader.fieldnames or [])
                missing = sorted(REQUIRED_COLUMNS - columns)
                if missing:
                    raise CommandError("CSV is missing required columns: " + ", ".join(missing))
                rows = []
                seen_links = set()
                validator = URLValidator(schemes=["http", "https"])
                for row_number, raw_row in enumerate(reader, start=2):
                    row = {column: collapse_spaces(raw_row.get(column, "")) for column in REQUIRED_COLUMNS}
                    for column in ("Link", "Status", "Priority", "Subject", "Author", "Assignee"):
                        if not row[column]:
                            raise CommandError(f"Row {row_number}: '{column}' is required.")
                    try:
                        validator(row["Link"])
                    except ValidationError as exc:
                        raise CommandError(f"Row {row_number}: invalid Link '{row['Link']}'.") from exc
                    if row["Link"] in seen_links:
                        raise CommandError(f"Row {row_number}: duplicate Link '{row['Link']}' in CSV.")
                    seen_links.add(row["Link"])
                    if len(row["Subject"]) > Task._meta.get_field("title").max_length:
                        raise CommandError(f"Row {row_number}: Subject exceeds the Task title limit.")
                    rows.append((row_number, row))
        except UnicodeDecodeError as exc:
            raise CommandError("CSV must use UTF-8 encoding.") from exc
        if not rows:
            raise CommandError("CSV contains no task rows.")
        return rows

    def parse_user_mappings(self, raw_mappings):
        mappings = {}
        for raw_mapping in raw_mappings:
            external_name, separator, username = raw_mapping.partition("=")
            external_name = collapse_spaces(external_name)
            username = username.strip()
            if not separator or not external_name or not username:
                raise CommandError("--map-user must use CSV_NAME=USERNAME format.")
            key = normalize_identity(external_name)
            if key in mappings and mappings[key].casefold() != username.casefold():
                raise CommandError(f"Multiple mappings were supplied for '{external_name}'.")
            mappings[key] = username
        return mappings

    def parse_link_base(self, value):
        if not value:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise CommandError("--link-base-url must be an absolute HTTP(S) URL.")
        return parts

    def rewrite_url(self, source_url, link_base):
        if not link_base:
            return source_url
        source = urlsplit(source_url)
        base_path = link_base.path.rstrip("/")
        path = f"{base_path}{source.path}" if base_path else source.path
        return urlunsplit((link_base.scheme, link_base.netloc, path, source.query, source.fragment))

    def get_scope(self, name):
        scope_name = collapse_spaces(name)
        if not scope_name:
            raise CommandError("--scope cannot be empty.")
        scope = Scope.objects.filter(name__iexact=scope_name).first()
        if scope:
            return scope, False
        return Scope.objects.create(
            name=scope_name,
            description="Tasks awaiting final scope classification.",
            color="#8A8591",
            is_active=True,
        ), True

    def map_choice(self, value, choices, label, row_number):
        mapped = choices.get(value.casefold())
        if mapped is None:
            raise CommandError(f"Row {row_number}: unsupported {label} '{value}'.")
        return mapped

    def parse_date(self, value, label, row_number):
        if not value:
            return None
        for date_format in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError:
                continue
        raise CommandError(
            f"Row {row_number}: invalid {label} '{value}'. Use M/D/YYYY or YYYY-MM-DD."
        )

    def date_to_datetime(self, value):
        return timezone.make_aware(datetime.combine(value, time(hour=12)))
