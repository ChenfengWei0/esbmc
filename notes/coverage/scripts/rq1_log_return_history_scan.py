#!/usr/bin/env python3
"""Audit retained history for SHA-bound fixed-return replay evidence.

The scan is read-only except for its JSON report.  It does not execute any
test, verifier, or PUT stage, and it never edits PUT or replay artifacts.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
from collections import Counter
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _identity(candidate: dict) -> tuple[str, str, str, str, str]:
    item = candidate["identity"]
    return (str(item["case"]), str(item["unit"]),
            str(item["path_function"]), str(item["enc"]),
            str(item.get("piece") or ""))


def _manifest_case(path: Path) -> str | None:
    parts = path.parts
    try:
        index = parts.index("subjects")
    except ValueError:
        return None
    if index < 1 or index + 1 >= len(parts):
        return None
    return f"{parts[index - 1]}/{parts[index + 1]}"


def _manifest_identity(path: Path, entry: dict) -> tuple[str, str, str, str, str] | None:
    case = _manifest_case(path)
    origin = entry.get("origin") or {}
    if not case or not isinstance(origin, dict):
        return None
    return (case, str(origin.get("unit") or ""),
            str(origin.get("path_function") or ""), str(origin.get("enc")),
            str(origin.get("piece") or ""))


def _entry_paths(manifest: Path, entry: dict) -> tuple[Path, Path]:
    subject = manifest.parent.parent
    project = subject / str(entry.get("project") or "")
    return (project / str(entry.get("test_file") or ""),
            project / str(entry.get("forge_log") or ""))


def _expected(candidate: dict) -> str:
    oracle = (candidate.get("concrete_oracles") or [{}])[0]
    return str(oracle.get("expected") or "")


def _manifest_evidence(manifest: Path, entry: dict, candidate: dict) -> dict:
    test_file, forge_log = _entry_paths(manifest, entry)
    test_hash = _sha256(test_file) if test_file.is_file() else None
    log_hash = _sha256(forge_log) if forge_log.is_file() else None
    put_json = Path(str(candidate.get("put_json") or ""))
    put_hash = _sha256(put_json) if put_json.is_file() else None
    origin_put = ((entry.get("origin") or {}).get("put_json") or {})
    matching = entry.get("matching_put_artifacts") or []
    return_oracles = [oracle for oracle in entry.get("concrete_oracles") or []
                      if isinstance(oracle, dict)
                      and oracle.get("kind") == "return-value"]
    checks = {
        "candidate_source_sha_matches": (
            test_hash == candidate.get("candidate_source_sha256")),
        "manifest_source_sha_matches_file": (
            test_hash is not None and test_hash == entry.get("test_sha256")),
        "return_oracle_matches_ce": (
            len(return_oracles) == 1
            and str(return_oracles[0].get("expected") or "")
            == _expected(candidate)),
        "executed_at_least_one": int(entry.get("forge_passed_tests") or 0) >= 1,
        "forge_status_success": entry.get("forge_status") == "Success",
        "forge_log_sha_matches_file": (
            log_hash is not None and log_hash == entry.get("forge_log_sha256")),
        "canonical_put_sha_matches": (
            put_hash is not None and origin_put.get("sha256") == put_hash),
        "matching_put_artifact_sha": any(
            isinstance(item, dict) and item.get("put_json_sha256") == put_hash
            and item.get("test") for item in matching),
    }
    return {
        "manifest": str(manifest),
        "replay_test": entry.get("test"),
        "historical_source": str(test_file),
        "historical_source_sha256": test_hash,
        "forge_log": str(forge_log),
        "forge_log_sha256": log_hash,
        "checks": checks,
        "complete": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-report", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-sha-index", type=Path)
    args = parser.parse_args()

    recovery = json.loads(args.recovery_report.read_text())
    candidates = recovery.get("unverified_candidates") or []
    by_identity = {_identity(item): item for item in candidates}
    by_hash = {str(item["candidate_source_sha256"]): item for item in candidates}

    source_file_count = 0
    exact_source_hits = []
    if args.source_sha_index:
        for line in args.source_sha_index.read_text(errors="replace").splitlines():
            digest, separator, source = line.partition("  ")
            if not separator:
                continue
            source_file_count += 1
            if digest in by_hash:
                exact_source_hits.append({"sha256": digest, "path": source})
    else:
        for source in args.results_root.rglob("*.t.sol"):
            if not source.is_file():
                continue
            source_file_count += 1
            digest = _sha256(source)
            if digest in by_hash:
                exact_source_hits.append({"sha256": digest, "path": str(source)})

    manifest_count = 0
    manifest_entry_count = 0
    exact_identity_evidence = []
    manifest_pattern = str(args.results_root / "**" / "manifest.json")
    for manifest_name in glob.iglob(manifest_pattern, recursive=True):
        manifest = Path(manifest_name)
        if manifest.parent.name != "concrete-replays":
            continue
        try:
            document = json.loads(manifest.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        manifest_count += 1
        for entry in document.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            manifest_entry_count += 1
            key = _manifest_identity(manifest, entry)
            if key in by_identity:
                exact_identity_evidence.append({
                    "identity": by_identity[key]["identity"],
                    **_manifest_evidence(manifest, entry, by_identity[key]),
                })

    complete = [item for item in exact_identity_evidence if item["complete"]]
    rejection_counts = Counter()
    for item in exact_identity_evidence:
        for check, passed in item["checks"].items():
            if not passed:
                rejection_counts[check] += 1
    matched_identities = {
        _identity({"identity": item["identity"]})
        for item in exact_identity_evidence
    }

    def count_names(name: str) -> int:
        pattern = str(args.results_root / "**" / name)
        return sum(1 for _ in glob.iglob(pattern, recursive=True))

    result_jsonl_count = count_names("results.jsonl")
    put_summary_count = count_names("put-summary.json")
    forge_log_count = count_names("forge-replay.log")
    monitor_log_count = sum(
        1 for name in glob.iglob(str(args.results_root / "**" / "*.jsonl"),
                                 recursive=True)
        if "monitor" in name.lower() or "progress" in Path(name).name.lower())

    report = {
        "schema": "veriput-rq1-log-return-history-scan/v1",
        "definitions": {
            "newly_confirmed": (
                "previously unverified fixed-return replay for which retained "
                "history closes exact identity, candidate replay source SHA, "
                "matching return oracle, executed>=1 Forge log SHA, and "
                "canonical PUT JSON SHA"),
            "still_unverified": (
                "fixed-return candidate lacking at least one required exact "
                "historical binding; excluded from valid replay counts"),
        },
        "candidate_count": len(candidates),
        "history_scanned": {
            "t_sol_sources": source_file_count,
            "concrete_replay_manifests": manifest_count,
            "manifest_entries": manifest_entry_count,
            "forge_replay_logs": forge_log_count,
            "results_jsonl": result_jsonl_count,
            "put_summaries": put_summary_count,
            "monitor_or_progress_jsonl": monitor_log_count,
        },
        "candidate_source_sha_history_hit_count": len(exact_source_hits),
        "candidate_source_sha_history_hits": exact_source_hits,
        "exact_identity_manifest_entry_count": len(exact_identity_evidence),
        "exact_identity_manifest_identity_count": len(matched_identities),
        "newly_confirmed_count": len(complete),
        "newly_confirmed": complete,
        "still_unverified_count": len(candidates) - len(complete),
        "manifest_rejection_counts": dict(sorted(rejection_counts.items())),
        "exact_identity_near_misses": exact_identity_evidence,
    }
    _atomic_json(args.report, report)
    print(json.dumps({
        "candidate_count": report["candidate_count"],
        "candidate_source_sha_history_hit_count": report[
            "candidate_source_sha_history_hit_count"],
        "exact_identity_manifest_entry_count": report[
            "exact_identity_manifest_entry_count"],
        "exact_identity_manifest_identity_count": report[
            "exact_identity_manifest_identity_count"],
        "newly_confirmed_count": report["newly_confirmed_count"],
        "still_unverified_count": report["still_unverified_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
