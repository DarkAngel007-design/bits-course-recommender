"""Course handout (Part II) parser.

Extracts course numbers, title, instructor-in-charge, description, evaluation components,
midsem/compre presence, attendance and make-up policy clauses, and syllabus text for topic
search. Explicit negatives (e.g. midsem_present = False) are only produced when the
evaluation table was parsed completely (weights total 100%); otherwise the value is unknown.
"""
from __future__ import annotations

import re

from ..common import Registry, find_codes, parse_code, squash

COMPONENT_PATTERNS = [
    ("midsem", r"mid[\s\-–]*sem(?:ester)?|mid[\s\-–]*term|\bmst\b|mid[\s\-–]*semester"),
    ("compre", r"compre(?:hensive)?|end[\s\-–]*sem(?:ester)?|final\s+exam(?:ination)?|\bcompre\b|\bce\b"),
    ("quiz", r"quiz(?:zes)?|surprise\s+test|class\s+test|\btests?\b"),
    ("assignment", r"assignments?|home\s*work|take[\s\-]*home|problem\s+sets?|tutorial"),
    ("project", r"projects?|term\s+paper|mini[\s\-]*project|capstone|design\s+exercise"),
    ("lab", r"\blab(?:oratory)?\b|practical|experiments?|lab\s+exam"),
    ("viva", r"viva"),
    ("presentation", r"presentations?|seminars?"),
    ("report", r"reports?|diary|journal"),
    ("case_study", r"case\s+stud(?:y|ies)"),
    ("participation", r"participation|class\s+(?:activity|activities|engagement)|discussion"),
    ("attendance", r"attendance"),
]
_COMP_RX = [(k, re.compile(p, re.I)) for k, p in COMPONENT_PATTERNS]


MID_RX = re.compile(r"(?i)\bmid\b|mid[\s\-–.]*(?:sem|term|test|exam)|midsem|mid[\s\-.]*semester")
AMBIG_MID_RX = re.compile(r"(?i)^\W*semester\s+test|^\W*(?:periodical|sessional)\s+test")


def classify_component(name: str) -> str:
    n = squash(name)
    if MID_RX.search(n):
        # "Mid-sem presentation/report/seminar/viva" is a mid-semester project evaluation, not an exam
        for k in ("presentation", "report", "viva"):
            if dict(_COMP_RX)[k].search(n):
                return k
        return "midsem"
    if AMBIG_MID_RX.search(n):
        return "ambiguous_test"
    for k, rx in _COMP_RX:
        if k == "midsem":
            continue
        if rx.search(n):
            return k
    return "other"


WEIGHT_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*(?:\(.*\))?\s*$")
WEIGHT_ANY_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
DURATION_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(min|mins|minutes|hrs?|hours?)\b")


def _nature(text: str) -> str | None:
    t = text.lower()
    if "partly" in t or "partial" in t:
        return "partly_open_book"
    if "open" in t and ("close" in t or "closed" in t) and "/" in t:
        return "open_or_closed_book"
    if "open" in t:
        return "open_book"
    if "close" in t:
        return "closed_book"
    if "take home" in t or "take-home" in t:
        return "take_home"
    return None


def _duration_minutes(text: str) -> int | None:
    m = DURATION_RE.search(text or "")
    if not m:
        return None
    v = float(m.group(1))
    return int(v * 60) if m.group(2).lower().startswith("h") else int(v)


PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*\*?\s*%")
BARE_NUM_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*\*?\s*(?:\(.*\))?\s*$")


def _header_info(table: list) -> tuple[int, dict] | None:
    for ri, row in enumerate(table[:4]):
        cells = [squash(c).lower() for c in row]
        joined = " ".join(c for c in cells if c)
        if re.search(r"weight|wt\.?\s*\(|marks|%|percentage", joined) and re.search(
                r"component|evaluation|type|test|exam|module", joined):
            find = lambda rx: next((i for i, h in enumerate(cells) if re.search(rx, h)), None)
            info = {
                "name": find(r"component|evaluation|type|module|^test|^exam"),
                "weight": find(r"weight|wt|%|percentage"),
                "marks": find(r"marks"),
                "duration": find(r"duration"),
                "date": find(r"date"),
                "nature": find(r"nature|comment|remark"),
                "width": len(row),
            }
            if info["name"] is None:
                info["name"] = 0
            return ri, info
    return None


