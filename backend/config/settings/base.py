"""Base Django settings.

Every environment-specific value is read from the environment. Nothing that
differs between deployments is hardcoded here — the legacy prototype hardcoded
its model name, CORS origins, storage paths and API base URL, which made it
undeployable anywhere but one developer's machine.

Air-gapped by construction: there is no setting in this file that points at an
external service, and no code path that requires internet access.
"""
from __future__ import annotations

import os
from pathlib import Path

from config.env import env_bool, env_int, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env_str("DJANGO_SECRET_KEY", required=True)
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "corsheaders",
]

LOCAL_APPS = [
    "claimiq.core",
    "claimiq.accounts",
    "claimiq.projects",
    "claimiq.documents",
    "claimiq.ingestion",
    "claimiq.clauses",
    "claimiq.knowledge",
    "claimiq.claims",
    "claimiq.correspondence",
    "claimiq.search",
    "claimiq.ai",
    "claimiq.analysis",
    "claimiq.reports",
    "claimiq.notifications",
    "claimiq.audit",
    "claimiq.imports",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Assigns a request id and binds it to the logging context, so every log
    # line and every audit row for one request can be correlated.
    "claimiq.core.middleware.RequestContextMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("POSTGRES_DB", default="claimiq"),
        "USER": env_str("POSTGRES_USER", default="claimiq"),
        "PASSWORD": env_str("POSTGRES_PASSWORD", required=True),
        "HOST": env_str("POSTGRES_HOST", default="postgres"),
        "PORT": env_int("POSTGRES_PORT", default=5432),
        "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", default=60),
        "OPTIONS": {
            "connect_timeout": 10,
            "application_name": "claimiq",
        },
    }
}

# ---------------------------------------------------------------------------
# Cache and broker
# ---------------------------------------------------------------------------

REDIS_URL = env_str("REDIS_URL", default="redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env_str("REDIS_CACHE_URL", default="redis://redis:6379/1"),
        "KEY_PREFIX": "claimiq",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = env_str("CELERY_RESULT_BACKEND", default="redis://redis:6379/2")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# Acknowledge only after completion so a worker crash re-queues the job rather
# than losing it. Ingestion jobs are idempotent (see DOCUMENT_PROCESSING.md),
# which is what makes late acknowledgement safe.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# One task at a time per worker process: OCR and inference are memory-hungry and
# prefetching several would risk the OOM killer taking the worker down.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = env_int("CELERY_MAX_TASKS_PER_CHILD", default=50)

CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "claimiq.ingestion.tasks.*": {"queue": "ingestion"},
    "claimiq.ai.tasks.*": {"queue": "ai"},
    "claimiq.analysis.tasks.*": {"queue": "ai"},
    "claimiq.reports.tasks.*": {"queue": "default"},
}

CELERY_TASK_SOFT_TIME_LIMIT = env_int("CELERY_SOFT_TIME_LIMIT", default=3600)
CELERY_TASK_TIME_LIMIT = env_int("CELERY_TIME_LIMIT", default=4200)

CELERY_BEAT_SCHEDULE = {
    # Backstop for jobs whose worker died without the broker re-queueing the
    # task. Every stage is idempotent and checkpointed, so re-dispatching a
    # stalled job resumes rather than restarting and loses nothing.
    "reap-stalled-ingestion-jobs": {
        "task": "claimiq.ingestion.tasks.reap_stalled_jobs",
        "schedule": env_int("REAP_STALLED_INTERVAL_SECONDS", default=900),
        "kwargs": {"stale_minutes": env_int("STALLED_JOB_MINUTES", default=120)},
        "options": {"queue": "default"},
    },
}

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Deny by default. A view that forgets to declare permissions is closed,
    # not open — the inverse of the prototype, where every endpoint was public.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "claimiq.core.api.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "EXCEPTION_HANDLER": "claimiq.core.api.exceptions.exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth": env_str("THROTTLE_AUTH", default="10/min"),
        "upload": env_str("THROTTLE_UPLOAD", default="60/hour"),
        "ai": env_str("THROTTLE_AI", default="120/hour"),
        "search": env_str("THROTTLE_SEARCH", default="600/hour"),
    },
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", default=[])

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", default=60 * 60 * 12)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", default=False)
CSRF_COOKIE_HTTPONLY = False  # the SPA must read it to echo the header
CSRF_COOKIE_SAMESITE = "Lax"

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": env_int("PASSWORD_MIN_LENGTH", default=12)},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Argon2 first: memory-hard and the current Django recommendation.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

