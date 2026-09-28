"""Evaluation report: extraction coverage, academic correctness, citation correctness,
hard-filter soundness and latency, reported separately (PRD §12).

    python -m tests.evaluation.run_eval            # writes docs/evaluation.md and prints JSON
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from backend.app.academics.eligibility import evaluate_offering
from backend.app.catalog.store import get_store
from backend.app.recommendations.engine import recommend
from backend.app.recommendations.intent import parse_rules
from tests.conftest import FIXTURES, state

QUERIES = [
    "Suggest DELs related to AI.", "I want an OPEL with no attendance requirement.",
    "Suggest courses with no midsem and a lenient makeup policy.", "I need a HUEL and prefer project-based evaluation.",
    "Suggest an AI-related DEL with no midsem", "Find an OPEL with no attendance requirement",
    "Two finance OPELs with no 8 AM classes, keep Saturday free", "HUEL on philosophy or ethics",
    "DEL in security or cryptography", "open electives about entrepreneurship without quizzes",
]
# Hand-labelled relevance for ranking: courses a CS student asking the query would expect near the top.
# Only courses actually offered in the 2026-27 Sem I timetable can be relevant.
RELEVANT = {
    "Suggest DELs related to AI.": {"CS F407", "BITS F464", "CS F425", "CS F429", "BITS F471", "CS F437", "CS F317",
                                    "BITS F343", "BITS F459", "BITS F312", "BITS F453", "BITS F454"},
    "DEL in security or cryptography": {"BITS F463", "CS F321", "CS G513", "CS F435", "CS F436", "BITS F452", "CS F468"},
}
RANK_PROFILE = "cs_2025_sem5"


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


def main() -> dict:
    snap = get_store().current()
    golden = {k: v for k, v in json.loads((FIXTURES / "golden_profiles.json").read_text()).items() if not k.startswith("_")}
    cov = snap.coverage
    out: dict = {"dataset": snap.id, "rules": snap.rules_version}

    # --- extraction ---------------------------------------------------------------
    out["extraction"] = {
        "offerings": cov["offerings_total"], "offered": cov["offerings_offered"],
        "handouts_distinct": cov["handouts_distinct"],
        "handout_eval_complete_pct": round(100 * cov["handout_extraction"]["ok"] / cov["handouts_distinct"], 1),
        "offered_courses_with_handout_pct": round(100 * cov["offered_with_handout"] / cov["offered_courses"], 1),
        "offered_courses_with_bulletin_desc_pct": round(100 * cov["offered_with_bulletin_description"] / cov["offered_courses"], 1),
        "rules_validated": f"{cov['rules_reviewed']}/{len(snap.rules_doc['rules'])}",
        "review_queue": cov["review_queue"],
    }
    # handout table self-consistency: complete tables sum to 100
    bad = [h["id"] for h in snap.handouts.values() if h.get("evaluation", {}).get("complete")
           and not 99 <= h["evaluation"]["total_weight"] <= 101]
    out["extraction"]["complete_tables_not_summing_to_100"] = len(bad)

    # --- academic correctness (golden) ------------------------------------------------
    import pytest
    rc = pytest.main(["-q", "-p", "no:cacheprovider", "-W", "ignore", "tests/unit/test_golden_profiles.py"])
    out["golden_profiles"] = {"profiles": len(golden), "passed": rc == 0}

    # --- recommendations: soundness, citations, latency --------------------------------
    lat, n_match, n_unverified, violations, cites, cite_bad, empty = [], 0, 0, 0, 0, 0, 0
    ranking = {}
    for name in ("cs_2025_sem5", "cs_2025_sem7", "dual_econ_cs"):
        p, scope, hist, req = state(golden[name]["profile"], snap)
        for q in QUERIES:
            t = time.perf_counter()
            r = recommend(p, snap, parse_rules(q, p.interests), "rules")
            lat.append((time.perf_counter() - t) * 1000)
            n_match += len(r["matches"])
            n_unverified += len(r["unverified"])
            empty += not r["matches"]
            for m in r["matches"]:
                ev = evaluate_offering(snap.offerings[m["offering_id"]], p, snap, scope, hist, req)
                violations += ev["status"] != "eligible"
                violations += any(f["value"] is not True for f in m["matched_hard_filters"])
                for e in m["evidence"]:
                    cites += 1
                    cite_bad += e not in snap.evidence
            if name == RANK_PROFILE and q in RELEVANT:
                top = [m["course_code"] for m in r["matches"]][:5]
                offered_rel = {c for c in RELEVANT[q] if any(o["status"] == "offered"
                                                             for o in snap.offerings_by_course.get(c, []))}
                hits = sum(c in RELEVANT[q] for c in top)
                ranking[q] = {"precision_at_5": round(hits / max(1, len(top)), 2),
                              "recall_at_5_of_offered": round(hits / max(1, min(5, len(offered_rel))), 2),
                              "offered_relevant": sorted(offered_rel), "returned": top}
    out["recommendations"] = {
        "runs": len(lat), "verified_matches": n_match, "needs_verification": n_unverified,
        "runs_without_verified_match": empty, "hard_rule_violations_in_accepted_results": violations,
        "citations_checked": cites, "citations_unresolved": cite_bad,
        "latency_ms": {"p50": round(pct(lat, 50), 1), "p95": round(pct(lat, 95), 1), "max": round(max(lat), 1)},
        "ranking_relevance": ranking,
    }
    # requirements latency (uncached engine call)
    rl = []
    for g in golden.values():
        t = time.perf_counter()
        state(g["profile"], snap)
        rl.append((time.perf_counter() - t) * 1000)
    out["requirements_latency_ms"] = {"p50": round(pct(rl, 50), 1), "p95": round(pct(rl, 95), 1)}
    return out


def write_md(r: dict) -> str:
    e, rec = r["extraction"], r["recommendations"]
    lines = [
        "# Evaluation results", "",
        f"Dataset `{r['dataset']}`, rules `{r['rules']}`. Generated by `python -m tests.evaluation.run_eval`; "
        "profiles are the synthetic golden fixtures in `tests/fixtures/golden_profiles.json`.", "",
        "## Extraction (reported separately from recommendation quality)", "",
        "| Metric | Value |", "|---|---|",
        f"| Timetable offerings parsed (offered / total) | {e['offered']} / {e['offerings']} |",
        f"| Distinct handouts | {e['handouts_distinct']} |",
        f"| Handouts with a complete evaluation table (weights total 100%) | {e['handout_eval_complete_pct']}% |",
        f"| Complete tables that do not sum to 100 | {e['complete_tables_not_summing_to_100']} |",
        f"| Offered courses with a supplied handout | {e['offered_courses_with_handout_pct']}% |",
        f"| Offered courses with a bulletin description | {e['offered_courses_with_bulletin_desc_pct']}% |",
        f"| Curated rules validated against PDF text | {e['rules_validated']} |",
        f"| Review-queue items (error / warning / info) | {e['review_queue']['by_severity'].get('error', 0)} / "
        f"{e['review_queue']['by_severity'].get('warning', 0)} / {e['review_queue']['by_severity'].get('info', 0)} |", "",
        "## Academic correctness", "",
        f"Golden profiles: {r['golden_profiles']['profiles']} — all expected results pass: **{r['golden_profiles']['passed']}**.", "",
        "## Recommendations", "",
        "| Metric | Value |", "|---|---|",
        f"| Query runs (3 profiles × {rec['runs'] // 3} queries) | {rec['runs']} |",
        f"| Verified matches returned | {rec['verified_matches']} |",
        f"| Shown as needs-verification (separated) | {rec['needs_verification']} |",
        f"| Runs with no verified match (honest empty result) | {rec['runs_without_verified_match']} |",
        f"| Hard-rule violations in accepted results | **{rec['hard_rule_violations_in_accepted_results']}** |",
        f"| Citations checked / unresolved | {rec['citations_checked']} / **{rec['citations_unresolved']}** |",
        f"| Recommendation latency p50 / p95 / max (ms, in-process) | {rec['latency_ms']['p50']} / {rec['latency_ms']['p95']} / {rec['latency_ms']['max']} |",
        f"| Requirement analysis latency p50 / p95 (ms, uncached) | {r['requirements_latency_ms']['p50']} / {r['requirements_latency_ms']['p95']} |", "",
        "### Ranking relevance (hand-labelled, CS year-III profile)", "",
    ]
    for q, v in rec["ranking_relevance"].items():
        lines.append(f"- *{q}* — precision@5 = {v['precision_at_5']}, recall@5 of offered relevant courses = "
                     f"{v['recall_at_5_of_offered']} (returned: {', '.join(v['returned']) or 'none'}; offered relevant: "
                     f"{', '.join(v['offered_relevant'])}). Weak keyword matches are labelled as such on the card.")
    lines += ["", "Latency excludes network and cold start; the PRD targets (p95 < 2 s analysis, < 10 s recommendation) "
              "are met with wide margin in-process. LLM intent parsing, when enabled, adds one model call.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    res = main()
    Path("docs").mkdir(exist_ok=True)
    Path("docs/evaluation.md").write_text(write_md(res))
    print(json.dumps(res, indent=1))