def _row_values(cells: list[str], info: dict) -> tuple[str, float | None, float | None, str]:
    """Return (name, pct_weight, bare_number_near_weight_col, raw) for one row."""
    name = cells[info["name"]] if info["name"] < len(cells) else ""
    if (not name or re.fullmatch(r"\d{1,2}\.?", name)) and len(cells) > info["name"] + 1:
        name = cells[info["name"] + 1]  # serial-number column first
    skip = {info["name"], info["duration"], info["date"]}
    pct, bare, raw = None, None, ""
    for i, c in enumerate(cells):
        if not c or i in skip:
            continue
        m = PCT_RE.search(c)
        if m and pct is None and not DURATION_RE.search(c):
            pct, raw = float(m.group(1)), c
    anchor = info["weight"] if info["weight"] is not None else info["marks"]
    if anchor is not None:
        for off in (0, 1, -1, 2, 3):
            i = anchor + off
            if 0 <= i < len(cells) and i not in skip and cells[i]:
                m = BARE_NUM_RE.match(cells[i])
                if m:
                    bare = float(m.group(1))
                    raw = raw or cells[i]
                    break
    return name, pct, bare, raw


def _parse_eval_tables(tables_by_page: dict[int, list]) -> tuple[list[dict], int | None]:
    """Find the evaluation table (header row mentioning weight/marks) and read its rows,
    following a continuation table on the next page. Percent-marked values win over bare
    numbers; a marks-only table is normalised to percentages and labelled as such."""
    found: list[tuple[list[dict], int]] = []
    for pno in sorted(tables_by_page):
        for ti, table in enumerate(tables_by_page[pno]):
            if not table:
                continue
            hi = _header_info(table)
            if hi is None:
                continue
            ri, info = hi
            rows = [(pno, r) for r in table[ri + 1:]]
            nxt = tables_by_page.get(pno + 1, []) if ti == len(tables_by_page[pno]) - 1 else []
            if nxt and nxt[0]:
                nh = _header_info(nxt[0])
                if nh is None and len(nxt[0][0]) == info["width"]:
                    rows += [(pno + 1, r) for r in nxt[0]]
                elif nh is not None and nh[1]["width"] == info["width"]:
                    rows += [(pno + 1, r) for r in nxt[0][nh[0] + 1:]]  # header repeated on next page
            parsed = []
            for rp, row in rows:
                cells = [squash(c) for c in row]
                if not any(cells):
                    continue
                name, pct, bare, raw = _row_values(cells, info)
                if not name or re.search(r"(?i)^(total|course total|overall)", name):
                    continue
                if name[:1] in "#*•●" or len(name) > 90:
                    continue
                if re.fullmatch(r"(?i)\(?%\)?|weightage.*|marks.*|component.*|\d+(?:\.\d+)?\s*%?", name):
                    continue
                parsed.append({"name": name, "pct": pct, "bare": bare, "raw": raw, "cells": cells, "page": rp})
            if not parsed:
                continue
            pcts = [r["pct"] for r in parsed]
            bares = [r["bare"] for r in parsed]
            normalized = False
            if all(v is not None for v in pcts) and 99 <= sum(pcts) <= 101:
                weights = pcts
            elif all(v is not None for v in bares) and 99 <= sum(bares) <= 101:
                weights = bares
            elif all((p if p is not None else b) is not None for p, b in zip(pcts, bares)) and \
                    99 <= sum((p if p is not None else b) for p, b in zip(pcts, bares)) <= 101:
                weights = [p if p is not None else b for p, b in zip(pcts, bares)]
            elif info["weight"] is None and info["marks"] is not None and all(v is not None for v in bares) and sum(bares):
                total = sum(bares)
                weights = [round(b * 100.0 / total, 2) for b in bares]
                normalized = True
            else:
                weights = [p if p is not None else b for p, b in zip(pcts, bares)]
            comps = []
            candidates_note = None
            for r, w in zip(parsed, weights):
                rest = " ".join(r["cells"])
                cells = r["cells"]
                comps.append({
                    "name": r["name"],
                    "type": classify_component(r["name"]),
                    "weight": w,
                    "weight_raw": r["raw"] or None,
                    "weight_normalized_from_marks": normalized,
                    "duration_min": _duration_minutes(cells[info["duration"]] if info["duration"] is not None
                                                      and info["duration"] < len(cells) else rest),
                    "date_raw": (cells[info["date"]] if info["date"] is not None and info["date"] < len(cells)
                                 else None) or None,
                    "nature": _nature(cells[info["nature"]] if info["nature"] is not None and info["nature"] < len(cells)
                                      else rest),
                    "page": r["page"],
                })
            found.append((comps, pno))
            if comps_closed(comps) and all(c["weight"] is not None for c in comps):
                return comps, pno
    if not found:
        return [], None
    # no complete table: return the candidate closest to a 100% total
    found.sort(key=lambda x: (abs(100 - sum(c["weight"] or 0 for c in x[0])), -len(x[0])))
    return found[0]


