"""Lexical retrieval over course text (BM25) plus taxonomy-topic relevance.

The index covers every course in the snapshot; it is scored over the *academically
eligible* candidate set only, so semantic top-k never hides a valid course (Architecture §8).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from ..catalog.store import Snapshot

TOKEN = re.compile(r"[a-z][a-z0-9+#]*")
STOP = set("""a an the of and or in on for to with by from as at is are be this that these those it its into
course courses students student will study studies introduction intro basic basics fundamentals concepts
principles topics topic various using use used also including include such their them which etc one two
suggest want need prefer preferably related something like some any about""".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 1]


class CourseIndex:
    def __init__(self, snap: Snapshot) -> None:
        self.docs: dict[str, Counter] = {}
        self.len: dict[str, int] = {}
        for code, c in snap.courses.items():
            parts = [c.get("title") or "", c.get("title") or "", c.get("description") or ""]
            for h in snap.handouts_for(code):
                parts += [h.get("description") or "", h.get("scope") or "", (h.get("syllabus_text") or "")[:3000]]
            toks = tokenize(" ".join(parts))
            self.docs[code] = Counter(toks)
            self.len[code] = len(toks)
        n = len(self.docs)
        df = Counter(t for d in self.docs.values() for t in d)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.avg = sum(self.len.values()) / max(1, n)

    def bm25(self, code: str, terms: list[str], k1: float = 1.4, b: float = 0.75) -> float:
        d = self.docs.get(code)
        if not d:
            return 0.0
        s = 0.0
        for t in terms:
            f = d.get(t, 0)
            if f:
                s += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * self.len[code] / self.avg))
        return s


_indexes: dict[str, CourseIndex] = {}


def get_index(snap: Snapshot) -> CourseIndex:
    if snap.id not in _indexes:
        _indexes[snap.id] = CourseIndex(snap)
    return _indexes[snap.id]


def topic_relevance(course: dict, topics: list[str]) -> tuple[float, list[dict]]:
    """0..1.3 relevance from stored topic tags, plus the keyword hits that justify it."""
    if not topics:
        return 0.0, []
    best, hits = 0.0, []
    for t in topics:
        tag = (course.get("topics") or {}).get(t)
        if tag:
            title_hit = any(h["field"] == "title" for h in tag["hits"])
            # saturating curve so strong, title-level matches outrank passing mentions
            r = 1 - math.exp(-tag["score"] / 20.0)
            best = max(best, min(1.3, r + (0.3 if title_hit else 0)))
            hits.append({"topic": t, "score": tag["score"], "hits": tag["hits"][:4]})
    return best, hits
