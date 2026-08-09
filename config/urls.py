from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from core import views
from core.forms import WorkspaceAuthenticationForm


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=WorkspaceAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("", include("core.urls")),
]
