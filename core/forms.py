from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from .business_rules import open_action_count_blocking_done
from .models import Board, Meeting, MinuteWriterRotation, MinuteWriterRotationMember, Scope, Task, TimelineGroup, TimelineHoliday, TimelineMilestone, User


class DateInput(forms.DateInput):
    input_type = "date"


class RegistrationForm(UserCreationForm):
    display_name = forms.CharField(max_length=120, label="Full Name")
    email = forms.EmailField(required=False)
    request_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Request Note")

    class Meta:
        model = User
        fields = ["display_name", "username", "email", "password1", "password2"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.account_status = User.AccountStatus.PENDING
        user.role = User.Role.MEMBER
        user.is_active = False
        user.request_note = self.cleaned_data.get("request_note", "")
        if commit:
            user.save()
        return user


class WorkspaceAuthenticationForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username")
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = None
        if user and user.account_status != User.AccountStatus.ACTIVE:
            messages = {
                User.AccountStatus.PENDING: "Your account is awaiting Admin approval.",
                User.AccountStatus.REJECTED: "Your registration request was not approved. Please contact an administrator.",
                User.AccountStatus.INACTIVE: "This account is inactive. Please contact an administrator.",
            }
            raise ValidationError(messages.get(user.account_status, "This account cannot sign in."), code="inactive")
        return super().clean()


class UserCreateForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["display_name", "username", "email", "role", "password1", "password2"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.account_status = User.AccountStatus.ACTIVE
        user.is_active = True
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    new_password = forms.CharField(required=False, widget=forms.PasswordInput, help_text="Leave blank to keep the current password.")

    class Meta:
        model = User
        fields = ["display_name", "username", "email", "role"]

    def clean_new_password(self):
        password = self.cleaned_data.get("new_password")
        if password:
            validate_password(password, self.instance)
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.role != User.Role.ADMIN:
            user.is_staff = False
            user.is_superuser = False
        if self.cleaned_data.get("new_password"):
            user.set_password(self.cleaned_data["new_password"])
        if commit:
            user.save()
        return user


class UserDeactivationForm(forms.Form):
    transfer_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Transfer responsibilities to",
        help_text="Select an active Admin who will take over this user's current work.",
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["transfer_to"].queryset = User.objects.filter(
            Q(role=User.Role.ADMIN) | Q(is_superuser=True),
            account_status=User.AccountStatus.ACTIVE,
        ).exclude(pk=getattr(user, "pk", None)).order_by("display_name", "username", "pk")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["display_name", "email"]


class ForgotPasswordRequestForm(forms.Form):
    username = forms.CharField(max_length=150, label="Username", widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}))


