"""Pydantic models for requests, responses and stored claims."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    MOTOR = "motor"
    HEALTH = "health"
    PROPERTY = "property"
    TRAVEL = "travel"
    UNKNOWN = "unknown"


class ClaimStatus(str, Enum):
    SUBMITTED = "submitted"
    TRIAGED = "triaged"  # triage finished; see decision fields
    AUTO_APPROVED = "auto_approved"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    DENIED = "denied"
    INFO_REQUESTED = "info_requested"


class ProposedDecision(str, Enum):
    AUTO_APPROVE = "auto_approve"
    ROUTE_TO_ADJUSTER = "route_to_adjuster"
    PROPOSE_DENY = "propose_deny"


class DocumentInput(BaseModel):
    """One supporting document.

    ``content`` is the extracted text of the document. In production this
    comes from Azure Document Intelligence (FNOL forms, bills, photos);
    the API accepts pre-extracted text so the service is demoable offline.
    """

    name: str = Field(..., examples=["fnol_form.pdf"])
    content: str = Field(default="", description="Extracted text of the document")
    content_type: str = Field(default="text/plain")


class ClaimCreate(BaseModel):
    claimant_name: str
    claimant_email: str | None = None
    claimant_phone: str | None = None
    policy_number: str
    policy_start_date: str | None = None  # YYYY-MM-DD
    incident_date: str | None = None  # YYYY-MM-DD
    incident_description: str
    claimed_amount: float | None = None
    claim_type_hint: str | None = None
    documents: list[DocumentInput] = Field(default_factory=list)
    flags: list[str] = Field(
        default_factory=list,
        description="Machine-readable incident flags, e.g. no_valid_licence",
    )


class ReviewRequest(BaseModel):
    """Human-in-the-loop review. Only valid on a pending_review claim."""

    decision: str = Field(..., pattern="^(approve|deny|request_info)$")
    reviewer: str
    note: str = ""


class Citation(BaseModel):
    clause_id: str
    clause_title: str
    score: float
    excerpt: str


class AgentStep(BaseModel):
    agent: str
    action: str
    detail: str
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AuditEntry(BaseModel):
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    actor: str  # "system" (an agent) or a reviewer name
    action: str
    detail: str


class Claim(BaseModel):
    id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    claimant_name: str
    policy_number: str
    policy_start_date: str | None = None
    claim_type_hint: str | None = None
    flags: list[str] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    claim_type: str = ClaimType.UNKNOWN.value
    claim_type_confidence: float = 0.0
    incident_date: str | None = None
    incident_description: str
    claimed_amount: float | None = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    covered: bool | None = None
    coverage_clause_id: str | None = None
    coverage_reasoning: str = ""
    anomaly_score: float = 0.0
    anomaly_flags: list[str] = Field(default_factory=list)
    decision: ProposedDecision | None = None
    proposed_decision: ProposedDecision | None = None
    requires_hitl: bool = False
    status: str = ClaimStatus.SUBMITTED.value
    reviewer: str | None = None
    review_note: str | None = None
    triage_duration_ms: int = 0
    trace: list[AgentStep] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)
    pii_detected: list[str] = Field(default_factory=list)


class ClaimSummary(BaseModel):
    """List-view shape — no free text, no PII."""

    id: str
    claim_type: str
    status: str
    decision: str | None
    claimed_amount: float | None
    anomaly_score: float
    requires_hitl: bool
    created_at: str


class MetricsResponse(BaseModel):
    total_claims: int
    auto_approved: int
    pending_review: int
    approved_by_reviewer: int
    denied_by_reviewer: int
    info_requested: int
    autonomous_rate: float
    avg_triage_ms: float
    anomaly_rate: float
