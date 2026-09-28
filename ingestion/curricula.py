"""Assemble curricula (single and dual degree) from bulletin charts, course lists and the
category structure table. Every requirement records how it was obtained:

  stated   - value read directly from the bulletin (e.g. chart footer "Discipline Electives-12 Units (4 Courses)")
  derived  - deterministic calculation over stated values (formula + inputs recorded)
  missing  - not available; the engine reports the requirement as unresolved
"""
from __future__ import annotations

import re

from .common import Registry, code_prefix, short_hash, squash

BULLETIN_EDITION = "bulletin-2025-26"
# Applicability: the supplied bulletin is the 2025-26 edition (fee and admission sections are
# for academic year 2025-26) and its charts are unit-based. The timetable states that the
# credit-hour framework applies to 2026 admissions. The only cohort this edition can be
# attributed to without further evidence is the 2025 admission cohort.
RESOLVED_ADMISSION_YEARS = [2025]

ALIASES = {"pharm": "pharmacy", "architecture": "architectural", "mathematic": "mathematics",
           "electrical": "electrical", "communications": "communication"}
STOP = {"b", "e", "be", "m", "sc", "msc", "b.e.", "m.sc.", "engineering", "programme", "program", "with", "specialization",
        "in", "the", "and", "of", "degree", "b.pharm", "bpharm", "stream", "yea", "r", "first", "semeste", "year", "fi"}


def _tokens(name: str) -> set[str]:
    n = name.lower().replace("&", " and ").replace("–", " ").replace("-", " ")
    n = re.sub(r"[^a-z ]", " ", n)
    toks = {ALIASES.get(t, t) for t in n.split()}
    return {t for t in toks if t not in STOP and len(t) > 1}


def slug(name: str) -> str:
    n = name.replace("B.E.", "BE").replace("M.Sc.", "MSC").replace("B.Pharm", "BPHARM")
    n = re.sub(r"[^A-Za-z0-9]+", "-", n).strip("-").upper()
    return n[:60]


def match_list(chart_name: str, lists: dict[str, dict]) -> tuple[str | None, float]:
    ct = _tokens(chart_name)
    best, score = None, 0.0
    for lname, lst in lists.items():
        lt = _tokens(lname if not lname.startswith("UNNAMED") else lst.get("inferred_name_from_prefix", ""))
        if not lt or not ct:
            continue
        j = len(ct & lt) / len(ct | lt)
        if j > score:
            best, score = lname, j
    return best, score


def _chart_named(chart: dict) -> list[dict]:
    out = []
    for s in chart["semesters"]:
        for c in s.get("courses", []):
            out.append({"codes": c["codes"], "alternative": c["alternative"], "year": s["year"], "sem": s["sem"]})
    return out


def _units(code: str, catalog: dict, lst_units: dict) -> int | None:
    if code in lst_units and lst_units[code] is not None:
        return lst_units[code]
    c = catalog.get(code)
    return c.get("U") if c else None


PS_CODES = {"BITS F221", "BITS F412", "BITS F421T", "BITS F422T", "BITS F423T", "BITS F424T", "BITS F425T"}


