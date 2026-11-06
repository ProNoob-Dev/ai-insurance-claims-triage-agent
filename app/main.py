"""Insurance Claims Triage Agent — FastAPI service.

Submit a claim, and four LangGraph agents triage it: ingestion classifies
and extracts, retrieval checks the policy wording, scoring flags
anomalies, and the supervisor routes it — auto-approve for clear
low-value claims, adjuster review for everything else. A denial is only
ever a *proposal* until a human reviewer confirms it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models import Claim, ClaimCreate, ClaimSummary, MetricsResponse, ReviewRequest
from app.pipeline import TriagePipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

pipeline: TriagePipeline | None = None

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    pipeline = TriagePipeline()
    logger.info("Claims Triage Agent started (LLM provider: %s)",
                pipeline.llm.model)
    yield


app = FastAPI(
    title="Insurance Claims Triage Agent",
    description=(
        "Multi-agent claims triage: read the policy, check the evidence, "
        "and triage each claim. Simple claims go straight through; the "
        "rest reach an adjuster with a full evidence pack. Built with "
        "FastAPI, LangGraph, Qdrant and Azure OpenAI."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # Self-hosted API docs (no CDN dependency — /docs works offline).
    # Swagger UI assets are vendored under app/static/swagger-ui/.
    docs_url=None,
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/docs", include_in_schema=False, tags=["service"])
def custom_swagger_ui() -> HTMLResponse:
    """Swagger UI, served from vendored local assets (works offline)."""
    return HTMLResponse(
        """
        <!DOCTYPE html>
        <html>
        <head>
          <link type="text/css" rel="stylesheet"
                href="/static/swagger-ui/swagger-ui.css">
          <title>Insurance Claims Triage Agent - Swagger UI</title>
        </head>
        <body>
          <div id="swagger-ui"></div>
          <script src="/static/swagger-ui/swagger-ui-bundle.js"></script>
          <script src="/static/swagger-ui/swagger-ui-standalone-preset.js"></script>
          <script>
            window.onload = () => {
              window.ui = SwaggerUIBundle({
                url: "/openapi.json",
                dom_id: "#swagger-ui",
                presets: [
                  SwaggerUIBundle.presets.apis,
                  SwaggerUIStandalonePreset
                ],
                layout: "StandaloneLayout"
              });
            };
          </script>
        </body>
        </html>
        """
    )


def _pipeline() -> TriagePipeline:
    assert pipeline is not None
    return pipeline


# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False, tags=["service"])
def dashboard() -> FileResponse:
    """User-facing dashboard (vanilla HTML/CSS/JS, no build step)."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health", tags=["service"])
def health() -> dict:
    return {"status": "ok", "llm_provider": _pipeline().llm.model}


# ---------------------------------------------------------------------------
@app.post("/claims", response_model=Claim, status_code=201, tags=["claims"])
def submit_claim(data: ClaimCreate) -> Claim:
    """Submit a claim and run the full triage pipeline on it."""
    return _pipeline().submit_claim(data)


@app.get("/claims", response_model=list[ClaimSummary], tags=["claims"])
def list_claims(
    status: str | None = Query(default=None, description="Filter by status"),
    claim_type: str | None = Query(default=None, description="motor/health/property/travel"),
    min_amount: float | None = Query(default=None),
    max_amount: float | None = Query(default=None),
) -> list[ClaimSummary]:
    claims = _pipeline().list_claims(
        status=status, claim_type=claim_type,
        min_amount=min_amount, max_amount=max_amount,
    )
    return [
        ClaimSummary(
            id=c.id,
            claim_type=c.claim_type,
            status=c.status,
            decision=c.decision.value if c.decision else None,
            claimed_amount=c.claimed_amount,
            anomaly_score=c.anomaly_score,
            requires_hitl=c.requires_hitl,
            created_at=c.created_at,
        )
        for c in claims
    ]


@app.get("/claims/{claim_id}", response_model=Claim, tags=["claims"])
def get_claim(claim_id: str) -> Claim:
    claim = _pipeline().get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@app.post("/claims/{claim_id}/review", response_model=Claim, tags=["claims"])
def review_claim(claim_id: str, review: ReviewRequest) -> Claim:
    """Human-in-the-loop review.

    Only ``pending_review`` claims can be reviewed. This is the ONLY way
    a claim reaches ``denied`` status — the agents can propose a denial,
    but a human must confirm it.
    """
    try:
        claim = _pipeline().review_claim(
            claim_id,
            decision=review.decision,
            reviewer=review.reviewer,
            note=review.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


@app.get("/metrics", response_model=MetricsResponse, tags=["claims"])
def get_metrics() -> MetricsResponse:
    return MetricsResponse(**_pipeline().metrics())


@app.get("/audit", tags=["claims"])
def get_audit(claim_id: str | None = Query(default=None)) -> JSONResponse:
    """Audit log — every triage decision and human review, with actor."""
    return JSONResponse(content=_pipeline().store.audit_entries(claim_id))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host=settings.host, port=settings.port, reload=False
    )
