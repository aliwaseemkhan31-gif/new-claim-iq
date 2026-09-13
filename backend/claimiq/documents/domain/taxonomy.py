"""Document type taxonomy.

The prototype recognised exactly two kinds of document: "contract" and "claim".
Real projects run on dozens — particular conditions, engineer's instructions,
daily reports, programmes, RFIs, determinations — and the distinctions matter to
retrieval. "Which correspondence contradicts this claim?" is only answerable if
correspondence is a distinguishable category.

The taxonomy is a two-level tree (category → type) and is extensible: a
deployment can register bespoke types without changing code, and the registry
validates additions against the same rules the built-ins satisfy.

Runs on Python 3.9+. See ADR 0001.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from claimiq.core.domain.errors import NotFoundError, ValidationError


class DocumentCategory:
    CONTRACT = "contract"
    CLAIM = "claim"
    CORRESPONDENCE = "correspondence"
    EVIDENCE = "evidence"
    KNOWLEDGE_BASE = "knowledge_base"
    OTHER = "other"


ALL_CATEGORIES = (
    DocumentCategory.CONTRACT,
    DocumentCategory.CLAIM,
    DocumentCategory.CORRESPONDENCE,
    DocumentCategory.EVIDENCE,
    DocumentCategory.KNOWLEDGE_BASE,
    DocumentCategory.OTHER,
)


@dataclass(frozen=True)
class DocumentType:
    """One node in the taxonomy.

    Attributes:
        code: Stable identifier used in scopes, filters and the API.
        label: Display name.
        category: Parent category.
        description: What belongs in this type.
        expects_clauses: True when the type normally contains numbered clauses,
            so clause detection is worth running. A photograph does not.
        expects_correspondence_metadata: True when sender/recipient/date
            extraction applies.
        is_contractual: True when the document can itself create obligations.
            Used to weight retrieval: a determination carries more contractual
            force than a progress photograph.
        sort_order: Display ordering within the category.
    """

    code: str
    label: str
    category: str
    description: str = ""
    expects_clauses: bool = False
    expects_correspondence_metadata: bool = False
    is_contractual: bool = False
    sort_order: int = 100

    def __post_init__(self) -> None:
        if not self.code:
            raise ValidationError("DocumentType.code must not be empty")
        if self.category not in ALL_CATEGORIES:
            raise ValidationError(
                f"Unknown category {self.category!r} for document type {self.code!r}",
                details={"valid": list(ALL_CATEGORIES)},
            )


def _t(
    code: str,
    label: str,
    category: str,
    description: str = "",
    *,
    clauses: bool = False,
    correspondence: bool = False,
    contractual: bool = False,
    order: int = 100,
) -> DocumentType:
    return DocumentType(
        code=code,
        label=label,
        category=category,
        description=description,
        expects_clauses=clauses,
        expects_correspondence_metadata=correspondence,
        is_contractual=contractual,
        sort_order=order,
    )


C = DocumentCategory

BUILTIN_TYPES: tuple[DocumentType, ...] = (
    # -- Contract documents -------------------------------------------------
    _t("contract_agreement", "Contract Agreement", C.CONTRACT,
       "The executed agreement between the parties.",
       clauses=True, contractual=True, order=10),
    _t("conditions_of_contract", "Conditions of Contract", C.CONTRACT,
       "General conditions, typically a standard form.",
       clauses=True, contractual=True, order=20),
    _t("particular_conditions", "Particular Conditions", C.CONTRACT,
       "Project-specific amendments to the general conditions. Takes precedence "
       "over the standard form where they conflict.",
       clauses=True, contractual=True, order=30),
    _t("general_conditions", "General Conditions", C.CONTRACT,
       "Unamended standard-form conditions.", clauses=True, contractual=True, order=40),
    _t("specification", "Specification", C.CONTRACT,
       "Technical specification.", clauses=True, contractual=True, order=50),
    _t("employer_requirements", "Employer's Requirements", C.CONTRACT,
       "Employer-stated requirements for the works.",
       clauses=True, contractual=True, order=60),
    _t("drawings", "Drawings", C.CONTRACT, "Contract drawings.", contractual=True, order=70),
    _t("bill_of_quantities", "Bill of Quantities", C.CONTRACT,
       "Measured quantities and rates.", contractual=True, order=80),
    _t("schedule", "Schedule", C.CONTRACT,
       "Contract schedules and appendices.", contractual=True, order=90),
    _t("addendum", "Addendum", C.CONTRACT,
       "Pre-award addendum to the tender documents.",
       clauses=True, contractual=True, order=100),
    _t("amendment", "Amendment", C.CONTRACT,
       "Post-award amendment or variation to the contract.",
       clauses=True, contractual=True, order=110),

    # -- Claims -------------------------------------------------------------
    _t("claim", "Claim", C.CLAIM, "A claim submission.", clauses=True, order=10),
    _t("eot_claim", "Extension of Time Claim", C.CLAIM,
       "Claim for an extension to the Time for Completion.", clauses=True, order=20),
    _t("variation_claim", "Variation Claim", C.CLAIM,
       "Claim arising from a variation.", clauses=True, order=30),
    _t("cost_claim", "Cost Claim", C.CLAIM, "Claim for additional cost.", clauses=True, order=40),
    _t("delay_claim", "Delay Claim", C.CLAIM, "Claim arising from delay.", clauses=True, order=50),
    _t("disruption_claim", "Disruption Claim", C.CLAIM,
       "Claim for loss of productivity.", clauses=True, order=60),
    _t("acceleration_claim", "Acceleration Claim", C.CLAIM,
       "Claim arising from acceleration.", clauses=True, order=70),
    _t("payment_claim", "Payment Claim", C.CLAIM,
       "Claim relating to payment or certification.", clauses=True, order=80),
    _t("compensation_event", "Compensation Event", C.CLAIM,
       "A compensation event notification.", clauses=True, order=90),

    # -- Correspondence -----------------------------------------------------
    _t("letter", "Letter", C.CORRESPONDENCE, "Formal project letter.",
       correspondence=True, order=10),
    _t("email", "Email", C.CORRESPONDENCE, "Project email.", correspondence=True, order=20),
    _t("notice", "Notice", C.CORRESPONDENCE,
       "A formal notice given under the contract. Contractual: notices create "
       "and preserve entitlement, and their timing is frequently decisive.",
       correspondence=True, contractual=True, order=30),
    _t("engineers_instruction", "Engineer's Instruction", C.CORRESPONDENCE,
       "An instruction issued by the Engineer.",
       correspondence=True, contractual=True, order=40),
    _t("determination", "Determination", C.CORRESPONDENCE,
       "An Engineer's determination under the contract.",
       correspondence=True, contractual=True, order=50),
    _t("response", "Response", C.CORRESPONDENCE,
       "A reply to earlier correspondence.", correspondence=True, order=60),
    _t("meeting_minutes", "Meeting Minutes", C.CORRESPONDENCE,
       "Minutes of a project meeting.", correspondence=True, order=70),
    _t("rfi", "Request for Information", C.CORRESPONDENCE,
       "An RFI and its response.", correspondence=True, order=80),
    _t("site_instruction", "Site Instruction", C.CORRESPONDENCE,
       "An instruction issued on site.", correspondence=True, contractual=True, order=90),

    # -- Evidence -----------------------------------------------------------
    _t("daily_report", "Daily Report", C.EVIDENCE, "Daily site record.", order=10),
    _t("progress_report", "Progress Report", C.EVIDENCE, "Periodic progress report.", order=20),
    _t("photograph", "Photograph", C.EVIDENCE, "Site photograph.", order=30),
    _t("programme", "Programme", C.EVIDENCE, "Baseline programme.", order=40),
    _t("updated_programme", "Updated Programme", C.EVIDENCE, "Revised programme.", order=50),
    _t("payment_record", "Payment Record", C.EVIDENCE,
       "Payment certificate or application.", order=60),
    _t("measurement_sheet", "Measurement Sheet", C.EVIDENCE, "Measurement record.", order=70),
    _t("site_record", "Site Record", C.EVIDENCE, "Other site record.", order=80),
    _t("timesheet", "Timesheet", C.EVIDENCE, "Labour or plant timesheet.", order=90),
    _t("invoice", "Invoice", C.EVIDENCE, "Supplier or subcontractor invoice.", order=100),

    # -- Knowledge base -----------------------------------------------------
    _t("standard_form", "Standard Form Conditions", C.KNOWLEDGE_BASE,
       "A published standard form, e.g. the FIDIC Red Book. Edition-scoped.",
       clauses=True, contractual=True, order=10),

    # -- Other --------------------------------------------------------------
    _t("other", "Other", C.OTHER, "Unclassified.", order=999),
)


class TaxonomyRegistry:
    """Lookup and extension point for document types."""

    def __init__(self, types: Iterable[DocumentType]) -> None:
        self._types: dict[str, DocumentType] = {}
        for document_type in types:
            self.register(document_type)

    def register(self, document_type: DocumentType) -> None:
        if document_type.code in self._types:
            raise ValidationError(
                f"Duplicate document type code: {document_type.code!r}"
            )
        self._types[document_type.code] = document_type

    def get(self, code: str) -> DocumentType:
        try:
            return self._types[code]
        except KeyError:
            raise NotFoundError(
                f"Unknown document type: {code!r}",
                details={"code": code},
            ) from None

    def has(self, code: str) -> bool:
        return code in self._types

    def all(self) -> tuple[DocumentType, ...]:
        return tuple(
            sorted(self._types.values(), key=lambda t: (t.category, t.sort_order, t.code))
        )

    def by_category(self, category: str) -> tuple[DocumentType, ...]:
        if category not in ALL_CATEGORIES:
            raise NotFoundError(f"Unknown category: {category!r}")
        return tuple(t for t in self.all() if t.category == category)

    def codes_expecting_clauses(self) -> frozenset[str]:
        return frozenset(t.code for t in self._types.values() if t.expects_clauses)

    def contractual_codes(self) -> frozenset[str]:
        return frozenset(t.code for t in self._types.values() if t.is_contractual)

    def correspondence_codes(self) -> frozenset[str]:
        return frozenset(
            t.code for t in self._types.values() if t.expects_correspondence_metadata
        )


DEFAULT_TAXONOMY = TaxonomyRegistry(BUILTIN_TYPES)
