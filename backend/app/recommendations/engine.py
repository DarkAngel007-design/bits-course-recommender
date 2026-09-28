"""Recommendation orchestration (bounded, deterministic after intent parsing).

  profile -> scope -> history -> requirements -> eligibility over ALL current offerings
  -> category + hard-filter evaluation (unknown never passes) -> ranking (rank-v1)
  -> optional schedule check against the draft plan -> final revalidation -> explanations

Results are split into:
  matches        eligible + every hard filter verified          (the only "recommendations")
  unverified     academically possible but eligibility or a hard filter could not be verified
  alternatives   clearly labelled near-misses (never silently relaxed)
"""
from __future__ import annotations

import time
import uuid

from ..academics.eligibility import evaluate_offering
from ..academics.history import interpret as interpret_history
from ..academics.models import StudentProfile
from ..academics.requirements import compute_requirements
from ..academics.scope import resolve_scope
from ..catalog.store import Snapshot
from ..retrieval import properties as P
from ..retrieval.search import get_index, tokenize, topic_relevance
from ..scheduling.solver import Prefs, check_candidate
from .intent import Intent

RANKING_VERSION = "rank-v1"
# rank-v1 = 3.0*topic_relevance + 1.0*normalised_bm25 + sum(weight*soft_score) + 0.75*fills_remaining_need
W_TOPIC, W_TEXT, W_NEED = 3.0, 1.0, 0.75


def academic_state(profile: StudentProfile, snap: Snapshot) -> dict:
    scope = resolve_scope(profile, snap)
    history = interpret_history(profile, snap)
    req = compute_requirements(profile, snap, scope, history)
    return {"scope": scope, "history": history, "requirements": req}


def _category_ok(category: str, elig: dict) -> tuple[bool | None, str, dict | None]:
    contrib = elig["contribution"]
    if category == "ANY":
        return True, "", contrib[0] if contrib else None
    want = {"CDC": "DC"}.get(category, category)
    hit = next((c for c in contrib if c["category"] == want), None)
    if category == "OPEL" and hit and any(c["kind"] == "named" for c in contrib):
        return False, "named course in your curriculum, not an elective", None
    if not hit:
        return False, f"not a {category} for your programme", None
    return True, "", hit


def _eval_hard(f, offering: dict, snap: Snapshot) -> P.PropertyResult:
    code = offering["course_code"]
    prop, val = f.property, f.value
    if prop == "midsem_present":
        r = P.exam_presence(code, snap, "midsem")
        return P.PropertyResult(None if r.value is None else (r.value == bool(val)), r.explanation, r.evidence)
    if prop == "compre_present":
        r = P.exam_presence(code, snap, "compre")
        return P.PropertyResult(None if r.value is None else (r.value == bool(val)), r.explanation, r.evidence)
    if prop == "attendance_none":
        return P.attendance_none(code, snap)
    if prop in ("has_component", "no_component"):
        r = P.component_presence(code, snap, str(val))
        want = prop == "has_component"
        return P.PropertyResult(None if r.value is None else (r.value == want), r.explanation, r.evidence)
    if prop == "department":
        ok = code.split(" ")[0] == str(val).upper()
        return P.PropertyResult(ok, f"Course prefix {code.split(' ')[0]}", [])
    if prop == "level":
        ok = code.split(" ")[1][0] == str(val).upper()
        return P.PropertyResult(ok, "Higher degree (G-level) course" if ok else "Not a higher degree course", [])
    if prop == "units":
        u = offering["credit"]["value"]
        return P.PropertyResult(u == int(val), f"{u} {offering['credit']['system']}", offering["evidence"][:1])
    if prop == "instructor":
        return P.instructor_match(code, snap, str(val), offering)
    if prop == "course_code":
        return P.PropertyResult(code == str(val).upper(), code, [])
    return P.PropertyResult(None, f"Unsupported property {prop}", [])


def _eval_soft(pref, offering: dict, snap: Snapshot) -> P.PropertyResult | None:
    code = offering["course_code"]
    if pref.property == "project_based":
        return P.project_weight(code, snap)
    if pref.property == "lenient_makeup":
        return P.makeup_profile(code, snap)
    if pref.property == "open_book":
        return P.open_book_weight(code, snap)
    if pref.property == "low_exam_weight":
        return P.exam_weight(code, snap)
    return None


