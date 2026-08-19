#!/usr/bin/env python3
"""Compare one Full smoke with its RQ3 ablations using strict test rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


class ComparisonError(ValueError):
    """The smoke inputs are missing evidence or violate expected monotonicity."""


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"{path}: expected JSON object")
    return value


def full_metrics(root: Path, allow_empty: bool = False) -> dict:
    rows = []
    wall_s = 0.0
    for result_path in sorted(root.rglob("result.json")):
        envelope = _json(result_path)
        result = envelope.get("row") if isinstance(envelope.get("row"), dict) else envelope
        timing = result.get("timing") or {}
        wall_s += float(timing.get("wall_total_s") or result.get("wall_total_s") or 0.0)
        rows.extend(row for row in (result.get("valid_tests") or [])
                    if isinstance(row, dict) and row.get("valid_reference_test") is True)
    if not rows and not allow_empty:
        raise ComparisonError(f"{root}: no strict-valid test rows")
    puts = [row for row in rows if row.get("kind") == "put" or row.get("is_put") is True]
    r1r2 = []
    families = {name: 0 for name in ("R2.1", "R2.2", "R2.3")}
    for row in puts:
        record_path = Path(str(row.get("put_json") or ""))
        if not record_path.is_file():
            raise ComparisonError(f"{row.get('test')}: missing put.json {record_path}")
        record = _json(record_path)
        stats = record.get("stats") or {}
        counts = stats.get("oracle_class_counts") or {}
        if int(counts.get("R1") or 0) + int(counts.get("R2") or 0) > 0:
            r1r2.append(row)
        for name in families:
            families[name] += int((stats.get("r2_subfamily_counts") or {}).get(name) or 0)
    return {
        "valid": len(rows),
        "put": len(puts),
        "concrete": len(rows) - len(puts),
        "put_with_r1r2": len(r1r2),
        "r2_1": families["R2.1"],
        "r2_2": families["R2.2"],
        "r2_3": families["R2.3"],
        "wall_s": round(wall_s, 3),
    }


def derived_metrics(root: Path) -> dict:
    manifest = _json(root / "manifest.json")
    entries = [entry for entry in (manifest.get("entries") or []) if isinstance(entry, dict)]
    if not entries:
        raise ComparisonError(f"{root}: empty derived manifest")
    valid = [entry for entry in entries if (entry.get("forge") or {}).get("status") == "Success"]
    mode = str(manifest.get("mode") or "")
    put = 0
    for entry in valid:
        origin = entry.get("origin") or {}
        if origin.get("kind") != "put":
            continue
        if mode in ("no-cer-reg",):
            continue
        if mode == "no-region-refinement" and origin.get("replacement"):
            continue
        put += 1
    return {
        "valid": len(valid),
        "put": put,
        "concrete": len(valid) - put,
        # The derivation deliberately removes generalized oracle refinement;
        # detailed R1/R2 counts remain meaningful only in Full put.json rows.
        "put_with_r1r2": 0 if mode in ("no-test-oracle-refinement", "no-cer-reg") else None,
        "r2_1": 0 if mode in ("no-test-oracle-refinement", "no-cer-reg") else None,
        "r2_2": 0 if mode in ("no-test-oracle-refinement", "no-cer-reg") else None,
        "r2_3": 0 if mode in ("no-test-oracle-refinement", "no-cer-reg") else None,
        "wall_s": None,
    }


def compare(full: dict, arms: dict[str, dict]) -> dict:
    failures = []
    for name, arm in arms.items():
        if arm["put"] > full["put"]:
            failures.append(f"{name} PUT {arm['put']} exceeds Full {full['put']}")
        if arm.get("put_with_r1r2") is not None and \
                arm["put_with_r1r2"] > full["put_with_r1r2"]:
            failures.append(f"{name} R1/R2 PUT {arm['put_with_r1r2']} exceeds Full "
                            f"{full['put_with_r1r2']}")
    no_cer = arms.get("no-cer-reg")
    if no_cer and no_cer["put"] != 0:
        failures.append(f"no-cer-reg must contain zero PUTs, got {no_cer['put']}")
    return {"full": full, "ablations": arms, "passes": not failures, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--no-selection", type=Path)
    parser.add_argument("--no-region", type=Path)
    parser.add_argument("--no-test-oracle", type=Path)
    parser.add_argument("--no-cer-reg", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        arms = {}
        if args.no_selection:
            arms["no-selection"] = full_metrics(args.no_selection, allow_empty=True)
        for name, path in (("no-region", args.no_region),
                           ("no-test-oracle", args.no_test_oracle),
                           ("no-cer-reg", args.no_cer_reg)):
            if path:
                arms[name] = derived_metrics(path)
        report = compare(full_metrics(args.full), arms)
    except ComparisonError as exc:
        print(f"REFUSED: {exc}")
        return 2
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
