#!/usr/bin/env python3
"""Close the RQ3-to-RQ1 mechanical insertion ledger for every target row.

Physical replay files are inserted by the source-backed/fallback passes.  This
pass adds the remaining bound metadata and explicit missing rows to each RQ1
``result.json`` so all frozen identities have one mechanical closure record.
It never treats metadata or a missing artifact as a replay, PUT, Forge result,
or generalized proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def subject(root: Path, identity: list[str]) -> Path:
    benchmark, case = identity[0].split("/", 1)
    return root / benchmark / "subjects" / case


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding", type=Path)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.binding.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    backups: list[str] = []
    for binding in document.get("rows", []):
        identity = list(binding["frozen_identity"])
        target = subject(args.rq1_root, identity)
        result = target / "result.json"
        selected = binding.get("selected") or {}
        source = selected.get("file") if selected.get("file_exists") else None
        if binding.get("binding_status") == "unbound":
            status = "missing-rq3-artifact"
        elif source:
            status = "source-backed"
        else:
            status = "metadata-only"
        entry: dict[str, Any] = {
            "schema": "veriput-rq3-mechanical-closure-entry/v2",
            "frozen_identity": identity,
            "rq3_identity": selected.get("identity"),
            "binding_status": binding.get("binding_status"),
            "binding_tier": binding.get("binding_tier"),
            "candidate_count": binding.get("candidate_count", 0),
            "status": status,
            "source": source,
            "test": selected.get("test"),
            "put_json": selected.get("put_json"),
            "source_sha256": (sha256(Path(source)) if source and Path(source).is_file()
                               else None),
            "put_json_sha256": (sha256(Path(selected["put_json"]))
                                if selected.get("put_json")
                                and Path(selected["put_json"]).is_file() else None),
            "forge_run": False,
            "put_credit": False,
            "identity_rewrite": False,
        }
        rows.append({"identity": identity, "status": status, "result": str(result)})
        if not args.apply:
            continue
        if not result.is_file():
            raise SystemExit(f"missing RQ1 result.json: {result}")
        current = json.loads(result.read_text(encoding="utf-8"))
        closures = current.setdefault("rq3_mechanical_closure", [])
        existing = [item for item in closures
                    if item.get("frozen_identity") == identity]
        # Keep the physical source-backed/source-only record created by the
        # earlier passes.  This pass only fills metadata-only or missing rows.
        physical = any(item.get("status") in {"source-backed", "source-only"}
                       or item.get("source_only") is True for item in existing)
        if physical:
            for item in existing:
                if item.get("source_only") is True:
                    item["status"] = "source-only"
            rows[-1]["status"] = "already-physical"
            result.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
            continue
        backups.append(str(result))
        closures[:] = [item for item in closures
                       if item.get("frozen_identity") != identity]
        closures.append(entry)
        result.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    summary = {
        "rows": len(rows),
        "source_backed": sum(row["status"] == "source-backed" for row in rows),
        "metadata_only": sum(row["status"] == "metadata-only" for row in rows),
        "missing_rq3_artifact": sum(row["status"] == "missing-rq3-artifact"
                                     for row in rows),
        "apply": args.apply,
    }
    output = {"schema": "veriput-rq3-mechanical-metadata-insertion/v1",
              "binding": str(args.binding.resolve()),
              "binding_sha256": sha256(args.binding),
              "summary": summary, "rows": rows, "result_preimages": backups,
              "policy": {"esbmc_run": False, "forge_run": False,
                         "put_credit": False, "identity_rewrite": False}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
