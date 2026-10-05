"""Allow a question to be put to a standard form alone.

Until now every AI question belonged to a project, because every question was
asked against a project's documents. Asking what the Red Book itself says is a
question with no project behind it — a user checking the form before a project
exists, or reading across projects — so ``project`` becomes nullable.

The tenant boundary cannot then be read through the project, so it is carried
on the row. Existing rows are backfilled from their project, which is where
their organization has always been.
"""
from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def backfill_organization(apps, schema_editor):
    AIQuestion = apps.get_model("ai", "AIQuestion")
    Project = apps.get_model("projects", "Project")
    organizations = dict(Project.objects.values_list("id", "organization_id"))
    updates = []
    for question in AIQuestion.objects.filter(organization__isnull=True).only(
        "id", "project_id"
    ):
        organization_id = organizations.get(question.project_id)
        if organization_id is None:
            continue
        question.organization_id = organization_id
        updates.append(question)
    if updates:
        AIQuestion.objects.bulk_update(updates, ["organization"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0002_modelbenchmarkrun_modelconfiguration_and_more"),
        ("accounts", "0001_initial"),
        ("projects", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="aiquestion",
            name="organization",
            field=models.ForeignKey(
                help_text=(
                    "Tenant the question belongs to. Carried on the row rather "
                    "than read through the project, because a standard-form "
                    "question has no project and must still be confined to one "
                    "organization."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ai_questions",
                to="accounts.organization",
            ),
        ),
        migrations.AlterField(
            model_name="aiquestion",
            name="project",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The project the question was asked about. Null for a "
                    "question put to a standard form alone, which belongs to no "
                    "project — see `edition_code` for what it was asked against."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ai_questions",
                to="projects.project",
            ),
        ),
        migrations.RunPython(backfill_organization, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="aiquestion",
            index=models.Index(
                fields=["edition_code", "-created_at"],
                name="ai_question_edition_3e061c_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="aiquestion",
            index=models.Index(
                fields=["organization", "-created_at"],
                name="ai_question_organiz_6a48ef_idx",
            ),
        ),
    ]
