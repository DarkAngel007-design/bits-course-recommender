"""Build the input manifest: every supplied file with hash, document family and page count."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pymupdf

from .common import short_hash


def classify(rel: str) -> str:
    name = rel.lower()
    if "/handouts/" in f"/{name}" or name.startswith("handouts/"):
        return "handout"
    if "regulation" in name:
        return "regulations"
    if "bulletin" in name:
        return "bulletin"
    if "timetable" in name:
        return "timetable"
    return "unknown"


def build_manifest(raw_dir: Path) -> list[dict]:
    docs: list[dict] = []
    for root, dirs, files in os.walk(raw_dir):
        dirs[:] = sorted(d for d in dirs if d != "__MACOSX" and not d.startswith("."))
        for f in sorted(files):
            if f.startswith(".") or not f.lower().endswith(".pdf"):
                continue
            path = Path(root) / f
            rel = path.relative_to(raw_dir).as_posix()
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            try:
                with pymupdf.open(stream=data, filetype="pdf") as d:
                    pages = len(d)
                status = "ok"
            except Exception as exc:  # pragma: no cover - corrupt input
                pages, status = 0, f"unreadable: {exc}"
            docs.append(
                {
                    "doc_id": "doc_" + short_hash(rel),
                    "file": rel,
                    "path": str(path),
                    "sha256": sha,
                    "bytes": len(data),
                    "family": classify(rel),
                    "pages": pages,
                    "status": status,
                }
            )
    return docs


def content_groups(docs: list[dict]) -> dict[str, list[str]]:
    """Map content hash -> files sharing identical bytes (duplicate handouts)."""
    groups: dict[str, list[str]] = {}
    for d in docs:
        groups.setdefault(d["sha256"], []).append(d["file"])
    return groups
