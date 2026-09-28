"""Timetable parser: offerings, sections, meetings, exams, equivalences, slot legend.

Authority: the timetable is authoritative for *current availability and meetings*
(Architecture §5). It is not a source of prerequisites (it defers those to an
external portal, timetable §VI) nor of curriculum requirements.
"""
from __future__ import annotations

import re
from datetime import date

from ..common import Registry, find_codes, parse_code, squash

# Slot legend, timetable p.6 (column 9 HOURS and columns 10/11 exam sessions).
HOUR_SLOTS = {
    1: ("08:00", "08:50"), 2: ("09:00", "09:50"), 3: ("10:00", "10:50"), 4: ("11:00", "11:50"),
    5: ("12:00", "12:50"), 6: ("13:00", "13:50"), 7: ("14:00", "14:50"), 8: ("15:00", "15:50"),
    9: ("16:00", "16:50"), 10: ("17:00", "17:50"),
}
EXAM_SESSIONS = {
    "FN1": ("09:00", "10:30"), "FN2": ("11:00", "12:30"),
    "AN1": ("14:00", "15:30"), "AN2": ("16:00", "17:30"),
    "FN": ("09:00", "12:00"), "AN": ("14:00", "17:00"),
}
DAY_TOKENS = {"M": "Mon", "T": "Tue", "W": "Wed", "Th": "Thu", "F": "Fri", "S": "Sat", "Su": "Sun"}

TERM = {"id": "2026-27-S1", "label": "First Semester 2026-27", "campus": "Pilani", "start_year": 2026}
CREDIT_HOUR_COMCOD_MIN = 5000


def parse_days_hours(raw: str) -> tuple[list[dict], bool]:
    """Parse 'M W F 10', 'T Th 2 F 9', 'S 1 2 3' into (day, hour) meetings.

    Returns (meetings, ok). Unknown tokens or an hour outside the legend make ok False
    so the meeting is kept only as raw text and routed to review.
    """
    toks = squash(raw).replace(",", " ").split()
    meetings: list[dict] = []
    days: list[str] = []
    hours: list[int] = []
    ok = bool(toks)

    def flush():
        for d in days:
            for h in hours:
                meetings.append({"day": DAY_TOKENS[d], "hour": h})

    prev_was_hour = False
    for t in toks:
        if t in DAY_TOKENS:
            if prev_was_hour:
                flush()
                days, hours = [], []
            days.append(t)
            prev_was_hour = False
        elif t.isdigit() and 1 <= int(t) <= 14:
            if int(t) not in HOUR_SLOTS:
                ok = False  # hour not defined by the slot legend -> unverified
            hours.append(int(t))
            prev_was_hour = True
        else:
            ok = False
    flush()
    if not meetings:
        ok = False
    # dedupe preserving order
    seen, out = set(), []
    for m in meetings:
        k = (m["day"], m["hour"])
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out, ok


def parse_exam(raw: str, kind: str) -> dict | None:
    raw = squash(raw)
    if not raw:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})\s*(FN1|FN2|AN1|AN2|FN|AN)$", raw)
    if not m:
        return {"type": kind, "raw": raw, "date": None, "session": None, "start": None, "end": None,
                "status": "unparsed"}
    d, mo, sess = int(m.group(1)), int(m.group(2)), m.group(3)
    year = TERM["start_year"] if mo >= 7 else TERM["start_year"] + 1
    try:
        dt = date(year, mo, d).isoformat()
    except ValueError:
        return {"type": kind, "raw": raw, "date": None, "session": sess, "start": None, "end": None,
                "status": "invalid_date"}
    s, e = EXAM_SESSIONS[sess]
    return {"type": kind, "raw": raw, "date": dt, "session": sess, "start": s, "end": e, "status": "scheduled"}


def _num(v: str) -> int | None:
    v = squash(v)
    if v == "-":
        return 0
    return int(v) if v.isdigit() else None


def _printed_label(page_text: str) -> str | None:
    for line in page_text.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= 6 else None
    return None


def _is_header(row: list[str]) -> bool:
    joined = " ".join(squash(c) for c in row).upper()
    return "COURSE NO" in joined or joined.strip() in {"L P T S U/C/H", "L P T S U/CH"} or (
        squash(row[3]).upper() == "L" and squash(row[4]).upper() == "P"
    )


