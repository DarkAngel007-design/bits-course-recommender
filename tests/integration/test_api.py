"""Integration: published records -> API responses (incl. no-hard-rule-violation gate)."""
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ.pop("ANTHROPIC_API_KEY", None)  # deterministic parser in tests
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from backend.app.api.main import app  # noqa: E402
from tests.conftest import FIXTURES  # noqa: E402

GOLDEN = {k: v for k, v in json.loads((FIXTURES / "golden_profiles.json").read_text()).items() if not k.startswith("_")}
QUERIES = ["Suggest DELs related to AI.", "I want an OPEL with no attendance requirement.",
           "Suggest courses with no midsem and a lenient makeup policy.",
           "I need a HUEL and prefer project-based evaluation.", "Suggest an AI-related DEL with no midsem",
           "Find an OPEL with no attendance requirement and no 8 AM classes"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _create(client, name):
    r = client.post("/profiles", json=GOLDEN[name]["profile"])
    assert r.status_code == 201
    d = r.json()
    return d["id"], {"X-Profile-Token": d["owner_token"]}


def test_ownership_and_versioning(client):
    pid, h = _create(client, "cs_2025_sem5")
    assert client.get(f"/profiles/{pid}").status_code == 403
    assert client.get(f"/profiles/{pid}", headers={"X-Profile-Token": "nope"}).status_code == 403
    prof = GOLDEN["cs_2025_sem5"]["profile"]
    assert client.patch(f"/profiles/{pid}", headers=h, json={"expected_version": 1, "profile": prof}).status_code == 200
    assert client.patch(f"/profiles/{pid}", headers=h, json={"expected_version": 1, "profile": prof}).status_code == 409


def test_malformed_input_422(client):
    assert client.post("/profiles", json={"admission_year": 2025}).status_code == 422
    bad = dict(GOLDEN["cs_2025_sem5"]["profile"], attempts=[{"course_code": "not a code"}])
    assert client.post("/profiles", json=bad).status_code == 422


@pytest.mark.parametrize("name", ["cs_2025_sem5", "cs_2025_sem7", "dual_econ_cs"])
@pytest.mark.parametrize("q", QUERIES)
def test_no_hard_rule_violations(client, name, q):
    pid, h = _create(client, name)
    r = client.post("/recommendations", headers=h, json={"profile_id": pid, "query": q})
    assert r.status_code == 200
    out = r.json()
    assert out["versions"]["dataset"] and out["parsed_intent"]
    ids = [m["offering_id"] for m in out["matches"]]
    if ids:
        ev = client.post("/eligibility/evaluate", headers=h, json={"profile_id": pid, "offering_ids": ids}).json()
        assert all(x["status"] == "eligible" for x in ev["results"])  # accepted results are eligible
    for m in out["matches"]:
        # unknown facts never satisfy hard filters
        assert all(f["value"] is True for f in m["matched_hard_filters"])
        assert len(m["matched_hard_filters"]) == len(out["parsed_intent"]["hard_filters"])
        assert m["requirement"] is not None
        cat = out["parsed_intent"]["category"]
        if cat not in ("ANY",):
            want = {"CDC": "DC"}.get(cat, cat)
            assert m["requirement"]["category"] == want
        for e in m["evidence"]:  # every displayed source resolves
            assert client.get(f"/sources/{e}").status_code == 200
    for u in out["unverified"]:
        assert u["eligibility"]["status"] != "ineligible"


def test_empty_result_reports_binding_filters(client):
    pid, h = _create(client, "cs_2025_sem5")
    out = client.post("/recommendations", headers=h, json={
        "profile_id": pid, "intent": {"category": "HUEL", "hard_filters": [
            {"property": "midsem_present", "value": False}, {"property": "no_component", "value": "assignment"},
            {"property": "attendance_none", "value": True}], "topics": ["quantum"]}}).json()
    assert out["status"] == "no_verified_match" and out["matches"] == []
    assert out["binding_filters"]["filters"]


def test_unsupported_scope(client):
    pid, h = _create(client, "cohort_2026")
    out = client.post("/recommendations", headers=h, json={"profile_id": pid, "query": "any DEL"}).json()
    assert out["status"] == "unsupported_scope" and out["matches"] == []


def test_plan_joint_rules_and_staleness(client, snap):
    pid, h = _create(client, "cs_2025_sem7")
    # two higher-degree courses together violate the one-HD-course rule
    hd = [o["id"] for o in snap.offerings.values() if o["course_code"].split(" ")[1].startswith("G")
          and o["status"] == "offered" and o["credit"]["system"] == "units"][:2]
    v = client.post("/plans/validate", headers=h, json={"profile_id": pid, "offering_ids": hd}).json()
    assert v["status"] == "invalid" and any(c["id"] == "hd_limit" and c["status"] == "fail" for c in v["checks"])
    ok = [snap.offerings_by_course["CS F469"][0]["id"]]
    saved = client.post("/plans", headers=h, json={"profile_id": pid, "offering_ids": ok}).json()
    assert saved["stale"] is False
    prof = dict(GOLDEN["cs_2025_sem7"]["profile"], interests=["networks"])
    client.patch(f"/profiles/{pid}", headers=h, json={"expected_version": 1, "profile": prof})
    again = client.get(f"/plans/{saved['id']}", headers=h).json()
    assert again["stale"] is True and "current_validation" in again


def test_overload_rejected(client, snap):
    pid, h = _create(client, "cs_2025_sem7")
    big = [o["id"] for o in snap.offerings.values() if o["status"] == "offered" and o["credit"]["system"] == "units"
           and (o["credit"]["value"] or 0) >= 4][:8]
    v = client.post("/plans/validate", headers=h, json={"profile_id": pid, "offering_ids": big}).json()
    assert any(c["id"] == "max_units" and c["status"] == "fail" for c in v["checks"])


def test_admin_requires_token(client):
    assert client.get("/admin/ingestion-runs").status_code == 403
    r = client.get("/admin/ingestion-runs", headers={"X-Admin-Token": "dev-admin-token"})
    assert r.status_code == 200 and r.json()["snapshots"]


def test_degraded_mode_without_llm(client):
    pid, h = _create(client, "cs_2025_sem5")
    out = client.post("/recommendations", headers=h, json={"profile_id": pid, "query": "DELs on AI"}).json()
    assert out["parser"] == "rules" and out["degraded"]
    assert client.get(f"/profiles/{pid}/requirements", headers=h).status_code == 200


def test_repeated_recommendations_have_fresh_run_metadata(client):
    from backend.app.profiles import db

    pid, h = _create(client, "cs_2025_sem5")
    body = {"profile_id": pid, "query": "DELs on AI", "use_llm": False}
    first = client.post("/recommendations", headers=h, json=body).json()
    second_response = client.post("/recommendations", headers=h, json=body)
    assert second_response.status_code == 200
    second = second_response.json()
    edited_response = client.post("/recommendations", headers=h, json={
        "profile_id": pid, "intent": first["parsed_intent"]})
    assert edited_response.status_code == 200
    edited = edited_response.json()
    assert first["matches"] == second["matches"] == edited["matches"]
    assert len({first["request_id"], second["request_id"], edited["request_id"]}) == 3
    assert edited["parser"] == "edited" and edited["degraded"] is None
    with db.session() as s:
        rows = s.scalars(db.select(db.RunRow).where(db.RunRow.profile_id == pid)).all()
    assert len(rows) == 3
    assert {r.parser for r in rows} == {"rules", "edited"}


@pytest.mark.parametrize("prop,value", [
    ("units", "three"), ("units", True), ("midsem_present", "false"),
    ("attendance_none", False), ("no_component", "made_up_component"),
])
def test_invalid_hard_filter_is_rejected(client, prop, value):
    pid, h = _create(client, "cs_2025_sem5")
    response = client.post("/recommendations", headers=h, json={
        "profile_id": pid, "intent": {"hard_filters": [{"property": prop, "value": value}]}})
    assert response.status_code == 422


def test_solver_rejects_unknown_offerings(client):
    pid, h = _create(client, "cs_2025_sem5")
    response = client.post("/plans/solve", headers=h, json={
        "profile_id": pid, "offering_ids": ["missing-offering"]})
    assert response.status_code == 404
