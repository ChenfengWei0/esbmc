#!/usr/bin/env python3
"""Adopt canonical structural-getter PUT rows without replacing prior tests."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from rq1_case_batch import _detailed_test_rows, _is_valid_reference_test
from rq1_final_test_inventory import _anchor_strength_audit


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _atomic_write(path: Path, document: dict) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _row_key(row: dict) -> tuple[str, str, str]:
    return (str(row.get("file") or ""), str(row.get("test") or ""),
            str(row.get("kind") or ""))


def _append_unique(document: dict, row: dict) -> None:
    put = document.setdefault("put", {})
    if not isinstance(put, dict):
        raise RuntimeError("result.put is not an object")
    for key in ("raw_tests", "valid_tests", "raw_artifacts", "valid_artifacts"):
        values = put.setdefault(key, [])
        if not isinstance(values, list):
            raise RuntimeError(f"result.put.{key} is not an array")
        if _row_key(row) not in {_row_key(value) for value in values if isinstance(value, dict)}:
            values.append(row)


def _refresh_counts(document: dict) -> tuple[int, int, int]:
    detailed = _detailed_test_rows(document)
    valid = [row for row in detailed if _is_valid_reference_test(row)]
    puts = [row for row in valid if row.get("kind") == "put"]
    r1r2 = [
        row for row in puts
        if {"R1", "R2"} & set(row.get("oracle_classes") or [])
    ]
    put = document.setdefault("put", {})
    row = document.setdefault("row", {})
    counts = row.setdefault("artifact_counts", {})
    for target in (put, counts):
        target["valid"] = len(valid)
        target["put_valid"] = len(puts)
        target["valid_put_with_R1_or_R2"] = len(r1r2)
    put["valid_put_with_R1"] = sum(
        "R1" in set(item.get("oracle_classes") or []) for item in puts)
    put["valid_put_with_R2"] = sum(
        "R2" in set(item.get("oracle_classes") or []) for item in puts)
    put["quality_bucket"] = ("valid-PUT-with-R1R2" if r1r2
                             else "valid-PUT-no-R1R2")
    return len(valid), len(puts), len(r1r2)


def _summary(subject_dir: Path, unit: str) -> Path:
    return subject_dir / "put" / f"structural_getter__{unit}" / "put-summary.json"


def _exact_put_json(summary_path: Path, source: str) -> Path:
    matches = []
    for path in summary_path.parent.glob("_wd/*/put.json"):
        document = _read(path)
        if str(document.get("file") or "") == source:
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError(f"expected one put.json for {source}, got {len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    inventory = _read(args.inventory)
    output = []
    for item in inventory.get("rows") or []:
        if item.get("category") != "structural-getter-only":
            continue
        identity = tuple(item["identity"])
        subject_dir = Path(item["manifest"]).parent.parent
        result_path = subject_dir / "result.json"
        summary_path = _summary(subject_dir, identity[2])
        summary = _read(summary_path)
        candidates = [
            row for row in (summary.get("deliverable_b") or {}).get("rows") or []
            if row.get("b") is True and str(row.get("unit") or "") == identity[2]
            and str(row.get("enc")) == identity[3]
            and ("" if row.get("piece") is None else str(row.get("piece"))) == identity[4]
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"expected one exact structural row for {identity}")
        candidate = dict(candidates[0])
        candidate["path_function"] = identity[1]
        candidate["put_json"] = str(
            _exact_put_json(summary_path, str(candidate.get("file") or "")))
        verdict = _anchor_strength_audit(candidate, identity, subject_dir)
        if verdict != (True, "strength-confirmed"):
            raise RuntimeError(f"structural audit failed for {identity}: {verdict}")
        result = _read(result_path)
        before = len([
            row for row in _detailed_test_rows(result)
            if _is_valid_reference_test(row) and row.get("kind") == "put"
        ])
        if args.apply:
            _append_unique(result, candidate)
            valid, after, r1r2 = _refresh_counts(result)
            if after < before + 1:
                raise RuntimeError(f"structural row did not increase PUT count: {identity}")
            _atomic_write(result_path, result)
        else:
            valid, after, r1r2 = (0, before + 1, 0)
        output.append({
            "identity": list(identity),
            "result": str(result_path),
            "before_put": before,
            "after_put": after,
            "valid": valid,
            "r1r2": r1r2,
            "strength": verdict[1],
            "applied": args.apply,
        })
    if len(output) != 23:
        raise RuntimeError(f"expected 23 structural rows, got {len(output)}")
    report = {"schema": "rq1-structural-getter-adoption/v1", "rows": output,
              "applied": sum(row["applied"] for row in output)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(args.report, report)
    print(json.dumps({"selected": len(output), "applied": report["applied"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
