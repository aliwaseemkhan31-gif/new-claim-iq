"""Create the first administrator and their organization.

``createsuperuser`` makes a Django user and nothing else. ClaimIQ grants access
through organization membership and roles, so a bare superuser signs in to an
application that says they belong to no organization and lets them create no
projects. This command makes the first account actually usable.

Safe to re-run: an existing user keeps their password, and their membership is
repaired to System Administrator.
"""
from __future__ import annotations

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from claimiq.accounts.domain.permissions import ROLE_SYSTEM_ADMINISTRATOR
from claimiq.accounts.models import Organization, OrganizationMembership, User


class Command(BaseCommand):
    help = "Create or repair the first System Administrator and their organization."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", required=True, help="Sign-in email address.")
        parser.add_argument("--organization", required=True, help="Organization name.")
        parser.add_argument("--full-name", default="", help="Display name.")
        parser.add_argument(
            "--password",
            help=(
                "Omit to be prompted, which is preferable: a password given here "
                "is saved in your shell history."
            ),
        )

    def handle(self, *args, **options) -> None:
        email = options["email"].strip().lower()
        organization_name = options["organization"].strip()
        full_name = options["full_name"].strip()
        password = options.get("password")

        if not organization_name:
            raise CommandError("An organization name is required.")

        existing = User.objects.filter(email=email).first()
        if existing is None:
            if password is None:
                password = self._prompt_password()
            try:
                validate_password(password, user=User(email=email, full_name=full_name))
            except ValidationError as exc:
                raise CommandError("Password rejected: " + " ".join(exc.messages)) from None

        slug = slugify(organization_name)[:100] or "organization"

        with transaction.atomic():
            organization = Organization.all_objects.filter(slug=slug).first()
            if organization is None:
                organization = Organization.objects.create(name=organization_name, slug=slug)
                organization_note = "created"
            elif organization.is_deleted:
                organization.restore()
                organization_note = "restored"
            else:
                organization_note = "already existed"

            if existing is None:
                user = User.objects.create_superuser(
                    email=email, password=password, full_name=full_name
                )
                user_note = "created"
            else:
                user = existing
                user_note = "already existed (password unchanged)"

            OrganizationMembership.objects.update_or_create(
                organization=organization,
                user=user,
                defaults={"role": ROLE_SYSTEM_ADMINISTRATOR.code, "is_active": True},
            )

        self.stdout.write(self.style.SUCCESS("Administrator ready."))
        self.stdout.write(f"  user:          {user.email} ({user_note})")
        self.stdout.write(f"  organization:  {organization.name} ({organization_note})")
        self.stdout.write(f"  role:          {ROLE_SYSTEM_ADMINISTRATOR.label}")

    def _prompt_password(self) -> str:
        for _ in range(3):
            first = getpass.getpass("Password: ")
            second = getpass.getpass("Password (again): ")
            if first and first == second:
                return first
            self.stderr.write("Passwords were empty or did not match. Try again.")
        raise CommandError("No password was set.")
