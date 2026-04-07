import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
#ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testapp.begintimes.by,178.163.244.93")
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testapp.begintimes.by", "178.163.244.93"]
CSRF_TRUSTED_ORIGINS = [
    "http://testapp.begintimes.by",
    "https://testapp.begintimes.by",
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'courses',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lms.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'lms' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

db_engine = os.getenv("DB_ENGINE", "").strip()
db_host = os.getenv("DB_HOST", "").strip()
use_postgres = bool(db_engine) or bool(db_host)

if use_postgres:
    DATABASES = {
        "default": {
            "ENGINE": db_engine or "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "lms"),
            "USER": os.getenv("DB_USER", "lmsuser"),
            "PASSWORD": os.getenv("DB_PASSWORD", "lmspass"),
            "HOST": db_host or "localhost",
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    sqlite_name = os.getenv("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_name,
        }
    }

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'lms' / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"        # куда после входа
LOGOUT_REDIRECT_URL = "/accounts/login/"
