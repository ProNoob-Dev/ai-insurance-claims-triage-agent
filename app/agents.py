"""Multi-agent triage workflow, built on LangGraph.

Four agents run as nodes in a state graph:

1. **Ingestion agent** — classifies the claim and extracts structured
   fields (policy number, incident date, amounts) from the FNOL form and
   documents. All free text is PII-masked before the LLM call.
2. **Retrieval agent** — checks the claim against the policy wording in
   the vector store (Qdrant, or the in-memory fallback) and returns the
   top clauses with citations to the exact clause.
3. **Scoring agent** — flags anomalies against historical claim patterns:
   duplicate filings, amounts far above the norm for the claim type,
   incident dates before the policy started or in the future, missing
   documents, and amount mismatches between documents.
4. **Supervisor agent** — makes the routing decision. Clear low-value
   claims are auto-approved; everything else goes to an adjuster with a
   full evidence pack. A denial is NEVER final here — it is only ever a
   *proposal* that a human must confirm in the review queue (HITL).

Every agent step is recorded in the claim's trace, and every decision is
written to the audit log with its citations.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.config import settings
from app.llm import LLMProvider
from app.models import AgentStep, Citation, Claim, ProposedDecision
from app.pii import mask_claim_payload, mask_text
from app.store import ClaimStore
from app.vector_store import InMemoryVectorStore

logger = logging.getLogger(__name__)


class TriageState(TypedDict, total=False):
    claim: dict[str, Any]
    claim_obj: Claim
    masked_payload: dict[str, Any]
    claim_type: str
    claim_type_confidence: float
    extracted_fields: dict[str, Any]
    citations: list[Citation]
    covered: bool | None
    coverage_clause_id: str | None
    coverage_reasoning: str
    anomaly_score: float
    anomaly_flags: list[str]
    decision: str
    requires_hitl: bool
    trace: list[AgentStep]


# Typical claim amounts per type (INR) — used by the scoring agent to
# spot amounts far outside the norm for the claim type.
_TYPICAL_MAX: dict[str, float] = {
    "motor": 150_000,
    "health": 300_000,
    "property": 500_000,
    "travel": 80_000,
}


class TriageWorkflow:
    def __init__(
        self,
        llm: LLMProvider,
        vector_store: InMemoryVectorStore,
        store: ClaimStore,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store
        self._store = store
        self._graph = self._build_graph()

    # -- graph --------------------------------------------------------------
    def _build_graph(self) -> StateGraph:
        graph: StateGraph = StateGraph(TriageState)
        graph.add_node("ingest", self._ingest)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("score", self._score)
        graph.add_node("supervise", self._supervise)
        graph.set_entry_point("ingest")
        graph.add_edge("ingest", "retrieve")
        graph.add_edge("retrieve", "score")
        graph.add_edge("score", "supervise")
        graph.add_edge("supervise", END)
        return graph.compile()

    def run(self, claim: Claim) -> Claim:
        started = time.monotonic()
        initial: TriageState = {
            "claim": claim.model_dump(),
            "claim_obj": claim,
            "trace": [],
        }
        result = self._graph.invoke(initial)
        claim = result["claim_obj"]
        claim.triage_duration_ms = int((time.monotonic() - started) * 1000)
        return claim

    # -- nodes --------------------------------------------------------------
    def _step(self, state: TriageState, agent: str, action: str, detail: str) -> None:
        step = AgentStep(agent=agent, action=action, detail=detail)
        state.setdefault("trace", []).append(step)
        state["claim_obj"].trace.append(step)

    def _ingest(self, state: TriageState) -> TriageState:
        claim: Claim = state["claim_obj"]
        payload = claim.model_dump()
        _masked, detected = mask_text(
            claim.incident_description, extra_names=[claim.claimant_name]
        )
        claim.pii_detected = detected
        safe_payload = mask_claim_payload(payload)
        state["masked_payload"] = safe_payload

        # 1a. Classify.
        classify_prompt = (
            "TASK: classify\n"
            "Classify this insurance claim into one of: motor, health, "
            "property, travel. Reply with JSON only.\n"
            f"CLAIM_JSON: {json.dumps(safe_payload, ensure_ascii=False)}"
        )
        resp = self._llm.complete(
            system="You are an insurance triage classifier. Reply with JSON only.",
            prompt=classify_prompt,
            json_mode=True,
        )
        try:
            classified = json.loads(resp.content)
        except json.JSONDecodeError:
            classified = {"claim_type": "unknown", "confidence": 0.0}
        claim_type = str(classified.get("claim_type", "unknown")).lower()
        if claim_type not in ("motor", "health", "property", "travel"):
            claim_type = "unknown"
        confidence = float(classified.get("confidence", 0.0) or 0.0)
        state["claim_type"] = claim_type
        state["claim_type_confidence"] = confidence
        claim.claim_type = claim_type
        claim.claim_type_confidence = confidence
        self._step(
            state, "ingestion",
            "classify",
            f"Claim classified as {claim_type} "
            f"(confidence {confidence:.2f}). PII masked: {detected or 'none'}.",
        )

        # 1b. Extract structured fields.
        extract_prompt = (
            "TASK: extract\n"
            "Extract structured fields from this claim. Reply with JSON only.\n"
            f"CLAIM_JSON: {json.dumps(safe_payload, ensure_ascii=False)}"
        )
        resp = self._llm.complete(
            system="You are an insurance document extractor. Reply with JSON only.",
            prompt=extract_prompt,
            json_mode=True,
        )
        try:
            extracted = json.loads(resp.content)
        except json.JSONDecodeError:
            extracted = {}
        # Never let extraction overwrite a caller-supplied amount/date.
        if claim.claimed_amount is not None:
            extracted["claimed_amount_inr"] = claim.claimed_amount
        if claim.incident_date:
            extracted["incident_date"] = claim.incident_date
        state["extracted_fields"] = extracted
        claim.extracted_fields = extracted
        if extracted.get("incident_date"):
            claim.incident_date = str(extracted["incident_date"])
        if extracted.get("claimed_amount_inr") is not None:
            claim.claimed_amount = float(extracted["claimed_amount_inr"])
        self._step(
            state, "ingestion",
            "extract",
            f"Extracted policy {extracted.get('policy_number')}, "
            f"incident {extracted.get('incident_date')}, "
            f"amount Rs. {extracted.get('claimed_amount_inr')}, "
            f"{extracted.get('documents_seen', 0)} document(s) seen.",
        )
        return state

    def _retrieve(self, state: TriageState) -> TriageState:
        claim: Claim = state["claim_obj"]
        query = (
            f"{state.get('claim_type', '')} "
            f"{claim.incident_description} "
            + " ".join(
                doc.get("content", "")
                for doc in claim.model_dump().get("documents", []) or []
            )
        )
        hits = self._vector_store.search(query, top_k=3)
        citations = [
            Citation(
                clause_id=hit.clause["id"],
                clause_title=hit.clause.get("title", ""),
                score=hit.score,
                excerpt=hit.clause["text"][:280],
            )
            for hit in hits
        ]
        state["citations"] = citations
        claim.citations = citations

        # Coverage assessment via the LLM, grounded in the retrieved clauses.
        safe_payload = state.get("masked_payload") or mask_claim_payload(
            claim.model_dump()
        )
        coverage_prompt = (
            "TASK: coverage\n"
            "Decide whether this claim is covered by the retrieved policy "
            "clauses. Reply with JSON only.\n"
            f"CLAIM_JSON: {json.dumps(safe_payload, ensure_ascii=False)}\n"
            "CLAUSES_JSON: "
            + json.dumps([h.clause for h in hits], ensure_ascii=False)
        )
        resp = self._llm.complete(
            system=(
                "You are an insurance coverage assessor. Decide coverage "
                "only from the retrieved clauses. Reply with JSON only."
            ),
            prompt=coverage_prompt,
            json_mode=True,
        )
        try:
            coverage = json.loads(resp.content)
        except json.JSONDecodeError:
            coverage = {"covered": False, "reasoning": "Coverage check failed."}
        state["covered"] = bool(coverage.get("covered", False))
        state["coverage_clause_id"] = coverage.get("clause_id")
        state["coverage_reasoning"] = str(coverage.get("reasoning", ""))
        state["coverage_limit_inr"] = coverage.get("coverage_limit_inr")
        claim.covered = state["covered"]
        claim.coverage_clause_id = state["coverage_clause_id"]
        claim.coverage_reasoning = state["coverage_reasoning"]
        self._step(
            state, "retrieval",
            "coverage-check",
            f"{len(citations)} clause(s) retrieved; top: "
            f"{citations[0].clause_id if citations else 'none'}. "
            f"Covered: {claim.covered}. {claim.coverage_reasoning}",
        )
        return state

    def _score(self, state: TriageState) -> TriageState:
        claim: Claim = state["claim_obj"]
        score = 0.0
        flags: list[str] = []
        payload = claim.model_dump()
        amount = claim.claimed_amount or 0.0
        ctype = state.get("claim_type", "unknown")

        # Duplicate filing: same policy + same type already in the store.
        if claim.policy_number:
            dups = self._store.find_duplicates(
                policy_number=claim.policy_number,
                claim_type=ctype,
                exclude_id=claim.id,
            )
            if dups:
                score += 0.45
                flags.append(
                    f"possible-duplicate: {len(dups)} earlier {ctype} claim(s) "
                    f"on policy {claim.policy_number}"
                )

        # Amount far above the norm for this claim type.
        typical = _TYPICAL_MAX.get(ctype)
        if typical and amount > 2 * typical:
            score += 0.35
            flags.append(
                f"amount-outlier: Rs. {amount:,.0f} vs typical max "
                f"Rs. {typical:,.0f} for {ctype}"
            )

        # Amount above the covering clause limit.
        limit = state.get("coverage_limit_inr")
        if claim.covered and limit and amount > float(limit):
            score += 0.35
            flags.append(
                f"amount-exceeds-coverage-limit: Rs. {amount:,.0f} vs "
                f"clause limit Rs. {float(limit):,.0f}"
            )

        # Incident before the policy started.
        if claim.incident_date and claim.model_dump().get("policy_start_date"):
            try:
                incident = date.fromisoformat(str(claim.incident_date))
                start = date.fromisoformat(
                    str(claim.model_dump()["policy_start_date"])
                )
                if incident < start:
                    score += 0.5
                    flags.append("incident-before-policy-start")
            except ValueError:
                pass

        # Incident in the future.
        if claim.incident_date:
            try:
                if date.fromisoformat(str(claim.incident_date)) > date.today():
                    score += 0.5
                    flags.append("incident-date-in-future")
            except ValueError:
                pass

        # Missing documents.
        if not payload.get("documents"):
            score += 0.2
            flags.append("no-supporting-documents")

        # Amount mismatch between the claimed amount and document figures.
        doc_amounts: list[float] = []
        for doc in payload.get("documents", []) or []:
            for match in re.findall(
                r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)",
                str(doc.get("content", "")),
            ):
                try:
                    doc_amounts.append(float(match.replace(",", "")))
                except ValueError:
                    pass
        if (
            doc_amounts
            and amount
            and max(doc_amounts) > 0
            and abs(max(doc_amounts) - amount) / max(amount, 1) > 0.5
        ):
            score += 0.3
            flags.append(
                f"amount-mismatch: claimed Rs. {amount:,.0f} vs document "
                f"Rs. {max(doc_amounts):,.0f}"
            )

        state["anomaly_score"] = round(min(score, 1.0), 2)
        state["anomaly_flags"] = flags
        claim.anomaly_score = state["anomaly_score"]
        claim.anomaly_flags = flags
        self._step(
            state, "scoring",
            "anomaly-check",
            f"Anomaly score {claim.anomaly_score:.2f}. "
            f"Flags: {flags or ['none']}.",
        )
        return state

    def _supervise(self, state: TriageState) -> TriageState:
        claim: Claim = state["claim_obj"]
        amount = claim.claimed_amount or 0.0
        anomaly = state.get("anomaly_score", 0.0)

        if not state.get("covered", False):
            decision = ProposedDecision.PROPOSE_DENY
            requires_hitl = True
            reason = (
                "Not covered by policy wording — denial proposed for "
                "human review. A denial can never be final without a "
                "reviewer confirming it."
            )
        elif anomaly >= settings.anomaly_route_threshold:
            decision = ProposedDecision.ROUTE_TO_ADJUSTER
            requires_hitl = True
            reason = (
                f"Anomaly score {anomaly:.2f} at/above threshold "
                f"{settings.anomaly_route_threshold} — routed to an adjuster "
                "with the evidence pack."
            )
        elif (
            amount <= settings.auto_approve_limit_inr
            and anomaly < settings.anomaly_low_threshold
        ):
            decision = ProposedDecision.AUTO_APPROVE
            requires_hitl = False
            reason = (
                f"Clear low-value claim (Rs. {amount:,.0f} within the "
                f"Rs. {settings.auto_approve_limit_inr:,.0f} auto-approve "
                "limit), covered, low anomaly — auto-approved."
            )
        else:
            decision = ProposedDecision.ROUTE_TO_ADJUSTER
            requires_hitl = True
            reason = (
                f"Claim of Rs. {amount:,.0f} exceeds the auto-approve limit "
                f"or needs a human look — routed to an adjuster with the "
                "evidence pack."
            )

        claim.decision = decision
        claim.proposed_decision = decision
        claim.requires_hitl = requires_hitl
        state["decision"] = decision.value
        state["requires_hitl"] = requires_hitl
        claim.status = (
            "auto_approved" if decision == ProposedDecision.AUTO_APPROVE
            else "pending_review"
        )
        self._step(state, "supervisor", "route", reason)
        self._store.audit(
            claim,
            actor="system:supervisor",
            action="triage-decision",
            detail=(
                f"Decision: {decision.value}; requires_hitl={requires_hitl}; "
                f"covered={claim.covered} "
                f"(clause {claim.coverage_clause_id}); "
                f"anomaly={anomaly:.2f}; citations="
                + ",".join(c.clause_id for c in claim.citations)
            ),
        )
        return state
