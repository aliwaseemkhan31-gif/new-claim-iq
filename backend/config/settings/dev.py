"""Development settings. Never used in production.

Built to run on a plain workstation with only PostgreSQL (with pgvector) and,
optionally, Ollama installed: it reads the repository's ``.env``, and when
Redis is absent it falls back to an in-process cache and runs background tasks
inline. Docker-compose never uses this module.
"""
from __future__ import annotations

from pathlib import Path

from config.env import env_bool, env_list, env_str, load_env_file

# Loaded before the base settings are imported, because they read the
# environment at import time. Shell variables win over the file. The file's
# DJANGO_SETTINGS_MODULE (which names the production module, for compose)
# cannot displace this one, because manage.py sets it before settings load.
load_env_file(Path(__file__).resolve().parents[3] / ".env")

from config.settings.base import *  # noqa: E402,F403
from config.settings.base import AI_SETTINGS, REST_FRAMEWORK  # noqa: E402

DEBUG = True
ALLOWED_HOSTS = ["*"]

CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:5273", "http://127.0.0.1:5273"],
)
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# Browsable API is convenient in development and must not ship to production.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# Redis is optional here. Without it the base settings cannot cache or keep
# sessions, and every sign-in fails. Set USE_REDIS=true if one is running.
USE_REDIS = env_bool("USE_REDIS", default=False)
if not USE_REDIS:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "claimiq-dev",
        }
    }
    SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Without a broker nothing would consume queued jobs, so an upload would sit at
# "queued" forever. Tasks therefore run inline in the request that queued them
# unless a broker is available. Uploads block while the pipeline runs — slow
# for a large PDF, but the document is actually processed.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", default=not USE_REDIS)
CELERY_TASK_EAGER_PROPAGATES = True

# Ollama on the host, rather than the compose service name `ollama`.
AI_SETTINGS = {
    **AI_SETTINGS,
    "OLLAMA_BASE_URL": env_str("OLLAMA_BASE_URL", default="http://127.0.0.1:11434"),
}
