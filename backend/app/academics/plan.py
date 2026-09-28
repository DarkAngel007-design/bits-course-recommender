"""Whole-plan validation (FR-13): joint rules that only appear once courses are combined."""
from __future__ import annotations

from ..catalog.store import Snapshot
from ..scheduling.solver import Prefs, solve
from .eligibility import evaluate_offering
from .history import History
from .models import StudentProfile
from .requirements import compute_requirements

PS_CODES = {"BITS F221", "BITS F412"}


def validate_plan(offering_ids: list[str], profile: StudentProfile, snap: Snapshot, scope: dict, history: History,
                  prefs: Prefs | None = None, locked: dict[str, list[str]] | None = None) -> dict:
    checks: list[dict] = []
    unknown_ids = [o for o in offering_ids if o not in snap.offerings]
    offerings = [snap.offerings[o] for o in offering_ids if o in snap.offerings]
    if unknown_ids:
        checks.append({"id": "known_offerings", "status": "fail", "message": f"Unknown offering ids: {unknown_ids}",
                       "evidence": []})
    req = compute_requirements(profile, snap, scope, history)
    req = req if req.get("available") else None
    per_course = [evaluate_offering(o, profile, snap, scope, history, req) for o in offerings]
    bad = [p for p in per_course if p["status"] == "ineligible"]
    unverified = [p for p in per_course if p["status"] == "needs_verification"]
    checks.append({"id": "individual_eligibility", "status": "fail" if bad else ("unknown" if unverified else "pass"),
                   "message": ("Ineligible: " + ", ".join(p["course_code"] for p in bad)) if bad else
                   ("Needs verification: " + ", ".join(p["course_code"] for p in unverified)) if unverified else
                   "Every course is individually eligible.", "evidence": []})

    codes = [o["course_code"] for o in offerings]
    dup = sorted({c for c in codes if codes.count(c) > 1})
    eq_pairs = sorted({tuple(sorted((a, b))) for a in codes for b in codes if a != b and b in snap.equivalents.get(a, ())})
    if dup or eq_pairs:
        checks.append({"id": "no_duplicates", "status": "fail",
                       "message": "Same or listed-equivalent courses together: " +
                       ", ".join(dup + [f"{a} = {b}" for a, b in eq_pairs]),
                       "evidence": [e for a, b in eq_pairs for e in snap.equivalence_evidence.get((a, b), [])]})
    else:
        checks.append({"id": "no_duplicates", "status": "pass", "message": "No duplicate or equivalent courses.",
                       "evidence": []})

    systems = {o["credit"]["system"] for o in offerings}
    rule = snap.rule("max_units_per_semester")
    if len(systems) > 1:
        checks.append({"id": "credit_framework", "status": "fail", "message": "Plan mixes unit and credit-hour "
                       "offerings; totals cannot be combined without a documented conversion.", "evidence": []})
    total = sum(o["credit"]["value"] or 0 for o in offerings)
    if systems == {"units"} or not systems:
        mx = rule["params"]["max_units"] if rule else 25
        checks.append({"id": "max_units", "status": "pass" if total <= mx else "fail",
                       "message": f"Total load {total} units (maximum {mx} for first degree).",
                       "evidence": snap.rule_evidence("max_units_per_semester")})
    else:
        checks.append({"id": "max_units", "status": "unknown", "message": f"Total {total} credit hours; no credit-hour "
                       "load limit in the supplied documents.", "evidence": []})

    hd = [c for c in codes if c.split(" ")[1].startswith("G")]
    checks.append({"id": "hd_limit", "status": "fail" if len(hd) > 1 else "pass",
                   "message": f"{len(hd)} higher degree course(s); at most one per semester." if hd else
                   "No higher degree course.", "evidence": snap.rule_evidence("higher_degree_course_limit")})
    ps = [c for c in codes if c in PS_CODES]
    if ps and len(codes) > len(ps):
        checks.append({"id": "practice_school_exclusive", "status": "fail",
                       "message": "Practice School cannot be combined with other courses.",
                       "evidence": snap.rule_evidence("practice_school_exclusive")})
    # electives beyond requirement
    if req:
        need = sum((c["remaining_after_projected"]["courses"] or 0) for c in req["categories"]
                   if c["category"] in ("DEL", "HUEL", "OPEL"))
        electives = [p for p in per_course if p["contribution"] and p["contribution"][0]["kind"] == "elective"]
        extra = max(0, len(electives) - need)
        mx = (snap.rule("extra_electives_limit") or {"params": {"max_extra_electives": 4}})["params"]["max_extra_electives"]
        checks.append({"id": "extra_electives", "status": "pass" if extra <= mx else "fail",
                       "message": f"{len(electives)} elective(s) in plan, {need} still required; {extra} beyond the "
                                  f"requirement (limit {mx} over the whole programme).",
                       "evidence": snap.rule_evidence("extra_electives_limit")})

    sched = solve(offerings, prefs, locked) if offerings else {"status": "feasible", "assignment": {}, "unresolved": [],
                                                               "exam_clashes": []}
    sched_status = sched["status"]
    if sched_status == "feasible" and sched["unresolved"]:
        sched_status = "unresolved"
    checks.append({"id": "timetable", "status": {"feasible": "pass", "infeasible": "fail"}.get(sched_status, "unknown"),
                   "message": {"feasible": "A clash-free section assignment exists.",
                               "infeasible": "No clash-free assignment: " + (sched.get("reason") or ""),
                               "unknown": "Search budget exhausted - feasibility not proven either way.",
                               "unresolved": "Clash-free for checked data; some meetings/exams are undefined or TBA."
                               }[sched_status], "evidence": snap.rule_evidence("timetable_conflict_free")
                   + snap.rule_evidence("compre_no_clash")})
    st = [c["status"] for c in checks]
    return {
        "valid": False if "fail" in st else (None if "unknown" in st else True),
        "status": "invalid" if "fail" in st else ("needs_verification" if "unknown" in st else "valid"),
        "checks": checks,
        "courses": per_course,
        "total": {"value": total, "system": next(iter(systems)) if len(systems) == 1 else "mixed"},
        "schedule": sched,
    }
