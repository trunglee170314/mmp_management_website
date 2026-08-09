from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    class AccountStatus(models.TextChoices):
        PENDING = "pending", "Pending Approval"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        REJECTED = "rejected", "Rejected"

    class ThemePreference(models.TextChoices):
        AUTO = "auto", "Auto"
        MORNING = "morning", "Morning"
        MIDDAY = "midday", "Midday"
        AFTERNOON = "afternoon", "Afternoon"
        EVENING = "evening", "Evening"

    display_name = models.CharField(max_length=120)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    account_status = models.CharField(max_length=12, choices=AccountStatus.choices, default=AccountStatus.PENDING)
    requested_at = models.DateTimeField(default=timezone.now)
    request_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_users")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    theme_preference = models.CharField(
        max_length=9,
        choices=ThemePreference.choices,
        default=ThemePreference.AUTO,
    )

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    def save(self, *args, **kwargs):
        self.is_active = self.account_status == self.AccountStatus.ACTIVE
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "account_status" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"is_active"}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name or self.username


class Scope(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=240, blank=True)
    color = models.CharField(max_length=7, default="#6D5CE7")
    is_active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class TimelineGroup(models.Model):
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#16835E")
    position = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_timeline_groups",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class TimelineHoliday(models.Model):
    name = models.CharField(max_length=120)
    color = models.CharField(max_length=7, default="#E07849")
    start_date = models.DateField()
    end_date = models.DateField()
    repeat_annually = models.BooleanField(default=False)
    notes = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_timeline_holidays",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self):
        return self.name


class TimelineMilestone(models.Model):
    name = models.CharField(max_length=120)
    date = models.DateField(db_index=True)
    color = models.CharField(max_length=7, default="#7C3AED")
    notes = models.CharField(max_length=240, blank=True)
    timeline_group = models.ForeignKey(
        TimelineGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="milestones",
    )
    scopes = models.ManyToManyField(Scope, blank=True, related_name="timeline_milestones")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_timeline_milestones",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "name", "pk"]

    def __str__(self):
        return self.name

    @property
    def scope_list(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("scopes")
        return list(prefetched) if prefetched is not None else list(self.scopes.all())


class Board(models.Model):
    name = models.CharField(max_length=100)
    barcode = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_boards")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="updated_boards")
    last_activity = models.CharField(max_length=240, default="Board created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "barcode"]

    def __str__(self):
        return self.name

    @property
    def display_label(self):
        return f"{self.name} ({self.barcode})"

    def save(self, *args, **kwargs):
        self.barcode = self.barcode.strip().upper()
        super().save(*args, **kwargs)


class BoardComponent(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="components")
    name = models.CharField(max_length=100)
    link_url = models.URLField(max_length=500)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "board", name="unique_board_component_name_ci"),
        ]

    def __str__(self):
        return f"{self.board.name} · {self.name}"

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        super().save(*args, **kwargs)


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        IN_PROGRESS = "in_progress", "In Progress"
        PAUSED = "paused", "Paused"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    scopes = models.ManyToManyField(Scope, related_name="tasks")
    parent_task = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subtasks",
    )
    related_tasks = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=True,
    )
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tasks")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    due_date = models.DateField(null=True, blank=True)
    timeline_start_date = models.DateField(null=True, blank=True)
    timeline_group = models.ForeignKey(
        TimelineGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    link_url = models.URLField(max_length=500)
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_tasks")
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="completed_tasks")
    completed_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["is_archived", "status", "due_date"],
                name="task_active_due_idx",
            ),
            models.Index(
                fields=["is_archived", "status", "completed_at"],
                name="task_completed_idx",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def scope_list(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("scopes")
        return list(prefetched) if prefetched is not None else list(self.scopes.all())

    @property
    def scope_label(self):
        return " / ".join(scope.name for scope in self.scope_list) or "Unscoped"


class TaskBoard(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="board_links")
    board = models.ForeignKey(Board, on_delete=models.PROTECT, related_name="task_links")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="added_task_boards")
    added_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ["added_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "board"],
                condition=models.Q(released_at__isnull=True),
                name="unique_active_task_board",
            ),
        ]


class BoardAssignment(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual Assignment"
        TASK = "task", "Task Assignment"

    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="board_assignments")
    source = models.CharField(max_length=10, choices=Source.choices)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="board_user_assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="issued_board_assignments")
    assigned_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["board", "user"],
                condition=models.Q(
                    source="manual",
                    released_at__isnull=True,
                ),
                name="unique_active_manual_board_assignment",
            ),
            models.UniqueConstraint(
                fields=["board", "user", "task"],
                condition=models.Q(
                    source="task",
                    released_at__isnull=True,
                ),
                name="unique_active_task_board_assignment",
            ),
        ]


class TaskHistory(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="history")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    event = models.CharField(max_length=120)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task", "-created_at"], name="taskhist_latest_idx"),
            models.Index(fields=["task", "event", "-created_at"], name="taskhist_event_idx"),
        ]


