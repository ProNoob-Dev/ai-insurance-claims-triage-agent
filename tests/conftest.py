"""Test fixtures.

Tests run against a temporary claim store (never the repo's data/ files)
and the deterministic mock LLM provider, so they are fully offline and
reproducible.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="claims_triage_test_")
os.environ["CLAIMS_DB_FILE"] = os.path.join(_TMP, "claims.json")
os.environ["AUDIT_LOG_FILE"] = os.path.join(_TMP, "audit.jsonl")
os.environ["LLM_PROVIDER"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import TriagePipeline
from app.store import ClaimStore


@pytest.fixture()
def pipeline(tmp_path):
    store = ClaimStore(
        str(tmp_path / "claims.json"), str(tmp_path / "audit.jsonl")
    )
    return TriagePipeline(store=store)


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        # Fresh store per test: the API's claim store is file-backed and
        # shared across tests, so clear it to avoid duplicate-flag
        # interference between tests using the same policy numbers.
        import app.main as main_module

        store = main_module.pipeline.store
        store._claims.clear()
        store._save()
        if os.path.exists(store._audit_file):
            os.remove(store._audit_file)
        yield test_client
