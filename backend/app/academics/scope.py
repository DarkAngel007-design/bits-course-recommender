"""Curriculum/scope resolution (FR-05)."""
from __future__ import annotations

from ..catalog.store import Snapshot
from .models import StudentProfile


def resolve_scope(profile: StudentProfile, snap: Snapshot) -> dict:
    """Return {status, curriculum_id, reasons[], evidence[]}.

    status: resolved | attested | unresolved | unsupported
      resolved   - curriculum applicability established from supplied evidence
      attested   - student confirmed the chart applies; results are labelled provisional
      unresolved - applicability not established; requirement claims are withheld
      unsupported- outside the supplied data (campus/cohort/programme)
    """
    reasons: list[str] = []
    ev: list[str] = []
    term = snap.term
    if profile.campus.strip().lower() != term["campus"].lower():
        return {"status": "unsupported", "curriculum_id": None, "evidence": [],
                "reasons": [f"Only the {term['campus']} campus timetable ({term['label']}) was supplied; "
                            f"{profile.campus} offerings cannot be checked."]}
    if profile.admission_year >= 2026:
        return {"status": "unsupported", "curriculum_id": None, "evidence": snap.rule_evidence("credit_hour_cohort"),
                "reasons": ["2026 admissions follow the new credit-hour framework; no applicable curriculum was "
                            "supplied, so requirements cannot be derived from the unit-based bulletin charts."]}
    unknown = [p for p in profile.programmes if p not in snap.programmes]
    if unknown:
        return {"status": "unsupported", "curriculum_id": None, "evidence": [],
                "reasons": [f"Unknown programme id(s): {', '.join(unknown)}"]}
    if len(profile.programmes) == 1:
        cid = next((c for c, cur in snap.curricula.items()
                    if cur["type"] == "single" and cur["programmes"] == profile.programmes), None)
    else:
        want = set(profile.programmes)
        cid = next((c for c, cur in snap.curricula.items() if cur["type"] == "dual" and set(cur["programmes"]) == want),
                   None)
    if cid is None:
        return {"status": "unsupported", "curriculum_id": None, "evidence": [],
                "reasons": ["No semester-wise chart in the supplied bulletin for this programme combination."]}
    cur = snap.curricula[cid]
    ev = list(cur["evidence"])
    if profile.admission_year in cur["cohort"]["resolved_admission_years"]:
        return {"status": "resolved", "curriculum_id": cid, "evidence": ev,
                "reasons": [f"{cur['name']} chart from the {cur['version']} bulletin (unit framework) applies to the "
                            f"{profile.admission_year} admission cohort."]}
    reasons.append(f"The supplied bulletin is the 2025-26 edition; it does not state that its charts apply to "
                   f"students admitted in {profile.admission_year}.")
    if profile.curriculum_attested:
        reasons.append("Student attested that this chart applies; requirement results are provisional.")
        return {"status": "attested", "curriculum_id": cid, "evidence": ev, "reasons": reasons}
    reasons.append("Confirm the chart applies (curriculum_attested) to see provisional requirements.")
    return {"status": "unresolved", "curriculum_id": cid, "evidence": ev, "reasons": reasons}