def recommend(profile: StudentProfile, snap: Snapshot, intent: Intent, parser: str, degraded: str | None = None,
              plan_offering_ids: list[str] | None = None, strict_prerequisites: bool = False,
              check_schedule: bool | None = None) -> dict:
    t0 = time.time()
    st = academic_state(profile, snap)
    scope, history, req = st["scope"], st["history"], st["requirements"]
    base = {
        "request_id": "rec_" + uuid.uuid4().hex[:12],
        "parsed_intent": intent.model_dump(),
        "parser": parser,
        "degraded": degraded,
        "versions": {"dataset": snap.id, "rules": snap.rules_version, "ranking": RANKING_VERSION},
        "scope": {k: v for k, v in scope.items()},
        "requirements_summary": _req_summary(req),
    }
    if scope["status"] == "unsupported":
        return {**base, "status": "unsupported_scope", "matches": [], "unverified": [], "alternatives": [],
                "message": " ".join(scope["reasons"]), "stats": {}}

    plan_offerings = [snap.offerings[o] for o in (plan_offering_ids or []) if o in snap.offerings]
    plan_codes = {o["course_code"] for o in plan_offerings}
    sched_prefs = Prefs(no_early=intent.schedule.no_early_classes, no_early_hard=intent.schedule.no_early_classes,
                        free_days=intent.schedule.free_days, free_days_hard=bool(intent.schedule.free_days),
                        compact=intent.schedule.compact)
    do_sched = check_schedule if check_schedule is not None else bool(
        plan_offerings or intent.schedule.no_early_classes or intent.schedule.free_days)

    index = get_index(snap)
    terms = [t for s in intent.search_terms for t in tokenize(s)]
    stats = {"offerings_considered": 0, "ineligible": 0, "category_excluded": 0, "hard_filter_failed": {},
             "hard_filter_unknown": {}, "schedule_excluded": 0}
    best_by_code: dict[str, dict] = {}
    for off in snap.offerings.values():
        if off["course_code"] in plan_codes:
            continue
        stats["offerings_considered"] += 1
        elig = evaluate_offering(off, profile, snap, scope, history, req if req.get("available") else None,
                                 strict_prerequisites)
        rank_status = {"eligible": 0, "needs_verification": 1, "ineligible": 2}[elig["status"]]
        prev = best_by_code.get(off["course_code"])
        if prev is None or rank_status < prev["_rank"]:
            best_by_code[off["course_code"]] = {"offering": off, "elig": elig, "_rank": rank_status}

    candidates = []
    for code, x in best_by_code.items():
        off, elig = x["offering"], x["elig"]
        if elig["status"] == "ineligible":
            stats["ineligible"] += 1
            continue
        ok, why, contrib = _category_ok(intent.category, elig)
        if not ok:
            stats["category_excluded"] += 1
            continue
        course = snap.courses.get(code, {})
        hard = []
        for f in intent.hard_filters:
            r = _eval_hard(f, off, snap)
            hard.append({"filter": f.model_dump(), **r.to_dict()})
            key = f"{f.property}={f.value}"
            if r.value is False:
                stats["hard_filter_failed"][key] = stats["hard_filter_failed"].get(key, 0) + 1
            elif r.value is None:
                stats["hard_filter_unknown"][key] = stats["hard_filter_unknown"].get(key, 0) + 1
        if any(h["value"] is False for h in hard):
            continue
        rel, topic_hits = topic_relevance(course, intent.topics)
        bm = index.bm25(code, terms) if terms else 0.0
        soft = []
        soft_total = 0.0
        for pref in intent.soft_preferences:
            r = _eval_soft(pref, off, snap)
            if r is None:
                continue
            s = r.score or 0.0
            soft_total += pref.weight * s
            soft.append({"preference": pref.model_dump(), **r.to_dict()})
        candidates.append({"offering": off, "elig": elig, "contrib": contrib, "hard": hard, "soft": soft,
                           "topic_relevance": rel, "topic_hits": topic_hits, "bm25": bm, "soft_total": soft_total})

    # topical intent: drop candidates with no textual/topic relevance at all
    topical = bool((intent.topics and intent.topic_mode == "filter") or terms)
    max_bm = max([c["bm25"] for c in candidates] + [1e-9])
    for c in candidates:
        c["text_relevance"] = c["bm25"] / max_bm if terms else 0.0
        need = 1.0 if (c["contrib"] and c["contrib"].get("remaining_need")) else 0.0
        c["score"] = round(W_TOPIC * c["topic_relevance"] + W_TEXT * c["text_relevance"] + c["soft_total"] + W_NEED * need, 4)
        # floor: a single passing keyword mention is not an interest match
        c["relevant"] = (c["topic_relevance"] >= 0.1 or c["text_relevance"] > 0.05) if topical else True
    relevant = [c for c in candidates if c["relevant"]]
    irrelevant_count = len(candidates) - len(relevant)
    relevant.sort(key=lambda c: (-c["score"], c["offering"]["course_code"]))

    matches, unverified, alternatives = [], [], []
    for c in relevant:
        hard_unknown = [h for h in c["hard"] if h["value"] is None]
        verified = c["elig"]["status"] == "eligible" and not hard_unknown
        c["schedule"] = {"status": "unchecked"}
        if do_sched:
            sch = check_candidate(c["offering"], plan_offerings, sched_prefs)
            c["schedule"] = sch
            if sch["status"] == "clash":
                stats["schedule_excluded"] += 1
                if len(alternatives) < 3:
                    alternatives.append(_card(c, snap, req, intent, "schedule_clash",
                                              "Academically valid but no section combination fits your plan/time "
                                              "preferences" + (f": {'; '.join(sch['excluded_by_preferences'][:2])}"
                                                               if sch.get("excluded_by_preferences") else "")))
                continue
        if verified and len(matches) < intent.count:
            matches.append(_card(c, snap, req, intent, "match"))
        elif not verified and len(unverified) < intent.count:
            reason = "; ".join([f"{h['filter']['property']}: {h['explanation']}" for h in hard_unknown] +
                               [ch["message"] for ch in c["elig"]["checks"] if ch["status"] == "unknown"])
            unverified.append(_card(c, snap, req, intent, "unverified", reason))

    # alternatives: when topics filtered everything or matches are short, show clearly-labelled options
    if len(matches) < intent.count and topical and irrelevant_count:
        pool = [c for c in candidates if not c["relevant"] and c["elig"]["status"] == "eligible"
                and not any(h["value"] is None for h in c["hard"])]
        pool.sort(key=lambda c: (-c["score"], c["offering"]["course_code"]))
        for c in pool[: max(0, min(3, intent.count - len(matches)))]:
            alternatives.append(_card(c, snap, req, intent, "alternative_off_topic",
                                      "Meets your category and filters but has no detected match to your interest "
                                      "topics"))
    binding = None
    if not matches:
        binding = _binding(stats, intent, len(candidates), len(relevant))
    # final revalidation: every match re-evaluated against the (unchanged) academic state
    revalidated = []
    for m in matches:
        re_ev = evaluate_offering(snap.offerings[m["offering_id"]], profile, snap, scope, history,
                                  req if req.get("available") else None, strict_prerequisites)
        if re_ev["status"] == "eligible":
            revalidated.append(m)
        else:  # defensive: never return a match that fails revalidation
            unverified.append({**m, "kind": "unverified", "reason": "failed final revalidation"})
    matches = revalidated
    status = "ok" if matches else "no_verified_match"
    msg = _headline(matches, unverified, intent, scope, binding)
    return {**base, "status": status, "message": msg, "matches": matches, "unverified": unverified,
            "alternatives": alternatives, "binding_filters": binding,
            "schedule_check": "checked" if do_sched else "unchecked",
            "stats": {**stats, "candidates_after_filters": len(candidates), "relevant": len(relevant),
                      "elapsed_ms": int((time.time() - t0) * 1000)}}


