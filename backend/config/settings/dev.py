"""Development settings. Never used in production."""
from __future__ import annotations

from config.env import env_bool, env_list
from config.settings.base import *  # noqa: F403
from config.settings.base import REST_FRAMEWORK

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

# Run tasks inline when no worker is available, so a developer can exercise the
# pipeline without the full stack. Off by default: the async path is the real
# one and should be what is normally tested.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
