"""Seed data: 40 sample claims + a labelled golden set of 20.

Run ``python scripts/seed.py`` to (re)generate ``data/sample_claims.json``
and ``data/golden_set.json``. Run ``python scripts/seed.py --submit`` to
also POST every sample claim to a locally running API
(http://localhost:8000 by default).

The golden set is the eval set: each case carries the routing the
pipeline is expected to produce. ``tests/test_evals.py`` runs the
pipeline over it and asserts >= 88% correct routing, zero final denials
without human review, and a citation on every decision.
"""

from __future__ import annotations

import argparse
import json
import os
import random

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

POLICIES = {
    "motor": "MOT-P",
    "health": "HLT-P",
    "property": "PRP-P",
    "travel": "TRV-P",
}

FIRST = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Karan",
         "Meera", "Arjun", "Divya", "Nikhil", "Kavya", "Aditya", "Riya",
         "Sanjay", "Pooja", "Manish", "Ishita", "Varun", "Nisha"]
LAST = ["Sharma", "Patel", "Iyer", "Reddy", "Khan", "Gupta", "Nair", "Singh",
        "Das", "Joshi", "Mehta", "Kulkarni", "Chopra", "Menon", "Rao"]


def _doc(name: str, content: str) -> dict:
    return {"name": name, "content": content, "content_type": "text/plain"}


