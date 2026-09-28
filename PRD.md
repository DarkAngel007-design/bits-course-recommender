# BITS Academic Course Recommender — Product Requirements

Version: 1.0 | Date: 2026-09-28 | Status: implementation baseline

Companion: [Architecture](ARCHITECTURE.md)

## 1. Product purpose

Help a BITS student choose academically valid courses for a semester based on remaining degree requirements, academic history and natural-language preferences. Every recommendation must be grounded in the supplied academic documents and processed data.

The product must first establish what the student needs and is eligible to take, then match preferences. It must explain uncertainty instead of guessing.

## 2. Users and core jobs

| User | Job |
|---|---|
| Student | Maintain academic history, understand remaining requirements, find suitable courses and assemble a semester plan |
| Dataset maintainer | Inspect extraction failures, review academic facts and publish new semester data |
| Evaluator | Reproduce preprocessing, inspect sources and verify that recommendations are calculated live |

## 3. Scope

### Required release

- Reproducible PDF preprocessing into structured records with provenance.
- Student profile creation and editing.
- Deterministic requirement and eligibility calculations.
- Natural-language preference parsing and structured retrieval.
- Concise, evidence-backed recommendations with explicit uncertainty.
- Working dashboard, clean repository and setup instructions.
- Profile-aware category allocation, including supported dual-degree and minor rules.
- Visible coverage boundaries for unsupported campuses, cohorts and curricula.

### Optional advanced release

- Class/tutorial/lab and examination clash checking.
- Alternate section selection and feasible semester-plan generation.
- Schedule preferences such as no early classes, free weekdays and compact days.

### Non-goals

- Official course registration, seat reservation or guaranteed admission.
- Invented prerequisites, grade predictions, instructor difficulty ratings or attendance claims.
- Universal campus/cohort coverage unsupported by the supplied documents.
- Automatic approval of academic exceptions.
- Multi-semester degree optimization in the first release.

## 4. Evidence and assumptions

The supplied archive contains 543 PDFs: 540 handouts plus regulations, bulletin and timetable. It does not contain a separate student-profile file. Current timetable coverage is Pilani Semester I 2026–27.

The timetable contains a new credit-hour framework and cohort restrictions for 2026 admissions. Requirements for those students must not be inferred from older unit-based curricula without applicable evidence.

The timetable prerequisite section refers to an external portal. Until authoritative rules are available and reviewed, affected eligibility must remain unresolved. External supplementation, if later used, must be identified by source and version; supplied data remains primary.

These are product constraints, not reasons to hide uncertainty or silently reduce the declared supported scope.

## 5. Primary user journey

1. Student enters campus, admission year, programme(s), semester, minor and interests.
2. Student adds completed attempts, grades/reports where needed, and current courses.
3. Dashboard shows verified scope, missing information and academic progress.
4. Student asks a query such as “Suggest DELs related to AI.”
5. System displays the interpreted filters and preferences.
6. System computes eligible offered courses, filters, ranks and validates them.
7. Student reads requirement contribution, eligibility, matching properties and source evidence.
8. Student adds courses to a plan; the combined plan is revalidated.

Changing a profile or dataset invalidates dependent results. In-progress courses appear as projected progress, never earned completion.

## 6. Functional requirements and acceptance criteria

