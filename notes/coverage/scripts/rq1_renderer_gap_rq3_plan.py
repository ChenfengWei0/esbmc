#!/usr/bin/env python3
"""Select exact valid RQ3 concrete sources for RQ1 renderer-gap obligations.

This is deliberately a planner: it neither runs ESBMC nor edits RQ1.  The
input identities come from the frozen recovery inventory, not from the
dynamic ``not_generalized`` set, whose physical projection can be empty while
the frozen recovery pool is still outstanding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq3_mechanical_match import load_rq3  # pylint: disable=wrong-import-position


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--rq3-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    recovery = read_json(args.recovery)
    requested = [
        tuple(str(value) for value in row["identity"])
        for row in recovery.get("rows", [])
        if row.get("category") == "certified-region-renderer-gap"
    ]
    if len(requested) != len(set(requested)):
        raise ValueError("renderer-gap recovery identities are not unique")

    candidates: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in load_rq3(args.rq3_root):
        if not (item.get("is_concrete") and not item.get("is_put")
                and item.get("forge_status") == "Success"
                and item.get("valid_reference_test")):
            continue
        path = Path(str(item.get("file") or ""))
        if not path.is_file():
            continue
        candidates[tuple(item["identity"])].append(item)

    rows = []
    for identity in sorted(requested):
        selected = []
        seen_hashes: set[str] = set()
        for candidate in sorted(
                candidates.get(identity, []),
                key=lambda item: ("/put/" not in str(item.get("file") or ""),
                                  str(item.get("file") or ""))):
            source = Path(str(candidate["file"]))
            source_sha = digest(source)
            if source_sha in seen_hashes:
                continue
            seen_hashes.add(source_sha)
            selected.append({
                "source": str(source),
                "source_sha256": source_sha,
                "test": candidate.get("test"),
                "put_json": candidate.get("put_json"),
                "put_json_sha256": candidate.get("put_json_sha256"),
                "concrete_oracles": candidate.get("concrete_oracles") or [],
                "stage2_source": candidate.get("stage2_source"),
            })
        rows.append({
            "identity": list(identity),
            "status": "ready" if len(selected) == 1 else "missing" if not selected else "ambiguous",
            "candidates": selected,
        })

    report = {
        "schema": "rq1-renderer-gap-rq3-plan/v2",
        "recovery": str(args.recovery.resolve()),
        "recovery_sha256": digest(args.recovery),
        "rq3_root": str(args.rq3_root.resolve()),
        "rows": rows,
        "counts": {
            "total": len(rows),
            "ready": sum(row["status"] == "ready" for row in rows),
            "missing": sum(row["status"] == "missing" for row in rows),
            "ambiguous": sum(row["status"] == "ambiguous" for row in rows),
        },
        "policy": "plan only: no ESBMC and no RQ1 write",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if not report["counts"]["ambiguous"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
