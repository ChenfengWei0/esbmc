#!/usr/bin/env python3
"""Insert source-only RQ3 replays when the contract is exact but unit differs.

This is deliberately not a PUT match: the closure records the differing RQ3
unit/path and keeps Forge and PUT credit disabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rq1-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--item", action="append", nargs=3, metavar=("CASE", "SOURCE", "TEST"), required=True)
    ns = ap.parse_args()
    rows = []
    for case, source_name, test in ns.item:
        source = Path(source_name)
        benchmark, subject = case.split("/", 1)
        target = ns.rq1_root / benchmark / "subjects" / subject
        result = target / "result.json"
        document = json.loads(result.read_text(encoding="utf-8"))
        identity = next((x["frozen_identity"] for x in document.get("rq3_mechanical_closure", [])
                         if x.get("frozen_identity", [None])[0] == case
                         and x["frozen_identity"][2] in ("tokenBalance", "pastBlockTime")), None)
        if identity is None:
            raise SystemExit(f"identity not found: {case}")
        key = hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]
        destination = target / "put" / "rq3-mechanical" / "unindexed-source-cross-identity" / key
        row = {"identity": identity, "source": str(source), "status": "source-only-cross-identity",
               "source_sha256": sha256(source), "destination": str(destination), "test": test}
        if ns.apply:
            copied = destination / "test" / source.name
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            closures = document.setdefault("rq3_mechanical_closure", [])
            closures[:] = [x for x in closures if x.get("frozen_identity") != identity]
            closures.append({
                "schema": "veriput-rq3-mechanical-source-only-cross-identity/v1",
                "frozen_identity": identity,
                "rq3_identity": None,
                "match_tier": "same-contract-source-different-unit",
                "source": str(copied), "source_sha256": sha256(copied), "test": test,
                "put_json": None, "forge_run": False, "put_credit": False,
                "source_only": True, "identity_rewrite": False,
                "rq3_source_unit_differs": True,
            })
            result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            row["result"] = str(result)
        rows.append(row)
    report = {"schema": "veriput-rq3-cross-identity-insert/v1", "apply": ns.apply,
              "rows": rows, "summary": {"rows": len(rows), "inserted": sum(r["status"].startswith("source") for r in rows)},
              "policy": {"esbmc_run": False, "forge_run": False, "put_credit": False}}
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    ns.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
