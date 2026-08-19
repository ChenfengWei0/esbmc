#!/usr/bin/env python3
"""Materialize and validate RQ3 mechanical concrete matches in isolation.

This deliberately never edits a RQ3 source, put.json, or result.json.  It
copies each candidate project to a durable scratch directory, duplicates the
retained concrete test as a concrete anchor, and requires exact Forge JSON
success for both tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_success(stdout: str, test_name: str) -> tuple[bool, str]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return False, f"Forge JSON invalid: {exc}"
    rows = []
    values = list(payload.values()) if isinstance(payload, dict) else payload
    for value in values if isinstance(values, (list, tuple)) else []:
        if not isinstance(value, dict):
            continue
        for named, test in (value.get("test_results") or {}).items():
            if named.split("(", 1)[0] == test_name and isinstance(test, dict):
                rows.append(test)
        for test in value.get("tests", []):
            if isinstance(test, dict) and test.get("test") == test_name:
                rows.append(test)
    if len(rows) != 1:
        return False, f"expected one JSON result for {test_name}, got {len(rows)}"
    status = rows[0].get("status")
    if status != "Success":
        return False, f"{test_name}: status={status!r}"
    return True, "Success"


def duplicate_test(source: str, test_name: str, anchor_name: str) -> str:
    marker = f"function {test_name}"
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"test function {test_name!r} not found")
    opening = source.find("{", start)
    if opening < 0:
        raise ValueError("test function has no body")
    depth = 0
    closing = None
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                closing = index + 1
                break
    if closing is None:
        raise ValueError("unterminated test function")
    function = source[start:closing].replace(
        f"function {test_name}", f"function {anchor_name}", 1)
    return source[:closing] + "\n\n  // RQ3 mechanically recovered concrete anchor.\n  " + function + source[closing:]


def run_forge(project: Path, source: Path, test_name: str) -> tuple[bool, str, int]:
    relative = source.relative_to(project)
    command = ["forge", "test", "--json", "--match-path", str(relative),
               "--match-test", test_name]
    process = subprocess.run(command, cwd=project, text=True, capture_output=True,
                             check=False)
    ok, reason = exact_success(process.stdout, test_name) if process.returncode == 0 else (
        False, f"Forge rc={process.returncode}")
    return ok, reason, process.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    document = json.loads(args.match.read_text(encoding="utf-8"))
    candidates = [item for item in document["mechanical_candidates"]
                  if isinstance(item.get("candidate"), dict)]
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "rq3-mechanical-materialization/v1", "rows": []}
    for ordinal, item in enumerate(candidates[:args.limit], 1):
        candidate = item["candidate"]
        row = {"ordinal": ordinal, "identity": item.get("frozen_identity"),
               "source": candidate.get("file"), "put_json": candidate.get("put_json"),
               "status": "refused"}
        source = Path(str(candidate.get("file", "")))
        if not source.is_file():
            row["reason"] = "candidate source is absent"
            report["rows"].append(row)
            continue
        project = next((parent for parent in (source.parent, *source.parents)
                        if (parent / "foundry.toml").is_file()), None)
        if project is None:
            row["reason"] = "Foundry project root is absent"
            report["rows"].append(row)
            continue
        target = args.output / f"{ordinal:03d}-{sha256(source)[:16]}"
        shutil.copytree(project, target, symlinks=True)
        copied = target / source.relative_to(project)
        test_name = str(candidate.get("test") or "")
        anchor_name = f"test_ce_anchor_rq3_{ordinal:03d}"
        try:
            copied.write_text(duplicate_test(copied.read_text(encoding="utf-8"), test_name,
                                             anchor_name), encoding="utf-8")
        except (OSError, ValueError) as exc:
            row["reason"] = str(exc)
            report["rows"].append(row)
            continue
        put_ok, put_reason, put_rc = run_forge(target, copied, test_name)
        anchor_ok, anchor_reason, anchor_rc = run_forge(target, copied, anchor_name)
        row.update(anchor_test=anchor_name, put_forge_ok=put_ok,
                   anchor_forge_ok=anchor_ok, put_returncode=put_rc,
                   anchor_returncode=anchor_rc, put_reason=put_reason,
                   anchor_reason=anchor_reason)
        if put_ok and anchor_ok:
            row["status"] = "validated"
        else:
            row["reason"] = " or ".join(reason for ok, reason in
                                         ((put_ok, put_reason), (anchor_ok, anchor_reason))
                                         if not ok)
        report["rows"].append(row)
    report["summary"] = {
        "validated": sum(row["status"] == "validated" for row in report["rows"]),
        "refused": sum(row["status"] != "validated" for row in report["rows"]),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                               encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0 if report["summary"]["refused"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
