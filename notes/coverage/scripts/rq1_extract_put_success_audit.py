#!/usr/bin/env python3
"""Extract canonical RQ1 PUT tests with recorded Forge success evidence.

The source result tree intentionally retains retries, workdirs, failed emission
attempts, and replay projects.  This script starts from the canonical
strict-valid rows and copies only the physical PUT test files selected into RQ1,
plus the JSON/log evidence needed to audit each selected test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rq1_concrete_replay_migrate import (  # noqa: E402
    DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests,
)
from rq1_concrete_replay_store import _physical_test_kind  # noqa: E402

DEFAULT_OUT = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT-clean-audit/"
    "put-success-1467")


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _copy_file(src: Path, dst: Path) -> dict:
    record = {
        "source": str(src),
        "present": src.is_file(),
        "sha256": _sha256(src),
    }
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        record["copy"] = str(dst)
        record["copy_sha256"] = _sha256(dst)
    return record


def _atomic_json(path: Path, doc: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        json.dump(doc, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _nearest_foundry_toml(test_file: Path) -> Path | None:
    for parent in [test_file.parent, *test_file.parents]:
        candidate = parent / "foundry.toml"
        if candidate.is_file():
            return candidate
    return None


def _put_unit_dir(put_json: Path) -> Path | None:
    if not put_json.is_file():
        return None
    for parent in put_json.parents:
        if (parent / "put-summary.json").is_file():
            return parent
    return None


def _summary_row(summary: Path, row: dict) -> tuple[dict | None, str]:
    doc = _read_json(summary)
    rows = ((doc.get("deliverable_b") or {}).get("rows") or [])
    matches = [
        item for item in rows
        if isinstance(item, dict)
        and str(item.get("file") or "") == str(row.get("file") or "")
        and str(item.get("test") or "") == str(row.get("test") or "")
    ]
    if len(matches) == 1:
        return matches[0], "exact"
    if not matches:
        return None, "missing"
    return matches[0], "ambiguous"


def _summary_evidence_status(summary_row: dict | None, match_status: str) -> str:
    if not isinstance(summary_row, dict):
        return f"summary_{match_status}"
    gates = summary_row.get("gates") or {}
    required = ("assert", "corpus", "fuzz", "green", "width")
    if (summary_row.get("forge_status") == "Success"
            and all(gates.get(name) is True for name in required)):
        return "summary_success_all_gates"
    if summary_row.get("forge_status") == "Success":
        return "summary_success_missing_gate"
    return f"summary_{summary_row.get('forge_status') or 'unknown'}"


def _raw_forge_evidence(unit_dir: Path | None) -> list[Path]:
    if unit_dir is None or not unit_dir.is_dir():
        return []
    matches = []
    for path in unit_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if "forge" in name and (name.endswith(".log") or name.endswith(".json")):
            matches.append(path)
    return sorted(matches)


def collect_success_puts(result_root: Path) -> tuple[list[dict], dict]:
    deduped: dict[tuple[str, str], dict] = {}
    row_count = 0
    success_row_count = 0
    for case, subject_dir in _case_dirs(result_root):
        for row in _strict_valid_tests(subject_dir):
            if _physical_test_kind(row) != "put":
                continue
            row_count += 1
            if row.get("forge_status") != "Success":
                continue
            success_row_count += 1
            file_name = str(row.get("file") or "")
            test_name = str(row.get("test") or "")
            if not file_name or not test_name:
                continue
            key = (file_name, test_name)
            entry = deduped.setdefault(key, {
                "case": case,
                "subject_dir": str(subject_dir),
                "file": file_name,
                "test": test_name,
                "rows": [],
            })
            entry["rows"].append(dict(row))
    return list(deduped.values()), {
        "strict_valid_put_rows": row_count,
        "strict_valid_put_rows_with_forge_success": success_row_count,
        "unique_physical_put_tests_with_forge_success": len(deduped),
    }


def extract(result_root: Path, out: Path) -> dict:
    entries, counts = collect_success_puts(result_root)
    if out.exists():
        raise SystemExit(f"output already exists: {out}")
    out.mkdir(parents=True)
    entry_records = []
    summary_counts: dict[str, int] = {}
    raw_forge_log_present = 0
    for index, entry in enumerate(entries):
        row = entry["rows"][0]
        test_file = Path(entry["file"])
        put_json = Path(str(row.get("put_json") or ""))
        unit_dir = _put_unit_dir(put_json)
        summary = (unit_dir / "put-summary.json") if unit_dir else Path()
        foundry_toml = _nearest_foundry_toml(test_file)
        cert = Path(entry["subject_dir"]) / "cert" / "certify-results.jsonl"
        stable = hashlib.sha256(
            f"{entry['file']}\0{entry['test']}".encode()).hexdigest()[:16]
        entry_dir = out / "entries" / f"{index:04d}_{stable}"
        summary_row, match_status = _summary_row(summary, row) if summary else (None, "missing")
        evidence_status = _summary_evidence_status(summary_row, match_status)
        summary_counts[evidence_status] = summary_counts.get(evidence_status, 0) + 1
        raw_logs = _raw_forge_evidence(unit_dir)
        if raw_logs:
            raw_forge_log_present += 1
        copied_logs = []
        for log_path in raw_logs[:20]:
            copied_logs.append(
                _copy_file(log_path, entry_dir / "logs" / log_path.name))
        record = {
            "id": f"{index:04d}_{stable}",
            "case": entry["case"],
            "subject_dir": entry["subject_dir"],
            "test": entry["test"],
            "original_file": entry["file"],
            "duplicate_source_rows": len(entry["rows"]),
            "row": row,
            "summary_match_status": match_status,
            "summary_evidence_status": evidence_status,
            "summary_row": summary_row,
            "raw_forge_logs_found": len(raw_logs),
            "files": {
                "test": _copy_file(test_file, entry_dir / "test.t.sol"),
                "put_json": _copy_file(put_json, entry_dir / "put.json"),
                "put_summary": _copy_file(summary, entry_dir / "put-summary.json"),
                "certify_results": _copy_file(cert, entry_dir / "certify-results.jsonl"),
                "foundry_toml": (_copy_file(foundry_toml, entry_dir / "foundry.toml")
                                 if foundry_toml else {
                                     "source": None,
                                     "present": False,
                                     "sha256": None,
                                 }),
                "assert_run_log": _copy_file(put_json.parent / "assert" / "run.log",
                                             entry_dir / "logs" / "assert-run.log"),
                "emit_run_log": _copy_file(put_json.parent / "emit" / "run.log",
                                           entry_dir / "logs" / "emit-run.log"),
                "raw_forge_logs": copied_logs,
            },
        }
        entry_records.append(record)
    with (out / "entries.jsonl").open("w") as stream:
        for record in entry_records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    manifest = {
        "schema": "rq1-put-success-audit-extract/v1",
        "generated_at": time.time(),
        "result_root": str(result_root),
        "output": str(out),
        **counts,
        "entry_count": len(entry_records),
        "summary_evidence_status_counts": summary_counts,
        "entries_with_raw_forge_logs": raw_forge_log_present,
        "notes": [
            "One entry is one unique physical (test .t.sol file, test function) selected from strict valid rows.",
            "forge_status=Success is copied from the canonical strict row.",
            "raw_forge_logs_found counts retained files named *forge*.log or *forge*.json near the put-summary unit directory; many historical PUT rows only retain summary evidence.",
        ],
    }
    _atomic_json(out / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--json-only", action="store_true",
                        help="print counts without copying files")
    args = parser.parse_args()
    if args.json_only:
        entries, counts = collect_success_puts(args.results_root)
        print(json.dumps({**counts, "entry_count": len(entries)}, indent=2,
                         sort_keys=True))
        return 0
    manifest = extract(args.results_root, args.out)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
