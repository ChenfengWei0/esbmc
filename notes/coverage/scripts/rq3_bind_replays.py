#!/usr/bin/env python3
"""Bind RQ3 replay evidence to the frozen RQ1 identity population.

Binding is deliberately separate from adoption.  It only records the
deterministic identity join and candidate paths; it never edits a subject,
rewrites an identity, runs ESBMC/Forge, or writes canonical results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def number(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def score(candidate: dict[str, Any], target: list[str]) -> tuple[int, int, int, int, str]:
    distance = abs((number(candidate.get("enc")) or 0) - (number(target[3]) or 0))
    return (
        int(bool(candidate.get("file_exists"))),
        int(candidate.get("forge_status") == "Success"),
        int(bool(candidate.get("concrete_oracles"))),
        -distance,
        str(candidate.get("put_json") or candidate.get("file") or ""),
    )


def reference(candidate: dict[str, Any]) -> dict[str, Any]:
    """Keep binding output small while retaining replay provenance."""
    keys = (
        "identity", "case", "path_function", "unit", "enc", "piece", "kind",
        "result_json", "put_json", "put_json_sha256", "file", "file_exists",
        "test", "forge_status", "valid_reference_test", "concrete_oracles",
        "stage2_source", "flat_source_sha256",
    )
    return {key: candidate.get(key) for key in keys}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("match", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.match.read_text(encoding="utf-8"))

    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for name in ("matched", "ambiguous"):
        for group in report.get(name, []):
            groups[tuple(group["frozen_identity"])] = group
    for group in report.get("missing", []):
        groups[tuple(group["frozen_identity"])] = group

    rows = []
    for target, group in sorted(groups.items()):
        candidates = [item for item in group.get("candidates", [])
                      if isinstance(item, dict)]
        ranked = sorted(candidates, key=lambda item: score(item, list(target)),
                        reverse=True)
        selected = ranked[0] if ranked else None
        rows.append({
            "frozen_identity": list(target),
            "binding_status": "bound" if selected else "unbound",
            "binding_tier": group.get("match_tier"),
            "candidate_count": len(ranked),
            "selected": reference(selected) if selected else None,
            "candidates": [reference(item) for item in ranked],
        })

    expected = int(report.get("target_count", 0))
    identities = {tuple(row["frozen_identity"]) for row in rows}
    if len(rows) != expected or len(identities) != expected:
        raise SystemExit(f"binding population mismatch: {len(rows)} != {expected}")
    bound = sum(row["binding_status"] == "bound" for row in rows)
    output = {
        "schema": "veriput-rq3-replay-binding/v1",
        "source_match": str(args.match.resolve()),
        "source_match_sha256": sha256(args.match),
        "target_count": expected,
        "counts": {
            "rows": len(rows),
            "bound": bound,
            "unbound": len(rows) - bound,
            "candidate_references": sum(row["candidate_count"] for row in rows),
        },
        "policy": {
            "identity_rewrite": False,
            "canonical_write": False,
            "esbmc_run": False,
            "forge_run": False,
            "adoption": False,
        },
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({"schema": output["schema"], **output["counts"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
