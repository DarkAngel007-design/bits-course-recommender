"""Shared ingestion primitives: evidence registry, review queue, course-code normalisation.

Every decision-bearing fact produced by a parser carries one or more evidence ids.
An evidence record pins the fact to a document, a 1-based PDF page, the printed
page label where known, and a short supporting excerpt. Ids are content hashes so
re-running ingestion over identical inputs yields identical ids (idempotency).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

PARSER_VERSION = "1.0.0"

_WS = re.compile(r"\s+")


def squash(text: str | None) -> str:
    """Collapse whitespace (including zero-width spaces some handouts contain)."""
    if not text:
        return ""
    text = text.replace("​", " ").replace(" ", " ").replace("﻿", " ")
    return _WS.sub(" ", text).strip()


def short_hash(*parts: Any, n: int = 12) -> str:
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return h[:n]


# ---------------------------------------------------------------------------
# Course codes
# ---------------------------------------------------------------------------
# BITS course numbers look like "CS F469", "BITS G564T", "BIO U101", "HSS F266".
# Level letter: F (first degree), G (higher degree), U (credit-hour framework,
# 2026 admissions), C (legacy), E/K/N/Z (special). A trailing letter (T) marks a
# non-letter-grade course and is part of the identity; it is never stripped.
CODE_RE = re.compile(r"\b([A-Z]{2,5})\s*[-]?\s*([FGUCEKNZ])\s?(\d{3})([A-Z]?)\b")
# Shared-prefix form used in the bulletin: "ECE/EEE/INSTR F211"
MULTI_PREFIX_RE = re.compile(r"\b((?:[A-Z]{2,5}\s*/\s*)+[A-Z]{2,5})\s+([FGUCEKNZ])\s?(\d{3})([A-Z]?)\b")

# Prefixes that appear in the supplied documents. Anything else found by the regex
# is treated as noise (e.g. "PAGE F101" would never be a course).
KNOWN_PREFIXES = {
    "AN", "AUE", "BIO", "BIOT", "BITS", "BBA", "CDP", "CE", "CHE", "CHEM", "CHI", "CS", "DE",
    "ECE", "ECOM", "ECON", "EEE", "EA", "ENGL", "ENV", "ENVS", "ES", "FIN", "FRE", "GER", "GS",
    "HSS", "HUM", "INSTR", "IS", "JAP", "MAC", "MATH", "MBA", "ME", "MEL", "MF", "MGTS", "MPBA",
    "MPH", "MSE", "MST", "PHA", "PHY", "QT", "RIA", "RUS", "SAN", "SNS", "SS", "SW", "ET", "MM",
    "AAOC", "TA", "BIT", "SANS", "MUSIC", "EE", "PHIL", "CDC", "DS", "EC", "MEC", "CSIS", "MMS", "SPA", "ARAB", "LIT",
}


def norm_code(prefix: str, level: str, number: str, suffix: str = "") -> str:
    return f"{prefix.upper()} {level.upper()}{number}{suffix.upper()}"


def find_codes(text: str) -> list[str]:
    """Return course codes in order of appearance, expanding shared prefixes."""
    text = squash(text)
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in MULTI_PREFIX_RE.finditer(text):
        prefixes = [p.strip() for p in m.group(1).split("/")]
        for p in prefixes:
            if p in KNOWN_PREFIXES:
                out.append((m.start(), norm_code(p, m.group(2), m.group(3), m.group(4))))
        spans.append((m.start(), m.end()))
    for m in CODE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        if m.group(1) in KNOWN_PREFIXES:
            out.append((m.start(), norm_code(m.group(1), m.group(2), m.group(3), m.group(4))))
    out.sort(key=lambda x: x[0])
    seen: list[str] = []
    for _, c in out:
        if c not in seen:
            seen.append(c)
    return seen


def parse_code(text: str, strict: bool = True) -> str | None:
    """First course code in text. strict=False accepts unknown prefixes; use it only for
    cells that are known to hold a course number (e.g. the timetable COURSE NO column)."""
    codes = find_codes(text)
    if codes:
        return codes[0]
    if not strict:
        m = CODE_RE.search(squash(text))
        if m:
            return norm_code(m.group(1), m.group(2), m.group(3), m.group(4))
    return None


def code_level(code: str) -> str:
    """Return the level letter of a normalised course code (F/G/U/...)."""
    return code.split(" ")[1][0]


def code_prefix(code: str) -> str:
    return code.split(" ")[0]


def code_number(code: str) -> int:
    m = re.search(r"(\d{3})", code)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# Evidence + review queue
# ---------------------------------------------------------------------------
@dataclass
class Evidence:
    id: str
    doc_id: str
    file: str
    family: str
    pdf_page: int
    printed_page: str | None
    excerpt: str
    parser: str
    confidence: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ReviewItem:
    id: str
    kind: str  # missing | conflict | unreliable | unparsed | ambiguous_mapping | validation_failure
    severity: str  # info | warning | error
    subject: str  # e.g. "course:CS F469" or "handout:175_CS_F469.pdf"
    field: str
    message: str
    evidence: list[str] = field(default_factory=list)
    status: str = "open"  # open | accepted | rejected (maintainer decision)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Registry:
    """Collects evidence and review items produced during one ingestion run."""

    def __init__(self) -> None:
        self.evidence: dict[str, Evidence] = {}
        self.review: dict[str, ReviewItem] = {}

    def cite(
        self,
        doc: dict,
        pdf_page: int,
        excerpt: str,
        parser: str,
        confidence: float = 1.0,
        printed_page: str | None = None,
    ) -> str:
        excerpt = squash(excerpt)
        if len(excerpt) > 600:
            excerpt = excerpt[:597] + "..."
        eid = "ev_" + short_hash(doc["doc_id"], pdf_page, excerpt, parser)
        if eid not in self.evidence:
            self.evidence[eid] = Evidence(
                id=eid,
                doc_id=doc["doc_id"],
                file=doc["file"],
                family=doc["family"],
                pdf_page=pdf_page,
                printed_page=printed_page,
                excerpt=excerpt,
                parser=f"{parser}@{PARSER_VERSION}",
                confidence=round(confidence, 3),
            )
        return eid

    def flag(
        self,
        kind: str,
        subject: str,
        field_name: str,
        message: str,
        evidence: list[str] | None = None,
        severity: str = "warning",
    ) -> str:
        rid = "rv_" + short_hash(kind, subject, field_name, message)
        if rid not in self.review:
            self.review[rid] = ReviewItem(
                id=rid,
                kind=kind,
                severity=severity,
                subject=subject,
                field=field_name,
                message=message,
                evidence=list(evidence or []),
            )
        return rid
