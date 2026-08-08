#!/usr/bin/env python3
"""Triage VeriPUT RQ1 results by methodological strength.

This is intentionally journal-only.  It reads
`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/<dataset>/results.jsonl`
with last-write-wins semantics and never scans `subjects/*/result.json`,
because redo directories preserve stale result files.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_RESULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DATASETS = ("bugfix124", "real203", "peer182")


def _count_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _valid_tests(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for test in row.get("valid_tests") or []:
        if isinstance(test, dict) and test.get("valid_reference_test", True):
            out.append(test)
    return out


def _valid_count(row: dict[str, Any]) -> int:
    if row.get("valid") is not None:
        return _count_int(row, "valid")
    split = _count_int(row, "put_valid") + _count_int(row, "concrete_valid")
    return split or len(_valid_tests(row))


def _kind_valid(row: dict[str, Any], kind: str) -> int:
    key = f"{kind}_valid"
    if row.get(key) is not None:
        return _count_int(row, key)
    return sum(1 for test in _valid_tests(row) if test.get("kind") == kind)


def _valid_put_classes(row: dict[str, Any]) -> Counter[str]:
    classes: Counter[str] = Counter()
    valid_puts = [test for test in _valid_tests(row) if test.get("kind") == "put"]
    if valid_puts:
        for test in valid_puts:
            labels = test.get("oracle_classes") or []
            if not labels:
                classes["<none>"] += 1
            for label in labels:
                classes[str(label)] += 1
        return classes

    # Compatibility path for old rows that have only aggregate counters.
    if _kind_valid(row, "put") > 0:
        for label, count in (row.get("oracle_class_counts") or {}).items():
            try:
                classes[str(label)] += int(count or 0)
            except (TypeError, ValueError):
                continue
        if not classes:
            classes["<unknown>"] += _kind_valid(row, "put")
    return classes


def _valid_put_combo_counts(row: dict[str, Any]) -> Counter[str]:
    combos: Counter[str] = Counter()
    valid_puts = [test for test in _valid_tests(row) if test.get("kind") == "put"]
    if valid_puts:
        for test in valid_puts:
            labels = tuple(str(v) for v in (test.get("oracle_classes") or []))
            combos["+".join(labels) if labels else "<none>"] += 1
        return combos
    if _kind_valid(row, "put") > 0:
        for label, count in (row.get("oracle_class_combo_counts") or {}).items():
            try:
                combos[str(label)] += int(count or 0)
            except (TypeError, ValueError):
                continue
    return combos


def _read_put_stats(test: dict[str, Any]) -> dict[str, Any]:
    doc = _read_put_json(test)
    stats = doc.get("stats") or {}
    return stats if isinstance(stats, dict) else {}


def _read_put_json(test: dict[str, Any]) -> dict[str, Any]:
    path = test.get("put_json")
    if not path:
        return {}
    try:
        doc = json.loads(Path(str(path)).read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _contains_text(value: Any, needle: str) -> bool:
    needle = needle.lower()
    if isinstance(value, str):
        return needle in value.lower()
    if isinstance(value, dict):
        return any(_contains_text(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_contains_text(v, needle) for v in value)
    return False


def _strength_issue_tags(row: dict[str, Any], bucket: str) -> Counter[str]:
    tags: Counter[str] = Counter()
    tests = _valid_tests(row)
    if bucket == "valid-no-PUT":
        if not tests:
            tags["no-valid-test-details"] += 1
        for test in tests:
            source = str(test.get("stage2_source") or "<missing>")
            tags[f"source:{source}"] += 1
            doc = _read_put_json(test)
            stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
            notes = doc.get("notes") or []
            reason = doc.get("concrete_reason") or ""
            if source == "certified_region":
                if _contains_text([notes, reason], "NOT PARAMETERIZED"):
                    tags["certified-region-no-rendered-wide-coordinate"] += 1
                if not int(stats.get("asserts") or 0):
                    tags["certified-region-no-emitted-oracle"] += 1
            if source == "timeout_concrete_fallback":
                tags["stage2-timeout-witness-only"] += 1
            if source == "cleared_not_certified_fallback":
                tags["stage2-refuted-region-concrete-only"] += 1
        reason = str(row.get("reason") or "")
        if "before Stage 4" in reason:
            tags["case-budget-before-stage4"] += 1
        if "before remaining units" in reason:
            tags["case-budget-before-remaining-units"] += 1
        return tags

    if bucket.startswith("valid-PUT-no-R1R2"):
        for test in [t for t in tests if t.get("kind") == "put"]:
            doc = _read_put_json(test)
            stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
            notes = doc.get("notes") or []
            oracle_skipped = stats.get("oracle_skipped") or []
            if stats.get("rollback_exit") is True:
                tags["rollback-exit-r0-only"] += 1
            if _contains_text(notes, "mapping or dynamic array"):
                tags["ladder-refused-mapping-or-dynarray"] += 1
            if _contains_text(oracle_skipped, "all return rungs DROPPED"):
                tags["return-varies-no-simple-rung"] += 1
            r2_prefilter = doc.get("r2_fuzz_prefilter") or {}
            if isinstance(r2_prefilter, dict):
                reason = str(r2_prefilter.get("reason") or "")
                if "no R2 candidate" in reason:
                    tags["no-r2-candidate-proposed"] += 1
                if "rollback path" in reason:
                    tags["r2-skipped-rollback"] += 1
            classes = set(stats.get("oracle_classes") or test.get("oracle_classes") or [])
            if classes == {"R0"}:
                tags["exit-only-oracle"] += 1
        return tags

    return tags


def _valid_put_no_r1r2_exit_shape(row: dict[str, Any]) -> str:
    valid_puts = [test for test in _valid_tests(row) if test.get("kind") == "put"]
    if not valid_puts:
        return "none"
    rollback = 0
    normal_or_unknown = 0
    for test in valid_puts:
        stats = _read_put_stats(test)
        if stats.get("rollback_exit") is True:
            rollback += 1
        else:
            normal_or_unknown += 1
    if normal_or_unknown:
        return "normal-or-unknown"
    if rollback:
        return "rollback"
    return "unknown"


def _bucket(row: dict[str, Any]) -> str:
    valid = _valid_count(row)
    put_valid = _kind_valid(row, "put")
    if valid <= 0:
        return "no-valid"
    if put_valid <= 0:
        return "valid-no-PUT"
    classes = _valid_put_classes(row)
    if classes.get("R2", 0) > 0:
        return "valid-PUT-with-R2"
    if classes.get("R1", 0) > 0:
        return "valid-PUT-with-R1-no-R2"
    shape = _valid_put_no_r1r2_exit_shape(row)
    if shape == "rollback":
        return "valid-PUT-no-R1R2-rollback"
    return "valid-PUT-no-R1R2-normal-or-unknown"


def _load_latest(path: Path) -> tuple[dict[str, dict[str, Any]], int, int]:
    latest: dict[str, dict[str, Any]] = {}
    bad_lines = 0
    lines = 0
    if not path.exists():
        return latest, lines, bad_lines
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        lines += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        key = row.get("key")
        if key:
            latest[str(key)] = row
    return latest, lines, bad_lines


def _sum_float(row: dict[str, Any], *keys: str) -> float:
    total = 0.0
    for key in keys:
        try:
            total += float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            pass
    return total


def _time_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    generation = [_sum_float(row, "generation_wall_s") for row in rows]
    stage2 = [_sum_float(row, "stage2_wall_s") for row in rows]
    stage4_gen = [_sum_float(row, "stage4_generation_wall_s") for row in rows]
    replay = [_sum_float(row, "foundry_replay_wall_s") for row in rows]

    def block(values: list[float]) -> dict[str, float]:
        nonzero = [value for value in values if value > 0]
        if not nonzero:
            return {"sum_s": 0.0, "mean_s": 0.0, "median_s": 0.0}
        return {
            "sum_s": round(sum(nonzero), 3),
            "mean_s": round(statistics.mean(nonzero), 3),
            "median_s": round(statistics.median(nonzero), 3),
        }

    return {
        "generation": block(generation),
        "stage2": block(stage2),
        "stage4_generation": block(stage4_gen),
        "foundry_replay_outside_timeout": block(replay),
    }


def _example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_id": row.get("subject_id"),
        "status": row.get("status"),
        "completion_status": row.get("completion_status"),
        "reason": row.get("reason"),
        "valid": _valid_count(row),
        "put_valid": _kind_valid(row, "put"),
        "concrete_valid": _kind_valid(row, "concrete"),
        "valid_put_classes": dict(_valid_put_classes(row)),
        "valid_put_combos": dict(_valid_put_combo_counts(row)),
        "generation_wall_s": row.get("generation_wall_s"),
        "stage2_wall_s": row.get("stage2_wall_s"),
        "stage4_generation_wall_s": row.get("stage4_generation_wall_s"),
    }


def summarize_dataset(root: Path, dataset: str,
                      sample_limit: int) -> dict[str, Any]:
    journal = root / dataset / "results.jsonl"
    latest, lines, bad_lines = _load_latest(journal)
    rows = list(latest.values())
    buckets = Counter(_bucket(row) for row in rows)
    status_counts = Counter(str(row.get("status") or "<missing>") for row in rows)
    completion_counts = Counter(
        str(row.get("completion_status") or "<missing>") for row in rows)
    reason_counts = Counter(
        str(row.get("reason") or "<none>") for row in rows if _bucket(row) == "no-valid")
    no_put_sources = Counter()
    valid_put_classes: Counter[str] = Counter()
    valid_put_combos: Counter[str] = Counter()
    no_r1r2_exit_shapes: Counter[str] = Counter()
    strength_issue_counts: Counter[str] = Counter()
    artifact = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        bucket = _bucket(row)
        artifact["raw"] += _count_int(row, "raw")
        artifact["valid"] += _valid_count(row)
        artifact["put_raw"] += _count_int(row, "put_raw")
        artifact["put_valid"] += _kind_valid(row, "put")
        artifact["concrete_raw"] += _count_int(row, "concrete_raw")
        artifact["concrete_valid"] += _kind_valid(row, "concrete")
        valid_put_classes.update(_valid_put_classes(row))
        valid_put_combos.update(_valid_put_combo_counts(row))
        if bucket.startswith("valid-PUT-no-R1R2"):
            no_r1r2_exit_shapes[_valid_put_no_r1r2_exit_shape(row)] += 1
        if bucket == "valid-no-PUT":
            for test in _valid_tests(row):
                no_put_sources[str(test.get("stage2_source") or "<missing>")] += 1
        for tag, count in _strength_issue_tags(row, bucket).items():
            strength_issue_counts[f"{bucket}:{tag}"] += count
        if len(examples[bucket]) < sample_limit:
            examples[bucket].append(_example(row))

    put_ratio = 0.0
    if artifact["valid"]:
        put_ratio = artifact["put_valid"] / artifact["valid"]
    valid_subjects = len(rows) - buckets["no-valid"]
    valid_subject_put_ratio = 0.0
    if valid_subjects:
        valid_subject_put_ratio = (
            valid_subjects - buckets["valid-no-PUT"]) / valid_subjects
    backlog = {
        "no_valid": buckets["no-valid"],
        "valid_no_put": buckets["valid-no-PUT"],
        "valid_put_no_r1r2_actionable": (
            buckets["valid-PUT-no-R1R2-normal-or-unknown"]),
        "valid_put_no_r1r2_rollback_accounting_only": (
            buckets["valid-PUT-no-R1R2-rollback"]),
    }

    return {
        "dataset": dataset,
        "journal": str(journal),
        "journal_lines": lines,
        "bad_jsonl_lines": bad_lines,
        "subjects": len(rows),
        "subject_buckets": dict(sorted(buckets.items())),
        "artifact_totals": dict(sorted(artifact.items())),
        "artifact_put_valid_ratio": round(put_ratio, 4),
        "valid_subject_any_put_ratio": round(valid_subject_put_ratio, 4),
        "methodology_backlog": backlog,
        "valid_put_oracle_class_counts": dict(sorted(valid_put_classes.items())),
        "valid_put_oracle_combo_counts": dict(sorted(valid_put_combos.items())),
        "valid_put_no_r1r2_exit_shapes": dict(sorted(no_r1r2_exit_shapes.items())),
        "valid_no_put_stage2_sources": dict(sorted(no_put_sources.items())),
        "strength_issue_counts": dict(sorted(strength_issue_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "completion_status_counts": dict(sorted(completion_counts.items())),
        "no_valid_reason_counts": dict(reason_counts.most_common(20)),
        "time_stats": _time_stats(rows),
        "examples": dict(examples),
    }


def _print_human(summary: dict[str, Any]) -> None:
    print(f"## {summary['dataset']}")
    print(f"journal: {summary['journal']}")
    print(
        f"subjects: {summary['subjects']} "
        f"(jsonl lines={summary['journal_lines']}, bad={summary['bad_jsonl_lines']})")
    print("subject buckets:")
    for key, value in summary["subject_buckets"].items():
        print(f"  {key}: {value}")
    totals = summary["artifact_totals"]
    print("artifact totals:")
    for key in ("raw", "valid", "put_raw", "put_valid",
                "concrete_raw", "concrete_valid"):
        print(f"  {key}: {totals.get(key, 0)}")
    print(f"artifact PUT/valid ratio: {summary['artifact_put_valid_ratio']:.3f}")
    print(
        "valid-subject any-PUT ratio: "
        f"{summary['valid_subject_any_put_ratio']:.3f}")
    print("methodology backlog:")
    for key, value in summary["methodology_backlog"].items():
        print(f"  {key}: {value}")
    print("valid PUT oracle classes:")
    for key, value in summary["valid_put_oracle_class_counts"].items():
        print(f"  {key}: {value}")
    print("valid PUT oracle combos:")
    for key, value in summary["valid_put_oracle_combo_counts"].items():
        print(f"  {key}: {value}")
    if summary["valid_put_no_r1r2_exit_shapes"]:
        print("valid PUT no-R1/R2 exit shapes:")
        for key, value in summary["valid_put_no_r1r2_exit_shapes"].items():
            print(f"  {key}: {value}")
    if summary["valid_no_put_stage2_sources"]:
        print("valid-no-PUT stage2 sources:")
        for key, value in summary["valid_no_put_stage2_sources"].items():
            print(f"  {key}: {value}")
    if summary["strength_issue_counts"]:
        print("strength issue counts:")
        for key, value in summary["strength_issue_counts"].items():
            print(f"  {key}: {value}")
    print("time stats:")
    for key, value in summary["time_stats"].items():
        print(
            f"  {key}: sum={value['sum_s']}s "
            f"mean={value['mean_s']}s median={value['median_s']}s")
    for bucket in (
            "no-valid",
            "valid-no-PUT",
            "valid-PUT-no-R1R2-normal-or-unknown",
            "valid-PUT-no-R1R2-rollback",
            "valid-PUT-with-R1-no-R2",
    ):
        rows = summary.get("examples", {}).get(bucket) or []
        if not rows:
            continue
        print(f"examples: {bucket}")
        for row in rows:
            subject = row.get("subject_id")
            valid = row.get("valid")
            put = row.get("put_valid")
            concrete = row.get("concrete_valid")
            classes = row.get("valid_put_classes")
            status = row.get("status")
            reason = row.get("reason")
            print(
                f"  {subject}: valid={valid} put={put} concrete={concrete} "
                f"classes={classes} status={status} reason={reason}")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    ap.add_argument("--benchmark", choices=("all",) + DATASETS, default="all")
    ap.add_argument("--json", action="store_true",
                    help="print machine-readable JSON instead of text")
    ap.add_argument("--sample-limit", type=int, default=8)
    args = ap.parse_args(argv)

    root = Path(args.result_root).expanduser().resolve()
    datasets = DATASETS if args.benchmark == "all" else (args.benchmark,)
    summaries = [
        summarize_dataset(root, dataset, max(0, args.sample_limit))
        for dataset in datasets
    ]
    if args.json:
        print(json.dumps({"datasets": summaries}, indent=2, sort_keys=True))
    else:
        for summary in summaries:
            _print_human(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
