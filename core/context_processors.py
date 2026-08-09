from .models import PasswordResetRequest, SystemSetting, User


def application_context(request):
    pending = 0
    pending_resets = 0
    if request.user.is_authenticated and request.user.is_admin:
        pending = User.objects.filter(account_status=User.AccountStatus.PENDING).count()
        pending_resets = PasswordResetRequest.objects.filter(status=PasswordResetRequest.Status.PENDING).count()
    return {
        "page_base_template": "partial_base.html" if request.headers.get("X-MMP-Partial") else "base.html",
        "pending_user_count": pending,
        "pending_password_reset_count": pending_resets,
        "pending_request_count": pending + pending_resets,
        "system_settings": SystemSetting.load(),
    }
