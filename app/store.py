"""Claim store + audit log.

Claims live in a small JSON file (``data/claims.json``) so the demo
survives restarts without a database. Every triage decision and every
human review is appended to an audit log — both on the claim record and
in ``data/audit_log.jsonl`` — so any decision can be traced back to its
source documents and the policy clause that justified it.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from app.models import AuditEntry, Claim

_lock = threading.Lock()


class ClaimStore:
    def __init__(self, claims_file: str, audit_file: str) -> None:
        self._claims_file = claims_file
        self._audit_file = audit_file
        os.makedirs(os.path.dirname(claims_file) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(audit_file) or ".", exist_ok=True)
        self._claims: dict[str, Claim] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self._claims_file):
            try:
                with open(self._claims_file, encoding="utf-8") as fh:
                    raw = json.load(fh)
                self._claims = {
                    item["id"]: Claim(**item) for item in raw
                }
            except (json.JSONDecodeError, KeyError, TypeError):
                self._claims = {}

    def _save(self) -> None:
        tmp = self._claims_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                [c.model_dump() for c in self._claims.values()],
                fh,
                indent=2,
                ensure_ascii=False,
            )
        os.replace(tmp, self._claims_file)

    # -- CRUD ---------------------------------------------------------------
    def new_id(self) -> str:
        return f"CLM-{uuid.uuid4().hex[:8].upper()}"

    def create(self, claim: Claim) -> Claim:
        with _lock:
            self._claims[claim.id] = claim
            self._save()
        return claim

    def get(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)

    def update(self, claim: Claim) -> Claim:
        with _lock:
            self._claims[claim.id] = claim
            self._save()
        return claim

    def list_all(self) -> list[Claim]:
        return sorted(
            self._claims.values(), key=lambda c: c.created_at, reverse=True
        )

    def find_duplicates(
        self, *, policy_number: str, claim_type: str, exclude_id: str
    ) -> list[Claim]:
        """Earlier claims on the same policy of the same type.

        Used by the scoring agent to flag possible duplicate filings.
        """
        return [
            c
            for c in self._claims.values()
            if c.id != exclude_id
            and c.policy_number == policy_number
            and c.claim_type == claim_type
        ]

    # -- audit --------------------------------------------------------------
    def audit(self, claim: Claim, *, actor: str, action: str, detail: str) -> None:
        entry = AuditEntry(actor=actor, action=action, detail=detail)
        claim.audit.append(entry)
        with _lock:
            with open(self._audit_file, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "claim_id": claim.id,
                            "at": entry.at,
                            "actor": actor,
                            "action": action,
                            "detail": detail,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            self._save()

    def audit_entries(self, claim_id: str | None = None) -> list[dict]:
        if not os.path.exists(self._audit_file):
            return []
        entries = []
        with open(self._audit_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if claim_id is None or item.get("claim_id") == claim_id:
                    entries.append(item)
        return entries
