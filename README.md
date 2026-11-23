# Capitation Revenue Reconciliation (prototype)

> 📊 **Deck (main deliverable): [Capitation_Reconciliation.pptx](Capitation_Reconciliation.pptx)** · open the file and
> click **Download** (or "View raw") to view it in PowerPoint.

Checks whether a health plan was paid correctly by the state each month, explains any differences, and corrects
past months when rates change. A deterministic engine computes every dollar, bounded Claude agents read rate notices
and explain gaps, and nothing reaches the ledger without an audited human approval. **All data is synthetic.**

> *Code computes. AI reads and explains. Humans approve.*

| Document | What's in it |
|---|---|
| [`Capitation_Reconciliation.pptx`](Capitation_Reconciliation.pptx) | **The deliverable deck:** understanding, approach, architecture and workflow diagrams, evaluation, decisions |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | As-built architecture, 11 Mermaid diagrams (prototype, data layers, close workflow, retro sequence, agents, privacy path, state machines, Azure target), plan vs. built |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 65 decisions: options, why, tradeoff, including bugs the build caught |
| [`evals/results.md`](evals/results.md) | Measured results against ground truth (all calls on Claude Haiku), with charts in `evals/charts/` |
| [`evals/recorded_opus_mix/`](evals/recorded_opus_mix/results.md) | Earlier run with Opus for extraction and complex cases; same quality, about 4x the cost |

## Results (synthetic benchmark, Claude Haiku)

| Measure | Result |
|---|---|
| Rate extraction, PDF + Excel + email | 34 / 34 cells exact |
| Seeded payment issues detected | 340 / 340, 0 false positives |
| Exact cause found, 462 variances | 94.6% rules only → 97.4% with AI (multi-cause cases: 52%) |
| Findings passing evidence and amount checks | 139 / 140 |
| Retro restatement vs. ground truth | $23,931.00, exact to the cent |
| Privacy | 0 leaks found; 200 / 200 injected PHI payloads blocked |
| Model cost for a full half-year run (uncached) | ≈ $0.08 |

## Quick start (Windows, Python 3.11+; built on 3.13)

No API key or secret is needed to run offline; none is stored in this repository.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe tasks.py data      # generate the synthetic data, rate documents and ground truth
.\.venv\Scripts\python.exe tasks.py test      # run the test suite
.\.venv\Scripts\python.exe tasks.py app       # open the app (free, offline mock AI)
```

### Run with the real model (your own key)

1. `copy .env.example .env`, then put **your own** `ANTHROPIC_API_KEY` in `.env` (it is gitignored) and set any long
   random `PSEUDONYM_SECRET`.
2. Either set `llm.provider: anthropic` in `config.yaml` (the app then uses the live model), or pass
   `--provider anthropic` to a single command.
3. Full scored evaluation with a hard spend cap: `python -m evals.run_evals --provider anthropic --max-spend 0.20`
   (about $0.08 on Haiku). Responses are cached in `data/llm_cache/` on your machine, so re-running is free and
   reproduces the results exactly.

The model for each task is set in `config.yaml` (`llm.tasks`). All tasks currently use Claude Haiku.

## Commands

| Command | What it does |
|---|---|
| `python tasks.py data` | Generate synthetic data, rate documents, ground truth (fixed seed) |
| `python tasks.py test` | Run the test suite |
| `python -m pytest --cov=engine --cov=agents --cov=llm --cov=privacy --cov=governance --cov=workflow --cov=storage --cov=schemas --cov=common` | Tests with line coverage (core packages; `app/`, `data_gen/`, `evals/` not counted) |
| `python -m data_gen.samples` | Print one example of each seeded discrepancy |
| `python -m workflow.pipeline` | End to end: extract rates, six closes, retro, AI investigation, memos (mock AI) |
| `python -m workflow.pipeline --provider anthropic` | Same with the live model (needs your key) |
| `python -m workflow.pipeline --cache-mode replay` | Offline replay of a live run previously recorded on this machine |
| `python -m evals.run_evals --provider anthropic --max-spend 0.20` | Full evals with the live model: record, replay, check identical; writes `evals/results.md` + charts |
| `python tasks.py evals` | Offline evals. With the default mock provider the AI-side numbers come from a deterministic stand-in and the report is labelled **MOCK**; the deterministic numbers (detection, retro, bank, guardrails, privacy) are real |
| `python tasks.py app` | Start the Streamlit app (Analyst view and Behind the scenes) |

## Layout

| Folder | Role |
|---|---|
| `schemas/` | Pydantic models; every field tagged with a privacy class (C1–C4) |
| `storage/` | DuckDB schema and access; `phi.py` is the only reader of names/DOB |
| `privacy/` | Gateway (allow-list, pseudonymise, scan-and-block), masking, redacting logger |
| `llm/` | Provider abstraction (Anthropic, Mock), response cache, cost meter, prompts |
| `engine/` | Deterministic money math (cannot import `llm/` or `storage.phi`) |
| `agents/` | Rate extractor, investigator, memo writer |
| `governance/` | Approvals and audit log |
| `workflow/` | Monthly close orchestrator |
| `app/` | Streamlit UI |
| `evals/` | Evaluation suite against generator ground truth |
