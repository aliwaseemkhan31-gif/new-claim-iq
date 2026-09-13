"""Import from the legacy ClaimIQ prototype.

The prototype is a **read-only reference** and lives outside this repository.
Nothing in this module writes to, moves or modifies anything under it; every
function takes a path and returns parsed data, so the caller owns locating it.

Three artefacts are worth bringing across:

1. **Clause skeletons** (``fidic_1987_skeleton.py``, ``fidic_2017_skeleton.py``)
   — clause number → title maps transcribed by hand from each source PDF's
   contents pages, including annotated source quirks. This is genuine domain
   work that would be tedious and error-prone to redo, and it was dead code in
   the prototype: nothing imported it.

2. **Extracted document text** (``db.json``) — page text and clause indexes for
   documents already processed, so a migrating deployment does not have to
   re-OCR its corpus.

3. **Clause indexes** — useful as a *comparison baseline*. Running the new
   detector over the same pages and diffing against the prototype's index shows
   concretely what the new structure detection changes.

Deliberately **not** imported: the prototype's embeddings. They were produced by
``all-MiniLM-L6-v2`` at 384 dimensions; the default here is BGE-M3 at 1024.
Vectors from different models are not comparable, and importing them would
silently corrupt retrieval. Text is imported and re-embedded instead. This is
recorded in the report so the operator sees the decision rather than wondering
where the vectors went.

The skeleton files are Python modules. They are parsed with :mod:`ast` and
**never imported or executed** — running code from a data file is not something
an import tool should do, even when the file is one we shipped.

Pure stdlib; runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from claimiq.core.domain.errors import ImportValidationError, NotFoundError


@dataclass(frozen=True)
class LegacyClause:
    """One clause from a legacy skeleton."""

    number: str
    title: str
    edition: str
    parent_number: str | None = None

    @property
    def depth(self) -> int:
        return len([p for p in self.number.split(".") if p])


@dataclass
class LegacySkeleton:
    """A parsed clause skeleton."""

    edition: str
    clauses: list[LegacyClause] = field(default_factory=list)
    source_notes: str = ""

    def top_level(self) -> list[LegacyClause]:
        return [c for c in self.clauses if c.depth == 1]

    def numbers(self) -> list[str]:
        return [c.number for c in self.clauses]

    def missing_from_sequence(self) -> list[str]:
        """Gaps in top-level numbering.

        The 1987 reprint jumps 25 → 27, and this surfaces that so it can be
        recorded as a known-absent clause rather than misread as a parse failure.
        """
        numbers = sorted(
            (int(c.number) for c in self.top_level() if c.number.isdigit())
        )
        if not numbers:
            return []
        gaps: list[str] = []
        for expected in range(numbers[0], numbers[-1] + 1):
            if expected not in numbers:
                gaps.append(str(expected))
        return gaps


@dataclass
class LegacyDocument:
    """A document record from the prototype's ``db.json``."""

    legacy_id: str
    filename: str
    kind: str  # "contract" | "claim"
    page_count: int
    pages: list[dict[str, Any]] = field(default_factory=list)
    clause_index: list[dict[str, Any]] = field(default_factory=list)
    ocr_method: str = ""
    uploaded_at: str = ""

    @property
    def total_characters(self) -> int:
        return sum(len(p.get("text", "")) for p in self.pages)

    @property
    def empty_page_count(self) -> int:
        return sum(1 for p in self.pages if not p.get("text", "").strip())


@dataclass
class ImportIssue:
    severity: str  # "error" | "warning" | "info"
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportPlan:
    """The outcome of a dry run.

    Produced before anything is written, so an operator can see exactly what
    would be created and decide whether to proceed.
    """

    skeletons: list[LegacySkeleton] = field(default_factory=list)
    documents: list[LegacyDocument] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ImportIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ImportIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def can_proceed(self) -> bool:
        return not self.errors

    def clause_count(self) -> int:
        return sum(len(s.clauses) for s in self.skeletons)

    def page_count(self) -> int:
        return sum(d.page_count for d in self.documents)

    def summary(self) -> str:
        return (
            f"{len(self.skeletons)} skeleton(s) / {self.clause_count()} clauses, "
            f"{len(self.documents)} document(s) / {self.page_count()} pages, "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        )


# ---------------------------------------------------------------------------
# Skeleton parsing
# ---------------------------------------------------------------------------


