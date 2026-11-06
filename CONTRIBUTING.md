# Contributing

Thanks for wanting to contribute! This is a small, focused project — please keep changes small and well-tested.

## Getting started

```bash
git clone https://github.com/ProNoob-Dev/ai-insurance-claims-triage-agent.git
cd ai-insurance-claims-triage-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v   # everything should pass before you start
```

## Ground rules

1. **Never weaken the HITL rule.** A denial is only ever a *proposal* until a named human reviewer confirms it in `POST /claims/{id}/review`. Any change that lets the agents finalise a denial will be rejected.
2. **PII stays masked.** No new free-text field may reach an LLM call unmasked — add it to `app/pii.py` first.
3. **Every decision keeps its citation.** If you change retrieval or coverage, `tests/test_evals.py` must still show a citation on every golden-set decision.
4. **Tests for behaviour changes.** Bug fix or new agent behaviour → add or update a test in `tests/`. Run the full suite plus lint before opening a PR:
   ```bash
   pytest -v
   ruff check app scripts tests
   ```
5. **No secrets in the repo.** API keys and endpoints go in `.env` (see `.env.example`). Never commit a real `.env`.

## Good first issues

- More policy clauses in `data/policy_clauses.json` (new products, new exclusions — add a golden-set case to match).
- A real document-intelligence extractor (Azure Document Intelligence) behind the same `DocumentInput` contract.
- A Qdrant-backed eval report (`scripts/eval.py`) that prints per-case routing tables.
- A simple web UI for the adjuster review queue.

## How to submit

1. Fork the repo and create a branch: `git checkout -b fix/short-description`.
2. Make your change, add tests, run `pytest` and `ruff`.
3. Open a pull request describing what changed and why, with the test output.
