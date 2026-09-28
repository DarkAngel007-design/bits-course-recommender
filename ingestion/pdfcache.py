"""Cached page-level text and table extraction.

Extraction is the slow step (the bulletin has 951 pages), so results are cached in
data/staging keyed by file content hash. The cache is purely an optimisation: deleting
it and re-running produces byte-identical published output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pdfplumber
import pymupdf

from .common import PARSER_VERSION

CACHE_VERSION = f"pc2-{PARSER_VERSION}"


class PdfCache:
    def __init__(self, staging: Path) -> None:
        self.dir = staging / "pdfcache"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, doc: dict, kind: str) -> Path:
        return self.dir / f"{doc['sha256'][:24]}.{kind}.{CACHE_VERSION}.json"

    def text(self, doc: dict) -> list[str]:
        """Plain text per page (index 0 = PDF page 1)."""
        p = self._path(doc, "text")
        if p.exists():
            return json.loads(p.read_text())
        with pymupdf.open(doc["path"]) as d:
            pages = [pg.get_text() for pg in d]
        p.write_text(json.dumps(pages))
        return pages

    def tables(self, doc: dict, pages: list[int] | None = None) -> dict[int, list]:
        """pdfplumber tables per 1-based page number."""
        key = "tables" if pages is None else f"tables-{pages[0]}-{pages[-1]}-{len(pages)}"
        p = self._path(doc, key)
        if p.exists():
            return {int(k): v for k, v in json.loads(p.read_text()).items()}
        out: dict[int, list] = {}
        with pdfplumber.open(doc["path"]) as pdf:
            wanted = pages or list(range(1, len(pdf.pages) + 1))
            for n in wanted:
                if 1 <= n <= len(pdf.pages):
                    try:
                        out[n] = pdf.pages[n - 1].extract_tables()
                    except Exception:  # pragma: no cover - malformed page
                        out[n] = []
        p.write_text(json.dumps(out))
        return out