def comps_closed(comps: list[dict]) -> bool:
    total = sum(c["weight"] or 0 for c in comps)
    return 99.0 <= total <= 101.0


SECTION_RX = re.compile(r"(?im)^\s*(\d{1,2})\s*[.)]\s*([A-Z][A-Za-z &/\-]{3,60}?)\s*:?\s*$")


def _section(text: str, head_rx: str, max_len: int = 3000) -> tuple[str, int] | None:
    m = re.search(head_rx, text, re.I)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"\n\s*\d{1,2}\s*[.)]\s+[A-Z][A-Za-z &/\-]{3,40}\s*:?", text[start:])
    end = start + (nxt.start() if nxt else max_len)
    return text[start:min(end, start + max_len)], m.start()


def _page_of(offset: int, page_offsets: list[int]) -> int:
    pno = 1
    for i, o in enumerate(page_offsets, start=1):
        if offset >= o:
            pno = i
    return pno


def extract_handout(doc: dict, pages_text: list[str], tables: dict[int, list], reg: Registry, file_code: str | None) -> dict:
    offsets, parts, pos = [], [], 0
    for t in pages_text:
        offsets.append(pos)
        parts.append(t)
        pos += len(t) + 1
    full = "\n".join(parts)
    flat = squash(full)
    subject = f"handout:{doc['file']}"
    rec: dict = {"doc_id": doc["doc_id"], "files": [doc["file"]], "sha256": doc["sha256"], "pages": len(pages_text)}

    if len(flat) < 300:
        reg.flag("unparsed", subject, "text", "Handout has no extractable text layer (scanned image); OCR required",
                 severity="error")
        rec.update({"extraction_status": "no_text_layer", "course_codes": [file_code] if file_code else [],
                    "evidence": {}})
        return rec

    ev: dict[str, list[str]] = {}

    def cite(field: str, pno: int, excerpt: str, conf: float = 0.9) -> str:
        e = reg.cite(doc, pno, excerpt, f"handout.{field}", confidence=conf)
        ev.setdefault(field, []).append(e)
        return e

    # --- course numbers & title -------------------------------------------------
    head = flat[:1500]
    m = re.search(r"(?i)course\s*(?:no\.?|number|code)s?\s*[:\-–]?\s*(.{0,140})", head)
    codes = find_codes(m.group(1)) if m else []
    if not codes:
        codes = find_codes(head)[:3]
    if file_code and file_code not in codes:
        if codes:
            reg.flag("ambiguous_mapping", subject, "course_codes",
                     f"File name code {file_code} not among handout course numbers {codes}", severity="info")
        codes = [file_code] + codes
    rec["course_codes"] = codes
    cite("course_codes", 1, (m.group(0) if m else head)[:220])

    tm = re.search(r"(?i)course\s*title\s*[:\-–]?\s*(.{3,120}?)(?:\s+instructor|\s+course\s+no|\s+ic\s*:|\s{2,}|$)", head)
    title = squash(tm.group(1)).strip(" :") if tm else None
    if title and re.match(r"(?i)^:?\s*instructor", title):
        title = None
    if not title:
        # "Course No / Course Title / Instructor" label block followed by ': value' lines
        vals = re.findall(r"(?m)^\s*:\s*(.+)$", "\n".join(pages_text[:1]))
        if len(vals) >= 2:
            title = squash(vals[1])
    rec["title"] = title
    im = re.search(r"(?i)instructor[\s\-]*in[\s\-]*charge\s*[:\-–]?\s*([A-Z][A-Za-z.\s]{3,60}?)(?:\s*\(|\s*,|\s+email|\s+e-mail|\s+TA\b|\s+Instructors?\b|\s+Course|\s{2,}|$)",
                   head)
    rec["instructor_in_charge"] = squash(im.group(1)) if im else None

    # --- description / scope ----------------------------------------------------
    desc = _section(full, r"course\s+description\s*:?", 2500)
    scope = _section(full, r"scope\s*(?:&|and)\s*objectives?(?:\s+of\s+the\s+course)?\s*:?", 2500)
    rec["description"] = squash(desc[0]) if desc else None
    rec["scope"] = squash(scope[0]) if scope else None
    if desc:
        cite("description", _page_of(desc[1], offsets), squash(desc[0])[:300], 0.8)
    plan = _section(full, r"(?:course\s+plan|lecture[\s\-]*wise\s+plan|lecture\s+plan|course\s+content|syllabus)\s*:?", 12000)
    rec["syllabus_text"] = squash(plan[0])[:6000] if plan else None

    # --- evaluation ----------------------------------------------------------------
    comps, epage = _parse_eval_tables(tables)
    method = "table"
    if not (comps and comps_closed(comps) and all(c["weight"] is not None for c in comps)):
        tcomps, tpage = _parse_eval_text(full, offsets)
        if tcomps and comps_closed(tcomps) or not comps:
            comps, epage, method = tcomps, tpage, "text"
    total = round(sum(c["weight"] or 0 for c in comps), 2)
    complete = bool(comps) and 99.0 <= total <= 101.0 and all(c["weight"] is not None for c in comps)
    rec["evaluation"] = {"components": comps, "total_weight": total, "complete": complete, "method": method}
    if comps:
        exc = "; ".join(f"{c['name']} {c['weight_raw'] or ''}".strip() for c in comps)
        cite("evaluation", epage or comps[0]["page"], f"Evaluation scheme: {exc}", 0.95 if complete else 0.6)
    if not comps:
        reg.flag("missing", subject, "evaluation", "Evaluation scheme not found or not parseable")
    elif not complete:
        reg.flag("unreliable", subject, "evaluation",
                 f"Evaluation weights total {total}% (expected 100%); presence/absence facts left unknown",
                 ev.get("evaluation"))

    def presence(kind: str) -> dict:
        hits = [c for c in comps if c["type"] == kind and (c["weight"] or 0) > 0]
        if hits:
            return {"value": True, "status": "stated", "weight": sum(c["weight"] or 0 for c in hits),
                    "evidence": ev.get("evaluation", [])}
        blocked = kind == "midsem" and any(c["type"] == "ambiguous_test" for c in comps)
        if complete and not blocked:
            return {"value": False, "status": "explicit_from_complete_table", "weight": 0,
                    "evidence": ev.get("evaluation", [])}
        return {"value": None, "status": "unknown", "weight": None, "evidence": ev.get("evaluation", [])}

    rec["midsem"] = presence("midsem")
    rec["compre"] = presence("compre")
    for kind in ("quiz", "assignment", "project", "lab", "viva", "presentation", "case_study", "attendance"):
        rec.setdefault("components_present", {})[kind] = presence(kind)
    ob = sum(c["weight"] or 0 for c in comps if c["nature"] in ("open_book", "take_home"))
    rec["open_book_weight"] = ob if complete else None
    # midsem details (open/closed book, duration)
    mids = [c for c in comps if c["type"] == "midsem"]
    if mids:
        rec["midsem"]["nature"] = mids[0]["nature"]
        rec["midsem"]["duration_min"] = mids[0]["duration_min"]

    # --- attendance ----------------------------------------------------------------
    att_sents = []
    for mm in re.finditer(r"(?i)[^.\n]*attendance[^.\n]*(?:\.|\n)", full):
        s = squash(mm.group(0))
        if len(s) > 12:
            att_sents.append((s, _page_of(mm.start(), offsets)))
    min_pct = None
    pct_rx = re.compile(r"(?i)attendance[^.%]{0,40}?(?:minimum|at least|below|less than|above|under)\s*(?:of\s*)?"
                        r"(\d{2,3})\s*%|(\d{2,3})\s*%\s*(?:of\s+(?:the\s+)?(?:classes|lectures|sessions|tutorials)|"
                        r"attendance)")
    for s, p in att_sents:
        pm = pct_rx.search(s)
        if pm and not re.search(r"(?i)\bNC\b|course total|total marks", s):
            min_pct = int(pm.group(1) or pm.group(2))
            cite("attendance", p, s)
            break
    negated = [s for s, _ in att_sents if re.search(r"(?i)(?:not|no)\s+(?:be\s+)?(?:mandatory|compulsory|required)|"
                                                    r"attendance\s+(?:is\s+)?optional", s)]
    mandatory = [s for s, _ in att_sents if re.search(r"(?i)mandatory|compulsory|must attend|will not be allowed|debarred|"
                                                      r"not be permitted|shortage|linked to attendance|attendance[\s-]*"
                                                      r"(?:linked|based|associated)", s) and s not in negated]
    att_component = rec["components_present"]["attendance"]
    linked = any(re.search(r"(?i)attendance", c["name"]) for c in comps)
    rec["attendance"] = {
        "statements": [s for s, _ in att_sents][:6],
        "min_percentage": min_pct,
        "marks_component": True if linked else att_component["value"],
        "mandatory_statements": mandatory[:3],
        "not_mandatory_statements": negated[:3],
    }
    for s, p in att_sents[:3]:
        cite("attendance", p, s, 0.85)
    # Status is derived from explicit text only. "none_found" means the whole handout was read,
    # no attendance clause exists and the complete evaluation table has no attendance component:
    # that is absence of a policy, NOT evidence of "no attendance requirement".
    if min_pct is not None or rec["attendance"]["marks_component"] or mandatory:
        status = "requirement_stated"
    elif negated:
        status = "explicitly_not_required"
    elif att_sents:
        status = "statement_without_requirement"
    elif complete:
        status = "none_found"
    else:
        status = "unknown"
    rec["attendance"]["status"] = status

    # --- make-up policy --------------------------------------------------------------
    mk = []
    for mm in re.finditer(r"(?i)(make[\s\-]?up[^\n]{0,20}(?:polic(?:y|ies)|:)?)", full):
        seg = squash(full[mm.start(): mm.start() + 600])
        seg = re.split(r"(?i)\s(?:\d{1,2}\s*[.)]\s+[A-Z][a-z]+|NC\s+(?:Policy|Criteri)|Notices?\s*:|Chamber|Plagiarism|"
                       r"Instructor[\s-]in[\s-]charge\s*$)", seg)[0]
        mk.append((seg[:480], _page_of(mm.start(), offsets)))
    uniq = []
    for s, p in mk:
        if not any(s[:60] == u[0][:60] for u in uniq):
            uniq.append((s, p))
    rec["makeup"] = classify_makeup(" ".join(s for s, _ in uniq)) if uniq else {"status": "not_stated"}
    rec["makeup"]["text"] = [s for s, _ in uniq][:3]
    for s, p in uniq[:2]:
        cite("makeup", p, s, 0.85)

    # --- NC policy -------------------------------------------------------------------
    nc = re.search(r"(?i)NC\s*(?:policy|criteri(?:on|a))\s*:?(.{0,400})", flat)
    rec["nc_policy"] = squash(nc.group(1))[:300] if nc else None

    rec["evidence"] = ev
    rec["extraction_status"] = "ok" if comps and complete else ("partial" if comps else "no_evaluation")
    return rec


