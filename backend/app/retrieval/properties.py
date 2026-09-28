"""Evaluate structured course properties from handout records.

Every evaluation returns a PropertyResult with a tri-state value:
  True  - supported by explicit evidence
  False - contradicted by explicit evidence
  None  - unknown (missing handout, incomplete table, conflicting handouts, no statement)
Unknown never satisfies a hard filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..catalog.store import Snapshot


@dataclass
class PropertyResult:
    value: bool | None
    explanation: str
    evidence: list[str] = field(default_factory=list)
    score: float | None = None  # for soft preferences, 0..1

    def to_dict(self) -> dict:
        return {"value": self.value, "explanation": self.explanation, "evidence": self.evidence, "score": self.score}


COMPONENT_LABEL = {"quiz": "quizzes", "project": "a project component", "lab": "a lab component",
                   "assignment": "assignments", "presentation": "presentations/seminars", "viva": "a viva",
                   "case_study": "case studies", "midsem": "a mid-semester exam", "compre": "a comprehensive exam"}


def _handouts(code: str, snap: Snapshot) -> list[dict]:
    return [h for h in snap.handouts_for(code) if h.get("extraction_status") != "no_text_layer"]


def _combine(results: list[PropertyResult], what: str) -> PropertyResult:
    vals = {r.value for r in results}
    if len(vals) == 1:
        r = results[0]
        if len(results) > 1:
            r = PropertyResult(r.value, r.explanation + f" (consistent across {len(results)} handouts)",
                               [e for x in results for e in x.evidence], r.score)
        return r
    return PropertyResult(None, f"Handouts listing this course disagree on {what}; cannot verify.",
                          [e for x in results for e in x.evidence])


def exam_presence(code: str, snap: Snapshot, kind: str) -> PropertyResult:
    hs = _handouts(code, snap)
    label = "mid-semester exam" if kind == "midsem" else "comprehensive exam"
    if not hs:
        return PropertyResult(None, f"No handout supplied; presence of a {label} cannot be verified.")
    out = []
    for h in hs:
        p = h.get(kind) or {}
        ev = (h.get("evidence") or {}).get("evaluation", [])
        if p.get("value") is True:
            nat = p.get("nature")
            extra = f", {nat.replace('_', ' ')}" if nat else ""
            out.append(PropertyResult(True, f"Handout lists a {label} ({p.get('weight'):g}%{extra}).", ev))
        elif p.get("value") is False:
            comps = ", ".join(f"{c['name']} {c['weight']:g}%" for c in h["evaluation"]["components"][:6])
            out.append(PropertyResult(False, f"Evaluation table totals 100% with no {label}: {comps}.", ev))
        else:
            out.append(PropertyResult(None, f"Handout evaluation table could not be read completely; {label} "
                                      "presence unverified.", ev))
    return _combine(out, f"the {label}")


def component_presence(code: str, snap: Snapshot, comp: str) -> PropertyResult:
    hs = _handouts(code, snap)
    label = COMPONENT_LABEL.get(comp, comp)
    if not hs:
        return PropertyResult(None, f"No handout supplied; cannot verify {label}.")
    out = []
    for h in hs:
        p = (h.get("components_present") or {}).get(comp) or {}
        ev = (h.get("evidence") or {}).get("evaluation", [])
        if p.get("value") is True:
            out.append(PropertyResult(True, f"Evaluation includes {label} ({p.get('weight'):g}%).", ev))
        elif p.get("value") is False:
            out.append(PropertyResult(False, f"Complete evaluation table has no {label}.", ev))
        else:
            out.append(PropertyResult(None, f"Evaluation table incomplete; {label} unverified.", ev))
    return _combine(out, label)


def attendance_none(code: str, snap: Snapshot) -> PropertyResult:
    """'No attendance requirement' needs an explicit statement. Absence of a clause is NOT a match."""
    hs = _handouts(code, snap)
    reg_ev = snap.rule_evidence("no_institute_attendance_minimum") + snap.rule_evidence("attendance_recorded")
    if not hs:
        return PropertyResult(None, "No handout supplied; attendance policy unknown.", reg_ev)
    out = []
    for h in hs:
        a = h.get("attendance") or {}
        ev = (h.get("evidence") or {}).get("attendance", [])
        st = a.get("status")
        if st == "explicitly_not_required":
            out.append(PropertyResult(True, f"Handout: \"{a['not_mandatory_statements'][0][:140]}\"", ev + reg_ev))
        elif st == "requirement_stated":
            why = []
            if a.get("min_percentage"):
                why.append(f"minimum {a['min_percentage']}% attendance")
            if a.get("marks_component"):
                why.append("attendance-linked marks")
            if a.get("mandatory_statements"):
                why.append(f"\"{a['mandatory_statements'][0][:110]}\"")
            out.append(PropertyResult(False, "Handout states an attendance requirement: " + "; ".join(why), ev))
        elif st == "none_found":
            out.append(PropertyResult(None, "Handout has no attendance clause and no attendance-weighted component. "
                                      "The Academic Regulations set no institute-wide minimum, but Handout Part I says attendance "
                                      "is recorded - absence of a clause is not verified absence of a requirement.",
                                      (h.get("evidence") or {}).get("evaluation", []) + reg_ev))
        else:
            stmt = (a.get("statements") or [""])[0][:140]
            out.append(PropertyResult(None, "Attendance is mentioned without a clear requirement: " +
                                      (f"\"{stmt}\"" if stmt else "unverified."), ev))
    return _combine(out, "attendance")


def makeup_profile(code: str, snap: Snapshot) -> PropertyResult:
    """Soft preference 'lenient make-up': a transparent mapping, not a judgement.
    Score = components the policy mentions make-up for, minus stated restrictions."""
    hs = _handouts(code, snap)
    if not hs:
        return PropertyResult(None, "No handout supplied; make-up policy unknown.", score=None)
    h = hs[0]
    m = h.get("makeup") or {}
    ev = (h.get("evidence") or {}).get("makeup", [])
    if m.get("status") != "stated":
        return PropertyResult(None, "Handout Part II states no make-up policy (Part I default: make-up only in "
                              "genuine cases of absence).", snap.rule_evidence("makeup_default"), score=None)
    restrictions = []
    if m.get("no_makeup_at_all"):
        restrictions.append("no make-up")
    if m.get("no_makeup_for"):
        restrictions.append("no make-up for " + ", ".join(m["no_makeup_for"]))
    if "restricted_cases" in m.get("conditions", []):
        restrictions.append("only in genuine/serious cases")
    if "medical" in m.get("conditions", []):
        restrictions.append("medical grounds mentioned")
    if "prior_permission" in m.get("conditions", []):
        restrictions.append("prior permission required")
    if "documentary_proof" in m.get("conditions", []):
        restrictions.append("documentary proof required")
    score = max(0.0, 1.0 - 0.2 * len(restrictions) - (0.6 if m.get("no_makeup_at_all") else 0))
    text = (m.get("text") or [""])[0][:220]
    return PropertyResult(None, f"Make-up policy: \"{text}\"" + (f" (restrictions: {'; '.join(restrictions)})"
                                                                  if restrictions else ""), ev, score=round(score, 2))


def project_weight(code: str, snap: Snapshot) -> PropertyResult:
    hs = _handouts(code, snap)
    if not hs:
        return PropertyResult(None, "No handout supplied; evaluation pattern unknown.", score=None)
    h = hs[0]
    comps = h.get("evaluation", {}).get("components", [])
    ev = (h.get("evidence") or {}).get("evaluation", [])
    w = sum(c["weight"] or 0 for c in comps if c["type"] in ("project", "report", "presentation", "case_study"))
    if not comps:
        return PropertyResult(None, "Evaluation scheme not parsed.", ev, score=None)
    names = ", ".join(f"{c['name']} {c['weight']:g}%" for c in comps if c["type"] in
                      ("project", "report", "presentation", "case_study") and c["weight"])
    return PropertyResult(w > 0 if h["evaluation"]["complete"] else (True if w > 0 else None),
                          (f"Project/report/presentation components carry {w:g}% ({names})" if w else
                           "No project, report or presentation component in the evaluation scheme"), ev,
                          score=min(1.0, w / 50.0))


def open_book_weight(code: str, snap: Snapshot) -> PropertyResult:
    hs = _handouts(code, snap)
    if not hs:
        return PropertyResult(None, "No handout supplied.", score=None)
    h = hs[0]
    ob = h.get("open_book_weight")
    ev = (h.get("evidence") or {}).get("evaluation", [])
    if ob is None:
        return PropertyResult(None, "Open-book share not determinable from the handout.", ev, score=None)
    return PropertyResult(ob > 0, f"Open-book/take-home components: {ob:g}% of evaluation.", ev, score=min(1.0, ob / 60))


def exam_weight(code: str, snap: Snapshot) -> PropertyResult:
    hs = _handouts(code, snap)
    if not hs or not hs[0].get("evaluation", {}).get("complete"):
        return PropertyResult(None, "Exam weight not determinable.", score=None)
    comps = hs[0]["evaluation"]["components"]
    w = sum(c["weight"] or 0 for c in comps if c["type"] in ("midsem", "compre"))
    return PropertyResult(None, f"Mid-sem + comprehensive exams carry {w:g}% of the grade.",
                          (hs[0].get("evidence") or {}).get("evaluation", []), score=round(1 - w / 100, 2))


def instructor_match(code: str, snap: Snapshot, name: str, offering: dict) -> PropertyResult:
    n = name.lower()
    ins = [i for s in offering["sections"] for i in s["instructors"]]
    hit = [i for i in ins if n in i.lower()]
    return PropertyResult(bool(hit), f"Instructors include {hit[0]}" if hit else "Instructor not listed for any section.",
                          offering["evidence"][:1])