def build_curricula(bulletin: dict, catalog: dict, structure: dict, reg: Registry, doc: dict) -> dict:
    lists = bulletin["programmes"]
    charts = bulletin["charts"]
    programmes: dict[str, dict] = {}
    curricula: dict[str, dict] = {}

    # ---------------- single-degree programmes ----------------
    singles = [c for c in charts if c["kind"] == "single"]
    for ch in singles:
        lname, score = match_list(ch["programme"], lists)
        pid = slug(ch["programme"])
        if lname is None or score < 0.5:
            reg.flag("ambiguous_mapping", f"programme:{pid}", "course_list",
                     f"No course list confidently matches chart '{ch['programme']}' (best {lname}, {score:.2f})",
                     ch["evidence"], severity="error")
            continue
        if score < 1.0:
            reg.flag("ambiguous_mapping", f"programme:{pid}", "course_list",
                     f"Chart '{ch['programme']}' mapped to list '{lname}' by token similarity {score:.2f}",
                     ch["evidence"], severity="info")
        lst = lists[lname]
        core_codes = [c["code"] for c in lst["core"]]
        el_codes = [c["code"] for c in lst["electives"]]
        lst_units = {c["code"]: c["U"] for c in lst["core"] + lst["electives"]}
        prefixes = [code_prefix(c) for c in core_codes]
        disc_prefix = max(set(prefixes), key=prefixes.count) if prefixes else None
        programmes[pid] = {
            "id": pid,
            "name": ch["programme"],
            "degree": "B.E." if ch["programme"].startswith("B.E.") else ("B.Pharm." if "Pharm" in ch["programme"] else "M.Sc."),
            "list_heading": lname,
            "discipline_prefix": disc_prefix,
            "core": core_codes,
            "electives": el_codes,
            "compulsory_electives": lst.get("compulsory_electives", []),
            "elective_groups": lst.get("groups", {}),
            "list_evidence": [e for c in lst["core"][:1] + lst["electives"][:1] for e in c["evidence"]],
            "course_evidence": {c["code"]: c["evidence"] for c in lst["core"] + lst["electives"]},
            "units": lst_units,
            "chart": ch,
        }

    for pid, p in programmes.items():
        cur = _single_curriculum(p, catalog, structure, reg, doc)
        curricula[cur["id"]] = cur

    # ---------------- dual degrees ----------------
    by_name = {p["name"]: pid for pid, p in programmes.items()}
    for ch in [c for c in charts if c["kind"] == "dual"]:
        parts = re.split(r"\s+with\s+", ch["programme"], maxsplit=1)
        if len(parts) != 2:
            reg.flag("unparsed", f"chart:{ch['programme']}", "dual", "Cannot split dual-degree name", ch["evidence"])
            continue
        msc_name = parts[0].strip()
        be_name = parts[1].strip()
        if not be_name.startswith("B.E."):
            be_name = "B.E. " + be_name
        msc_id = _match_programme(msc_name, programmes)
        be_id = _match_programme(be_name.replace(" Programme", "").replace(" Program", ""), programmes)
        if not msc_id or not be_id:
            reg.flag("ambiguous_mapping", f"chart:{ch['programme']}", "programmes",
                     f"Could not map dual degree components ({msc_name} -> {msc_id}, {be_name} -> {be_id})",
                     ch["evidence"])
            continue
        cur = _dual_curriculum(ch, programmes[msc_id], programmes[be_id], catalog, structure, reg, doc)
        curricula[cur["id"]] = cur

    for p in programmes.values():
        p.pop("chart", None)
    return {"programmes": programmes, "curricula": curricula}


def _match_programme(name: str, programmes: dict) -> str | None:
    nt = _tokens(name)
    best, score = None, 0.0
    for pid, p in programmes.items():
        pt = _tokens(p["name"])
        j = len(nt & pt) / len(nt | pt) if nt | pt else 0
        # prefer exact degree type
        if name.startswith("M.Sc") != p["name"].startswith("M.Sc"):
            j -= 0.5
        if j > score:
            best, score = pid, j
    return best if score >= 0.6 else None


def _named_courses(chart: dict, core: set[str], exclude_ps: bool = True) -> list[dict]:
    out = []
    for n in _chart_named(chart):
        codes = [c for c in n["codes"] if not (exclude_ps and c in PS_CODES)]
        if not codes:
            continue
        out.append({"codes": codes, "choice": len(codes) > 1, "year": n["year"], "sem": n["sem"],
                    "category": "DC" if any(c in core for c in codes) else "GIR"})
    return out


