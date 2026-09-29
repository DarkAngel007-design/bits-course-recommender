# BITS Academic Course Recommender

Evidence-backed course recommendations for BITS Pilani students. The supplied PDFs (Academic
Regulations, Bulletin 2025-26, Timetable Sem I 2026-27, 540 course handouts) are preprocessed
offline into a versioned, citable dataset. At request time a deterministic academic engine
works out what the student still needs and is eligible to take, and only then are courses
matched to the student's natural-language request. Every claim on a course card links back to
a document page and excerpt.

Design documents: [PRD](PRD.md) · [Architecture](ARCHITECTURE.md) ·
[Dataset & coverage](docs/dataset.md) · [Evaluation results](docs/evaluation.md) ·
[OpenAPI spec](docs/openapi.json) · [Postman collection](postman/BITS-Course-Recommender.postman_collection.json)

```
PDFs ──► ingestion (manifest → family parsers → validation/review queue → atomic snapshot)
                                                   │ data/published/<snapshot>/*.json
profile ──► scope → attempt history → requirement allocation → eligibility (all offerings)
query ────► intent (rules or Claude, strict schema) → hard filters (unknown ≠ pass) → ranking
         ──► optional section/exam solver → revalidation → cited explanation → dashboard
```

## Quick start

Requirements: Python 3.11+ (3.12 recommended), Node 20+, and optionally
[uv](https://github.com/astral-sh/uv). A published dataset snapshot is committed under
`data/published/`, so the app runs **without** the raw PDFs.

```bash
make setup
```

```bash
make build
```

```bash
make api
```

Open <http://localhost:8000>. Click one of the *synthetic demo profiles* or create your own
profile, then use **Progress**, **Recommendations** and **Semester plan**.

For frontend development with hot reload, run `make api` and `make web` in two terminals and
open <http://localhost:5173>.

Without `make`:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[ingest,llm,dev]" && npm --prefix frontend install
```

```bash
npm --prefix frontend run build && .venv/bin/uvicorn backend.app.api.main:app --port 8000
```

### Docker (PostgreSQL)

```bash
docker compose up --build
```

The compose file runs PostgreSQL 16 for profiles/plans and the app on port 8000. Set
`ADMIN_TOKEN` (required, 16+ characters; compose refuses to start without it) and optionally
`GEMINI_API_KEY` in the environment first:

```bash
export ADMIN_TOKEN=$(openssl rand -hex 16)
```

## Rebuilding the dataset from the PDFs

Place the supplied archive contents in `dataset/` (`dataset/timetable.pdf`,
`dataset/bulletin.pdf`, `dataset/Academic-Regulations-2023.pdf`, `dataset/handouts/*.pdf`).
Raw PDFs are git-ignored.

```bash
make ingest
```

This runs `python -m ingestion build --raw dataset --out data/published`:

1. **Manifest** – every PDF with SHA-256, family, page count (`__MACOSX` ignored);
   identical handouts are extracted once and linked to every file/course code.
2. **Family parsers** – timetable tables (offerings, sections, meetings, exams, cancellations,
   credit framework, cohort restriction, equivalence list), bulletin (1,988 course
   descriptions and printed prerequisites, programme core/elective lists, humanities pool,
   project-course limits, 23 minors, 99 semester charts, category structure), handouts
   (evaluation tables, mid-sem/compre presence, attendance and make-up clauses, topics),
   and 35 curated regulation rules whose excerpts must be found verbatim in the PDFs.
3. **Validation** – evidence references, weights totalling 100%, chart vs list totals,
   unknown codes, conflicts. Everything uncertain goes to the review queue
   (`review_queue.json`) instead of becoming a fact.
4. **Publish** – an immutable directory `data/published/snap-<hash>` written atomically and
   pointed to by `data/published/CURRENT`.

The snapshot id hashes the input files, parser code and rules, so an identical rerun
re-verifies the existing snapshot instead of duplicating it; a new timetable or handout set
produces a new snapshot and older plans are reported as stale. `make verify` re-runs the
publication checks. Maintainers can publish another snapshot from the dashboard
(Data & coverage) or `POST /admin/datasets/{id}/publish`.

## Tests and evaluation

```bash
make test
```

74 tests: golden synthetic profiles (single degree, dual degree, repeats and reports, missing
prerequisite, 2026 cohort, unattested cohort, other campus, incomplete history, minor
exclusion), Hypothesis property tests (no double counting, determinism, status/check
consistency), extraction fixtures (multi-page tables, shared prefixes, cancellations,
equivalence vs shared handouts), NLP hard/soft parsing, and API integration including a
"no hard-rule violation in accepted results" gate over 18 profile × query combinations.

```bash
make e2e
```

Playwright browser tests: profile → progress → recommendation → evidence → plan, honest
empty results, and unsupported cohorts.

```bash
make eval
```

Writes [docs/evaluation.md](docs/evaluation.md): extraction coverage, golden results,
hard-rule violations (0), unresolved citations (0 of 576), latency (p95 ≈ 45 ms in-process)
and hand-labelled ranking relevance.

The Postman collection runs clean with newman (29 requests, 37 assertions) against a local
server:

```bash
npx newman run postman/BITS-Course-Recommender.postman_collection.json
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/app.db` | Profiles, plans, run log. PostgreSQL: `postgresql+psycopg://…` |
| `DATA_DIR` | `data/published` | Snapshot directory |
| `RAW_DIR` | `dataset` | Optional; enables "open PDF page" links on evidence |
| `ADMIN_TOKEN` | random per process | Maintainer endpoints (`X-Admin-Token`). No built-in default: if unset, a random token is generated and printed once in the server log; if set it must be 16+ characters |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | unset | Optional. Enables LLM query understanding via Google Gemini (free key: <https://aistudio.google.com/apikey>) |
| `ANTHROPIC_API_KEY` | unset | Optional alternative provider (Claude) |
| `LLM_PROVIDER` | auto | `gemini`, `anthropic` or `none`. Auto picks Gemini if its key is set, else Anthropic |
| `LLM_MODEL` | `gemini-flash-latest` / `claude-opus-5` | Overrides the provider default model |
| `CORS_ORIGINS` | `*` | Allowed browser origins |

Credentials stay server-side (put them in a git-ignored `.env`; see `.env.example`). Only the query
text and the profile's interests are sent to the LLM, never grades or history. Note that
Gemini's free tier may use submitted content to improve Google products, so don't type anything
sensitive into the query box when using it. Without a key (or when the call fails) the deterministic parser
is used and the UI says so; profile, progress, structured filters and plans are unaffected.

## How recommendations are decided

* **Scope first.** Pilani campus, 2025 admissions, Bulletin 2025-26 (unit framework) is
  resolved from the documents. Other pre-2026 cohorts can attest that the chart applies
  (results are labelled provisional). 2026 admissions (credit-hour framework), other campuses
  and unknown programme combinations are reported as unsupported, never guessed.
* **Requirements.** Named GIR and CDC courses are matched first (dual-degree shared CDCs count
  for both degrees, as the bulletin allows); electives are allocated by exhaustive search that
  minimises the remaining DEL/HUEL deficit, with project-course limits and overflow to open
  electives. In-progress courses are projected, never earned. Latest attempt governs; reports
  (NC, W, RC…) don't clear a course.
* **Eligibility.** Each offering gets `eligible`, `ineligible` or `needs_verification` from
  explicit checks: availability (cancelled components), cohort/credit framework, already
  cleared or listed equivalent, printed prerequisites (in-progress courses don't count),
  prior-preparation rules, other-discipline and higher-degree rules, approvals.
* **Preferences.** Hard filters (e.g. "no midsem", "no attendance requirement") require
  explicit evidence; unknown facts put a course in the separate *Needs verification* list.
  "No midsem" is accepted only from a handout whose evaluation table totals 100% without one;
  "no attendance requirement" only from an explicit statement. Subjective words ("lenient",
  "easy") become transparent soft rankings or are reported as unsupported.
* **Empty results** name the binding filters; nothing is silently relaxed.

See [docs/dataset.md](docs/dataset.md) for the data contract, coverage numbers, known
limitations and deviations from the architecture baseline.

## Repository layout

```
backend/app/        api (FastAPI), catalog (snapshot store), profiles (SQLAlchemy),
                    academics (scope, history, requirements, eligibility, plan),
                    retrieval (properties, BM25/topics), recommendations (intent, engine),
                    scheduling (section/exam solver)
ingestion/          manifest, pdf cache, extractors/{timetable,bulletin,handouts,rules},
                    curricula, topics, checks, rules/academic_rules.yaml, CLI
frontend/           React + TypeScript dashboard (Vite), Playwright tests in e2e/
data/published/     committed dataset snapshot (JSON)
schemas/            JSON Schemas for the snapshot files (versioned)
tests/              unit, integration, evaluation, synthetic golden fixtures
docs/  postman/     evaluation report, dataset notes, OpenAPI, Postman collection
```
