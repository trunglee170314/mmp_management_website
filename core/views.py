from calendar import monthrange
from datetime import date, timedelta
import json
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.password_validation import get_default_password_validators
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DateTimeField, Exists, Max, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce, ExtractYear
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import AdminPasswordResetForm, BoardForm, ForgotPasswordRequestForm, MeetingForm, MinuteWriterRotationForm, ProfileForm, RegistrationForm, ScopeForm, TaskForm, TimelineGroupForm, TimelineHolidayForm, TimelineMilestoneForm, TimelineTaskForm, UserCreateForm, UserDeactivationForm, UserEditForm
from .models import ActionItem, AuditLog, Board, BoardAssignment, Meeting, MeetingTask, MinuteWriterRotation, PasswordResetRequest, Scope, SystemSetting, Task, TaskBoard, TaskHistory, TimelineGroup, TimelineHoliday, TimelineMilestone, User
from .services import assign_next_minute_writer, deactivate_user_and_transfer, freeze_meeting_entry, frozen_meeting_entry_actions, initialize_meeting_tasks, lock_responsibility_transfer_mutex, log_related_task_changes, log_scope_changes, log_task_changes, meeting_entries_action_groups, previous_meeting_completed_after, publish_meeting_actions, record_audit, release_all_task_boards, set_manual_board_user, sync_task_boards, task_snapshot, user_responsibility_counts


VISIBLE_ACTION_HISTORY_EVENTS = (
    "Action item completed",
    "Action item reopened",
)


def with_latest_status_change(queryset):
    latest_status_change = TaskHistory.objects.filter(
        task_id=OuterRef("pk"),
        event="Status changed",
    ).order_by("-created_at", "-pk")
    return queryset.annotate(
        inactive_at=Coalesce(
            Subquery(latest_status_change.values("created_at")[:1], output_field=DateTimeField()),
            "completed_at",
            "created_at",
        ),
    ).annotate(inactive_year=ExtractYear("inactive_at"))


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_admin:
            return HttpResponseForbidden("Admin access required.")
        return view(request, *args, **kwargs)
    return wrapped


def can_record_meeting(user, meeting):
    return (
        meeting.status == Meeting.Status.DRAFT
        and user.is_authenticated
        and user.account_status == User.AccountStatus.ACTIVE
    )


def can_review_meeting(user, meeting):
    return meeting.status == Meeting.Status.DRAFT and meeting.host_id == user.pk


def wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def safe_redirect(request, target, fallback):
    return redirect(safe_redirect_url(request, target, fallback))


def safe_redirect_url(request, target, fallback):
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return reverse(fallback)


def can_toggle_action_item(user, item):
    return (
        item.published_at is not None
        and user.is_authenticated
        and user.account_status == User.AccountStatus.ACTIVE
        and not (
            item.is_completed
            and item.task_id
            and item.task.status == Task.Status.DONE
        )
    )


def positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def pagination_query(request):
    query = request.GET.copy()
    query.pop("page", None)
    return query.urlencode()


def meeting_order_payload(meeting):
    return list(meeting.task_entries.order_by("position").values("id", "position"))


def serialize_action_item(item):
    completed_at = timezone.localtime(item.completed_at) if item.completed_at else None
    data = {
        "id": item.pk,
        "content": item.content,
        "assignee": str(item.assignee) if item.assignee else "Unassigned",
        "assignee_id": item.assignee_id or "",
        "due_date": item.due_date.isoformat() if item.due_date else "",
        "due_label": item.due_date.strftime("%d %b") if item.due_date else "",
        "is_completed": item.is_completed,
        "created_at": item.created_at.isoformat(),
        "completed_by": str(item.completed_by) if item.completed_by else "",
        "completed_label": completed_at.strftime("%d %b") if completed_at else "",
        "completed_date": completed_at.strftime("%d %b %Y") if completed_at else "",
        "completed_display": completed_at.strftime("%d %b %Y, %H:%M") if completed_at else "",
        "is_published": item.published_at is not None,
    }
    if item.meeting_id:
        data.update({
            "edit_url": reverse("meeting_action_update", args=[item.meeting_id, item.pk]),
            "delete_url": reverse("meeting_action_delete", args=[item.meeting_id, item.pk]),
        })
    return data


def parse_meeting_action_payload(request):
    content = request.POST.get("new_action_item", request.POST.get("content", "")).strip()
    if not content:
        return None, None, None, "Action Item content is required."
    if len(content) > 300:
        return None, None, None, "Action Item must be 300 characters or fewer."

    assignee_id = request.POST.get("action_assignee", request.POST.get("assignee", ""))
    assignee = None
    if assignee_id:
        assignee = User.objects.filter(pk=assignee_id, account_status=User.AccountStatus.ACTIVE).first()
        if not assignee:
            return None, None, None, "Select an active assignee."

    due_value = request.POST.get("action_due_date", request.POST.get("due_date", ""))
    due_date = parse_date(due_value) if due_value else None
    if due_value and not due_date:
        return None, None, None, "Enter a valid due date."
    return content, assignee, due_date, None


def touch_meeting_action_entry(meeting, item, user):
    updated_at = timezone.now()
    MeetingTask.objects.filter(meeting=meeting, task_id=item.task_id).update(updated_by=user, updated_at=updated_at)
    Meeting.objects.filter(pk=meeting.pk).update(updated_at=updated_at)
    return updated_at


def history_change_kind(action):
    """Return the shared visual treatment for history events."""
    action = action.lower()
    if any(word in action for word in ("released", "removed", "deleted")):
        return "removed"
    if any(word in action for word in ("assigned", "added", "created", "reopened")):
        return "added"
    return "modified"


def prepare_audit_history(queryset, field_labels=None):
    """Add template-friendly diff rows without changing stored audit data."""
    history = list(queryset)
    field_labels = field_labels or {}
    for item in history:
        item.change_kind = history_change_kind(item.action)
        item.change_rows = []
        details = item.details if isinstance(item.details, dict) else {}
        changes = details.get("changes", {})
        if not isinstance(changes, dict):
            continue
        for field, values in changes.items():
            if not isinstance(values, dict):
                continue
            item.change_rows.append({
                "label": field_labels.get(field, field.replace("_", " ").title()),
                "old": values.get("from", ""),
                "new": values.get("to", ""),
            })
    return history


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return render(request, "registration/register_done.html")
    return render(request, "registration/register.html", {"form": form})


