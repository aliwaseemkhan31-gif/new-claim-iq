"""Contract form and edition registry.

Editions are data, never string literals scattered through the codebase. This
module is the single authority on which contract forms the system understands
and how their clause numbering is organised.

Why this exists (ADR 0004): FIDIC editions differ in ways that change the answer
to a contractual question. Claims procedure is Clause 53 in the 1987 Red Book
and Clause 20 in the 2017 Red Book; disputes move from 67 to 21. Answering a
2017 question with 1987 text is a wrong answer delivered confidently.

The registry is deliberately open: adding the 1999 Red Book, a Yellow/Silver
Book, or a bespoke amended form is a data change here, not a code change
elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from claimiq.core.domain.errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class ContractForm:
    """A family of standard-form contracts (e.g. the FIDIC Red Book)."""

    code: str
    publisher: str
    name: str
    description: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValidationError("ContractForm.code must not be empty")


@dataclass(frozen=True)
class TopicMapping:
    """Where a recurring contractual topic lives in a given edition.

    Clause numbering is not stable across editions, so the *topic* is the stable
    key and the clause number is edition-specific. This lets the application ask
    "where is the claims procedure for this project's contract?" without
    hardcoding a number.
    """

    topic: str
    clause_number: str
    title: str


@dataclass(frozen=True)
class ContractEdition:
    """A specific published edition of a contract form.

    Attributes:
        code: Stable identifier used in scopes, metadata and citations.
            Format: ``{form}-{year}``, e.g. ``red-book-2017``.
        form_code: The :class:`ContractForm` this belongs to.
        year: Publication year of the edition.
        label: Human-readable name shown in citations and the UI. This is what
            a reader sees, so it must be unambiguous on its own.
        topic_map: Topic → clause mapping for this edition.
        absent_clauses: Clause numbers that do not exist in this edition. The
            1987 reprint jumps from 25 to 27, a quirk recorded by hand in the
            legacy skeleton and preserved here so validation does not report a
            genuine gap as a missing clause.
        notes: Provenance and known source quirks.
    """

    code: str
    form_code: str
    year: int
    label: str
    topic_map: Mapping[str, TopicMapping] = field(default_factory=dict)
    absent_clauses: frozenset[str] = frozenset()
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.code:
            raise ValidationError("ContractEdition.code must not be empty")
        if self.year < 1900 or self.year > 2200:
            raise ValidationError(
                f"ContractEdition.year out of range: {self.year}",
                details={"edition": self.code, "year": self.year},
            )

    def clause_for_topic(self, topic: str) -> TopicMapping | None:
        """Return the clause covering ``topic`` in this edition, if mapped."""
        return self.topic_map.get(topic)

    def citation_prefix(self) -> str:
        """Edition label as it appears at the head of a citation.

        Always includes the edition, so a reader can never mistake which
        contract form a quoted provision came from.
        """
        return self.label


# --------------------------------------------------------------------------
# Recurring contractual topics
# --------------------------------------------------------------------------

TOPIC_CLAIMS_PROCEDURE = "claims_procedure"
TOPIC_DISPUTES = "disputes"
TOPIC_EXTENSION_OF_TIME = "extension_of_time"
TOPIC_VARIATIONS = "variations"
TOPIC_PAYMENT = "payment"
TOPIC_TERMINATION = "termination"
TOPIC_ENGINEER = "engineer"
TOPIC_CONTRACTOR_OBLIGATIONS = "contractor_obligations"
TOPIC_EMPLOYER_OBLIGATIONS = "employer_obligations"
TOPIC_FORCE_MAJEURE = "force_majeure"
TOPIC_PHYSICAL_CONDITIONS = "physical_conditions"
TOPIC_SUSPENSION = "suspension"

ALL_TOPICS = (
    TOPIC_CLAIMS_PROCEDURE,
    TOPIC_DISPUTES,
    TOPIC_EXTENSION_OF_TIME,
    TOPIC_VARIATIONS,
    TOPIC_PAYMENT,
    TOPIC_TERMINATION,
    TOPIC_ENGINEER,
    TOPIC_CONTRACTOR_OBLIGATIONS,
    TOPIC_EMPLOYER_OBLIGATIONS,
    TOPIC_FORCE_MAJEURE,
    TOPIC_PHYSICAL_CONDITIONS,
    TOPIC_SUSPENSION,
)


# --------------------------------------------------------------------------
# Known contract forms
# --------------------------------------------------------------------------

FORM_FIDIC_RED_BOOK = ContractForm(
    code="fidic-red-book",
    publisher="FIDIC",
    name="Conditions of Contract for Construction (Red Book)",
    description=(
        "Building and engineering works designed by the Employer. The standard "
        "measure-and-value form; the Engineer administers the contract."
    ),
)

FORM_FIDIC_YELLOW_BOOK = ContractForm(
    code="fidic-yellow-book",
    publisher="FIDIC",
    name="Conditions of Contract for Plant and Design-Build (Yellow Book)",
    description="Electrical/mechanical plant and works designed by the Contractor.",
)

FORM_FIDIC_SILVER_BOOK = ContractForm(
    code="fidic-silver-book",
    publisher="FIDIC",
    name="Conditions of Contract for EPC/Turnkey Projects (Silver Book)",
    description="Turnkey projects with greater risk transfer to the Contractor.",
)


def _topics(*mappings: TopicMapping) -> dict[str, TopicMapping]:
    return {m.topic: m for m in mappings}


# --------------------------------------------------------------------------
# Known editions
#
# The 1987 and 2017 topic maps below are derived from the clause skeletons in
# the legacy prototype (`../backend/fidic_1987_skeleton.py`,
# `fidic_2017_skeleton.py`), which were transcribed by hand from each source
# PDF's contents pages. Provenance is recorded in `notes`.
# --------------------------------------------------------------------------

EDITION_RED_BOOK_1987 = ContractEdition(
    code="red-book-1987",
    form_code=FORM_FIDIC_RED_BOOK.code,
    year=1987,
    label="FIDIC Red Book 1987 (4th Edition)",
    topic_map=_topics(
        TopicMapping(TOPIC_ENGINEER, "2", "Engineer's Duties and Authority"),
        TopicMapping(TOPIC_CONTRACTOR_OBLIGATIONS, "8", "Contractor's General Obligations"),
        TopicMapping(TOPIC_SUSPENSION, "40", "Suspension of Work"),
        TopicMapping(TOPIC_EXTENSION_OF_TIME, "44", "Extension of Time for Completion"),
        TopicMapping(TOPIC_VARIATIONS, "52", "Valuation of Variations"),
        TopicMapping(TOPIC_CLAIMS_PROCEDURE, "53", "Procedure for Claims"),
        TopicMapping(TOPIC_PAYMENT, "60", "Certificates and Payment"),
        TopicMapping(TOPIC_FORCE_MAJEURE, "65", "Special Risks"),
        TopicMapping(TOPIC_TERMINATION, "63", "Default of Contractor"),
        TopicMapping(TOPIC_DISPUTES, "67", "Settlement of Disputes"),
    ),
    # Recorded by hand in the legacy skeleton: the contents pages of this
    # reprint jump from 25 to 27. Clause 26 genuinely does not exist, so KB
    # validation must not report it as a missing clause.
    absent_clauses=frozenset({"26"}),
    notes=(
        "4th Edition, reprinted 1988 and 1992. Topic map derived from the "
        "contents pages of the source PDF. Known source quirks: Clause 26 is "
        "absent from the numbering; Clause 21 has no 21.1/21.2 and begins at "
        "21.3; the title of Sub-Clause 54.1 wraps across two physical lines in "
        "the source contents and was truncated in the legacy transcription."
    ),
)

EDITION_RED_BOOK_1999 = ContractEdition(
    code="red-book-1999",
    form_code=FORM_FIDIC_RED_BOOK.code,
    year=1999,
    label="FIDIC Red Book 1999 (1st Edition)",
    topic_map=_topics(
        TopicMapping(TOPIC_ENGINEER, "3", "The Engineer"),
        TopicMapping(TOPIC_CONTRACTOR_OBLIGATIONS, "4", "The Contractor"),
        TopicMapping(TOPIC_EXTENSION_OF_TIME, "8.4", "Extension of Time for Completion"),
        TopicMapping(TOPIC_SUSPENSION, "8.8", "Suspension of Work"),
        TopicMapping(TOPIC_PAYMENT, "14", "Contract Price and Payment"),
        TopicMapping(TOPIC_VARIATIONS, "13", "Variations and Adjustments"),
        TopicMapping(TOPIC_TERMINATION, "15", "Termination by Employer"),
        TopicMapping(TOPIC_CLAIMS_PROCEDURE, "20.1", "Contractor's Claims"),
        TopicMapping(TOPIC_DISPUTES, "20.4", "Obtaining Dispute Adjudication Board's Decision"),
        TopicMapping(TOPIC_FORCE_MAJEURE, "19", "Force Majeure"),
        TopicMapping(TOPIC_PHYSICAL_CONDITIONS, "4.12", "Unforeseeable Physical Conditions"),
    ),
    notes=(
        "Registered so that projects governed by the 1999 edition are "
        "representable and are never silently served 1987 or 2017 text. No "
        "knowledge-base content is bundled for this edition; retrieval against "
        "it fails cleanly until a source document is ingested."
    ),
)

EDITION_RED_BOOK_2017 = ContractEdition(
    code="red-book-2017",
    form_code=FORM_FIDIC_RED_BOOK.code,
    year=2017,
    label="FIDIC Red Book 2017 (2nd Edition)",
    topic_map=_topics(
        TopicMapping(TOPIC_ENGINEER, "3", "The Engineer"),
        TopicMapping(TOPIC_CONTRACTOR_OBLIGATIONS, "4", "The Contractor"),
        TopicMapping(TOPIC_EMPLOYER_OBLIGATIONS, "2", "The Employer"),
        TopicMapping(TOPIC_EXTENSION_OF_TIME, "8.5", "Extension of Time for Completion"),
        TopicMapping(TOPIC_SUSPENSION, "8.9", "Employer's Suspension"),
        TopicMapping(TOPIC_VARIATIONS, "13", "Variations and Adjustments"),
        TopicMapping(TOPIC_PAYMENT, "14", "Contract Price and Payment"),
        TopicMapping(TOPIC_TERMINATION, "15", "Termination by Employer"),
        TopicMapping(TOPIC_FORCE_MAJEURE, "18", "Exceptional Events"),
        TopicMapping(TOPIC_CLAIMS_PROCEDURE, "20", "Employer's and Contractor's Claims"),
        TopicMapping(TOPIC_DISPUTES, "21", "Disputes and Arbitration"),
        TopicMapping(TOPIC_PHYSICAL_CONDITIONS, "4.12", "Unforeseeable Physical Conditions"),
    ),
    notes=(
        "2nd Edition 2017. Topic map derived from the contents pages of the "
        "source PDF. Note the split notice regime at 20.2.1 (Notice of Claim) "
        "and the 28-day time bar, which differ materially from the 1987 form."
    ),
)


class EditionRegistry:
    """Lookup for contract forms and editions.

    Instantiated with an explicit collection so tests and future
    database-backed configuration can supply their own set. The module-level
    :data:`DEFAULT_REGISTRY` holds the forms shipped with the product.
    """

    def __init__(
        self,
        forms: Iterable[ContractForm],
        editions: Iterable[ContractEdition],
    ) -> None:
        self._forms: dict[str, ContractForm] = {f.code: f for f in forms}
        self._editions: dict[str, ContractEdition] = {}
        for edition in editions:
            if edition.form_code not in self._forms:
                raise ValidationError(
                    f"Edition {edition.code!r} references unknown form "
                    f"{edition.form_code!r}",
                    details={"edition": edition.code, "form": edition.form_code},
                )
            self._editions[edition.code] = edition

    def get_edition(self, code: str) -> ContractEdition:
        """Return the edition for ``code``.

        Raises:
            NotFoundError: if the code is unknown. Deliberately not falling back
                to a default — see ADR 0004.
        """
        try:
            return self._editions[code]
        except KeyError:
            raise NotFoundError(
                f"Unknown contract edition: {code!r}",
                details={"edition": code, "known": sorted(self._editions)},
            ) from None

    def get_form(self, code: str) -> ContractForm:
        try:
            return self._forms[code]
        except KeyError:
            raise NotFoundError(
                f"Unknown contract form: {code!r}",
                details={"form": code, "known": sorted(self._forms)},
            ) from None

    def editions(self) -> tuple[ContractEdition, ...]:
        return tuple(sorted(self._editions.values(), key=lambda e: (e.form_code, e.year)))

    def forms(self) -> tuple[ContractForm, ...]:
        return tuple(sorted(self._forms.values(), key=lambda f: f.code))

    def editions_for_form(self, form_code: str) -> tuple[ContractEdition, ...]:
        self.get_form(form_code)  # validates the form exists
        return tuple(
            sorted(
                (e for e in self._editions.values() if e.form_code == form_code),
                key=lambda e: e.year,
            )
        )

    def has_edition(self, code: str) -> bool:
        return code in self._editions


DEFAULT_REGISTRY = EditionRegistry(
    forms=[FORM_FIDIC_RED_BOOK, FORM_FIDIC_YELLOW_BOOK, FORM_FIDIC_SILVER_BOOK],
    editions=[EDITION_RED_BOOK_1987, EDITION_RED_BOOK_1999, EDITION_RED_BOOK_2017],
)
