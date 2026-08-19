#!/usr/bin/env python3
"""Validate staged fourteen anchors in disposable Foundry project copies."""
from __future__ import annotations
import argparse, json, shutil, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def run(row: dict, staging: Path, root: Path) -> dict:
    result = {"identity": row["identity"], "anchor_test": row["anchor_test"],
              "project": row.get("project_selected"), "status": "refused"}
    project = Path(str(row.get("project_selected") or ""))
    if not project.is_dir():
        result["reason"] = "selected Foundry project absent"
        return result
    source = Path(row["staged_source"])
    if not source.is_file():
        result["reason"] = "staged source absent"
        return result
    # The staging path is intentionally copied into a disposable project only.
    with tempfile.TemporaryDirectory(prefix="rq1-anchor14-") as temp:
        target = Path(temp) / "project"
        shutil.copytree(project, target, symlinks=True)
        test_dir = target / "test"
        test_dir.mkdir(exist_ok=True)
        copied = test_dir / source.name
        shutil.copy2(source, copied)
        rel = str(copied.relative_to(target))
        commands = {}
        for label, test in (("put", row["test"]), ("anchor", row["anchor_test"])):
            proc = subprocess.run(["forge", "test", "--json", "--match-path", rel,
                                   "--match-test", test], cwd=target, text=True,
                                  capture_output=True, timeout=180, check=False)
            commands[label] = {"returncode": proc.returncode,
                               "stdout_tail": proc.stdout[-2000:],
                               "stderr_tail": proc.stderr[-2000:]}
        result["commands"] = commands
        result["status"] = ("validated" if all(v["returncode"] == 0
                                               for v in commands.values()) else "failed")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("--staging", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    doc = json.loads(args.report.read_text())
    rows = doc["rows"]
    # Parameterized rows have already been rewritten to fixed constants by the
    # staging pass, so they are validated exactly like the zero-parameter rows.
    targets = list(rows)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda row: run(row, args.staging, args.staging), targets))
    output = {"schema": "rq1-rq3-anchor-validation14/v1", "input": str(args.report),
              "rows": results, "counts": {
                  "total": len(rows), "forge_targets": len(targets),
                  "validated": sum(x["status"] == "validated" for x in results),
                  "failed": sum(x["status"] == "failed" for x in results),
                  "refused": sum(x["status"] == "refused" for x in results),
                  "fixed_parameterized_not_run": 0},
              "policy": "disposable project copies; no canonical writes or PUT credit"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output["counts"], sort_keys=True))
    return 0 if not output["counts"]["failed"] and not output["counts"]["refused"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
