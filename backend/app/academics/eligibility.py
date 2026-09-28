"""Offering eligibility (FR-08): eligible | ineligible | needs_verification with reason codes.

Every check returns {id, status: pass|fail|unknown|not_applicable, message, evidence}.
  any fail              -> ineligible
  else any unknown      -> needs_verification
  else                  -> eligible
Unknown never becomes pass: missing facts, unresolved applicability or approvals needed
keep a course out of the verified list.
"""
from __future__ import annotations

from ..catalog.store import Snapshot
from .history import History
from .models import StudentProfile, sem_index
from .requirements import elective_candidates, minor_exclusion, own_context, project_kind


def _check(cid: str, status: str, message: str, evidence: list[str] | None = None, **extra) -> dict:
    return {"id": cid, "status": status, "message": message, "evidence": evidence or [], **extra}


def eval_prereq_expr(expr: dict, history: History) -> tuple[bool, list[str], list[str]]:
    """Return (satisfied, satisfied_by, missing). In-progress courses do not count (no
    concurrency rule in the supplied documents)."""
    if "course" in expr:
        c = expr["course"]
        ok = history.is_cleared(c)
        return ok, ([history.cleared_via(c) or c] if ok else []), ([] if ok else [c])
    results = [eval_prereq_expr(i, history) for i in expr["items"]]
    if expr["op"] == "any":
        ok = any(r[0] for r in results)
        return ok, [x for r in results if r[0] for x in r[1]], ([] if ok else [x for r in results for x in r[2]])
    ok = all(r[0] for r in results)
    return ok, [x for r in results for x in r[1]], [x for r in results for x in r[2]]


def contribution(code: str, profile: StudentProfile, snap: Snapshot, cur: dict | None, req: dict | None,
                 ctx: dict | None) -> list[dict]:
    """Which requirement(s) the course could count toward for this student, in allocation order."""
    if not cur:
        return []
    out = []
    rem = {c["key"]: c for c in (req or {}).get("categories", [])}
    for rq in cur["requirements"]:
        if rq["kind"] == "named":
            for item in rq["courses"]:
                if code in item["codes"]:
                    key = rq["category"] + (f":{rq['degree']}" if rq.get("degree") else "")
                    out.append({"category": rq["category"], "key": key, "kind": "named",
                                "label": ("CDC" if rq["category"] == "DC" else "GIR") +
                                         (f" ({snap.programmes[rq['degree']]['name']})" if rq.get("degree") else ""),
                                "remaining_need": True, "evidence": rq.get("evidence", [])[:2]})
    if out:
        return out
    level = code.split(" ")[1][0]
    for k in elective_candidates(code, cur, snap, ctx):
        key = k if k != "HUEL" else "HUEL"
        r = rem.get(key)
        need = bool(r and r["status"] not in ("met", "projected_met", "met_by_rule") and
                    ((r["remaining_after_projected"]["courses"] or 0) > 0 or (r["remaining_after_projected"]["units"] or 0) > 0))
        cat = "DEL" if k.startswith("DEL") else "HUEL"
        ev = []
        if cat == "DEL":
            deg = k.split(":", 1)[1]
            ev = snap.programmes[deg]["course_evidence"].get(code, [])
            label = "DEL" + (f" ({snap.programmes[deg]['name']})" if cur["type"] == "dual" else "")
        else:
            ev = snap.humanities_evidence.get(code, [])
            label = "HUEL"
        out.append({"category": cat, "key": key, "kind": "elective", "label": label, "remaining_need": need,
                    "evidence": ev[:2]})
    # open elective: any course; for a dual degree the OPEL requirement is met by rule
    r = rem.get("OPEL")
    opel_need = bool(r and r["status"] not in ("met", "projected_met", "met_by_rule") and
                     ((r["remaining_after_projected"]["courses"] or 0) > 0))
    note = None
    if level == "G":
        note = "Higher degree course: counts as an open elective unless listed in your DEL pool"
    out.append({"category": "OPEL", "key": "OPEL", "kind": "elective", "label": "OPEL", "remaining_need": opel_need,
                "note": note, "evidence": snap.rule_evidence("elective_overflow_to_open")[:1]})
    if profile.minor:
        m = snap.minors["minors"].get(profile.minor)
        excl = minor_exclusion(profile, snap)
        if m and code in {c["code"] for c in m["core"] + m["electives"]} and not (excl and excl["excluded"]):
            role = "core" if code in {c["code"] for c in m["core"]} else "elective"
            out.append({"category": "MINOR", "key": "MINOR", "kind": role, "label": f"Minor in {m['name']} ({role})",
                        "remaining_need": True, "evidence": m["evidence"][:1]})
    # order: categories with remaining need first (stable)
    out.sort(key=lambda x: (not x["remaining_need"], ["DC", "GIR", "DEL", "HUEL", "OPEL", "MINOR"].index(x["category"])))
    return out