def _claim(**overrides) -> dict:
    base = {
        "claimant_name": "Test Claimant",
        "policy_number": "MOT-P-10001",
        "policy_start_date": "2025-01-01",
        "incident_date": "2026-03-10",
        "incident_description": "",
        "claimed_amount": 10000,
        "documents": [],
        "flags": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Golden set (20) — labelled expected routing
# ---------------------------------------------------------------------------
def golden_set() -> list[dict]:
    cases: list[dict] = []

    def add(name: str, expected: str, **kw) -> None:
        case = _claim(**kw)
        case["case_id"] = name
        case["expected_decision"] = expected
        cases.append(case)

    # --- auto-approve: low value, covered, clean, documents present ---
    add("G01-motor-bumper", "auto_approve",
        claimant_name="Aarav Sharma", policy_number="MOT-P-10001",
        incident_description="Car bumper dented in a minor parking collision. "
        "Repair estimate attached.",
        claimed_amount=8500,
        documents=[_doc("repair_estimate.pdf",
                         "Repair estimate: bumper replacement Rs. 8,500")])
    add("G02-motor-windshield", "auto_approve",
        claimant_name="Priya Patel", policy_number="MOT-P-10002",
        incident_description="Windshield cracked by a stone on the highway. "
        "Windshield replacement needed.",
        claimed_amount=12000,
        documents=[_doc("repair_estimate.pdf",
                         "Windshield replacement Rs. 12,000")])
    add("G03-health-cataract", "auto_approve",
        claimant_name="Rohan Iyer", policy_number="HLT-P-20001",
        incident_description="Cataract surgery done as a day-care procedure "
        "at City Eye Hospital.",
        claimed_amount=18000,
        documents=[_doc("hospital_bill.pdf",
                         "Day-care cataract surgery bill Rs. 18,000")])
    add("G04-travel-baggage", "auto_approve",
        claimant_name="Sneha Reddy", policy_number="TRV-P-40001",
        incident_description="Checked-in baggage lost on flight to Singapore. "
        "Airline issued Property Irregularity Report.",
        claimed_amount=9500,
        documents=[_doc("pir.pdf",
                         "Property Irregularity Report: 1 bag lost, "
                         "value Rs. 9,500")])
    add("G05-motor-bike-scratch", "auto_approve",
        claimant_name="Vikram Khan", policy_number="MOT-P-10003",
        incident_description="Motorcycle scratched and indicator broken after "
        "a minor fall on a wet road.",
        claimed_amount=6200,
        documents=[_doc("repair_estimate.pdf",
                         "Repairs Rs. 6,200")])
    add("G06-health-dengue", "auto_approve",
        claimant_name="Ananya Gupta", policy_number="HLT-P-20002",
        incident_description="Hospitalised for dengue fever for 3 days, "
        "IV fluids and monitoring.",
        claimed_amount=22000,
        documents=[_doc("hospital_bill.pdf",
                         "Hospital bill: 3 days admission Rs. 22,000")])
    add("G07-property-fire-small", "auto_approve",
        claimant_name="Karan Nair", policy_number="PRP-P-30001",
        incident_description="Small kitchen fire damaged the chimney and "
        "cabinets. Fire brigade report attached.",
        claimed_amount=20000,
        documents=[_doc("fire_report.pdf",
                         "Fire damage assessment Rs. 20,000")])
    add("G08-travel-cancellation", "auto_approve",
        claimant_name="Meera Singh", policy_number="TRV-P-40001",
        incident_description="Trip cancelled because of sudden illness. "
        "Doctor certificate and booking proof attached.",
        claimed_amount=15000,
        documents=[_doc("doctor_certificate.pdf",
                         "Unfit to travel; non-refundable booking "
                         "Rs. 15,000")])

    # --- route to adjuster: covered, but value or complexity needs a human ---
    add("G09-motor-collision", "route_to_adjuster",
        claimant_name="Arjun Das", policy_number="MOT-P-10001",
        incident_description="Car collision at an intersection. Front bumper, "
        "headlight and bonnet damaged.",
        claimed_amount=85000,
        documents=[_doc("repair_estimate.pdf",
                         "Repair estimate Rs. 85,000"),
                   _doc("photos.zip", "Accident photos")])
    add("G10-health-surgery", "route_to_adjuster",
        claimant_name="Divya Joshi", policy_number="HLT-P-20001",
        incident_description="Knee replacement surgery, 5 days hospitalisation.",
        claimed_amount=250000,
        documents=[_doc("hospital_bill.pdf",
                         "Surgery and stay Rs. 2,50,000")])
    add("G11-property-flood", "route_to_adjuster",
        claimant_name="Nikhil Mehta", policy_number="PRP-P-30002",
        incident_description="Flood water entered the ground floor and damaged "
        "furniture and appliances.",
        claimed_amount=400000,
        documents=[_doc("damage_assessment.pdf",
                         "Flood damage assessment Rs. 4,00,000")])
    add("G12-motor-no-documents", "route_to_adjuster",
        claimant_name="Kavya Kulkarni", policy_number="MOT-P-10002",
        incident_description="Car accident on highway, side panel and door "
        "damaged. Repair estimate will follow.",
        claimed_amount=120000,
        documents=[])
    add("G13-travel-baggage-high", "route_to_adjuster",
        claimant_name="Aditya Chopra", policy_number="TRV-P-40001",
        incident_description="Two checked-in bags lost on an international "
        "flight, including a laptop.",
        claimed_amount=45000,
        documents=[_doc("pir.pdf",
                         "Property Irregularity Report: 2 bags lost")])
    add("G14-health-maternity", "route_to_adjuster",
        claimant_name="Riya Menon", policy_number="HLT-P-20002",
        incident_date="2026-02-01",
        incident_description="Normal delivery at City Hospital, 2 days stay.",
        claimed_amount=40000,
        documents=[_doc("hospital_bill.pdf",
                         "Delivery and stay Rs. 40,000")])
    add("G15-property-burglary", "route_to_adjuster",
        claimant_name="Sanjay Rao", policy_number="PRP-P-30001",
        incident_description="House burglary overnight, laptop and jewellery "
        "stolen. FIR filed.",
        claimed_amount=150000,
        documents=[_doc("fir.pdf", "FIR copy: burglary reported"),
                   _doc("valuation.pdf", "Stolen items valued Rs. 1,50,000")])
    add("G16-motor-theft", "route_to_adjuster",
        claimant_name="Pooja Sharma", policy_number="MOT-P-10003",
        incident_description="Car stolen from apartment parking overnight. "
        "FIR filed within 24 hours.",
        claimed_amount=300000,
        documents=[_doc("fir.pdf", "FIR copy: vehicle theft reported")])

    # --- propose deny: exclusion triggered, must go to human review ---
    add("G17-motor-wear-tear", "propose_deny",
        claimant_name="Manish Patel", policy_number="MOT-P-10001",
        incident_description="Car engine and paint show wear and tear after "
        "4 years of use. Requesting replacement of worn parts.",
        claimed_amount=15000,
        flags=["wear_and_tear"],
        documents=[_doc("inspection.pdf",
                         "Inspection: general wear and tear, no accident")])
    add("G18-motor-no-licence", "propose_deny",
        claimant_name="Ishita Iyer", policy_number="MOT-P-10002",
        incident_description="Car accident while driving without a valid "
        "licence. Front damage.",
        claimed_amount=45000,
        flags=["no_valid_licence"],
        documents=[_doc("repair_estimate.pdf",
                         "Repair estimate Rs. 45,000")])
    add("G19-health-opd", "propose_deny",
        claimant_name="Varun Reddy", policy_number="HLT-P-20001",
        incident_description="OPD consultation for fever, doctor prescribed "
        "medicines. No admission.",
        claimed_amount=3000,
        flags=["opd_only"],
        documents=[_doc("prescription.pdf",
                         "OPD prescription Rs. 3,000")])
    add("G20-health-pre-existing", "propose_deny",
        claimant_name="Nisha Khan", policy_number="HLT-P-20002",
        incident_description="Hospital treatment for diabetes, a pre-existing "
        "condition diagnosed 3 years ago.",
        claimed_amount=60000,
        flags=["pre_existing"],
        documents=[_doc("hospital_bill.pdf",
                         "Diabetes treatment Rs. 60,000")])
    return cases


# ---------------------------------------------------------------------------
# Sample claims (40) — realistic variety for demos and manual testing
# ---------------------------------------------------------------------------
_TEMPLATES = [
    ("motor", "Car bumper dented in parking. Repair estimate attached.",
     6000, 90000),
    ("motor", "Windshield cracked on the highway, needs replacement.",
     9000, 40000),
    ("motor", "Bike skidded on a wet road, handle and mirror damaged.",
     4000, 30000),
    ("motor", "Car collision at a signal, front end damaged.",
     30000, 180000),
    ("motor", "Car stolen from office parking, FIR filed.",
     200000, 450000),
    ("health", "Hospitalised for dengue, 3 days admission.",
     15000, 60000),
    ("health", "Knee surgery and 4 days hospital stay.",
     120000, 350000),
    ("health", "Cataract surgery as day-care procedure.",
     15000, 30000),
    ("health", "Fracture treatment after a fall, plaster and physio.",
     20000, 90000),
    ("property", "Kitchen fire damaged cabinets and chimney.",
     15000, 80000),
    ("property", "Flood water damaged ground floor furniture.",
     100000, 600000),
    ("property", "Burglary overnight, electronics stolen. FIR filed.",
     80000, 300000),
    ("travel", "Checked-in baggage lost, PIR issued by airline.",
     8000, 40000),
    ("travel", "Trip cancelled due to illness, booking non-refundable.",
     10000, 60000),
]


def sample_claims(n: int = 40, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    claims: list[dict] = []
    for i in range(n):
        ctype, desc, lo, hi = _TEMPLATES[i % len(_TEMPLATES)]
        amount = rng.randint(lo, hi)
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        # Unique policy per claim: reusing one policy across many claims
        # would (correctly) trip the duplicate-filing anomaly detector.
        policy = f"{POLICIES[ctype]}-{10000 + i}"
        claims.append(_claim(
            claimant_name=name,
            policy_number=policy,
            incident_description=desc,
            claimed_amount=amount,
            incident_date=f"2026-0{rng.randint(1, 3)}-{rng.randint(10, 28):02d}",
            documents=[_doc("supporting_document.pdf",
                             f"{desc} Assessed value Rs. {amount:,}.")],
        ))
    return claims


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate seed data.")
    parser.add_argument("--submit", action="store_true",
                        help="POST sample claims to a running API")
    parser.add_argument("--api", default="http://localhost:8000",
                        help="API base URL for --submit")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    golden = golden_set()
    samples = sample_claims()
    with open(os.path.join(DATA_DIR, "golden_set.json"), "w",
              encoding="utf-8") as fh:
        json.dump(golden, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(DATA_DIR, "sample_claims.json"), "w",
              encoding="utf-8") as fh:
        json.dump(samples, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {len(golden)} golden cases and {len(samples)} sample claims.")

    if args.submit:
        import urllib.request

        for claim in samples:
            body = {k: v for k, v in claim.items()
                    if k not in ("case_id", "expected_decision")}
            req = urllib.request.Request(
                args.api.rstrip("/") + "/claims",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            print(f"  {result['id']}: {result['claim_type']} "
                  f"-> {result['status']} ({result['decision']})")


if __name__ == "__main__":
    main()