def _req_summary(req: dict) -> dict | None:
    if not req.get("available"):
        return {"available": False, "reasons": req.get("reasons")}
    return {"available": True, "curriculum": req["curriculum_name"], "provisional": req["provisional"],
            "categories": [{"key": c["key"], "label": c["label"], "status": c["status"],
                            "remaining": c["remaining"], "remaining_after_projected": c["remaining_after_projected"]}
                           for c in req["categories"]]}


def _binding(stats: dict, intent: Intent, n_cand: int, n_rel: int) -> dict:
    out = []
    for k, v in sorted(stats["hard_filter_failed"].items(), key=lambda x: -x[1]):
        out.append({"filter": k, "excluded": v, "kind": "failed"})
    for k, v in sorted(stats["hard_filter_unknown"].items(), key=lambda x: -x[1]):
        out.append({"filter": k, "unverifiable": v, "kind": "unknown"})
    if n_cand and not n_rel:
        out.append({"filter": "topics/search terms", "excluded": n_cand, "kind": "no_topic_match"})
    if stats["category_excluded"]:
        out.append({"filter": f"category={intent.category}", "excluded": stats["category_excluded"], "kind": "category"})
    if stats["schedule_excluded"]:
        out.append({"filter": "schedule", "excluded": stats["schedule_excluded"], "kind": "schedule"})
    return {"filters": out, "ineligible_offerings": stats["ineligible"]}


