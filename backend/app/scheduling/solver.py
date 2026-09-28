"""Section selection and clash checking (optional timetable intelligence, FR-16).

Model: for each offering choose one active section per component type (Lecture / Tutorial /
Practical). Meetings are (day, hour-slot) cells from the timetable legend, so two meetings
clash exactly when they share a cell (half-open 50-minute intervals). Exams are
offering-level (date + session) and clash on overlapping intervals on the same date.

Search: exact depth-first branch-and-bound over bundles, most-constrained course first,
bounded by a node budget. Outcomes:
  feasible    - an assignment was found (the best one within budget is returned)
  infeasible  - the search space was exhausted without a feasible assignment
  unknown     - budget exhausted, or data needed for a check is missing (never reported
                as infeasible)
Undefined slots, TBA exams and conflicting exam cells make the result "unresolved" for the
affected course instead of being silently ignored.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@dataclass
class Prefs:
    no_early: bool = False           # avoid hour 1 (8-8:50 AM)
    no_early_hard: bool = False
    free_days: list[str] = field(default_factory=list)
    free_days_hard: bool = False
    compact: bool = False            # minimise gaps between classes
    avoid_late: bool = False         # avoid hour >= 9 (after 4 PM)


def bundles_for(offering: dict) -> tuple[list[dict], list[str]]:
    """All section bundles (one active section per component type) plus data issues."""
    issues = []
    by_type: dict[str, list[dict]] = {}
    for s in offering["sections"]:
        if s["status"] != "active":
            continue
        by_type.setdefault(s["type"], []).append(s)
    for s in offering["sections"]:
        if s["status"] == "active" and s["schedule_status"] in ("undefined_slot", "unparsed"):
            issues.append(f"{offering['course_code']} {s['label']}: slot '{s['raw_slot']}' not defined in the "
                          f"timetable legend")
    types = sorted(by_type)
    if not types:
        return [], issues + [f"{offering['course_code']}: no active sections"]
    out = []
    for combo in itertools.product(*[by_type[t] for t in types]):
        cells: set[tuple[str, int]] = set()
        clash = False
        undefined = False
        for s in combo:
            for m in s["meetings"]:
                if m.get("verification_status") == "undefined_slot":
                    undefined = True
                k = (m["day"], m["hour"])
                if k in cells:
                    clash = True
                cells.add(k)
        if clash:
            continue
        out.append({"sections": [s["label"] for s in combo], "section_ids": [s["id"] for s in combo],
                    "cells": cells, "undefined": undefined,
                    "detail": [{"label": s["label"], "type": s["type"], "slot": s["raw_slot"], "room": s["room"],
                                "instructors": s["instructors"][:3]} for s in combo]})
    return out, issues


def exam_conflicts(offerings: list[dict]) -> tuple[list[dict], list[str]]:
    clashes, unresolved = [], []
    exams = []
    for o in offerings:
        for e in o["exams"]:
            if e["status"] != "scheduled":
                unresolved.append(f"{o['course_code']} {e['type']}: {e['status'].replace('_', ' ')}"
                                  + (f" ('{e['raw']}')" if e.get("raw") else ""))
                continue
            exams.append((o["course_code"], e))
    for (c1, e1), (c2, e2) in itertools.combinations(exams, 2):
        if e1["date"] == e2["date"] and e1["start"] < e2["end"] and e2["start"] < e1["end"]:
            clashes.append({"courses": [c1, c2], "type": f"{e1['type']}/{e2['type']}", "date": e1["date"],
                            "sessions": [e1["session"], e2["session"]]})
    return clashes, unresolved


def _cost(cells: set, prefs: Prefs) -> float:
    c = 0.0
    if prefs.no_early:
        c += 5 * sum(1 for _, h in cells if h == 1)
    if prefs.avoid_late:
        c += 2 * sum(1 for _, h in cells if h >= 9)
    if prefs.free_days:
        c += 8 * sum(1 for d, _ in cells if d in prefs.free_days)
    return c


def _gaps(cells: set) -> int:
    g = 0
    for d in DAYS:
        hs = sorted(h for dd, h in cells if dd == d)
        if len(hs) > 1:
            g += (hs[-1] - hs[0] + 1) - len(hs)
    return g


def solve(offerings: list[dict], prefs: Prefs | None = None, locked: dict[str, list[str]] | None = None,
          node_budget: int = 200_000, time_budget_s: float = 2.0) -> dict:
    prefs = prefs or Prefs()
    locked = locked or {}
    t0 = time.time()
    options: dict[str, list[dict]] = {}
    issues: list[str] = []
    excluded: dict[str, list[str]] = {}
    for o in offerings:
        bs, iss = bundles_for(o)
        issues += iss
        if o["course_code"] in locked:
            bs = [b for b in bs if set(locked[o["course_code"]]) <= set(b["sections"])]
        kept = []
        for b in bs:
            reasons = []
            if prefs.no_early_hard and any(h == 1 for _, h in b["cells"]):
                reasons.append("8 AM class")
            if prefs.free_days_hard and any(d in prefs.free_days for d, _ in b["cells"]):
                reasons.append("meets on a day you want free")
            if reasons:
                excluded.setdefault(o["course_code"], []).append(f"{'+'.join(b['sections'])}: {', '.join(reasons)}")
            else:
                kept.append(b)
        kept.sort(key=lambda b: (_cost(b["cells"], prefs), b["sections"]))
        options[o["course_code"]] = kept
    ex_clash, ex_unresolved = exam_conflicts(offerings)

    empty = [c for c, bs in options.items() if not bs]
    if empty:
        return {"status": "infeasible", "reason": "no_admissible_sections", "courses_without_sections": empty,
                "excluded_by_preferences": excluded, "exam_clashes": ex_clash, "unresolved": issues + ex_unresolved,
                "assignment": None, "elapsed_ms": int((time.time() - t0) * 1000)}

    order = sorted(options, key=lambda c: (len(options[c]), c))
    best: dict = {"cost": None, "assign": None}
    nodes = 0
    exhausted = True

    def dfs(i: int, used: set, cost: float, assign: dict) -> None:
        nonlocal nodes, exhausted
        if nodes > node_budget or time.time() - t0 > time_budget_s:
            exhausted = False
            return
        nodes += 1
        if best["cost"] is not None and cost >= best["cost"]:
            return
        if i == len(order):
            total = cost + (0.5 * _gaps(used) if prefs.compact else 0)
            if best["cost"] is None or total < best["cost"]:
                best["cost"], best["assign"] = total, dict(assign)
            return
        c = order[i]
        for b in options[c]:
            if b["cells"] & used:
                continue
            assign[c] = b
            dfs(i + 1, used | b["cells"], cost + _cost(b["cells"], prefs), assign)
            del assign[c]

    dfs(0, set(), 0.0, {})
    elapsed = int((time.time() - t0) * 1000)
    if best["assign"] is None:
        status = "infeasible" if exhausted else "unknown"
        # binding pairs: courses whose every bundle pair clashes
        pairs = []
        for a, b in itertools.combinations(order, 2):
            if all(x["cells"] & y["cells"] for x in options[a] for y in options[b]):
                pairs.append([a, b])
        return {"status": status, "reason": "class_clash" if exhausted else "search_budget_exhausted",
                "binding_pairs": pairs, "excluded_by_preferences": excluded, "exam_clashes": ex_clash,
                "unresolved": issues + ex_unresolved, "assignment": None, "elapsed_ms": elapsed, "nodes": nodes}
    assign = {c: {"sections": b["sections"], "detail": b["detail"],
                  "alternatives": len(options[c]) - 1} for c, b in best["assign"].items()}
    cells = set().union(*[b["cells"] for b in best["assign"].values()]) if best["assign"] else set()
    status = "feasible" if not ex_clash else "infeasible"
    return {
        "status": status,
        "reason": "exam_clash" if ex_clash else None,
        "optimal_within_budget": exhausted,
        "assignment": assign,
        "grid": sorted([{"day": d, "hour": h, "course": next(c for c, b in best["assign"].items() if (d, h) in b["cells"])}
                        for d, h in cells], key=lambda x: (DAYS.index(x["day"]) if x["day"] in DAYS else 9, x["hour"])),
        "exam_clashes": ex_clash,
        "unresolved": issues + ex_unresolved,
        "excluded_by_preferences": excluded,
        "metrics": {"early_classes": sum(1 for _, h in cells if h == 1), "gap_hours": _gaps(cells),
                    "days_used": sorted({d for d, _ in cells}, key=lambda d: DAYS.index(d) if d in DAYS else 9)},
        "elapsed_ms": elapsed,
        "nodes": nodes,
    }


def check_candidate(candidate: dict, fixed: list[dict], prefs: Prefs, budget_s: float = 0.5) -> dict:
    """Can `candidate` be added to the fixed courses? Alternate sections are tried before rejecting."""
    res = solve(fixed + [candidate], prefs, time_budget_s=budget_s, node_budget=50_000)
    unresolved_for = [u for u in res["unresolved"] if u.startswith(candidate["course_code"])]
    exam = [c for c in res["exam_clashes"] if candidate["course_code"] in c["courses"]]
    if res["status"] == "feasible":
        status = "unresolved" if unresolved_for else "checked"
    elif res["status"] == "unknown":
        status = "unresolved"
    else:
        status = "clash"
    return {"status": status, "sections": (res.get("assignment") or {}).get(candidate["course_code"]),
            "exam_clashes": exam, "unresolved": unresolved_for, "reason": res.get("reason"),
            "binding_pairs": res.get("binding_pairs", []), "excluded_by_preferences":
                res.get("excluded_by_preferences", {}).get(candidate["course_code"], [])}
