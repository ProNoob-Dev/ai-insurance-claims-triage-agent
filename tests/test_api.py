"""API tests — full HTTP round-trips through the FastAPI app."""

PAYLOAD = {
    "claimant_name": "Aarav Sharma",
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


def test_submit_claim_returns_triaged_claim(client):
    resp = client.post("/claims", json=PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert body["claim_type"] == "motor"
    assert body["status"] == "auto_approved"
    assert body["decision"] == "auto_approve"
    assert len(body["citations"]) >= 1
    assert body["coverage_clause_id"] == "MOTOR-001"
    assert body["triage_duration_ms"] >= 0
    assert len(body["trace"]) == 5  # classify, extract, retrieve, score, supervise


def test_get_claim_and_list(client):
    created = client.post("/claims", json=PAYLOAD).json()
    fetched = client.get(f"/claims/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    listed = client.get("/claims", params={"status": "auto_approved"})
    assert listed.status_code == 200
    assert any(c["id"] == created["id"] for c in listed.json())


def test_get_missing_claim_404(client):
    assert client.get("/claims/CLM-DOESNOTEXIST").status_code == 404


def test_review_flow_end_to_end(client):
    high_value = dict(PAYLOAD, claimed_amount=95000)
    created = client.post("/claims", json=high_value).json()
    assert created["status"] == "pending_review"

    reviewed = client.post(
        f"/claims/{created['id']}/review",
        json={"decision": "approve", "reviewer": "adjuster-1",
              "note": "Verified with garage."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "approved"
    assert reviewed.json()["reviewer"] == "adjuster-1"

    # Reviewing an already-decided claim is a conflict.
    again = client.post(
        f"/claims/{created['id']}/review",
        json={"decision": "deny", "reviewer": "adjuster-1"},
    )
    assert again.status_code == 409


def test_metrics_endpoint(client):
    client.post("/claims", json=PAYLOAD)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_claims"] >= 1
    assert body["auto_approved"] >= 1
    assert 0.0 <= body["autonomous_rate"] <= 1.0


def test_audit_endpoint(client):
    created = client.post("/claims", json=PAYLOAD).json()
    resp = client.get("/audit", params={"claim_id": created["id"]})
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()]
    assert "claim-submitted" in actions
    assert "triage-decision" in actions
