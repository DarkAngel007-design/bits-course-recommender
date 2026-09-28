"""Infer JSON Schemas for the published snapshot files -> schemas/snapshot-<version>/.

The schemas document the dataset contract for consumers; `python -m ingestion verify`
remains the authoritative publication check.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "data" / "published"
snap = PUB / (PUB / "CURRENT").read_text().strip()
MAP_FILES = {"courses.json", "offerings.json", "handouts.json", "programmes.json", "curricula.json", "evidence.json"}


def tname(v):
    return {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", float: "number",
            type(None): "null"}[type(v)]


def merge(a, b):
    if a is None:
        return b
    ta = set(a["type"]) if isinstance(a["type"], list) else {a["type"]}
    tb = set(b["type"]) if isinstance(b["type"], list) else {b["type"]}
    t = sorted(ta | tb)
    out = {"type": t[0] if len(t) == 1 else t}
    if "properties" in a or "properties" in b:
        props = dict(a.get("properties", {}))
        for k, v in b.get("properties", {}).items():
            props[k] = merge(props.get(k), v)
        out["properties"] = props
        out["required"] = sorted(set(a.get("required", props)) & set(b.get("required", props)))
    if "items" in a or "items" in b:
        out["items"] = merge(a.get("items"), b.get("items")) if a.get("items") and b.get("items") else a.get("items") or b.get("items")
    return out


def infer(v, depth=0):
    s = {"type": tname(v)}
    if isinstance(v, dict) and depth < 8:
        s["properties"] = {k: infer(x, depth + 1) for k, x in v.items()}
        s["required"] = sorted(v)
    elif isinstance(v, list) and v and depth < 8:
        it = None
        for x in v[:400]:
            it = merge(it, infer(x, depth + 1))
        s["items"] = it
    return s


manifest = json.loads((snap / "manifest.json").read_text())
out = ROOT / "schemas" / f"snapshot-{manifest['schema_version']}"
out.mkdir(parents=True, exist_ok=True)
for f in sorted(snap.glob("*.json")):
    data = json.loads(f.read_text())
    if f.name in MAP_FILES:
        vals = None
        for v in list(data.values())[:1500]:
            vals = merge(vals, infer(v))
        schema = {"type": "object", "description": f"Map of id -> record ({f.stem})", "additionalProperties": vals}
    else:
        schema = infer(data)
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": f.stem,
              "x-schema-version": manifest["schema_version"], **schema}
    (out / f"{f.stem}.schema.json").write_text(json.dumps(schema, indent=1))
print(f"wrote {len(list(out.glob('*.json')))} schemas to {out.relative_to(ROOT)}")
