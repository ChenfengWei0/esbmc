#!/usr/bin/env python3
"""Physically insert exact-identity RQ3 emitted Solidity sources.

This covers emitted ``*.cov.t.sol`` files under the exact RQ3
``unit/pf/path`` and ``enc/certify-results`` directory.  It never runs a
checker and never grants PUT credit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_for(rq3: Path, identity: list[str]) -> Path | None:
    case, path_function, unit, enc, _piece = identity
    benchmark, subject = case.split("/", 1)
    root = rq3 / benchmark / "subjects" / subject
    suffix = f"__{unit}__pf{path_function.rsplit('#', 1)[-1]}"
    files: list[Path] = []
    for directory in sorted(root.glob(f"put/**/*{suffix}")):
        if not directory.is_dir():
            continue
        for source in directory.rglob("*.t.sol"):
            if "/lib/" in str(source):
                continue
            if f"__{enc}__certify-results" in str(source):
                files.append(source)
    if not files:
        return None
    files.sort(key=lambda p: (0 if "/test/" in str(p) else 1,
                             0 if "test_cov_1" in p.read_text(errors="replace") else 1,
                             str(p)))
    return files[0]


def target_root(rq1: Path, identity: list[str]) -> Path:
    benchmark, subject = identity[0].split("/", 1)
    return rq1 / benchmark / "subjects" / subject


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binding", type=Path)
    parser.add_argument("--rq3-root", type=Path, required=True)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    binding = json.loads(args.binding.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in binding["rows"]:
        identity = [str(x) for x in item["frozen_identity"]]
        result = target_root(args.rq1_root, identity) / "result.json"
        try:
            document = json.loads(result.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            document = {"rq3_mechanical_closure": []}
        closures = document.setdefault("rq3_mechanical_closure", [])
        existing = next((x for x in closures
                         if x.get("frozen_identity") == identity), None)
        if existing and existing.get("source") and Path(existing["source"]).exists():
            continue
        source = source_for(args.rq3_root, identity)
        row: dict[str, Any] = {"identity": identity, "status": "missing"}
        if source is None:
            rows.append(row)
            continue
        key = hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]
        destination = target_root(args.rq1_root, identity) / "put" / "rq3-mechanical" / "exact-folder-source" / key
        row.update({"status": "source-backed", "source": str(source),
                    "source_sha256": sha256(source), "destination": str(destination)})
        if args.apply:
            copied = destination / "test" / source.name
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            project = next((p for p in (source.parent, *source.parents)
                            if (p / "foundry.toml").is_file()), None)
            flat = project / "src" / "flat.sol" if project else None
            flat_copy = None
            if flat and flat.is_file():
                flat_copy = destination / "src" / "flat.sol"
                flat_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(flat, flat_copy)
            closures[:] = [x for x in closures
                           if x.get("frozen_identity") != identity]
            closures.append({
                "schema": "veriput-rq3-mechanical-exact-folder-source/v1",
                "frozen_identity": identity, "rq3_identity": identity,
                "match_tier": "exact-folder-source-scan",
                "source": str(copied), "source_sha256": sha256(copied),
                "test": next((x.group(1) for x in __import__("re").finditer(
                    r"function\s+(test_[A-Za-z0-9_]+)\s*\(",
                    copied.read_text(errors="replace"))), None),
                "put_json": None, "forge_run": False, "put_credit": False,
                "source_only": True,
                "flat_source": str(flat_copy) if flat_copy else None,
            })
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
        rows.append(row)
    summary = {"rows": len(rows),
               "source_backed": sum(x["status"] == "source-backed" for x in rows),
               "missing": sum(x["status"] == "missing" for x in rows),
               "apply": args.apply}
    report = {"schema": "veriput-rq3-mechanical-exact-folder-source/v1",
              "summary": summary, "rows": rows,
              "policy": {"esbmc_run": False, "forge_run": False,
                         "put_credit": False, "identity_rewrite": False}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
