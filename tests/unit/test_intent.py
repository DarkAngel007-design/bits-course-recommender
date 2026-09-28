"""NLP tests: hard vs soft preference parsing and unsupported claims (deterministic parser)."""
import pytest

from backend.app.recommendations.intent import Intent, parse_rules


def hard(it: Intent) -> set:
    return {(f.property, f.value) for f in it.hard_filters}


def soft(it: Intent) -> set:
    return {p.property for p in it.soft_preferences}


def test_del_ai():
    it = parse_rules("Suggest DELs related to AI.")
    assert it.category == "DEL" and "artificial_intelligence" in it.topics and not it.hard_filters


def test_opel_no_attendance():
    it = parse_rules("I want an OPEL with no attendance requirement.")
    assert it.category == "OPEL" and ("attendance_none", True) in hard(it)


def test_no_midsem_lenient_makeup():
    it = parse_rules("Suggest courses with no midsem and a lenient makeup policy.")
    assert ("midsem_present", False) in hard(it)
    assert "lenient_makeup" in soft(it) and it.clarifications  # subjective -> soft + clarification


def test_huel_prefer_project():
    it = parse_rules("I need a HUEL and prefer project-based evaluation.")
    assert it.category == "HUEL" and "project_based" in soft(it) and not hard(it)


def test_schedule_prefs():
    it = parse_rules("An AI-related DEL with no midsem, no 8 AM classes, keep Friday free")
    assert it.schedule.no_early_classes and it.schedule.free_days == ["Fri"]


@pytest.mark.parametrize("q", ["an easy OPEL", "HUEL with lenient grading", "best professor for CS electives"])
def test_unsupported_claims_flagged_not_filtered(q):
    it = parse_rules(q)
    assert it.unsupported and not any(f.property not in ("department",) for f in it.hard_filters)


def test_schema_rejects_unknown_operators():
    with pytest.raises(Exception):
        Intent.model_validate({"hard_filters": [{"property": "grade_leniency", "value": True}]})
    it = Intent.model_validate({"topics": ["artificial_intelligence", "not_a_topic"]})
    assert it.topics == ["artificial_intelligence"]
