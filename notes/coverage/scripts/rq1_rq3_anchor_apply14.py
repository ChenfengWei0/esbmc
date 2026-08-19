#!/usr/bin/env python3
"""Promote the sealed fourteen RQ3 mechanical anchors into RQ1.

The promotion is deliberately narrow: every row must still point at the
same source bytes that were staged, and the staged file must be exactly the
original source with one deterministic anchor inserted.  This does not claim
Forge success; that is recorded separately by the later validation step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def digest(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = report.get("rows", [])
    if len(rows) != 14 or any(row.get("status") != "staged" for row in rows):
        raise SystemExit("report is not the sealed 14-row staged set")

    checks = []
    for row in rows:
        source = Path(row["source"])
        staged = Path(row["staged_source"])
        if not source.is_file() or not staged.is_file():
            raise SystemExit(f"missing source/staging file: {source} / {staged}")
        original = source.read_text(encoding="utf-8")
        promoted = staged.read_text(encoding="utf-8")
        if digest(original) != row["source_sha256"]:
            raise SystemExit(f"source changed after staging: {source}")
        if digest(promoted) != row["staged_sha256"]:
            raise SystemExit(f"staging changed after sealing: {staged}")
        marker = row["anchor_test"]
        if promoted.count("function " + marker + "(") != 1:
            raise SystemExit(f"anchor count is not one: {source}")
        if "test_ce_anchor_" in original:
            raise SystemExit(f"source already has an anchor: {source}")
        insertion = "\n\n  // RQ1/RQ3 mechanical anchor.\n  " + row["anchor_body"]
        if promoted.replace(insertion, "", 1) != original:
            raise SystemExit(f"staged source is not original-plus-anchor: {source}")
        checks.append({
            "source": str(source),
            "source_sha256_before": digest(original),
            "staged_sha256": digest(promoted),
            "anchor_test": marker,
            "status": "checked",
        })

    result = {
        "schema": "rq1-rq3-anchor-apply14/v1",
        "mode": "apply" if args.apply else "dry-run",
        "rows": checks,
        "counts": {"checked": len(checks), "written": 0},
    }
    if args.apply:
        args.backup.mkdir(parents=True, exist_ok=True)
        for row, checked in zip(rows, checks):
            source = Path(row["source"])
            staged = Path(row["staged_source"])
            backup = args.backup / (digest(str(source))[:16] + ".t.sol")
            if backup.exists():
                raise SystemExit(f"backup collision: {backup}")
            shutil.copy2(source, backup)
            data = staged.read_bytes()
            fd, tmp_name = tempfile.mkstemp(prefix=".rq1-anchor-", dir=source.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, source)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            checked["status"] = "written"
            checked["source_sha256_after"] = digest(source.read_text(encoding="utf-8"))
        result["counts"]["written"] = len(checks)
    args.progress.parent.mkdir(parents=True, exist_ok=True)
    args.progress.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
