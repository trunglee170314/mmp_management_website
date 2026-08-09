from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django import forms
from .models import ActionItem, AuditLog, Board, BoardAssignment, Meeting, MeetingTask, PasswordResetRequest, RelationshipEdge, RelationshipGroup, RelationshipMap, RelationshipMapRevision, RelationshipNode, Scope, SystemSetting, Task, TaskBoard, TaskHistory, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Application", {"fields": ("display_name", "role", "account_status", "theme_preference", "reviewed_by", "reviewed_at", "review_note")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Application", {"fields": ("display_name", "role", "account_status")}),)
    list_display = ["username", "display_name", "role", "account_status", "is_staff"]


class AdminTaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = "__all__"

class AdminActionItemForm(forms.ModelForm):
    class Meta:
        model = ActionItem
        fields = "__all__"

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = AdminTaskForm


@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    form = AdminActionItemForm


class RelationshipReadOnlyAdmin(admin.ModelAdmin):
    """Keep System Map recovery invariants by routing mutations through the app UI."""

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.concrete_fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RelationshipMap)
class RelationshipMapAdmin(RelationshipReadOnlyAdmin):
    list_display = ["name", "is_deleted", "created_by", "updated_at"]
    list_filter = ["is_deleted"]


@admin.register(RelationshipMapRevision)
class RelationshipMapRevisionAdmin(RelationshipReadOnlyAdmin):
    list_display = ["relationship_map", "action", "actor", "created_at"]
    list_select_related = ["relationship_map", "actor"]


@admin.register(RelationshipGroup, RelationshipNode, RelationshipEdge)
class RelationshipEntityAdmin(RelationshipReadOnlyAdmin):
    pass


admin.site.register([Scope, Board, BoardAssignment, TaskBoard, TaskHistory, Meeting, MeetingTask, AuditLog, PasswordResetRequest, SystemSetting])
