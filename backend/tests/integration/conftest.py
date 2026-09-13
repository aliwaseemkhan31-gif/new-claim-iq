"""Django setup for the integration suite.

Configures Django explicitly rather than through pytest-django's ini option,
because that option is global and would force every test — including the pure
domain suite, which must stay runnable on a bare Python 3.9 with no Django
installed — to load the framework. See ADR 0001 and docs/TESTING.md.

Tests in this package that mock the repository layer need no database. Tests
that touch the ORM do, and are marked accordingly.
"""
from __future__ import annotations

import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-secret")
os.environ.setdefault("POSTGRES_PASSWORD", "test-only")

django.setup()


@pytest.fixture
def organization_id():
    from uuid import UUID

    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def project_id():
    from uuid import UUID

    return UUID("22222222-2222-2222-2222-222222222222")
