#!/usr/bin/env python3
"""Seal the state-only subset of frozen unresolved B340 anchor obligations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from rq1_final_test_inventory import obligations

REASON = "first-pass recovery supports only return, normal-exit, or revert"
IDENTITY_FIELDS = ("case", "path_function", "unit", "enc", "piece")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(record: dict[str, Any]) -> tuple[str, ...] | None:
    value = record.get("identity")
    if (not isinstance(value, dict)
            or any(value.get(field) is None for field in IDENTITY_FIELDS[:-1])):
        return None
    return tuple(str(value.get(field) or "") for field in IDENTITY_FIELDS)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def build_partition(inventory_path: Path, dry_run_path: Path, audit_path: Path, frozen_path: Path,
                    result_root: Path) -> dict[str, Any]:
    """Select the exact current, frozen, state-only subset and bind all inputs."""
    # pylint: disable=too-many-locals,too-many-branches
    inventory = _load(inventory_path)
    dry_run = _load(dry_run_path)
    audit = _load(audit_path)
    frozen = _load(frozen_path)
    if audit.get("schema") != "rq1-frozen905-mutually-exclusive-anchor-audit/v2":
        raise ValueError("mutually exclusive audit has an unexpected schema")
    if audit.get("population_count") != 905:
        raise ValueError("mutually exclusive audit is not the frozen unresolved-905 population")
    if (frozen.get("schema") != "veriput-rq1-ce-obligation-ledger/v1"
            or frozen.get("total_ce_obligations") != 1808):
        raise ValueError("frozen CE ledger has an unexpected schema or population")
    records = inventory.get("records")
    dry_rows = dry_run.get("rows")
    frozen_rows = frozen.get("obligations")
    if not isinstance(records, list) or not isinstance(dry_rows, list) or not isinstance(
            frozen_rows, list):
        raise ValueError("one B340 input has no row list")

    record_index = {}
    for record in records:
        key = _identity(record)
        digest = record.get("identity_sha256") if isinstance(record, dict) else None
        if key is None or not digest or key in record_index:
            raise ValueError("recovery inventory has a malformed or duplicate identity")
        record_index[key] = record
    frozen_keys = {tuple(str(value) for value in row) for row in frozen_rows}
    _generalized, unresolved, _not_generalized = obligations(result_root)
    population = unresolved & frozen_keys
    if len(population) != 905:
        raise ValueError(f"expected 905 frozen unresolved PUTs, found {len(population)}")

    refused = {}
    for row in dry_rows:
        identity = row.get("identity") if isinstance(row, dict) else None
        if not isinstance(identity, list) or len(identity) != 5:
            raise ValueError("first-pass dry-run has a malformed identity")
        key = tuple(str(value) for value in identity)
        if key in refused:
            raise ValueError("first-pass dry-run has duplicate identities")
        if key in population and row.get("reason") == REASON:
            refused[key] = row
    if len(refused) != 340:
        raise ValueError(f"expected the frozen B340 population, found {len(refused)}")

    rows = []
    for key, dry_row in sorted(refused.items()):
        record = record_index.get(key)
        if record is None or dry_row.get("record_identity_sha256") != record.get("identity_sha256"):
            raise ValueError("B340 row is not bound to its recovery inventory record")
        kinds = (record.get("observable_evidence") or {}).get("anchor_required_kinds") or []
        if kinds != ["state-delta"]:
            continue
        rows.append({
            "identity": list(key),
            "record_identity_sha256": record["identity_sha256"],
            "required_kinds": kinds,
            "status": "selected",
        })
    if len(rows) != 72:
        raise ValueError(f"expected 72 state-only B340 rows, found {len(rows)}")

    inputs = []
    for role, path in (("recovery-inventory", inventory_path), ("first-pass-dry-run", dry_run_path),
                       ("mutually-exclusive-audit", audit_path), ("frozen-ledger", frozen_path)):
        inputs.append({
            "role": role,
            "path": str(path.resolve()),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        })
    return {
        "schema": "veriput-rq1-anchor-b340-state-partition/v1",
        "identity": list(IDENTITY_FIELDS),
        "selection_reason": REASON,
        "b340_population": len(refused),
        "exclusive_owned": len(rows),
        "inputs": inputs,
        "rows": rows,
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False,
                                     dir=path.parent) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def bind_prepared(partition: dict[str, Any], selector_path: Path,
                  progress_path: Path) -> dict[str, Any]:
    """Seal exactly the rows accepted by the main backfill materializer."""
    selector = _load(selector_path)
    progress = _load(progress_path)
    if selector != partition:
        raise ValueError("sealed selector differs from the regenerated B340 partition")
    if (progress.get("recovery_partition") != "b340-state"
            or progress.get("partition_artifact") != str(selector_path)):
        raise ValueError("preparation progress does not bind the sealed selector")
    selected = {tuple(row["identity"]): row for row in partition["rows"]}
    progress_rows = progress.get("rows")
    if not isinstance(progress_rows, list):
        raise ValueError("preparation progress has no rows")
    prepared = {}
    for row in progress_rows:
        identity = row.get("identity") if isinstance(row, dict) else None
        if not isinstance(identity, list) or len(identity) != 5:
            raise ValueError("preparation progress has a malformed identity")
        key = tuple(str(value) for value in identity)
        if key in prepared:
            raise ValueError("preparation progress has duplicate identities")
        prepared[key] = row
    if set(prepared) != set(selected):
        raise ValueError("preparation progress does not cover the selector exactly")
    ready = [selected[key] for key in sorted(selected) if prepared[key].get("status") == "ready"]
    result = dict(partition)
    result["candidate_owned"] = partition["exclusive_owned"]
    result["exclusive_owned"] = len(ready)
    result["rows"] = ready
    result["preparation_counts"] = progress.get("counts")
    result["inputs"] = list(partition["inputs"]) + [{
        "role": "candidate-selector",
        "path": str(selector_path.resolve()),
        "sha256": _sha256(selector_path),
        "bytes": selector_path.stat().st_size,
    }, {
        "role": "preparation-progress",
        "path": str(progress_path.resolve()),
        "sha256": _sha256(progress_path),
        "bytes": progress_path.stat().st_size,
    }]
    return result


def main() -> int:
    """Write one immutable selector artifact; never modify result data."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--dry-run", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selector", type=Path)
    parser.add_argument("--prepared-progress", type=Path)
    args = parser.parse_args()
    result = build_partition(args.inventory, args.dry_run, args.audit, args.frozen,
                             args.result_root)
    if bool(args.selector) != bool(args.prepared_progress):
        parser.error("--selector and --prepared-progress must be used together")
    if args.selector is not None:
        result = bind_prepared(result, args.selector, args.prepared_progress)
    _atomic_json(args.output, result)
    print(
        json.dumps({
            "b340": result["b340_population"],
            "state": result["exclusive_owned"]
        },
                   sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