def classify_makeup(text: str) -> dict:
    t = text.lower()
    conds = []
    if re.search(r"medical|illness|hospital|sick", t):
        conds.append("medical")
    if re.search(r"only|strictly|extreme|serious|genuine|exceptional", t):
        conds.append("restricted_cases")
    if re.search(r"prior\s+(?:permission|approval|intimation)|before the (?:test|exam)", t):
        conds.append("prior_permission")
    if re.search(r"document|certificate|proof", t):
        conds.append("documentary_proof")
    none_for = []
    for comp in ("quiz", "assignment", "lab", "project", "tutorial", "surprise", "class test", "presentation"):
        if re.search(rf"no\s+make[\s\-]?ups?\s+(?:will be (?:given|allowed|granted|provided)\s+)?(?:for\s+)?(?:any\s+)?(?:the\s+)?[\w\s,/&]*?{comp}", t) \
                or re.search(rf"{comp}[\w\s,/&()]*?(?:will|shall)\s+(?:have|be)\s+no\s+make[\s\-]?up", t) \
                or re.search(rf"make[\s\-]?up[\w\s]*?(?:will\s+)?not\s+be\s+(?:given|allowed|granted|provided)[\w\s]*{comp}", t):
            none_for.append(comp)
    no_makeup_at_all = bool(re.search(r"no\s+make[\s\-]?up\s+(?:will be|shall be|is)\s+(?:given|allowed|granted|provided)"
                                      r"(?!\s+for)", t)) and not re.search(r"make[\s\-]?up\s+(?:will|shall)\s+be\s+(?:given|"
                                                                              r"allowed|granted|provided)", t)
    allowed_for = []
    for comp, rx in (("midsem", r"mid[\s\-]?sem"), ("compre", r"compre")):
        if re.search(rx, t):
            allowed_for.append(comp)
    return {
        "status": "stated",
        "conditions": conds,
        "no_makeup_for": none_for,
        "no_makeup_at_all": no_makeup_at_all,
        "mentions_components": allowed_for,
    }


