"""Configuration for the Insurance Claims Triage Agent.

All settings come from environment variables (see .env.example).
No secrets are hardcoded anywhere in this project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Application settings, loaded from the environment."""

    # LLM provider: "mock" (default, offline, deterministic) or "azure"
    llm_provider: str = os.environ.get("LLM_PROVIDER", "mock").lower()

    # Azure OpenAI (only used when LLM_PROVIDER=azure)
    azure_openai_endpoint: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_api_key: str = os.environ.get("AZURE_OPENAI_API_KEY", "")
    azure_openai_deployment: str = os.environ.get(
        "AZURE_OPENAI_DEPLOYMENT", "gpt-4o"
    )
    azure_openai_api_version: str = os.environ.get(
        "AZURE_OPENAI_API_VERSION", "2024-02-01"
    )

    # Vector store: "qdrant" if QDRANT_URL is reachable, else in-memory fallback
    qdrant_url: str = os.environ.get("QDRANT_URL", "http://localhost:6333")
    qdrant_collection: str = os.environ.get(
        "QDRANT_COLLECTION", "policy_clauses"
    )

    # Triage policy
    auto_approve_limit_inr: int = int(
        os.environ.get("AUTO_APPROVE_LIMIT_INR", "25000")
    )
    anomaly_route_threshold: float = float(
        os.environ.get("ANOMALY_ROUTE_THRESHOLD", "0.6")
    )
    anomaly_low_threshold: float = float(
        os.environ.get("ANOMALY_LOW_THRESHOLD", "0.3")
    )

    # Persistence
    data_dir: str = os.environ.get("DATA_DIR", "data")
    audit_log_file: str = os.environ.get(
        "AUDIT_LOG_FILE", "data/audit_log.jsonl"
    )
    claims_db_file: str = os.environ.get("CLAIMS_DB_FILE", "data/claims.json")

    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8000"))


settings = Settings()
