#!/usr/bin/env python3
"""Triage existing VeriPUT RQ1 results by methodology strength.

The runner records raw/valid totals, but the next debugging decision needs a
more specific queue: no valid artifact, valid concrete-only artifact, or valid
PUT that carries only R0.  This script is intentionally read-only with respect
to benchmark inputs; it only scans the generated RQ1 result tree.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RESULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_DATASETS = ("bugfix124", "real203", "peer182")
DATASETS = DEFAULT_DATASETS
REDO_SUFFIX_RE = re.compile(r"\.redo\.\d+(?:\.\d+)?$")
ADOPTED_SUFFIX_RE = re.compile(r"\.adopted_from_[^.]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def base_subject_id(dirname: str) -> str:
    return ADOPTED_SUFFIX_RE.sub("", REDO_SUFFIX_RE.sub("", dirname))


def _load_json(path: Path) -> dict:
    try:
        with path.open() as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return {}


def latest_result_paths(result_root: Path, datasets: list[str]) -> list[tuple[str, str, Path]]:
    out = []
    for dataset in datasets:
        subject_root = result_root / dataset / "subjects"
        grouped: dict[str, list[Path]] = defaultdict(list)
        for path in subject_root.glob("*/result.json"):
            grouped[base_subject_id(path.parent.name)].append(path)
        for subject_id, paths in grouped.items():
            # `rq1_veriput_run.py --redo` archives the previous canonical
            # subject directory as `<subject>.redo.<time>.<pid>` and then
            # writes the new result back to `<subject>/`.  The archive can have
            # a newer directory or file mtime than the canonical result,
            # especially when external tools touch copied artifacts later.  For
            # RQ1 accounting the canonical directory is authoritative whenever
            # it exists; redo/adopted siblings are historical evidence only.
            canonical = [
                path for path in paths if path.parent.name == subject_id
            ]
            latest = canonical[0] if canonical else max(
                paths, key=lambda path: path.stat().st_mtime)
            out.append((dataset, subject_id, latest))
    return sorted(out)


def valid_put_tests(result: dict) -> list[dict]:
    tests = result.get("put", {}).get("valid_tests") or []
    return [
        test for test in tests
        if test.get("kind") == "put" or test.get("b") is True
    ]


def _count(result: dict, key: str) -> int:
    for section in (result.get("put") or {}, result.get("row") or {}):
        if key not in section:
            continue
        try:
            return int(section.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _has_r1r2(classes) -> bool:
    return bool({"R1", "R2"} & set(classes or []))


def _summary_paths(result: dict, result_path: Path | None = None) -> list[Path]:
    paths = []
    for section in (result.get("put") or {}, result.get("row") or {}):
        for raw in section.get("summary_paths") or section.get("put_summary_paths") or []:
            path = Path(raw)
            if path not in paths:
                paths.append(path)
    if result_path is not None:
        for path in sorted(result_path.parent.joinpath("put").rglob("put-summary.json")):
            if path not in paths:
                paths.append(path)
    return paths


def _raw_test_has_r1r2(result: dict, test: str | None) -> bool:
    if not test:
        return False
    for section in (result.get("put") or {}, result.get("row") or {}):
        for raw_test in section.get("raw_tests") or []:
            if raw_test.get("test") == test:
                return _has_r1r2(raw_test.get("oracle_classes"))
    return False


def _put_json_has_r1r2(summary_path: Path, test: str | None) -> bool:
    if not test:
        return False
    put_root = summary_path.parent
    for path in sorted(put_root.rglob("put.json")):
        doc = _load_json(path)
        if doc.get("test") != test:
            continue
        stats = doc.get("stats") or {}
        if _has_r1r2(stats.get("oracle_classes")):
            return True
    return False


def _put_json_for_summary_row(row: dict) -> dict:
    test = row.get("test")
    summary_path = row.get("summary_path")
    if not test or not summary_path:
        return {}
    put_root = Path(summary_path).parent
    for path in sorted(put_root.rglob("put.json")):
        doc = _load_json(path)
        if doc.get("test") == test:
            return doc
    return {}


def _classify_r0_only_put_json(put_json: dict) -> str:
    stats = put_json.get("stats") or {}
    notes = _text_blob(put_json.get("notes") or [])
    skipped = _text_blob(stats.get("oracle_skipped") or [])
    if stats.get("rollback_exit") or "ROLLBACK revert" in skipped:
        return "rollback-unobservable"
    if stats.get("exit_kind") == "revert" or "path exits through a revert" in skipped:
        return "revert-unobservable"
    if "mapping or dynamic array" in notes or "mapping or dynamic array" in skipped:
        return "mapping-dynarray-unrendered"
    if "NOT ONE candidate assertion could be formed" in notes:
        return "no-candidate-assertion"
    return "normal-r0-only-other"


def _row_is_unsupported_concrete(row: dict) -> bool:
    if row.get("kind") != "concrete":
        return False
    file_name = row.get("file")
    if not file_name:
        return False
    try:
        text = Path(file_name).read_text(errors="replace")
    except OSError:
        return False
    return "UNSUPPORTED:" in text


def case_logs_contain(result_path: Path | None, *needles: str) -> bool:
    if result_path is None:
        return False
    root = result_path.parent / "put"
    if not root.exists():
        return False
    for path in root.rglob("run*.log"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if all(needle in text for needle in needles):
            return True
    return False


def generated_logs_contain(result_path: Path | None, *needles: str) -> bool:
    if result_path is None:
        return False
    root = result_path.parent / "logs"
    if not root.exists():
        return False
    for path in root.glob("*.log"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if all(needle in text for needle in needles):
            return True
    return False


def green_r1r2_no_width_rows(
        result: dict, result_path: Path | None = None) -> list[dict]:
    out = []
    for path in _summary_paths(result, result_path):
        doc = _load_json(path)
        for row in ((doc.get("deliverable_b") or {}).get("rows") or []):
            gates = row.get("gates") or {}
            if row.get("kind") != "put":
                continue
            if row.get("valid_reference_test"):
                continue
            if row.get("forge_status") != "Success":
                continue
            if not gates.get("assert"):
                continue
            if gates.get("width") or gates.get("fuzz"):
                continue
            # The summary row tells us that assertions exist and the only B
            # blockers are fuzz/width.  The corresponding put.json carries the
            # oracle-class labels.
            test = row.get("test")
            has_r1r2 = (_raw_test_has_r1r2(result, test)
                        or _put_json_has_r1r2(path, test))
            if not has_r1r2:
                continue
            enriched = dict(row)
            enriched["summary_path"] = str(path)
            out.append(enriched)
    return out


def quality_bucket(result: dict, result_path: Path | None = None) -> str:
    totals = artifact_totals(result, result_path)
    valid = totals["valid"]
    put_valid = totals["put_valid"]
    if put_valid <= 0 and green_r1r2_no_width_rows(result, result_path):
        return "PUT-with-R1R2-but-no-width"
    if valid <= 0:
        return "no-valid"
    if put_valid <= 0:
        return "valid-no-PUT"
    if _summary_valid_put_has_r1r2(result, result_path):
        return "valid-PUT-with-R1R2"
    for test in valid_put_tests(result):
        if _has_r1r2(test.get("oracle_classes")):
            return "valid-PUT-with-R1R2"
    return "valid-PUT-no-R1R2"


def _put_json_for_test(test: dict) -> dict:
    path = test.get("put_json")
    if not path:
        return {}
    return _load_json(Path(path))


def _text_blob(items) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        return items
    return "\n".join(str(item) for item in items)


def classify_no_valid(result: dict, result_path: Path | None = None) -> str:
    row = result.get("row", {})
    cert = result.get("certification", {})
    buckets = Counter(cert.get("bucket_counts") or row.get("cert_bucket_counts") or {})
    reason = row.get("early_stop_reason") or ""
    if cert.get("oom_units") or row.get("cert_oom_units"):
        return "oom"
    if generated_logs_contain(result_path, "REFUSING to resume",
                              "do not match the identity on disk now"):
        return "stale-resume-identity"
    if case_logs_contain(result_path, "NAMED OBSTACLE", "No *.t.sol generated"):
        return "model-chain-obstacle-no-test"
    if "no output after" in reason:
        return "stage2-no-output-timeout"
    if "no Stage-2 candidate" in reason:
        return "stage2-no-candidate-early-stop"
    if buckets:
        return "cert-" + buckets.most_common(1)[0][0].lower()
    return row.get("completion_status") or "no-valid-unknown"


def classify_valid_no_put(result: dict) -> str:
    sources = Counter()
    notes = []
    for test in result.get("put", {}).get("valid_tests") or []:
        if test.get("kind") == "concrete":
            sources[test.get("stage2_source") or "concrete-unknown"] += 1
        put_json = _put_json_for_test(test)
        notes.extend(put_json.get("notes") or [])
    blob = _text_blob(notes)
    if "cannot be synthesized as a full-domain fuzz input" in blob:
        return "unsupported-calldata-type"
    if ("NOT ONE candidate assertion could be formed" in blob
            and "NOT PARAMETERIZED" in blob):
        return "no-observable-oracle-no-width"
    if "NOT PARAMETERIZED" in blob:
        return "not-parameterized-no-wide-rendered-coordinate"
    if sources:
        return sources.most_common(1)[0][0]
    return "valid-no-PUT-unknown"


def classify_no_r1r2(result: dict, result_path: Path | None = None) -> str:
    causes = Counter()
    for row in summary_artifact_rows(result, result_path):
        if row.get("kind") != "put" or not row.get("valid_reference_test"):
            continue
        if _put_json_has_r1r2(Path(row["summary_path"]), row.get("test")):
            continue
        causes[_classify_r0_only_put_json(_put_json_for_summary_row(row))] += 1
    for test in valid_put_tests(result):
        put_json = _put_json_for_test(test)
        causes[_classify_r0_only_put_json(put_json)] += 1
    if not causes:
        return "missing-valid-put-json"
    return causes.most_common(1)[0][0]


def classify(result: dict, bucket: str, result_path: Path | None = None) -> str:
    if bucket == "PUT-with-R1R2-but-no-width":
        return "green-r1r2-put-no-fuzz-width"
    if bucket == "no-valid":
        return classify_no_valid(result, result_path)
    if bucket == "valid-no-PUT":
        return classify_valid_no_put(result)
    if bucket == "valid-PUT-no-R1R2":
        return classify_no_r1r2(result, result_path)
    return "strong-enough"


def _artifact_totals(result: dict) -> dict:
    return _result_artifact_totals(result)


def _result_artifact_totals(result: dict) -> dict:
    put = result.get("put", {})
    return {
        "raw": put.get("raw", 0),
        "valid": put.get("valid", 0),
        "put_raw": put.get("put_raw", 0),
        "put_valid": put.get("put_valid", 0),
        "concrete_raw": put.get("concrete_raw", 0),
        "concrete_valid": put.get("concrete_valid", 0),
    }


def summary_artifact_rows(
        result: dict, result_path: Path | None = None) -> list[dict]:
    rows = []
    seen = set()
    for path in _summary_paths(result, result_path):
        doc = _load_json(path)
        for row in ((doc.get("deliverable_b") or {}).get("rows") or []):
            if row.get("refused") or row.get("stale"):
                continue
            kind = row.get("kind")
            if kind not in {"put", "concrete"}:
                continue
            if _row_is_unsupported_concrete(row):
                continue
            key = (row.get("file"), row.get("test"), row.get("unit"),
                   row.get("enc"), kind)
            if key in seen:
                continue
            seen.add(key)
            enriched = dict(row)
            enriched["summary_path"] = str(path)
            rows.append(enriched)
    return rows


def summary_artifact_totals(
        result: dict, result_path: Path | None = None) -> dict | None:
    paths = _summary_paths(result, result_path)
    rows = summary_artifact_rows(result, result_path)
    if not rows and not paths:
        return None
    if not rows:
        # Some historical runs recorded `put_summary_paths` for units whose
        # summary files have no deliverable rows, while the canonical
        # result.json still carries valid concrete fallbacks in `put.raw_tests`
        # / `put.valid_tests`.  An empty summary is not stronger evidence than
        # the row-level totals; falling back avoids turning valid references
        # into no-valid cases.
        return None
    return {
        "raw": len(rows),
        "valid": sum(1 for row in rows if row.get("valid_reference_test")),
        "put_raw": sum(1 for row in rows if row.get("kind") == "put"),
        "put_valid": sum(
            1 for row in rows
            if row.get("kind") == "put" and row.get("valid_reference_test")),
        "concrete_raw": sum(
            1 for row in rows if row.get("kind") == "concrete"),
        "concrete_valid": sum(
            1 for row in rows
            if row.get("kind") == "concrete"
            and row.get("valid_reference_test")),
    }


def artifact_totals(result: dict, result_path: Path | None = None) -> dict:
    return summary_artifact_totals(result, result_path) or _artifact_totals(
        result)


def _summary_valid_put_has_r1r2(
        result: dict, result_path: Path | None = None) -> bool:
    for row in summary_artifact_rows(result, result_path):
        if row.get("kind") != "put" or not row.get("valid_reference_test"):
            continue
        if _put_json_has_r1r2(Path(row["summary_path"]), row.get("test")):
            return True
    return False


def _float_or_zero(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _time_block(rows: list[dict], key: str) -> dict:
    values = [_float_or_zero(row.get(key)) for row in rows]
    nonzero = [value for value in values if value > 0]
    if not nonzero:
        return {"sum_s": 0.0, "mean_s": 0.0, "median_s": 0.0}
    return {
        "sum_s": round(sum(nonzero), 3),
        "mean_s": round(statistics.mean(nonzero), 3),
        "median_s": round(statistics.median(nonzero), 3),
    }


def _time_stats(rows: list[dict]) -> dict:
    return {
        "wall_total": _time_block(rows, "wall_total_s"),
        "stage2": _time_block(rows, "stage2_wall_s"),
        "stage4": _time_block(rows, "stage4_wall_s"),
        "foundry_replay_outside_timeout": _time_block(rows, "foundry_replay_wall_s"),
    }


def triage_rows(result_root: Path, datasets: list[str]) -> list[dict]:
    rows = []
    for dataset, subject_id, path in latest_result_paths(result_root, datasets):
        result = _load_json(path)
        bucket = quality_bucket(result, path)
        row = result.get("row", {})
        cert = result.get("certification", {})
        totals = artifact_totals(result, path)
        rows.append({
            "dataset": dataset,
            "subject_id": subject_id,
            "contract": row.get("contract") or result.get("subject", {}).get("contract"),
            "quality_bucket": bucket,
            "triage_cause": classify(result, bucket, path),
            "completion_status": row.get("completion_status"),
            "early_stop_reason": row.get("early_stop_reason"),
            "cert_bucket_counts": cert.get("bucket_counts") or row.get("cert_bucket_counts") or {},
            "cert_exit_counts": cert.get("exit_counts") or row.get("cert_exit_counts") or {},
            "result_json": str(path),
            "wall_total_s": row.get("wall_total_s") or row.get("wall"),
            "stage2_wall_s": row.get("stage2_wall_s"),
            "stage4_wall_s": row.get("stage4_wall_s"),
            "foundry_replay_wall_s": row.get("foundry_replay_wall_s"),
            "maxrss_mb": row.get("maxrss_mb"),
            **totals,
        })
    return rows


def build_doc(result_root: Path, datasets: list[str]) -> dict:
    rows = triage_rows(result_root, datasets)
    by_dataset: dict[str, dict] = {}
    for dataset in datasets:
        ds_rows = [row for row in rows if row["dataset"] == dataset]
        by_dataset[dataset] = {
            "rows": len(ds_rows),
            "quality_bucket": dict(Counter(row["quality_bucket"] for row in ds_rows)),
            "triage_cause": dict(Counter(row["triage_cause"] for row in ds_rows)),
            "artifact_totals": {
                key: sum(row.get(key, 0) or 0 for row in ds_rows)
                for key in ("raw", "valid", "put_raw", "put_valid",
                            "concrete_raw", "concrete_valid")
            },
            "time_stats": _time_stats(ds_rows),
            "maxrss_mb": {
                "max": max((_float_or_zero(row.get("maxrss_mb"))
                            for row in ds_rows), default=0.0),
            },
        }
    return {
        "schema": "veriput-rq1-triage/v1",
        "generated_at": _utc_now(),
        "result_root": str(result_root),
        "datasets": datasets,
        "summary": by_dataset,
        "rows": rows,
    }


def queue_order(row: dict) -> tuple[int, str, str, str]:
    unobservable = row["triage_cause"] in {
        "rollback-unobservable",
        "revert-unobservable",
    }
    hard_no_r1r2 = row["triage_cause"] in {
        "mapping-dynarray-unrendered",
        "no-candidate-assertion",
    }
    if (row["quality_bucket"] == "valid-PUT-no-R1R2"
            and not unobservable and not hard_no_r1r2):
        bucket_rank = 0
    elif row["quality_bucket"] == "valid-no-PUT":
        bucket_rank = 1
    elif row["quality_bucket"] == "PUT-with-R1R2-but-no-width":
        bucket_rank = 2
    elif row["quality_bucket"] == "no-valid":
        bucket_rank = 3
    elif row["quality_bucket"] == "valid-PUT-no-R1R2":
        bucket_rank = 4
    elif row["quality_bucket"] == "valid-PUT-with-R1R2":
        bucket_rank = 5
    else:
        bucket_rank = 9
    cause_rank = {
        "normal-r0-only-other": 0,
        "timeout_concrete_fallback": 1,
        "cleared_not_certified_fallback": 2,
        "unsupported-calldata-type": 3,
        "cert-no-coordinate": 4,
        "cert-no-witness-unknown": 5,
        "stage2-no-output-timeout": 6,
        "mapping-dynarray-unrendered": 8,
        "no-observable-oracle-no-width": 8,
        "not-parameterized-no-wide-rendered-coordinate": 8,
        "rollback-unobservable": 8,
        "revert-unobservable": 8,
    }.get(row["triage_cause"], 7)
    dataset_rank = {
        "real203": 0,
        "bugfix124": 1,
        "peer182": 2,
    }.get(row["dataset"], 9)
    return (bucket_rank, cause_rank, dataset_rank, row["subject_id"])


def markdown(doc: dict, limit: int) -> str:
    lines = [
        "# VeriPUT RQ1 Triage",
        "",
        f"Generated: `{doc['generated_at']}`",
        f"Result root: `{doc['result_root']}`",
        "",
        "## Summary",
        "",
    ]
    for dataset, summary in doc["summary"].items():
        lines.append(f"### {dataset}")
        lines.append("")
        lines.append(f"- subjects: {summary['rows']}")
        lines.append(f"- quality_bucket: `{json.dumps(summary['quality_bucket'], sort_keys=True)}`")
        lines.append(f"- triage_cause: `{json.dumps(summary['triage_cause'], sort_keys=True)}`")
        lines.append(f"- artifacts: `{json.dumps(summary['artifact_totals'], sort_keys=True)}`")
        lines.append(f"- time_stats: `{json.dumps(summary['time_stats'], sort_keys=True)}`")
        lines.append(f"- maxrss_mb: `{json.dumps(summary['maxrss_mb'], sort_keys=True)}`")
        lines.append("")
    queue = [
        row for row in doc["rows"]
        if row["quality_bucket"] != "valid-PUT-with-R1R2"
    ]
    queue.sort(key=queue_order)
    lines += ["## Action Queue", ""]
    for row in queue[:limit]:
        lines.append(
            f"- `{row['dataset']}` `{row['subject_id']}` "
            f"{row['quality_bucket']} / {row['triage_cause']} "
            f"(valid={row['valid']}, put_valid={row['put_valid']}, "
            f"concrete_valid={row['concrete_valid']})")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--benchmark", choices=("all",) + DEFAULT_DATASETS,
                        default=None,
                        help="Compatibility alias for old triage invocations.")
    parser.add_argument("--dataset", action="append", choices=DEFAULT_DATASETS,
                        help="Dataset to include; repeatable. Defaults to all.")
    parser.add_argument("--json", action="store_true",
                        help="Compatibility mode: print JSON to stdout.")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--limit", type=int, default=80,
                        help="Number of queue rows to print in markdown/stdout.")
    parser.add_argument("--sample-limit", type=int,
                        help="Compatibility alias for --limit.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.benchmark and args.benchmark != "all":
        datasets = [args.benchmark]
    elif args.benchmark == "all":
        datasets = list(DEFAULT_DATASETS)
    else:
        datasets = args.dataset or list(DEFAULT_DATASETS)
    limit = args.sample_limit if args.sample_limit is not None else args.limit
    doc = build_doc(args.result_root, datasets)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
        return 0
    text = markdown(doc, limit)
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
