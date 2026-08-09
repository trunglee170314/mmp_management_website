from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django import forms
from .business_rules import action_item_would_be_open_on_done_task, open_action_count_blocking_done
from .models import ActionItem, AuditLog, Board, BoardAssignment, Meeting, MeetingTask, PasswordResetRequest, Scope, SystemSetting, Task, TaskBoard, TaskHistory, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Application", {"fields": ("display_name", "role", "account_status", "reviewed_by", "reviewed_at", "review_note")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Application", {"fields": ("display_name", "role", "account_status")}),)
    list_display = ["username", "display_name", "role", "account_status", "is_staff"]


class AdminTaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        self.old_status = kwargs.get("instance").status if kwargs.get("instance") and kwargs.get("instance").pk else None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        count = open_action_count_blocking_done(
            self.instance, self.old_status, cleaned.get("status"),
        )
        if count:
            self.add_error("status", f"Complete {count} open Action Item(s) before marking this task as Done.")
        return cleaned


class AdminActionItemForm(forms.ModelForm):
    class Meta:
        model = ActionItem
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if action_item_would_be_open_on_done_task(cleaned.get("task"), cleaned.get("is_completed", False)):
            self.add_error("is_completed", "Reopen the task before adding or reopening an Action Item.")
        return cleaned


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = AdminTaskForm


@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    form = AdminActionItemForm


admin.site.register([Scope, Board, BoardAssignment, TaskBoard, TaskHistory, Meeting, MeetingTask, AuditLog, PasswordResetRequest, SystemSetting])
