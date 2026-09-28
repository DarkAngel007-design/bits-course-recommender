"""CLI: python -m ingestion {build,verify,list}"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .build import build, verify


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m ingestion", description="BITS dataset ingestion pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="extract, validate and publish a snapshot")
    b.add_argument("--raw", default="dataset", help="directory holding the supplied PDFs")
    b.add_argument("--out", default="data/published")
    b.add_argument("--staging", default="data/staging")
    b.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    b.add_argument("--force", action="store_true", help="rebuild even if the snapshot already exists")
    v = sub.add_parser("verify", help="re-run publication checks on a snapshot")
    v.add_argument("--snapshot", default=None)
    v.add_argument("--out", default="data/published")
    sub.add_parser("list", help="list published snapshots").add_argument("--out", default="data/published")
    a = ap.parse_args()
    if a.cmd == "build":
        path = build(Path(a.raw), Path(a.out), Path(a.staging), a.workers, a.force)
        print(path)
    elif a.cmd == "verify":
        snap = Path(a.snapshot) if a.snapshot else Path(a.out) / (Path(a.out) / "CURRENT").read_text().strip()
        problems = verify(snap)
        print("OK" if not problems else "\n".join(problems))
        raise SystemExit(1 if problems else 0)
    elif a.cmd == "list":
        out = Path(a.out)
        cur = (out / "CURRENT").read_text().strip() if (out / "CURRENT").exists() else None
        for d in sorted(p for p in out.iterdir() if p.is_dir() and p.name.startswith("snap-")):
            print(("* " if d.name == cur else "  ") + d.name)


if __name__ == "__main__":
    main()
