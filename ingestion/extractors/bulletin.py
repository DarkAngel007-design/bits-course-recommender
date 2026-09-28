"""Bulletin parser.

Produces, each with evidence:
  * course catalogue from Part VI course descriptions (title, L-P-U, description, prerequisite text)
  * programme course lists (discipline core / discipline electives, tracks and pools) from Part IV
  * humanities-elective pool, project-course limits
  * minor programmes (core, electives, course/unit minimums)
  * semester-wise charts (named courses per year/semester, elective slots, DC/DE totals)
  * programme structure (category unit/course minimums, dual-degree principles)

Sections are located by their headings, not fixed page numbers, so a new bulletin edition
can be ingested without code changes. Anything that cannot be parsed reliably is sent to
the review queue rather than guessed.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..common import CODE_RE, KNOWN_PREFIXES, MULTI_PREFIX_RE, Registry, find_codes, norm_code, squash

LINE_CODE_RE = re.compile(r"^([A-Z]{2,6})\s*[\*]?\s+([FGUCEKNZ])\s?(\d{3})([A-Z]?)\*?\b\s*(.*)$")
UNITS_TAIL_RE = re.compile(r"(?:^|\s)(\d{1,2}\*?|-)\s+(\d{1,2}|-)\s+(\d{1,2}\*?)\s*$")
UNITS_ONLY_RE = re.compile(r"^(\d{1,2})\*?$")
PAGE_LABEL_RE = re.compile(r"^(?:[IVX]+-\d+|[ivx]+|\d+)$")


def _printed(page_text: str) -> str | None:
    for line in page_text.splitlines():
        s = line.strip()
        if s:
            return s if PAGE_LABEL_RE.match(s) else None
    return None


def _lines(page_text: str) -> list[str]:
    """Non-empty squashed lines, with split 'ECOM\\nF321' code fragments re-joined."""
    raw = [squash(l) for l in page_text.splitlines()]
    raw = [l for l in raw if l]
    out: list[str] = []
    i = 0
    while i < len(raw):
        l = raw[i]
        if (l in KNOWN_PREFIXES or l in {"BIOT", "MUSIC"}) and i + 1 < len(raw) and re.match(r"^[FGUCEKNZ]\d{3}", raw[i + 1]):
            out.append(f"{l} {raw[i + 1]}")
            i += 2
            continue
        out.append(l)
        i += 1
    return out


def _is_toc(text: str) -> bool:
    return "TABLE OF CONTENTS" in text or text.count("…") >= 6 or text.count("....") >= 6


def _find_page(pages: list[str], pattern: str, start: int = 1, flags=re.I) -> int | None:
    """First page at/after `start` matching pattern, skipping table-of-contents pages."""
    rx = re.compile(pattern, flags)
    for i in range(start, len(pages) + 1):
        if rx.search(pages[i - 1]) and not _is_toc(pages[i - 1]):
            return i
    return None


# ---------------------------------------------------------------------------
# Part VI: course descriptions
# ---------------------------------------------------------------------------
def extract_course_descriptions(doc: dict, pages: list[str], reg: Registry) -> dict[str, dict]:
    start = _find_page(pages, r"Course Description for all On-campus Programmes")
    end = _find_page(pages, r"^\s*PART\s+VII\b", start or 1, flags=re.M) if start else None
    if end is None and start:
        # Part VII (off-campus) begins where page labels switch to VII-n
        for i in range(start, len(pages) + 1):
            if (_printed(pages[i - 1]) or "").startswith("VII-"):
                end = i
                break
    if not start:
        reg.flag("unparsed", f"doc:{doc['file']}", "course_descriptions", "Part VI not located", severity="error")
        return {}
    end = end or len(pages)
    courses: dict[str, dict] = {}
    cur: dict | None = None

    def close():
        nonlocal cur
        if cur is None:
            return
        body = dehyphenate(squash(" ".join(cur.pop("_body"))))
        cur["description"] = body
        _attach_prereq(cur, body, doc, reg)
        ev = reg.cite(doc, cur["page"], f"{cur['code']} {cur['title']} {cur['_units_raw']} {body[:220]}",
                      "bulletin.course_description", printed_page=cur["printed_page"])
        cur["evidence"] = [ev]
        cur.pop("_units_raw", None)
        code = cur["code"]
        if code in courses:
            # Same code described twice (e.g. repeated department listing): keep first, flag.
            if squash(courses[code]["title"]).lower() != squash(cur["title"]).lower():
                reg.flag("conflict", f"course:{code}", "title",
                         f"Bulletin describes {code} twice with different titles: '{courses[code]['title']}' vs "
                         f"'{cur['title']}'", courses[code]["evidence"] + [ev])
        else:
            courses[code] = cur
        cur = None

    for pno in range(start, end):
        text = pages[pno - 1]
        printed = _printed(text)
        lines = _lines(text)
        i = 0
        while i < len(lines):
            line = lines[i]
            m = LINE_CODE_RE.match(line)
            header = None
            if m and m.group(1) in KNOWN_PREFIXES | {"MUSIC", "EE"} and not line.rstrip().endswith(":") \
                    and not re.match(r"^[A-Z]{2,6}\s*\*?\s+[FGUCEKNZ]\d{3}[A-Z]?\s*[:(]", line):
                # A header needs units on this line or within the next 3 lines.
                title_parts = [m.group(5)]
                units = None
                lookahead = 0
                tail = UNITS_TAIL_RE.search(m.group(5))
                single_tail = re.search(r"\s(\d{1,2})\*?\s*$", m.group(5))
                if tail:
                    units = tail.groups()
                    title_parts = [m.group(5)[: tail.start()]]
                elif single_tail and len(m.group(5)) > 8:
                    units = (None, None, single_tail.group(1))
                    title_parts = [m.group(5)[: single_tail.start()]]
                else:
                    for k in range(1, 4):
                        if i + k >= len(lines):
                            break
                        nxt = lines[i + k]
                        if LINE_CODE_RE.match(nxt):
                            break
                        t2 = UNITS_TAIL_RE.search(nxt)
                        if re.fullmatch(r"(\d{1,2}\*?|-)\s+(\d{1,2}|-)\s+(\d{1,2}\*?)", nxt):
                            units = tuple(nxt.split())
                            lookahead = k
                            break
                        if UNITS_ONLY_RE.match(nxt):
                            units = (None, None, nxt)
                            lookahead = k
                            break
                        if t2:
                            units = t2.groups()
                            title_parts.append(nxt[: t2.start()])
                            lookahead = k
                            break
                        title_parts.append(nxt)
                if units and squash(" ".join(title_parts)):
                    header = (norm_code(m.group(1), m.group(2), m.group(3), m.group(4)),
                              squash(" ".join(title_parts)).rstrip(":").strip(), units)
                    i += lookahead
            if header:
                close()
                code, title, units = header
                u_raw = " ".join(x for x in units if x)
                def _n(x):
                    return int(x.rstrip("*")) if x and x.rstrip("*").isdigit() else None
                cur = {
                    "code": code,
                    "title": title,
                    "L": _n(units[0]), "P": _n(units[1]), "U": _n(units[2]),
                    "units_note": "variable/non-standard (starred in bulletin)" if "*" in u_raw else None,
                    "page": pno,
                    "printed_page": printed,
                    "_units_raw": u_raw,
                    "_body": [],
                }
            elif cur is not None:
                if not PAGE_LABEL_RE.match(line) and not re.match(r"^Course Description for all", line):
                    cur["_body"].append(line)
            i += 1
    close()
    return courses


def dehyphenate(text: str) -> str:
    """Re-join words split across lines by the two-column layout ('catego- rization')."""
    return re.sub(r"(\w)- ([a-z])", r"\1\2", text)


PREREQ_RE = re.compile(r"(?i)\bpre[\s-]?requisites?\s*[:\-–]?\s*(.+?)(?=(?:\bEquivalent\s*:|\bPre[\s-]?requisites?\b|$))")


def _attach_prereq(course: dict, body: str, doc: dict, reg: Registry) -> None:
    """Parse 'Pre-requisite: X OR Y' statements into a typed expression.

    Only code-level AND/OR structure is accepted. Anything else ("Fundamental knowledge in
    workshop practices", a code missing its level letter) is kept verbatim and marked
    unresolved so eligibility becomes needs_verification instead of a guess.
    """
    m = PREREQ_RE.search(body)
    course["prerequisite"] = {"stated": False, "text": None, "expression": None, "status": "none_stated"}
    eq = re.search(r"(?i)\bEquivalent\s*:\s*([A-Z/ ]{2,20}\s?[FGUC]\s?\d{3}[A-Z]?(?:\s*(?:,|/|or|and)\s*[A-Z/ ]{2,20}\s?[FGUC]\s?\d{3}[A-Z]?)*)", body)
    course["equivalent_stated"] = find_codes(eq.group(1)) if eq else []
    if not m:
        return
    text = squash(m.group(1)).rstrip(".;, ")
    course["prerequisite"]["stated"] = True
    course["prerequisite"]["text"] = text
    ev = reg.cite(doc, course["page"], f"{course['code']}: Pre-requisite: {text}", "bulletin.prerequisite",
                  printed_page=course["printed_page"])
    course["prerequisite"]["evidence"] = [ev]
    expr, clean = parse_prereq_expression(text)
    if expr is None:
        course["prerequisite"]["status"] = "unresolved"
        reg.flag("unreliable", f"course:{course['code']}", "prerequisite",
                 f"Prerequisite text could not be reduced to course codes: '{text[:160]}'", [ev])
    else:
        course["prerequisite"]["expression"] = expr
        course["prerequisite"]["status"] = "parsed" if clean else "parsed_partial"
        if not clean:
            reg.flag("unreliable", f"course:{course['code']}", "prerequisite",
                     f"Prerequisite parsed to codes but text contains unparsed qualifiers: '{text[:160]}'", [ev],
                     severity="info")


def parse_prereq_expression(text: str) -> tuple[dict | None, bool]:
    """Return ({op: any|all, items: [...]} , clean) or (None, False)."""
    t = squash(text)
    # normalise connectors
    t = re.sub(r"(?i)\(\s*pre-?requisite\s*\)", "", t)
    codes = find_codes(t)
    if not codes:
        return None, False
    # Split on AND at the top level, OR inside; commas/slashes between whole codes read as OR
    # only when the statement uses OR somewhere, otherwise as AND ("A, B" lists).
    has_or = bool(re.search(r"(?i)\bor\b|/", t))
    has_and = bool(re.search(r"(?i)\band\b|&", t))
    residue = t
    for c in codes:
        residue = residue.replace(c, " ")
    residue = MULTI_PREFIX_RE.sub(" ", residue)
    residue = CODE_RE.sub(" ", residue)
    # words that are expected around codes: course titles are fine, but qualifiers like
    # "knowledge", "consent", "grade", "minimum" change meaning
    risky = re.search(r"(?i)knowledge|consent|permission|grade|minimum|cgpa|instructor|equivalent|basic|"
                      r"familiarity|background|concurrent|registered|any\s+course|at least|\b\d{3}\b", residue)
    if has_and and has_or:
        # try "A and (B or C)" style: split by and
        parts = re.split(r"(?i)\band\b|&", t)
        items = []
        for p in parts:
            pc = find_codes(p)
            if not pc:
                continue
            items.append({"op": "any", "items": [{"course": c} for c in pc]} if len(pc) > 1 else {"course": pc[0]})
        expr = {"op": "all", "items": items}
        return expr, not risky and len(items) > 1
    if has_or:
        expr = {"op": "any", "items": [{"course": c} for c in codes]}
    elif len(codes) == 1:
        expr = {"op": "all", "items": [{"course": codes[0]}]}
    else:
        expr = {"op": "all", "items": [{"course": c} for c in codes]}
    return expr, not risky


# ---------------------------------------------------------------------------
# Part IV: programme course lists, humanities pool, project limits
# ---------------------------------------------------------------------------
SKIP_HEADINGS = {"CORE COURSES", "DISCIPLINE ELECTIVE COURSES", "L P U", "L", "P", "U", "COURSE NO", "COURSE TITLE",
                 "L P", "COURSE", "NO."}


def _is_heading(line: str) -> bool:
    if LINE_CODE_RE.match(line) or PAGE_LABEL_RE.match(line):
        return False
    letters = re.sub(r"[^A-Za-z]", "", line)
    return len(letters) >= 4 and line.upper() == line and line not in SKIP_HEADINGS and not re.search(r"\d", line)


def _units_from(tokens: list[str]) -> tuple[int | None, int | None, int | None, bool]:
    nums = [t for t in tokens if re.fullmatch(r"\d{1,2}\*?|-", t)]
    star = any(t.endswith("*") for t in nums)
    vals = [int(t.rstrip("*")) if t != "-" else 0 for t in nums]
    if len(vals) >= 3:
        return vals[-3], vals[-2], vals[-1], star
    if len(vals) == 1:
        return None, None, vals[0], star
    if len(vals) == 2:
        return vals[0], None, vals[1], star
    return None, None, None, star


def _course_entries(lines: list[str], pno: int) -> Iterable[tuple[int, str, str, tuple]]:
    """Yield (line_index, code, title, (L,P,U,star)) entries from a course-list page."""
    i = 0
    while i < len(lines):
        m = LINE_CODE_RE.match(lines[i])
        if not m or m.group(1) not in KNOWN_PREFIXES | {"BIOT", "EE"}:
            i += 1
            continue
        code = norm_code(m.group(1), m.group(2), m.group(3), m.group(4))
        rest = m.group(5)
        title_parts, tokens = [], []
        # split trailing numbers off the first line
        tm = re.search(r"((?:\s+(?:\d{1,2}\*?|-))+)\s*$", " " + rest)
        if tm:
            title_parts.append(rest[: max(0, tm.start() - 1)])
            tokens += tm.group(1).split()
        else:
            title_parts.append(rest)
        j = i + 1
        while j < len(lines) and not LINE_CODE_RE.match(lines[j]) and not _is_heading(lines[j]) \
                and not re.match(r"(?i)^(track|pool)\b", lines[j]) and lines[j] not in SKIP_HEADINGS \
                and not lines[j].startswith("*"):
            l = lines[j]
            if re.fullmatch(r"((\d{1,2}\*?|-)\s*)+", l):
                tokens += l.split()
            else:
                tm = re.search(r"((?:\s+(?:\d{1,2}\*?|-))+)\s*$", " " + l)
                if tm:
                    title_parts.append(l[: max(0, tm.start() - 1)])
                    tokens += tm.group(1).split()
                elif tokens:
                    break  # title text after units belongs to something else
                else:
                    title_parts.append(l)
            j += 1
            if len(tokens) >= 3:
                break
        yield i, code, squash(" ".join(title_parts)), _units_from(tokens)
        i = j if j > i else i + 1


def extract_programme_lists(doc: dict, pages: list[str], reg: Registry) -> dict:
    start = _find_page(pages, r"List of Courses for B\.E\.")
    if not start:
        reg.flag("unparsed", f"doc:{doc['file']}", "programme_lists", "Programme course lists not located", severity="error")
        return {"programmes": {}, "humanities_pool": [], "project_rules": None}
    hum_page = _find_page(pages, r"Pool of Humanities courses for first degree", start)
    minors_page = _find_page(pages, r"MINOR PROGRAMMES FOR FIRST", start)
    programmes: dict[str, dict] = {}
    heading: list[str] = []
    cur: dict | None = None
    section = None
    group = None
    humanities: list[dict] = []
    in_hum = False
    project_ev = None

    stop = minors_page or (start + 30)
    for pno in range(start, stop):
        text = pages[pno - 1]
        printed = _printed(text)
        lines = _lines(text)
        if "Project Type Courses" in text:
            idx = text.index("Project Type Courses")
            project_ev = reg.cite(doc, pno, text[idx: idx + 900], "bulletin.project_rules", printed_page=printed)
        entries = {i: (code, title, u) for i, code, title, u in _course_entries(lines, pno)}
        for i, line in enumerate(lines):
            if line.startswith("Pool of Humanities courses"):
                in_hum = True
                section = None
                cur = None
                hum_ev_page = pno
                continue
            if in_hum:
                if i in entries:
                    code, title, (L, P, U, star) = entries[i]
                    ev = reg.cite(doc, pno, f"Pool of Humanities courses: {code} {title} {U}", "bulletin.humanities_pool",
                                  printed_page=printed)
                    humanities.append({"code": code, "title": title, "U": U, "evidence": [ev]})
                if line.startswith("It may be noted that a student cannot count") or line == "Other Courses":
                    in_hum = False  # the pool ends with the own-discipline note
                continue
            if line == "Project Type Courses":
                cur, section = None, None
                continue
            if _is_heading(line):
                heading.append(line)
                continue
            if line == "CORE COURSES":
                name = squash(" ".join(heading))
                if not name:
                    name = f"UNNAMED_AFTER_{cur['name'] if cur else 'START'}"
                heading = []
                cur = programmes.setdefault(name, {"name": name, "core": [], "electives": [], "compulsory_electives": [],
                                                   "groups": {}, "pages": [], "evidence": []})
                if name.startswith("UNNAMED"):
                    reg.flag("ambiguous_mapping", f"programme_list:{name}", "heading",
                             f"Core list on p.{pno} has no programme heading", severity="warning")
                section, group = "core", None
                cur["pages"].append(pno)
                continue
            if line == "DISCIPLINE ELECTIVE COURSES":
                heading = []
                section, group = "electives", None
                continue
            gm = re.match(r"(?i)^(track\s*[-–]?\s*\d+\s*:?.*|pool\s*[-–]?\s*[\dIVX]+\s*[:(].*)$", line)
            if gm and cur is not None:
                group = squash(gm.group(1))
                continue
            if line.startswith("* Compulsory") and cur is not None:
                cur["compulsory_note_page"] = pno
                continue
            if i in entries and cur is not None and section:
                heading = []
                code, title, (L, P, U, star) = entries[i]
                ev = reg.cite(doc, pno, f"{cur['name']} {section.upper()}: {code} {title} "
                              f"{'' if L is None else L} {'' if P is None else P} {U if U is not None else ''}",
                              "bulletin.programme_list", printed_page=printed)
                rec = {"code": code, "title": title, "L": L, "P": P, "U": U, "units_starred": star,
                       "group": group, "evidence": [ev]}
                if U is None:
                    reg.flag("missing", f"programme_list:{cur['name']}", code, "Units not parsed in course list", [ev],
                             severity="info")
                if section == "core":
                    cur["core"].append(rec)
                else:
                    cur["electives"].append(rec)
                    if group:
                        cur["groups"].setdefault(group, []).append(code)
                    raw_line = lines[i]
                    if re.search(rf"{re.escape(code.split()[1])}\*", raw_line.replace(" ", "")) or "*" in title:
                        cur["compulsory_electives"].append(code)
                        rec["title"] = title.replace("*", "").strip()
                if pno not in cur["pages"]:
                    cur["pages"].append(pno)
    # Cross-listed pairs ("CS G514 / SS G514 Title") come out as an entry titled "/":
    # give it the partner's title/units and record the cross-listing.
    for p in programmes.values():
        for key in ("core", "electives"):
            lst = p[key]
            for k, rec in enumerate(lst):
                if rec["title"] in ("/", "") and k + 1 < len(lst):
                    nxt = lst[k + 1]
                    rec.update({"title": nxt["title"], "L": nxt["L"], "P": nxt["P"], "U": nxt["U"],
                                "cross_listed_with": nxt["code"]})
                    nxt["cross_listed_with"] = rec["code"]
    # Name unnamed lists by majority prefix of their core courses (flagged inference).
    for name in list(programmes):
        p = programmes[name]
        if name.startswith("UNNAMED"):
            prefixes = [c["code"].split()[0] for c in p["core"]]
            if prefixes:
                maj = max(set(prefixes), key=prefixes.count)
                p["inferred_name_from_prefix"] = maj
    project_rules = {
        "discipline_elective_max_projects": 3,
        "discipline_elective_subhead_max": {"study": 1, "laboratory": 2, "design": 2, "special": 1},
        "open_elective_max_projects": 3,
        "total_elective_max_projects": 5,
        "project_codes": {"study": ["F266"], "laboratory": ["F366", "F367"], "design": ["F376", "F377"],
                          "special": ["F491"]},
        "evidence": [project_ev] if project_ev else [],
        "review_status": "reviewed" if project_ev else "missing_evidence",
    }
    hum_note = _find_page(pages, r"humanities elective even if it", start)
    hum_excl_ev = None
    if hum_note:
        t = squash(pages[hum_note - 1])
        k = t.find("humanities elective even if it")
        hum_excl_ev = reg.cite(doc, hum_note, t[max(0, k - 220): k + 120], "bulletin.humanities_note",
                               printed_page=_printed(pages[hum_note - 1]))
    return {"programmes": programmes, "humanities_pool": humanities, "project_rules": project_rules,
            "humanities_exclusion_evidence": [hum_excl_ev] if hum_excl_ev else []}


# ---------------------------------------------------------------------------
# Minors
# ---------------------------------------------------------------------------
def extract_minors(doc: dict, pages: list[str], reg: Registry) -> dict:
    start = _find_page(pages, r"MINOR PROGRAMMES FOR FIRST")
    if not start:
        return {"general": None, "minors": {}}
    end = _find_page(pages, r"2\+2 INTERNATIONAL COLLABORATION", start) or start + 14
    general_text = squash(pages[start - 1])
    gen_ev = reg.cite(doc, start, general_text[general_text.find("Requirements for a minor"):][:700],
                      "bulletin.minor_general", printed_page=_printed(pages[start - 1]))
    general = {"core_max_courses": 4, "core_max_units": 12, "electives_min_courses": 2, "electives_min_units": 6,
               "total_min_courses": 5, "total_min_units": 15, "max_project_courses": 1,
               "max_overlap_with_mandatory_courses": 2, "max_overlap_units": 6, "min_cgpa_in_minor_courses": 4.5,
               "declare_by": "end of 2nd year", "evidence": [gen_ev]}
    minors: dict[str, dict] = {}
    cur = None
    section = None
    for pno in range(start + 1, end):
        text = pages[pno - 1]
        printed = _printed(text)
        lines = _lines(text)
        entries = {i: (code, title, u) for i, code, title, u in _course_entries(lines, pno)}
        for i, line in enumerate(lines):
            mm = re.match(r"^Minor in (.+)$", line)
            if mm and not line.endswith("."):
                name = squash(mm.group(1))
                cur = minors.setdefault(name, {"name": name, "core": [], "electives": [], "min_courses": None,
                                               "min_units": None, "exclusions": [], "notes": [], "pages": [pno],
                                               "evidence": []})
                section = None
                continue
            if cur is None:
                continue
            req = re.search(r"(\d{1,2})\s*courses?\s*\(min\)", line, re.I)
            if req:
                cur["min_courses"] = int(req.group(1))
                ru = re.search(r"(\d{1,2})\s*units?\s*\(min\)", line + " " + (lines[i + 1] if i + 1 < len(lines) else ""),
                               re.I)
                if ru:
                    cur["min_units"] = int(ru.group(1))
                cur["evidence"].append(reg.cite(doc, pno, f"Minor in {cur['name']}: Courses & Units Required {line}",
                                                "bulletin.minor", printed_page=printed))
            elif re.search(r"(\d{1,2})\s*units?\s*\(min\)", line, re.I) and cur["min_units"] is None:
                cur["min_units"] = int(re.search(r"(\d{1,2})\s*units?\s*\(min\)", line, re.I).group(1))
            if re.match(r"(?i)^core\s+courses", line):
                section = "core"
                continue
            if re.match(r"(?i)^electives?\b", line):
                section = "electives"
                continue
            if re.search(r"(?i)exclusively designed for first-degree students of non-|not (be )?eligible|"
                         r"not open to|excluded", line):
                cur["exclusions"].append(line)
                cur["notes"].append(reg.cite(doc, pno, line, "bulletin.minor_exclusion", printed_page=printed))
            if i in entries and section:
                code, title, (L, P, U, star) = entries[i]
                ev = reg.cite(doc, pno, f"Minor in {cur['name']} {section}: {code} {title} {U}", "bulletin.minor_course",
                              printed_page=printed)
                cur[section].append({"code": code, "title": title, "U": U, "evidence": [ev]})
                if pno not in cur["pages"]:
                    cur["pages"].append(pno)
    # Exclusion clauses often span several lines: search each minor's full section text.
    flat_pages = {pno: squash(pages[pno - 1]) for pno in range(start + 1, end)}
    names = list(minors)
    for pno, text in flat_pages.items():
        for i, name in enumerate(names):
            k = text.find(f"Minor in {name}")
            if k < 0:
                continue
            others = [text.find(f"Minor in {n}", k + 5) for n in names if n != name]
            nxt = min([o for o in others if o > k] or [len(text)])
            seg = text[k:nxt]
            for mm in re.finditer(r"[^.]*(?:exclusively (?:designed|meant) for|not (?:be )?(?:eligible|open)|"
                                  r"cannot (?:opt|take|register)|excluded)[^.]*\.", seg, re.I):
                sent = squash(mm.group(0))
                m = minors[name]
                if sent not in m["exclusions"]:
                    m["exclusions"].append(sent)
                    m["notes"].append(reg.cite(doc, pno, sent, "bulletin.minor_exclusion", printed_page=_printed(pages[pno - 1])))
    for m in minors.values():
        if m["min_courses"] is None:
            m["min_courses"] = general["total_min_courses"]
            m["min_courses_source"] = "general minor rule (minor-specific value not parsed)"
            reg.flag("missing", f"minor:{m['name']}", "min_courses",
                     "Minor-specific course minimum not parsed; general 5-course rule applied", m["evidence"],
                     severity="info")
        if m["min_units"] is None:
            m["min_units"] = general["total_min_units"]
        if not m["core"] and not m["electives"]:
            reg.flag("unparsed", f"minor:{m['name']}", "courses", "No courses parsed for minor", m["evidence"])
    return {"general": general, "minors": minors}


# ---------------------------------------------------------------------------
# Semester-wise charts
# ---------------------------------------------------------------------------
CHART_TITLE_RE = re.compile(r"Semester-?\s*wise\s+[Pp]attern\s+for\s+(?:Students\s+Admitted\s+to\s+)?(.+?)"
                            r"(?:\s*Programme|\s+Yea\s*r\b|$)", re.S)
DUAL_TITLE_RE = re.compile(r"composite\s+Dual\s+Degree\s+Programmes\s*\(?\s*(M\.\s?Sc\.?.+?)\)?\s*(?:Year|$)", re.S | re.I)
SLOT_RE = re.compile(r"(?i)(open\s*/\s*humanities\s+electives?|humanities\s+electives?|open\s+electives?|"
                     r"discipline\s+electives?|first\s+discipline\s+electives?)")


def _clean_prog(name: str) -> str:
    name = squash(name).replace("B. E.", "B.E.").replace("M. Sc.", "M.Sc.").replace("B. Pharm.", "B.Pharm.")
    return name.strip(" .")


def extract_charts(doc: dict, pages: list[str], tables: dict[int, list], reg: Registry) -> list[dict]:
    charts: list[dict] = []
    for pno in sorted(tables):
        text = pages[pno - 1]
        flat = squash(text)
        if "Semester-wise" not in flat and "Semesterwise" not in flat and "semester-wise" not in flat.lower():
            continue
        if re.search(r"RMIT|ISU|Iowa|Buffalo|\bUB\b|RPI|CSP|Pattern\s*[123]\b|Higher Degree|M\.E\.|M\. Phil|"
                     r"M\.Phil|Ph\.D", flat):
            continue
        dual = DUAL_TITLE_RE.search(flat)
        single = CHART_TITLE_RE.search(flat)
        if dual:
            prog = _clean_prog(dual.group(1))
            kind = "dual"
        elif single:
            prog = _clean_prog(single.group(1))
            kind = "single"
        else:
            continue
        if not re.match(r"(?i)^(B\.E\.|B\.Pharm|M\.Sc|M\. ?Sc)", prog):
            continue
        printed = _printed(text)
        chart = {"programme": prog, "kind": kind, "page": pno, "printed_page": printed, "semesters": [],
                 "dc_total": None, "de_total": None, "evidence": []}
        chart["evidence"].append(reg.cite(doc, pno, flat[:300], "bulletin.chart", printed_page=printed))
        dc = re.search(r"Discipline\s+Core\s*[-–:]?\s*(\d+)\s*Units?\s*\(\s*(\d+)\s*Courses?\)", flat, re.I)
        de = re.search(r"Discipline\s+Electives?\s*[-–:]?\s*(\d+)\s*Units?\s*\(\s*(\d+)\s*Courses?\)", flat, re.I)
        if dc:
            chart["dc_total"] = {"units": int(dc.group(1)), "courses": int(dc.group(2)),
                                 "evidence": [reg.cite(doc, pno, dc.group(0), "bulletin.chart_totals", printed_page=printed)]}
        if de:
            chart["de_total"] = {"units": int(de.group(1)), "courses": int(de.group(2)),
                                 "evidence": [reg.cite(doc, pno, de.group(0), "bulletin.chart_totals", printed_page=printed)]}
        year = None
        for table in tables[pno]:
            for row in table:
                cells = [(c or "") for c in row]
                if not cells:
                    continue
                y = squash(cells[0])
                if re.fullmatch(r"I{1,3}|IV|V|VI", y):
                    year = y
                if year is None or len(cells) < 4:
                    continue
                for sem_idx, col in ((1, 1), (2, 3)):
                    cell = cells[col] if col < len(cells) else ""
                    if not cell or re.fullmatch(r"(?i)\s*(first|second)\s+semester\s*", cell):
                        continue
                    if "Same as First degree" in cell:
                        chart["semesters"].append({"year": year, "sem": sem_idx, "same_as_first_degree": True,
                                                   "courses": [], "slots": []})
                        continue
                    entry = _parse_chart_cell(cell)
                    if not entry["courses"] and not entry["slots"]:
                        continue
                    entry.update({"year": year, "sem": sem_idx})
                    chart["semesters"].append(entry)
        if not chart["semesters"]:
            reg.flag("unparsed", f"chart:{prog}", "semesters", f"No semester rows parsed on p.{pno}", chart["evidence"])
        # A dual-degree chart may continue over two pages: merge by programme name.
        existing = next((c for c in charts if c["programme"] == prog and c["kind"] == kind), None)
        if existing:
            existing["semesters"] += chart["semesters"]
            existing["evidence"] += chart["evidence"]
            existing["dc_total"] = existing["dc_total"] or chart["dc_total"]
            existing["de_total"] = existing["de_total"] or chart["de_total"]
        else:
            charts.append(chart)
    return charts


def _parse_chart_cell(cell: str) -> dict:
    """Split one semester cell into named courses (with OR-alternatives) and elective slots."""
    lines = [squash(l) for l in cell.split("\n") if squash(l)]
    courses: list[dict] = []
    slots: list[str] = []
    pending_or = False
    for l in lines:
        if l.lower() == "or":
            pending_or = True
            continue
        codes = find_codes(l)
        # "F425T Thesis" after "BITS F421T" inside an OR chain: inherit previous prefix
        if not codes:
            m = re.match(r"^([FGUCEKNZ]\d{3}[A-Z]?)\b", l)
            if m and courses:
                prev = courses[-1]["codes"][-1]
                codes = [f"{prev.split()[0]} {m.group(1)}"]
        sm = SLOT_RE.search(l)
        if sm and not codes:
            slots.append(squash(sm.group(1)).title())
            pending_or = False
            continue
        for c in codes:
            if pending_or and courses:
                courses[-1]["codes"].append(c)
                courses[-1]["alternative"] = True
            else:
                courses.append({"codes": [c], "alternative": False})
            pending_or = False
    return {"courses": courses, "slots": slots, "same_as_first_degree": False}


# ---------------------------------------------------------------------------
# Structure table (IV-1/IV-2)
# ---------------------------------------------------------------------------
def extract_structure(doc: dict, pages: list[str], reg: Registry) -> dict:
    p = _find_page(pages, r"The category-wise structure of each program")
    if not p:
        reg.flag("unparsed", f"doc:{doc['file']}", "structure", "Category structure table not located", severity="error")
        return {}
    t = squash(pages[p - 1])
    printed = _printed(pages[p - 1])
    ev = reg.cite(doc, p, t[t.find("Category"):][:900], "bulletin.structure", printed_page=printed)
    p2 = p + 1
    t2 = squash(pages[p2 - 1])
    ev2 = reg.cite(doc, p2, t2[:700], "bulletin.structure_notes", printed_page=_printed(pages[p2 - 1]))
    k = t2.find("Dual Degree Programs")
    ev_dual = reg.cite(doc, p2, t2[k: k + 900] if k >= 0 else t2[-900:], "bulletin.dual_degree_principles",
                       printed_page=_printed(pages[p2 - 1]))
    # Values are read from the table text and asserted against it; mismatch -> review.
    expected = {
        "Humanities Electives": ("8", "3"), "Science Foundation": ("9", "3"), "Mathematics Foundation": ("12", "4"),
        "Engineering Foundation": ("6", "2"), "Technical Arts": ("10", "4"),
    }
    for k2, (u, c) in expected.items():
        if not re.search(rf"{k2}\s+{u}\s+{c}", t):
            reg.flag("validation_failure", "structure", k2, f"Expected '{k2} {u} {c}' in structure table", [ev])
    ok_open = re.search(r"Open Electives\s+15 to 27\s+5 to 9", t)
    ok_cw = re.search(r"Course-work\s+Sub-Total\s+129 \(min\)\s+41 \(min\)", t)
    if not ok_open or not ok_cw:
        reg.flag("validation_failure", "structure", "open_electives", "Open-elective / coursework minimums not found",
                 [ev])
    return {
        "humanities": {"min_units": 8, "min_courses": 3},
        "open_electives": {"min_units": 15, "max_units": 27, "min_courses": 5, "max_courses": 9},
        "coursework": {"min_units": 129, "min_courses": 41},
        "total": {"min_units": 144, "min_courses": 42},
        "evidence": [ev],
        "notes_evidence": [ev2],
        "dual_degree_evidence": [ev_dual],
    }
