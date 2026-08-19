#!/usr/bin/env python3
"""Insert RQ3 emitted Solidity sources that lack a usable put.json index.

This is a source-only continuation of the RQ3 mechanical closure.  It uses
the frozen identity's subject/unit/path-function/enc to locate the emitted
``*.t.sol`` under the RQ3 subject, copies the source and flat source into an
RQ1 subject-local closure directory, and records ``forge_run=false`` and
``put_credit=false``.  It never invents a PUT or a Forge result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def target_subject(root: Path, identity: list[str]) -> Path:
    benchmark, subject = identity[0].split("/", 1)
    return root / benchmark / "subjects" / subject


def path_number(path_function: str) -> str:
    return path_function.rsplit("#", 1)[-1] if "#" in path_function else ""


def find_source(rq3_root: Path, identity: list[str]) -> tuple[Path, str] | None:
    case, path_function, unit, enc, _piece = identity
    benchmark, subject = case.split("/", 1)
    subject_dir = rq3_root / benchmark / "subjects" / subject
    if not subject_dir.is_dir():
        return None
    suffix = f"__{unit}__pf{path_number(path_function)}"
    candidates: list[tuple[Path, str]] = []
    for directory in sorted(subject_dir.glob(f"put/**/*{suffix}")):
        if not directory.is_dir():
            continue
        for source in sorted(directory.rglob("*.t.sol")):
            if f"__{enc}__certify-results" not in str(source):
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            names = re.findall(r"function\s+(test_[A-Za-z0-9_]+)\s*\(", text)
            if not names:
                continue
            test = "test_cov_1" if "test_cov_1" in names else names[0]
            candidates.append((source, test))
    if not candidates:
        return None
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("match", type=Path)
    parser.add_argument("--rq3-root", type=Path, required=True)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.match.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for group in report.get("missing", []):
        identity = [str(value) for value in group["frozen_identity"]]
        found = find_source(args.rq3_root, identity)
        row: dict[str, Any] = {"identity": identity, "status": "missing"}
        if found is None:
            rows.append(row)
            continue
        source, test = found
        target = target_subject(args.rq1_root, identity)
        key = hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]
        destination = target / "put" / "rq3-mechanical" / "unindexed-source" / key
        row.update({"status": "source-only", "source": str(source), "test": test,
                    "source_sha256": digest(source), "destination": str(destination)})
        if args.apply:
            destination.mkdir(parents=True, exist_ok=True)
            copied = destination / "test" / source.name
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            source_project = next((p for p in (source.parent, *source.parents)
                                   if (p / "foundry.toml").is_file()), None)
            flat = source_project / "src" / "flat.sol" if source_project else None
            flat_copy = None
            if flat is not None and flat.is_file():
                flat_copy = destination / "src" / "flat.sol"
                flat_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(flat, flat_copy)
            result = target / "result.json"
            document = json.loads(result.read_text(encoding="utf-8"))
            closures = document.setdefault("rq3_mechanical_closure", [])
            closures[:] = [entry for entry in closures
                           if entry.get("frozen_identity") != identity]
            closures.append({
                "schema": "veriput-rq3-mechanical-source-only/v1",
                "frozen_identity": identity,
                "rq3_identity": identity,
                "match_tier": "missing-rq3-put-json-source-scan",
                "source": str(copied),
                "source_sha256": digest(copied),
                "test": test,
                "put_json": None,
                "forge_run": False,
                "put_credit": False,
                "source_only": True,
                "flat_source": str(flat_copy) if flat_copy else None,
            })
            result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
            row["result"] = str(result)
        rows.append(row)
    summary = {"rows": len(rows),
               "source_only": sum(row["status"] == "source-only" for row in rows),
               "missing": sum(row["status"] == "missing" for row in rows),
               "apply": args.apply}
    output = {"schema": "veriput-rq3-mechanical-source-fallback/v1",
              "summary": summary, "rows": rows,
              "policy": {"esbmc_run": False, "forge_run": False,
                         "put_credit": False, "identity_rewrite": False}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