def extract_timetable(doc: dict, pages_text: list[str], tables: dict[int, list], reg: Registry) -> dict:
    # Locate the course-wise timetable pages by content, not fixed numbers, so a new
    # semester's timetable can be ingested without code changes.
    start = end = None
    for i, t in enumerate(pages_text, start=1):
        up = t.upper()
        if start is None and "COURSEWISE TIMETABLE" in up and "COM" in up:
            start = i
        if start is not None and i > start and re.search(r"III\.\s*TEXT\s*BOOKS", up):
            end = i - 1
            break
    if start is None:
        reg.flag("unparsed", f"doc:{doc['file']}", "coursewise_timetable", "Course-wise timetable section not found",
                 severity="error")
        return {"offerings": [], "equivalences": [], "term": TERM}
    end = end or len(pages_text)

    offerings: list[dict] = []
    cur: dict | None = None
    cur_type = "Lecture"
    cur_sec: dict | None = None

    cohort_note = None
    for i in range(start, end + 1):
        if "com cod >=5000" in pages_text[i - 1]:
            cohort_note = reg.cite(doc, i, "Note:. Courses with com cod >=5000 are meant only for 2026 admissions "
                                   "into FD, HD and PHD and not for others", "timetable.note",
                                   printed_page=_printed_label(pages_text[i - 1]))
            break

    for pno in range(start, end + 1):
        printed = _printed_label(pages_text[pno - 1])
        for table in tables.get(pno, []):
            for row in table:
                if not row or len(row) < 14:
                    if row and any(squash(c) for c in row):
                        reg.flag("unparsed", f"timetable:p{pno}", "row", f"Row with {len(row)} columns skipped: "
                                 f"{squash(' | '.join(c or '' for c in row))[:120]}")
                    continue
                row = [c or "" for c in row[:14]]
                if _is_header(row):
                    continue
                comcod, cno, title, L, P, T, S, U, sec, instr, room, dh, mid, comp = (squash(c) for c in row)
                instr_raw = row[9] or ""
                excerpt = " | ".join(x for x in (comcod, cno, title, L, P, T, S, U, sec, instr, room, dh, mid, comp) if x)
                if cno:
                    code = parse_code(cno, strict=False)
                    if not code or not comcod.isdigit():
                        reg.flag("unparsed", f"timetable:p{pno}", "course_no", f"Unrecognised course row: {excerpt[:120]}")
                        cur = None
                        continue
                    ev = reg.cite(doc, pno, excerpt, "timetable.row", printed_page=printed)
                    comnum = int(comcod)
                    credit_hours = comnum >= CREDIT_HOUR_COMCOD_MIN
                    cur = {
                        "id": f"{TERM['id']}:{comcod}",
                        "term": TERM["id"],
                        "campus": TERM["campus"],
                        "computer_code": comcod,
                        "course_code": code,
                        "title_timetable": title,
                        "credit": {"L": _num(L), "P": _num(P), "T": _num(T), "S": _num(S),
                                   "value": _num(U),
                                   "system": "credit_hours" if credit_hours else "units"},
                        "cohort_scope": ({"admission_years": [2026], "note": "com cod >= 5000: 2026 admissions only"}
                                         if credit_hours else {"exclude_admission_years": [2026],
                                                               "note": "com cod < 5000"}),
                        "cohort_evidence": [cohort_note] if cohort_note else [],
                        "sections": [],
                        "exams": [],
                        "evidence": [ev],
                        "pages": [pno],
                        "status": "offered",
                    }
                    if credit_hours and cohort_note is None:
                        reg.flag("missing", f"offering:{cur['id']}", "cohort_scope",
                                 "Credit-hour offering without located cohort note")
                    if cur["credit"]["value"] is None:
                        reg.flag("missing", f"offering:{cur['id']}", "credit", f"Credit value not parsed: {excerpt[:100]}",
                                 [ev])
                    for kind, raw in (("midsem", mid), ("compre", comp)):
                        ex = parse_exam(raw, kind)
                        if ex:
                            ex["evidence"] = [ev]
                            cur["exams"].append(ex)
                            if ex["status"] != "scheduled":
                                reg.flag("unreliable", f"offering:{cur['id']}", kind, f"Exam cell not parsed: '{raw}'", [ev])
                    offerings.append(cur)
                    cur_type = "Lecture"
                    cur_sec = None
                elif cur is None:
                    continue
                elif pno not in cur["pages"]:
                    cur["pages"].append(pno)

                if cur is None:
                    continue
                if title in ("Tutorial", "Practical", "Lecture"):
                    cur_type = title
                if sec:
                    ev = reg.cite(doc, pno, f"{cur['course_code']} {excerpt}", "timetable.section", printed_page=printed)
                    cancelled = "CANCLED" in (room + instr).upper() or "CANCELLED" in (room + instr).upper()
                    meetings, ok = parse_days_hours(dh) if dh else ([], False)
                    cur_sec = {
                        "id": f"{cur['id']}:{sec}",
                        "type": cur_type,
                        "label": sec,
                        "instructors": [instr] if instr and not cancelled else [],
                        "instructor_in_charge": instr if instr and instr_raw.strip().isupper() and not cancelled else None,
                        "room": None if cancelled else (room or None),
                        "raw_slot": dh or None,
                        "meetings": [] if cancelled else [
                            {**m, "start": HOUR_SLOTS.get(m["hour"], (None, None))[0],
                             "end": HOUR_SLOTS.get(m["hour"], (None, None))[1],
                             "verification_status": "verified" if m["hour"] in HOUR_SLOTS else "undefined_slot"}
                            for m in meetings
                        ],
                        "status": "cancelled" if cancelled else "active",
                        "schedule_status": ("cancelled" if cancelled else
                                            "verified" if ok else
                                            "no_meetings_listed" if not dh else
                                            "undefined_slot" if meetings else "unparsed"),
                        "evidence": [ev],
                    }
                    if dh and not ok and not cancelled:
                        reg.flag("unreliable", f"section:{cur_sec['id']}", "days_hours",
                                 f"Slot '{dh}' uses an hour not defined in the timetable legend (1-10) or is unparsed; "
                                 "meeting times remain unverified", [ev])
                    cur["sections"].append(cur_sec)
                    # exam cells on non-first lecture rows must agree with the offering exams
                    for kind, raw in (("midsem", mid), ("compre", comp)):
                        if raw and cno == "":
                            known = next((e for e in cur["exams"] if e["type"] == kind), None)
                            if known and known["raw"] != raw:
                                known["status"] = "conflict"
                                reg.flag("conflict", f"offering:{cur['id']}", kind,
                                         f"Section {sec} lists {kind} '{raw}' but offering lists '{known['raw']}'", [ev])
                            elif not known:
                                ex = parse_exam(raw, kind)
                                if ex:
                                    ex["evidence"] = [ev]
                                    cur["exams"].append(ex)
                elif instr and cur_sec is not None and cur_sec["status"] == "active":
                    cur_sec["instructors"].append(instr)

    # Offering-level viability: a course is available only if every component type it
    # lists has at least one active section (timetable legend + Architecture §9).
    for o in offerings:
        types = {}
        for s in o["sections"]:
            types.setdefault(s["type"], []).append(s["status"])
        o["component_types"] = sorted(types)
        dead = [t for t, st in types.items() if all(x == "cancelled" for x in st)]
        if not o["sections"]:
            o["status"] = "no_sections_listed"
        elif dead:
            o["status"] = "cancelled" if "Lecture" in dead or len(dead) == len(types) else "partially_cancelled"
            reg.flag("validation_failure", f"offering:{o['id']}", "status",
                     f"All {', '.join(dead)} sections cancelled; offering marked {o['status']}", o["evidence"],
                     severity="info")
        for kind in ("midsem", "compre"):
            if not any(e["type"] == kind for e in o["exams"]) and o["status"] == "offered":
                o["exams"].append({"type": kind, "raw": None, "date": None, "session": None, "start": None, "end": None,
                                   "status": "not_listed", "evidence": o["evidence"]})
        # Credit sanity: in the unit framework U usually equals L + P for regular courses.
        c = o["credit"]
        if c["system"] == "units" and c["L"] and c["P"] is not None and c["value"] is not None:
            if c["L"] + c["P"] != c["value"]:
                reg.flag("validation_failure", f"offering:{o['id']}", "credit",
                         f"U={c['value']} differs from L+P={c['L'] + c['P']}", o["evidence"], severity="info")

    equivalences = _extract_equivalences(doc, pages_text, tables, reg)
    legend_ev = None
    for i, t in enumerate(pages_text, start=1):
        if "L E G E N D" in t or "LEGEND" in t.upper()[:200]:
            legend_ev = reg.cite(doc, i, "HOURS: 1 8-8:50AM ... 10 5-5:50PM; MIDSEM FN1 9.00-10.30, FN2 11:00-12:30, "
                                 "AN1 2:00-3:30, AN2 4:00-5:30; COMPRE FN 9-12, AN 2-5; Comprehensive examination date "
                                 "for lab courses to be announced by Instructor-in-charge", "timetable.legend")
            break
    return {"term": TERM, "offerings": offerings, "equivalences": equivalences,
            "slot_legend": {"hours": HOUR_SLOTS, "exam_sessions": EXAM_SESSIONS, "evidence": [legend_ev] if legend_ev else []},
            "section_range": [start, end]}


def _extract_equivalences(doc: dict, pages_text: list[str], tables: dict[int, list], reg: Registry) -> list[dict]:
    out: list[dict] = []
    for pno in sorted(tables):
        for table in tables[pno]:
            header = " ".join(squash(c) for c in (table[0] if table else []) if c).upper()
            if not ("COURSE NO" in header and "EQUIVALENT" in header):
                continue
            for row in table:
                cells = [squash(c) for c in row if c is not None]
                if not cells or "COURSE NO" in cells[0].upper():
                    continue
                code = parse_code(cells[0])
                if not code:
                    continue
                equiv = []
                for c in cells[2:]:
                    for e in find_codes(c):
                        if e != code and e not in equiv:
                            equiv.append(e)
                ev = reg.cite(doc, pno, " | ".join(cells), "timetable.equivalents")
                out.append({"course_code": code, "title": cells[1] if len(cells) > 1 else "", "equivalent_codes": equiv,
                            "relationship_type": "equivalence", "scope": "listed for First Semester 2026-27",
                            "evidence": [ev]})
    return out
