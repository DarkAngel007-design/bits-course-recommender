"""Attempt-history interpretation (FR-07).

Semantics (reviewed rules):
  * course_cleared_by_grade   - a course is cleared when a grade is obtained; reports do not clear.
  * latest_performance_governs- with several attempts, the latest outcome decides.
  * An in-progress attempt is *projected*, never earned. If an earlier attempt already cleared
    the course (a repeat to improve the grade), the course stays cleared until the new outcome.
  * Equivalence is honoured only for pairs listed as equivalent in the supplied documents.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..catalog.store import Snapshot
from .models import StudentProfile


@dataclass
class CourseState:
    code: str
    status: str  # cleared | in_progress | not_cleared | grade_missing | unknown_grade
    grade: str | None
    units: int | None
    credit_system: str
    attempts: int
    repeat_in_progress: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class History:
    states: dict[str, CourseState]
    cleared: set[str]
    in_progress: set[str]
    issues: list[dict]
    equivalence_credit: dict[str, tuple[str, list[str]]]  # target -> (source, evidence)

    def is_cleared(self, code: str, projected: bool = False) -> bool:
        pool = self.cleared | (self.in_progress if projected else set())
        return code in pool or (code in self.equivalence_credit and
                                (self.equivalence_credit[code][0] in pool))

    def cleared_via(self, code: str) -> str | None:
        if code in self.cleared:
            return code
        if code in self.equivalence_credit:
            return self.equivalence_credit[code][0]
        return None


def interpret(profile: StudentProfile, snap: Snapshot) -> History:
    rule = snap.rule("course_cleared_by_grade") or {"params": {"clearing_grades": [], "non_clearing_reports": []}}
    clearing = set(rule["params"]["clearing_grades"])
    reports = set(rule["params"]["non_clearing_reports"])
    by_code: dict[str, list] = {}
    for i, a in enumerate(profile.attempts):
        by_code.setdefault(a.course_code, []).append((a.attempt_order or 0, i, a))
    states: dict[str, CourseState] = {}
    issues: list[dict] = []
    for code, lst in by_code.items():
        lst.sort(key=lambda x: (x[0], x[1]))
        latest = lst[-1][2]
        completed = [x[2] for x in lst if x[2].status == "completed"]
        units = latest.credit_value if latest.credit_value is not None else snap.course_units(code)
        system = latest.credit_system or "units"
        if code not in snap.courses:
            issues.append({"code": "unknown_course", "course": code,
                           "message": f"{code} is not in the supplied bulletin, timetable or handouts; it cannot be "
                                      "matched to requirements."})
        st = CourseState(code=code, status="not_cleared", grade=latest.grade, units=units, credit_system=system,
                         attempts=len(lst))
        if latest.status == "in_progress":
            prev = completed[-1] if completed else None
            if prev and prev.grade in clearing:
                st.status, st.grade, st.repeat_in_progress = "cleared", prev.grade, True
                st.notes.append("Repeat in progress: the latest outcome will replace this grade (Academic Regulations: latest performance governs).")
            else:
                st.status = "in_progress"
        elif latest.grade is None:
            st.status = "grade_missing"
            issues.append({"code": "grade_missing", "course": code,
                           "message": f"{code} is marked completed without a grade or report; it is not counted."})
        elif latest.grade in clearing:
            st.status = "cleared"
        elif latest.grade in reports:
            st.status = "not_cleared"
            st.notes.append(f"Latest outcome '{latest.grade}' is a report, not a grade; the course is not cleared.")
        else:
            st.status = "unknown_grade"
            issues.append({"code": "unknown_grade", "course": code,
                           "message": f"Grade/report '{latest.grade}' for {code} is not recognised; not counted."})
        if system == "credit_hours":
            st.notes.append("Credit-hour course: excluded from unit totals (no documented conversion).")
        states[code] = st
    cleared = {c for c, s in states.items() if s.status == "cleared"}
    in_progress = {c for c, s in states.items() if s.status == "in_progress"}
    eq_credit: dict[str, tuple[str, list[str]]] = {}
    for c in cleared | in_progress:
        for other in snap.equivalents.get(c, ()):  # listed equivalences only
            if other not in states:
                eq_credit.setdefault(other, (c, snap.equivalence_evidence.get((c, other), [])))
    return History(states=states, cleared=cleared, in_progress=in_progress, issues=issues, equivalence_credit=eq_credit)
