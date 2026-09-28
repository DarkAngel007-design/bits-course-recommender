"""Requirement allocation (FR-06): completed and projected progress per category.

Allocation is deterministic:
  1. Named courses (GIR, DC of each degree) are matched first. A DC course shared by both
     degrees of a dual degree satisfies both (rule dual_degree_dc_shared); nothing else is
     double counted.
  2. Remaining courses are electives. Candidates: DEL (own pool incl. own-discipline project
     courses, within project limits), HUEL (humanities pool, not own discipline), OPEL (any).
     Courses eligible for several categories are assigned by exhaustive search minimising the
     remaining DEL/HUEL deficit (no greedy stranding); ties break on course code. Anything
     beyond a category's need overflows to OPEL (rule elective_overflow_to_open).
  3. The minor is tracked separately with its overlap limits.
Earned (cleared) and projected (cleared + in progress) allocations are computed separately.
"""
from __future__ import annotations

import itertools

from ..catalog.store import Snapshot
from .history import History
from .models import StudentProfile, sem_index

PROJECT_KIND = {"266": "study", "366": "laboratory", "367": "laboratory", "376": "design", "377": "design",
                "491": "special"}


def project_kind(code: str) -> str | None:
    lvl_num = code.split(" ")[1]
    if lvl_num.startswith("F") and lvl_num[1:4] in PROJECT_KIND:
        return PROJECT_KIND[lvl_num[1:4]]
    return None


def own_context(cur: dict, snap: Snapshot) -> dict:
    progs = [snap.programmes[p] for p in cur["programmes"]]
    return {
        "prefixes": {p["discipline_prefix"] for p in progs if p["discipline_prefix"]},
        "lists": {c for p in progs for c in p["core"] + p["electives"]},
        "core": {c for p in progs for c in p["core"]},
        "programmes": progs,
    }


def _units_of(code: str, history: History, snap: Snapshot) -> int:
    st = history.states.get(code)
    if st and st.credit_system == "credit_hours":
        return 0
    if st and st.units is not None:
        return st.units
    return snap.course_units(code) or 0


def elective_candidates(code: str, cur: dict, snap: Snapshot, ctx: dict) -> list[str]:
    cands = []
    for rq in cur["requirements"]:
        if rq["category"] == "DEL":
            deg = rq.get("degree") or cur["programmes"][0]
            prefix = snap.programmes[deg]["discipline_prefix"]
            if code in rq.get("pool", []) or (project_kind(code) and code.split(" ")[0] == prefix):
                cands.append(f"DEL:{deg}")
    if code in snap.humanities_codes and code.split(" ")[0] not in ctx["prefixes"] and code not in ctx["lists"]:
        cands.append("HUEL")
    return cands


