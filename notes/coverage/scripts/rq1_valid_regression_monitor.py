#!/usr/bin/env python3
"""Continuously sample canonical valid RQ1 subjects without rewriting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rq1_artifact_audit import canonical_subject
from rq1_case_batch import (
    _detailed_test_rows as strict_detailed_test_rows,
    _is_valid_reference_test as strict_valid_reference_test,
    result_numbers as strict_result_numbers,
)
from rq1_concrete_replay_store import load_manifest

DATASETS = ("peer182", "bugfix124", "real203")
QUALITY_RANK = {
    "no-valid": 0,
    "valid-no-PUT": 1,
    "valid-PUT-no-R1R2": 2,
    "valid-PUT-with-R1R2": 3,
}
ARCHIVE_MARKERS = (".redo.", ".superseded.", ".adopted_from_")
QUALITY_CLASSES = ("put", "concrete")
STRICT_QUALITY = {
    "NO_VALID": "no-valid",
    "VALID_NO_PUT": "valid-no-PUT",
    "VALID_PUT_NO_R1R2": "valid-PUT-no-R1R2",
    "VALID_PUT_R1R2": "valid-PUT-with-R1R2",
}
INFRASTRUCTURE_PATTERNS = (
    "permissionerror",
    "errno 13",
    "permission denied",
    "binary is not executable",
    "cannot execute binary file",
    "exec format error",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def quality(doc: dict) -> str:
    strict = strict_result_numbers(doc).get("quality_bucket")
    return STRICT_QUALITY.get(str(strict), "no-valid")


def valid_artifacts(doc: dict) -> list[dict]:
    """Return retained valid artifacts across canonical and adopted schemas."""
    return [item for item in strict_detailed_test_rows(doc)
            if strict_valid_reference_test(item)]


def generation_wall(doc: dict) -> float:
    row = doc.get("row") or {}
    try:
        value = row.get("generation_wall_s")
        return float(value) if value is not None else 1e9
    except (TypeError, ValueError):
        return 1e9


def forge_root(test_file: Path) -> Path | None:
    for parent in test_file.parents:
        if (parent / "foundry.toml").is_file():
            return parent
    return None


def _concrete_candidate(doc: dict, wall_s: float) -> dict | None:
    artifacts = [item for item in valid_artifacts(doc)
                 if item.get("kind") != "put"]
    if not artifacts:
        return None
    artifacts.sort(key=lambda item: (
        item.get("unit") in (None, "__deploy__"),
        not bool(item.get("file")),
        str(item.get("unit") or ""),
        str(item.get("file") or ""),
    ))
    artifact = artifacts[0]
    unit = str(artifact.get("unit") or "__deploy__")
    stage2_source = str(artifact.get("stage2_source") or "").replace("_", "-")
    stage4_kind = str(artifact.get("stage4_kind") or "").replace("_", "-")
    # A short historical callable run exercises the current ESBMC pipeline.
    # Slow and structural cases replay their retained concrete Foundry oracle so
    # that every canonical valid-no-PUT subject remains in the bounded stream.
    source_grounded_replay = (
        stage2_source.startswith("source-")
        or stage2_source == "structural-getter-only"
        or stage4_kind.endswith("revert-only")
        or stage4_kind == "getter-only"
    )
    if unit != "__deploy__" and wall_s <= 90 and not source_grounded_replay:
        return {"unit": unit, "validation_mode": "runner"}
    test_file = Path(str(artifact.get("file") or ""))
    root = forge_root(test_file) if test_file.is_file() else None
    return {
        "unit": unit,
        "validation_mode": "concrete-replay",
        "replay_file": str(test_file),
        "replay_test": str(artifact.get("test") or ""),
        "forge_root": str(root or ""),
    }


def _put_candidate(doc: dict, wall_s: float) -> dict | None:
    artifacts = [item for item in valid_artifacts(doc) if item.get("kind") == "put"]
    if not artifacts:
        return None
    artifacts.sort(key=lambda item: (str(item.get("unit") or ""),
                                     str(item.get("file") or "")))
    artifact = artifacts[0]
    unit = str(artifact.get("unit") or "")
    row = doc.get("row") or {}
    if (unit and unit != "__deploy__" and row.get("completion_status") == "ok"
            and wall_s <= 90):
        return {"unit": unit, "validation_mode": "runner"}
    test_file = Path(str(artifact.get("file") or ""))
    root = forge_root(test_file) if test_file.is_file() else None
    return {
        "unit": unit,
        "validation_mode": "put-replay",
        "replay_file": str(test_file),
        "replay_test": str(artifact.get("test") or ""),
        "forge_root": str(root or ""),
    }


def canonical_candidates(canonical_root: Path,
                         case_state: Path | None = None) -> dict[str, list[dict]]:
    allowed = None
    if case_state is not None:
        state = load_json(case_state)
        allowed = set((state.get("cases") or {}).keys())
    result = {}
    for dataset in DATASETS:
        subject_root = canonical_root / dataset / "subjects"
        rows = {quality_class: [] for quality_class in QUALITY_CLASSES}
        for path in subject_root.glob("*/result.json"):
            if path.parent.parent != subject_root:
                continue
            if any(marker in path.parent.name for marker in ARCHIVE_MARKERS):
                continue
            if allowed is not None and f"{dataset}/{path.parent.name}" not in allowed:
                continue
            doc = load_json(path)
            row = doc.get("row") or {}
            old_quality = quality(doc)
            wall_s = generation_wall(doc)
            quality_rank = QUALITY_RANK.get(old_quality, 0)
            if quality_rank < 1:
                continue
            detail = None
            quality_class = "concrete"
            if quality_rank >= 2:
                detail = _put_candidate(doc, wall_s)
                if detail is None:
                    continue
                quality_class = "put"
            else:
                detail = _concrete_candidate(doc, wall_s)
                if detail is None:
                    continue
            rows[quality_class].append({
                "dataset": dataset,
                "subject_id": path.parent.name,
                "old_quality": old_quality,
                "old_result": str(path),
                "historical_generation_wall_s": wall_s,
                "quality_class": quality_class,
                "pool_key": f"{dataset}:{quality_class}",
                **detail,
            })
        for quality_class in QUALITY_CLASSES:
            pool = rows[quality_class]
            pool.sort(key=lambda item: (item["historical_generation_wall_s"],
                                        item["subject_id"]))
            result[f"{dataset}:{quality_class}"] = pool
    return result


def _single_option(command: list[str], option: str) -> str | None:
    positions = [index for index, value in enumerate(command) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        return None
    value = command[positions[0] + 1]
    return value if value and not value.startswith("--") else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_replay(subject_dir: Path, entry: dict) -> dict | None:
    project_value = str(entry.get("project") or "")
    test_value = str(entry.get("test_file") or "")
    command = entry.get("forge_command")
    if (not project_value or not test_value or Path(project_value).is_absolute()
            or Path(test_value).is_absolute() or not isinstance(command, list)
            or not all(isinstance(value, str) for value in command)):
        return None
    project = (subject_dir / project_value).resolve()
    test_file = (project / test_value).resolve()
    try:
        project.relative_to((subject_dir / "concrete-replays").resolve())
        test_file.relative_to(project)
    except ValueError:
        return None
    flat_value = str(entry.get("flat_source") or "")
    flat_file = (project / flat_value).resolve()
    try:
        flat_file.relative_to(project)
    except ValueError:
        return None
    if (not (project / "foundry.toml").is_file() or not test_file.is_file()
            or not flat_value or not flat_file.is_file()
            or not (project / "lib" / "forge-std" / "src" / "Test.sol").is_file()
            or entry.get("test_sha256") != _sha256(test_file)
            or entry.get("flat_sha256") != _sha256(flat_file)):
        return None
    if len(command) < 2 or Path(command[0]).name != "forge" or command[1] != "test":
        return None
    match_path = _single_option(command, "--match-path")
    match_test = _single_option(command, "--match-test")
    test_name = str(entry.get("test") or "")
    expected_match_test = f"^{re.escape(test_name)}\\("
    if (match_path != test_value or not test_name
            or match_test != expected_match_test):
        return None
    if len(command) != 6 or set(command[2::2]) != {"--match-path", "--match-test"}:
        return None
    return {
        "unit": str((entry.get("origin") or {}).get("unit") or "__deploy__"),
        "validation_mode": "manifest-replay",
        "replay_id": str(entry.get("replay_id") or ""),
        "replay_file": str(test_file),
        "replay_test": test_name,
        "forge_root": str(project),
        "forge_command": command,
    }


def manifest_replay_candidates(canonical_root: Path) -> dict[str, list[dict]]:
    """Discover only self-contained exact replays from canonical subjects."""
    result = {f"{dataset}:{quality_class}": []
              for dataset in DATASETS for quality_class in QUALITY_CLASSES}
    for dataset in DATASETS:
        subject_root = canonical_root / dataset / "subjects"
        for result_path in sorted(subject_root.glob("*/result.json")):
            subject_id, historical = canonical_subject(result_path.parent.name)
            if historical or subject_id != result_path.parent.name:
                continue
            subject_dir = result_path.parent
            old_quality = quality(load_json(result_path))
            quality_rank = QUALITY_RANK.get(old_quality, 0)
            if quality_rank < 1:
                continue
            quality_class = "put" if quality_rank >= 2 else "concrete"
            manifest = load_manifest(subject_dir)
            for entry in manifest.get("entries") or []:
                if (not isinstance(entry, dict)
                        or entry.get("valid_reference_test") is not True
                        or entry.get("forge_status") != "Success"):
                    continue
                detail = _manifest_replay(subject_dir, entry)
                if detail is None:
                    continue
                result[f"{dataset}:{quality_class}"].append({
                    "dataset": dataset,
                    "subject_id": subject_id,
                    "old_quality": old_quality,
                    "old_result": str(result_path),
                    "historical_generation_wall_s": generation_wall(
                        load_json(result_path)),
                    "quality_class": quality_class,
                    "pool_key": f"{dataset}:{quality_class}",
                    **detail,
                })
    return result


def pool_lanes(pools: dict[str, list[dict]]) -> list[str]:
    return [f"{dataset}:{quality_class}" for dataset in DATASETS
            for quality_class in QUALITY_CLASSES
            if pools.get(f"{dataset}:{quality_class}")]


def current_replay_retry(retry_sample: dict | None,
                         pools: dict[str, list[dict]]) -> dict | None:
    if retry_sample is None:
        return None
    retry_key = (str(retry_sample.get("dataset") or ""),
                 str(retry_sample.get("subject_id") or ""),
                 str(retry_sample.get("replay_id") or ""))
    current_replays = {
        (item["dataset"], item["subject_id"], item["replay_id"]): item
        for pool in pools.values() for item in pool
    }
    current = current_replays.get(retry_key)
    if current is None:
        return None
    rebound = dict(current)
    rebound["retry_of_sequence"] = retry_sample.get("retry_of_sequence")
    return rebound


def history_pool_key(row: dict) -> str:
    quality_class = "concrete" if row.get("old_quality") == "valid-no-PUT" else "put"
    return str(row.get("pool_key") or f"{row.get('dataset')}:{quality_class}")


def build_runner_command(args, sample: dict, run_root: Path) -> list[str]:
    return [
        sys.executable, str(args.runner),
        "--veriput-root", "/home/samson/workspace/VeriPUT",
        "--benchmark", sample["dataset"],
        "--subject-id", sample["subject_id"],
        "--unit", sample["unit"],
        "--result-root", str(run_root),
        "--timeout", "75",
        "--esbmc-run-timeout", "60",
        "--stage2-unit-timeout-cap-s", "60",
        "--stage2-stage4-reserve-s", "40",
        "--wrapper-grace", "5",
        "--min-remaining-s", "5",
        # Ten seconds is below the observed cold Solc/Forge startup time for
        # retained projects.  The monitor's case_timeout remains the hard cap.
        "--forge-timeout", "20",
        "--memlimit-gib", str(args.memlimit_gib),
        "--jobs", "1",
        "--esbmc", str(args.esbmc),
        "--redo",
    ]


def build_replay_command(args, sample: dict, run_root: Path) -> list[str]:
    root = Path(sample["forge_root"])
    test_file = Path(sample["replay_file"])
    relative = test_file.relative_to(root)
    retained = sample.get("forge_command")
    if retained is not None:
        retained = list(retained)
        command = [str(args.forge), *retained[1:], "--root", str(root)]
    else:
        command = [str(args.forge), "test", "--root", str(root),
                   "--match-path", str(relative)]
    return [*command, "--fuzz-runs", "1", "--no-cache",
            "--out", str(run_root / "forge-out"),
            "--cache-path", str(run_root / "forge-cache")]


def forge_replay_passed_count(log_path: Path) -> int:
    try:
        output = log_path.read_text(errors="replace")
    except OSError:
        return 0
    if "No tests found" in output:
        return 0
    return max((int(count) for count in re.findall(r"\b(\d+) passed\b", output)),
               default=0)


def forge_replay_succeeded(returncode: int, timed_out: bool, log_path: Path) -> bool:
    return (returncode == 0 and not timed_out
            and forge_replay_passed_count(log_path) >= 1)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_history(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    os.replace(tmp, path)


def binary_identity(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "path": str(path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "inode": stat.st_ino,
        "executable": path.is_file() and os.access(path, os.X_OK),
    }


def binary_changed(before: dict | None, after: dict | None) -> bool:
    if before is None or after is None:
        return before != after
    return any(before.get(key) != after.get(key)
               for key in ("mtime_ns", "size", "inode"))


def wait_for_stable_binary(path: Path, stable_s: float, poll_s: float,
                           heartbeat=None) -> dict:
    stable_since = None
    previous = None
    while True:
        current = binary_identity(path)
        now = time.monotonic()
        usable = current is not None and current["executable"]
        if not usable or binary_changed(previous, current):
            stable_since = now if usable else None
        elif stable_since is None:
            stable_since = now
        if heartbeat is not None:
            heartbeat(current, 0 if stable_since is None else now - stable_since)
        if usable and stable_since is not None and now - stable_since >= stable_s:
            return current
        previous = current
        time.sleep(poll_s)


def _evidence_files(result_path: Path, log_path: Path) -> list[Path]:
    paths = [log_path]
    case_dir = result_path.parent
    if case_dir.is_dir():
        paths.extend(case_dir.glob("cert/**/*.json"))
        paths.extend(case_dir.glob("cert/**/*.jsonl"))
        paths.extend(case_dir.glob("cert/**/*.log"))
        paths.extend(case_dir.glob("logs/*.log"))
    return paths


def infrastructure_evidence(result_path: Path, log_path: Path) -> list[str]:
    matches = []
    for path in _evidence_files(result_path, log_path):
        try:
            # Failure evidence is near the tail; bound reads so monitoring itself stays cheap.
            data = path.read_bytes()
        except OSError:
            continue
        text = data[-4 * 1024 * 1024:].decode(errors="replace").lower()
        for pattern in INFRASTRUCTURE_PATTERNS:
            if pattern in text:
                matches.append(f"{pattern}: {path}")
    return sorted(set(matches))


def classify_infrastructure(result_path: Path, log_path: Path,
                            before: dict | None = None,
                            after: dict | None = None,
                            check_binary: bool = True) -> tuple[bool, list[str]]:
    reasons = infrastructure_evidence(result_path, log_path)
    if check_binary and binary_changed(before, after):
        reasons.append("esbmc binary identity changed during sample window")
    if check_binary and (after is None or not after.get("executable", False)):
        reasons.append("esbmc binary is not executable after sample")
    return bool(reasons), sorted(set(reasons))


def reconcile_history(rows: list[dict], alerts: Path,
                      infrastructure: Path,
                      eligible_subjects: set[tuple[str, str]] | None = None,
                      resolutions: list[dict] | None = None,
                      ) -> tuple[list[dict], dict | None]:
    resolution_by_sequence = {
        int(row["resolves_sequence"]): row for row in (resolutions or [])
        if row.get("resolves_sequence") is not None and row.get("valid_now") is True
    }
    corrected = []
    for original in rows:
        row = dict(original)
        result_path = Path(str(row.get("run_result") or ""))
        log_path = Path(str(row.get("log") or ""))
        infra = bool(row.get("infrastructure_error"))
        reasons = list(row.get("infrastructure_reasons") or [])
        if not infra and result_path and log_path:
            evidence = infrastructure_evidence(result_path, log_path)
            if evidence:
                infra = True
                reasons = evidence
        replay_mode = row.get("validation_mode") in (
            "put-replay", "concrete-replay", "manifest-replay")
        replay_tests_passed = forge_replay_passed_count(log_path) if replay_mode else None
        if replay_mode:
            row["replay_tests_passed"] = replay_tests_passed
        replay_invalid = (replay_mode and not forge_replay_succeeded(
            int(row.get("returncode") or 0), bool(row.get("timed_out")), log_path))
        identity = (str(row.get("dataset") or ""), str(row.get("subject_id") or ""))
        ledger_contamination = (eligible_subjects is not None
                                and identity not in eligible_subjects)
        resolution = resolution_by_sequence.get(int(row.get("sequence") or 0))
        if ledger_contamination:
            row.update({
                "classification": "ledger-contamination",
                "ledger_contamination": True,
                "contamination_reason": "subject is not strict-valid in canonical RQ1 scope",
                "quality_regressed": False,
                "regressed": False,
            })
        elif resolution is not None:
            classification = str(resolution.get("classification") or "resolved-regression")
            row.update({
                "classification": classification,
                "resolution": resolution,
                "runner_valid_now": row.get("valid_now"),
                "valid_now": True,
                "generation_new_quality": row.get("new_quality"),
                "new_quality": row.get("old_quality"),
                "runner_generation_regression":
                classification == "runner-generation-regression",
                "quality_regressed": False,
                "regressed": False,
            })
        elif infra:
            row.update({
                "classification": "infrastructure-error",
                "infrastructure_error": True,
                "infrastructure_reasons": sorted(set(reasons)),
                "quality_regressed": False,
                "regressed": False,
            })
        elif replay_invalid:
            row.update({
                "classification": "regression",
                "valid_now": False,
                "new_quality": "no-valid",
                "quality_regressed": True,
                "regressed": True,
                "rerun_failure_reason":
                "retained Forge replay did not execute at least one passing test",
            })
        elif row.get("regressed"):
            row["classification"] = "regression"
        else:
            row["classification"] = "pass"
        corrected.append(row)
    write_jsonl(alerts, [row for row in corrected if row.get("regressed")])
    write_jsonl(infrastructure,
                [row for row in corrected if row.get("infrastructure_error")])
    pending = None
    retried = {int(row.get("retry_of_sequence")) for row in corrected
               if row.get("retry_of_sequence") is not None}
    for row in corrected:
        sequence = int(row.get("sequence") or 0)
        if (row.get("classification") == "infrastructure-error"
                and sequence not in retried):
            pending = {
                key: row[key]
                for key in ("dataset", "subject_id", "unit", "old_quality", "old_result",
                            "historical_generation_wall_s", "quality_class", "pool_key",
                            "validation_mode", "replay_file", "replay_test", "forge_root",
                            "forge_command", "replay_id")
                if key in row
            }
            pending["retry_of_sequence"] = sequence
            break
    return corrected, pending


def terminate_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--case-state", type=Path, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--esbmc", type=Path)
    parser.add_argument("--forge", type=Path, default=shutil.which("forge"))
    parser.add_argument("--case-timeout", type=int, default=120)
    parser.add_argument("--sleep", type=int, default=5)
    parser.add_argument("--memlimit-gib", type=int, default=6)
    parser.add_argument("--binary-stable-s", type=float, default=10.0)
    parser.add_argument("--candidate-refresh-s", type=float, default=30.0)
    parser.add_argument("--replay-only", action="store_true",
                        help="sample exact canonical manifest replays only")
    args = parser.parse_args()
    if not args.replay_only and (args.runner is None or args.esbmc is None):
        parser.error("--runner and --esbmc are required outside --replay-only")

    args.state_root.mkdir(parents=True, exist_ok=True)
    args.run_root.mkdir(parents=True, exist_ok=True)
    journal = args.state_root / "samples.jsonl"
    alerts = args.state_root / "regressions.jsonl"
    infrastructure = args.state_root / "infrastructure.jsonl"
    contamination = args.state_root / "ledger-contamination.jsonl"
    runner_generation = args.state_root / "runner-generation-regressions.jsonl"
    resolutions = args.state_root / "resolutions.jsonl"
    state_path = args.state_root / "state.json"
    pools = (manifest_replay_candidates(args.canonical_root) if args.replay_only
             else canonical_candidates(args.canonical_root, args.case_state))
    lanes = pool_lanes(pools)
    if not lanes:
        raise SystemExit("no canonical valid regression candidates")
    if args.forge is None:
        raise SystemExit("forge is required for concrete replay samples")
    eligible_subjects = {
        (item["dataset"], item["subject_id"])
        for pool in pools.values() for item in pool
    }
    history, retry_sample = reconcile_history(
        load_history(journal), alerts, infrastructure, eligible_subjects,
        load_history(resolutions))
    if args.replay_only and retry_sample is not None:
        retry_sample = current_replay_retry(retry_sample, pools)
    write_jsonl(journal, history)
    write_jsonl(contamination,
                [row for row in history if row.get("ledger_contamination")])
    write_jsonl(runner_generation,
                [row for row in history if row.get("runner_generation_regression")])
    indexes = {
        lane: sum(1 for row in history
                  if history_pool_key(row) == lane
                  and row.get("retry_of_sequence") is None)
        for lane in lanes
    }
    lane_index = sum(1 for row in history if row.get("retry_of_sequence") is None)
    sequence = max((int(row.get("sequence") or 0) for row in history), default=0)
    candidates_refreshed_at = time.monotonic()

    def refresh_candidates() -> None:
        nonlocal pools, lanes, candidates_refreshed_at
        if time.monotonic() - candidates_refreshed_at < args.candidate_refresh_s:
            return
        refreshed = (manifest_replay_candidates(args.canonical_root)
                     if args.replay_only else
                     canonical_candidates(args.canonical_root, args.case_state))
        refreshed_lanes = pool_lanes(refreshed)
        if refreshed_lanes:
            pools = refreshed
            lanes = refreshed_lanes
            for lane in lanes:
                indexes.setdefault(lane, 0)
        candidates_refreshed_at = time.monotonic()

    def waiting_heartbeat(identity: dict | None, stable_for_s: float) -> None:
        state = {
            "schema": "veriput-rq1-valid-regression-monitor/v1",
            "monitor_pid": os.getpid(),
            "updated_at": utc_now(),
            "phase": "waiting-for-stable-esbmc",
            "binary_stable_for_s": round(stable_for_s, 3),
            "binary_stable_required_s": args.binary_stable_s,
            "esbmc_binary": identity,
            "pending_retry": retry_sample,
            "candidate_counts": {key: len(value) for key, value in pools.items()},
            "candidate_total": sum(len(value) for value in pools.values()),
            "samples_completed": sequence,
            "journal": str(journal),
            "alerts": str(alerts),
            "infrastructure": str(infrastructure),
            "ledger_contamination": str(contamination),
            "runner_generation_regressions": str(runner_generation),
        }
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    while True:
        if retry_sample is not None:
            sample = dict(retry_sample)
        else:
            refresh_candidates()
            lane = lanes[lane_index % len(lanes)]
            lane_index += 1
            pool = pools[lane]
            position = indexes[lane] % len(pool)
            indexes[lane] += 1
            sample = dict(pool[position])
        dataset = sample["dataset"]
        runner_mode = sample.get("validation_mode", "runner") == "runner"
        if args.replay_only and runner_mode:
            raise RuntimeError("replay-only monitor selected a runner candidate")
        binary_before = (wait_for_stable_binary(args.esbmc, args.binary_stable_s, 1.0,
                                                waiting_heartbeat)
                         if runner_mode else None)
        sequence += 1
        run_id = f"{sequence:06d}-{dataset}-{sample['subject_id']}"
        run_root = args.run_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        log_path = args.state_root / "logs" / f"{run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            command = (build_runner_command(args, sample, run_root) if runner_mode else
                       build_replay_command(args, sample, run_root))
            command_error = None
        except (OSError, ValueError) as exc:
            command = []
            command_error = str(exc)
        started_at = utc_now()
        started = time.monotonic()
        timed_out = False
        with log_path.open("w") as log:
            log.write("command: " + " ".join(command) + "\n")
            log.flush()
            if command_error is not None:
                log.write(f"candidate error: {command_error}\n")
                proc = None
                returncode = 2
            else:
                proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                try:
                    returncode = proc.wait(timeout=args.case_timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    terminate_group(proc)
                    returncode = proc.returncode
                except KeyboardInterrupt:
                    terminate_group(proc)
                    raise
        wall_s = round(time.monotonic() - started, 3)
        result_path = (run_root / dataset / "subjects" / sample["subject_id"] / "result.json"
                       if runner_mode else run_root / "concrete-replay-result.json")
        if runner_mode:
            rerun_doc = load_json(result_path)
        else:
            replay_tests_passed = forge_replay_passed_count(log_path)
            replay_passed = (returncode == 0 and not timed_out
                             and replay_tests_passed >= 1)
            oracle_classes = (["R1"] if sample["old_quality"] ==
                              "valid-PUT-with-R1R2" else [])
            rerun_doc = {
                "row": {
                    "completion_status": "ok" if replay_passed else "replay-failed",
                    "failure_reason": (None if replay_passed else
                                       (command_error or "Foundry replay had no passing test")),
                    "raw_artifacts": [{
                        "kind": sample["quality_class"],
                        "unit": sample["unit"],
                        "stage2_source": "retained-concrete-replay",
                        "stage4_kind": "retained-concrete-replay",
                        "valid_reference_test": replay_passed,
                        "oracle_classes": oracle_classes,
                    }],
                },
            }
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(rerun_doc, indent=2, sort_keys=True) + "\n")
        rerun_row = rerun_doc.get("row") or {}
        binary_after = binary_identity(args.esbmc) if runner_mode else None
        infrastructure_error, infrastructure_reasons = classify_infrastructure(
            result_path, log_path, binary_before, binary_after,
            check_binary=runner_mode)
        new_quality = quality(rerun_doc) if rerun_doc else "no-result"
        valid_now = QUALITY_RANK.get(new_quality, 0) >= 1
        quality_regressed = (QUALITY_RANK.get(new_quality, 0) <
                             QUALITY_RANK.get(sample["old_quality"], 0))
        regressed = quality_regressed and not infrastructure_error
        record = {
            **sample,
            "schema": "veriput-rq1-valid-regression-sample/v1",
            "sequence": sequence,
            "started_at": started_at,
            "finished_at": utc_now(),
            "command": command,
            "pid": proc.pid if proc is not None else None,
            "returncode": returncode,
            "timed_out": timed_out,
            "wall_s": wall_s,
            "replay_tests_passed": replay_tests_passed if not runner_mode else None,
            "new_quality": new_quality,
            "rerun_completion_status": rerun_row.get("completion_status"),
            "rerun_failure_reason": rerun_row.get("failure_reason"),
            "valid_now": valid_now,
            "quality_regressed": quality_regressed,
            "regressed": regressed,
            "classification": ("infrastructure-error" if infrastructure_error else
                               ("regression" if regressed else "pass")),
            "infrastructure_error": infrastructure_error,
            "infrastructure_reasons": infrastructure_reasons,
            "esbmc_binary_before": binary_before,
            "esbmc_binary_after": binary_after,
            "run_result": str(result_path),
            "log": str(log_path),
        }
        append_jsonl(journal, record)
        if regressed:
            append_jsonl(alerts, record)
        if infrastructure_error:
            append_jsonl(infrastructure, record)
            retry_sample = dict(sample)
            retry_sample["retry_of_sequence"] = sequence
        else:
            retry_sample = None
        state = {
            "schema": "veriput-rq1-valid-regression-monitor/v1",
            "monitor_pid": os.getpid(),
            "updated_at": utc_now(),
            "case_timeout_s": args.case_timeout,
            "phase": "sleeping",
            "candidate_counts": {key: len(value) for key, value in pools.items()},
            "candidate_total": sum(len(value) for value in pools.values()),
            "samples_completed": sequence,
            "last_sample": record,
            "pending_retry": retry_sample,
            "journal": str(journal),
            "alerts": str(alerts),
            "infrastructure": str(infrastructure),
            "ledger_contamination": str(contamination),
            "runner_generation_regressions": str(runner_generation),
        }
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        time.sleep(args.sleep)


if __name__ == "__main__":
    raise SystemExit(main())
