"""Extraction fixtures: parsers over minimal synthetic inputs plus spot checks of the
published snapshot against hand-verified facts from the supplied PDFs."""
from ingestion.common import Registry, find_codes, parse_code
from ingestion.extractors.bulletin import _parse_chart_cell, parse_prereq_expression
from ingestion.extractors.handouts import _parse_eval_tables, classify_component, classify_makeup
from ingestion.extractors.timetable import parse_days_hours, parse_exam


def test_code_normalisation_keeps_suffixes():
    assert find_codes("BITS G564T is a pre-requisite for BITS G563T") == ["BITS G564T", "BITS G563T"]
    assert find_codes("ECE/EEE/INSTR F211: Electrical Machines") == ["ECE F211", "EEE F211", "INSTR F211"]
    assert parse_code("CS F469 / CS U469") == "CS F469"
    assert parse_code("EE G501", strict=False) == "EE G501"


def test_days_hours_grammar():
    m, ok = parse_days_hours("T Th 2 F 9")
    assert ok and [(x["day"], x["hour"]) for x in m] == [("Tue", 2), ("Thu", 2), ("Fri", 9)]
    m, ok = parse_days_hours("M W F 11")  # hour 11 is not in the slot legend
    assert not ok and all(x["hour"] == 11 for x in m)


def test_exam_cells():
    e = parse_exam("09/10 AN1", "midsem")
    assert e["date"] == "2026-10-09" and e["start"] == "14:00" and e["status"] == "scheduled"
    assert parse_exam("TBA", "compre")["status"] == "unparsed"


def test_prerequisite_expressions():
    expr, clean = parse_prereq_expression("CE F231 OR CHE F212 OR ME F212 Fluid Mechanics")
    assert expr["op"] == "any" and len(expr["items"]) == 3 and clean
    expr, clean = parse_prereq_expression("Fundamental knowledge in workshop practices")
    assert expr is None
    expr, clean = parse_prereq_expression("CE F231 OR MF 218 Transport Phenomena")  # code missing level letter
    assert not clean


def test_chart_cell_alternatives():
    cell = "ECON F211 Principles of Economics\nor\nMGTS F211 Principles of\nManagement\nHumanities Electives\nCS F211 Data"
    out = _parse_chart_cell(cell)
    assert out["courses"][0] == {"codes": ["ECON F211", "MGTS F211"], "alternative": True}
    assert out["courses"][1]["codes"] == ["CS F211"]
    assert out["slots"] == ["Humanities Electives"]


def test_eval_table_multi_page_and_percent_in_marks():
    tables = {3: [[["EC No.", "Evaluation Component", "Duration", "Marks (Weightage)", "Date"],
                   ["1", "Mid-Semester Test", "90", "90 (30%)", "5/10"],
                   ["2", "Quizzes", "-", "30 (10%)", "-"]]],
              4: [[["3", "Comprehensive Examination", "180", "180 (60%)", "3/12"]]]}
    comps, page = _parse_eval_tables(tables)
    assert [c["weight"] for c in comps] == [30, 10, 60]
    assert [c["type"] for c in comps] == ["midsem", "quiz", "compre"]


def test_component_classifier():
    assert classify_component("Mid. Sem. Presentation") == "presentation"  # not an exam
    assert classify_component("Mid-Term Test") == "midsem"
    assert classify_component("Semester Test") == "ambiguous_test"


def test_makeup_classifier():
    m = classify_makeup("Make-up will be given in case of extreme medical emergency or hospitalization only. "
                        "No make-up for quizzes.")
    assert "medical" in m["conditions"] and "restricted_cases" in m["conditions"]
    assert "quiz" in m["no_makeup_for"]


# ---- published snapshot spot checks (hand-verified against the PDFs) ----
def test_snapshot_timetable_facts(snap):
    o = snap.offerings["2026-27-S1:1857"]  # timetable p.46: CS F469 INFORMATION RETRIEVAL, M W F 8
    assert o["course_code"] == "CS F469" and o["credit"] == {"L": 3, "P": 0, "T": 0, "S": 0, "value": 3, "system": "units"}
    assert {(m["day"], m["hour"]) for m in o["sections"][0]["meetings"]} == {("Mon", 8), ("Wed", 8), ("Fri", 8)}
    assert {e["type"]: e["date"] for e in o["exams"]} == {"midsem": "2026-10-06", "compre": "2026-12-07"}
    u = next(x for x in snap.offerings.values() if x["course_code"] == "CS U469")  # same handout, credit hours
    assert u["credit"]["system"] == "credit_hours" and u["cohort_scope"]["admission_years"] == [2026]


def test_snapshot_cancellation_semantics(snap):
    bio = next(o for o in snap.offerings.values() if o["course_code"] == "BIO F101" and o["computer_code"] == "2863")
    pract = [s for s in bio["sections"] if s["type"] == "Practical"]
    assert any(s["status"] == "cancelled" for s in pract) and any(s["status"] == "active" for s in pract)
    assert bio["status"] == "offered"  # some practical sections remain, lecture active


def test_snapshot_shared_handout_is_not_equivalence(snap):
    rel = [r for r in snap.relationships if r["type"] == "shared_handout" and r["source"] == "CS F469"]
    assert rel and "CS U469" in rel[0]["targets"]
    assert "CS U469" not in snap.equivalents.get("CS F469", set())


def test_snapshot_handout_facts(snap):
    h = snap.handouts_for("CS F469")[0]  # 175_CS_F469.pdf p.3 evaluation table
    assert h["evaluation"]["complete"] and h["midsem"]["value"] is True
    assert [c["weight"] for c in h["evaluation"]["components"]] == [30, 10, 25, 35]
    assert "quiz" in h["makeup"]["no_makeup_for"]


def test_every_reference_resolves(snap):
    from ingestion.checks import verify
    assert verify(snap.path) == []
    for cur in snap.curricula.values():
        for rq in cur["requirements"]:
            for e in rq.get("evidence", []):
                assert e in snap.evidence
