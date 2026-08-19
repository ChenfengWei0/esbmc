#!/usr/bin/env python3
"""Audit unresolved RQ3 identities for physically available Solidity sources.

This intentionally does not infer a source from a neighboring unit.  It only
accepts a ``.t.sol`` below the exact RQ3 subject and exact unit/path folder,
and records manifest cardinalities to make genuine source absence explicit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def path_number(path_function: str) -> str:
    return path_function.rsplit("#", 1)[-1] if "#" in path_function else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binding", type=Path)
    parser.add_argument("--rq3-root", type=Path, required=True)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binding = json.loads(args.binding.read_text(encoding="utf-8"))
    rows = []
    for record in binding["rows"]:
        identity = record["frozen_identity"]
        case, path_function, unit, _enc, _piece = identity
        benchmark, subject = case.split("/", 1)
        result = args.rq1_root / benchmark / "subjects" / subject / "result.json"
        document = json.loads(result.read_text(encoding="utf-8"))
        closure = [entry for entry in document.get("rq3_mechanical_closure", [])
                   if entry.get("frozen_identity") == identity]
        if closure and closure[0].get("source") and Path(closure[0]["source"]).is_file():
            continue
        subject_dir = args.rq3_root / benchmark / "subjects" / subject
        suffix = f"__{unit}__pf{path_number(path_function)}"
        exact_files = []
        if subject_dir.is_dir():
            for folder in (subject_dir / "put").glob(f"*{suffix}"):
                if folder.is_dir():
                    exact_files.extend(str(path) for path in folder.rglob("*.t.sol"))
        manifests = []
        for manifest in subject_dir.rglob("manifest.json") if subject_dir.is_dir() else []:
            try:
                manifests.extend(json.loads(manifest.read_text(encoding="utf-8")).get("entries", []))
            except (OSError, ValueError):
                continue
        rows.append({"frozen_identity": identity,
                     "subject_exists": subject_dir.is_dir(),
                     "exact_unit_path_sources": sorted(exact_files),
                     "manifest_entries": len(manifests)})
    output = {"schema": "rq3-mechanical-remaining-physical-audit/v1",
              "rows": len(rows), "entries": rows,
              "policy": {"esbmc_run": False, "forge_run": False,
                         "cross_identity_fallback": False}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"rows": len(rows),
                      "with_exact_source": sum(bool(row["exact_unit_path_sources"])
                                                for row in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