def _headline(matches, unverified, intent, scope, binding) -> str:
    prov = " Requirements are provisional (curriculum applicability attested, not verified)." \
        if scope["status"] == "attested" else ""
    if scope["status"] == "unresolved":
        prov = " Your cohort's curriculum applicability is not established, so no course can be fully verified."
    if matches:
        return f"{len(matches)} verified recommendation(s)." + prov + (
            f" {len(unverified)} more need verification." if unverified else "")
    if unverified:
        return "No course could be fully verified against your request; the closest candidates need verification " \
               "(shown separately)." + prov
    return "No offered course satisfies the request. See the binding filters below; nothing was relaxed." + prov


def _card(c: dict, snap: Snapshot, req: dict, intent: Intent, kind: str, reason: str | None = None) -> dict:
    off, elig = c["offering"], c["elig"]
    code = off["course_code"]
    course = snap.courses.get(code, {})
    contrib = c["contrib"] or (elig["contribution"][0] if elig["contribution"] else None)
    hs = snap.handouts_for(code)
    h = hs[0] if hs else None
    lecture = next((s for s in off["sections"] if s["type"] == "Lecture" and s["status"] == "active"), None)
    exams = {e["type"]: e for e in off["exams"]}
    facts = {}
    if h:
        facts = {
            "evaluation": [{"name": x["name"], "weight": x["weight"], "type": x["type"], "nature": x["nature"]}
                           for x in h.get("evaluation", {}).get("components", [])],
            "evaluation_complete": h.get("evaluation", {}).get("complete"),
            "midsem": (h.get("midsem") or {}).get("value"),
            "attendance_status": (h.get("attendance") or {}).get("status"),
            "makeup": (h.get("makeup") or {}).get("text", [None])[0] if (h.get("makeup") or {}).get("text") else None,
            "handout_evidence": [e for v in (h.get("evidence") or {}).values() for e in v][:6],
        }
    why = []
    for t in c["topic_hits"]:
        kws = sorted({x["keyword"] for x in t["hits"]})[:4]
        fields = sorted({x["field"].replace("_", " ") for x in t["hits"]})
        weak = "Weak match to" if c["topic_relevance"] < 0.35 else "Matches"
        why.append(f"{weak} '{snap.topics[t['topic']]['label']}' via {', '.join(kws)} (in {', '.join(fields)})")
    if c.get("text_relevance", 0) > 0.05:
        why.append(f"Course text matches your terms: {', '.join(intent.search_terms[:4])}")
    for hres in c["hard"]:
        if hres["value"] is True:
            why.append(f"✓ {hres['filter'].get('source_text') or hres['filter']['property']}: {hres['explanation']}")
    for s in c["soft"]:
        if s.get("score") is not None:
            why.append(f"~ {s['preference'].get('source_text') or s['preference']['property']}: {s['explanation']}")
    unknowns = [f"{hr['filter'].get('source_text') or hr['filter']['property']}: {hr['explanation']}"
                for hr in c["hard"] if hr["value"] is None]
    unknowns += [ch["message"] for ch in elig["checks"] if ch["status"] == "unknown"]
    caveats = [ch["message"] for ch in elig["checks"] if ch.get("caveat")]
    return {
        "kind": kind,
        "reason": reason,
        "offering_id": off["id"],
        "course_code": code,
        "title": course.get("title") or off["title_timetable"].title(),
        "term": snap.term["label"],
        "credit": {"value": off["credit"]["value"], "system": off["credit"]["system"],
                   "L": off["credit"]["L"], "P": off["credit"]["P"]},
        "requirement": contrib,
        "all_contributions": elig["contribution"],
        "eligibility": {"status": elig["status"], "decisive": elig["decisive"][:4],
                        "approvals_needed": elig["approvals_needed"]},
        "matched_hard_filters": [x for x in c["hard"] if x["value"] is True],
        "soft_preferences": c["soft"],
        "why": why,
        "unknowns": unknowns,
        "caveats": caveats,
        "score": c["score"],
        "score_breakdown": {"topic": round(c["topic_relevance"], 3), "text": round(c.get("text_relevance", 0), 3),
                            "soft": round(c["soft_total"], 3)},
        "facts": facts,
        "instructor_in_charge": (lecture or {}).get("instructor_in_charge"),
        "lecture_slot": (lecture or {}).get("raw_slot"),
        "exams": {k: {"date": v.get("date"), "session": v.get("session"), "status": v["status"]} for k, v in exams.items()},
        "schedule": c.get("schedule", {"status": "unchecked"}),
        "evidence": list(dict.fromkeys(off["evidence"][:1] + (course.get("evidence", {}).get("bulletin") or [])[:1]
                                       + facts.get("handout_evidence", [])[:3]
                                       + [e for x in c["hard"] for e in x["evidence"]][:4]
                                       + ((contrib or {}).get("evidence") or [])[:2])),
    }
