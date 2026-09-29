"""Export docs/openapi.json and postman/BITS-Course-Recommender.postman_collection.json."""
import json
from pathlib import Path

from backend.app.api.main import app

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "openapi.json").write_text(json.dumps(app.openapi(), indent=1))

golden = json.loads((ROOT / "tests" / "fixtures" / "golden_profiles.json").read_text())
PROFILE = golden["cs_2025_sem5"]["profile"]


def req(name, method, path, body=None, auth="profile", tests=None, desc="", expect_2xx=True):
    headers = [{"key": "Content-Type", "value": "application/json"}]
    if auth == "profile":
        headers.append({"key": "X-Profile-Token", "value": "{{ownerToken}}"})
    elif auth == "admin":
        headers.append({"key": "X-Admin-Token", "value": "{{adminToken}}"})
    item = {"name": name, "request": {"method": method, "header": headers, "description": desc,
                                      "url": {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"],
                                              "path": [p for p in path.split("?")[0].split("/") if p],
                                              **({"query": [{"key": k, "value": v} for k, v in
                                                            (q.split("=") for q in path.split("?")[1].split("&"))]}
                                                 if "?" in path else {})}}}
    if body is not None:
        item["request"]["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2), "options": {"raw": {"language": "json"}}}
    ok = ["pm.test('status is 2xx', () => pm.expect(pm.response.code).to.be.within(200, 299));"] if expect_2xx else []
    item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": (tests or []) + ok}}]
    return item


items = [
    {"name": "Meta", "item": [
        req("Health", "GET", "/health", auth=None),
        req("Meta, coverage and supported scope", "GET", "/meta", auth=None,
            tests=["pm.test('has dataset version', () => pm.expect(pm.response.json().dataset_version).to.be.a('string'));"]),
        req("Search courses", "GET", "/courses?q=CS F4&limit=10", auth=None),
        req("Course detail", "GET", "/courses/CS F469", auth=None),
        req("Resolve curriculum", "GET", "/curricula/resolve?programmes=BE-COMPUTER-SCIENCE", auth=None),
    ]},
    {"name": "Profiles", "item": [
        req("Create profile (synthetic)", "POST", "/profiles", PROFILE, auth=None, tests=[
            "const j = pm.response.json();",
            "pm.collectionVariables.set('profileId', j.id);",
            "pm.collectionVariables.set('ownerToken', j.owner_token);",
            "pm.test('returns owner token', () => pm.expect(j.owner_token).to.be.a('string'));"]),
        req("Get profile", "GET", "/profiles/{{profileId}}"),
        req("Update profile (expected_version=1)", "PATCH", "/profiles/{{profileId}}",
            {"expected_version": 1, "profile": {**PROFILE, "minor": "Finance"}}),
        req("Stale update -> 409", "PATCH", "/profiles/{{profileId}}", {"expected_version": 1, "profile": PROFILE}, tests=[
            "pm.test('409 on stale version', () => pm.expect(pm.response.code).to.eql(409));"], expect_2xx=False),
        req("Requirements (completed / projected / remaining)", "GET", "/profiles/{{profileId}}/requirements", tests=[
            "pm.test('requirements available', () => pm.expect(pm.response.json().requirements.available).to.eql(true));"]),
    ]},
    {"name": "Recommendations", "item": [
        req("Parse intent", "POST", "/intent/parse", {"query": "Suggest an AI-related DEL with no midsem"}),
        *[req(f"Recommend: {q}", "POST", "/recommendations", {"profile_id": "{{profileId}}", "query": q}, tests=[
            "const j = pm.response.json();",
            "pm.test('matches are eligible', () => j.matches.forEach(m => pm.expect(m.eligibility.status).to.eql('eligible')));",
            "if (j.matches.length) { pm.collectionVariables.set('offeringId', j.matches[0].offering_id); pm.collectionVariables.set('evidenceId', j.matches[0].evidence[0]); }"])
          for q in ["Suggest DELs related to AI.", "I want an OPEL with no attendance requirement.",
                    "Suggest courses with no midsem and a lenient makeup policy.",
                    "I need a HUEL and prefer project-based evaluation.",
                    "Suggest an AI-related DEL with no midsem"]],
        req("Recommend with edited intent", "POST", "/recommendations", {"profile_id": "{{profileId}}", "intent": {
            "category": "OPEL", "topics": ["finance"], "hard_filters": [{"property": "midsem_present", "value": True}],
            "soft_preferences": [{"property": "open_book", "weight": 1}], "count": 5,
            "schedule": {"no_early_classes": True, "free_days": ["Sat"], "compact": False}}}),
        req("Evaluate eligibility", "POST", "/eligibility/evaluate",
            {"profile_id": "{{profileId}}", "course_codes": ["CS F407", "MAC F313", "CS U469"]}),
        req("Offering detail", "GET", "/offerings/{{offeringId}}", auth=None),
    ]},
    {"name": "Plans", "item": [
        req("Validate plan", "POST", "/plans/validate", {"profile_id": "{{profileId}}", "offering_ids": ["{{offeringId}}"],
                                                         "preferences": {"no_early_classes": True, "free_days": ["Sat"]}}),
        req("Solve sections", "POST", "/plans/solve", {"profile_id": "{{profileId}}", "offering_ids": ["{{offeringId}}"]}),
        req("Save plan", "POST", "/plans", {"profile_id": "{{profileId}}", "offering_ids": ["{{offeringId}}"], "name": "Demo"},
            tests=["pm.collectionVariables.set('planId', pm.response.json().id);"]),
        req("Get plan (stale check + revalidation)", "GET", "/plans/{{planId}}"),
        req("List plans", "GET", "/profiles/{{profileId}}/plans"),
    ]},
    {"name": "Sources", "item": [req("Evidence record", "GET", "/sources/{{evidenceId}}", auth=None,
                                     desc="Set evidenceId from any 'evidence' array in a response.")]},
    {"name": "Admin", "item": [
        req("Ingestion runs / snapshots", "GET", "/admin/ingestion-runs", auth="admin",
            tests=["pm.collectionVariables.set('snapshotId', pm.response.json().current);"]),
        req("Review queue (errors)", "GET", "/admin/review-queue?severity=error&limit=50", auth="admin"),
        req("Rules", "GET", "/admin/rules", auth="admin"),
        req("Publish snapshot", "POST", "/admin/datasets/{{snapshotId}}/publish", auth="admin"),
    ]},
]
collection = {
    "info": {"name": "BITS Course Recommender API", "schema":
             "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
             "description": "Run the folders in order. Profiles are synthetic. The owner token from 'Create profile' "
                            "is stored automatically. Set the adminToken collection variable to your server's ADMIN_TOKEN "
                            "(or the one-time token printed in the server log) before running the Admin folder."},
    "item": items,
    "variable": [{"key": "baseUrl", "value": "http://localhost:8000"}, {"key": "adminToken", "value": ""},
                 {"key": "profileId", "value": ""}, {"key": "ownerToken", "value": ""}, {"key": "offeringId", "value": ""},
                 {"key": "planId", "value": ""}, {"key": "evidenceId", "value": ""}, {"key": "snapshotId", "value": ""}],
}
(ROOT / "postman").mkdir(exist_ok=True)
(ROOT / "postman" / "BITS-Course-Recommender.postman_collection.json").write_text(json.dumps(collection, indent=1))
print("wrote docs/openapi.json and postman collection")