def _literal(node: ast.AST) -> Any:
    """Evaluate a literal node, returning None when it is not one.

    ``ast.literal_eval`` only accepts literals, so this cannot execute code
    even if the source file were modified to contain some.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def parse_skeleton_source(source: str, *, filename: str = "<skeleton>") -> LegacySkeleton:
    """Parse a legacy skeleton module's ``SKELETON`` assignment.

    Args:
        source: The module source text.
        filename: Name used in error messages.

    Raises:
        ImportValidationError: no usable ``SKELETON`` mapping was found.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ImportValidationError(
            f"Could not parse {filename}: {exc.msg}",
            details={"filename": filename, "line": exc.lineno},
        ) from None

    skeleton_value: Any = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SKELETON":
                skeleton_value = _literal(node.value)

    if not isinstance(skeleton_value, dict):
        raise ImportValidationError(
            f"{filename} does not define a SKELETON dictionary.",
            details={"filename": filename},
        )

    edition = str(skeleton_value.get("edition", "")).strip()
    if not edition:
        raise ImportValidationError(
            f"{filename} SKELETON has no 'edition' key. An edition is mandatory: "
            f"clauses cannot be imported without knowing which contract form "
            f"they belong to.",
            details={"filename": filename},
        )

    raw_clauses = skeleton_value.get("clauses")
    if not isinstance(raw_clauses, dict):
        raise ImportValidationError(
            f"{filename} SKELETON has no 'clauses' mapping.",
            details={"filename": filename, "edition": edition},
        )

    clauses: list[LegacyClause] = []
    for number, payload in raw_clauses.items():
        number = str(number).strip()
        if not number:
            continue
        if isinstance(payload, dict):
            title = str(payload.get("title", "")).strip()
            clauses.append(LegacyClause(number=number, title=title, edition=edition))
            subclauses = payload.get("subclauses")
            if isinstance(subclauses, dict):
                for sub_number, sub_title in subclauses.items():
                    sub_number = str(sub_number).strip()
                    if not sub_number:
                        continue
                    clauses.append(
                        LegacyClause(
                            number=sub_number,
                            title=str(sub_title).strip(),
                            edition=edition,
                            parent_number=number,
                        )
                    )
        else:
            clauses.append(
                LegacyClause(number=number, title=str(payload).strip(), edition=edition)
            )

    clauses.sort(key=lambda c: tuple(
        int(p) if p.isdigit() else 0 for p in c.number.split(".")
    ))

    return LegacySkeleton(
        edition=edition,
        clauses=clauses,
        source_notes=ast.get_docstring(tree) or "",
    )


def parse_skeleton_file(path: Path) -> LegacySkeleton:
    """Parse a skeleton module from disk. Read-only."""
    if not path.is_file():
        raise NotFoundError(
            f"Legacy skeleton not found: {path.name}", details={"path": str(path)}
        )
    return parse_skeleton_source(
        path.read_text(encoding="utf-8", errors="replace"), filename=path.name
    )


# ---------------------------------------------------------------------------
# db.json parsing
# ---------------------------------------------------------------------------


def parse_legacy_db(payload: dict[str, Any]) -> list[LegacyDocument]:
    """Extract document records from a parsed ``db.json``.

    The prototype stored contracts and claims under separate top-level keys,
    each a mapping of id → record, with page text embedded inline.
    """
    documents: list[LegacyDocument] = []

    for kind, key in (("contract", "contracts"), ("claim", "claims")):
        section = payload.get(key)
        if not isinstance(section, dict):
            continue
        for legacy_id, record in section.items():
            if not isinstance(record, dict):
                continue
            pages = record.get("pages")
            pages = pages if isinstance(pages, list) else []
            clause_index = record.get("clause_index")
            clause_index = clause_index if isinstance(clause_index, list) else []
            documents.append(
                LegacyDocument(
                    legacy_id=str(legacy_id),
                    filename=str(
                        record.get("filename")
                        or record.get("original_filename")
                        or f"{legacy_id}.pdf"
                    ),
                    kind=kind,
                    page_count=int(record.get("page_count") or len(pages)),
                    pages=pages,
                    clause_index=clause_index,
                    ocr_method=str(record.get("ocr_method", "")),
                    uploaded_at=str(record.get("uploaded_at", "")),
                )
            )

    return documents


