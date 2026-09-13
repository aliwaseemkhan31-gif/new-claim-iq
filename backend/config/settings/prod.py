"""Production settings for a standalone LAN or air-gapped server."""
from __future__ import annotations

from config.env import env_bool
from config.settings.base import *  # noqa: F403
from config.settings.base import LOGGING

DEBUG = False

# TLS is terminated at nginx. These are opt-in because a genuinely isolated
# LAN deployment may run plain HTTP behind a firewall, and forcing redirects
# there would make the product unreachable rather than more secure.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", default=True)
SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_SSL_REDIRECT
SECURE_HSTS_PRELOAD = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    **LOGGING,
    "root": {"handlers": ["console"], "level": "INFO"},
}
