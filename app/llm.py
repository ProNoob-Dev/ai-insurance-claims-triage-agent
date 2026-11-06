"""LLM provider interface.

The pipeline NEVER calls a model directly — it always goes through
``LLMProvider.complete()``. Two implementations ship:

* :class:`MockLLMProvider` (default) — deterministic, offline, rule-based.
  It implements the same prompts and the same JSON contracts as the real
  provider, so the whole system runs, demos and tests without any API key.
* :class:`AzureOpenAIProvider` — real Azure OpenAI (GPT-4o), activated by
  setting ``LLM_PROVIDER=azure`` plus ``AZURE_OPENAI_ENDPOINT``,
  ``AZURE_OPENAI_API_KEY`` and ``AZURE_OPENAI_DEPLOYMENT``.

No API key is ever hardcoded. If ``LLM_PROVIDER=azure`` is requested but
the credentials are missing, startup falls back to the mock provider with
a loud warning instead of crashing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(Protocol):
    def complete(
        self, *, system: str, prompt: str, json_mode: bool = False
    ) -> LLMResponse:
        ...


# ---------------------------------------------------------------------------
# Mock provider (default)
# ---------------------------------------------------------------------------

_CLAIM_TYPE_KEYWORDS: dict[str, list[str]] = {
    "motor": [
        "car", "vehicle", "accident", "bumper", "windshield", "windscreen",
        "collision", "bike", "motorcycle", "dent", "scratch", "theft",
        "stolen", "garage", "driving", "road",
    ],
    "health": [
        "hospital", "surgery", "treatment", "doctor", "admission",
        "medical", "illness", "fracture", "dengue", "surgery", "therapy",
        "clinic", "maternity", "delivery",
    ],
    "property": [
        "house", "home", "fire", "burglary", "flood", "roof", "theft",
        "apartment", "flat", "earthquake", "water damage", "contents",
        "electronics", "jewellery", "jewelry", "laptop",
    ],
    "travel": [
        "flight", "trip", "baggage", "luggage", "cancelled", "cancellation",
        "hotel", "journey", "abroad", "visa",
    ],
}


class MockLLMProvider:
    """Deterministic stand-in for Azure OpenAI.

    The mock reads the structured claim JSON embedded in the prompt and
    applies the same decision rules a prompted model would be instructed
    to follow. It is deliberately boring: no randomness, so evals are
    reproducible.
    """

    model = "mock-llm-v1"

    def complete(
        self, *, system: str, prompt: str, json_mode: bool = False
    ) -> LLMResponse:
        task = self._task_of(prompt)
        claim = self._claim_of(prompt)
        clauses = self._clauses_of(prompt)

        if task == "classify":
            result = self._classify(claim)
        elif task == "extract":
            result = self._extract(claim)
        elif task == "coverage":
            result = self._coverage(claim, clauses)
        else:  # generic fallback — echo a safe structured answer
            result = {"answer": "not-enough-information"}

        content = json.dumps(result)
        return LLMResponse(
            content=content,
            model=self.model,
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(content.split()),
        )

    # -- prompt parsing ---------------------------------------------------
    @staticmethod
    def _task_of(prompt: str) -> str:
        match = re.search(r"TASK:\s*(\w+)", prompt)
        return match.group(1).lower() if match else ""

    @staticmethod
    def _claim_of(prompt: str) -> dict[str, Any]:
        match = re.search(r"CLAIM_JSON:\s*(\{.*?\})\s*(?:\n|$)", prompt, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _clauses_of(prompt: str) -> list[dict[str, Any]]:
        match = re.search(r"CLAUSES_JSON:\s*(\[.*?\])\s*(?:\n|$)", prompt, re.DOTALL)
        if not match:
            return []
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

    # -- tasks ------------------------------------------------------------
    @staticmethod
    def _classify(claim: dict[str, Any]) -> dict[str, Any]:
        text = " ".join(
            str(claim.get(field, ""))
            for field in ("incident_description", "claim_type_hint", "title")
        ).lower()
        # Documents are also a signal: a hospital bill means health, etc.
        for doc in claim.get("documents", []) or []:
            text += " " + str(doc.get("content", "")).lower()

        scores = {
            ctype: sum(1 for kw in kws if kw in text)
            for ctype, kws in _CLAIM_TYPE_KEYWORDS.items()
        }
        best = max(scores, key=lambda k: scores[k])
        confidence = 0.9 if scores[best] >= 2 else (0.7 if scores[best] == 1 else 0.4)
        return {
            "claim_type": best if scores[best] > 0 else "unknown",
            "confidence": confidence,
            "reasoning": f"Matched {scores[best]} keyword(s) for {best}.",
        }

    @staticmethod
    def _extract(claim: dict[str, Any]) -> dict[str, Any]:
        description = str(claim.get("incident_description", ""))
        docs_text = " ".join(
            str(doc.get("content", "")) for doc in claim.get("documents", []) or []
        )
        full = f"{description} {docs_text}"

        # Amounts: prefer an explicit claimed_amount, else the largest
        # currency figure mentioned in the text.
        amount = claim.get("claimed_amount")
        if amount is None:
            figures = [
                float(m.replace(",", ""))
                for m in re.findall(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)", full)
            ]
            amount = max(figures) if figures else None

        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", full)
        return {
            "policy_number": claim.get("policy_number"),
            "incident_date": claim.get("incident_date")
            or (date_match.group(1) if date_match else None),
            "claimed_amount_inr": amount,
            "incident_summary": description[:240],
            "documents_seen": len(claim.get("documents", []) or []),
        }

    @staticmethod
    def _coverage(
        claim: dict[str, Any], clauses: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Decide coverage from the retrieved clauses.

        The best-matching clause is picked by keyword overlap with the
        claim text (the retrieval agent's re-rank), and coverage is
        assessed against that one clause only — the way a human adjuster
        reads the single most relevant policy section. Exclusions are
        matched two ways: explicit ``flags`` on the claim (machine-readable
        tags such as ``no_valid_licence``) and the exclusion phrase
        appearing in the incident/description text.
        """
        text = (
            str(claim.get("incident_description", ""))
            + " "
            + " ".join(
                str(doc.get("content", ""))
                for doc in claim.get("documents", []) or []
            )
        ).lower()
        flags = {str(f).lower() for f in claim.get("flags", []) or []}

        def _match_score(clause: dict[str, Any]) -> int:
            score = 0
            for kw in clause.get("keywords", []) or []:
                if kw.lower() in text:
                    score += 1
            for word in str(clause.get("title", "")).lower().split():
                if len(word) > 3 and word in text:
                    score += 1
            return score

        if not clauses:
            return {
                "covered": False,
                "clause_id": None,
                "clause_title": None,
                "coverage_limit_inr": None,
                "deductible_inr": 0,
                "reasoning": "No policy clause retrieved for this claim.",
            }

        best = max(clauses, key=_match_score)
        hit: list[str] = []
        for exc in best.get("exclusions", []) or []:
            key = str(exc.get("key", "")).lower()
            phrase = str(exc.get("text", "")).lower()
            if key and key in flags:
                hit.append(exc.get("text", key))
            elif phrase and phrase in text:
                hit.append(exc.get("text", phrase))
        if hit:
            return {
                "covered": False,
                "clause_id": best["id"],
                "clause_title": best.get("title", ""),
                "coverage_limit_inr": best.get("coverage_limit_inr"),
                "deductible_inr": best.get("deductible_inr", 0),
                "reasoning": (
                    f"Clause {best['id']} applies but exclusion "
                    f"triggered: " + "; ".join(hit)
                ),
            }
        return {
            "covered": True,
            "clause_id": best["id"],
            "clause_title": best.get("title", ""),
            "coverage_limit_inr": best.get("coverage_limit_inr"),
            "deductible_inr": best.get("deductible_inr", 0),
            "reasoning": (
                f"Claim matches clause {best['id']} "
                f"({best.get('title', '')}); no exclusion triggered."
            ),
        }


