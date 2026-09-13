"""Seed reference data.

Run by the `migrate` service in docker-compose after migrations, and safe to
re-run: everything here is idempotent.

Reference data only — contract forms, editions, document types, role
definitions. No projects, no documents, no sample claims. A seeded "demo
project" would be fabricated data indistinguishable from real data once someone
starts working in the installation, and this product's whole posture is that
you can trust what it shows you.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from claimiq.accounts.domain.permissions import (
    ALL_PERMISSIONS,
    SYSTEM_ROLES,
    validate_role_definitions,
)
from claimiq.documents.domain.taxonomy import DEFAULT_TAXONOMY
from claimiq.ingestion.domain.pipeline import validate_stage_order
from claimiq.knowledge.domain.editions import DEFAULT_REGISTRY


class Command(BaseCommand):
    help = "Validate and report the reference data this installation ships with."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Validate the catalogues and exit non-zero on any problem.",
        )

    def handle(self, *args, **options) -> None:
        problems: list[str] = []

        # The catalogues are code, not rows: editions, document types,
        # permissions and pipeline stages are all defined in the domain layer
        # so they can be unit-tested without a database. What this command does
        # is assert at deploy time that they are internally consistent, so a
        # bad catalogue fails the deployment rather than surfacing later as a
        # role that silently grants nothing.
        problems.extend(validate_role_definitions())
        problems.extend(validate_stage_order())

        from claimiq.ai.domain.prompts import validate_prompts

        problems.extend(validate_prompts())

        if problems:
            for problem in problems:
                self.stderr.write(self.style.ERROR(f"  {problem}"))
            raise SystemExit(1)

        editions = DEFAULT_REGISTRY.editions()
        types = DEFAULT_TAXONOMY.all()

        self.stdout.write(self.style.SUCCESS("Reference data validated."))
        self.stdout.write(f"  contract forms:   {len(DEFAULT_REGISTRY.forms())}")
        self.stdout.write(f"  editions:         {len(editions)}")
        for edition in editions:
            self.stdout.write(f"    - {edition.code}: {edition.label}")
        self.stdout.write(f"  document types:   {len(types)}")
        self.stdout.write(f"  roles:            {len(SYSTEM_ROLES)}")
        self.stdout.write(f"  permissions:      {len(ALL_PERMISSIONS)}")

        if options.get("check_only"):
            return

        self.stdout.write("")
        self.stdout.write(
            "No projects, documents or claims are seeded. This installation "
            "starts empty by design."
        )
