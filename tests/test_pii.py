"""PII masking tests — nothing sensitive may reach the model."""

from app.pii import mask_claim_payload, mask_text


def test_masks_email_phone_aadhaar_pan():
    text = (
        "Reach me at raj@example.com or 9876543210. "
        "Aadhaar 1234 5678 9012, PAN ABCDE1234F."
    )
    masked, detected = mask_text(text)
    assert "raj@example.com" not in masked
    assert "9876543210" not in masked
    assert "1234 5678 9012" not in masked
    assert "ABCDE1234F" not in masked
    assert set(detected) == {"EMAIL", "PHONE", "AADHAAR", "PAN"}


def test_masks_claimant_name():
    masked, detected = mask_text(
        "Aarav Sharma called about his car.", extra_names=["Aarav Sharma"]
    )
    assert "Aarav Sharma" not in masked
    assert "NAME" in detected


def test_clean_text_untouched():
    text = "Car bumper dented in a parking collision."
    masked, detected = mask_text(text)
    assert masked == text
    assert detected == []


def test_mask_claim_payload_keeps_structure():
    payload = {
        "claimant_name": "Aarav Sharma",
        "claimant_email": "aarav@example.com",
        "policy_number": "MOT-P-10001",
        "incident_description": "Call me on 9876543210 about the car.",
        "documents": [
            {"name": "bill.pdf", "content": "Email aarav@example.com"}
        ],
    }
    safe = mask_claim_payload(payload)
    assert safe["policy_number"] == "MOT-P-10001"  # needed for triage
    assert safe["claimant_name"] == "<NAME_REDACTED>"
    assert "9876543210" not in safe["incident_description"]
    assert "aarav@example.com" not in safe["documents"][0]["content"]