| ID | Requirement | Acceptance criterion |
|---|---|---|
| FR-01 | Reproducible ingestion | A documented command rebuilds structured data from supplied files; identical reruns do not duplicate records |
| FR-02 | Source traceability | Every decision-bearing rule/property links to a document page and supporting excerpt |
| FR-03 | Extraction review | Missing, conflicting or unreliable facts enter a review queue and cannot silently become verified facts |
| FR-04 | Student profiles | Users can create/edit all brief-required fields and persist course-attempt history |
| FR-05 | Scope resolution | System selects applicable curriculum by campus/programme/cohort or explicitly reports unresolved scope |
| FR-06 | Requirements | Dashboard calculates remaining CDC/DEL/HUEL/OPEL counts and credits where supported, plus named-course obligations |
| FR-07 | Academic history | Current courses, completed attempts, repeats and documented equivalents are treated distinctly |
| FR-08 | Eligibility | Every candidate is eligible, ineligible or needs verification, with structured reasons |
| FR-09 | Query interpretation | Natural-language intent becomes editable hard filters and soft preferences; material ambiguity triggers clarification |
| FR-10 | Preference matching | Verified structured properties and semantic topics determine matches only after academic filtering |
| FR-11 | Explanations | Each recommendation states requirement contribution, eligibility, relevant properties, match reason and evidence |
| FR-12 | No-match behavior | Empty results identify binding filters and offer clearly labelled alternatives without silent relaxation |
| FR-13 | Plan validation | Combining individually eligible courses triggers checks for applicable total-load and joint restrictions |
| FR-14 | Data refresh | A new semester snapshot can be published without changing recommendation logic; old plans are marked stale |
| FR-15 | Degraded operation | LLM failure leaves profile/progress and structured-filter workflows usable |
| FR-16 | Optional scheduling | Feasible alternate sections are considered before rejecting a course for clashes |

## 7. Recommendation contract

Each course card contains:

- Course code, title, term and credit value with its credit system.
- Requirement it can satisfy for this student.
- Eligibility status and the decisive checks.
- Matched hard filters and soft preferences.
- Relevant evaluation, attendance or makeup facts when requested.
- Unknown/conflicting properties and approvals still needed.
- Clickable source evidence.
- Schedule feasibility status: checked, unchecked or unresolved.

Unverified candidates must be visually separated from verified recommendations. Do not use a confidence percentage as a substitute for eligibility evidence.

## 8. Query semantics

| Query | Expected interpretation |
|---|---|
| “Suggest DELs related to AI” | Student-specific DEL membership and eligibility are hard conditions; AI relevance drives ranking |
| “An OPEL with no attendance requirement” | Require evidence for the precise attendance property; missing policy is not a match |
| “No midsem and lenient makeup” | Verified no-midsem condition; clarify or explain makeup conditions without inventing leniency |
| “A HUEL, preferably project-based” | HUEL is a hard condition; supported project evaluation is a soft preference |
| “No 8 AM classes” | When scheduling is enabled, apply to the full required section bundle |

Examples are test intents, not promises that matching courses exist.

## 9. Dashboard requirements

### Profile

Provide structured fields, searchable course entry and validation of unknown codes. Capture grades/reports and academic approvals when required by applicable rules. Show how missing information affects eligibility.

### Progress

Show completed, in-progress and remaining counts/credits. Keep credit frameworks separate. Explain course-to-requirement allocation and distinguish graduation requirements from current-semester obligations.

### Recommendations

Provide a query box, editable interpreted constraints, concise course cards and evidence drawers. Include loading, no-match, partial-coverage and service-error states.

### Plan

Allow add/remove, show total load and validation reasons, and save the plan with profile/data versions. Optional timetable view shows sections, exams and conflicts.

### Review administration

Show ingestion status, source coverage, ambiguous mappings, conflicts and validation failures. Publishing a dataset requires an authorized maintainer and successful publication checks.

## 10. Important edge cases

| Case | Required behavior |
|---|---|
| Identical handout under multiple codes | Share extracted document facts without inventing equivalence |
| Same code under different computer codes/frameworks | Preserve distinct offerings and cohort scope |
| Course appears in bulletin but not current timetable | Do not present it as currently offered |
| Cancelled lecture with remaining practical rows | Validate complete offering viability; do not infer availability from one row |
| Missing prerequisites or conflicting sources | Needs verification |
| Current prerequisite course | Do not count as cleared unless an applicable concurrency rule permits it |
| Repeat with changed result | Apply documented attempt semantics |
| Dual degree or minor overlap | Apply documented allocation and prevent unauthorized double counting |
| Attendance policy absent | State unknown; do not claim no attendance requirement |
| Exam date TBA | Schedule status remains incomplete |
| Individually valid courses violate a joint rule | Reject the combined plan and explain the rule |
| Unsupported campus/cohort | Display scope limitation and request necessary evidence |
| No eligible match | Return an honest empty result and explicit alternatives |

