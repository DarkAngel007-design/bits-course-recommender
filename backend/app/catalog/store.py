"""Read-only access to a published dataset snapshot.

Loaded once into memory (the reference dataset is ~15 MB of JSON). Request handlers never
touch raw PDFs. Several snapshots can be held at once so saved plans can be compared with
the snapshot they were created against.
"""
from __future__ import annotations

import json
import os
from functools import cached_property
from pathlib import Path
from threading import Lock

DEFAULT_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[3] / "data" / "published"))


class Snapshot:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.id = path.name
        load = lambda n: json.loads((path / n).read_text())  # noqa: E731
        self.manifest = load("manifest.json")
        self.courses: dict[str, dict] = load("courses.json")
        self.offerings: dict[str, dict] = load("offerings.json")
        self.handouts: dict[str, dict] = load("handouts.json")
        self.programmes: dict[str, dict] = load("programmes.json")
        self.curricula: dict[str, dict] = load("curricula.json")
        self.minors: dict = load("minors.json")
        self.humanities: dict = load("humanities_pool.json")
        self.project_rules: dict = load("project_rules.json")
        self.structure: dict = load("structure.json")
        self.rules_doc: dict = load("rules.json")
        self.relationships: list = load("relationships.json")
        self.slot_legend: dict = load("slot_legend.json")
        self.topics: dict = load("topics.json")
        self.evidence: dict[str, dict] = load("evidence.json")
        self.review_queue: list = load("review_queue.json")
        self.coverage: dict = load("coverage.json")
        self.term = self.manifest["term"]
        self.rules = {r["id"]: r for r in self.rules_doc["rules"] if r["review_status"] == "reviewed"}

    # ------------------------------------------------------------------ lookups
    @property
    def rules_version(self) -> str:
        return self.rules_doc["version"]

    @cached_property
    def offerings_by_course(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for o in self.offerings.values():
            out.setdefault(o["course_code"], []).append(o)
        return out

    @cached_property
    def humanities_codes(self) -> set[str]:
        return {c["code"] for c in self.humanities["courses"]}

    @cached_property
    def humanities_evidence(self) -> dict[str, list[str]]:
        return {c["code"]: c["evidence"] for c in self.humanities["courses"]}

    @cached_property
    def equivalents(self) -> dict[str, set[str]]:
        """Symmetric equivalence map from listed equivalence rows only (never shared handouts)."""
        eq: dict[str, set[str]] = {}
        for r in self.relationships:
            if r["type"] != "equivalence":
                continue
            a, b = r["source"], r["target"]
            eq.setdefault(a, set()).add(b)
            eq.setdefault(b, set()).add(a)
        return eq

    @cached_property
    def equivalence_evidence(self) -> dict[tuple[str, str], list[str]]:
        out = {}
        for r in self.relationships:
            if r["type"] == "equivalence":
                out[(r["source"], r["target"])] = r["evidence"]
                out[(r["target"], r["source"])] = r["evidence"]
        return out

    @cached_property
    def list_membership(self) -> dict[str, list[tuple[str, str]]]:
        """course code -> [(programme_id, 'core'|'elective')] across all programme lists."""
        out: dict[str, list[tuple[str, str]]] = {}
        for pid, p in self.programmes.items():
            for c in p["core"]:
                out.setdefault(c, []).append((pid, "core"))
            for c in p["electives"]:
                out.setdefault(c, []).append((pid, "elective"))
        return out

    def rule(self, rid: str) -> dict | None:
        return self.rules.get(rid)

    def rule_evidence(self, rid: str) -> list[str]:
        r = self.rules.get(rid)
        return list(r["evidence"]) if r else []

    def course_units(self, code: str) -> int | None:
        c = self.courses.get(code)
        if c and c.get("U") is not None:
            return c["U"]
        for o in self.offerings_by_course.get(code, []):
            if o["credit"]["system"] == "units" and o["credit"]["value"] is not None:
                return o["credit"]["value"]
        return None

    def course_title(self, code: str) -> str:
        c = self.courses.get(code)
        return (c or {}).get("title") or code

    def handouts_for(self, code: str) -> list[dict]:
        c = self.courses.get(code)
        return [self.handouts[h] for h in (c or {}).get("handouts", []) if h in self.handouts]

    def evidence_record(self, eid: str) -> dict | None:
        return self.evidence.get(eid)


class SnapshotStore:
    """Holds the CURRENT snapshot plus any older snapshot a saved plan refers to."""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self._cache: dict[str, Snapshot] = {}
        self._lock = Lock()
        self._current_id: str | None = None

    def current_id(self) -> str:
        p = self.data_dir / "CURRENT"
        if not p.exists():
            raise RuntimeError(f"No published snapshot in {self.data_dir}; run `python -m ingestion build`")
        return p.read_text().strip()

    def current(self) -> Snapshot:
        return self.get(self.current_id())

    def get(self, snap_id: str) -> Snapshot:
        with self._lock:
            if snap_id not in self._cache:
                path = self.data_dir / snap_id
                if not path.is_dir():
                    raise KeyError(snap_id)
                self._cache[snap_id] = Snapshot(path)
            return self._cache[snap_id]

    def list(self) -> list[str]:
        return sorted(p.name for p in self.data_dir.iterdir() if p.is_dir() and p.name.startswith("snap-"))

    def set_current(self, snap_id: str) -> None:
        if not (self.data_dir / snap_id).is_dir():
            raise KeyError(snap_id)
        tmp = self.data_dir / ".CURRENT.tmp"
        tmp.write_text(snap_id + "\n")
        os.replace(tmp, self.data_dir / "CURRENT")


_store: SnapshotStore | None = None


def get_store() -> SnapshotStore:
    global _store
    if _store is None:
        _store = SnapshotStore()
    return _store