def evaluate_offering(offering: dict, profile: StudentProfile, snap: Snapshot, scope: dict, history: History,
                      req: dict | None, strict_prerequisites: bool = False) -> dict:
    code = offering["course_code"]
    course = snap.courses.get(code, {})
    cur = snap.curricula.get(scope.get("curriculum_id")) if scope.get("curriculum_id") else None
    ctx = own_context(cur, snap) if cur else None
    checks: list[dict] = []
    approvals = {(a.type, a.course_code) for a in profile.approvals}

    # 1. scope
    if scope["status"] == "unsupported":
        checks.append(_check("scope", "fail", "; ".join(scope["reasons"]), scope.get("evidence")))
    elif scope["status"] == "unresolved":
        checks.append(_check("scope", "unknown", "Curriculum applicability for your cohort is not established: " +
                             " ".join(scope["reasons"]), scope.get("evidence")))
    elif scope["status"] == "attested":
        checks.append(_check("scope", "pass", "Curriculum applicability attested by you (provisional).",
                             scope.get("evidence")))
    else:
        checks.append(_check("scope", "pass", scope["reasons"][0], scope.get("evidence")[:1]))

    # 2. availability
    if offering["status"] == "offered":
        checks.append(_check("offered", "pass", f"Offered in {snap.term['label']} (computer code {offering['computer_code']}).",
                             offering["evidence"][:1]))
    else:
        checks.append(_check("offered", "fail", f"Offering status is '{offering['status']}' in the timetable "
                             f"(all sections of a required component cancelled).", offering["evidence"][:1]))

    # 3. cohort / credit framework
    cs = offering.get("cohort_scope", {})
    if "admission_years" in cs:
        ok = profile.admission_year in cs["admission_years"]
        checks.append(_check("cohort", "pass" if ok else "fail",
                             "Credit-hour offering (com cod >= 5000) meant only for 2026 admissions." if not ok else
                             "Credit-hour offering for 2026 admissions.", offering.get("cohort_evidence")))
    elif profile.admission_year in cs.get("exclude_admission_years", []):
        checks.append(_check("cohort", "fail", "Unit-framework offering; 2026 admissions register in the credit-hour "
                             "(com cod >= 5000) offerings.", offering.get("cohort_evidence")))
    else:
        checks.append(_check("cohort", "pass", "Offering is open to your admission cohort (com cod < 5000).",
                             offering.get("cohort_evidence")))

    # 4. history
    st = history.states.get(code)
    via = history.equivalence_credit.get(code)
    if st and st.status == "cleared":
        checks.append(_check("not_cleared", "fail", f"Already cleared ({st.grade}). Repeating only to improve a grade "
                             "is a separate, optional choice.", snap.rule_evidence("repeat_to_improve")))
    elif st and st.status == "in_progress":
        checks.append(_check("not_cleared", "fail", "You are currently registered in this course."))
    elif via:
        checks.append(_check("not_cleared", "fail", f"Equivalent course {via[0]} already taken (listed equivalence).",
                             via[1]))
    else:
        checks.append(_check("not_cleared", "pass", "Not previously cleared."))

    # 5. prerequisites
    pr = course.get("prerequisite") or {"status": "not_in_bulletin"}
    waived = ("prerequisite_waiver", code) in approvals
    if pr.get("status") in ("parsed", "parsed_partial") and pr.get("expression"):
        ok, by, missing = eval_prereq_expr(pr["expression"], history)
        if ok and pr["status"] == "parsed":
            checks.append(_check("prerequisite", "pass", f"Prerequisite satisfied by {', '.join(by)} "
                                 f"(bulletin: '{pr['text']}').", pr.get("evidence")))
        elif ok:
            checks.append(_check("prerequisite", "unknown", f"Course-code part satisfied by {', '.join(by)}, but the "
                                 f"statement has qualifiers that could not be verified: '{pr['text']}'.", pr.get("evidence")))
        elif waived:
            checks.append(_check("prerequisite", "pass", f"Prerequisite ({pr['text']}) waived by recorded Dean's approval.",
                                 snap.rule_evidence("prerequisite_waiver")))
        else:
            ip = [m for m in missing if m in history.in_progress]
            msg = f"Prerequisite not met: needs {pr['text']}."
            if ip:
                msg += f" {', '.join(ip)} is in progress; current registration does not count (no concurrency rule)."
            checks.append(_check("prerequisite", "fail", msg, (pr.get("evidence") or []) +
                                 snap.rule_evidence("prerequisite_required")[:1], missing=missing))
    elif pr.get("status") == "unresolved":
        checks.append(_check("prerequisite", "pass" if waived else "unknown",
                             f"Prerequisite stated as '{pr['text']}' cannot be checked against course records."
                             if not waived else "Prerequisite waived by recorded approval.", pr.get("evidence")))
    else:
        cov = snap.rule_evidence("prerequisite_coverage_external")
        src = "bulletin course description" if pr.get("status") == "none_stated" else "supplied documents"
        if strict_prerequisites:
            checks.append(_check("prerequisite", "unknown", f"No prerequisite printed in the {src}; the authoritative "
                                 "list is on an external portal that was not supplied (strict mode).", cov))
        else:
            checks.append(_check("prerequisite", "pass", f"No prerequisite printed in the {src}. The timetable points "
                                 "to an external prerequisite list that was not supplied - confirm at registration.",
                                 (course.get("evidence", {}).get("bulletin") or [])[:1] + cov, caveat=True))

    # 6. programme-structure rules
    level = code.split(" ")[1][0]
    if cur and ctx:
        yr, sm = profile.year_sem
        cur_idx = sem_index(yr, sm)
        placement = next((n for n in cur["named_placement"] if code in n["codes"]), None)
        is_own_list = code in ctx["lists"]
        if placement:
            p_idx = sem_index(placement["year"], placement["sem"])
            before = [n for n in cur["named_placement"] if sem_index(n["year"], n["sem"]) < p_idx]
            missing = [n["codes"][0] for n in before if not any(history.is_cleared(c) for c in n["codes"])]
            ev = snap.rule_evidence("prior_preparation_named_courses")
            if not missing:
                checks.append(_check("prior_preparation", "pass", f"All named courses before year {placement['year']} "
                                     f"semester {placement['sem']} are cleared.", ev))
            elif ("dca_prior_preparation", code) in approvals and len(missing) <= 2:
                checks.append(_check("prior_preparation", "pass", f"DCA exception recorded for missing {', '.join(missing)}.",
                                     snap.rule_evidence("prior_preparation_dca_exception")))
            elif len(missing) <= 2:
                checks.append(_check("prior_preparation", "unknown", f"Prior preparation incomplete ({', '.join(missing)} "
                                     "not cleared); the DCA may examine cases with at most two such courses.",
                                     ev + snap.rule_evidence("prior_preparation_dca_exception"), approval="DCA"))
            else:
                checks.append(_check("prior_preparation", "fail", f"{len(missing)} named courses from earlier semesters "
                                     f"are not cleared (e.g. {', '.join(missing[:4])}).", ev, missing=missing))
            if p_idx > cur_idx + 1:
                checks.append(_check("sequence", "unknown", f"Named course placed in year {placement['year']} sem "
                                     f"{placement['sem']}; taking it early is a departure from the prescribed pattern.",
                                     snap.rule_evidence("prior_preparation_named_courses")))
        elif level == "G":
            own = code.split(" ")[0] in ctx["prefixes"] or is_own_list
            first_dc = [c for c in ctx["core"] if (next((n for n in cur["named_placement"] if c in n["codes"]), {})
                                                   .get("year") == "II")]
            done = all(history.is_cleared(c) for c in first_dc)
            if own:
                checks.append(_check("hd_prior_preparation", "pass" if done else "fail",
                                     "First set (year II) of own discipline core courses cleared." if done else
                                     "Higher degree course requires the year-II discipline core courses to be cleared.",
                                     snap.rule_evidence("higher_degree_course_prior_prep")))
            else:
                checks.append(_check("hd_prior_preparation", "unknown", "Higher degree course outside your discipline: "
                                     "requires the CDC of that discipline and DCA approval.",
                                     snap.rule_evidence("hd_course_needs_cdc")))
            hd_ok = ("hd_course_permission", code) in approvals or ("hd_course_permission", None) in approvals
            checks.append(_check("hd_cgpa", "pass" if hd_ok else "unknown",
                                 "Permission recorded." if hd_ok else "Higher degree courses are allowed above a CGPA "
                                 "prescribed by AGC; the threshold is not in the supplied documents.",
                                 snap.rule_evidence("higher_degree_course_limit"), approval=None if hd_ok else "AGC/DCA"))
        else:
            others = [pid for pid, kind in snap.list_membership.get(code, []) if pid not in cur["programmes"]]
            if others and not is_own_list and code not in snap.humanities_codes:
                y12 = [n for n in cur["named_placement"] if sem_index(n["year"], n["sem"]) < sem_index("III", 1)]
                missing = [n["codes"][0] for n in y12 if not any(history.is_cleared(c) for c in n["codes"])]
                checks.append(_check("other_discipline_prior_prep", "pass" if not missing else "fail",
                                     "Prior preparation through year III semester 1 of your programme completed "
                                     "(required for another degree's discipline course)." if not missing else
                                     f"Discipline course of another degree ({snap.programmes[others[0]]['name']}) "
                                     f"requires your years I-II named courses; missing {', '.join(missing[:4])}"
                                     f"{'...' if len(missing) > 4 else ''}.",
                                     snap.rule_evidence("other_discipline_course_prior_prep"), missing=missing))
        pk = project_kind(code)
        if pk:
            checks.append(_check("project_allotment", "unknown", "Project-type course: registration depends on "
                                 "allotment/supervisor consent.", snap.rule_evidence("project_course_limits")))

    statuses = [c["status"] for c in checks]
    status = "ineligible" if "fail" in statuses else ("needs_verification" if "unknown" in statuses else "eligible")
    contrib = contribution(code, profile, snap, cur, req, ctx) if cur else []
    return {
        "offering_id": offering["id"],
        "course_code": code,
        "status": status,
        "checks": checks,
        "decisive": [c for c in checks if c["status"] in ("fail", "unknown")] or
                    [c for c in checks if c["id"] in ("prerequisite", "offered", "cohort")],
        "approvals_needed": sorted({c.get("approval") for c in checks if c.get("approval")}),
        "contribution": contrib,
    }