def _single_curriculum(p: dict, catalog: dict, structure: dict, reg: Registry, doc: dict) -> dict:
    ch = p["chart"]
    core = set(p["core"])
    named = _named_courses(ch, core)
    for n in named:
        n["units"] = _units(n["codes"][0], catalog, p["units"])
    gir = [n for n in named if n["category"] == "GIR"]
    dc_named = [n for n in named if n["category"] == "DC"]
    in_chart = {c for n in named for c in n["codes"]}
    missing_core = [c for c in p["core"] if c not in in_chart]
    if missing_core:
        reg.flag("conflict", f"curriculum:{p['id']}", "core",
                 f"Core-list courses absent from the semester chart: {', '.join(missing_core)}", ch["evidence"],
                 severity="info")
    not_listed = [c for n in dc_named for c in n["codes"] if c not in core]
    # requirement values
    gir_units = sum(n["units"] or 0 for n in gir)
    dc_units_list = sum((p["units"].get(c) or _units(c, catalog, {}) or 0) for c in p["core"])
    reqs = []
    reqs.append({"category": "GIR", "kind": "named", "courses": gir, "min_courses": len(gir), "min_units": gir_units,
                 "provenance": "stated", "evidence": ch["evidence"],
                 "note": "Named general-institutional courses from the semester-wise chart (years I-II)."})
    if ch.get("dc_total"):
        dc_units, dc_courses, dc_prov, dc_ev = ch["dc_total"]["units"], ch["dc_total"]["courses"], "stated", ch["dc_total"]["evidence"]
        if dc_courses != len(p["core"]) or dc_units != dc_units_list:
            reg.flag("conflict", f"curriculum:{p['id']}", "DC",
                     f"Chart states DC {dc_units} units/{dc_courses} courses; core list has {dc_units_list} units/"
                     f"{len(p['core'])} courses", dc_ev, severity="warning")
    else:
        dc_units, dc_courses, dc_prov, dc_ev = dc_units_list, len(p["core"]), "derived", p["list_evidence"]
    reqs.append({"category": "DC", "kind": "named", "courses": [{"codes": [c], "choice": False,
                                                                  "units": p["units"].get(c)} for c in p["core"]],
                 "min_courses": dc_courses, "min_units": dc_units, "provenance": dc_prov, "evidence": dc_ev,
                 "placement": {c: next(({"year": n["year"], "sem": n["sem"]} for n in named if c in n["codes"]), None)
                               for c in p["core"]}})
    if ch.get("de_total"):
        de = {"min_units": ch["de_total"]["units"], "min_courses": ch["de_total"]["courses"], "provenance": "stated",
              "evidence": ch["de_total"]["evidence"]}
    else:
        de = {"min_units": None, "min_courses": None, "provenance": "missing", "evidence": ch["evidence"]}
        reg.flag("missing", f"curriculum:{p['id']}", "DEL", "Chart does not state discipline-elective totals; DEL "
                 "requirement left unresolved", ch["evidence"], severity="warning")
    reqs.append({"category": "DEL", "kind": "pool", "pool": p["electives"], "compulsory": p["compulsory_electives"],
                 "groups": p["elective_groups"], **de})
    reqs.append({"category": "HUEL", "kind": "pool", "pool_ref": "humanities_pool",
                 "min_units": structure["humanities"]["min_units"], "min_courses": structure["humanities"]["min_courses"],
                 "provenance": "stated", "evidence": structure["evidence"]})
    # Open electives: programme-specific minimum derived from coursework minimums, never below
    # the category table minimum.
    if de["min_units"] is not None:
        derived_units = structure["coursework"]["min_units"] - gir_units - structure["humanities"]["min_units"] - dc_units - de["min_units"]
        derived_courses = structure["coursework"]["min_courses"] - len(gir) - structure["humanities"]["min_courses"] - dc_courses - de["min_courses"]
        op_units = max(structure["open_electives"]["min_units"], derived_units)
        op_courses = max(structure["open_electives"]["min_courses"], derived_courses)
        reqs.append({"category": "OPEL", "kind": "pool", "pool_ref": "any_offered",
                     "min_units": op_units, "min_courses": op_courses, "provenance": "derived",
                     "derivation": {
                         "formula": "max(category minimum, coursework minimum - GIR - HUEL - DC - DEL)",
                         "units": f"max({structure['open_electives']['min_units']}, {structure['coursework']['min_units']}"
                                  f" - {gir_units} - {structure['humanities']['min_units']} - {dc_units} - {de['min_units']})",
                         "courses": f"max({structure['open_electives']['min_courses']}, "
                                    f"{structure['coursework']['min_courses']} - {len(gir)} - "
                                    f"{structure['humanities']['min_courses']} - {dc_courses} - {de['min_courses']})"},
                     "evidence": structure["evidence"] + de["evidence"]})
    else:
        reqs.append({"category": "OPEL", "kind": "pool", "pool_ref": "any_offered",
                     "min_units": structure["open_electives"]["min_units"],
                     "min_courses": structure["open_electives"]["min_courses"], "provenance": "stated_category_minimum",
                     "evidence": structure["evidence"],
                     "note": "Programme-specific value not derivable (DEL totals missing); category minimum shown."})
    reqs.append({"category": "PS_THESIS", "kind": "named_option",
                 "options": [["BITS F221", "BITS F412"], ["BITS F421T"], ["BITS F424T"], ["BITS F425T"]],
                 "provenance": "stated", "evidence": structure["evidence"],
                 "note": "PS-I and PS-II (25 units) or Thesis; tracked for completeness, not recommended."})
    cid = f"{p['id']}@{BULLETIN_EDITION}"
    return {
        "id": cid,
        "version": BULLETIN_EDITION,
        "type": "single",
        "programmes": [p["id"]],
        "name": p["name"],
        "campus_scope": ["Pilani"],
        "cohort": {"resolved_admission_years": RESOLVED_ADMISSION_YEARS, "credit_system": "units"},
        "requirements": reqs,
        "named_placement": [{"codes": n["codes"], "year": n["year"], "sem": n["sem"], "category": n["category"]}
                            for n in named],
        "chart_page": ch["page"],
        "evidence": ch["evidence"],
        "review_status": "auto_extracted",
    }


