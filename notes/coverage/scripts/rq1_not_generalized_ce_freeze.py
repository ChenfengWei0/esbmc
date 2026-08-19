#!/usr/bin/env python3
"""Freeze the canonical not-generalized RQ1 CE population for KI workers.

This is a read-only RQ1 audit. Outputs are written outside Results/RQ1 and one
row denotes exactly one (target, path_function, unit, enc, piece) obligation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from rq1_concrete_replay_migrate import DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests
from rq1_concrete_replay_store import (
    _artifact_key,
    _concrete_test_key,
    _entry_is_currently_not_generalized,
    _entry_test_keys,
    _physical_test_kind,
    audit_manifest,
    load_manifest,
)

DEFAULT_OUT = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_NoPUT/frozen-545"
)
FIELDS = [
    "identity_hash", "case", "dataset", "subject", "path_function", "unit",
    "enc", "piece", "canonical_row_file", "canonical_test",
    "canonical_test_sha256", "replay_manifest", "replay_id", "replay_project",
    "replay_test_file", "replay_flat_source", "oracle_json", "forge_status",
    "forge_passed_tests", "forge_log", "stage2_source", "stage2_witness_check",
    "unit_schedule", "target_manifest_json",
]
EVIDENCE_FIELDS = FIELDS + [
    "current_target_json",
    "replay_test_sha256", "replay_flat_sha256", "forge_command_json",
    "forge_log_sha256", "forge_verified_at",
    "cert_journal", "cert_bucket", "certified_region_json", "ce_json",
    "cov_ce_journal", "cov_report", "put_summary_paths_json",
    "historical_candidates_json", "historical_failure_kind",
    "historical_failure_reason", "feasibility_tier", "complexity_score",
    "complexity_tier",
]


def _compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _identity(case: str, key: tuple) -> tuple[list[str], str]:
    values = [case, str(key[0]), str(key[1]), str(key[2]), str(key[3])]
    return values, hashlib.sha256(_compact(values).encode()).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict]:
    values = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _target(subject_dir: Path, unit: str, path_function: str) -> dict:
    schedule_path = subject_dir / "unit-schedule.json"
    schedule = _read_json(schedule_path)
    jobs = schedule.get("jobs") if isinstance(schedule.get("jobs"), list) else []
    matches = [job for job in jobs if isinstance(job, dict)
               and job.get("unit") == unit
               and job.get("path_function") == path_function]
    if not matches:
        matches = [job for job in jobs if isinstance(job, dict) and job.get("unit") == unit]
    job = matches[0] if matches else {}
    result = _read_json(subject_dir / "result.json")
    current_target = {
        "job_id": job.get("job_id"),
        "contract": job.get("contract") or (result.get("target") or {}).get("contract"),
        "target": job.get("target") or result.get("target"),
        "subject": job.get("subject") or result.get("subject"),
        "unit_info": job.get("unit_info"),
        "static_obstacles": job.get("static_obstacles") or [],
        "sequence_strategy": job.get("sequence_strategy"),
        "source_result": str(subject_dir / "result.json"),
    }
    return {
        "unit_schedule": str(schedule_path),
        "current_target_json": _compact(current_target),
        "target_manifest_json": _compact({
            "job_id": job.get("job_id"),
            "contract": job.get("contract"),
            "target": job.get("target"),
            "subject": job.get("subject"),
            "unit_info": job.get("unit_info"),
            "static_obstacles": job.get("static_obstacles") or [],
            "sequence_strategy": job.get("sequence_strategy"),
        }),
    }


@lru_cache(maxsize=None)
def _cert_rows(subject_dir: Path) -> tuple[dict, ...]:
    return tuple(_read_jsonl(subject_dir / "cert" / "certify-results.jsonl"))


@lru_cache(maxsize=None)
def _cert_evidence(subject_dir: Path, unit: str, path_function: str,
                   enc: str) -> dict:
    journal = subject_dir / "cert" / "certify-results.jsonl"
    candidates = [row for row in _cert_rows(subject_dir)
                  if row.get("unit") == unit
                  and (not path_function or row.get("path_function") == path_function)]
    exact = [row for row in candidates
             if enc in (row.get("certified_details") or {})
             or enc in (row.get("certified") or {})]
    cert = (exact or candidates)[-1] if (exact or candidates) else {}
    detail = (cert.get("certified_details") or {}).get(enc) or {}
    failure = cert.get("failure_evidence") or {}
    cov_ce = failure.get("cov_ce_journal") or {}
    cov_report = failure.get("cov_report") or {}
    return {
        "cert_journal": str(journal),
        "cert_bucket": str(cert.get("bucket") or ""),
        "certified_region_json": _compact(detail),
        "ce_json": _compact(detail.get("ce") or {}),
        "cov_ce_journal": str(cov_ce.get("path") or ""),
        "cov_report": str(cov_report.get("path") or ""),
        "_cert": cert,
        "_has_region": bool(detail or enc in (cert.get("certified") or {})),
    }


@lru_cache(maxsize=None)
def _put_summaries(subject_dir: Path) -> tuple[tuple[Path, dict], ...]:
    return tuple((path, _read_json(path))
                 for path in sorted((subject_dir / "put").glob("**/put-summary.json")))


@lru_cache(maxsize=None)
def _stage4_evidence(subject_dir: Path, unit: str, enc: str, piece: str) -> dict:
    paths = []
    candidates = []
    for path, summary in _put_summaries(subject_dir):
        matched = []
        for row in (summary.get("deliverable_b") or {}).get("rows") or []:
            if not isinstance(row, dict) or str(row.get("unit") or "") != unit:
                continue
            if str(row.get("enc") if row.get("enc") is not None else "") != enc:
                continue
            row_piece = str(row.get("piece") if row.get("piece") is not None else "")
            if row_piece != piece:
                continue
            matched.append(row)
        if matched:
            paths.append(str(path))
            candidates.extend(matched)
    return {
        "put_summary_paths_json": _compact(paths),
        "historical_candidates_json": _compact(candidates),
        "_candidates": candidates,
    }


def _classify(row: dict, cert: dict, stage4: dict) -> dict:
    target = json.loads(row["current_target_json"])
    unit_info = target.get("unit_info") or {}
    obstacles = target.get("static_obstacles") or []
    candidates = stage4["_candidates"]
    has_region = cert["_has_region"]
    source = Path(row["replay_flat_source"])
    test = Path(row["replay_test_file"])
    score = min(source.stat().st_size // 10_000, 50) if source.is_file() else 50
    score += min(test.stat().st_size // 2_000, 20) if test.is_file() else 20
    score += 10 * int(unit_info.get("parameter_count") or 0)
    score += 3 * int(unit_info.get("return_count") or 0)
    score += 25 * len(obstacles)
    score += 3 * len(json.loads(row["oracle_json"]))
    if unit_info.get("state_mutability") not in ("pure", "view"):
        score += 12

    if candidates and has_region:
        feasibility = "F0-certified-candidate-retry"
        kind = "stage4-candidate-rejected"
        reason = "; ".join(
            f"forge={item.get('forge_status')}; gates={_compact(item.get('gates') or {})}; "
            f"refused={item.get('refused')}" for item in candidates)
    elif has_region:
        feasibility = "F1-certified-region-ready"
        kind = "certified-region-no-valid-put"
        reason = "exact retained certified region exists; Stage4 produced no current valid PUT"
    elif "COMPLETE-WITNESS" in row["stage2_witness_check"]:
        feasibility = "F2-complete-witness-recertify"
        kind = "complete-witness-no-region"
        reason = row["stage2_witness_check"]
    elif "no-coordinate" in row["stage2_source"]:
        feasibility = "F3-concrete-point-recertify"
        kind = "no-coordinate-concrete-fallback"
        reason = row["stage2_source"]
    elif obstacles:
        feasibility = "F4-static-obstacle"
        kind = "static-obstacle"
        reason = _compact(obstacles)
    else:
        feasibility = "F3-concrete-point-recertify"
        kind = "no-retained-region"
        reason = (cert.get("cert_bucket") or row["stage2_source"]
                  or row["stage2_witness_check"] or "no retained reason")
    complexity = ("C0-simple" if score < 25 else "C1-medium" if score < 55
                  else "C2-complex" if score < 100 else "C3-hard")
    return {
        "historical_failure_kind": kind,
        "historical_failure_reason": reason,
        "feasibility_tier": feasibility,
        "complexity_score": score,
        "complexity_tier": complexity,
    }


def rows(result_root: Path) -> list[dict]:
    frozen: dict[tuple[str, ...], dict] = {}
    for case, subject_dir in _case_dirs(result_root):
        valid_rows = _strict_valid_tests(subject_dir)
        put_keys = {_artifact_key(row) for row in valid_rows
                    if _physical_test_kind(row) == "put"}
        concrete = {_concrete_test_key(row): row for row in valid_rows
                    if _physical_test_kind(row) == "concrete"}
        manifest_path = subject_dir / "concrete-replays" / "manifest.json"
        for entry in load_manifest(subject_dir).get("entries") or []:
            if (not isinstance(entry, dict)
                    or not _entry_is_currently_not_generalized(entry, put_keys)
                    or audit_manifest(subject_dir, {"entries": [entry]})):
                continue
            matched = sorted(concrete.keys() & _entry_test_keys(entry), key=str)
            if not matched:
                continue
            canonical = concrete[matched[0]]
            key = _artifact_key(canonical)
            identity, identity_hash = _identity(case, key)
            dataset, subject = case.split("/", 1)
            project = subject_dir / str(entry.get("project") or "")
            replay_test = project / str(entry.get("test_file") or "")
            replay_flat = project / str(entry.get("flat_source") or "")
            forge_log = project / str(entry.get("forge_log") or "")
            row = {
                "identity_hash": identity_hash,
                "identity": identity,
                "case": case,
                "dataset": dataset,
                "subject": subject,
                "path_function": key[0],
                "unit": key[1],
                "enc": key[2],
                "piece": key[3],
                "canonical_row_file": str(canonical.get("file") or ""),
                "canonical_test": str(canonical.get("test") or ""),
                "canonical_test_sha256": matched[0][2],
                "replay_manifest": str(manifest_path),
                "replay_id": str(entry.get("replay_id") or ""),
                "replay_project": str(project),
                "replay_test_file": str(replay_test),
                "replay_flat_source": str(replay_flat),
                "oracle_json": _compact(entry.get("concrete_oracles") or []),
                "replay_test_sha256": str(entry.get("test_sha256") or ""),
                "replay_flat_sha256": str(entry.get("flat_sha256") or ""),
                "forge_command_json": _compact(entry.get("forge_command") or []),
                "forge_status": str(entry.get("forge_status") or ""),
                "forge_passed_tests": entry.get("forge_passed_tests"),
                "forge_log": str(forge_log),
                "forge_log_sha256": str(entry.get("forge_log_sha256") or ""),
                "forge_verified_at": entry.get("forge_verified_at"),
                "stage2_source": str((entry.get("origin") or {}).get("stage2_source") or ""),
                "stage2_witness_check": str(
                    (entry.get("origin") or {}).get("stage2_witness_check") or ""),
            }
            row.update(_target(subject_dir, key[1], key[0]))
            cert = _cert_evidence(subject_dir, key[1], key[0], key[2])
            stage4 = _stage4_evidence(subject_dir, key[1], key[2], key[3])
            row.update({name: value for name, value in cert.items()
                        if not name.startswith("_")})
            row.update({name: value for name, value in stage4.items()
                        if not name.startswith("_")})
            row.update(_classify(row, cert, stage4))
            identity_key = tuple(identity)
            old = frozen.get(identity_key)
            if old is None or row["replay_id"] < old["replay_id"]:
                frozen[identity_key] = row
    return sorted(frozen.values(), key=lambda item: tuple(item["identity"]))


def _write_tsv(path: Path, values: list[dict], fields: list[str] = FIELDS) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t",
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    values = rows(args.result_root)
    if len(values) != 545:
        raise RuntimeError(f"expected frozen population 545, got {len(values)}")
    base_values = [{name: row[name] for name in ["identity"] + FIELDS} for row in values]
    shards = {
        "bugfix124": [row for row in base_values if row["dataset"] == "bugfix124"],
        "peer182": [row for row in base_values if row["dataset"] == "peer182"],
        "real203-0-7": [row for row in base_values
                        if row["dataset"] == "real203" and row["identity_hash"][0] < "8"],
        "real203-8-f": [row for row in base_values
                        if row["dataset"] == "real203" and row["identity_hash"][0] >= "8"],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    _write_tsv(args.out / "not-generalized-545.tsv", base_values)
    _write_tsv(args.out / "not-generalized-545.evidence.tsv", values, EVIDENCE_FIELDS)
    for name, shard_rows in shards.items():
        _write_tsv(args.out / f"shard-{name}.tsv", shard_rows)
    inventory = {
        "schema": "veriput-rq1-not-generalized-ce-freeze/v1",
        "scope": "canonical-current",
        "grain": ["case", "path_function", "unit", "enc", "piece"],
        "identity_hash": "sha256(compact JSON array [case,path_function,unit,enc,piece])",
        "total": len(values),
        "shard_counts": {name: len(shard_rows) for name, shard_rows in shards.items()},
        "mutually_exclusive": sum(map(len, shards.values())) == len(values)
                              and len({row["identity_hash"] for group in shards.values()
                                       for row in group}) == len(values),
        "rows_sha256": hashlib.sha256(_compact(base_values).encode()).hexdigest(),
        "rows": base_values,
    }
    (args.out / "not-generalized-545.frozen.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence = {
        "schema": "veriput-rq1-not-generalized-ce-evidence/v1",
        "frozen_rows_sha256": inventory["rows_sha256"],
        "total": len(values),
        "feasibility_counts": {},
        "complexity_counts": {},
        "rows": values,
    }
    for name in ("feasibility_tier", "complexity_tier"):
        key = name.replace("_tier", "_counts")
        evidence[key] = {
            value: sum(row[name] == value for row in values)
            for value in sorted({row[name] for row in values})
        }
    (args.out / "not-generalized-545.evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in inventory.items() if key != "rows"},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
