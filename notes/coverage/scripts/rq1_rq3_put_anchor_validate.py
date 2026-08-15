#!/usr/bin/env python3
"""Run the exact PUT and RQ3 anchor pair recorded by the mechanical mapper."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


def foundry_root(source: Path) -> Path | None:
    return next((parent for parent in source.parents
                 if (parent / "foundry.toml").is_file()), None)


def run(row: dict[str, Any], timeout: int, anchor_only: bool) -> dict[str, Any]:
    source = Path(row["source"])
    root = foundry_root(source)
    if root is None:
        return {"identity": row["identity"], "status": "Failure",
                "reason": "foundry root absent"}
    names = ([row["anchor_test"]] if anchor_only else
             [row["test"], row["anchor_test"]])
    pattern = "^(" + "|".join(re.escape(name) for name in names) + r")(\(|$)"
    command = ["forge", "test", "--root", str(root), "--match-path",
               str(source.relative_to(root)), "--match-test", pattern,
               "--fuzz-runs", "256", "--json"]
    try:
        process = subprocess.run(command, capture_output=True, text=True,
                                 timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"identity": row["identity"], "source": str(source),
                "status": "Timeout"}
    statuses = []
    try:
        result = json.loads(process.stdout)
        for suite in result.values():
            statuses.extend([item.get("status") for item in
                             suite.get("test_results", {}).values()])
    except json.JSONDecodeError:
        pass
    success = (process.returncode == 0 and len(statuses) == len(names) and
               all(status == "Success" for status in statuses))
    return {"identity": row["identity"], "source": str(source),
            "put_test": row["test"], "anchor_test": row["anchor_test"],
            "status": "Success" if success else "Failure",
            "returncode": process.returncode, "test_statuses": statuses,
            "stderr": process.stderr[-2000:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--anchor-only", action="store_true")
    args = parser.parse_args()
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    jobs = [row for row in mapping["rows"] if row.get("status") == "applied"]
    started = time.monotonic()
    completed = {}
    if args.output.is_file():
        old = json.loads(args.output.read_text(encoding="utf-8"))
        completed = {(row.get("source"), row.get("put_test")): row
                     for row in old.get("rows", [])}
    jobs = [row for row in jobs
            if (row.get("source"), row.get("test")) not in completed]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run, row, args.timeout, args.anchor_only): row
                   for row in jobs}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            completed[(row.get("source"), row.get("put_test"))] = row
            rows = list(completed.values())
            partial = {"schema": "veriput-rq1-rq3-anchor-forge/v1",
                       "total": len(rows),
                       "success": sum(item["status"] == "Success" for item in rows),
                       "failed": sum(item["status"] != "Success" for item in rows),
                       "complete": False, "rows": rows}
            temporary = args.output.with_suffix(args.output.suffix + ".tmp")
            temporary.write_text(json.dumps(partial, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, args.output)
    rows = list(completed.values())
    result = {"schema": "veriput-rq1-rq3-anchor-forge/v1",
              "total": len(rows),
              "success": sum(row["status"] == "Success" for row in rows),
              "failed": sum(row["status"] != "Success" for row in rows),
              "wall_s": time.monotonic() - started, "complete": True, "rows": rows}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({key: result[key] for key in
                      ("total", "success", "failed", "wall_s")}))
    return int(result["failed"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