def _allocate(codes: list[str], cur: dict, snap: Snapshot, history: History, projected: bool, ctx: dict) -> dict:
    alloc: dict[str, list[dict]] = {}
    used: set[str] = set()
    named_status: dict[str, list[dict]] = {}
    pool = set(codes)

    def satisfied(target: str) -> str | None:
        if target in pool:
            return target
        via = history.equivalence_credit.get(target)
        if via and via[0] in pool:
            return via[0]
        return None

    for rq in cur["requirements"]:
        if rq["kind"] != "named":
            continue
        key = rq["category"] + (f":{rq['degree']}" if rq.get("degree") else "")
        rows = []
        for item in rq["courses"]:
            by = next((s for s in (satisfied(c) for c in item["codes"]) if s), None)
            shared_ok = rq["category"] == "DC" and by in used and by in set(rq.get("shared_with_other_degree", []))
            if by and (by not in used or shared_ok):
                used.add(by)
                u = _units_of(by, history, snap)
                alloc.setdefault(key, []).append({"code": by, "units": u, "for": item["codes"],
                                                  "via_equivalence": by not in item["codes"]})
                rows.append({"codes": item["codes"], "done": True, "by": by})
            else:
                rows.append({"codes": item["codes"], "done": False, "by": None, "units": item.get("units")})
        named_status[key] = rows

    # electives
    need: dict[str, list[float]] = {}
    for rq in cur["requirements"]:
        if rq["kind"] == "pool" and rq["category"] in ("DEL", "HUEL", "OPEL"):
            key = rq["category"] + (f":{rq['degree']}" if rq.get("degree") else
                                    (f":{cur['programmes'][0]}" if rq["category"] == "DEL" else ""))
            need[key] = [rq.get("min_courses") or 0, rq.get("min_units") or 0]
    leftovers = sorted(c for c in codes if c not in used)
    cand = {c: [k for k in elective_candidates(c, cur, snap, ctx) if k in need] for c in leftovers}
    units = {c: _units_of(c, history, snap) for c in leftovers}
    limits = snap.rule("project_course_limits")
    lp = limits["params"] if limits else {"de_max": 3, "opel_max": 3, "total_max": 5, "study_max": 1,
                                          "laboratory_max": 2, "design_max": 2, "special_max": 1}

    def simulate(choice: dict[str, str]) -> tuple[dict, float]:
        rem = {k: list(v) for k, v in need.items()}
        out: dict[str, list[str]] = {}
        proj = {"DEL": 0, "OPEL": 0, "total": 0}
        sub = {"study": 0, "laboratory": 0, "design": 0, "special": 0}
        for c in leftovers:
            target = choice.get(c, "OPEL")
            pk = project_kind(c)
            if target.startswith("DEL") and (rem[target][0] <= 0 and rem[target][1] <= 0):
                target = "OPEL"  # overflow
            if target == "HUEL" and rem["HUEL"][0] <= 0 and rem["HUEL"][1] <= 0:
                target = "OPEL"
            if pk:
                if target.startswith("DEL") and (proj["DEL"] >= lp["de_max"] or sub[pk] >= lp.get(f"{pk}_max", 9)):
                    target = "OPEL"
                if target == "OPEL" and proj["OPEL"] >= lp["opel_max"]:
                    target = "UNCOUNTED"
                if proj["total"] >= lp["total_max"]:
                    target = "UNCOUNTED"
                if target != "UNCOUNTED":
                    proj["total"] += 1
                    proj["DEL" if target.startswith("DEL") else "OPEL"] += 1
                    if target.startswith("DEL"):
                        sub[pk] += 1
            if target in rem:
                rem[target][0] -= 1
                rem[target][1] -= units[c]
            out.setdefault(target, []).append(c)
        deficit = sum(max(0, v[0]) * 3 + max(0, v[1]) for k, v in rem.items() if k != "OPEL")
        deficit_open = sum(max(0, v[0]) * 3 + max(0, v[1]) for k, v in rem.items() if k == "OPEL")
        return out, deficit * 1000 + deficit_open

    ambiguous = [c for c in leftovers if len(cand[c]) >= 2]
    fixed = {c: (cand[c][0] if cand[c] else "OPEL") for c in leftovers if len(cand[c]) < 2}
    best = None
    if len(ambiguous) <= 10:
        for combo in itertools.product(*[cand[c] + ["OPEL"] for c in ambiguous]):
            choice = {**fixed, **dict(zip(ambiguous, combo))}
            out, score = simulate(choice)
            key = (score, tuple(sorted(choice.items())))
            if best is None or key < best[0]:
                best = (key, out)
    else:  # bounded fallback: deterministic first-candidate choice
        choice = {**fixed, **{c: cand[c][0] for c in ambiguous}}
        best = ((0,), simulate(choice)[0])
    for cat, cs in best[1].items():
        for c in cs:
            alloc.setdefault(cat, []).append({"code": c, "units": units[c], "candidates": cand[c] or ["OPEL"]})
    return {"alloc": alloc, "named": named_status}


