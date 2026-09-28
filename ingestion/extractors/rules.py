"""Validate curated academic rules against the supplied PDFs and attach evidence."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..common import Registry, squash

RULES_FILE = Path(__file__).resolve().parent.parent / "rules" / "academic_rules.yaml"


def _norm(t: str) -> str:
    t = squash(t)
    t = (t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
         .replace("–", "-").replace("—", "-").replace("−", "-"))
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    return t.lower()


def load_and_validate(docs_by_family: dict[str, tuple[dict, list[str]]], reg: Registry) -> dict:
    data = yaml.safe_load(RULES_FILE.read_text())
    norm_pages = {fam: [_norm(p) for p in pages] for fam, (_, pages) in docs_by_family.items()}
    out = []
    for rule in data["rules"]:
        src = rule["source"]
        fam = src["family"]
        needle = _norm(src["excerpt"])
        rule = dict(rule)
        found = None
        for i, page in enumerate(norm_pages.get(fam, []), start=1):
            if needle in page:
                found = i
                break
        if found is None:
            rule["review_status"] = "rejected_missing_evidence"
            rule["evidence"] = []
            reg.flag("validation_failure", f"rule:{rule['id']}", "source",
                     f"Excerpt not found in {fam}: '{src['excerpt'][:90]}'", severity="error")
        else:
            doc, pages = docs_by_family[fam]
            first = next((l.strip() for l in pages[found - 1].splitlines() if l.strip()), None)
            printed = first if first and len(first) <= 6 else None
            rule["evidence"] = [reg.cite(doc, found, src["excerpt"], "rules.curated", printed_page=printed)]
            rule["review_status"] = "reviewed"
        out.append(rule)
    return {"version": data["version"], "reviewed_by": data.get("reviewed_by"), "rules": out}