def _dual_curriculum(ch: dict, msc: dict, be: dict, catalog: dict, structure: dict, reg: Registry, doc: dict) -> dict:
    core_both = set(msc["core"]) | set(be["core"])
    named = []
    # Year I "Same as First degree Programme": take the B.E. programme's first-year chart rows.
    be_chart_named = []
    for s in ch["semesters"]:
        if s.get("same_as_first_degree"):
            be_chart_named = [n for n in _named_courses(be["chart"], set(be["core"])) if n["year"] == s["year"]]
            break
    named += [dict(n) for n in be_chart_named]
    named += _named_courses(ch, core_both)
    seen = set()
    uniq = []
    for n in named:
        k = tuple(n["codes"])
        if k in seen:
            continue
        seen.add(k)
        n["units"] = _units(n["codes"][0], catalog, {**msc["units"], **be["units"]})
        uniq.append(n)
    named = uniq
    gir = [n for n in named if n["category"] == "GIR"]
    shared = sorted(set(msc["core"]) & set(be["core"]))
    reqs = [{"category": "GIR", "kind": "named", "courses": gir, "min_courses": len(gir),
             "min_units": sum(n["units"] or 0 for n in gir), "provenance": "stated", "evidence": ch["evidence"],
             "note": "General institutional requirements are the same for both degrees and are not repeated."}]
    for prog, tag in ((msc, "DC:" + msc["id"]), (be, "DC:" + be["id"])):
        units = sum((prog["units"].get(c) or 0) for c in prog["core"])
        reqs.append({"category": "DC", "degree": prog["id"], "kind": "named",
                     "courses": [{"codes": [c], "choice": False, "units": prog["units"].get(c)} for c in prog["core"]],
                     "min_courses": len(prog["core"]), "min_units": units, "provenance": "derived",
                     "evidence": prog["list_evidence"], "shared_with_other_degree": shared,
                     "placement": {c: next(({"year": n["year"], "sem": n["sem"]} for n in named if c in n["codes"]), None)
                                   for c in prog["core"]}})
    for prog in (msc, be):
        dt = prog["chart"].get("de_total")
        reqs.append({"category": "DEL", "degree": prog["id"], "kind": "pool", "pool": prog["electives"],
                     "compulsory": prog["compulsory_electives"], "groups": prog["elective_groups"],
                     "min_units": dt["units"] if dt else None, "min_courses": dt["courses"] if dt else None,
                     "provenance": "stated" if dt else "missing", "evidence": dt["evidence"] if dt else prog["chart"]["evidence"]})
        if not dt:
            reg.flag("missing", f"curriculum:dual:{msc['id']}+{be['id']}", f"DEL:{prog['id']}",
                     "Single-degree chart gives no DEL totals; requirement unresolved", prog["chart"]["evidence"])
    reqs.append({"category": "HUEL", "kind": "pool", "pool_ref": "humanities_pool",
                 "min_units": structure["humanities"]["min_units"], "min_courses": structure["humanities"]["min_courses"],
                 "provenance": "stated", "evidence": structure["evidence"]})
    reqs.append({"category": "OPEL", "kind": "met_by_rule", "rule": "dual_degree_open_met_by_de", "min_units": 0,
                 "min_courses": 0, "provenance": "stated_rule", "evidence": structure["dual_degree_evidence"],
                 "note": "Dual degree: discipline electives of one degree count as open electives of the other."})
    reqs.append({"category": "PS_THESIS", "kind": "named_option", "count": 2,
                 "options": [["BITS F412", "BITS F412"], ["BITS F421T", "BITS F421T"], ["BITS F412", "BITS F421T"]],
                 "provenance": "stated", "evidence": structure["dual_degree_evidence"],
                 "note": "Two PS-II, two Thesis, or one of each."})
    cid = f"DUAL-{msc['id']}+{be['id']}@{BULLETIN_EDITION}"
    return {
        "id": cid,
        "version": BULLETIN_EDITION,
        "type": "dual",
        "programmes": [msc["id"], be["id"]],
        "name": f"{msc['name']} with {be['name']}",
        "campus_scope": ["Pilani"],
        "cohort": {"resolved_admission_years": RESOLVED_ADMISSION_YEARS, "credit_system": "units"},
        "requirements": reqs,
        "named_placement": [{"codes": n["codes"], "year": n["year"], "sem": n["sem"], "category": n["category"]}
                            for n in named],
        "chart_page": ch["page"],
        "evidence": ch["evidence"],
        "review_status": "auto_extracted",
    }