def compute_requirements(profile: StudentProfile, snap: Snapshot, scope: dict, history: History) -> dict:
    if scope["status"] in ("unsupported",) or not scope.get("curriculum_id"):
        return {"available": False, "reasons": scope["reasons"]}
    cur = snap.curricula[scope["curriculum_id"]]
    ctx = own_context(cur, snap)
    earned_codes = sorted(history.cleared)
    projected_codes = sorted(history.cleared | history.in_progress)
    earned = _allocate(earned_codes, cur, snap, history, False, ctx)
    proj = _allocate(projected_codes, cur, snap, history, True, ctx)
    categories = []
    for rq in cur["requirements"]:
        if rq["category"] == "PS_THESIS":
            continue
        key = rq["category"] + (f":{rq['degree']}" if rq.get("degree") else
                                (f":{cur['programmes'][0]}" if rq["category"] == "DEL" else ""))
        e_list = earned["alloc"].get(key, [])
        p_list = proj["alloc"].get(key, [])
        mc, mu = rq.get("min_courses"), rq.get("min_units")
        def tot(lst):
            return {"courses": len(lst), "units": sum(x["units"] or 0 for x in lst)}
        et, pt = tot(e_list), tot(p_list)
        if rq.get("provenance") == "missing" or mc is None:
            status = "unresolved"
        elif rq["kind"] == "met_by_rule":
            status = "met_by_rule"
        elif et["courses"] >= mc and et["units"] >= (mu or 0):
            status = "met"
        elif pt["courses"] >= mc and pt["units"] >= (mu or 0):
            status = "projected_met"
        else:
            status = "in_progress" if et["courses"] else "not_started"
        entry = {
            "key": key,
            "category": rq["category"],
            "degree": rq.get("degree") or (cur["programmes"][0] if rq["category"] in ("DEL", "DC") else None),
            "label": _label(rq, snap, cur),
            "required": {"courses": mc, "units": mu},
            "earned": {**et, "list": e_list},
            "projected": {**pt, "list": [x for x in p_list if x["code"] in history.in_progress]},
            "remaining": {"courses": None if mc is None else max(0, mc - et["courses"]),
                          "units": None if mu is None else max(0, (mu or 0) - et["units"])},
            "remaining_after_projected": {"courses": None if mc is None else max(0, mc - pt["courses"]),
                                          "units": None if mu is None else max(0, (mu or 0) - pt["units"])},
            "status": status,
            "provenance": rq.get("provenance"),
            "derivation": rq.get("derivation"),
            "note": rq.get("note"),
            "evidence": rq.get("evidence", []),
            "credit_system": "units",
        }
        if rq["kind"] == "named":
            rows = earned["named"].get(key, [])
            prow = {tuple(r["codes"]): r for r in proj["named"].get(key, [])}
            entry["named_remaining"] = [
                {"codes": r["codes"], "title": " / ".join(snap.course_title(c) for c in r["codes"]),
                 "in_progress": prow.get(tuple(r["codes"]), {}).get("done", False),
                 "placement": (rq.get("placement") or {}).get(r["codes"][0]) or _placement(cur, r["codes"])}
                for r in rows if not r["done"]]
        categories.append(entry)
    # other allocations (overflow beyond OPEL limits, dual-degree extra electives)
    extra = earned["alloc"].get("UNCOUNTED", [])
    if cur["type"] == "dual":
        opel_extra = earned["alloc"].get("OPEL", [])
        for c in categories:
            if c["category"] == "OPEL":
                c["earned"] = {"courses": len(opel_extra), "units": sum(x["units"] for x in opel_extra), "list": opel_extra}

    yr, sm = profile.year_sem
    cur_idx = sem_index(yr, sm)
    obligations, backlog = [], []
    for n in cur["named_placement"]:
        idx = sem_index(n["year"], n["sem"])
        done = any(history.is_cleared(c) for c in n["codes"])
        ip = any(c in history.in_progress for c in n["codes"])
        row = {"codes": n["codes"], "title": " / ".join(snap.course_title(c) for c in n["codes"]),
               "category": n["category"], "year": n["year"], "sem": n["sem"], "in_progress": ip}
        if done:
            continue
        if idx == cur_idx:
            obligations.append(row)
        elif idx < cur_idx and not ip:
            backlog.append(row)
    minor = _minor_progress(profile, snap, history, earned, cur, ctx) if profile.minor else None
    totals = {"earned_units": sum(history.states[c].units or 0 for c in history.cleared
                                  if history.states[c].credit_system == "units"),
              "earned_courses": len(history.cleared),
              "in_progress_courses": len(history.in_progress),
              "in_progress_units": sum(history.states[c].units or 0 for c in history.in_progress)}
    return {
        "available": True,
        "curriculum_id": cur["id"],
        "curriculum_name": cur["name"],
        "curriculum_type": cur["type"],
        "provisional": scope["status"] != "resolved",
        "categories": categories,
        "uncounted": extra,
        "semester": {"year": yr, "sem": sm, "obligations": obligations, "backlog": backlog},
        "minor": minor,
        "totals": totals,
        "history_issues": history.issues,
        "rules_applied": ["course_cleared_by_grade", "latest_performance_governs", "elective_overflow_to_open",
                          "humanities_own_discipline_excluded", "project_course_limits"]
        + (["dual_degree_dc_shared", "dual_degree_open_met_by_de"] if cur["type"] == "dual" else []),
    }