def registration_password_check(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    password = str(data.get("password", ""))
    results = {"similarity": False, "length": False, "common": False, "numeric": False}
    if not password:
        return JsonResponse(results)
    candidate = User(
        username=str(data.get("username", "")),
        email=str(data.get("email", "")),
        display_name=str(data.get("display_name", "")),
        first_name=str(data.get("display_name", "")),
    )
    validator_keys = {
        "UserAttributeSimilarityValidator": "similarity",
        "MinimumLengthValidator": "length",
        "CommonPasswordValidator": "common",
        "NumericPasswordValidator": "numeric",
    }
    for validator in get_default_password_validators():
        key = validator_keys.get(validator.__class__.__name__)
        if not key:
            continue
        try:
            validator.validate(password, candidate)
        except Exception:
            results[key] = False
        else:
            results[key] = True
    return JsonResponse(results)


def forgot_password_request(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = ForgotPasswordRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.filter(username__iexact=form.cleaned_data["username"], account_status=User.AccountStatus.ACTIVE).first()
        if user:
            reset_request, created = PasswordResetRequest.objects.get_or_create(
                user=user,
                status=PasswordResetRequest.Status.PENDING,
            )
            if created:
                record_audit(None, user, "Password reset requested")
        return redirect("forgot_password_done")
    return render(request, "registration/forgot_password.html", {"form": form})


def forgot_password_done(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "registration/forgot_password_done.html")


@login_required
def dashboard(request):
    today = timezone.localdate()
    active_tasks = Task.objects.filter(is_archived=False).exclude(
        status__in=[Task.Status.DONE, Task.Status.CANCELLED]
    )
    open_actions = ActionItem.objects.filter(is_completed=False, published_at__isnull=False)
    active_users = User.objects.filter(account_status=User.AccountStatus.ACTIVE).order_by(
        "display_name", "username", "pk"
    )
    dashboard_view = request.GET.get("view", "team" if request.user.is_admin else "mine")
    if not request.user.is_admin or dashboard_view != "team":
        dashboard_view = "mine"

    my_tasks = active_tasks.filter(assignee=request.user)
    my_actions = open_actions.filter(assignee=request.user).select_related(
        "task",
    ).prefetch_related("task__scopes").order_by("created_at", "pk")
    my_scope_rows = list(Scope.objects.filter(is_active=True).annotate(
        active_count=Count(
            "tasks",
            filter=(
                Q(tasks__is_archived=False, tasks__assignee=request.user)
                & ~Q(tasks__status__in=[Task.Status.DONE, Task.Status.CANCELLED])
            ),
            distinct=True,
        ),
    ))
    max_my_scope = max([row.active_count for row in my_scope_rows] + [1])
    for row in my_scope_rows:
        row.bar_percent = round(row.active_count / max_my_scope * 100)

    context = {
        "dashboard_view": dashboard_view,
        "is_team_overview": dashboard_view == "team",
        "my_actions": my_actions[:6],
        "scope_rows": my_scope_rows,
        "recent_tasks": my_tasks.select_related("assignee").prefetch_related("scopes")[:6],
        "current_year": today.year,
    }

    if dashboard_view == "mine":
        context.update({
            "active_task_count": my_tasks.count(),
            "completed_task_count": Task.objects.filter(
                is_archived=False,
                status=Task.Status.DONE,
                completed_by=request.user,
                completed_at__year=today.year,
            ).count(),
            "overdue_count": my_tasks.filter(due_date__lt=today).count(),
            "open_action_count": my_actions.count(),
        })
        return render(request, "dashboard.html", context)

    inactive_base = with_latest_status_change(Task.objects.filter(
        is_archived=False,
        status__in=[Task.Status.DONE, Task.Status.CANCELLED],
    ))
    inactive_year_values = set(
        inactive_base.values_list("inactive_year", flat=True).distinct()
    )
    inactive_year_values.discard(None)
    inactive_year_values.add(today.year)
    available_years = sorted(inactive_year_values, reverse=True)
    try:
        selected_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        selected_year = today.year
    if selected_year not in inactive_year_values:
        selected_year = today.year

    inactive_assignee_ids = inactive_base.filter(
        inactive_year=selected_year,
        assignee__isnull=False,
    ).order_by().values_list("assignee_id", flat=True)
    member_filter_options = User.objects.filter(
        Q(account_status=User.AccountStatus.ACTIVE) | Q(pk__in=inactive_assignee_ids)
    ).distinct().order_by("display_name", "username", "pk")

    selected_member = None
    selected_member_id = request.GET.get("member", "").strip()
    if selected_member_id:
        selected_member = member_filter_options.filter(pk=selected_member_id).first()
        if selected_member is None:
            selected_member_id = ""

    inactive_tasks = inactive_base.filter(inactive_year=selected_year)
    if selected_member:
        inactive_tasks = inactive_tasks.filter(assignee=selected_member)

    completed_tasks = inactive_tasks.filter(status=Task.Status.DONE)
    scope_counts = dict(
        completed_tasks.values("scopes__id")
        .exclude(scopes__id__isnull=True)
        .annotate(total=Count("id"))
        .values_list("scopes__id", "total")
    )
    unscoped_completed_count = completed_tasks.filter(scopes__isnull=True).count()
    chart_scopes = Scope.objects.filter(pk__in=scope_counts).order_by("position", "name")
    scope_chart_rows = []
    scope_assignment_count = sum(scope_counts.values()) + unscoped_completed_count
    for scope in chart_scopes:
        count = scope_counts[scope.pk]
        percent = count / scope_assignment_count * 100 if scope_assignment_count else 0
        scope_chart_rows.append({
            "id": scope.pk,
            "name": scope.name,
            "color": scope.color,
            "count": count,
            "percent": percent,
        })
    if unscoped_completed_count:
        scope_chart_rows.append({
            "id": None,
            "name": "Uncategorized",
            "color": "#918D99",
            "count": unscoped_completed_count,
            "percent": unscoped_completed_count / scope_assignment_count * 100,
        })
    scope_chart_rows.sort(key=lambda row: (-row["count"], row["name"].lower()))

    scope_pie_position = 0.0
    scope_pie_segments = []
    for row in scope_chart_rows:
        next_position = scope_pie_position + row["percent"]
        scope_pie_segments.append(
            f"{row['color']} {scope_pie_position:.2f}% {next_position:.2f}%"
        )
        scope_pie_position = next_position

    inactive_task_count = inactive_tasks.count()
    inactive_status_counts = dict(
        inactive_tasks.values("status").annotate(total=Count("id")).values_list("status", "total")
    )
    inactive_status_rows = []
    status_colors = {
        Task.Status.DONE: "#21865F",
        Task.Status.CANCELLED: "#C75555",
    }
    status_labels = dict(Task.Status.choices)
    for status in (Task.Status.DONE, Task.Status.CANCELLED):
        count = inactive_status_counts.get(status, 0)
        if not count:
            continue
        percent = count / inactive_task_count * 100 if inactive_task_count else 0
        inactive_status_rows.append({
            "status": status,
            "name": status_labels[status],
            "color": status_colors[status],
            "count": count,
            "percent": percent,
        })

    pie_position = 0.0
    pie_segments = []
    for row in inactive_status_rows:
        next_position = pie_position + row["percent"]
        pie_segments.append(f"{row['color']} {pie_position:.2f}% {next_position:.2f}%")
        pie_position = next_position

    member_rows = list(member_filter_options)
    member_ids = [member.pk for member in member_rows]
    active_member_tasks = Task.objects.filter(
        assignee_id__in=member_ids,
        is_archived=False,
    ).exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
    active_member_counts = dict(
        active_member_tasks.values("assignee_id")
        .annotate(total=Count("id"))
        .values_list("assignee_id", "total")
    )
    overdue_member_counts = dict(
        active_member_tasks.filter(due_date__lt=today)
        .values("assignee_id")
        .annotate(total=Count("id"))
        .values_list("assignee_id", "total")
    )
    open_action_member_counts = dict(
        ActionItem.objects.filter(
            assignee_id__in=member_ids,
            is_completed=False,
            published_at__isnull=False,
        ).values("assignee_id")
        .annotate(total=Count("id"))
        .values_list("assignee_id", "total")
    )
    board_member_counts = dict(
        BoardAssignment.objects.filter(
            user_id__in=member_ids,
            released_at__isnull=True,
            board__is_archived=False,
        ).values("user_id")
        .annotate(total=Count("board_id", distinct=True))
        .values_list("user_id", "total")
    )
    member_inactive_counts = dict(
        inactive_base.filter(inactive_year=selected_year, assignee__isnull=False)
        .values("assignee_id")
        .annotate(total=Count("id"))
        .values_list("assignee_id", "total")
    )
    for member in member_rows:
        member.active_count = active_member_counts.get(member.pk, 0)
        member.overdue_count = overdue_member_counts.get(member.pk, 0)
        member.open_action_count = open_action_member_counts.get(member.pk, 0)
        member.board_count = board_member_counts.get(member.pk, 0)
        member.inactive_count = member_inactive_counts.get(member.pk, 0)
    member_rows.sort(key=lambda member: (
        -member.overdue_count,
        -member.active_count,
        member.display_name.lower(),
        member.username.lower(),
        member.pk,
    ))

    board_palette = [
        "#4F3BE7", "#21865F", "#C27C2C", "#C75555", "#3978BD",
        "#7957B8", "#168B8B", "#8B6B3D", "#607D3B", "#8B5876",
    ]
    active_boards = Board.objects.filter(is_archived=False)
    board_name_counts = list(
        active_boards.order_by().values("name")
        .annotate(count=Count("id")).order_by("name")
    )
    board_total = sum(row["count"] for row in board_name_counts)
    board_chart_rows = []
    board_pie_segments = []
    board_pie_position = 0.0
    for index, row in enumerate(board_name_counts):
        percent = row["count"] / board_total * 100 if board_total else 0
        color = board_palette[index % len(board_palette)]
        board_chart_rows.append({**row, "color": color, "percent": percent})
        next_position = board_pie_position + percent
        board_pie_segments.append(
            f"{color} {board_pie_position:.2f}% {next_position:.2f}%"
        )
        board_pie_position = next_position

    boards_with_assignment_count = active_boards.annotate(
        active_assignee_count=Count(
            "assignments__user",
            filter=Q(assignments__released_at__isnull=True),
            distinct=True,
        ),
    )
    board_availability = {
        "available": boards_with_assignment_count.filter(active_assignee_count=0).count(),
        "assigned": boards_with_assignment_count.filter(active_assignee_count=1).count(),
        "shared": boards_with_assignment_count.filter(active_assignee_count__gte=2).count(),
    }

    overdue_task_count = active_tasks.filter(due_date__lt=today).count()
    paused_task_count = active_tasks.filter(status=Task.Status.PAUSED).count()
    context.update({
        "active_task_count": active_tasks.count(),
        "overdue_count": overdue_task_count,
        "open_action_count": open_actions.count(),
        "paused_count": paused_task_count,
        "available_years": available_years,
        "selected_year": selected_year,
        "selected_member": selected_member,
        "selected_member_id": selected_member_id,
        "member_filter_options": member_filter_options,
        "scope_chart_rows": scope_chart_rows,
        "inactive_status_rows": inactive_status_rows,
        "inactive_task_count": inactive_task_count,
        "status_pie_gradient": f"conic-gradient({', '.join(pie_segments)})" if pie_segments else "",
        "scope_pie_gradient": f"conic-gradient({', '.join(scope_pie_segments)})" if scope_pie_segments else "",
        "scope_assignment_count": scope_assignment_count,
        "member_rows": member_rows,
        "board_chart_rows": board_chart_rows,
        "board_chart_total": board_total,
        "board_pie_gradient": f"conic-gradient({', '.join(board_pie_segments)})" if board_pie_segments else "",
        "board_availability": board_availability,
    })
    return render(request, "dashboard.html", context)


@admin_required
def user_list(request):
    users = User.objects.annotate(
        board_count=Count("board_assignments__board", filter=Q(board_assignments__released_at__isnull=True), distinct=True),
    ).order_by("date_joined", "pk")
    reset_requests = PasswordResetRequest.objects.filter(status=PasswordResetRequest.Status.PENDING).select_related("user")
    return render(request, "users/list.html", {"users": users, "reset_requests": reset_requests})


@admin_required
def user_add(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.reviewed_by = request.user
        user.reviewed_at = timezone.now()
        user.save()
        record_audit(request.user, user, "User created")
        messages.success(request, "User created successfully.")
        return redirect("users")
    return render(request, "generic_form.html", {"form": form, "title": "Add User", "subtitle": "Create an active account.", "cancel_url": "users"})


@login_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if not request.user.is_admin and request.user.pk != user.pk:
        return HttpResponseForbidden("You can only edit your own profile.")
    if request.user.is_admin:
        form = UserEditForm(request.POST or None, instance=user)
    else:
        form = ProfileForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        if request.user.is_admin and user.is_admin and user.account_status == User.AccountStatus.ACTIVE:
            removing_admin = form.cleaned_data.get("role") != User.Role.ADMIN
            remaining_admins = User.objects.filter(
                Q(role=User.Role.ADMIN) | Q(is_superuser=True),
                account_status=User.AccountStatus.ACTIVE,
            ).exclude(pk=user.pk)
            if removing_admin and not remaining_admins.exists():
                form.add_error(None, "The last active Admin cannot be changed to Member.")
                return render(request, "generic_form.html", {"form": form, "title": "Edit User", "subtitle": str(user), "cancel_url": "users"})
        changed = list(form.changed_data)
        form.save()
        record_audit(request.user, user, "User profile updated", {"fields": changed})
        messages.success(request, "User updated successfully.")
        return redirect("users" if request.user.is_admin else "profile")
    return render(request, "generic_form.html", {"form": form, "title": "Edit User", "subtitle": str(user), "cancel_url": "users" if request.user.is_admin else "profile"})


@admin_required
def user_action(request, pk):
    if request.method != "POST":
        return redirect("users")
    user = get_object_or_404(User, pk=pk)
    action = request.POST.get("action")
    transfer_completed = False
    if action == "approve":
        if user.account_status != User.AccountStatus.PENDING:
            if wants_json(request):
                return JsonResponse({"error": "Only pending registrations can be approved."}, status=400)
            messages.error(request, "Only pending registrations can be approved.")
            return redirect("users")
        user.account_status = User.AccountStatus.ACTIVE
        user.role = User.Role.MEMBER
        text = "Registration approved"
    elif action == "reject":
        if user.account_status != User.AccountStatus.PENDING:
            if wants_json(request):
                return JsonResponse({"error": "Only pending registrations can be rejected."}, status=400)
            messages.error(request, "Only pending registrations can be rejected.")
            return redirect("users")
        user.account_status = User.AccountStatus.REJECTED
        text = "Registration rejected"
    elif action == "activate":
        if user.account_status != User.AccountStatus.INACTIVE:
            if wants_json(request):
                return JsonResponse({"error": "Only inactive users can be activated."}, status=400)
            messages.error(request, "Only inactive users can be activated.")
            return redirect("users")
        user.account_status = User.AccountStatus.ACTIVE
        text = "User activated"
    elif action == "deactivate":
        if not request.POST.get("transfer_to"):
            if wants_json(request):
                return JsonResponse({
                    "error": "Select an active Admin to receive this user's responsibilities.",
                    "transfer_url": reverse("user_deactivate", args=[user.pk]),
                }, status=400)
            return redirect("user_deactivate", pk=user.pk)
        form = UserDeactivationForm(request.POST, user=user)
        if not form.is_valid():
            error = next(iter(form.errors.values()))[0]
            if wants_json(request):
                return JsonResponse({"error": error}, status=400)
            messages.error(request, error)
            return redirect("user_deactivate", pk=user.pk)
        try:
            deactivate_user_and_transfer(user, form.cleaned_data["transfer_to"], request.user)
        except ValueError as exc:
            if wants_json(request):
                return JsonResponse({"error": str(exc)}, status=400)
            messages.error(request, str(exc))
            return redirect("user_deactivate", pk=user.pk)
        text = "User deactivated and responsibilities transferred"
        user.refresh_from_db()
        transfer_completed = True
    else:
        if wants_json(request):
            return JsonResponse({"error": "Unknown action."}, status=400)
        messages.error(request, "Unknown action.")
        return redirect("users")
    if not transfer_completed:
        user.reviewed_by = request.user
        user.reviewed_at = timezone.now()
        user.review_note = request.POST.get("review_note", "")
        user.save()
        record_audit(request.user, user, text, {"note": user.review_note})
    if wants_json(request):
        next_action = "deactivate" if user.account_status == User.AccountStatus.ACTIVE else "activate"
        return JsonResponse({
            "updated": True,
            "status": user.account_status,
            "status_label": user.get_account_status_display(),
            "next_action": next_action,
            "next_action_label": next_action.title(),
            "message": text + ".",
        })
    messages.success(request, text + ".")
    return redirect("users")


@admin_required
def user_deactivate(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.account_status != User.AccountStatus.ACTIVE:
        messages.error(request, "Only an active user can be deactivated.")
        return redirect("users")
    counts = user_responsibility_counts(user)
    form = UserDeactivationForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        try:
            deactivate_user_and_transfer(user, form.cleaned_data["transfer_to"], request.user)
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "User deactivated and responsibilities transferred.")
            return redirect("users")
    return render(request, "users/deactivate.html", {
        "target_user": user,
        "form": form,
        "responsibility_counts": counts,
    })


@admin_required
def password_reset_review(request, pk):
    reset_request = get_object_or_404(PasswordResetRequest.objects.select_related("user"), pk=pk, status=PasswordResetRequest.Status.PENDING)
    form = AdminPasswordResetForm(request.POST or None, user=reset_request.user)
    if request.method == "POST" and form.is_valid():
        reset_request.user.set_password(form.cleaned_data["new_password1"])
        reset_request.user.save(update_fields=["password"])
        reset_request.status = PasswordResetRequest.Status.COMPLETED
        reset_request.reviewed_by = request.user
        reset_request.reviewed_at = timezone.now()
        reset_request.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        record_audit(request.user, reset_request.user, "Password reset completed")
        messages.success(request, f"Password reset completed for {reset_request.user}.")
        return redirect("users")
    return render(request, "generic_form.html", {
        "form": form,
        "title": "Reset Password",
        "subtitle": f"Set a new password for {reset_request.user} (@{reset_request.user.username}).",
        "cancel_url": "users",
    })


@admin_required
def password_reset_reject(request, pk):
    if request.method == "POST":
        reset_request = get_object_or_404(PasswordResetRequest, pk=pk, status=PasswordResetRequest.Status.PENDING)
        reset_request.status = PasswordResetRequest.Status.REJECTED
        reset_request.reviewed_by = request.user
        reset_request.reviewed_at = timezone.now()
        reset_request.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        record_audit(request.user, reset_request.user, "Password reset request rejected")
        messages.success(request, "Password reset request rejected.")
    return redirect("users")


@login_required
def profile(request):
    return render(request, "users/profile.html")


@admin_required
def scope_settings(request):
    form = ScopeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        scope = form.save()
        record_audit(request.user, scope, "Scope created")
        messages.success(request, "Scope created successfully.")
        return redirect("scopes")
    scopes = Scope.objects.annotate(task_count=Count("tasks")).order_by("position", "pk")
    return render(request, "scopes/list.html", {"scopes": scopes, "form": form})


@admin_required
def scope_edit(request, pk):
    scope = get_object_or_404(Scope, pk=pk)
    form = ScopeForm(request.POST or None, instance=scope)
    if request.method == "POST" and form.is_valid():
        form.save()
        record_audit(request.user, scope, "Scope updated", {"fields": form.changed_data})
        messages.success(request, "Scope updated successfully.")
        return redirect("scopes")
    return render(request, "generic_form.html", {"form": form, "title": "Edit Scope", "subtitle": scope.name, "cancel_url": "scopes"})


@admin_required
@transaction.atomic
def scope_delete(request, pk):
    if request.method != "POST":
        return redirect("scopes")
    scope = get_object_or_404(Scope.objects.select_for_update(), pk=pk)
    task_count = scope.tasks.count()
    if task_count:
        messages.error(request, f'Cannot delete "{scope.name}" because it is used by {task_count} task(s). Archive it from Edit instead.')
        return redirect("scopes")
    scope_name = scope.name
    record_audit(request.user, scope, "Scope deleted")
    scope.delete()
    messages.success(request, f'Scope "{scope_name}" deleted successfully.')
    return redirect("scopes")


@login_required
def board_list(request):
    query = request.GET.get("q", "").strip()
    board_options = list(
        Board.objects.filter(is_archived=False)
        .values("name")
        .annotate(total=Count("id"))
        .order_by("name")
    )
    active_users = list(
        User.objects.filter(account_status=User.AccountStatus.ACTIVE).order_by("display_name")
    )
    valid_board_names = {option["name"] for option in board_options}
    selected_board_filters = [
        value for value in dict.fromkeys(request.GET.getlist("board"))
        if value in valid_board_names
    ]
    valid_user_ids = {member.pk for member in active_users}
    selected_assigned_user_ids = [
        user_id for value in dict.fromkeys(request.GET.getlist("assigned_user"))
        if (user_id := positive_int(value)) in valid_user_ids
    ]
    selected_assigned_user_filters = [str(user_id) for user_id in selected_assigned_user_ids]
    selected_assignment_filters = [
        value for value in dict.fromkeys(request.GET.getlist("assignment"))
        if value in {"assigned", "unassigned"}
    ]
    boards = Board.objects.filter(is_archived=False).select_related("updated_by")
    if query:
        boards = boards.filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(notes__icontains=query))
    if selected_board_filters:
        boards = boards.filter(name__in=selected_board_filters)
    if selected_assigned_user_ids:
        boards = boards.filter(
            assignments__user_id__in=selected_assigned_user_ids,
            assignments__released_at__isnull=True,
        ).distinct()
    active_assignment_exists = BoardAssignment.objects.filter(
        board_id=OuterRef("pk"),
        released_at__isnull=True,
    )
    if selected_assignment_filters == ["assigned"]:
        boards = boards.annotate(
            has_active_assignment=Exists(active_assignment_exists)
        ).filter(has_active_assignment=True)
    elif selected_assignment_filters == ["unassigned"]:
        boards = boards.annotate(
            has_active_assignment=Exists(active_assignment_exists)
        ).filter(has_active_assignment=False)
    boards = boards.order_by("created_at", "pk").prefetch_related(Prefetch(
        "assignments",
        queryset=BoardAssignment.objects.filter(released_at__isnull=True).select_related("user"),
        to_attr="active_assignments",
    ))
    page_obj = Paginator(boards, 50).get_page(request.GET.get("page"))
    boards = list(page_obj.object_list)
    for board in boards:
        users_by_id = {}
        for assignment in board.active_assignments:
            member = users_by_id.setdefault(assignment.user_id, assignment.user)
            if not hasattr(member, "manual_count"):
                member.manual_count = 0
                member.task_source_count = 0
            if assignment.source == BoardAssignment.Source.MANUAL:
                member.manual_count += 1
            else:
                member.task_source_count += 1
        board.active_user_list = sorted(
            users_by_id.values(),
            key=lambda member: (member.display_name.casefold(), member.pk),
        )
    return render(request, "boards/list.html", {
        "boards": boards,
        "active_users": active_users,
        "board_options": board_options,
        "query": query,
        "selected_assignment_filters": selected_assignment_filters,
        "selected_assigned_user_filters": selected_assigned_user_filters,
        "selected_board_filters": selected_board_filters,
        "selected_filter_count": (
            len(selected_assignment_filters)
            + len(selected_assigned_user_filters)
            + len(selected_board_filters)
        ),
        "page_obj": page_obj,
        "pagination_query": pagination_query(request),
    })


@login_required
def board_form(request, pk=None):
    return_target = (
        request.POST.get("next", "")
        if request.method == "POST"
        else request.GET.get("next", "") or request.META.get("HTTP_REFERER", "")
    )
    return_url = safe_redirect_url(request, return_target, "boards")
    board = get_object_or_404(Board, pk=pk, is_archived=False) if pk else None
    is_new = board is None
    previous_activity = board.last_activity if board else ""
    old_values = {field: getattr(board, field) for field in ["name", "barcode", "link_url", "notes"]} if board else {}
    form = BoardForm(request.POST or None, instance=board)
    if request.method == "POST" and form.is_valid():
        board = form.save(commit=False)
        if not board.pk:
            board.created_by = request.user
        board.updated_by = request.user
        labels = {"name": "Board Name", "barcode": "Barcode", "link_url": "Link", "notes": "Notes"}
        changed_labels = [labels[field] for field in form.changed_data if field in labels]
        board.last_activity = "Board created" if is_new else (f"Updated {', '.join(changed_labels)}" if changed_labels else previous_activity)
        board.save()
        changes = {field: {"from": old_values.get(field, ""), "to": getattr(board, field)} for field in form.changed_data if field in labels}
        record_audit(request.user, board, board.last_activity, {"changes": changes})
        messages.success(request, "Board saved successfully.")
        return redirect(return_url)
    return render(request, "generic_form.html", {
        "form": form,
        "title": "Edit Board" if board else "Add Board",
        "subtitle": "Manage board information and its external link.",
        "cancel_url": "boards",
        "cancel_href": return_url,
        "return_url": return_url,
    })


@admin_required
@transaction.atomic
def board_delete(request, pk):
    if request.method == "POST":
        board = get_object_or_404(Board, pk=pk, is_archived=False)
        released_at = timezone.now()
        board.task_links.filter(released_at__isnull=True).update(
            released_at=released_at,
            release_reason="Board archived",
        )
        board.assignments.filter(released_at__isnull=True).update(
            released_at=released_at,
            release_reason="Board archived",
        )
        board.is_archived = True
        board.updated_by = request.user
        board.last_activity = "Board archived"
        board.save()
        record_audit(request.user, board, "Board archived")
        messages.success(request, "Board archived and active assignments released.")
    return safe_redirect(
        request,
        request.POST.get("next", "") or request.META.get("HTTP_REFERER", ""),
        "boards",
    )


@login_required
def board_user_action(request, pk):
    if request.method != "POST":
        return redirect("boards")
    return_target = request.POST.get("next", "") or request.META.get("HTTP_REFERER", "")
    board = get_object_or_404(Board, pk=pk, is_archived=False)
    user_id = positive_int(request.POST.get("user_id"))
    if not user_id:
        messages.error(request, "Select a valid user.")
        return safe_redirect(request, return_target, "boards")
    user = get_object_or_404(User, pk=user_id, account_status=User.AccountStatus.ACTIVE)
    set_manual_board_user(board, user, request.user, assign=request.POST.get("action") != "remove")
    messages.success(request, "Board users updated.")
    return safe_redirect(request, return_target, "boards")


@login_required
def board_history(request, pk):
    board = get_object_or_404(Board, pk=pk)
    logs = prepare_audit_history(
        AuditLog.objects.filter(entity_type="Board", entity_id=board.pk).select_related("actor"),
        {"name": "Board", "barcode": "Barcode", "link_url": "Link", "redmine_url": "Link", "notes": "Notes"},
    )
    return render(request, "boards/history.html", {"board": board, "logs": logs})


def annual_holiday_start(source_date, year):
    try:
        return source_date.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def holiday_covers_day(holiday, day):
    if not holiday.repeat_annually:
        return holiday.start_date <= day <= holiday.end_date
    duration = holiday.end_date - holiday.start_date
    for year in (day.year - 1, day.year):
        occurrence_start = annual_holiday_start(holiday.start_date, year)
        if occurrence_start <= day <= occurrence_start + duration:
            return True
    return False


def timeline_redirect(request):
    return safe_redirect(request, request.POST.get("next", ""), "task_timeline")


def timeline_task_label(task):
    group_name = task.timeline_group.name if task.timeline_group_id else "Ungrouped"
    start_label = task.timeline_start_date.isoformat() if task.timeline_start_date else "None"
    due_label = task.due_date.isoformat() if task.due_date else "None"
    return f"Group: {group_name}; Start: {start_label}; Due: {due_label}"


@login_required
def task_timeline(request):
    today = timezone.localdate()

    tasks = Task.objects.filter(is_archived=False).select_related(
        "assignee", "timeline_group"
    ).prefetch_related("scopes").order_by("created_at", "pk")
    status_filter_options = [("active", "Active tasks"), *Task.Status.choices]
    valid_status_filters = {value for value, _label in status_filter_options}
    selected_status_filters = [
        value for value in dict.fromkeys(request.GET.getlist("status"))
        if value in valid_status_filters
    ]
    if selected_status_filters:
        status_query = Q(pk__in=[])
        for status in selected_status_filters:
            if status == "active":
                status_query |= ~Q(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
            else:
                status_query |= Q(status=status)
        tasks = tasks.filter(status_query)

    referenced_scope_ids = Task.objects.filter(
        is_archived=False,
        scopes__isnull=False,
    ).order_by().values_list("scopes__id", flat=True)
    timeline_scope_options = list(
        Scope.objects.filter(Q(is_active=True) | Q(pk__in=referenced_scope_ids))
        .distinct().order_by("position", "name")
    )
    active_scope_options = list(Scope.objects.filter(is_active=True).order_by("position", "name"))
    valid_scope_ids = {scope.pk for scope in timeline_scope_options}
    selected_scope_ids = [
        scope_id for value in dict.fromkeys(request.GET.getlist("scope"))
        if (scope_id := positive_int(value)) in valid_scope_ids
    ]
    selected_scope_filters = [str(scope_id) for scope_id in selected_scope_ids]
    if selected_scope_ids:
        tasks = tasks.filter(scopes__id__in=selected_scope_ids).distinct()

    referenced_assignee_ids = Task.objects.filter(
        is_archived=False,
        assignee__isnull=False,
    ).order_by().values_list("assignee_id", flat=True)
    active_users = list(
        User.objects.filter(
            Q(account_status=User.AccountStatus.ACTIVE) | Q(pk__in=referenced_assignee_ids)
        ).distinct().order_by("display_name", "username")
    )
    valid_user_ids = {member.pk for member in active_users}
    raw_assignee_filters = list(dict.fromkeys(request.GET.getlist("assignee")))
    include_unassigned = "unassigned" in raw_assignee_filters
    selected_assignee_ids = [
        assignee_id for value in raw_assignee_filters
        if (assignee_id := positive_int(value)) in valid_user_ids
    ]
    selected_assignee_filters = [str(assignee_id) for assignee_id in selected_assignee_ids]
    if include_unassigned:
        selected_assignee_filters.insert(0, "unassigned")
    if selected_assignee_filters:
        assignee_query = Q(pk__in=[])
        if include_unassigned:
            assignee_query |= Q(assignee__isnull=True)
        if selected_assignee_ids:
            assignee_query |= Q(assignee_id__in=selected_assignee_ids)
        tasks = tasks.filter(assignee_query)

    groups = list(TimelineGroup.objects.order_by("position", "name"))
    valid_group_ids = {group.pk for group in groups}
    raw_group_filters = list(dict.fromkeys(request.GET.getlist("group")))
    include_ungrouped = "ungrouped" in raw_group_filters
    selected_group_ids = [
        group_id for value in raw_group_filters
        if (group_id := positive_int(value)) in valid_group_ids
    ]
    selected_group_filters = [str(group_id) for group_id in selected_group_ids]
    if include_ungrouped:
        selected_group_filters.insert(0, "ungrouped")
    if selected_group_filters:
        group_query = Q(pk__in=[])
        if include_ungrouped:
            group_query |= Q(timeline_group__isnull=True)
        if selected_group_ids:
            group_query |= Q(timeline_group_id__in=selected_group_ids)
        tasks = tasks.filter(group_query)
    query = request.GET.get("q", "").strip()
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(description__icontains=query))

    milestone_queryset = TimelineMilestone.objects.select_related(
        "timeline_group", "created_by"
    ).prefetch_related("scopes")
    if selected_scope_ids:
        milestone_queryset = milestone_queryset.filter(
            Q(scopes__isnull=True) | Q(scopes__id__in=selected_scope_ids)
        ).distinct()
    if selected_group_filters:
        milestone_group_query = Q(timeline_group__isnull=True)
        if selected_group_ids:
            milestone_group_query |= Q(timeline_group_id__in=selected_group_ids)
        milestone_queryset = milestone_queryset.filter(
            milestone_group_query
        )

    page_size = positive_int(request.GET.get("page_size", "100")) or 100
    if page_size not in {50, 100, 200}:
        page_size = 100
    total_scheduled_count = tasks.filter(
        Q(timeline_start_date__isnull=False) | Q(due_date__isnull=False)
    ).count()
    date_bounds = tasks.aggregate(
        latest_start=Max("timeline_start_date"),
        latest_due=Max("due_date"),
    )
    paginator = Paginator(tasks, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    tasks = list(page_obj.object_list)
    unscheduled_tasks = [
        task for task in tasks if not task.timeline_start_date and not task.due_date
    ]

    latest_milestone_date = milestone_queryset.aggregate(value=Max("date"))["value"]
    scheduled_dates = [value for value in date_bounds.values() if value]
    if latest_milestone_date:
        scheduled_dates.append(latest_milestone_date)
    first_year = today.year - 1
    requested_last_year = max([today.year] + [value.year for value in scheduled_dates])
    last_year = min(requested_last_year, today.year + settings.TIMELINE_MAX_FUTURE_YEARS)
    window_start = date(first_year, 1, 1)
    window_end = date(last_year, 12, 31)
    day_count = (window_end - window_start).days + 1
    timeline_year_label = f"{first_year}–{last_year}"

    holidays = list(TimelineHoliday.objects.order_by("start_date", "name"))
    milestones = list(
        milestone_queryset.filter(date__range=(window_start, window_end)).order_by("date", "name", "pk")
    )
    all_milestones = []
    if request.user.is_admin:
        all_milestones = list(
            TimelineMilestone.objects.select_related("timeline_group", "created_by")
            .prefetch_related("scopes")
            .order_by("date", "name", "pk")
        )
        for milestone in all_milestones:
            options_by_id = {scope.pk: scope for scope in active_scope_options}
            options_by_id.update({scope.pk: scope for scope in milestone.scope_list})
            milestone.edit_scope_options = sorted(
                options_by_id.values(), key=lambda scope: (scope.position, scope.name.lower(), scope.pk)
            )

    def milestone_marker(items):
        labels = []
        for milestone in items:
            location = milestone.timeline_group.name if milestone.timeline_group_id else "Global"
            scope_names = ", ".join(scope.name for scope in milestone.scope_list) or "All scopes"
            label = f"{milestone.name} ({location}; {scope_names})"
            if milestone.notes:
                label += f" — {milestone.notes}"
            labels.append(label)
        return {
            "left": (items[0].date - window_start).days + 1,
            "color": items[0].color,
            "count": len(items),
            "tooltip": " | ".join(labels),
        }

    milestones_by_date = {}
    global_milestones_by_date = {}
    group_milestones_by_date = {}
    for milestone in milestones:
        milestones_by_date.setdefault(milestone.date, []).append(milestone)
        if milestone.timeline_group_id:
            group_milestones_by_date.setdefault(milestone.timeline_group_id, {}).setdefault(
                milestone.date, []
            ).append(milestone)
        else:
            global_milestones_by_date.setdefault(milestone.date, []).append(milestone)
    milestone_markers = {
        milestone_date: milestone_marker(items)
        for milestone_date, items in milestones_by_date.items()
    }
    global_milestone_lines = [
        milestone_marker(items) for items in global_milestones_by_date.values()
    ]
    years = []
    months = []
    for year in range(first_year, last_year + 1):
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        years.append({
            "label": year,
            "left": (year_start - window_start).days + 1,
            "span": (year_end - year_start).days + 1,
        })
        for month in range(1, 13):
            month_start = date(year, month, 1)
            months.append({
                "label": month_start.strftime("%b"),
                "left": (month_start - window_start).days + 1,
                "span": monthrange(year, month)[1],
            })
    days = []
    for offset in range(day_count):
        current = window_start + timedelta(days=offset)
        matching_holidays = [holiday for holiday in holidays if holiday_covers_day(holiday, current)]
        holiday_names = [holiday.name for holiday in matching_holidays]
        days.append({
            "date": current,
            "number": current.day,
            "is_today": current == today,
            "is_weekend": current.weekday() >= 5,
            "is_holiday": bool(holiday_names),
            "holiday_names": ", ".join(holiday_names),
            "holiday_color": matching_holidays[0].color if matching_holidays else "#E07849",
            "milestone": milestone_markers.get(current),
        })
    calendar_holidays = [
        {
            "left": index,
            "names": day["holiday_names"],
            "color": day["holiday_color"],
        }
        for index, day in enumerate(days, start=1)
        if day["is_holiday"]
    ]
    weekend_offset = (5 - window_start.weekday()) % 7

    rows_by_group = {group.pk: [] for group in groups}
    ungrouped_rows = []
    outside_range_count = 0
    task_count = 0
    for task in tasks:
        task_start = task.timeline_start_date or task.due_date
        task_end = task.due_date or task.timeline_start_date
        if not task_start or not task_end:
            continue
        task_count += 1
        if task_end < window_start or task_start > window_end:
            outside_range_count += 1
            continue
        visible_start = max(task_start, window_start)
        visible_end = min(task_end, window_end)
        row = {
            "task": task,
            "left": (visible_start - window_start).days + 1,
            "span": (visible_end - visible_start).days + 1,
            "starts_before": task_start < window_start,
            "ends_after": task_end > window_end,
            "date_label": f"{task_start.strftime('%d %b %Y')} – {task_end.strftime('%d %b %Y')}",
        }
        if task.timeline_group_id:
            rows_by_group.setdefault(task.timeline_group_id, []).append(row)
        else:
            ungrouped_rows.append(row)

    sections = []
    for group in groups:
        milestone_lines = [
            milestone_marker(items)
            for items in group_milestones_by_date.get(group.pk, {}).values()
        ]
        if rows_by_group.get(group.pk) or milestone_lines:
            sections.append({
                "id": f"group-{group.pk}",
                "name": group.name,
                "color": group.color,
                "rows": rows_by_group[group.pk],
                "milestone_lines": milestone_lines,
            })
    if ungrouped_rows:
        sections.append({
            "id": "ungrouped",
            "name": "Ungrouped",
            "color": "#8A918C",
            "rows": ungrouped_rows,
            "milestone_lines": [],
        })

    return render(request, "tasks/timeline.html", {
        "days": days,
        "years": years,
        "months": months,
        "day_count": day_count,
        "window_start": window_start,
        "window_end": window_end,
        "sections": sections,
        "unscheduled_tasks": unscheduled_tasks,
        "outside_range_count": outside_range_count,
        "task_count": total_scheduled_count,
        "page_scheduled_count": task_count,
        "page_obj": page_obj,
        "page_size": page_size,
        "timeline_page_query": "&".join(
            part for part in request.GET.urlencode().split("&")
            if not part.startswith("page=")
        ),
        "calendar_holidays": calendar_holidays,
        "global_milestone_lines": global_milestone_lines,
        "weekend_offset": weekend_offset,
        "timeline_year_label": timeline_year_label,
        "groups": groups,
        "holidays": holidays,
        "milestones": all_milestones,
        "scopes": active_scope_options,
        "filter_scopes": timeline_scope_options,
        "users": active_users,
        "status_filter_options": status_filter_options,
        "selected_status_filters": selected_status_filters,
        "selected_scope_filters": selected_scope_filters,
        "selected_assignee_filters": selected_assignee_filters,
        "selected_group_filters": selected_group_filters,
        "selected_filter_count": (
            len(selected_status_filters)
            + len(selected_scope_filters)
            + len(selected_assignee_filters)
            + len(selected_group_filters)
        ),
        "query": query,
        "timeline_return_url": request.get_full_path(),
    })


@admin_required
@transaction.atomic
def timeline_task_update(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    task = get_object_or_404(Task.objects.select_related("timeline_group"), pk=pk, is_archived=False)
    old_label = timeline_task_label(task)
    old_values = {
        "timeline_start_date": task.timeline_start_date.isoformat() if task.timeline_start_date else "",
        "due_date": task.due_date.isoformat() if task.due_date else "",
        "timeline_group": task.timeline_group.name if task.timeline_group_id else "Ungrouped",
    }
    form = TimelineTaskForm(request.POST, instance=task)
    if not form.is_valid():
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
        return timeline_redirect(request)
    task = form.save()
    new_label = timeline_task_label(task)
    if old_label != new_label:
        TaskHistory.objects.create(
            task=task,
            actor=request.user,
            event="Timeline schedule updated",
            old_value=old_label,
            new_value=new_label,
        )
        record_audit(request.user, task, "Timeline schedule updated", {"changes": {
            "timeline_start_date": {"from": old_values["timeline_start_date"], "to": task.timeline_start_date.isoformat() if task.timeline_start_date else ""},
            "due_date": {"from": old_values["due_date"], "to": task.due_date.isoformat() if task.due_date else ""},
            "timeline_group": {"from": old_values["timeline_group"], "to": task.timeline_group.name if task.timeline_group_id else "Ungrouped"},
        }})
        messages.success(request, "Task schedule updated.")
    return timeline_redirect(request)


@admin_required
def timeline_group_add(request):
    if request.method != "POST":
        return redirect("task_timeline")
    form = TimelineGroupForm(request.POST)
    if form.is_valid():
        group = form.save(commit=False)
        group.created_by = request.user
        group.position = (TimelineGroup.objects.aggregate(value=Max("position"))["value"] or 0) + 1
        group.save()
        record_audit(request.user, group, "Timeline group added")
        messages.success(request, "Timeline group added.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return timeline_redirect(request)


@admin_required
def timeline_group_edit(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    group = get_object_or_404(TimelineGroup, pk=pk)
    form = TimelineGroupForm(request.POST, instance=group)
    if form.is_valid():
        form.save()
        record_audit(request.user, group, "Timeline group updated")
        messages.success(request, "Timeline group updated.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return timeline_redirect(request)


@admin_required
def timeline_group_delete(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    group = get_object_or_404(TimelineGroup, pk=pk)
    record_audit(request.user, group, "Timeline group deleted")
    group.delete()
    messages.success(request, "Timeline group deleted. Its tasks are now Ungrouped and its milestones are now global.")
    return timeline_redirect(request)


@admin_required
def timeline_holiday_add(request):
    if request.method != "POST":
        return redirect("task_timeline")
    form = TimelineHolidayForm(request.POST)
    if form.is_valid():
        holiday = form.save(commit=False)
        holiday.created_by = request.user
        holiday.save()
        record_audit(request.user, holiday, "Timeline holiday added")
        messages.success(request, "Holiday added.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return timeline_redirect(request)


@admin_required
def timeline_holiday_edit(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    holiday = get_object_or_404(TimelineHoliday, pk=pk)
    form = TimelineHolidayForm(request.POST, instance=holiday)
    if form.is_valid():
        form.save()
        record_audit(request.user, holiday, "Timeline holiday updated")
        messages.success(request, "Holiday updated.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return timeline_redirect(request)


@admin_required
def timeline_holiday_delete(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    holiday = get_object_or_404(TimelineHoliday, pk=pk)
    record_audit(request.user, holiday, "Timeline holiday deleted")
    holiday.delete()
    messages.success(request, "Holiday deleted.")
    return timeline_redirect(request)


@admin_required
@transaction.atomic
def timeline_milestone_add(request):
    if request.method != "POST":
        return redirect("task_timeline")
    form = TimelineMilestoneForm(request.POST)
    if form.is_valid():
        milestone = form.save(commit=False)
        milestone.created_by = request.user
        milestone.save()
        form.save_m2m()
        record_audit(request.user, milestone, "Timeline milestone added", {
            "date": milestone.date.isoformat(),
            "timeline_group": milestone.timeline_group.name if milestone.timeline_group_id else "Global",
            "scopes": list(milestone.scopes.values_list("name", flat=True)),
        })
        messages.success(request, "Milestone added.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return timeline_redirect(request)


@admin_required
@transaction.atomic
def timeline_milestone_edit(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    milestone = get_object_or_404(TimelineMilestone, pk=pk)
    form = TimelineMilestoneForm(request.POST, instance=milestone)
    if form.is_valid():
        milestone = form.save()
        record_audit(request.user, milestone, "Timeline milestone updated", {
            "date": milestone.date.isoformat(),
            "timeline_group": milestone.timeline_group.name if milestone.timeline_group_id else "Global",
            "scopes": list(milestone.scopes.values_list("name", flat=True)),
        })
        messages.success(request, "Milestone updated.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return timeline_redirect(request)


@admin_required
@transaction.atomic
def timeline_milestone_delete(request, pk):
    if request.method != "POST":
        return redirect("task_timeline")
    milestone = get_object_or_404(TimelineMilestone, pk=pk)
    record_audit(request.user, milestone, "Timeline milestone deleted", {
        "name": milestone.name,
        "date": milestone.date.isoformat(),
    })
    milestone.delete()
    messages.success(request, "Milestone deleted.")
    return timeline_redirect(request)


@login_required
def task_list(request):
    tab = request.GET.get("tab", "all")
    if tab == "actions":
        actions = ActionItem.objects.filter(
            assignee=request.user,
            published_at__isnull=False,
        ).select_related("task", "meeting").prefetch_related("task__scopes")
        if request.GET.get("show") != "all":
            actions = actions.filter(is_completed=False)
        actions = actions.order_by("created_at", "pk")
        page_obj = Paginator(actions, 50).get_page(request.GET.get("page"))
        return render(request, "tasks/list.html", {
            "tab": tab,
            "actions": page_obj.object_list,
            "page_obj": page_obj,
            "pagination_query": pagination_query(request),
        })
    tasks = Task.objects.filter(is_archived=False).select_related(
        "parent_task", "assignee", "created_by", "completed_by"
    ).annotate(
        open_action_count=Count(
            "action_items",
            filter=Q(action_items__is_completed=False, action_items__published_at__isnull=False),
            distinct=True,
        ),
        subtask_count=Count("subtasks", filter=Q(subtasks__is_archived=False), distinct=True),
    ).prefetch_related(
        "scopes",
        Prefetch(
            "board_links",
            queryset=TaskBoard.objects.filter(released_at__isnull=True)
            .select_related("board")
            .order_by("added_at", "pk"),
            to_attr="active_board_links",
        ),
        Prefetch(
            "history",
            queryset=TaskHistory.objects.filter(
                ~Q(event__istartswith="Action item")
                | Q(event__in=VISIBLE_ACTION_HISTORY_EVENTS)
            )
            .select_related("actor")
            .order_by("-created_at", "-pk")[:1],
            to_attr="display_history",
        ),
    ).order_by("-created_at", "-pk")
    if tab == "mine":
        tasks = tasks.filter(assignee=request.user)
    query = request.GET.get("q", "").strip()
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(description__icontains=query))

    selected_scope_filters = list(dict.fromkeys(request.GET.getlist("scope")))
    selected_scope_ids = [
        scope_id for value in selected_scope_filters
        if (scope_id := positive_int(value))
    ]
    selected_scope_filters = [str(scope_id) for scope_id in selected_scope_ids]
    if selected_scope_ids:
        tasks = tasks.filter(scopes__id__in=selected_scope_ids).distinct()

    status_filter_options = [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("overdue", "Overdue"),
        *Task.Status.choices,
    ]
    allowed_status_filters = {value for value, _ in status_filter_options}
    selected_status_filters = [
        value for value in dict.fromkeys(request.GET.getlist("status"))
        if value in allowed_status_filters
    ]
    if selected_status_filters:
        status_query = Q(pk__in=[])
        for status in selected_status_filters:
            if status == "active":
                status_query |= ~Q(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
            elif status == "inactive":
                status_query |= Q(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
            elif status == "overdue":
                status_query |= (
                    Q(due_date__lt=timezone.localdate())
                    & ~Q(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
                )
            else:
                status_query |= Q(status=status)
        tasks = tasks.filter(status_query)

    selected_priority_filters = [
        value for value in dict.fromkeys(request.GET.getlist("priority"))
        if value in Task.Priority.values
    ]
    if selected_priority_filters:
        tasks = tasks.filter(priority__in=selected_priority_filters)

    selected_assignee_filters = list(dict.fromkeys(request.GET.getlist("assignee")))
    include_unassigned = "unassigned" in selected_assignee_filters
    selected_assignee_ids = [
        assignee_id for value in selected_assignee_filters
        if value != "unassigned" and (assignee_id := positive_int(value))
    ]
    selected_assignee_filters = [str(pk) for pk in selected_assignee_ids]
    if include_unassigned:
        selected_assignee_filters.insert(0, "unassigned")
    if selected_assignee_filters:
        assignee_query = Q(pk__in=[])
        if include_unassigned:
            assignee_query |= Q(assignee__isnull=True)
        if selected_assignee_ids:
            assignee_query |= Q(assignee_id__in=selected_assignee_ids)
        tasks = tasks.filter(assignee_query)

    inactive_year = request.GET.get("inactive_year") or request.GET.get("completed_year")
    inactive_year_statuses = {"inactive", Task.Status.DONE, Task.Status.CANCELLED}
    if (
        inactive_year
        and selected_status_filters
        and set(selected_status_filters).issubset(inactive_year_statuses)
    ):
        try:
            inactive_year = int(inactive_year)
            tasks = with_latest_status_change(tasks).filter(inactive_year=inactive_year)
        except (TypeError, ValueError):
            pass
    page_obj = Paginator(tasks, 50).get_page(request.GET.get("page"))
    tasks = list(page_obj.object_list)
    for task in tasks:
        task.active_board_list = [link.board for link in task.active_board_links]
        task.last_history = task.display_history[0] if task.display_history else None
    return render(request, "tasks/list.html", {
        "tab": tab, "tasks": tasks, "query": query,
        "scopes": Scope.objects.filter(
            Q(is_active=True) | Q(pk__in=selected_scope_ids)
        ).distinct().order_by("position", "name"),
        "users": User.objects.filter(
            Q(account_status=User.AccountStatus.ACTIVE) | Q(pk__in=selected_assignee_ids)
        ).distinct().order_by("display_name", "username", "pk"),
        "status_choices": Task.Status.choices,
        "status_filter_options": status_filter_options,
        "priority_choices": Task.Priority.choices,
        "selected_scope_filters": selected_scope_filters,
        "selected_assignee_filters": selected_assignee_filters,
        "selected_status_filters": selected_status_filters,
        "selected_priority_filters": selected_priority_filters,
        "selected_filter_count": (
            len(selected_scope_filters)
            + len(selected_assignee_filters)
            + len(selected_status_filters)
            + len(selected_priority_filters)
        ),
        "today": timezone.localdate(),
        "page_obj": page_obj,
        "pagination_query": pagination_query(request),
    })


@login_required
@transaction.atomic
def task_form(request, pk=None):
    return_url = safe_redirect_url(
        request,
        request.POST.get("next", "") if request.method == "POST" else request.GET.get("next", ""),
        "tasks",
    )
    task_queryset = Task.objects.select_for_update() if request.method == "POST" else Task.objects
    task = get_object_or_404(task_queryset, pk=pk, is_archived=False) if pk else None
    score_enabled = SystemSetting.load().task_score_enabled
    old_values = {}
    old_assignee = None
    old_status = None
    old_scope_ids = set()
    old_related_ids = set()
    if task:
        old_assignee, old_status = task.assignee, task.status
        old_scope_ids = set(task.scopes.values_list("pk", flat=True))
        old_related_ids = set(task.related_tasks.values_list("pk", flat=True))
        old_values = {field: getattr(task, field) for field in ["title", "parent_task_id", "assignee_id", "status", "priority", "timeline_start_date", "due_date", "link_url"]}
    form = TaskForm(request.POST or None, instance=task, score_enabled=score_enabled)
    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        if not task.pk:
            task.created_by = request.user
        if task.status == Task.Status.DONE and old_status != Task.Status.DONE:
            task.completed_by = task.assignee
            task.completed_at = timezone.now()
        elif task.status != Task.Status.DONE:
            task.completed_by = None
            task.completed_at = None
        task.save()
        if old_values:
            log_task_changes(task, request.user, old_values, form.cleaned_data.get("status_note", ""))
        else:
            TaskHistory.objects.create(task=task, actor=request.user, event="Task created")
            if task.status in {Task.Status.DONE, Task.Status.CANCELLED}:
                TaskHistory.objects.create(
                    task=task,
                    actor=request.user,
                    event="Status changed",
                    new_value=task.get_status_display(),
                    note=form.cleaned_data.get("status_note", ""),
                )
            if task.parent_task_id:
                TaskHistory.objects.create(
                    task=task,
                    actor=request.user,
                    event="Parent Task added",
                    new_value=task.parent_task.title,
                )
        form.save_m2m()
        log_scope_changes(task, request.user, old_scope_ids)
        log_related_task_changes(task, request.user, old_related_ids)
        if task.status in {Task.Status.DONE, Task.Status.CANCELLED}:
            release_all_task_boards(task, request.user, f"Task {task.get_status_display().lower()}")
        else:
            sync_task_boards(task, form.cleaned_data.get("boards", []), request.user, old_assignee=old_assignee)
        record_audit(request.user, task, "Task updated" if pk else "Task created", {"status": task.status})
        messages.success(request, "Task saved successfully.")
        return redirect(return_url)
    return render(request, "tasks/form.html", {
        "form": form,
        "task": task,
        "title": "Edit Task" if task else "Add Task",
        "return_url": return_url,
    })


@admin_required
def task_delete(request, pk):
    if request.method == "POST":
        task = get_object_or_404(Task, pk=pk, is_archived=False)
        release_all_task_boards(task, request.user, "Task deleted")
        task.is_archived = True
        task.save(update_fields=["is_archived", "updated_at"])
        TaskHistory.objects.create(task=task, actor=request.user, event="Task deleted")
        messages.success(request, "Task deleted successfully.")
    return safe_redirect(
        request,
        request.POST.get("next", "") or request.META.get("HTTP_REFERER", ""),
        "tasks",
    )


@login_required
def task_history(request, pk):
    task = get_object_or_404(Task.objects.select_related("assignee", "parent_task").prefetch_related("scopes", "related_tasks__scopes"), pk=pk)
    history = list(task.history.filter(
        ~Q(event__istartswith="Action item")
        | Q(event__in=VISIBLE_ACTION_HISTORY_EVENTS)
    ).select_related("actor"))
    for item in history:
        item.change_kind = history_change_kind(item.event)
    actions = list(
        task.action_items.filter(published_at__isnull=False).select_related(
            "assignee", "meeting", "completed_by", "task"
        ).order_by("created_at", "pk")
    )
    for item in actions:
        item.can_toggle = can_toggle_action_item(request.user, item)
    return render(request, "tasks/history.html", {
        "task": task,
        "return_url": safe_redirect_url(request, request.GET.get("next", ""), "tasks"),
        "history": history,
        "actions": actions,
        "subtasks": task.subtasks.filter(is_archived=False).select_related("assignee").prefetch_related("scopes").order_by("created_at", "pk"),
        "related_tasks": task.related_tasks.filter(is_archived=False).select_related("assignee").prefetch_related("scopes").order_by("created_at", "pk"),
    })
@login_required
@require_POST
@transaction.atomic
def action_item_toggle(request, pk):
    item_reference = get_object_or_404(
        ActionItem.objects.only("pk", "task_id", "meeting_id", "created_at"),
        pk=pk,
    )
    meeting_ids = set()
    if item_reference.meeting_id:
        meeting_ids.add(item_reference.meeting_id)
    if item_reference.task_id:
        meeting_ids.update(
            MeetingTask.objects.filter(
                task_id=item_reference.task_id,
                meeting__status=Meeting.Status.DRAFT,
                meeting__created_at__gte=item_reference.created_at,
            ).values_list("meeting_id", flat=True)
        )
    list(Meeting.objects.select_for_update().filter(pk__in=meeting_ids).order_by("pk"))
    task = None
    if item_reference.task_id:
        task = get_object_or_404(Task.objects.select_for_update(), pk=item_reference.task_id)
    item = get_object_or_404(
        ActionItem.objects.select_for_update(), pk=pk,
    )
    if task:
        item.task = task
    if item.is_completed and task and task.status == Task.Status.DONE:
        error = "Reopen the task before reopening this Action Item."
        if wants_json(request):
            return JsonResponse({"error": error}, status=400)
        messages.error(request, error)
        return safe_redirect(request, request.POST.get("next", ""), "tasks")
    if not can_toggle_action_item(request.user, item):
        return HttpResponseForbidden("You cannot update this Action Item.")
    item.is_completed = not item.is_completed
    item.completed_by = request.user if item.is_completed else None
    item.completed_at = timezone.now() if item.is_completed else None
    item.save(update_fields=["is_completed", "completed_by", "completed_at"])
    if item.task_id:
        TaskHistory.objects.create(
            task_id=item.task_id,
            actor=request.user,
            event="Action item completed" if item.is_completed else "Action item reopened",
            old_value="Open" if item.is_completed else "Completed",
            new_value="Completed" if item.is_completed else "Open",
            note=item.content,
        )
    updated_at = touch_meeting_action_entry(item.meeting, item, request.user) if item.meeting_id else None
    if item.task_id:
        carried_entries = MeetingTask.objects.filter(
            task_id=item.task_id,
            meeting__status=Meeting.Status.DRAFT,
            meeting__created_at__gte=item.created_at,
        ).exclude(meeting_id=item.meeting_id)
        carried_meeting_ids = list(
            carried_entries.values_list("meeting_id", flat=True).distinct()
        )
        if carried_meeting_ids:
            updated_at = timezone.now()
            carried_entries.update(updated_by=request.user, updated_at=updated_at)
            Meeting.objects.filter(pk__in=carried_meeting_ids).update(updated_at=updated_at)
    if wants_json(request):
        return JsonResponse({
            "updated": True,
            "action": serialize_action_item(item),
            "updated_at": updated_at.isoformat() if updated_at else "",
            "updated_by": str(request.user),
        })
    messages.success(request, "Action item updated.")
    return safe_redirect(request, request.POST.get("next", ""), "tasks")


@login_required
def meeting_list(request):
    query = request.GET.get("q", "").strip()
    meetings = Meeting.objects.select_related("host", "minute_taker", "writer_rotation", "created_by").annotate(
        reviewed_count=Count("task_entries", filter=~Q(task_entries__review_state=MeetingTask.ReviewState.PENDING), distinct=True),
        task_count=Count("task_entries", distinct=True),
        open_action_count=Count("action_items", filter=Q(action_items__is_completed=False), distinct=True),
    ).order_by("-meeting_date", "-created_at", "-pk")
    if query:
        meetings = meetings.filter(title__icontains=query)
    page_obj = Paginator(meetings, 30).get_page(request.GET.get("page"))
    meetings = list(page_obj.object_list)
    return render(request, "meetings/list.html", {
        "meetings": meetings,
        "query": query,
        "page_obj": page_obj,
        "pagination_query": pagination_query(request),
    })


@admin_required
def minute_writer_rotation_list(request):
    today = timezone.localdate()
    rotations = list(
        MinuteWriterRotation.objects.select_related("created_by")
        .prefetch_related("writer_members__user")
        .order_by("name")
    )
    for rotation in rotations:
        upcoming = rotation.upcoming_writer_members(max(today, rotation.anchor_date), count=2)
        rotation.next_writer = upcoming[0].user if upcoming else None
        rotation.following_writer = upcoming[1].user if len(upcoming) > 1 else None
    return render(request, "meetings/writer_rotation_list.html", {
        "rotations": rotations,
        "today": today,
    })


@admin_required
@transaction.atomic
def minute_writer_rotation_form(request, pk=None):
    if request.method == "POST":
        lock_responsibility_transfer_mutex()
        posted_writer_ids = {
            positive_int(value.strip())
            for value in request.POST.get("writers_order", "").split(",")
            if value.strip()
        }
        list(User.objects.select_for_update().filter(
            pk__in={user_id for user_id in posted_writer_ids if user_id},
        ).order_by("pk"))
    rotation_queryset = (
        MinuteWriterRotation.objects.select_for_update()
        if request.method == "POST"
        else MinuteWriterRotation.objects
    )
    rotation = get_object_or_404(rotation_queryset, pk=pk) if pk else None
    form = MinuteWriterRotationForm(request.POST or None, instance=rotation)
    if request.method == "POST" and form.is_valid():
        if not rotation:
            form.instance.created_by = request.user
        saved_rotation = form.save()
        record_audit(
            request.user,
            saved_rotation,
            "Minute Writer rotation updated" if rotation else "Minute Writer rotation created",
            {"writers": [user.pk for user in form.writer_users]},
        )
        messages.success(request, "Minute Writer rotation saved.")
        return redirect("minute_writer_rotations")
    return render(request, "meetings/writer_rotation_form.html", {
        "form": form,
        "rotation": rotation,
        "writer_options": form.writer_options,
    })


@admin_required
@require_POST
@transaction.atomic
def minute_writer_rotation_delete(request, pk):
    lock_responsibility_transfer_mutex()
    rotation = get_object_or_404(MinuteWriterRotation.objects.select_for_update(), pk=pk)
    rotation_name = rotation.name
    rotation.delete()
    messages.success(request, f"{rotation_name} rotation deleted.")
    return redirect("minute_writer_rotations")


@login_required
def minute_writer_preview(request):
    rotation_id = positive_int(request.GET.get("rotation"))
    meeting_id = positive_int(request.GET.get("meeting"))
    meeting_date = parse_date(request.GET.get("meeting_date", ""))
    if not rotation_id or not meeting_date:
        return JsonResponse({"error": "Select a rotation and meeting date."}, status=400)
    rotation = get_object_or_404(MinuteWriterRotation, pk=rotation_id)
    existing_meeting = None
    if meeting_id:
        existing_meeting = Meeting.objects.select_related("minute_taker").filter(
            pk=meeting_id,
            status=Meeting.Status.DRAFT,
            writer_assignment=Meeting.WriterAssignment.AUTOMATIC,
            writer_rotation_id=rotation_id,
        ).first()
    if meeting_date < rotation.anchor_date and not (
        existing_meeting and existing_meeting.meeting_date == meeting_date
    ):
        return JsonResponse({
            "error": "This rotation has not started for the selected meeting date.",
        }, status=400)
    if not existing_meeting and not rotation.is_active:
        return JsonResponse({"error": "This Writer Rotation is inactive."}, status=400)
    writer = existing_meeting.minute_taker if existing_meeting else rotation.writer_for(meeting_date)
    if not writer:
        return JsonResponse({"error": "This rotation has no active Minute Writers."}, status=400)
    return JsonResponse({
        "writer_id": writer.pk,
        "writer_name": str(writer),
        "rotation_name": rotation.name,
        "preserved": bool(existing_meeting),
    })


@admin_required
@transaction.atomic
def meeting_create(request):
    if request.method == "POST":
        lock_responsibility_transfer_mutex()
    form = MeetingForm(request.POST or None, initial={
        "meeting_date": timezone.localdate(),
        "host": request.user,
        "minute_taker": request.user,
        "writer_assignment": Meeting.WriterAssignment.MANUAL,
    })
    if request.method == "POST" and form.is_valid():
        meeting = form.save(commit=False)
        meeting.created_by = request.user
        writer = (
            assign_next_minute_writer(meeting.writer_rotation_id, meeting.meeting_date)
            if meeting.writer_assignment == Meeting.WriterAssignment.AUTOMATIC
            else meeting.minute_taker
        )
        if meeting.writer_assignment == Meeting.WriterAssignment.AUTOMATIC and writer:
            meeting.minute_taker = writer
        if not writer:
            form.add_error("writer_rotation", "The rotation cannot assign an active Minute Writer.")
        else:
            meeting.save()
            initialize_meeting_tasks(meeting)
            record_audit(request.user, meeting, "Meeting created")
            messages.success(request, "Meeting Minute created. To Do tasks without open Action Items were skipped.")
            return redirect("meeting_detail", pk=meeting.pk)
    return render(request, "meetings/form.html", {
        "form": form,
        "title": "Add Meeting Minute",
        "subtitle": "Use a rotation for automatic meeting occurrences or assign a writer manually.",
        "cancel_href": reverse("meetings"),
    })


@login_required
def meeting_detail(request, pk):
    meeting = get_object_or_404(Meeting.objects.select_related("host", "minute_taker", "writer_rotation", "created_by"), pk=pk)
    entries = list(
        meeting.task_entries.select_related(
            "task", "task__assignee", "updated_by",
        ).prefetch_related("task__scopes", Prefetch(
            "task__board_links",
            queryset=TaskBoard.objects.filter(released_at__isnull=True).select_related("board"),
            to_attr="_snapshot_board_links",
        ))
    )
    completed_after = previous_meeting_completed_after(meeting)
    needs_live_actions = meeting.status == Meeting.Status.DRAFT or any(
        entry.snapshot.get("snapshot_version", 0) < 2 for entry in entries
    )
    action_groups = (
        meeting_entries_action_groups(
            meeting, entries, completed_after=completed_after,
        )
        if needs_live_actions
        else {}
    )
    for entry in entries:
        if meeting.status == Meeting.Status.FINALIZED and entry.snapshot.get("snapshot_version", 0) >= 2:
            entry.open_actions, entry.recent_completed_actions, entry.new_actions = frozen_meeting_entry_actions(entry)
            entry.weekly_progress = entry.snapshot.get("weekly_progress", entry.weekly_progress)
            entry.review_state = entry.snapshot.get("review_state", entry.review_state)
            entry.display_snapshot = entry.snapshot
        else:
            entry.open_actions, entry.recent_completed_actions, entry.new_actions = action_groups.get(
                entry.pk, ([], [], []),
            )
            entry.display_snapshot = task_snapshot(entry.task) if entry.task else entry.snapshot
        if meeting.status == Meeting.Status.DRAFT:
            for item in entry.open_actions + entry.recent_completed_actions:
                item.can_toggle = can_toggle_action_item(request.user, item)
    reviewed = sum(1 for entry in entries if entry.review_state != MeetingTask.ReviewState.PENDING)
    return render(request, "meetings/detail.html", {
        "meeting": meeting, "entries": entries, "reviewed": reviewed,
        "can_record": can_record_meeting(request.user, meeting),
        "can_review": can_review_meeting(request.user, meeting),
        "can_manage": request.user.is_admin,
        "is_minute_writer": meeting.minute_taker_id == request.user.pk,
        "active_users": User.objects.filter(account_status=User.AccountStatus.ACTIVE).order_by("display_name", "username"),
    })


@login_required
def meeting_export_pdf(request, pk):
    meeting = get_object_or_404(
        Meeting.objects.select_related("host", "minute_taker", "created_by"),
        pk=pk,
    )
    from .pdf_export import build_meeting_pdf, meeting_pdf_filename

    pdf_data = build_meeting_pdf(meeting)
    response = HttpResponse(pdf_data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{meeting_pdf_filename(meeting)}"'
    response["Content-Length"] = len(pdf_data)
    return response


@admin_required
@transaction.atomic
def meeting_edit(request, pk):
    if request.method == "POST":
        lock_responsibility_transfer_mutex()
    queryset = Meeting.objects.select_for_update() if request.method == "POST" else Meeting.objects
    meeting = get_object_or_404(queryset, pk=pk)
    if meeting.status != Meeting.Status.DRAFT:
        return HttpResponseForbidden("This Meeting Minute cannot be edited.")
    previous_assignment = meeting.writer_assignment
    previous_rotation_id = meeting.writer_rotation_id
    form = MeetingForm(request.POST or None, instance=meeting)
    if request.method == "POST" and form.is_valid():
        meeting = form.save(commit=False)
        needs_new_automatic_writer = (
            meeting.writer_assignment == Meeting.WriterAssignment.AUTOMATIC
            and (
                previous_assignment != Meeting.WriterAssignment.AUTOMATIC
                or previous_rotation_id != meeting.writer_rotation_id
            )
        )
        writer = assign_next_minute_writer(
            meeting.writer_rotation_id, meeting.meeting_date,
        ) if needs_new_automatic_writer else meeting.minute_taker
        if needs_new_automatic_writer and writer:
            meeting.minute_taker = writer
        if not writer:
            form.add_error("writer_rotation", "The rotation cannot assign an active Minute Writer.")
        else:
            meeting.save()
            record_audit(request.user, meeting, "Meeting details updated", {"fields": form.changed_data})
            messages.success(request, "Meeting details updated.")
            return redirect("meeting_detail", pk=meeting.pk)
    return render(request, "meetings/form.html", {
        "form": form,
        "title": "Edit Meeting Minute",
        "subtitle": meeting.title,
        "cancel_href": reverse("meeting_detail", args=[meeting.pk]),
    })


@admin_required
@transaction.atomic
def meeting_delete(request, pk):
    if request.method == "POST":
        meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=pk)
        meeting.action_items.filter(published_at__isnull=True).delete()
        meeting.action_items.filter(published_at__isnull=False).update(
            source_meeting_title=meeting.title,
            source_meeting_date=meeting.meeting_date,
        )
        meeting.delete()
        messages.success(request, "Meeting Minute deleted.")
    return redirect("meetings")


@login_required
@transaction.atomic
def meeting_task_progress_save(request, meeting_pk, entry_pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=meeting_pk)
    if request.method != "POST" or not can_record_meeting(request.user, meeting):
        return JsonResponse({"error": "This Meeting Minute is not writable."}, status=403)
    entry = get_object_or_404(MeetingTask, pk=entry_pk, meeting=meeting)
    entry.weekly_progress = request.POST.get("weekly_progress", "")
    entry.included = bool(entry.weekly_progress) or entry.review_state in {MeetingTask.ReviewState.REVIEWED, MeetingTask.ReviewState.NO_UPDATE}
    entry.updated_by = request.user
    entry.save(update_fields=["weekly_progress", "included", "updated_by", "updated_at"])
    Meeting.objects.filter(pk=meeting.pk).update(updated_at=timezone.now())
    return JsonResponse({
        "saved": True,
        "updated_at": entry.updated_at.isoformat(),
        "updated_by": str(request.user),
    })


@login_required
@transaction.atomic
def meeting_action_add(request, meeting_pk, entry_pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=meeting_pk)
    if request.method != "POST" or not can_record_meeting(request.user, meeting):
        return JsonResponse({"error": "This Meeting Minute is not writable."}, status=403)
    entry = get_object_or_404(MeetingTask.objects.select_related("task", "task__assignee"), pk=entry_pk, meeting=meeting)
    if not entry.task:
        return JsonResponse({"error": "This task is no longer available."}, status=400)
    task = get_object_or_404(Task.objects.select_for_update(), pk=entry.task_id)
    if task.status == Task.Status.DONE:
        return JsonResponse({
            "error": "Reopen the task before adding a new Action Item.",
        }, status=400)
    content, assignee, due_date, error = parse_meeting_action_payload(request)
    if error:
        return JsonResponse({"error": error}, status=400)
    if not request.POST.get("action_assignee"):
        assignee = task.assignee
    item = ActionItem.objects.create(
        task=task,
        meeting=meeting,
        source_meeting_title=meeting.title,
        source_meeting_date=meeting.meeting_date,
        content=content,
        assignee=assignee,
        due_date=due_date,
        created_by=request.user,
    )
    entry.included = True
    entry.updated_by = request.user
    entry.save(update_fields=["included", "updated_by", "updated_at"])
    Meeting.objects.filter(pk=meeting.pk).update(updated_at=timezone.now())
    return JsonResponse({
        "created": True,
        "action": serialize_action_item(item),
        "updated_at": entry.updated_at.isoformat(),
        "updated_by": str(request.user),
    })


@login_required
@transaction.atomic
def meeting_action_update(request, meeting_pk, pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=meeting_pk)
    if request.method != "POST" or not can_record_meeting(request.user, meeting):
        return JsonResponse({"error": "This Meeting Minute is not writable."}, status=403)
    item = get_object_or_404(ActionItem.objects.select_related("task", "assignee"), pk=pk, meeting=meeting, published_at__isnull=True)
    content, assignee, due_date, error = parse_meeting_action_payload(request)
    if error:
        return JsonResponse({"error": error}, status=400)

    item.content = content
    item.assignee = assignee
    item.due_date = due_date
    item.save(update_fields=["content", "assignee", "due_date"])
    updated_at = touch_meeting_action_entry(meeting, item, request.user)
    return JsonResponse({
        "updated": True,
        "action": serialize_action_item(item),
        "updated_at": updated_at.isoformat(),
        "updated_by": str(request.user),
    })


@login_required
@transaction.atomic
def meeting_action_delete(request, meeting_pk, pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=meeting_pk)
    if request.method != "POST" or not can_record_meeting(request.user, meeting):
        return JsonResponse({"error": "This Meeting Minute is not writable."}, status=403)
    item = get_object_or_404(ActionItem.objects.select_related("task"), pk=pk, meeting=meeting, published_at__isnull=True)
    action_id = item.pk
    updated_at = touch_meeting_action_entry(meeting, item, request.user)
    item.delete()
    return JsonResponse({
        "deleted": True,
        "action_id": action_id,
        "updated_at": updated_at.isoformat(),
        "updated_by": str(request.user),
    })


@login_required
def meeting_live_updates(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    meeting_version = meeting.updated_at.isoformat()
    client_version = parse_datetime(request.GET.get("since", ""))
    if client_version and timezone.is_naive(client_version):
        client_version = timezone.make_aware(client_version)
    if client_version and client_version >= meeting.updated_at:
        return JsonResponse({"changed": False, "updated_at": meeting_version})

    all_entries = list(meeting.task_entries.select_related("updated_by").order_by("position"))
    entries = [
        entry for entry in all_entries
        if not client_version or entry.updated_at > client_version
    ]
    task_ids = [entry.task_id for entry in entries if entry.task_id]
    previous_actions = ActionItem.objects.none()
    if task_ids:
        completed_after = previous_meeting_completed_after(meeting)
        previous_actions = ActionItem.objects.filter(
            task_id__in=task_ids,
            published_at__isnull=False,
            created_at__lte=meeting.created_at,
        ).exclude(meeting=meeting).filter(
            Q(is_completed=False)
            | Q(is_completed=True, completed_at__gte=completed_after)
        ).select_related("assignee", "completed_by", "meeting")
    open_actions_by_task = {}
    completed_actions_by_task = {}
    for item in previous_actions:
        target = completed_actions_by_task if item.is_completed else open_actions_by_task
        target.setdefault(item.task_id, []).append(item)
    for items in open_actions_by_task.values():
        items.sort(key=lambda item: (item.created_at, item.pk))
    for task_id, items in completed_actions_by_task.items():
        completed_actions_by_task[task_id] = sorted(
            items,
            key=lambda item: (item.completed_at, item.pk),
            reverse=True,
        )[:4]

    def serialize_previous(item):
        data = serialize_action_item(item)
        data["can_toggle"] = can_toggle_action_item(request.user, item)
        data["toggle_url"] = reverse("action_item_toggle", args=[item.pk])
        return data

    actions_by_task = {}
    for item in ActionItem.objects.filter(
        meeting=meeting,
        task_id__in=task_ids,
    ).select_related("assignee", "completed_by").order_by("created_at", "pk"):
        actions_by_task.setdefault(item.task_id, []).append(serialize_action_item(item))
    return JsonResponse({
        "changed": True,
        "updated_at": meeting_version,
        "meeting_status": meeting.status,
        "order_mode": meeting.task_order,
        "order": [
            {"id": entry.pk, "position": entry.position}
            for entry in all_entries
        ],
        "reviewed": sum(
            entry.review_state != MeetingTask.ReviewState.PENDING
            for entry in all_entries
        ),
        "total": len(all_entries),
        "entries": [{
            "id": entry.pk,
            "position": entry.position,
            "weekly_progress": entry.weekly_progress,
            "review_state": entry.review_state,
            "review_label": entry.get_review_state_display(),
            "updated_at": entry.updated_at.isoformat(),
            "updated_by": str(entry.updated_by) if entry.updated_by else "System",
            "open_actions": [
                serialize_previous(item)
                for item in open_actions_by_task.get(entry.task_id, [])
            ],
            "recent_completed_actions": [
                serialize_previous(item)
                for item in completed_actions_by_task.get(entry.task_id, [])
            ],
            "actions": actions_by_task.get(entry.task_id, []),
        } for entry in entries],
    })


@login_required
@transaction.atomic
def meeting_task_review(request, meeting_pk, entry_pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=meeting_pk)
    if request.method != "POST" or not can_review_meeting(request.user, meeting):
        return HttpResponseForbidden("Only the Host can review this Meeting Minute.")
    entry = get_object_or_404(MeetingTask, pk=entry_pk, meeting=meeting)
    state = request.POST.get("review_state", MeetingTask.ReviewState.PENDING)
    if state not in dict(MeetingTask.ReviewState.choices):
        if wants_json(request):
            return JsonResponse({"error": "Select a valid review result."}, status=400)
        messages.error(request, "Select a valid review result.")
        return redirect(f"/meetings/{meeting.pk}/#task-{entry.pk}")
    entry.review_state = state
    entry.included = bool(entry.weekly_progress) or state in {MeetingTask.ReviewState.REVIEWED, MeetingTask.ReviewState.NO_UPDATE}
    entry.updated_by = request.user
    entry.save(update_fields=["review_state", "included", "updated_by", "updated_at"])
    Meeting.objects.filter(pk=meeting.pk).update(updated_at=timezone.now())
    record_audit(request.user, meeting, f"Task #{entry.position} review updated", {"review_state": state})
    pending_entries = meeting.task_entries.filter(
        review_state=MeetingTask.ReviewState.PENDING,
    ).exclude(pk=entry.pk)
    next_entry = pending_entries.filter(
        position__gt=entry.position,
    ).order_by("position").first() or pending_entries.order_by("position").first()
    target = next_entry.pk if next_entry else entry.pk
    if wants_json(request):
        reviewed = meeting.task_entries.exclude(review_state=MeetingTask.ReviewState.PENDING).count()
        total = meeting.task_entries.count()
        return JsonResponse({
            "saved": True,
            "entry_id": entry.pk,
            "review_state": entry.review_state,
            "review_label": entry.get_review_state_display(),
            "reviewed": reviewed,
            "total": total,
            "progress_percent": round(reviewed / total * 100) if total else 0,
            "next_entry_id": target,
            "updated_at": entry.updated_at.isoformat(),
            "updated_by": str(request.user),
        })
    return redirect(f"/meetings/{meeting.pk}/#task-{target}")


@login_required
@transaction.atomic
def meeting_task_move(request, meeting_pk, entry_pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=meeting_pk)
    if request.method != "POST" or not can_review_meeting(request.user, meeting):
        return HttpResponseForbidden("Only the Host can reorder this Meeting Minute.")
    entry = get_object_or_404(MeetingTask, pk=entry_pk, meeting=meeting)
    direction = request.POST.get("direction")
    if direction == "up":
        other = meeting.task_entries.filter(position__lt=entry.position).order_by("-position").first()
    else:
        other = meeting.task_entries.filter(position__gt=entry.position).order_by("position").first()
    updated_at = None
    if other:
        entry.position, other.position = other.position, entry.position
        entry.save(update_fields=["position"])
        other.save(update_fields=["position"])
        updated_at = timezone.now()
        meeting.task_order = Meeting.TaskOrder.MANUAL
        Meeting.objects.filter(pk=meeting.pk).update(
            task_order=meeting.task_order,
            updated_at=updated_at,
        )
    if wants_json(request):
        return JsonResponse({
            "moved": bool(other),
            "mode": meeting.task_order,
            "entries": meeting_order_payload(meeting),
            "updated_at": updated_at.isoformat() if updated_at else "",
            "updated_by": str(request.user),
        })
    return redirect(f"/meetings/{meeting.pk}/?order=manual#task-{entry.pk}")


@login_required
@transaction.atomic
def meeting_order(request, pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=pk)
    if request.method != "POST" or not can_review_meeting(request.user, meeting):
        return HttpResponseForbidden("Only the Host can reorder this Meeting Minute.")
    mode = request.POST.get("order_by", "manual")
    if mode not in Meeting.TaskOrder.values:
        if wants_json(request):
            return JsonResponse({"error": "Select a valid task order."}, status=400)
        messages.error(request, "Select a valid task order.")
        return redirect("meeting_detail", pk=meeting.pk)
    entries = list(
        meeting.task_entries.select_related("task", "task__assignee").prefetch_related("task__scopes")
    )
    def scope_sort_key(entry):
        if not entry.task:
            return (1, (), entry.snapshot.get("title", "").lower())
        scopes = tuple(
            (scope.position, scope.name.lower(), scope.pk)
            for scope in entry.task.scope_list
        )
        return (0 if scopes else 1, scopes, entry.task.title.lower())
    if mode == "scope":
        entries.sort(key=scope_sort_key)
    elif mode == "assignee":
        entries.sort(key=lambda entry: (
            (entry.task.assignee.display_name if entry.task and entry.task.assignee else "zzzz unassigned").lower(),
            scope_sort_key(entry),
        ))
    elif mode == "previous":
        previous = Meeting.objects.exclude(pk=meeting.pk).filter(meeting_date__lte=meeting.meeting_date).order_by("-meeting_date", "-created_at").first()
        previous_ids = list(previous.task_entries.values_list("task_id", flat=True)) if previous else []
        rank = {task_id: index for index, task_id in enumerate(previous_ids)}
        entries.sort(key=lambda entry: (rank.get(entry.task_id, 999999), entry.position))
    for position, entry in enumerate(entries, start=1):
        entry.position = position
    MeetingTask.objects.bulk_update(entries, ["position"])
    updated_at = timezone.now()
    meeting.task_order = mode
    Meeting.objects.filter(pk=meeting.pk).update(
        task_order=meeting.task_order,
        updated_at=updated_at,
    )
    record_audit(request.user, meeting, f"Task order changed to {mode}")
    if wants_json(request):
        return JsonResponse({
            "ordered": True,
            "mode": mode,
            "entries": meeting_order_payload(meeting),
            "updated_at": updated_at.isoformat(),
            "updated_by": str(request.user),
        })
    messages.success(request, "Meeting task order updated.")
    return redirect(f"/meetings/{meeting.pk}/?order={mode}")


@login_required
@transaction.atomic
def meeting_finalize(request, pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=pk)
    if request.method != "POST" or not can_review_meeting(request.user, meeting):
        return HttpResponseForbidden("Only the Host can finalize this Meeting Minute.")
    if meeting.task_entries.filter(review_state=MeetingTask.ReviewState.PENDING).exists():
        messages.error(request, "Review every task before finalizing this Meeting Minute.")
        return redirect("meeting_detail", pk=meeting.pk)
    completed_after = previous_meeting_completed_after(meeting)
    entries = list(
        meeting.task_entries.select_related("task", "task__assignee")
        .prefetch_related("task__scopes")
        .prefetch_related(Prefetch(
            "task__board_links",
            queryset=TaskBoard.objects.filter(released_at__isnull=True).select_related("board"),
            to_attr="_snapshot_board_links",
        ))
        .order_by("position")
    )
    action_groups = meeting_entries_action_groups(
        meeting, entries, completed_after=completed_after,
    )
    for entry in entries:
        freeze_meeting_entry(
            entry,
            meeting,
            completed_after=completed_after,
            action_groups=action_groups.get(entry.pk),
        )
        entry.save(update_fields=["snapshot"])
    finalized_at = timezone.now()
    published_count = publish_meeting_actions(meeting, request.user, published_at=finalized_at)
    meeting.status = Meeting.Status.FINALIZED
    meeting.finalized_at = finalized_at
    meeting.save(update_fields=["status", "finalized_at", "updated_at"])
    record_audit(request.user, meeting, "Meeting finalized", {"published_action_items": published_count})
    messages.success(request, f"Meeting Minute finalized. {published_count} Action Item(s) published to Tasks.")
    return redirect("meeting_detail", pk=meeting.pk)


@admin_required
@transaction.atomic
def meeting_reopen(request, pk):
    meeting = get_object_or_404(Meeting.objects.select_for_update(), pk=pk)
    if request.method != "POST":
        return HttpResponseForbidden("Only an Admin can reopen this Meeting Minute.")
    meeting.status = Meeting.Status.DRAFT
    meeting.finalized_at = None
    meeting.save(update_fields=["status", "finalized_at", "updated_at"])
    record_audit(request.user, meeting, "Meeting reopened", {"note": request.POST.get("note", "")})
    messages.success(request, "Meeting Minute reopened.")
    return redirect("meeting_detail", pk=meeting.pk)