def parse_legacy_db_file(path: Path) -> list[LegacyDocument]:
    """Parse ``db.json`` from disk. Read-only."""
    if not path.is_file():
        raise NotFoundError(
            f"Legacy database not found: {path.name}", details={"path": str(path)}
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ImportValidationError(
            f"Could not parse {path.name}: {exc.msg}",
            details={"path": str(path), "line": exc.lineno},
        ) from None
    if not isinstance(payload, dict):
        raise ImportValidationError(f"{path.name} is not a JSON object.")
    return parse_legacy_db(payload)


# ---------------------------------------------------------------------------
# Validation and planning
# ---------------------------------------------------------------------------


def validate_skeleton(
    skeleton: LegacySkeleton, known_editions: Iterable[str] = ()
) -> list[ImportIssue]:
    """Check a skeleton before it is imported."""
    issues: list[ImportIssue] = []
    known = set(known_editions)

    if known and skeleton.edition not in known:
        issues.append(
            ImportIssue(
                "warning",
                f"Skeleton edition {skeleton.edition!r} does not match a "
                f"registered edition code. It must be mapped before import so "
                f"clauses are not filed under an unknown edition.",
                {"edition": skeleton.edition, "known": sorted(known)},
            )
        )

    if not skeleton.clauses:
        issues.append(
            ImportIssue("error", f"Skeleton {skeleton.edition!r} contains no clauses.")
        )
        return issues

    untitled = [c.number for c in skeleton.clauses if not c.title]
    if untitled:
        issues.append(
            ImportIssue(
                "warning",
                f"{len(untitled)} clause(s) have no title and will import with an "
                f"empty title: {', '.join(untitled[:10])}"
                + (" ..." if len(untitled) > 10 else ""),
                {"numbers": untitled},
            )
        )

    duplicates = _duplicates(skeleton.numbers())
    if duplicates:
        issues.append(
            ImportIssue(
                "error",
                f"Duplicate clause numbers in skeleton: {', '.join(sorted(duplicates))}",
                {"numbers": sorted(duplicates)},
            )
        )

    gaps = skeleton.missing_from_sequence()
    if gaps:
        issues.append(
            ImportIssue(
                "info",
                f"Top-level numbering skips {', '.join(gaps)}. If genuine in the "
                f"source, record these as absent clauses so knowledge-base "
                f"validation does not report them as missing.",
                {"gaps": gaps},
            )
        )

    orphans = _orphans(skeleton)
    if orphans:
        issues.append(
            ImportIssue(
                "warning",
                f"{len(orphans)} sub-clause(s) reference a parent that is not in "
                f"the skeleton: {', '.join(orphans[:10])}",
                {"numbers": orphans},
            )
        )

    return issues


def validate_document(document: LegacyDocument) -> list[ImportIssue]:
    """Check a legacy document before it is imported."""
    issues: list[ImportIssue] = []

    if not document.pages:
        issues.append(
            ImportIssue(
                "error",
                f"Document {document.filename!r} has no extracted pages. There is "
                f"nothing to import; re-process the source file instead.",
                {"legacy_id": document.legacy_id},
            )
        )
        return issues

    if document.page_count != len(document.pages):
        issues.append(
            ImportIssue(
                "warning",
                f"Document {document.filename!r} declares {document.page_count} "
                f"pages but carries {len(document.pages)}.",
                {"legacy_id": document.legacy_id},
            )
        )

    if document.empty_page_count:
        share = document.empty_page_count / len(document.pages)
        severity = "warning" if share > 0.3 else "info"
        issues.append(
            ImportIssue(
                severity,
                f"Document {document.filename!r} has {document.empty_page_count} "
                f"empty page(s) of {len(document.pages)} "
                f"({share:.0%}). Likely an incomplete extraction in the source system.",
                {"legacy_id": document.legacy_id},
            )
        )

    if document.total_characters == 0:
        issues.append(
            ImportIssue(
                "error",
                f"Document {document.filename!r} contains no text at all.",
                {"legacy_id": document.legacy_id},
            )
        )

    return issues


def build_import_plan(
    *,
    skeletons: Iterable[LegacySkeleton] = (),
    documents: Iterable[LegacyDocument] = (),
    known_editions: Iterable[str] = (),
) -> ImportPlan:
    """Assemble a dry-run plan with every validation finding.

    Nothing is written. ``can_proceed`` is False if any error was raised.
    """
    plan = ImportPlan(skeletons=list(skeletons), documents=list(documents))

    for skeleton in plan.skeletons:
        plan.issues.extend(validate_skeleton(skeleton, known_editions))
    for document in plan.documents:
        plan.issues.extend(validate_document(document))

    editions = [s.edition for s in plan.skeletons]
    duplicate_editions = _duplicates(editions)
    if duplicate_editions:
        plan.issues.append(
            ImportIssue(
                "error",
                f"More than one skeleton supplied for edition(s) "
                f"{', '.join(sorted(duplicate_editions))}. Importing both would "
                f"merge two clause sets into one edition.",
                {"editions": sorted(duplicate_editions)},
            )
        )

    if plan.documents:
        plan.issues.append(
            ImportIssue(
                "info",
                "Legacy embeddings are not imported. They were produced by a "
                "384-dimension model and are not comparable with the current "
                "embedding model. Imported text is re-embedded after import.",
                {"documents": len(plan.documents)},
            )
        )

    return plan


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _orphans(skeleton: LegacySkeleton) -> list[str]:
    present = set(skeleton.numbers())
    return [
        c.number
        for c in skeleton.clauses
        if c.parent_number and c.parent_number not in present
    ]
