"""Settings for the Order-to-Audit prototype."""
import os
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
# Hosts like Render terminate HTTPS in front of the app: trust their forwarded headers.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")   # set automatically by Render
if _render_host:
    ALLOWED_HOSTS.append(_render_host)
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "masters",
    "inventory",
    "production",
    "sales",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "accounts.context_processors.company",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite keeps the demo to one command. Every money/qty column is a DecimalField,
# so switching to PostgreSQL is a settings change only (see README).
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.environ.get("DB_PATH", BASE_DIR / "db.sqlite3"),
}}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

AUTH_PASSWORD_VALIDATORS = []  # demo logins; enable validators in production

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- Business configuration ------------------------------------------------
COMPANY = {
    "name": "MAS Snacks (Demo)",
    "gstin": "33ABCDE1234F1Z5",          # fictitious
    "state_code": "33",                   # Tamil Nadu
    "state_name": "Tamil Nadu",
    "address": "12, Industrial Estate, Tiruchirappalli, Tamil Nadu - 620 015",
    "phone": "0431-0000000",
    "email": "accounts@example.com",
}
# Default variance tolerance (%) for planned-vs-actual flags; each BOM can override it.
DEMO_LOGINS = os.environ.get("DEMO_LOGINS", "1") == "1"   # show the quick sign-in panel on the login page
DEFAULT_VARIANCE_TOLERANCE = Decimal("3")

import sys
if "test" in sys.argv:  # fast hashing so the test-suite runs quickly
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