class MinuteWriterRotation(models.Model):
    name = models.CharField(max_length=120, unique=True)
    anchor_date = models.DateField(help_text="The first automatic meeting on or after this date uses the first writer.")
    last_assigned_writer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="last_assigned_writer_rotations",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_minute_writer_rotations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def _members(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("writer_members")
        if prefetched is None:
            return list(
                self.writer_members.select_related("user")
                .order_by("position", "pk")
            )
        return list(prefetched)

    def next_writer_member(self, meeting_date):
        upcoming = self.upcoming_writer_members(meeting_date, count=1)
        return upcoming[0] if upcoming else None

    def upcoming_writer_members(self, meeting_date, count=2):
        if not meeting_date or meeting_date < self.anchor_date:
            return []
        members = self._members()
        if not members or count < 1:
            return []
        last_index = next(
            (index for index, member in enumerate(members) if member.user_id == self.last_assigned_writer_id),
            -1,
        )
        upcoming = []
        offset = 1
        while len(upcoming) < count and offset <= len(members) * count:
            candidate = members[(last_index + offset) % len(members)]
            if candidate.user.account_status == User.AccountStatus.ACTIVE:
                upcoming.append(candidate)
            offset += 1
        return upcoming

    def writer_for(self, meeting_date):
        """Preview the next occurrence-based writer without advancing the rotation."""
        member = self.next_writer_member(meeting_date)
        return member.user if member else None

    def assign_next_writer(self, meeting_date):
        """Assign and consume the next writer. Call while holding a row lock."""
        member = self.next_writer_member(meeting_date)
        if not member:
            return None
        self.last_assigned_writer = member.user
        self.save(update_fields=["last_assigned_writer", "updated_at"])
        return member.user


class MinuteWriterRotationMember(models.Model):
    rotation = models.ForeignKey(
        MinuteWriterRotation,
        on_delete=models.CASCADE,
        related_name="writer_members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="minute_writer_rotations",
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["rotation", "user"], name="unique_rotation_writer"),
            models.UniqueConstraint(fields=["rotation", "position"], name="unique_rotation_writer_position"),
        ]

    def __str__(self):
        return f"{self.rotation}: {self.user}"


class Meeting(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"

    class WriterAssignment(models.TextChoices):
        AUTOMATIC = "automatic", "Automatic Rotation"
        MANUAL = "manual", "Manual"

    class TaskOrder(models.TextChoices):
        MANUAL = "manual", "Manual"
        PREVIOUS = "previous", "Previous Meeting"
        SCOPE = "scope", "Scope"
        ASSIGNEE = "assignee", "Assignee"

    title = models.CharField(max_length=180)
    meeting_date = models.DateField()
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hosted_meetings")
    minute_taker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_meetings")
    writer_assignment = models.CharField(
        max_length=12,
        choices=WriterAssignment.choices,
        default=WriterAssignment.MANUAL,
    )
    writer_rotation = models.ForeignKey(
        MinuteWriterRotation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meetings",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    task_order = models.CharField(
        max_length=12,
        choices=TaskOrder.choices,
        default=TaskOrder.SCOPE,
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_meetings")
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-meeting_date", "-created_at"]
        indexes = [
            models.Index(fields=["-meeting_date", "-created_at"], name="meeting_latest_idx"),
        ]

    def __str__(self):
        return self.title


class MeetingTask(models.Model):
    class ReviewState(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"
        NO_UPDATE = "no_update", "No Update"
        SKIPPED = "skipped", "Skipped"

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="task_entries")
    task = models.ForeignKey(Task, null=True, on_delete=models.SET_NULL, related_name="meeting_entries")
    position = models.PositiveIntegerField(default=0)
    included = models.BooleanField(default=False)
    review_state = models.CharField(max_length=12, choices=ReviewState.choices, default=ReviewState.PENDING)
    weekly_progress = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_meeting_tasks")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["meeting", "task"], name="unique_meeting_task")]
        indexes = [
            models.Index(fields=["meeting", "position"], name="meetingtask_order_idx"),
        ]


class ActionItem(models.Model):
    task = models.ForeignKey(Task, null=True, on_delete=models.SET_NULL, related_name="action_items")
    meeting = models.ForeignKey(Meeting, null=True, blank=True, on_delete=models.SET_NULL, related_name="action_items")
    source_meeting_title = models.CharField(max_length=180, blank=True)
    source_meeting_date = models.DateField(null=True, blank=True)
    content = models.CharField(max_length=300)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="action_items")
    due_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    is_cancelled = models.BooleanField(default=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="completed_action_items")
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="cancelled_action_items")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_action_items")
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["is_completed", "due_date", "created_at"]
        indexes = [
            models.Index(
                fields=["task", "published_at", "is_cancelled", "is_completed"],
                name="action_task_state_idx",
            ),
            models.Index(
                fields=["assignee", "published_at", "is_cancelled", "is_completed"],
                name="action_owner_state_idx",
            ),
        ]

    def __str__(self):
        return self.content

    @property
    def is_published(self):
        return self.published_at is not None


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    entity_type = models.CharField(max_length=40)
    entity_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=180)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id", "-created_at"],
                name="audit_entity_latest_idx",
            ),
        ]


