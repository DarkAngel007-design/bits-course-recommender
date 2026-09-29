"""FastAPI application: versioned REST API over the academic engine (Architecture §10).

Auth model (local deployment):
  * Creating a profile returns an owner token; every profile-scoped call must send it in
    `X-Profile-Token` (profile ownership check).
  * Maintainer endpoints require `X-Admin-Token` == ADMIN_TOKEN (server-side env var).
Error contract: 422 malformed input, 404 unknown ids, 409 stale profile version,
403 wrong token; empty recommendation results are 200 with reasons.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..academics.eligibility import evaluate_offering
from ..academics.models import StudentProfile, normalize_code
from ..academics.plan import validate_plan
from ..catalog.store import Snapshot, get_store
from ..profiles import db
from ..recommendations.engine import academic_state, recommend
from ..recommendations.intent import Intent, interpret
from ..scheduling.solver import Prefs, solve

log = logging.getLogger("api")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
MIN_ADMIN_TOKEN_LEN = 16


def _admin_token() -> tuple[str, bool]:
    """Maintainer token. There is no built-in default: set ADMIN_TOKEN (>= 16 chars) for a stable
    token, otherwise a random one is generated for this process and logged once at startup."""
    tok = os.environ.get("ADMIN_TOKEN", "").strip()
    if not tok:
        return secrets.token_urlsafe(24), True
    if len(tok) < MIN_ADMIN_TOKEN_LEN:
        raise RuntimeError(f"ADMIN_TOKEN must be at least {MIN_ADMIN_TOKEN_LEN} characters")
    return tok, False


ADMIN_TOKEN, ADMIN_TOKEN_GENERATED = _admin_token()
RAW_DIR = Path(os.environ.get("RAW_DIR", Path(__file__).resolve().parents[3] / "dataset"))
API_VERSION = "1.0.0"

app = FastAPI(title="BITS Academic Course Recommender API", version=API_VERSION,
              description="Evidence-backed course recommendations computed live from a published dataset snapshot.")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","), allow_methods=["*"],
                   allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    snap = get_store().current()
    log.info("serving snapshot %s (%s), rules %s", snap.id, snap.term["label"], snap.rules_version)
    if ADMIN_TOKEN_GENERATED:
        log.warning("ADMIN_TOKEN not set: generated a one-time maintainer token for this process: %s "
                    "(set ADMIN_TOKEN for a stable value)", ADMIN_TOKEN)


@app.exception_handler(ValidationError)
async def _pyd(request, exc: ValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors(include_url=False)})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def snapshot() -> Snapshot:
    try:
        return get_store().current()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


class _LRU:
    def __init__(self, n: int = 256) -> None:
        self.n, self.d = n, OrderedDict()

    def get(self, k):
        if k in self.d:
            self.d.move_to_end(k)
            return self.d[k]
        return None

    def put(self, k, v):
        self.d[k] = v
        self.d.move_to_end(k)
        while len(self.d) > self.n:
            self.d.popitem(last=False)


_cache = _LRU()


def load_profile(profile_id: str, token: str | None) -> tuple[db.ProfileRow, StudentProfile]:
    with db.session() as s:
        row = s.get(db.ProfileRow, profile_id)
    if row is None:
        raise HTTPException(404, "profile not found")
    if not token or not hmac.compare_digest(db.hash_token(token), row.token_hash):
        raise HTTPException(403, "missing or invalid X-Profile-Token")
    return row, StudentProfile.model_validate(row.data)


def require_admin(x_admin_token: str | None = Header(None)) -> None:
    if not x_admin_token or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(403, "maintainer token required")


def profile_warnings(p: StudentProfile, snap: Snapshot) -> list[dict]:
    out = []
    for a in p.attempts:
        if a.course_code not in snap.courses:
            out.append({"field": "attempts", "course": a.course_code,
                        "message": f"{a.course_code} is not in the supplied bulletin, timetable or handouts"})
        if a.status == "completed" and not a.grade:
            out.append({"field": "attempts", "course": a.course_code,
                        "message": f"{a.course_code}: add the grade or report so it can be counted"})
    for pid in p.programmes:
        if pid not in snap.programmes:
            out.append({"field": "programmes", "message": f"Unknown programme {pid}"})
    if p.minor and p.minor not in snap.minors["minors"]:
        out.append({"field": "minor", "message": f"Minor '{p.minor}' not found in the bulletin"})
    return out


def profile_out(row: db.ProfileRow, snap: Snapshot) -> dict:
    p = StudentProfile.model_validate(row.data)
    return {"id": row.id, "version": row.version, "profile": row.data, "updated_at": row.updated_at,
            "warnings": profile_warnings(p, snap)}


# ---------------------------------------------------------------------------
# meta / catalogue
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    snap = snapshot()
    return {"status": "ok", "dataset_version": snap.id, "rules_version": snap.rules_version}


@app.get("/meta")
def meta():
    snap = snapshot()
    llm = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return {
        "api_version": API_VERSION,
        "dataset_version": snap.id,
        "rules_version": snap.rules_version,
        "term": snap.term,
        "curriculum_edition": snap.manifest["curriculum_edition"],
        "coverage": snap.coverage,
        "llm_enabled": llm,
        "programmes": sorted([{"id": p["id"], "name": p["name"], "degree": p["degree"]} for p in snap.programmes.values()],
                             key=lambda x: x["name"]),
        "dual_degrees": sorted([{"id": c["id"], "programmes": c["programmes"], "name": c["name"]}
                                for c in snap.curricula.values() if c["type"] == "dual"], key=lambda x: x["name"]),
        "minors": sorted(snap.minors["minors"]),
        "topics": [{"id": k, **v} for k, v in snap.topics.items()],
        "grades": (snap.rule("course_cleared_by_grade") or {}).get("params"),
    }


@app.get("/curricula/resolve")
def resolve_curriculum(programmes: str):
    snap = snapshot()
    want = [p for p in programmes.split(",") if p]
    cur = next((c for c in snap.curricula.values()
                if (c["type"] == "single" and c["programmes"] == want) or
                (c["type"] == "dual" and len(want) == 2 and set(c["programmes"]) == set(want))), None)
    if not cur:
        raise HTTPException(404, "no curriculum for this programme combination")
    return {"id": cur["id"], "name": cur["name"], "type": cur["type"], "named_placement": cur["named_placement"],
            "evidence": cur["evidence"]}


@app.get("/demo-profiles")
def demo_profiles():
    """Synthetic golden profiles (the same fixtures the test suite uses)."""
    import json
    path = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "golden_profiles.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {k: {"label": v["label"], "profile": v["profile"]} for k, v in data.items() if not k.startswith("_")}


@app.get("/courses")
def search_courses(q: str = Query("", max_length=80), offered_only: bool = False, limit: int = Query(20, le=100)):
    snap = snapshot()
    ql = q.strip().lower().replace(" ", "")
    out = []
    for code, c in snap.courses.items():
        offered = any(o["status"] == "offered" for o in snap.offerings_by_course.get(code, []))
        if offered_only and not offered:
            continue
        hay = (code.replace(" ", "") + " " + (c.get("title") or "")).lower()
        if not ql or ql in hay or q.lower() in (c.get("title") or "").lower():
            out.append({"code": code, "title": c.get("title"), "units": c.get("U"), "offered": offered})
    out.sort(key=lambda x: (not x["code"].replace(" ", "").lower().startswith(ql), x["code"]))
    return {"results": out[:limit], "total": len(out)}


@app.get("/courses/{code}")
def get_course(code: str):
    snap = snapshot()
    try:
        code = normalize_code(code)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    c = snap.courses.get(code)
    if not c:
        raise HTTPException(404, "course not in snapshot")
    return {**c, "offerings": [snap.offerings[o] for o in c["offerings"]],
            "handouts": [{k: v for k, v in h.items() if k != "syllabus_text"} for h in snap.handouts_for(code)],
            "equivalents": sorted(snap.equivalents.get(code, [])),
            "programme_lists": snap.list_membership.get(code, []),
            "in_humanities_pool": code in snap.humanities_codes}


@app.get("/offerings/{offering_id:path}")
def get_offering(offering_id: str):
    snap = snapshot()
    o = snap.offerings.get(offering_id)
    if not o:
        raise HTTPException(404, "offering not found")
    code = o["course_code"]
    return {**o, "course": {k: snap.courses[code].get(k) for k in ("code", "title", "U", "description", "prerequisite")},
            "handouts": [{k: v for k, v in h.items() if k != "syllabus_text"} for h in snap.handouts_for(code)]}


@app.get("/sources/{evidence_id}")
def get_source(evidence_id: str):
    snap = snapshot()
    ev = snap.evidence_record(evidence_id)
    if not ev:
        raise HTTPException(404, "evidence not found")
    available = (RAW_DIR / ev["file"]).exists()
    return {**ev, "document_url": f"/documents/{ev['doc_id']}#page={ev['pdf_page']}" if available else None,
            "document_available": available}


@app.get("/documents/{doc_id}")
def get_document(doc_id: str):
    snap = snapshot()
    doc = next((d for d in snap.manifest["created_from"] if d["doc_id"] == doc_id), None)
    if not doc:
        raise HTTPException(404, "document not in manifest")
    path = RAW_DIR / doc["file"]
    if not path.exists():
        raise HTTPException(404, "raw PDF not present on this server (dataset/ is not distributed with the repo)")
    return FileResponse(path, media_type="application/pdf")


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------
class ProfilePatch(BaseModel):
    expected_version: int
    profile: StudentProfile


@app.post("/profiles", status_code=201)
def create_profile(profile: StudentProfile):
    snap = snapshot()
    bad = [p for p in profile.programmes if p not in snap.programmes]
    if bad:
        raise HTTPException(422, f"unknown programme id(s): {bad}")
    token = db.new_token()
    row = db.ProfileRow(id=db.new_id("prof"), token_hash=db.hash_token(token), version=1,
                        data=profile.model_dump(mode="json"), created_at=db.now(), updated_at=db.now())
    with db.session() as s:
        s.add(row)
        s.commit()
    return {**profile_out(row, snap), "owner_token": token}


@app.get("/profiles/{profile_id}")
def get_profile(profile_id: str, x_profile_token: str | None = Header(None)):
    row, _ = load_profile(profile_id, x_profile_token)
    return profile_out(row, snapshot())


@app.patch("/profiles/{profile_id}")
def patch_profile(profile_id: str, body: ProfilePatch, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, _ = load_profile(profile_id, x_profile_token)
    bad = [p for p in body.profile.programmes if p not in snap.programmes]
    if bad:
        raise HTTPException(422, f"unknown programme id(s): {bad}")
    with db.session() as s:
        cur = s.get(db.ProfileRow, profile_id)
        if cur.version != body.expected_version:
            raise HTTPException(409, {"message": "profile was changed elsewhere; reload and retry",
                                      "current_version": cur.version})
        cur.data = body.profile.model_dump(mode="json")
        cur.version += 1
        cur.updated_at = db.now()
        s.commit()
        s.refresh(cur)
        return profile_out(cur, snap)


@app.get("/profiles/{profile_id}/requirements")
def get_requirements(profile_id: str, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, prof = load_profile(profile_id, x_profile_token)
    key = ("req", row.id, row.version, snap.id, snap.rules_version)
    hit = _cache.get(key)
    if hit:
        return hit
    st = academic_state(prof, snap)
    out = {"profile_id": row.id, "profile_version": row.version, "dataset_version": snap.id,
           "rules_version": snap.rules_version, "scope": st["scope"], "requirements": st["requirements"],
           "attempts": {c: {"status": s.status, "grade": s.grade, "units": s.units, "notes": s.notes,
                            "repeat_in_progress": s.repeat_in_progress}
                        for c, s in st["history"].states.items()},
           "equivalence_credit": {k: {"via": v[0], "evidence": v[1]} for k, v in st["history"].equivalence_credit.items()}}
    out = db.dumps(out)
    _cache.put(key, out)
    return out


# ---------------------------------------------------------------------------
# eligibility / recommendations
# ---------------------------------------------------------------------------
class EligibilityRequest(BaseModel):
    profile_id: str
    offering_ids: list[str] = Field(default_factory=list)
    course_codes: list[str] = Field(default_factory=list)
    strict_prerequisites: bool = False


@app.post("/eligibility/evaluate")
def eligibility(body: EligibilityRequest, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, prof = load_profile(body.profile_id, x_profile_token)
    ids = list(body.offering_ids)
    for c in body.course_codes:
        try:
            code = normalize_code(c)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        ids += [o["id"] for o in snap.offerings_by_course.get(code, [])]
    missing = [i for i in ids if i not in snap.offerings]
    if missing:
        raise HTTPException(404, f"unknown offering ids {missing}")
    st = academic_state(prof, snap)
    req = st["requirements"] if st["requirements"].get("available") else None
    return {"profile_version": row.version, "dataset_version": snap.id, "rules_version": snap.rules_version,
            "scope": st["scope"],
            "results": [evaluate_offering(snap.offerings[i], prof, snap, st["scope"], st["history"], req,
                                          body.strict_prerequisites) for i in ids]}


class IntentRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    use_llm: bool = True
    profile_id: str | None = None


@app.post("/intent/parse")
def parse_intent(body: IntentRequest, x_profile_token: str | None = Header(None)):
    interests = None
    if body.profile_id:
        _, prof = load_profile(body.profile_id, x_profile_token)
        interests = prof.interests  # only interests are sent to the LLM, never grades/history
    intent, parser, degraded = interpret(body.query, interests, body.use_llm)
    return {"intent": intent.model_dump(), "parser": parser, "degraded": degraded}


class RecommendationRequest(BaseModel):
    profile_id: str
    query: str = Field("", max_length=500)
    intent: Intent | None = Field(None, description="Edited intent; when present the query is not re-parsed")
    use_llm: bool = True
    plan_offering_ids: list[str] = Field(default_factory=list)
    strict_prerequisites: bool = False
    check_schedule: bool | None = None


@app.post("/recommendations")
def recommendations(body: RecommendationRequest, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, prof = load_profile(body.profile_id, x_profile_token)
    if body.intent is not None:
        intent, parser, degraded = body.intent, "edited", None
    elif body.query.strip():
        intent, parser, degraded = interpret(body.query, prof.interests, body.use_llm)
    else:
        raise HTTPException(422, "provide a query or an intent")
    key = ("rec", row.id, row.version, snap.id, snap.rules_version, intent.model_dump_json(),
           tuple(sorted(body.plan_offering_ids)), body.strict_prerequisites, body.check_schedule)
    out = _cache.get(key)
    if out is None:
        out = db.dumps(recommend(prof, snap, intent, parser, degraded, body.plan_offering_ids,
                                 body.strict_prerequisites, body.check_schedule))
        _cache.put(key, out)
    # Cache the calculation, but give every invocation its own audit record and
    # parser metadata (an edited intent can share the same calculation).
    out = {**out, "request_id": db.new_id("rec"), "parser": parser, "degraded": degraded,
           "profile_version": row.version, "query": body.query}
    with db.session() as s:
        s.add(db.RunRow(id=out["request_id"], profile_id=row.id, profile_version=row.version,
                        dataset_version=snap.id, rules_version=snap.rules_version, query=body.query,
                        intent=out["parsed_intent"], parser=parser,
                        summary={"status": out["status"], "matches": [m["course_code"] for m in out["matches"]],
                                 "unverified": [m["course_code"] for m in out["unverified"]]}, created_at=db.now()))
        s.commit()
    return out


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------
class PrefsIn(BaseModel):
    no_early_classes: bool = False
    free_days: list[str] = Field(default_factory=list)
    compact: bool = False
    hard: bool = True


def _prefs(p: PrefsIn | None) -> Prefs:
    p = p or PrefsIn()
    return Prefs(no_early=p.no_early_classes, no_early_hard=p.no_early_classes and p.hard, free_days=p.free_days,
                 free_days_hard=bool(p.free_days) and p.hard, compact=p.compact)


class PlanRequest(BaseModel):
    profile_id: str
    offering_ids: list[str]
    locked_sections: dict[str, list[str]] = Field(default_factory=dict)
    preferences: PrefsIn | None = None
    name: str = "Semester plan"


@app.post("/plans/validate")
def plans_validate(body: PlanRequest, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, prof = load_profile(body.profile_id, x_profile_token)
    st = academic_state(prof, snap)
    res = validate_plan(body.offering_ids, prof, snap, st["scope"], st["history"], _prefs(body.preferences),
                        body.locked_sections)
    return db.dumps({"profile_version": row.version, "dataset_version": snap.id, "rules_version": snap.rules_version,
                     **res})


@app.post("/plans/solve")
def plans_solve(body: PlanRequest, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    load_profile(body.profile_id, x_profile_token)
    missing = [o for o in body.offering_ids if o not in snap.offerings]
    if missing:
        raise HTTPException(404, f"unknown offering ids {missing}")
    offs = [snap.offerings[o] for o in body.offering_ids]
    return db.dumps(solve(offs, _prefs(body.preferences), body.locked_sections))


@app.post("/plans", status_code=201)
def plans_save(body: PlanRequest, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, prof = load_profile(body.profile_id, x_profile_token)
    st = academic_state(prof, snap)
    res = db.dumps(validate_plan(body.offering_ids, prof, snap, st["scope"], st["history"], _prefs(body.preferences),
                                 body.locked_sections))
    plan = db.PlanRow(id=db.new_id("plan"), profile_id=row.id, name=body.name, profile_version=row.version,
                      dataset_version=snap.id, rules_version=snap.rules_version, offering_ids=body.offering_ids,
                      selection={"locked_sections": body.locked_sections,
                                 "preferences": (body.preferences or PrefsIn()).model_dump()},
                      validation=res, created_at=db.now())
    with db.session() as s:
        s.add(plan)
        s.commit()
    return _plan_out(plan, row, snap)


def _plan_out(plan: db.PlanRow, prof_row: db.ProfileRow, snap: Snapshot) -> dict:
    stale = []
    if plan.profile_version != prof_row.version:
        stale.append(f"profile changed (saved at v{plan.profile_version}, now v{prof_row.version})")
    if plan.dataset_version != snap.id:
        stale.append(f"dataset snapshot changed ({plan.dataset_version} -> {snap.id})")
    if plan.rules_version != snap.rules_version:
        stale.append("rules changed")
    return {"id": plan.id, "name": plan.name, "profile_id": plan.profile_id, "offering_ids": plan.offering_ids,
            "selection": plan.selection, "profile_version": plan.profile_version,
            "dataset_version": plan.dataset_version, "rules_version": plan.rules_version,
            "created_at": plan.created_at, "stale": bool(stale), "stale_reasons": stale,
            "validation": plan.validation}


@app.get("/profiles/{profile_id}/plans")
def list_plans(profile_id: str, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    row, _ = load_profile(profile_id, x_profile_token)
    with db.session() as s:
        plans = s.scalars(db.select(db.PlanRow).where(db.PlanRow.profile_id == profile_id)
                          .order_by(db.PlanRow.created_at.desc())).all()
    return {"plans": [_plan_out(p, row, snap) for p in plans]}


@app.get("/plans/{plan_id}")
def get_plan(plan_id: str, revalidate: bool = True, x_profile_token: str | None = Header(None)):
    snap = snapshot()
    with db.session() as s:
        plan = s.get(db.PlanRow, plan_id)
    if not plan:
        raise HTTPException(404, "plan not found")
    row, prof = load_profile(plan.profile_id, x_profile_token)
    out = _plan_out(plan, row, snap)
    if revalidate and out["stale"]:
        st = academic_state(prof, snap)
        valid_ids = [o for o in plan.offering_ids if o in snap.offerings]
        out["current_validation"] = db.dumps(validate_plan(valid_ids, prof, snap, st["scope"], st["history"],
                                                           _prefs(PrefsIn(**plan.selection.get("preferences", {}))),
                                                           plan.selection.get("locked_sections")))
        out["missing_offerings"] = [o for o in plan.offering_ids if o not in snap.offerings]
    return out


# ---------------------------------------------------------------------------
# administration
# ---------------------------------------------------------------------------
@app.get("/admin/ingestion-runs", dependencies=[Depends(require_admin)])
def admin_runs():
    store = get_store()
    snap = snapshot()
    runs = []
    for sid in store.list():
        m = store.get(sid)
        runs.append({"snapshot_id": sid, "current": sid == snap.id, "parser_version": m.manifest["parser_version"],
                     "rules_version": m.rules_version, "term": m.term["label"], "coverage": m.coverage})
    return {"current": snap.id, "snapshots": runs}


@app.get("/admin/review-queue", dependencies=[Depends(require_admin)])
def admin_review(kind: str | None = None, severity: str | None = None, q: str | None = None,
                 offset: int = 0, limit: int = Query(50, le=500)):
    snap = snapshot()
    items = [r for r in snap.review_queue if (not kind or r["kind"] == kind) and (not severity or r["severity"] == severity)
             and (not q or q.lower() in (r["subject"] + r["message"]).lower())]
    return {"total": len(items), "items": items[offset: offset + limit]}


@app.get("/admin/rules", dependencies=[Depends(require_admin)])
def admin_rules():
    return snapshot().rules_doc


@app.post("/admin/datasets/{snapshot_id}/publish", dependencies=[Depends(require_admin)])
def admin_publish(snapshot_id: str):
    from ingestion.checks import verify  # publication checks (no PDF libraries needed)
    store = get_store()
    path = store.data_dir / snapshot_id
    if not path.is_dir():
        raise HTTPException(404, "snapshot not found")
    problems = verify(path)
    if problems:
        raise HTTPException(422, {"message": "publication checks failed", "problems": problems[:20]})
    previous = store.current_id()
    store.set_current(snapshot_id)
    _cache.d.clear()
    return {"published": snapshot_id, "previous": previous,
            "note": "Saved plans created against another snapshot are now reported as stale."}


# serve the built dashboard if present (single-process deployment)
_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="dashboard")
