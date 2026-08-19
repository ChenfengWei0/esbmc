#!/usr/bin/env python3
"""Resolve RQ3 matcher ambiguities without changing any canonical ledger.

The input is the mechanical matcher JSON.  A candidate is chosen only when
the strongest observable RQ3 evidence has a unique winner.  Ties are kept as
alternatives, since an encoding number by itself is not a stable identity
across RQ1/RQ3 runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path):
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def score(candidate):
    """Rank only evidence present in the RQ3 artifact; no RQ1 assumptions."""
    material = candidate.get("materialization") or {}
    return (
        int(bool(candidate.get("file_exists"))),
        int(candidate.get("forge_status") == "Success"),
        int(bool(candidate.get("test"))),
        int(bool(candidate.get("concrete_oracles"))),
        int(bool(material.get("is_concrete"))),
        int(not material.get("is_refusal", False)),
    )


def candidate_summary(candidate):
    path = candidate.get("file")
    put_json = candidate.get("put_json")
    return {
        "enc": candidate.get("enc"),
        "piece": candidate.get("piece"),
        "path_function": candidate.get("path_function"),
        "unit": candidate.get("unit"),
        "test": candidate.get("test"),
        "file": path,
        "file_exists": bool(candidate.get("file_exists")),
        "file_sha256": sha256_file(path),
        "put_json": put_json,
        "put_json_sha256": candidate.get("put_json_sha256") or sha256_file(put_json),
        "result_json": candidate.get("result_json"),
        "forge_status": candidate.get("forge_status"),
        "kind": candidate.get("kind"),
        "is_concrete": bool(candidate.get("is_concrete")),
        "is_put": bool(candidate.get("is_put")),
        "materialization": candidate.get("materialization"),
        "concrete_oracles": candidate.get("concrete_oracles"),
        "score": score(candidate),
    }


def resolve(input_path, output_path):
    data = json.loads(Path(input_path).read_text())
    rows = []
    chosen = 0
    unresolved = 0
    for item in data.get("ambiguous", []):
        candidates = item.get("candidates", [])
        scored = [(score(candidate), candidate) for candidate in candidates]
        best = max((value for value, _ in scored), default=())
        winners = [candidate for value, candidate in scored if value == best]
        row = {
            "frozen_identity": item.get("frozen_identity"),
            "match_tier": item.get("match_tier"),
            "best_score": best,
            "candidate_count": len(candidates),
            "winner_count": len(winners),
            "decision": "chosen" if len(winners) == 1 else "unresolved-alternatives",
            "chosen": candidate_summary(winners[0]) if len(winners) == 1 else None,
            "alternatives": [candidate_summary(candidate) for candidate in winners],
        }
        rows.append(row)
        if len(winners) == 1:
            chosen += 1
        else:
            unresolved += 1

    report = {
        "schema": "rq3-ambiguous-resolution-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(Path(input_path).resolve()),
        "input_sha256": sha256_file(input_path),
        "canonical_write": False,
        "policy": {
            "description": "Mechanical RQ3 evidence ranking only; ties are never guessed.",
            "score_fields": [
                "file_exists",
                "forge_status=Success",
                "test",
                "concrete_oracles",
                "materialization.is_concrete",
                "not materialization.is_refusal",
            ],
        },
        "input_ambiguous_count": len(data.get("ambiguous", [])),
        "chosen_count": chosen,
        "unresolved_alternative_count": unresolved,
        "rows": rows,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = resolve(args.input, args.output)
    print(json.dumps({
        "input_ambiguous_count": report["input_ambiguous_count"],
        "chosen_count": report["chosen_count"],
        "unresolved_alternative_count": report["unresolved_alternative_count"],
        "output": str(args.output.resolve()),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