# ---------------------------------------------------------------------------
# Azure OpenAI provider
# ---------------------------------------------------------------------------

class AzureOpenAIProvider:
    """Real Azure OpenAI provider (GPT-4o)."""

    def __init__(self, *, endpoint: str, api_key: str, deployment: str,
                 api_version: str) -> None:
        from openai import AzureOpenAI

        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        self._deployment = deployment
        self.model = deployment

    def complete(
        self, *, system: str, prompt: str, json_mode: bool = False
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._deployment,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=self._deployment,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )


def get_llm_provider() -> LLMProvider:
    """Build the configured provider, falling back to mock if needed."""
    from app.config import settings

    if settings.llm_provider == "azure":
        if settings.azure_openai_endpoint and settings.azure_openai_api_key:
            logger.info("LLM provider: Azure OpenAI (%s)",
                        settings.azure_openai_deployment)
            return AzureOpenAIProvider(
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                deployment=settings.azure_openai_deployment,
                api_version=settings.azure_openai_api_version,
            )
        logger.warning(
            "LLM_PROVIDER=azure but Azure credentials are missing — "
            "falling back to the mock provider. Set AZURE_OPENAI_ENDPOINT "
            "and AZURE_OPENAI_API_KEY to use the real model."
        )
    logger.info("LLM provider: mock (offline, deterministic)")
    return MockLLMProvider()
