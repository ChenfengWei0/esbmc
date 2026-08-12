#!/usr/bin/env python3
"""Audit canonical VeriPUT RQ1 artifacts and rebuild dataset journals."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from collections import Counter
from pathlib import Path


DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
HISTORICAL_SUFFIX_RE = re.compile(
    r"(?P<canonical>.+?)(?P<suffix>\.redo\..+|\.superseded\..+|"
    r"\.adopted_from_.+|\.incomplete\..+|\.pre-owned-put\..+|"
    r"\.pre-valuegate-.+|\.pre_valuegate\..+|\.failed\..+|"
    r"\.failed_restore\..+)$")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tmp:
        json.dump(doc, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tmp:
        for row in rows:
            tmp.write(json.dumps(row, sort_keys=True) + "\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def canonical_subject(subject: str) -> tuple[str, bool]:
    match = HISTORICAL_SUFFIX_RE.match(subject)
    if not match:
        return subject, False
    return match.group("canonical"), True


def _valid_reference_test(test: dict) -> bool:
    if (test.get("stage2_source") == "no_unit_deploy_fallback"
            or test.get("stage4_kind") in ("deploy-only", "creation-code-only")):
        return False
    return (test.get("valid_reference_test") is True and not test.get("stale")
            and test.get("refused") is not True)


def _detailed_test_rows(result: dict) -> list[dict]:
    rows = []
    for source in (result, result.get("row"), result.get("put"),
                   result.get("adoption")):
        if not isinstance(source, dict):
            continue
        for key in ("raw_tests", "valid_tests", "raw_artifacts", "valid_artifacts"):
            value = source.get(key)
            if isinstance(value, list):
                rows.extend(test for test in value if isinstance(test, dict))
    deduped = {}
    for test in rows:
        identity = (str(test.get("file") or ""), str(test.get("test") or ""),
                    str(test.get("kind") or ""), str(test.get("unit") or ""),
                    str(test.get("enc") if test.get("enc") is not None else ""))
        deduped[identity] = test
    return list(deduped.values())


def metric_counts(result: dict) -> dict:
    detailed = _detailed_test_rows(result)
    if detailed:
        valid = [test for test in detailed if _valid_reference_test(test)]
        valid_puts = [test for test in valid if test.get("kind") == "put"]
        with_r1 = [test for test in valid_puts if "R1" in (test.get("oracle_classes") or [])]
        with_r2 = [test for test in valid_puts if "R2" in (test.get("oracle_classes") or [])]
        with_r1r2 = [
            test for test in valid_puts
            if {"R1", "R2"} & set(test.get("oracle_classes") or [])
        ]
        return {
            "raw": len(detailed),
            "valid": len(valid),
            "put_valid": len(valid_puts),
            "concrete_valid": sum(test.get("kind") == "concrete" for test in valid),
            "valid_put_with_R1": len(with_r1),
            "valid_put_with_R2": len(with_r2),
            "valid_put_with_R1_or_R2": len(with_r1r2),
            "valid_put_without_R1R2": len(valid_puts) - len(with_r1r2),
        }
    row = result.get("row") if isinstance(result.get("row"), dict) else {}
    put = result.get("put") if isinstance(result.get("put"), dict) else {}
    adoption = result.get("adoption") if isinstance(result.get("adoption"), dict) else {}
    docs = [result, row, put, adoption]
    counts: dict[str, int] = {}
    for key in (
            "raw", "valid", "put_valid", "concrete_valid",
            "valid_put_with_R1", "valid_put_with_R2",
            "valid_put_with_R1_or_R2", "valid_put_without_R1R2"):
        values: list[int] = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            try:
                values.append(int(doc.get(key) or 0))
            except (TypeError, ValueError):
                pass
            artifact_counts = doc.get("artifact_counts")
            if isinstance(artifact_counts, dict):
                try:
                    values.append(int(artifact_counts.get(key) or 0))
                except (TypeError, ValueError):
                    pass
        counts[key] = max(values or [0])
    if not counts["valid"]:
        counts["valid"] = counts["put_valid"] + counts["concrete_valid"]
    if not counts["raw"]:
        counts["raw"] = counts["valid"]
    return counts


def quality_bucket(counts: dict) -> str:
    if counts["valid"] <= 0:
        return "no-valid"
    if counts["put_valid"] <= 0:
        return "valid-no-PUT"
    if counts["valid_put_with_R1_or_R2"] > 0:
        return "valid-PUT-with-R1R2"
    return "valid-PUT-no-R1R2"


def oracle_tags(result: dict) -> dict:
    tags = set()
    for source in (result, result.get("row"), result.get("put")):
        if not isinstance(source, dict):
            continue
        for key in ("oracle_tags", "oracle_classes"):
            value = source.get(key)
            if isinstance(value, list):
                tags.update(str(item) for item in value)
        for oracle in source.get("assertion_oracles") or []:
            if not isinstance(oracle, dict):
                continue
            for cls in oracle.get("classes") or []:
                tags.add(str(cls))
    lowered = {tag.lower() for tag in tags}
    return {
        "has_R0": any("r0" == tag or "r0" in tag for tag in lowered),
        "has_R1": any("r1" == tag or "r1" in tag for tag in lowered),
        "has_R2": any("r2" == tag or "r2" in tag for tag in lowered),
        "oracle_tags": sorted(tags),
    }


def subject_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/subjects/*/result.json"))


def choose_canonical(root: Path) -> dict[tuple[str, str], dict]:
    chosen: dict[tuple[str, str], dict] = {}
    for subject_dir in subject_dirs(root):
        bench = subject_dir.parent.parent.name
        subject = subject_dir.name
        canonical, historical = canonical_subject(subject)
        result = read_json(subject_dir / "result.json")
        counts = metric_counts(result)
        score = (
            1 if counts["valid"] else 0,
            1 if counts["put_valid"] else 0,
            1 if counts["valid_put_with_R1_or_R2"] else 0,
            counts["valid"],
            counts["put_valid"],
            counts["valid_put_with_R1_or_R2"],
            0 if historical else 1,
            (subject_dir / "result.json").stat().st_mtime,
        )
        row = {
            "bench": bench,
            "subject": subject,
            "canonical_subject": canonical,
            "historical": historical,
            "subject_dir": subject_dir,
            "result": result,
            "counts": counts,
            "score": score,
        }
        key = (bench, canonical)
        if key not in chosen or score > chosen[key]["score"]:
            chosen[key] = row
    return chosen


def build_summary_row(item: dict) -> dict:
    result = item["result"]
    row = result.get("row") if isinstance(result.get("row"), dict) else dict(result)
    row = dict(row)
    counts = item["counts"]
    row.update(counts)
    row["artifact_counts"] = dict(counts)
    row["benchmark"] = f"{item['bench']}__{item['canonical_subject']}"
    row["dataset"] = item["bench"]
    row["subject_id"] = item["canonical_subject"]
    row["key"] = item["canonical_subject"]
    row["result_json"] = str(item["subject_dir"] / "result.json")
    row["artifact_root"] = str(item["subject_dir"])
    row["quality_bucket"] = quality_bucket(counts)
    row.setdefault("schema", "veriput-rq1-row/v1")
    return row


def adoption_summary(item: dict, row: dict) -> dict:
    counts = item["counts"]
    tags = oracle_tags(item["result"])
    return {
        "schema": "veriput-rq1-adopted-subject/v1",
        "adopted_ts": time.time(),
        "source": str(item["subject_dir"]),
        "source_subject_dir": str(item["subject_dir"]),
        "source_subject_id": item["subject"],
        "dataset": item["bench"],
        "subject": item["canonical_subject"],
        "canonical_subject_id": item["canonical_subject"],
        "valid": int(counts["valid"] > 0),
        "put_valid": int(counts["put_valid"] > 0),
        "concrete_valid": int(counts["concrete_valid"] > 0),
        "valid_put_with_R1_or_R2": int(counts["valid_put_with_R1_or_R2"] > 0),
        "raw_count": counts["raw"],
        "valid_count": counts["valid"],
        "put_valid_count": counts["put_valid"],
        "concrete_valid_count": counts["concrete_valid"],
        "valid_put_with_R1_or_R2_count": counts["valid_put_with_R1_or_R2"],
        "quality_bucket": row["quality_bucket"],
        "wall_result_s": float(row.get("wall_total_s") or 0),
        "wall_put_s": float(row.get("put_all_wall_s") or 0),
        "wall_total_s": float(row.get("wall_total_s") or 0),
        **tags,
    }


def audit(args: argparse.Namespace) -> dict:
    chosen = choose_canonical(args.results_root)
    rows = []
    by_dataset: dict[str, list[dict]] = {}
    mismatches = []
    for key in sorted(chosen):
        item = chosen[key]
        summary_row = build_summary_row(item)
        adoption = adoption_summary(item, summary_row)
        result = dict(item["result"])
        old_adoption = result.get("adoption") if isinstance(result.get("adoption"), dict) else {}
        old_tuple = (
            int(old_adoption.get("valid") or 0),
            int(old_adoption.get("put_valid") or 0),
            int(old_adoption.get("valid_put_with_R1_or_R2") or 0),
        )
        new_tuple = (
            int(adoption["valid"]),
            int(adoption["put_valid"]),
            int(adoption["valid_put_with_R1_or_R2"]),
        )
        if old_tuple != new_tuple:
            mismatches.append({
                "bench": item["bench"],
                "subject": item["canonical_subject"],
                "old": old_tuple,
                "new": new_tuple,
                "result_json": str(item["subject_dir"] / "result.json"),
            })
        if args.rewrite:
            result["row"] = summary_row
            result["adoption"] = adoption
            atomic_write_json(item["subject_dir"] / "result.json", result)
            atomic_write_json(item["subject_dir"] / "adoption.json", adoption)
        rows.append({
            "bench": item["bench"],
            "subject": item["canonical_subject"],
            "source_subject": item["subject"],
            "historical_source": item["historical"],
            "valid": int(summary_row["valid"] > 0),
            "put_valid": int(summary_row["put_valid"] > 0),
            "r1r2": int(summary_row["valid_put_with_R1_or_R2"] > 0),
            "raw_count": summary_row["raw"],
            "valid_count": summary_row["valid"],
            "put_valid_count": summary_row["put_valid"],
            "r1r2_count": summary_row["valid_put_with_R1_or_R2"],
            "quality_bucket": summary_row["quality_bucket"],
            "result_json": str(item["subject_dir"] / "result.json"),
        })
        by_dataset.setdefault(item["bench"], []).append(summary_row)
    if args.rewrite:
        for dataset, dataset_rows in by_dataset.items():
            atomic_write_jsonl(args.results_root / dataset / "results.jsonl",
                               sorted(dataset_rows, key=lambda row: row["key"]))
    counts = Counter(row["quality_bucket"] for row in rows)
    by_bench = {}
    for bench in sorted(by_dataset):
        bench_rows = [row for row in rows if row["bench"] == bench]
        by_bench[bench] = {
            "total": len(bench_rows),
            "valid": sum(row["valid"] for row in bench_rows),
            "put": sum(row["put_valid"] for row in bench_rows),
            "r1r2": sum(row["r1r2"] for row in bench_rows),
            "no_valid": sum(1 for row in bench_rows if not row["valid"]),
            "valid_no_put": sum(1 for row in bench_rows
                                if row["valid"] and not row["put_valid"]),
            "put_no_r1r2": sum(1 for row in bench_rows
                               if row["put_valid"] and not row["r1r2"]),
        }
    return {
        "schema": "veriput-rq1-artifact-audit/v1",
        "results_root": str(args.results_root),
        "rewritten": bool(args.rewrite),
        "total": len(rows),
        "valid": sum(row["valid"] for row in rows),
        "put": sum(row["put_valid"] for row in rows),
        "r1r2": sum(row["r1r2"] for row in rows),
        "no_valid": sum(1 for row in rows if not row["valid"]),
        "valid_no_put": sum(1 for row in rows if row["valid"] and not row["put_valid"]),
        "put_no_r1r2": sum(1 for row in rows if row["put_valid"] and not row["r1r2"]),
        "quality_bucket_counts": dict(sorted(counts.items())),
        "by_benchmark": by_bench,
        "adoption_mismatch_count": len(mismatches),
        "adoption_mismatches": mismatches,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--out", type=Path,
                        default=Path("notes/coverage/rq1_artifact_audit.json"))
    parser.add_argument("--rewrite", action="store_true")
    args = parser.parse_args()
    doc = audit(args)
    atomic_write_json(args.out, doc)
    print(json.dumps({
        key: doc[key]
        for key in ("total", "valid", "put", "r1r2", "no_valid",
                    "valid_no_put", "put_no_r1r2",
                    "adoption_mismatch_count", "rewritten")
    }, indent=2, sort_keys=True))
    print(json.dumps(doc["by_benchmark"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
