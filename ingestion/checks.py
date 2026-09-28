"""Publication checks (dependency-light so the API can verify snapshots without PDF libraries)."""
from __future__ import annotations

import json
from pathlib import Path


def _walk_evidence(obj, found: set):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("evidence", "cohort_evidence", "notes_evidence", "dual_degree_evidence",
                     "own_discipline_exclusion_evidence", "list_evidence", "notes") and isinstance(v, (list, dict)):
                vals = v if isinstance(v, list) else [x for vv in v.values() for x in (vv if isinstance(vv, list) else [vv])]
                for e in vals:
                    if isinstance(e, str) and e.startswith("ev_"):
                        found.add(e)
            _walk_evidence(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_evidence(v, found)


def check_payload(p: dict) -> list[str]:
    problems = []
    ev = p["evidence.json"]
    refs: set[str] = set()
    for name, obj in p.items():
        if name not in ("evidence.json",):
            _walk_evidence(obj, refs)
    missing = sorted(r for r in refs if r not in ev)
    if missing:
        problems.append(f"{len(missing)} evidence ids referenced but not recorded, e.g. {missing[:3]}")
    for oid, o in p["offerings.json"].items():
        if o["course_code"] not in p["courses.json"]:
            problems.append(f"offering {oid} references unknown course {o['course_code']}")
        if not o["evidence"]:
            problems.append(f"offering {oid} has no evidence")
    for code, c in p["courses.json"].items():
        for h in c["handouts"]:
            if h not in p["handouts.json"]:
                problems.append(f"course {code} references unknown handout {h}")
    for r in p["rules.json"]["rules"]:
        if r["review_status"] == "reviewed" and not r["evidence"]:
            problems.append(f"rule {r['id']} reviewed without evidence")
    if not p["offerings.json"]:
        problems.append("no offerings extracted")
    if not p["curricula.json"]:
        problems.append("no curricula built")
    return problems


def verify(snapshot: Path) -> list[str]:
    payload = {f.name: json.loads(f.read_text()) for f in snapshot.glob("*.json")}
    required = ["manifest.json", "courses.json", "offerings.json", "handouts.json", "curricula.json", "rules.json",
                "evidence.json", "review_queue.json", "coverage.json"]
    problems = [f"missing {r}" for r in required if r not in payload]
    if problems:
        return problems
    return check_payload(payload)
