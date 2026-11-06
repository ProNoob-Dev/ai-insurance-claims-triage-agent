"""Pipeline unit tests — routing, HITL enforcement, audit trail."""

from app.models import ClaimCreate


def _low_value_motor_claim(**overrides):
    data = {
        "claimant_name": "Test User",
        "policy_number": "MOT-P-10001",
        "policy_start_date": "2025-01-01",
        "incident_date": "2026-03-10",
        "incident_description": "Car bumper dented in a minor parking collision.",
        "claimed_amount": 8500,
        "documents": [
            {
                "name": "repair_estimate.pdf",
                "content": "Repair estimate: bumper replacement Rs. 8,500",
            }
        ],
    }
    data.update(overrides)
    return ClaimCreate(**data)


def test_low_value_covered_claim_auto_approves(pipeline):
    claim = pipeline.submit_claim(_low_value_motor_claim())
    assert claim.claim_type == "motor"
    assert claim.covered is True
    assert claim.decision.value == "auto_approve"
    assert claim.status == "auto_approved"
    assert claim.requires_hitl is False
    assert len(claim.citations) >= 1
    assert claim.coverage_clause_id is not None


def test_high_value_claim_routes_to_adjuster(pipeline):
    claim = pipeline.submit_claim(
        _low_value_motor_claim(
            incident_description="Car collision at an intersection, front end damaged.",
            claimed_amount=85000,
            documents=[
                {
                    "name": "repair_estimate.pdf",
                    "content": "Repair estimate Rs. 85,000",
                }
            ],
        )
    )
    assert claim.decision.value == "route_to_adjuster"
    assert claim.status == "pending_review"
    assert claim.requires_hitl is True


def test_excluded_claim_only_proposes_denial(pipeline):
    claim = pipeline.submit_claim(
        _low_value_motor_claim(
            incident_description="Car engine shows wear and tear after 4 years.",
            claimed_amount=15000,
            flags=["wear_and_tear"],
        )
    )
    assert claim.covered is False
    assert claim.decision.value == "propose_deny"
    # The supervisor NEVER finalises a denial — it stays pending review.
    assert claim.status == "pending_review"
    assert claim.requires_hitl is True


def test_reviewer_can_confirm_denial(pipeline):
    claim = pipeline.submit_claim(
        _low_value_motor_claim(
            incident_description="Car engine shows wear and tear after 4 years.",
            claimed_amount=15000,
            flags=["wear_and_tear"],
        )
    )
    reviewed = pipeline.review_claim(
        claim.id, decision="deny", reviewer="adjuster-1",
        note="Wear and tear is excluded under MOTOR-001.",
    )
    assert reviewed.status == "denied"
    assert reviewed.reviewer == "adjuster-1"


def test_reviewer_can_approve_pending_claim(pipeline):
    claim = pipeline.submit_claim(
        _low_value_motor_claim(claimed_amount=85000)
    )
    reviewed = pipeline.review_claim(
        claim.id, decision="approve", reviewer="adjuster-1", note="Verified."
    )
    assert reviewed.status == "approved"


def test_cannot_review_auto_approved_claim(pipeline):
    import pytest

    claim = pipeline.submit_claim(_low_value_motor_claim())
    assert claim.status == "auto_approved"
    with pytest.raises(ValueError):
        pipeline.review_claim(
            claim.id, decision="approve", reviewer="adjuster-1"
        )


def test_audit_log_records_every_decision(pipeline):
    claim = pipeline.submit_claim(_low_value_motor_claim())
    entries = pipeline.store.audit_entries(claim.id)
    actions = [e["action"] for e in entries]
    assert "claim-submitted" in actions
    assert "triage-decision" in actions
    # The triage decision entry names the citations behind it.
    decision_entry = next(e for e in entries if e["action"] == "triage-decision")
    assert "MOTOR-001" in decision_entry["detail"]


def test_duplicate_filing_is_flagged(pipeline):
    first = pipeline.submit_claim(_low_value_motor_claim())
    assert first.status == "auto_approved"
    second = pipeline.submit_claim(
        _low_value_motor_claim(
            incident_description="Car bumper dented again in parking.",
            claimed_amount=9000,
        )
    )
    assert any("duplicate" in f for f in second.anomaly_flags)
    assert second.anomaly_score > 0
