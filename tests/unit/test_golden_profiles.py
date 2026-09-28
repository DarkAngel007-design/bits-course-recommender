"""Golden-profile academic results (release gate: must pass completely)."""
import pytest

from backend.app.academics.eligibility import evaluate_offering


def _cat(req, key):
    return next(c for c in req["categories"] if c["key"] == key)


@pytest.mark.parametrize("name", ["cs_2025_sem5", "cs_2025_sem7", "dual_econ_cs"])
def test_requirement_allocation(name, golden, make_state):
    g = golden[name]
    p, scope, hist, req = make_state(g["profile"])
    exp = g["expected"]
    assert scope["status"] == exp["scope"]
    if "curriculum" in exp:
        assert req["curriculum_id"] == exp["curriculum"]
    for key, e in exp["categories"].items():
        c = _cat(req, key)
        if "status" in e:
            assert c["status"] == e["status"], (key, c["status"])
        if "required" in e:
            assert [c["required"]["courses"], c["required"]["units"]] == e["required"], key
        if "earned" in e:
            assert [c["earned"]["courses"], c["earned"]["units"]] == e["earned"], key
        if "remaining" in e:
            assert [c["remaining"]["courses"], c["remaining"]["units"]] == e["remaining"], key
    if "obligations" in exp:
        assert [o["codes"][0] for o in req["semester"]["obligations"]] == exp["obligations"]
    if "backlog" in exp:
        assert [o["codes"][0] for o in req["semester"]["backlog"]] == exp["backlog"]


def test_repeat_semantics(golden, make_state):
    g = golden["repeat_and_reports"]
    _, _, hist, _ = make_state(g["profile"])
    for code, st in g["expected"]["states"].items():
        assert hist.states[code].status == st, code
    assert [c for c, s in hist.states.items() if s.repeat_in_progress] == g["expected"]["repeat_in_progress"]


def test_missing_prerequisite(golden, make_state, snap):
    g = golden["missing_prereq"]
    p, scope, hist, req = make_state(g["profile"])
    res = evaluate_offering(snap.offerings[g["expected"]["offering"]], p, snap, scope, hist, req)
    assert res["status"] == g["expected"]["status"]
    failed = {c["id"] for c in res["checks"] if c["status"] == "fail"}
    assert set(g["expected"]["failed_checks"]) <= failed


@pytest.mark.parametrize("name", ["cohort_2026", "cohort_2023_unattested", "goa_campus"])
def test_scope_boundaries(name, golden, make_state):
    g = golden[name]
    _, scope, _, req = make_state(g["profile"])
    assert scope["status"] == g["expected"]["scope"]
    assert scope["reasons"]
    if scope["status"] == "unsupported":
        assert req["available"] is False


def test_incomplete_history(golden, make_state):
    g = golden["incomplete_history"]
    _, _, hist, _ = make_state(g["profile"])
    assert sorted({i["code"] for i in hist.issues}) == sorted(g["expected"]["issues"])
    assert sorted(hist.cleared) == sorted(g["expected"]["cleared"])


def test_attested_cohort_is_provisional(golden, make_state):
    prof = dict(golden["cohort_2023_unattested"]["profile"], curriculum_attested=True)
    _, scope, _, req = make_state(prof)
    assert scope["status"] == "attested"
    assert req["provisional"] is True
