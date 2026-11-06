"""Triage pipeline orchestration + human review.

The pipeline is the only entry point for creating and reviewing claims.
It wires the LLM provider, the vector store and the LangGraph workflow
together, and enforces the one rule that matters most:

**No claim is ever finally denied without a human reviewer.**
The supervisor may only *propose* a denial; the final deny happens in
:func:`review_claim`, by a named reviewer, and is written to the audit log.
"""

from __future__ import annotations

import logging

from app.agents import TriageWorkflow
from app.config import settings
from app.llm import get_llm_provider
from app.models import Claim, ClaimCreate, ClaimStatus
from app.store import ClaimStore
from app.vector_store import build_vector_store

logger = logging.getLogger(__name__)


class TriagePipeline:
    def __init__(self, store: ClaimStore | None = None) -> None:
        self.store = store or ClaimStore(
            settings.claims_db_file, settings.audit_log_file
        )
        self.llm = get_llm_provider()
        self.vector_store = build_vector_store()
        self.workflow = TriageWorkflow(self.llm, self.vector_store, self.store)

    # -- submit -------------------------------------------------------------
    def submit_claim(self, data: ClaimCreate) -> Claim:
        claim = Claim(
            id=self.store.new_id(),
            claimant_name=data.claimant_name,
            policy_number=data.policy_number,
            policy_start_date=data.policy_start_date,
            claim_type_hint=data.claim_type_hint,
            flags=list(data.flags),
            documents=[d.model_dump() for d in data.documents],
            incident_description=data.incident_description,
            claimed_amount=data.claimed_amount,
            incident_date=data.incident_date,
        )
        # The workflow only ever sees the PII-masked version of this
        # payload (see agents._ingest); the raw submission lives in the
        # local claim store only and is never logged.
        self.store.create(claim)
        self.store.audit(
            claim, actor="system", action="claim-submitted",
            detail=(
                f"Claim {claim.id} submitted for policy "
                f"{data.policy_number}; "
                f"{len(data.documents)} document(s) attached."
            ),
        )
        triaged = self.workflow.run(claim)
        self.store.update(triaged)
        return triaged

    # -- review (HITL) --------------------------------------------------------
    def review_claim(
        self, claim_id: str, *, decision: str, reviewer: str, note: str = ""
    ) -> Claim | None:
        """Apply a human reviewer's decision.

        Only ``pending_review`` claims can be reviewed. A denial here is
        the ONLY way a claim reaches ``denied`` status — the agents can
        never deny on their own.
        """
        claim = self.store.get(claim_id)
        if claim is None:
            return None
        if claim.status != ClaimStatus.PENDING_REVIEW.value:
            raise ValueError(
                f"Claim {claim_id} is {claim.status} and cannot be reviewed "
                "(only pending_review claims can be reviewed)."
            )
        mapping = {
            "approve": ClaimStatus.APPROVED.value,
            "deny": ClaimStatus.DENIED.value,
            "request_info": ClaimStatus.INFO_REQUESTED.value,
        }
        claim.status = mapping[decision]
        claim.reviewer = reviewer
        claim.review_note = note
        self.store.audit(
            claim,
            actor=f"reviewer:{reviewer}",
            action=f"review-{decision}",
            detail=(
                f"Human review: {decision}. "
                f"Proposed decision was {claim.proposed_decision}. "
                f"Note: {note or '-'}"
            ),
        )
        self.store.update(claim)
        return claim

    # -- reads ------------------------------------------------------------------
    def get_claim(self, claim_id: str) -> Claim | None:
        return self.store.get(claim_id)

    def list_claims(
        self,
        *,
        status: str | None = None,
        claim_type: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
    ) -> list[Claim]:
        claims = self.store.list_all()
        if status:
            claims = [c for c in claims if c.status == status]
        if claim_type:
            claims = [c for c in claims if c.claim_type == claim_type]
        if min_amount is not None:
            claims = [
                c for c in claims
                if c.claimed_amount is not None and c.claimed_amount >= min_amount
            ]
        if max_amount is not None:
            claims = [
                c for c in claims
                if c.claimed_amount is not None and c.claimed_amount <= max_amount
            ]
        return claims

    def metrics(self) -> dict:
        claims = self.store.list_all()
        total = len(claims)
        auto = sum(1 for c in claims if c.status == "auto_approved")
        pending = sum(1 for c in claims if c.status == "pending_review")
        approved = sum(1 for c in claims if c.status == "approved")
        denied = sum(1 for c in claims if c.status == "denied")
        info = sum(1 for c in claims if c.status == "info_requested")
        autonomous = (auto + approved + denied) / total if total else 0.0
        avg_ms = (
            sum(c.triage_duration_ms for c in claims) / total if total else 0.0
        )
        anomalous = sum(1 for c in claims if c.anomaly_score >= 0.6)
        return {
            "total_claims": total,
            "auto_approved": auto,
            "pending_review": pending,
            "approved_by_reviewer": approved,
            "denied_by_reviewer": denied,
            "info_requested": info,
            "autonomous_rate": round(autonomous, 3),
            "avg_triage_ms": round(avg_ms, 1),
            "anomaly_rate": round(anomalous / total, 3) if total else 0.0,
        }