class RelationshipMap(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_relationship_maps",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_relationship_maps",
    )

    class Meta:
        ordering = ["name", "pk"]
        indexes = [
            models.Index(fields=["is_deleted", "deleted_at"], name="rel_map_trash_expiry_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(is_deleted=True, deleted_at__isnull=False)
                    | models.Q(is_deleted=False, deleted_at__isnull=True, deleted_by__isnull=True)
                ),
                name="relationship_map_deletion_state_valid",
            ),
        ]

    def __str__(self):
        return self.name


class RelationshipMapRevision(models.Model):
    relationship_map = models.ForeignKey(
        RelationshipMap,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="relationship_map_revisions",
    )
    action = models.CharField(max_length=180)
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["relationship_map", "-created_at"],
                name="rel_map_revision_latest_idx",
            ),
        ]

    def __str__(self):
        return f"{self.relationship_map}: {self.action}"


class RelationshipGroup(models.Model):
    relationship_map = models.ForeignKey(RelationshipMap, on_delete=models.CASCADE, related_name="groups")
    name = models.CharField(max_length=120)
    color = models.CharField(max_length=7, default="#eaf4ee")
    x = models.FloatField(default=80)
    y = models.FloatField(default=80)
    width = models.FloatField(default=360)
    height = models.FloatField(default=260)
    is_collapsed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["relationship_map", "name"], name="unique_relationship_group_name"),
        ]

    def __str__(self):
        return f"{self.relationship_map}: {self.name}"


class RelationshipNode(models.Model):
    relationship_map = models.ForeignKey(RelationshipMap, on_delete=models.CASCADE, related_name="nodes")
    group = models.ForeignKey(RelationshipGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="nodes")
    name = models.CharField(max_length=160)
    node_type = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=40, blank=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#ffffff")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="relationship_nodes",
    )
    linked_task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="relationship_nodes")
    linked_board = models.ForeignKey(Board, null=True, blank=True, on_delete=models.SET_NULL, related_name="relationship_nodes")
    external_url = models.URLField(max_length=500, blank=True)
    x = models.FloatField(default=160)
    y = models.FloatField(default=160)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        indexes = [models.Index(fields=["relationship_map", "node_type"], name="rel_node_type_idx")]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.group_id and self.relationship_map_id and self.group.relationship_map_id != self.relationship_map_id:
            raise ValidationError({"group": "The Group must belong to the same Map as the Node."})


class RelationshipEdge(models.Model):
    class Direction(models.TextChoices):
        NONE = "none", "No direction"
        FORWARD = "forward", "Forward"
        BIDIRECTIONAL = "bidirectional", "Bidirectional"

    class LineStyle(models.TextChoices):
        SOLID = "solid", "Solid"
        DASHED = "dashed", "Dashed"

    relationship_map = models.ForeignKey(RelationshipMap, on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey(RelationshipNode, on_delete=models.CASCADE, related_name="outgoing_relationships")
    target = models.ForeignKey(RelationshipNode, on_delete=models.CASCADE, related_name="incoming_relationships")
    relation_type = models.CharField(max_length=80, blank=True)
    label = models.CharField(max_length=120, blank=True)
    direction = models.CharField(max_length=16, choices=Direction.choices, default=Direction.NONE)
    line_style = models.CharField(max_length=12, choices=LineStyle.choices, default=LineStyle.SOLID)
    color = models.CharField(max_length=7, default="#aeb7b1")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(source=models.F("target")), name="relationship_edge_distinct_nodes"),
        ]
        indexes = [models.Index(fields=["relationship_map", "source", "target"], name="rel_edge_path_idx")]

    def __str__(self):
        return self.label or self.relation_type or f"{self.source} — {self.target}"

    def clean(self):
        super().clean()
        if self.source_id and self.relationship_map_id and self.source.relationship_map_id != self.relationship_map_id:
            raise ValidationError({"source": "The source Node must belong to this Map."})
        if self.target_id and self.relationship_map_id and self.target.relationship_map_id != self.relationship_map_id:
            raise ValidationError({"target": "The target Node must belong to this Map."})
        if self.source_id and self.target_id and self.relationship_map_id:
            duplicate = type(self).objects.filter(relationship_map_id=self.relationship_map_id).filter(
                models.Q(source_id=self.source_id, target_id=self.target_id)
                | models.Q(source_id=self.target_id, target_id=self.source_id)
            )
            if self.pk:
                duplicate = duplicate.exclude(pk=self.pk)
            if duplicate.exists():
                raise ValidationError("These Nodes are already connected.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PasswordResetRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_reset_requests")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_password_resets")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Password reset for {self.user}"


class SystemSetting(models.Model):
    singleton = models.BooleanField(default=True, unique=True)
    task_score_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(singleton=True)
        return obj
