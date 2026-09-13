"""Test settings.

Optimised for speed where it does not change behaviour under test. Password
hashing is weakened deliberately — Argon2 is correct in production and makes a
test suite that creates users unbearably slow.
"""
from __future__ import annotations

from config.settings.base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-not-a-secret"  # noqa: S105

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Tests must never reach a model runtime. Any provider call must be against the
# fake provider or an explicit stub; a real call is a test bug.
AI_SETTINGS = {
    "OLLAMA_BASE_URL": "http://127.0.0.1:0",
    "OLLAMA_TIMEOUT_SECONDS": 1,
    "DEFAULT_LLM_MODEL": "",
    "DEFAULT_EMBEDDING_MODEL": "",
    "DEFAULT_RERANKER_MODEL": "",
    "EMBEDDING_DIMENSIONS": 8,
    "HARDWARE_PROFILE": "cpu",
    "MODEL_CACHE_DIR": "/tmp/claimiq-models",  # noqa: S108
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
