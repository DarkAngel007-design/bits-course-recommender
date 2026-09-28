# Dataset, coverage and limitations

## Snapshot contract (schema version 1.0)

A snapshot is an immutable directory `data/published/snap-<hash>/`. JSON Schemas for every
file are in [`schemas/snapshot-1.0/`](../schemas/snapshot-1.0). All decision-bearing records
carry `evidence` ids that resolve in `evidence.json` to `{file, family, pdf_page,
printed_page, excerpt, parser, confidence}`.

| File | Contents |
|---|---|
| `manifest.json` | Snapshot id, schema/parser/rules versions, term, every input file with SHA-256, duplicate-handout groups |
| `offerings.json` | Offering id `<term>:<computer code>`: course code, credit `{L,P,T,S,value,system}`, cohort scope, sections (type, label, instructors, room, meetings with slot verification status, cancelled/active), exams (date, session, status), offering status |
| `courses.json` | Catalogue keyed by code: title (with source), L-P-U, bulletin description, prerequisite `{stated, text, expression, status}`, handout ids, offering ids, topic tags with the keywords that produced them |
| `handouts.json` | One record per distinct handout content: all files and course codes that share it, evaluation components and completeness, mid-sem/compre presence (`stated`, `explicit_from_complete_table`, `unknown`), attendance and make-up clauses, NC policy, syllabus text |
| `programmes.json` / `curricula.json` | Programme core and elective lists; single and dual-degree curricula with named courses per semester, category requirements and their provenance (`stated`, `derived` with formula, `missing`) |
| `minors.json`, `humanities_pool.json`, `project_rules.json`, `structure.json` | Minor rules and pools (incl. exclusion clauses), humanities pool, project-course limits, category minimums |
| `rules.json` | 35 typed regulation rules; each excerpt was located verbatim in the PDF text during ingestion |
| `relationships.json` | Listed equivalences only; shared handouts are recorded separately and never treated as equivalence |
| `review_queue.json` | Missing, conflicting, unreliable or unparsed facts for maintainer review |
| `coverage.json` | Coverage statistics and the supported-scope statement shown in the dashboard |

Tri-state facts: `true` needs explicit evidence, `false` needs explicit evidence (e.g. a
complete evaluation table without a mid-sem), and a missing value is `null`/`unknown`.
Credit values are stored with their system (`units` or `credit_hours`) and never converted.

## Coverage (current snapshot)

See [evaluation.md](evaluation.md) for live numbers. In summary: 728 timetable offerings
(600 offered, 128 with a required component fully cancelled), 399 distinct handouts from 540
files (83% with complete evaluation tables; one scanned handout has no text layer and is in
the review queue), 1,988 bulletin course descriptions, 27 single-degree and 72 dual-degree
curricula, 23 minors, 35/35 rules validated.

## Supported scope

* Pilani campus, First Semester 2026-27 timetable.
* Bulletin 2025-26 curricula (unit framework), resolved for the 2025 admission cohort.
  Students admitted earlier may attest that the chart applies; their results are labelled
  provisional.
* First-degree requirements (GIR, CDC, DEL, HUEL, OPEL), dual-degree allocation, minors.

Not supported, and reported as such: other campuses; 2026 admissions (credit-hour framework,
no curriculum supplied); higher-degree/PhD requirements; 2+2 international programmes.

## Known limitations and interpretations

| Topic | Handling |
|---|---|
| Prerequisites | The timetable defers the authoritative list to an external portal that was not supplied. Prerequisites printed in bulletin course descriptions are evaluated (64 parsed; 17 with qualifiers or no codes are `needs_verification`). Courses with none printed pass with a visible caveat; **strict mode** turns that into `needs_verification`. |
| Cohort applicability | The bulletin edition is 2025-26 and states no cohort; only the 2025 cohort is resolved. |
| Open-elective minimum | Programme-specific value derived as max(category minimum, coursework minimum − GIR − HUEL − DC − DEL), with the formula shown. Dual degrees meet OPEL by rule. |
| Missing DEL totals | Four M.Sc. charts (Biological Sciences, Physics, Physics – Space Science, Semiconductor & Nanoscience) give no DEL totals; those DEL requirements are `unresolved` rather than guessed. |
| Course-code mismatches | e.g. Biotechnology charts use `BIOT` codes while the timetable offers `BIO` codes. No equivalence is invented; the review queue lists them. |
| Timetable slots | Hours outside the legend (1–10) are kept but marked `undefined_slot`; such sections make schedule checks `unresolved`. |
| Higher-degree courses | The CGPA threshold is "prescribed by AGC" and not supplied, so HD courses are `needs_verification` unless the student records permission. |
| "No attendance requirement" | Matches only an explicit handout statement (e.g. "Attendance is not mandatory"). A handout without an attendance clause is shown as unverified: the Regulations set no institute minimum, but Handout Part I says attendance is recorded. |
| "Lenient make-up" | Subjective. Ranked by a transparent reading of the make-up clause (components covered minus restrictions) and a clarification is shown. |
| OCR | No OCR engine is bundled; the one scanned handout stays in the review queue. |

## Deviations from the architecture baseline

| Baseline | Implementation | Reason |
|---|---|---|
| PostgreSQL for all data | Academic data are immutable JSON snapshots loaded in memory; PostgreSQL (compose) or SQLite (default) store profiles, plans and runs | Snapshots are read-only, versioned and small (~15 MB); this keeps publication atomic and the app runnable without a database server. `DATABASE_URL` switches to PostgreSQL. |
| Embeddings for semantic search | BM25 over course text plus a curated topic taxonomy whose keyword hits are shown as the match reason; optional Claude intent parsing maps free text to topics | No embedding provider was configured. Every candidate is scored after academic filtering, so no global top-k cut-off hides valid courses. |
| OR-Tools CP-SAT | Exact depth-first branch-and-bound over section bundles with node/time budgets and `feasible` / `infeasible` / `unknown` outcomes | Problem sizes are small (a handful of courses, few sections each); avoids a heavy native dependency. A budget timeout is reported as `unknown`, never as infeasible. |
| Authentication | Per-profile owner token plus a maintainer token | Sufficient for a local/evaluation deployment; there is no campus SSO in scope. |