class AdminPasswordResetForm(forms.Form):
    new_password1 = forms.CharField(label="New Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    new_password2 = forms.CharField(label="Confirm New Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("new_password1")
        if password and password != cleaned.get("new_password2"):
            self.add_error("new_password2", "The two password fields do not match.")
        if password:
            validate_password(password, self.user)
        return cleaned


class ScopeForm(forms.ModelForm):
    class Meta:
        model = Scope
        fields = ["name", "description", "color", "is_active"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class BoardForm(forms.ModelForm):
    class Meta:
        model = Board
        fields = ["name", "barcode", "link_url", "notes"]
        labels = {"link_url": "Link"}

    def clean_barcode(self):
        return self.cleaned_data["barcode"].strip().upper()


class BoardMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, board):
        return board.display_label


class TaskForm(forms.ModelForm):
    scopes = forms.ModelMultipleChoiceField(
        queryset=Scope.objects.none(),
        required=True,
        label="Scopes",
        widget=forms.CheckboxSelectMultiple,
    )
    boards = BoardMultipleChoiceField(
        queryset=Board.objects.none(),
        required=False,
        label="Boards (Optional)",
        widget=forms.CheckboxSelectMultiple,
    )
    related_tasks = forms.ModelMultipleChoiceField(
        queryset=Task.objects.none(),
        required=False,
        label="Related Tasks (Optional)",
        widget=forms.CheckboxSelectMultiple,
    )
    status_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), help_text="Required when pausing or cancelling a task.")

    class Meta:
        model = Task
        fields = ["title", "description", "scopes", "parent_task", "related_tasks", "assignee", "status", "timeline_start_date", "due_date", "priority", "boards", "link_url", "score"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "timeline_start_date": DateInput(),
            "due_date": DateInput(),
        }
        labels = {"timeline_start_date": "Start Date", "parent_task": "Parent Task (Optional)", "link_url": "Link"}

    def __init__(self, *args, score_enabled=False, **kwargs):
        self.old_status = kwargs.get("instance").status if kwargs.get("instance") and kwargs.get("instance").pk else None
        super().__init__(*args, **kwargs)
        scope_options = Scope.objects.filter(is_active=True)
        if self.instance.pk:
            scope_options = Scope.objects.filter(Q(is_active=True) | Q(tasks=self.instance))
        self.fields["scopes"].queryset = scope_options.distinct().order_by("position", "name")
        self.fields["assignee"].queryset = User.objects.filter(account_status=User.AccountStatus.ACTIVE)
        parent_tasks = Task.objects.filter(is_archived=False).order_by("created_at", "pk")
        if self.instance.pk:
            parent_tasks = parent_tasks.exclude(pk=self.instance.pk)
        self.fields["parent_task"].queryset = parent_tasks
        self.fields["parent_task"].empty_label = "No parent task"
        related_tasks = Task.objects.filter(is_archived=False).order_by("created_at", "pk")
        if self.instance.pk:
            related_tasks = related_tasks.exclude(pk=self.instance.pk)
        self.fields["related_tasks"].queryset = related_tasks
        self.fields["boards"].queryset = Board.objects.filter(is_archived=False).order_by("name", "barcode")
        if self.instance.pk:
            self.fields["boards"].initial = Board.objects.filter(task_links__task=self.instance, task_links__released_at__isnull=True)
        if not score_enabled:
            self.fields.pop("score")

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        assignee = cleaned.get("assignee")
        boards = cleaned.get("boards")
        timeline_start = cleaned.get("timeline_start_date")
        due_date = cleaned.get("due_date")
        ancestor = cleaned.get("parent_task")
        related_tasks = cleaned.get("related_tasks")
        visited = {self.instance.pk} if self.instance.pk else set()
        while ancestor:
            if ancestor.pk in visited:
                self.add_error("parent_task", "A task cannot be its own parent or descendant.")
                break
            visited.add(ancestor.pk)
            ancestor = ancestor.parent_task
        if related_tasks:
            related_ids = {related.pk for related in related_tasks}
            parent = cleaned.get("parent_task")
            if parent and parent.pk in related_ids:
                self.add_error("related_tasks", "The Parent Task is already represented by the parent relationship.")
            if self.instance.pk:
                child_ids = set(self.instance.subtasks.filter(is_archived=False).values_list("pk", flat=True))
                if related_ids & child_ids:
                    self.add_error("related_tasks", "A Subtask is already represented by the parent relationship.")
        if timeline_start and due_date and due_date < timeline_start:
            self.add_error("due_date", "Due Date cannot be earlier than Start Date.")
        if status == Task.Status.DONE and not assignee:
            self.add_error("assignee", "An assignee is required before completing this task.")
        if status == Task.Status.DONE and self.instance.pk:
            open_action_count = open_action_count_blocking_done(
                self.instance, self.old_status, status,
            )
            if open_action_count:
                self.add_error(
                    "status",
                    f"Complete {open_action_count} open Action Item(s) before marking this task as Done.",
                )
        if boards and not assignee:
            self.add_error("boards", "Select an assignee before assigning boards.")
        if status in {Task.Status.PAUSED, Task.Status.CANCELLED} and status != self.old_status and not cleaned.get("status_note", "").strip():
            self.add_error("status_note", "A note is required for Paused and Cancelled tasks.")
        if self.old_status == Task.Status.DONE and status == Task.Status.DONE and self.instance.pk:
            original = Task.objects.filter(pk=self.instance.pk).values_list("assignee_id", flat=True).first()
            if original != getattr(assignee, "pk", None):
                self.add_error("assignee", "Reopen the task before changing its assignee.")
        return cleaned


class TimelineTaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["timeline_start_date", "due_date", "timeline_group"]
        widgets = {
            "timeline_start_date": DateInput(),
            "due_date": DateInput(),
        }
        labels = {
            "timeline_start_date": "Start Date",
            "due_date": "Due Date",
            "timeline_group": "Timeline Group",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timeline_group"].queryset = TimelineGroup.objects.order_by("position", "name")
        self.fields["timeline_group"].empty_label = "Ungrouped"

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("timeline_start_date")
        due_date = cleaned.get("due_date")
        if start_date and due_date and due_date < start_date:
            self.add_error("due_date", "Due Date cannot be earlier than Start Date.")
        return cleaned


class TimelineGroupForm(forms.ModelForm):
    class Meta:
        model = TimelineGroup
        fields = ["name", "color"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class TimelineHolidayForm(forms.ModelForm):
    color = forms.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$",
        widget=forms.TextInput(attrs={"type": "color"}),
        error_messages={"invalid": "Enter a valid hex color."},
    )

    class Meta:
        model = TimelineHoliday
        fields = ["name", "color", "start_date", "end_date", "repeat_annually", "notes"]
        widgets = {
            "start_date": DateInput(),
            "end_date": DateInput(),
            "notes": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "End Date cannot be earlier than Start Date.")
        return cleaned


class TimelineMilestoneForm(forms.ModelForm):
    color = forms.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$",
        widget=forms.TextInput(attrs={"type": "color"}),
        error_messages={"invalid": "Enter a valid hex color."},
    )

    class Meta:
        model = TimelineMilestone
        fields = ["name", "date", "color", "timeline_group", "scopes", "notes"]
        widgets = {
            "date": DateInput(),
            "scopes": forms.SelectMultiple(attrs={"size": 4}),
            "notes": forms.TextInput(attrs={"placeholder": "Optional note"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timeline_group"].queryset = TimelineGroup.objects.order_by("position", "name")
        self.fields["timeline_group"].empty_label = "Global milestone"
        scopes = Scope.objects.filter(is_active=True)
        if self.instance and self.instance.pk:
            scopes = Scope.objects.filter(Q(is_active=True) | Q(timeline_milestones=self.instance))
        self.fields["scopes"].queryset = scopes.distinct().order_by("position", "name")
        self.fields["scopes"].required = False


class MinuteWriterRotationForm(forms.ModelForm):
    writers_order = forms.CharField(widget=forms.HiddenInput(), label="Minute Writers")

    class Meta:
        model = MinuteWriterRotation
        fields = ["name", "anchor_date", "is_active", "writers_order"]
        widgets = {"anchor_date": DateInput()}
        labels = {"anchor_date": "Rotation Start Date", "is_active": "Active"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        existing_ids = []
        if self.instance and self.instance.pk:
            existing_ids = list(
                self.instance.writer_members.order_by("position", "pk").values_list("user_id", flat=True)
            )
        if not self.is_bound:
            self.initial["writers_order"] = ",".join(str(user_id) for user_id in existing_ids)
        self.writer_options = list(
            User.objects.filter(
                Q(account_status=User.AccountStatus.ACTIVE) | Q(pk__in=existing_ids)
            ).distinct().order_by("display_name", "username")
        )
        self._writer_options_by_id = {user.pk: user for user in self.writer_options}
        self.writer_users = []

    def clean_writers_order(self):
        raw_value = self.cleaned_data.get("writers_order", "")
        raw_ids = [value.strip() for value in raw_value.split(",") if value.strip()]
        try:
            writer_ids = [int(value) for value in raw_ids]
        except ValueError as exc:
            raise ValidationError("The writer list is invalid.") from exc
        if not writer_ids:
            raise ValidationError("Add at least one Minute Writer.")
        if len(writer_ids) != len(set(writer_ids)):
            raise ValidationError("Each Minute Writer can appear only once.")
        missing_ids = [user_id for user_id in writer_ids if user_id not in self._writer_options_by_id]
        if missing_ids:
            raise ValidationError("One or more selected writers are unavailable.")
        self.writer_users = [self._writer_options_by_id[user_id] for user_id in writer_ids]
        if any(user.account_status != User.AccountStatus.ACTIVE for user in self.writer_users):
            raise ValidationError("All Minute Writers must have active accounts.")
        return ",".join(str(user_id) for user_id in writer_ids)

    def save(self, commit=True):
        old_writer_ids = []
        if self.instance.pk and self.instance.last_assigned_writer_id:
            old_writer_ids = list(
                self.instance.writer_members.order_by("position", "pk")
                .values_list("user_id", flat=True)
            )
        rotation = super().save(commit=commit)
        if commit:
            rotation.writer_members.all().delete()
            MinuteWriterRotationMember.objects.bulk_create([
                MinuteWriterRotationMember(rotation=rotation, user=user, position=position)
                for position, user in enumerate(self.writer_users, start=1)
            ])
            writer_ids = [user.pk for user in self.writer_users]
            if rotation.last_assigned_writer_id and rotation.last_assigned_writer_id not in writer_ids:
                if rotation.last_assigned_writer_id in old_writer_ids:
                    old_last_index = old_writer_ids.index(rotation.last_assigned_writer_id)
                    old_successors = (
                        old_writer_ids[old_last_index + 1:] + old_writer_ids[:old_last_index]
                    )
                    next_writer_id = next(
                        (user_id for user_id in old_successors if user_id in writer_ids),
                        writer_ids[0],
                    )
                else:
                    next_writer_id = writer_ids[0]
                next_index = writer_ids.index(next_writer_id)
                rotation.last_assigned_writer = self.writer_users[next_index - 1]
                rotation.save(update_fields=["last_assigned_writer", "updated_at"])
        return rotation


class MeetingForm(forms.ModelForm):
    class Meta:
        model = Meeting
        fields = ["title", "meeting_date", "host", "writer_assignment", "writer_rotation", "minute_taker"]
        widgets = {"meeting_date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_users = User.objects.filter(account_status=User.AccountStatus.ACTIVE).order_by("display_name", "username")
        self.fields["host"].queryset = active_users
        self.fields["minute_taker"].queryset = active_users
        self.fields["minute_taker"].required = False
        self.fields["minute_taker"].label = "Minute Writer"
        existing_rotation_id = self.instance.writer_rotation_id if self.instance and self.instance.pk else None
        self.fields["writer_rotation"].queryset = MinuteWriterRotation.objects.filter(
            Q(is_active=True) | Q(pk=existing_rotation_id)
        ).order_by("name")
        self.fields["writer_assignment"].label = "Writer Assignment"
        self.fields["writer_rotation"].label = "Writer Rotation"
        self.fields["writer_assignment"].help_text = "Automatic assigns the next writer in the rotation when the meeting is saved."
        self.fields["writer_rotation"].help_text = "Optional for Manual assignment; required for Automatic Rotation."

    def clean(self):
        cleaned = super().clean()
        assignment = cleaned.get("writer_assignment")
        rotation = cleaned.get("writer_rotation")
        meeting_date = cleaned.get("meeting_date")
        minute_taker = cleaned.get("minute_taker")
        if assignment == Meeting.WriterAssignment.AUTOMATIC:
            if not rotation:
                self.add_error("writer_rotation", "Select a Writer Rotation for automatic assignment.")
            elif meeting_date:
                keep_existing_writer = (
                    self.instance.pk
                    and self.instance.writer_assignment == Meeting.WriterAssignment.AUTOMATIC
                    and self.instance.writer_rotation_id == rotation.pk
                )
                if not rotation.is_active and not keep_existing_writer:
                    self.add_error("writer_rotation", "Select an active Writer Rotation.")
                elif meeting_date < rotation.anchor_date and not (
                    keep_existing_writer
                    and self.instance.meeting_date < rotation.anchor_date
                    and meeting_date == self.instance.meeting_date
                ):
                    self.add_error("writer_rotation", "This rotation has not started for the selected meeting date.")
                else:
                    scheduled_writer = self.instance.minute_taker if keep_existing_writer else rotation.writer_for(meeting_date)
                    if scheduled_writer:
                        cleaned["minute_taker"] = scheduled_writer
                    else:
                        self.add_error("writer_rotation", "This rotation has no active Minute Writers.")
        elif not minute_taker:
            self.add_error("minute_taker", "Select a Minute Writer for manual assignment.")
        return cleaned
