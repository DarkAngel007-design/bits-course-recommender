"""Property-based tests for academic invariants: no double counting, no mixed-framework totals,
determinism, and unknown facts never becoming passes."""
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.app.academics.eligibility import evaluate_offering
from backend.app.catalog.store import get_store
from tests.conftest import state

SNAP = get_store().current()
CUR = SNAP.curricula["BE-COMPUTER-SCIENCE@bulletin-2025-26"]
POOL = sorted({c for n in CUR["named_placement"] for c in n["codes"]}
              | set(SNAP.programmes["BE-COMPUTER-SCIENCE"]["electives"][:25])
              | set(sorted(SNAP.humanities_codes)[:25]) | {"BITS F221", "ECON F212", "ME F211", "CS F266", "CS F366"})
GRADES = ["A", "A-", "B", "C", "D", "E", "NC", "W", "GOOD", None]


def profile_strategy():
    attempt = st.fixed_dictionaries({"course_code": st.sampled_from(sorted(POOL)), "grade": st.sampled_from(GRADES),
                                     "status": st.sampled_from(["completed", "completed", "in_progress"])})
    return st.fixed_dictionaries({
        "admission_year": st.just(2025), "programmes": st.just(["BE-COMPUTER-SCIENCE"]),
        "current_semester": st.integers(1, 8), "attempts": st.lists(attempt, max_size=45),
    })


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(profile_strategy())
def test_no_double_counting_and_totals(pd):
    _, scope, hist, req = state(pd, SNAP)
    seen = {}
    for c in req["categories"]:
        for x in c["earned"]["list"]:
            assert x["code"] not in seen, f"{x['code']} counted in {seen.get(x['code'])} and {c['key']}"
            seen[x["code"]] = c["key"]
            assert x["code"] in hist.cleared or x["code"] in {v[0] for v in hist.equivalence_credit.values()}
        # earned never exceeds what was cleared; units are unit-framework integers
        assert c["earned"]["units"] == sum(x["units"] for x in c["earned"]["list"])
        assert c["credit_system"] == "units"
    # projected >= earned
    for c in req["categories"]:
        if c["required"]["courses"] is not None and c["status"] == "met":
            assert c["earned"]["courses"] >= c["required"]["courses"]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(profile_strategy())
def test_determinism(pd):
    a = state(pd, SNAP)[3]
    b = state(pd, SNAP)[3]
    assert a == b


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(profile_strategy(), st.sampled_from(sorted(SNAP.offerings)))
def test_eligibility_status_consistent_with_checks(pd, oid):
    p, scope, hist, req = state(pd, SNAP)
    res = evaluate_offering(SNAP.offerings[oid], p, scope=scope, snap=SNAP, history=hist, req=req)
    sts = [c["status"] for c in res["checks"]]
    if res["status"] == "eligible":
        assert "fail" not in sts and "unknown" not in sts
    if "fail" in sts:
        assert res["status"] == "ineligible"
    if res["status"] == "needs_verification":
        assert "unknown" in sts and "fail" not in sts
    # cleared/in-progress courses are never eligible again
    if SNAP.offerings[oid]["course_code"] in hist.cleared | hist.in_progress:
        assert res["status"] == "ineligible"


def test_credit_hour_offering_rejected_for_unit_cohort(make_state, snap):
    p, scope, hist, req = make_state({"admission_year": 2025, "programmes": ["BE-COMPUTER-SCIENCE"],
                                      "current_semester": 5})
    ch = next(o for o in snap.offerings.values() if o["credit"]["system"] == "credit_hours")
    res = evaluate_offering(ch, p, snap, scope, hist, req)
    assert res["status"] == "ineligible"
    assert any(c["id"] == "cohort" and c["status"] == "fail" for c in res["checks"])


def test_cancelled_offering_not_available(make_state, snap):
    p, scope, hist, req = make_state({"admission_year": 2025, "programmes": ["BE-COMPUTER-SCIENCE"],
                                      "current_semester": 5})
    off = next(o for o in snap.offerings.values() if o["status"] == "cancelled")
    res = evaluate_offering(off, p, snap, scope, hist, req)
    assert any(c["id"] == "offered" and c["status"] == "fail" for c in res["checks"])


def test_in_progress_prerequisite_not_counted(make_state, snap):
    base = {"admission_year": 2025, "programmes": ["BE-COMPUTER-SCIENCE"], "current_semester": 5,
            "attempts": [{"course_code": "MATH F113", "status": "in_progress"}]}
    p, scope, hist, req = make_state(base)
    res = evaluate_offering(snap.offerings["2026-27-S1:2652"], p, snap, scope, hist, req)  # MAC F313 needs MATH F113
    pre = next(c for c in res["checks"] if c["id"] == "prerequisite")
    assert pre["status"] == "fail" and "in progress" in pre["message"]


def test_strict_prerequisites_turn_unlisted_into_unknown(make_state, snap):
    p, scope, hist, req = make_state({"admission_year": 2025, "programmes": ["BE-COMPUTER-SCIENCE"],
                                      "current_semester": 7})
    off = snap.offerings_by_course["CS F407"][0]
    lax = evaluate_offering(off, p, snap, scope, hist, req)
    strict = evaluate_offering(off, p, snap, scope, hist, req, strict_prerequisites=True)
    assert next(c for c in lax["checks"] if c["id"] == "prerequisite")["status"] == "pass"
    assert next(c for c in strict["checks"] if c["id"] == "prerequisite")["status"] == "unknown"


def test_humanities_own_discipline_excluded(snap):
    from backend.app.academics.requirements import elective_candidates, own_context
    cur = snap.curricula["MSC-ECONOMICS@bulletin-2025-26"]
    ctx = own_context(cur, snap)
    econ_hum = [c for c in snap.humanities_codes if c.startswith("ECON ")]
    for code in econ_hum:
        assert "HUEL" not in elective_candidates(code, cur, snap, ctx)