## 11. Quality and security requirements

- Deterministic academic results for the same profile, rules and dataset versions.
- No known hard-rule violations in accepted results across the release test set.
- Every returned academic or policy claim traceable to evidence or a deterministic calculation over cited facts.
- No raw-PDF parsing during ordinary recommendation requests.
- Versioned snapshots and reproducible ingestion.
- Profile ownership checks; administrative actions restricted to maintainers.
- Credentials server-side; minimal student data sent to the LLM.
- Accessible forms and keyboard-operable core flows.
- Clear timeouts and error states; no solver timeout labelled as proven infeasibility.

Proposed performance targets, to validate rather than assume: p95 under 2 seconds for cached academic analysis and under 10 seconds for a recommendation on the reference dataset, excluding cold start. Give optional schedule solving a bounded execution budget and report its actual outcome.

## 12. Validation and release gates

Create manually verified profiles and expected outcomes covering a supported single-degree curriculum, dual-degree allocation, repeats, missing prerequisites, cohort restrictions and incomplete history. Label synthetic profiles clearly.

Required tests:

1. Extraction fixtures for multi-page tables, course aliases, assessment tables and cancellations.
2. Unit/property tests for counts, credits, allocation, applicability and eligibility.
3. Integration tests from published records through API responses.
4. NLP tests for hard/soft preference parsing and unsupported claims.
5. Browser tests for profile creation, progress calculation, query, evidence and plan changes.
6. Optional solver tests for alternate sections, labs, exams, infeasibility and timeouts.

Release gates:

- All brief-required dashboard flows operate on processed data, not fixed answers.
- Supported scope is explicitly listed and backed by reviewed rules.
- Golden-profile expected academic results pass completely.
- Unknown facts never satisfy hard filters in the test set.
- Every displayed source reference resolves to the correct evidence.
- Re-ingestion is idempotent and new snapshots invalidate stale plans.
- README setup works from a clean environment.

Report extraction correctness and coverage separately, plus eligibility precision, ranking relevance, citation correctness, latency and failure rates. A product that abstains on every candidate does not meet usefulness goals.

## 13. Delivery milestones

| Milestone | Exit condition |
|---|---|
| M1 — Data contract | Inventory, schema and supported-scope matrix agreed; missing evidence listed |
| M2 — Processed dataset | Family-specific parsers, review queue, provenance and snapshot publication work |
| M3 — Academic engine | Golden-profile requirement and eligibility tests pass |
| M4 — Recommendations | Structured queries produce validated, cited results and honest no-match behavior |
| M5 — Conversational dashboard | Profile-to-query workflow works end to end with editable intent |
| M6 — Optional timetable | Section/exam feasibility and alternatives validated |
| M7 — Submission | Repository, setup guide, preprocessing instructions, tests, demo profiles and Postman collection complete |

Start with a fully verified vertical slice for one supported curriculum, then expand coverage. Do not describe that slice as full institutional support. Do not postpone provenance or uncertainty handling; both belong in the first slice.

## 14. Final deliverables

1. Working dashboard with live profile-based requirements and recommendations.
2. Git repository containing application, preprocessing and retrieval code.
3. README with setup, ingestion, run and test instructions.
4. Versioned schema and dataset manifest with a coverage/limitations statement.
5. Golden test profiles and evaluation results.
6. API specification and Postman collection.
7. Optional timetable intelligence demonstrated separately from core academic validity.

## 15. Open dependencies

- Obtain or identify complete authoritative prerequisite/restriction data where the supplied documents are insufficient.
- Verify curriculum applicability for the new 2026 credit-hour framework.
- Establish supported programme/cohort coverage from reviewed evidence before advertising it.
- Select an available LLM/embedding provider and configure server-side credentials during implementation.

These dependencies do not block schema, ingestion or interface work. They do block unsupported claims of academic eligibility or complete curriculum coverage.