def _parse_eval_text(full: str, offsets: list[int]) -> tuple[list[dict], int | None]:
    """Fallback: read 'Component ... NN%' pairs from the evaluation section text."""
    sec = _section(full, r"evaluation\s+(?:scheme|components?|plan|procedure)\s*:?", 2500)
    if not sec:
        return [], None
    text, off = sec
    page = _page_of(off, offsets)
    comps = []
    lines = [squash(l) for l in text.split("\n") if squash(l)]
    cur = None
    for l in lines:
        kind = classify_component(l) if not WEIGHT_RE.match(l) else "other"
        if kind != "other" and not re.fullmatch(r"(?i)(weightage|weight|duration|date.*|component.*|nature.*)", l):
            cur = {"name": l[:80], "type": kind, "weight": None, "weight_raw": None, "duration_min": _duration_minutes(l),
                   "date_raw": None, "nature": _nature(l), "page": page}
            comps.append(cur)
            wm = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", l)
            if wm:
                cur["weight"], cur["weight_raw"] = float(wm.group(1)), wm.group(0)
            continue
        if cur is not None and cur["weight"] is None:
            wm = WEIGHT_RE.match(l) or re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", l)
            if wm and not DURATION_RE.search(l):
                v = float(wm.group(1))
                if 0 < v <= 100:
                    cur["weight"], cur["weight_raw"] = v, l
        if cur is not None:
            cur["duration_min"] = cur["duration_min"] or _duration_minutes(l)
            cur["nature"] = cur["nature"] or _nature(l)
    comps = [c for c in comps if c["weight"] is not None]
    return comps, page
