from pathlib import Path
import os
import sys

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
IS_TESTING = "test" in sys.argv
DEBUG = os.getenv("DEBUG", "1") == "1"
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY and IS_TESTING:
    SECRET_KEY = "test-only-secret-key-not-used-outside-the-test-suite"
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set.")
if not DEBUG and (
    SECRET_KEY.startswith("dev-only-")
    or "replace-with" in SECRET_KEY.lower()
    or len(SECRET_KEY) < 50
):
    raise ImproperlyConfigured("SECRET_KEY must be a long, non-placeholder value when DEBUG=0.")
ALLOWED_HOSTS = [item.strip() for item in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if item.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.context_processors.application_context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

if IS_TESTING and not os.getenv("POSTGRES_DB"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    required_database_settings = {
        "POSTGRES_DB": os.getenv("POSTGRES_DB"),
        "POSTGRES_USER": os.getenv("POSTGRES_USER"),
        "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
    }
    missing_database_settings = [
        name for name, value in required_database_settings.items() if not value
    ]
    if missing_database_settings:
        raise ImproperlyConfigured(
            "PostgreSQL configuration is required. Missing: "
            + ", ".join(missing_database_settings)
        )
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required_database_settings["POSTGRES_DB"],
        "USER": required_database_settings["POSTGRES_USER"],
        "PASSWORD": required_database_settings["POSTGRES_PASSWORD"],
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Ho_Chi_Minh")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "assets"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
TIMELINE_MAX_FUTURE_YEARS = max(1, int(os.getenv("TIMELINE_MAX_FUTURE_YEARS", "10")))