# ---------------------------------------------------------------------------
# Files and storage
# ---------------------------------------------------------------------------

MEDIA_ROOT = Path(env_str("MEDIA_ROOT", default=str(BASE_DIR / "var" / "media")))
MEDIA_URL = "/media/"
STATIC_ROOT = Path(env_str("STATIC_ROOT", default=str(BASE_DIR / "var" / "static")))
STATIC_URL = "/static/"

#: Hard ceiling on a single upload. Enforced again at the service layer so it
#: applies to every ingress path, not just multipart form posts.
MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", default=512 * 1024 * 1024)

#: Stream to disk above this size rather than buffering in memory. Contract sets
#: run to hundreds of megabytes; buffering them would exhaust the web process.
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("FILE_UPLOAD_MAX_MEMORY_SIZE", default=2 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = FILE_UPLOAD_MAX_MEMORY_SIZE
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000
FILE_UPLOAD_PERMISSIONS = 0o640

ALLOWED_UPLOAD_MIME_TYPES = env_list(
    "ALLOWED_UPLOAD_MIME_TYPES",
    default=[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/plain",
        "text/csv",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "message/rfc822",
    ],
)

# ---------------------------------------------------------------------------
# AI providers (all local)
# ---------------------------------------------------------------------------

AI_SETTINGS = {
    "OLLAMA_BASE_URL": env_str("OLLAMA_BASE_URL", default="http://ollama:11434"),
    "OLLAMA_TIMEOUT_SECONDS": env_int("OLLAMA_TIMEOUT_SECONDS", default=300),
    # No model name is hardcoded. The active model is resolved from the
    # ModelConfiguration table at call time, so an administrator can change it
    # without a deploy. The values below seed an empty installation only.
    "DEFAULT_LLM_MODEL": env_str("DEFAULT_LLM_MODEL", default=""),
    "DEFAULT_EMBEDDING_MODEL": env_str("DEFAULT_EMBEDDING_MODEL", default=""),
    "DEFAULT_RERANKER_MODEL": env_str("DEFAULT_RERANKER_MODEL", default=""),
    "EMBEDDING_DIMENSIONS": env_int("EMBEDDING_DIMENSIONS", default=1024),
    "HARDWARE_PROFILE": env_str("HARDWARE_PROFILE", default="cpu"),
    "MODEL_CACHE_DIR": env_str("MODEL_CACHE_DIR", default="/models"),
}

# Belt and braces for an air-gapped install: these libraries will not attempt a
# network fetch even if a model is missing from the local cache.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

RETRIEVAL_SETTINGS = {
    "LEXICAL_CANDIDATES": env_int("RETRIEVAL_LEXICAL_CANDIDATES", default=50),
    "VECTOR_CANDIDATES": env_int("RETRIEVAL_VECTOR_CANDIDATES", default=50),
    "RERANK_CANDIDATES": env_int("RETRIEVAL_RERANK_CANDIDATES", default=30),
    "FINAL_CONTEXT_CHUNKS": env_int("RETRIEVAL_FINAL_CHUNKS", default=12),
    "RRF_K": env_int("RETRIEVAL_RRF_K", default=60),
    "MAX_CHUNKS_PER_DOCUMENT": env_int("RETRIEVAL_MAX_PER_DOCUMENT", default=4),
}

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Logging — structured, no print()
# ---------------------------------------------------------------------------

LOG_LEVEL = env_str("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {
            "()": "claimiq.core.logging.RequestContextFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "claimiq.core.logging.JSONFormatter",
        },
        "console": {
            "format": "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": env_str("LOG_FORMAT", default="json"),
            "filters": ["request_context"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "claimiq": {"level": LOG_LEVEL, "propagate": True},
        "celery": {"level": "INFO", "propagate": True},
    },
}