def _placement(cur: dict, codes: list[str]) -> dict | None:
    for n in cur["named_placement"]:
        if set(codes) & set(n["codes"]):
            return {"year": n["year"], "sem": n["sem"]}
    return None


def _label(rq: dict, snap: Snapshot, cur: dict) -> str:
    base = {"GIR": "General institutional (named)", "DC": "Discipline core (CDC)", "DEL": "Discipline electives",
            "HUEL": "Humanities electives", "OPEL": "Open electives"}[rq["category"]]
    if rq.get("degree") and cur["type"] == "dual":
        base += f" - {snap.programmes[rq['degree']]['name']}"
    return base


def minor_exclusion(profile: StudentProfile, snap: Snapshot) -> dict | None:
    """Apply a minor's stated exclusion clause, e.g. '...exclusively designed for first-degree students of
    non-Computer Science disciplines.' Returns {excluded: bool|None, clause, evidence} or None."""
    import re
    m = snap.minors["minors"].get(profile.minor or "")
    if not m or not m.get("exclusions"):
        return None
    clause = m["exclusions"][0]
    hit = re.search(r"non-([A-Z][A-Za-z &]+?)\s+(?:disciplines?|programmes?|students)", clause)
    progs = [snap.programmes[p]["name"] for p in profile.programmes if p in snap.programmes]
    excluded = None
    if hit:
        disc = hit.group(1).strip().lower()
        excluded = any(disc in n.lower() for n in progs)
    return {"excluded": excluded, "clause": clause, "evidence": m.get("notes", [])}


def _minor_progress(profile: StudentProfile, snap: Snapshot, history: History, earned: dict, cur: dict, ctx: dict) -> dict:
    m = snap.minors["minors"].get(profile.minor)
    if not m:
        return {"name": profile.minor, "status": "unknown_minor", "message": "Minor not found in supplied bulletin"}
    gen = snap.minors["general"]
    core = [c["code"] for c in m["core"]]
    electives = [c["code"] for c in m["electives"]]
    mandatory = {x["code"] for k, v in earned["alloc"].items() if k.startswith(("GIR", "DC")) for x in v}
    counted, overlap, projects, notes = [], [], 0, []
    for code in sorted(history.cleared):
        if code not in core and code not in electives:
            continue
        u = snap.course_units(code) or 0
        if code in mandatory:
            if len(overlap) >= gen["max_overlap_with_mandatory_courses"] or \
                    sum(x["units"] for x in overlap) + u > gen["max_overlap_units"]:
                notes.append(f"{code} not counted: overlap with mandatory courses is limited to 2 courses / 6 units.")
                continue
            overlap.append({"code": code, "units": u})
        if code in electives and project_kind(code):
            if projects >= gen["max_project_courses"]:
                notes.append(f"{code} not counted: at most one project/seminar course may count toward a minor.")
                continue
            projects += 1
        counted.append({"code": code, "units": u, "role": "core" if code in core else "elective"})
    done_core = [c for c in core if any(x["code"] == c for x in counted)]
    tc, tu = len(counted), sum(x["units"] for x in counted)
    excl = minor_exclusion(profile, snap)
    return {
        "name": m["name"],
        "exclusion": excl,
        "required": {"courses": m["min_courses"], "units": m["min_units"]},
        "earned": {"courses": tc, "units": tu, "list": counted},
        "core": {"all": core, "done": done_core, "remaining": [c for c in core if c not in done_core]},
        "overlap_with_mandatory": overlap,
        "status": "excluded" if excl and excl["excluded"] else "met" if tc >= m["min_courses"] and tu >= m["min_units"] and not [c for c in core if c not in done_core]
        else "in_progress",
        "exclusions": m.get("exclusions", []),
        "notes": notes,
        "evidence": m["evidence"] + snap.rule_evidence("minor_overlap_limit"),
        "approval_needed": "Minor admission may carry CGPA/grade criteria set by the offering department; "
                           "declaration is due at the end of the 2nd year.",
    }
