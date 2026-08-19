#!/usr/bin/env python3
"""Remove the legacy unverified RQ3 rows from RQ1 PUT arrays.

The mechanical closure is retained in ``rq3_mechanical_closure`` and in its
copied source directories.  Older insertion attempts also appended rows to
``put.valid_artifacts`` without a Forge run; those rows are removed so PUT and
valid counters retain their original meaning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove-legacy-trees", action="store_true")
    args = parser.parse_args()
    changes = []
    for result in sorted(args.rq1_root.glob("*/subjects/*/result.json")):
        document = json.loads(result.read_text(encoding="utf-8"))
        put = document.get("put")
        if not isinstance(put, dict):
            continue
        raw = put.get("raw_artifacts")
        valid = put.get("valid_artifacts")
        if not isinstance(raw, list) or not isinstance(valid, list):
            continue
        is_legacy = lambda row: isinstance(row, dict) and (
            "rq3-mechanical" in str(row.get("put_json") or "")
            or isinstance(row.get("mechanical_origin"), dict))
        raw_new = [row for row in raw if not is_legacy(row)]
        valid_new = [row for row in valid if not is_legacy(row)]
        removed = len(raw) - len(raw_new) + len(valid) - len(valid_new)
        if not removed:
            continue
        changes.append({"result": str(result), "removed_raw": len(raw) - len(raw_new),
                        "removed_valid": len(valid) - len(valid_new)})
        if args.apply:
            put["raw_artifacts"] = raw_new
            put["valid_artifacts"] = valid_new
            put["raw"] = len(raw_new)
            put["valid"] = len(valid_new)
            put["concrete_raw"] = sum(row.get("kind") == "concrete"
                                      for row in raw_new if isinstance(row, dict))
            put["concrete_valid"] = sum(row.get("kind") == "concrete"
                                        for row in valid_new if isinstance(row, dict))
            result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    legacy_trees = []
    for tree in sorted(args.rq1_root.glob("*/subjects/*/put/**/rq3-mechanical/*")):
        if not tree.is_dir() or not (tree / "project").is_dir() or not (tree / "_wd").is_dir():
            continue
        legacy_trees.append(str(tree))
        if args.apply and args.remove_legacy_trees:
            import shutil
            shutil.rmtree(tree)
    output = {"schema": "veriput-rq3-mechanical-cleanup/v1",
              "apply": args.apply, "files": changes,
              "legacy_trees": legacy_trees,
              "summary": {"result_files": len(changes),
                          "removed_raw": sum(x["removed_raw"] for x in changes),
                          "removed_valid": sum(x["removed_valid"] for x in changes)},
              "policy": {"closure_preserved": True, "esbmc_run": False,
                         "forge_run": False}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(output["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
