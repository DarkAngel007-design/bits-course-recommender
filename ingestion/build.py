"""Ingestion orchestrator: raw PDFs -> validated, immutable, published dataset snapshot.

    python -m ingestion build  --raw dataset --out data/published
    python -m ingestion verify --snapshot data/published/<id>

Idempotent: the snapshot id is a hash of the input file hashes, parser version and rules
version. Re-running over identical inputs reproduces the same id; an existing snapshot is
verified and left untouched rather than duplicated. A new timetable/handout set yields a new
snapshot id and the previous snapshot is kept (old plans become stale, not overwritten).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import curricula as cur_mod
from .common import PARSER_VERSION, Registry, code_level, code_prefix, parse_code, short_hash, squash
from .extractors import bulletin as B
from .extractors.handouts import extract_handout
from .extractors.rules import load_and_validate
from .extractors.timetable import extract_timetable
from .manifest import build_manifest, content_groups
from .pdfcache import PdfCache
from .topics import TAXONOMY, tag_topics

SCHEMA_VERSION = "1.0"


def _log(msg: str) -> None:
    print(f"[ingest] {msg}", file=sys.stderr, flush=True)


def _warm(args):
    doc, staging, tables = args
    pc = PdfCache(Path(staging))
    pc.text(doc)
    if tables:
        pc.tables(doc)
    return doc["file"]


def _file_code(file: str) -> str | None:
    stem = Path(file).stem
    stem = re.sub(r"^\d+_", "", stem)
    stem = re.sub(r"-\d+$", "", stem)
    return parse_code(stem.replace("_", " "), strict=False)


def build(raw: Path, out: Path, staging: Path, workers: int = 8, force: bool = False) -> Path:
    t0 = time.time()
    docs = build_manifest(raw)
    if not docs:
        raise SystemExit(f"No PDFs found under {raw}")
    fams = {f: [d for d in docs if d["family"] == f] for f in ("timetable", "bulletin", "regulations", "handout")}
    for f in ("timetable", "bulletin", "regulations"):
        if len(fams[f]) != 1:
            raise SystemExit(f"Expected exactly one {f} PDF, found {len(fams[f])}")
    # Snapshot identity covers inputs AND the pipeline code/rules, so a parser change yields a
    # new snapshot while an identical rerun reproduces the same one.
    code_hash = short_hash(*[f.read_text() for f in sorted(Path(__file__).parent.rglob("*"))
                             if f.suffix in (".py", ".yaml") and "__pycache__" not in f.parts])
    snap_id = "snap-" + short_hash(PARSER_VERSION, SCHEMA_VERSION, code_hash,
                                   *sorted(d["sha256"] + d["file"] for d in docs), n=16)
    target = out / snap_id
    if target.exists() and not force:
        _log(f"snapshot {snap_id} already published for identical inputs; verifying instead of rebuilding")
        problems = verify(target)
        if problems:
            raise SystemExit("existing snapshot failed verification:\n" + "\n".join(problems[:20]))
        _point_current(out, snap_id)
        return target

    staging.mkdir(parents=True, exist_ok=True)
    pc = PdfCache(staging)
    uniq_handouts: dict[str, dict] = {}
    for d in fams["handout"]:
        uniq_handouts.setdefault(d["sha256"], d)
    jobs = [(d, str(staging), True) for d in uniq_handouts.values()]
    jobs += [(fams["timetable"][0], str(staging), True), (fams["bulletin"][0], str(staging), False),
             (fams["regulations"][0], str(staging), False)]
    _log(f"{len(docs)} PDFs ({len(uniq_handouts)} distinct handouts); extracting text/tables with {workers} workers")
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            list(ex.map(_warm, jobs))
    else:
        for j in jobs:
            _warm(j)

    reg = Registry()
    # ---------------- timetable ----------------
    tdoc = fams["timetable"][0]
    tt = extract_timetable(tdoc, pc.text(tdoc), pc.tables(tdoc), reg)
    _log(f"timetable: {len(tt['offerings'])} offerings, {len(tt['equivalences'])} equivalence rows")

    # ---------------- bulletin ----------------
    bdoc = fams["bulletin"][0]
    bpages = pc.text(bdoc)
    descriptions = B.extract_course_descriptions(bdoc, bpages, reg)
    lists = B.extract_programme_lists(bdoc, bpages, reg)
    minors = B.extract_minors(bdoc, bpages, reg)
    structure = B.extract_structure(bdoc, bpages, reg)
    chart_start = B._find_page(bpages, r"Semester-wise Pattern for Students Admitted to") or 1
    chart_end = B._find_page(bpages, r"List of Courses for B\.E\.", chart_start) or chart_start + 110
    btables = pc.tables(bdoc, list(range(chart_start, chart_end)))
    charts = B.extract_charts(bdoc, bpages, btables, reg)
    _log(f"bulletin: {len(descriptions)} course descriptions, {len(lists['programmes'])} programme lists, "
         f"{len(charts)} charts, {len(minors['minors'])} minors")

    # ---------------- regulations / rules ----------------
    rdoc = fams["regulations"][0]
    rules = load_and_validate({"regulations": (rdoc, pc.text(rdoc)), "bulletin": (bdoc, bpages),
                               "timetable": (tdoc, pc.text(tdoc))}, reg)
    _log(f"rules: {sum(r['review_status'] == 'reviewed' for r in rules['rules'])}/{len(rules['rules'])} validated")

    # ---------------- handouts ----------------
    groups = content_groups(fams["handout"])
    handouts: dict[str, dict] = {}
    for sha, d in uniq_handouts.items():
        rec = extract_handout(d, pc.text(d), pc.tables(d), reg, _file_code(d["file"]))
        files = sorted(groups[sha])
        rec["files"] = files
        file_codes = [c for c in (_file_code(f) for f in files) if c]
        for c in file_codes:
            if c not in rec["course_codes"]:
                rec["course_codes"].append(c)
        rec["file_codes"] = file_codes
        rec["id"] = "ho_" + sha[:12]
        handouts[rec["id"]] = rec
    _log(f"handouts: {len(handouts)} distinct records")

    # ---------------- catalogue ----------------
    courses: dict[str, dict] = {}

    def course(code: str) -> dict:
        if code not in courses:
            courses[code] = {"code": code, "prefix": code_prefix(code), "level": code_level(code), "title": None,
                             "title_source": None, "L": None, "P": None, "U": None, "description": None,
                             "prerequisite": {"stated": False, "status": "not_in_bulletin", "text": None,
                                              "expression": None},
                             "equivalent_stated": [], "handouts": [], "offerings": [], "evidence": {}}
        return courses[code]

    for code, d in descriptions.items():
        c = course(code)
        c.update({"title": d["title"], "title_source": "bulletin", "L": d["L"], "P": d["P"], "U": d["U"],
                  "units_note": d.get("units_note"), "description": d["description"],
                  "prerequisite": d["prerequisite"], "equivalent_stated": d["equivalent_stated"]})
        c["evidence"]["bulletin"] = d["evidence"]
        c["bulletin_page"] = d["page"]
    for o in tt["offerings"]:
        c = course(o["course_code"])
        c["offerings"].append(o["id"])
        if not c["title"]:
            c["title"], c["title_source"] = o["title_timetable"].title(), "timetable"
        c["evidence"].setdefault("timetable", []).extend(o["evidence"])
    for hid, h in handouts.items():
        for code in h.get("course_codes", []):
            c = course(code)
            c["handouts"].append(hid)
            if not c["title"] and h.get("title"):
                c["title"], c["title_source"] = h["title"], "handout"
    # distinct handouts for one code -> conflict to review (both kept)
    for code, c in courses.items():
        if len(c["handouts"]) > 1:
            reg.flag("conflict", f"course:{code}", "handout",
                     f"{len(c['handouts'])} different handout documents list {code}; offering-specific facts are "
                     "shown per handout", [e for h in c["handouts"] for e in handouts[h]["evidence"].get("course_codes", [])])
    # timetable offering without bulletin description
    for o in tt["offerings"]:
        if o["course_code"] not in descriptions:
            reg.flag("missing", f"course:{o['course_code']}", "bulletin_description",
                     "Offered in timetable but not described in bulletin Part VI", o["evidence"], severity="info")
        if not courses[o["course_code"]]["handouts"] and o["status"] == "offered":
            reg.flag("missing", f"offering:{o['id']}", "handout", f"No handout supplied for {o['course_code']}",
                     o["evidence"], severity="info")

    # topics
    for code, c in courses.items():
        hts = [handouts[h] for h in c["handouts"]]
        fields = {"title": c["title"] or "", "bulletin_description": c["description"] or "",
                  "handout": " ".join(filter(None, [x.get("description") for x in hts] + [x.get("scope") for x in hts]
                                      + [x.get("syllabus_text") for x in hts]))}
        c["topics"] = tag_topics(fields)

    # equivalences / relationships (never invented: only listed pairs)
    relationships = []
    for e in tt["equivalences"]:
        for other in e["equivalent_codes"]:
            relationships.append({"source": e["course_code"], "target": other, "type": "equivalence",
                                  "applicability": e["scope"], "evidence": e["evidence"]})
    for code, d in descriptions.items():
        for other in d["equivalent_stated"]:
            if other != code:
                relationships.append({"source": code, "target": other, "type": "equivalence",
                                      "applicability": "bulletin course description", "evidence": d["evidence"]})
    for hid, h in handouts.items():
        if len(h.get("course_codes", [])) > 1:
            relationships.append({"source": h["course_codes"][0], "targets": h["course_codes"][1:],
                                  "type": "shared_handout", "applicability": "same handout document (not equivalence)",
                                  "evidence": h["evidence"].get("course_codes", [])})

    # curricula
    cur = cur_mod.build_curricula({"programmes": lists["programmes"], "charts": charts}, courses, structure, reg, bdoc)
    for cid, c in cur["curricula"].items():
        for rq in c["requirements"]:
            for n in rq.get("courses", []):
                for code in n["codes"]:
                    if code not in courses:
                        reg.flag("missing", f"curriculum:{cid}", code,
                                 "Curriculum course not found in bulletin descriptions or timetable", c["evidence"],
                                 severity="info")
    _log(f"curricula: {len(cur['programmes'])} programmes, {len(cur['curricula'])} curricula")

    # ---------------- assemble + validate ----------------
    evidence = {k: v.to_dict() for k, v in sorted(reg.evidence.items())}
    review = [v.to_dict() for v in sorted(reg.review.values(), key=lambda r: (r.severity, r.kind, r.subject))]
    offerings = {o["id"]: o for o in tt["offerings"]}
    manifest = {
        "snapshot_id": snap_id,
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "rules_version": rules["version"],
        "term": tt["term"],
        "curriculum_edition": cur_mod.BULLETIN_EDITION,
        "created_from": [{k: d[k] for k in ("doc_id", "file", "sha256", "family", "pages", "bytes")} for d in docs],
        "duplicate_handout_groups": [v for v in groups.values() if len(v) > 1],
    }
    payload = {
        "manifest.json": manifest,
        "courses.json": courses,
        "offerings.json": offerings,
        "handouts.json": handouts,
        "programmes.json": cur["programmes"],
        "curricula.json": cur["curricula"],
        "minors.json": minors,
        "humanities_pool.json": {"courses": lists["humanities_pool"],
                                 "own_discipline_exclusion_evidence": lists["humanities_exclusion_evidence"]},
        "project_rules.json": lists["project_rules"],
        "structure.json": structure,
        "rules.json": rules,
        "relationships.json": relationships,
        "slot_legend.json": tt["slot_legend"],
        "topics.json": {k: {"label": v["label"], "aliases": v["aliases"]} for k, v in TAXONOMY.items()},
        "evidence.json": evidence,
        "review_queue.json": review,
    }
    payload["coverage.json"] = coverage_report(payload)
    problems = check_payload(payload)
    if problems:
        for p in problems[:30]:
            _log("CHECK FAILED: " + p)
        raise SystemExit(f"{len(problems)} publication checks failed; snapshot not published")

    tmp = out / f".{snap_id}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for name, obj in payload.items():
        (tmp / name).write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, default=str))
    if target.exists():
        shutil.rmtree(target)
    os.replace(tmp, target)  # atomic publish of the directory
    _point_current(out, snap_id)
    _log(f"published {snap_id} in {time.time() - t0:.1f}s ({len(review)} review items)")
    return target


def _point_current(out: Path, snap_id: str) -> None:
    tmp = out / ".CURRENT.tmp"
    tmp.write_text(snap_id + "\n")
    os.replace(tmp, out / "CURRENT")


def coverage_report(p: dict) -> dict:
    offerings = p["offerings.json"]
    handouts = p["handouts.json"]
    courses = p["courses.json"]
    offered = [o for o in offerings.values() if o["status"] == "offered"]
    codes_offered = {o["course_code"] for o in offered}
    with_handout = {c for c in codes_offered if courses[c]["handouts"]}
    with_desc = {c for c in codes_offered if courses[c].get("bulletin_page")}
    hs = list(handouts.values())
    rq = p["review_queue.json"]
    from collections import Counter
    return {
        "offerings_total": len(offerings),
        "offerings_offered": len(offered),
        "offerings_by_status": dict(Counter(o["status"] for o in offerings.values())),
        "offered_courses": len(codes_offered),
        "offered_with_handout": len(with_handout),
        "offered_with_bulletin_description": len(with_desc),
        "handouts_distinct": len(hs),
        "handout_extraction": dict(Counter(h["extraction_status"] for h in hs)),
        "handout_midsem_status": dict(Counter((h.get("midsem") or {}).get("status", "n/a") for h in hs)),
        "handout_attendance_status": dict(Counter((h.get("attendance") or {}).get("status", "n/a") for h in hs)),
        "catalog_courses": len(courses),
        "prerequisite_status": dict(Counter(c["prerequisite"]["status"] for c in courses.values())),
        "curricula": len(p["curricula.json"]),
        "curricula_by_type": dict(Counter(c["type"] for c in p["curricula.json"].values())),
        "rules_reviewed": sum(r["review_status"] == "reviewed" for r in p["rules.json"]["rules"]),
        "review_queue": {"total": len(rq), "by_kind": dict(Counter(r["kind"] for r in rq)),
                         "by_severity": dict(Counter(r["severity"] for r in rq))},
        "supported_scope": {
            "campus": "Pilani",
            "term": p["manifest.json"]["term"]["label"],
            "curriculum_edition": p["manifest.json"]["curriculum_edition"],
            "resolved_admission_years": cur_mod.RESOLVED_ADMISSION_YEARS,
            "unsupported": [
                "Dubai, Goa, Hyderabad, Mumbai campuses (no timetable supplied)",
                "2026 admissions (credit-hour framework; no applicable curriculum supplied)",
                "Admission years other than 2025: curriculum applicability not established by supplied documents",
                "Higher degree and PhD programmes (first-degree requirements only)",
                "International 2+2 / collaborative programmes",
            ],
            "limitations": [
                "Authoritative prerequisite list is on an external portal (timetable VI); only prerequisites printed "
                "in bulletin course descriptions are evaluated",
                "CGPA threshold for higher-degree electives is prescribed by AGC and not supplied",
            ],
        },
    }


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
