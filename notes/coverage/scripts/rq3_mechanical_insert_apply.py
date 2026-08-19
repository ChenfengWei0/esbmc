#!/usr/bin/env python3
"""Mechanically insert source-backed exact RQ3 concrete rows into RQ1.

This is an evidence copy only.  It does not run ESBMC or Forge and never
rewrites an identity.  Every changed result is backed up in ``preimage``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("unit", "")), str(row.get("enc", "")),
            str(row.get("piece") or ""))


def project_root(source_file: Path) -> Path:
    test_dir = source_file.parent
    if test_dir.name != "test" or not (test_dir.parent / "foundry.toml").is_file():
        raise ValueError(f"source is not a Foundry project test: {source_file}")
    return test_dir.parent


def job_root(put_json: Path) -> Path:
    # put/<path-function>/_wd/<attempt>/put.json
    if len(put_json.parents) < 3 or put_json.parent.parent.name != "_wd":
        raise ValueError(f"unexpected put.json layout: {put_json}")
    return put_json.parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--transaction", type=Path, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="perform the mechanical copy; default is a dry run")
    args = parser.parse_args()
    manifest = read(args.manifest)
    tx = args.transaction.resolve()
    if tx.exists() and args.apply:
        raise SystemExit(f"transaction already exists: {tx}")

    selected = []
    for row in manifest.get("rows", []):
        candidate = row.get("rq3_candidate") or {}
        if (row.get("insertion_status") == "mapped"
                and row.get("match_tier") == "exact"
                and candidate.get("file_exists") and candidate.get("file")
                and candidate.get("put_json")):
            selected.append(row)

    report: dict[str, Any] = {
        "schema": "veriput-rq3-mechanical-insert/v1",
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256(args.manifest),
        "results_root": str(args.results_root.resolve()),
        "transaction": str(tx),
        "apply": bool(args.apply),
        "policy": {"esbmc_run": False, "forge_run": False,
                   "identity_rewrite": False, "canonical_source": "RQ1"},
        "rows": [],
    }
    if args.apply:
        tx.mkdir(parents=True)
        (tx / "preimage").mkdir()

    for item in selected:
        frozen = item["frozen_identity"]
        case, path_function, unit, enc, piece = frozen
        dataset, subject = case.split("/", 1)
        candidate = item["rq3_candidate"]
        source_file = Path(candidate["file"]).resolve()
        source_put = Path(candidate["put_json"]).resolve()
        target_subject = args.results_root / dataset / "subjects" / subject
        entry: dict[str, Any] = {"frozen_identity": frozen,
                                 "source_file": str(source_file),
                                 "source_put_json": str(source_put),
                                 "target_subject": str(target_subject),
                                 "status": "planned"}
        try:
            source_project = project_root(source_file)
            source_job = job_root(source_put)
            if not source_file.is_file() or not source_put.is_file():
                raise ValueError("source evidence is missing")
            if not target_subject.is_dir():
                raise ValueError("RQ1 target subject is missing")
            result_path = target_subject / "result.json"
            result = read(result_path) if result_path.is_file() else {}
            put = result.setdefault("put", {})
            existing = {identity(row) for row in put.get("valid_tests", [])
                        if isinstance(row, dict)}
            key = (unit, enc, piece)
            if key in existing:
                entry["status"] = "skipped-existing"
                report["rows"].append(entry)
                continue
            if args.apply:
                safe = hashlib.sha256(
                    ("|".join(str(x) for x in frozen)).encode()).hexdigest()[:16]
                preimage = tx / "preimage" / dataset / subject
                preimage.mkdir(parents=True, exist_ok=True)
                if result_path.is_file():
                    shutil.copy2(result_path, preimage / "result.json")
                destination_project = (target_subject / "concrete-replays" /
                                       "projects" / f"rq3-mechanical-{safe}")
                destination_job = target_subject / "put" / unit / f"rq3-mechanical-{safe}"
                if destination_project.exists() or destination_job.exists():
                    raise ValueError("destination collision")
                shutil.copytree(source_project, destination_project)
                shutil.copytree(source_job, destination_job)
                target_test = destination_project / "test" / source_file.name
                target_put = next(destination_job.rglob("put.json"))
                row = read(source_put)
                row["file"] = str(target_test)
                row["put_json"] = str(target_put)
                row["path_function"] = path_function
                row["unit"] = unit
                row["enc"] = enc
                row["piece"] = piece or None
                row["kind"] = "concrete"
                row["is_concrete"] = True
                row["is_put"] = False
                row["rq3_mechanical_insert"] = {
                    "source_put_json_sha256": sha256(source_put),
                    "source_test_sha256": sha256(source_file),
                    "inserted_at": time.time(),
                    "forge_run": False,
                    "esbmc_run": False,
                }
                write(target_put, row)
                for key_name in ("raw_tests", "valid_tests", "raw_artifacts",
                                 "valid_artifacts"):
                    put.setdefault(key_name, []).append(row)
                put["concrete_valid"] = int(put.get("concrete_valid") or 0) + 1
                put["valid"] = int(put.get("valid") or 0) + 1
                put["raw"] = int(put.get("raw") or 0) + 1
                result.setdefault("rq3_mechanical_insertions", []).append({
                    "identity": frozen, "source": str(source_put),
                    "target": str(target_put), "source_sha256": sha256(source_file)})
                write(result_path, result)
                entry.update({"status": "inserted", "target_file": str(target_test),
                              "target_put_json": str(target_put),
                              "preimage": str(preimage / "result.json")})
            report["rows"].append(entry)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            entry.update({"status": "refused", "reason": str(error)})
            report["rows"].append(entry)

    counts = {}
    for row in report["rows"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    report["counts"] = {"selected": len(selected), **counts}
    if args.apply:
        write(tx / "insert-report.json", report)
    print(json.dumps({"schema": report["schema"], **report["counts"]}, sort_keys=True))
    return 0 if not counts.get("refused") else 1


if __name__ == "__main__":
    raise SystemExit(main())
