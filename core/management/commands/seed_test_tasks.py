import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, Scope, Task, TaskHistory, TimelineGroup, User


TASK_ACTIONS = [
    "Calibrate",
    "Document",
    "Inspect",
    "Integrate",
    "Optimize",
    "Prepare",
    "Review",
    "Test",
    "Update",
    "Validate",
]

TASK_SUBJECTS = [
    "assembly procedure",
    "board firmware",
    "component inventory",
    "data acquisition flow",
    "equipment checklist",
    "failure analysis report",
    "lab network configuration",
    "measurement workflow",
    "prototype enclosure",
    "test station setup",
]


class Command(BaseCommand):
    help = "Create random tasks for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=20)
        parser.add_argument("--seed", type=int)

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        if count < 1:
            raise CommandError("--count must be at least 1.")

        rng = random.Random(options["seed"])
        scopes = list(Scope.objects.filter(is_active=True)) or list(Scope.objects.all())
        users = list(User.objects.filter(account_status=User.AccountStatus.ACTIVE))
        groups = list(TimelineGroup.objects.all())

        if not scopes:
            raise CommandError("Create at least one Scope before seeding tasks.")
        if not users:
            raise CommandError("Create at least one active User before seeding tasks.")

        today = timezone.localdate()
        now = timezone.now()
        created = []

        for index in range(1, count + 1):
            status = rng.choices(
                list(Task.Status.values),
                weights=[35, 30, 10, 20, 5],
                k=1,
            )[0]
            assignee = rng.choice(users) if rng.random() < 0.9 else None
            creator = rng.choice(users)
            scheduled = rng.random() < 0.85
            start_date = today + timedelta(days=rng.randint(-30, 30)) if scheduled else None
            due_date = start_date + timedelta(days=rng.randint(3, 60)) if start_date else None
            action = rng.choice(TASK_ACTIONS)
            subject = rng.choice(TASK_SUBJECTS)

            task = Task.objects.create(
                title=f"[TEST {index:02d}] {action} {subject}",
                description=(
                    "Randomly generated test task for validating task lists, filters, "
                    "the timeline, dashboards, and Meeting Minutes."
                ),
                assignee=assignee,
                status=status,
                priority=rng.choice(list(Task.Priority.values)),
                due_date=due_date,
                timeline_start_date=start_date,
                timeline_group=rng.choice(groups) if scheduled and groups and rng.random() < 0.8 else None,
                link_url=f"https://example.test/issues/{10000 + index}",
                created_by=creator,
                completed_by=assignee if status == Task.Status.DONE else None,
                completed_at=now if status == Task.Status.DONE else None,
            )
            scope_count = rng.randint(1, min(3, len(scopes)))
            task.scopes.set(rng.sample(scopes, k=scope_count))
            TaskHistory.objects.create(task=task, actor=creator, event="Test task created")
            if status in {Task.Status.DONE, Task.Status.CANCELLED}:
                TaskHistory.objects.create(
                    task=task,
                    actor=creator,
                    event="Status changed",
                    new_value=task.get_status_display(),
                )
            AuditLog.objects.create(
                actor=creator,
                entity_type="Task",
                entity_id=task.pk,
                action="Test task created",
                details={"status": status, "seeded": True},
            )
            created.append(task)

        status_counts = {
            label: sum(task.status == value for task in created)
            for value, label in Task.Status.choices
        }
        summary = ", ".join(f"{label}: {total}" for label, total in status_counts.items() if total)
        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} test tasks ({summary})."))
