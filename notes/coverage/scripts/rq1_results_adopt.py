#!/usr/bin/env python3
"""Adopt VeriPUT RQ1 run artifacts into the canonical Results tree.

The worker scripts run cases in-place or on a remote mirror.  This script is the
single hard write-back path for canonicalizing result.json / put.json and
preserving raw + valid artifacts under Results/RQ1/VeriPUT.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import time
from pathlib import Path


DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
HISTORICAL_SUFFIX_RE = re.compile(
    r"(?P<canonical>.+?)"
    r"(?P<suffix>"
    r"\.redo\..+"
    r"|\.superseded\..+"
    r"|\.adopted_from_.+"
    r"|\.incomplete\..+"
    r")$")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tmp:
        json.dump(doc, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _copytree_merge(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    same_tree = False
    try:
        same_tree = src.resolve() == dst.resolve()
    except OSError:
        same_tree = False
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if same_tree:
            continue
        try:
            if item.resolve() == target.resolve():
                continue
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def canonical_subject_id(subject_id: str) -> str:
    match = HISTORICAL_SUFFIX_RE.match(subject_id)
    if match:
        return match.group("canonical")
    return subject_id


def _subject_key(subject_dir: Path) -> tuple[str, str] | None:
    parts = subject_dir.parts
    if "subjects" not in parts:
        return None
    idx = parts.index("subjects")
    if idx == 0 or idx + 1 >= len(parts):
        return None
    return parts[idx - 1], canonical_subject_id(parts[idx + 1])


def _source_subject_id(subject_dir: Path) -> str:
    parts = subject_dir.parts
    if "subjects" not in parts:
        return subject_dir.name
    idx = parts.index("subjects")
    if idx + 1 >= len(parts):
        return subject_dir.name
    return parts[idx + 1]


def _oracle_tags(result_doc: dict, put_doc: dict) -> dict:
    tags = set()
    for doc in (result_doc, put_doc):
        for key in ("oracle_tags", "assertion_tags", "region_tags"):
            value = doc.get(key)
            if isinstance(value, list):
                tags.update(str(item) for item in value)
        stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
        for key in ("oracle_classes", "assertion_classes", "rungs"):
            value = stats.get(key)
            if isinstance(value, list):
                tags.update(str(item) for item in value)
    lowered = {tag.lower() for tag in tags}
    return {
        "has_R0": any("r0" in tag for tag in lowered),
        "has_R1": any("r1" in tag for tag in lowered),
        "has_R2": any("r2" in tag for tag in lowered),
        "oracle_tags": sorted(tags),
    }


def _count_metric(key: str, *docs: dict) -> int:
    values = []
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
    return max(values or [0])


def _artifact_metrics(result_doc: dict, put_doc: dict) -> dict:
    row = result_doc.get("row") if isinstance(result_doc.get("row"), dict) else {}
    put = result_doc.get("put") if isinstance(result_doc.get("put"), dict) else {}
    docs = (result_doc, row, put, put_doc)
    metrics = {
        "valid": _count_metric("valid", *docs),
        "put_valid": _count_metric("put_valid", *docs),
        "concrete_valid": _count_metric("concrete_valid", *docs),
        "valid_put_with_R1": _count_metric("valid_put_with_R1", *docs),
        "valid_put_with_R2": _count_metric("valid_put_with_R2", *docs),
        "valid_put_with_R1_or_R2":
            _count_metric("valid_put_with_R1_or_R2", *docs),
    }
    if metrics["valid"] <= 0:
        metrics["valid"] = metrics["put_valid"] + metrics["concrete_valid"]
    return metrics


def adopt_subject(src_subject: Path, results_root: Path) -> dict:
    key = _subject_key(src_subject)
    if not key:
        return {"source": str(src_subject), "adopted": False, "reason": "bad-key"}
    dataset, subject = key
    source_subject = _source_subject_id(src_subject)
    dst_subject = results_root / dataset / "subjects" / subject
    result_doc = _json(src_subject / "result.json")
    put_doc = _json(src_subject / "put.json")
    tags = _oracle_tags(result_doc, put_doc)
    metrics = _artifact_metrics(result_doc, put_doc)
    valid = metrics["valid"] > 0
    put_valid = metrics["put_valid"] > 0
    concrete_valid = metrics["concrete_valid"] > 0 or (valid and not put_valid)
    summary = {
        "schema": "veriput-rq1-adopted-subject/v1",
        "adopted_ts": time.time(),
        "source": str(src_subject),
        "source_subject_dir": str(src_subject),
        "source_subject_id": source_subject,
        "dataset": dataset,
        "subject": subject,
        "canonical_subject_id": subject,
        "valid": int(valid),
        "put_valid": int(put_valid),
        "concrete_valid": int(concrete_valid),
        "valid_put_with_R1": int(metrics["valid_put_with_R1"] > 0),
        "valid_put_with_R2": int(metrics["valid_put_with_R2"] > 0),
        "valid_put_with_R1_or_R2": int(metrics["valid_put_with_R1_or_R2"] > 0),
        "valid_count": metrics["valid"],
        "put_valid_count": metrics["put_valid"],
        "concrete_valid_count": metrics["concrete_valid"],
        "valid_put_with_R1_count": metrics["valid_put_with_R1"],
        "valid_put_with_R2_count": metrics["valid_put_with_R2"],
        "valid_put_with_R1_or_R2_count": metrics["valid_put_with_R1_or_R2"],
        "wall_result_s": float(result_doc.get("wall_total_s") or 0),
        "wall_put_s": float(put_doc.get("wall_total_s") or 0),
        "wall_total_s": max(float(result_doc.get("wall_total_s") or 0),
                            float(put_doc.get("wall_total_s") or 0)),
        **tags,
    }
    _copytree_merge(src_subject, dst_subject)
    if result_doc:
        merged = dict(result_doc)
        merged["adoption"] = summary
        _atomic_write_json(dst_subject / "result.json", merged)
    if put_doc:
        merged = dict(put_doc)
        merged["adoption"] = summary
        _atomic_write_json(dst_subject / "put.json", merged)
    _atomic_write_json(dst_subject / "adoption.json", summary)
    return {"source": str(src_subject), "target": str(dst_subject), "adopted": True,
            **summary}


def candidate_subjects(root: Path) -> list[Path]:
    seen = set()
    out = []
    for marker in ("result.json", "put.json", "adoption.json"):
        for path in root.glob(f"**/{marker}"):
            subject_dir = path.parent
            key = str(subject_dir.resolve())
            if key in seen:
                continue
            seen.add(key)
            if _subject_key(subject_dir):
                out.append(subject_dir)
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--subject-dir", type=Path)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = []
    subjects = [args.subject_dir] if args.subject_dir else candidate_subjects(args.source_root)
    for subject_dir in subjects:
        if args.dry_run:
            rows.append({
                "source": str(subject_dir),
                "adopted": False,
                "reason": "dry-run",
            })
        else:
            rows.append(adopt_subject(subject_dir, args.results_root))
    doc = {
        "schema": "veriput-rq1-results-adopt/v1",
        "source_root": str(args.source_root),
        "results_root": str(args.results_root),
        "count": len(rows),
        "adopted": sum(1 for row in rows if row.get("adopted")),
        "rows": rows,
    }
    payload = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
