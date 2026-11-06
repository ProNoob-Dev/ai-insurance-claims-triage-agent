"""Golden-set evaluation.

Runs the full pipeline over the 20 labelled cases in
``data/golden_set.json`` and asserts the acceptance criteria from the
build spec:

* >= 88% of golden claims routed correctly (auto-approve / adjuster /
  propose-deny),
* ZERO final denials without a human reviewer,
* every decision linked to at least one policy clause citation.
"""

import json
import os

from app.models import ClaimCreate

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_golden() -> list[dict]:
    with open(os.path.join(BASE_DIR, "data", "golden_set.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def test_golden_set_routing_accuracy(pipeline):
    cases = _load_golden()
    assert len(cases) == 20

    correct = 0
    failures: list[str] = []
    for case in cases:
        payload = {
            k: v for k, v in case.items()
            if k not in ("case_id", "expected_decision")
        }
        claim = pipeline.submit_claim(ClaimCreate(**payload))
        got = claim.decision.value if claim.decision else None
        expected = case["expected_decision"]
        if got == expected:
            correct += 1
        else:
            failures.append(
                f"{case['case_id']}: expected {expected}, got {got} "
                f"(covered={claim.covered}, clause={claim.coverage_clause_id}, "
                f"anomaly={claim.anomaly_score})"
            )

    accuracy = correct / len(cases)
    assert accuracy >= 0.88, (
        f"Routing accuracy {accuracy:.0%} < 88% "
        f"({correct}/{len(cases)} correct). Failures:\n" + "\n".join(failures)
    )


def test_golden_set_zero_denials_without_hitl(pipeline):
    cases = _load_golden()
    for case in cases:
        payload = {
            k: v for k, v in case.items()
            if k not in ("case_id", "expected_decision")
        }
        claim = pipeline.submit_claim(ClaimCreate(**payload))
        # No claim may ever be finally denied by the agents alone.
        assert claim.status != "denied", (
            f"{case['case_id']} was denied without human review"
        )
        if claim.decision and claim.decision.value == "propose_deny":
            assert claim.status == "pending_review"
            assert claim.requires_hitl is True


def test_golden_set_every_decision_has_citation(pipeline):
    cases = _load_golden()
    for case in cases:
        payload = {
            k: v for k, v in case.items()
            if k not in ("case_id", "expected_decision")
        }
        claim = pipeline.submit_claim(ClaimCreate(**payload))
        assert len(claim.citations) >= 1, (
            f"{case['case_id']} has no policy citation"
        )
        assert claim.coverage_clause_id is not None
