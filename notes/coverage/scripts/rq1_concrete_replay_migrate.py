#!/usr/bin/env python3
"""Audit or migrate canonical concrete replay projects for all RQ1 cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

from rq1_concrete_replay_store import (  # noqa: E402
    DEFAULT_INVALIDATION_LEDGER, ReplayPersistenceError, annotate_generalization,
    audit_manifest,
    invalidated_cases, invalidation_applies, load_manifest,
    persist_concrete_replay, persistence_coverage, replay_identity,
    repair_manifest_independence,
)
from rq1_case_batch import (  # noqa: E402
    _detailed_test_rows as strict_detailed_test_rows,
    _is_valid_reference_test as strict_valid_reference_test,
    result_numbers as strict_result_numbers,
)
from rq1_artifact_audit import canonical_subject  # noqa: E402
from rq1_veriput_run import (  # noqa: E402
    _forge_json_has_successful_test, _is_valid_reference_test,
    summarize_put_artifacts,
)
from solidity_path_put import (  # noqa: E402
    _source_param_decl_type, _source_sol_param_name,
    _source_type_default_expr, _split_top_level_commas,
)


DEFAULT_RESULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
PUT_ALL = HERE / "put_all.py"


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as stream:
        json.dump(doc, stream, indent=2, sort_keys=True)
        stream.write("\n")
        tmp = Path(stream.name)
    os.replace(tmp, path)


def _case_dirs(result_root: Path) -> list[tuple[str, Path]]:
    """Discover canonical RQ1 subjects from the result tree itself."""
    rows = []
    for result_path in sorted(result_root.glob("*/subjects/*/result.json")):
        subject_dir = result_path.parent
        bench = subject_dir.parent.parent.name
        subject = subject_dir.name
        canonical, historical = canonical_subject(subject)
        if historical or canonical != subject:
            continue
        rows.append((f"{bench}/{subject}", subject_dir))
    return rows


def _strict_valid_tests(subject_dir: Path) -> list[dict]:
    result = _read_json(subject_dir / "result.json")
    if strict_result_numbers(result).get("quality_bucket") == "NO_VALID":
        return []
    rows = [row for row in strict_detailed_test_rows(result)
            if isinstance(row, dict) and strict_valid_reference_test(row)]
    deduped = {}
    for row in rows:
        key = (str(row.get("file") or ""), str(row.get("test") or ""),
               str(row.get("kind") or ""), str(row.get("unit") or ""))
        deduped[key] = row
    return list(deduped.values())


def _cert_benchmark(cert: Path, unit: str, path_function: str | None) -> str | None:
    try:
        lines = cert.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("unit") != unit:
            continue
        if path_function and row.get("path_function") != path_function:
            continue
        if row.get("benchmark"):
            return str(row["benchmark"])
    return None


def _generate_basis(subject_dir: Path, missing: dict, args) -> tuple[list[dict], dict]:
    cert = subject_dir / "cert" / "certify-results.jsonl"
    unit = str(missing.get("unit") or "")
    path_function = missing.get("path_function")
    benchmark = _cert_benchmark(cert, unit, path_function)
    if not cert.is_file() or not benchmark or not unit:
        return [], {"status": "unrecoverable", "reason": "retained certified row missing"}
    selector = f"{benchmark}.{path_function or unit}"
    generation_root = subject_dir / "concrete-replays" / ".generation"
    generation_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="basis-", dir=generation_root))
    output = run_root / "out"
    command = [
        sys.executable, str(PUT_ALL), "--cert", str(cert), "--only", selector,
        "--certified-concrete-only", "--timeout", str(args.timeout),
        "--forge-timeout", str(args.forge_timeout), "--memlimit-gib",
        str(args.memlimit_gib), "--out-root", str(output), "--esbmc", str(args.esbmc),
    ]
    original_put_json = Path(str(missing.get("put_json") or ""))
    original_emit = original_put_json.parent / "emit"
    if (original_emit / "cov-report.json").is_file():
        command += ["--reuse-emitted-dir", str(original_emit)]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   timeout=args.timeout + 2 * args.forge_timeout + 30)
    except subprocess.TimeoutExpired as exc:
        return [], {
            "status": "timeout",
            "wall_s": round(time.monotonic() - started, 3),
            "selector": selector,
            "command": command,
            "valid_concrete": 0,
            "log_tail": str(exc.stdout or "")[-4000:],
            "generation_root": str(run_root),
        }
    summary = summarize_put_artifacts(output)
    concrete = [row for row in summary.get("valid_tests") or []
                if row.get("kind") == "concrete" and _is_valid_reference_test(row)]
    status = {
        "status": "ok" if concrete else "failed",
        "returncode": completed.returncode,
        "wall_s": round(time.monotonic() - started, 3),
        "selector": selector,
        "command": command,
        "valid_concrete": len(concrete),
        "log_tail": completed.stdout[-4000:],
        "generation_root": str(run_root),
    }
    return concrete, status


def _function_parameter_span(source: str, name: str) -> tuple[int, int, int] | None:
    """Return the parameter and body-open offsets for one named function."""
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", source)
    if not match:
        return None
    start = match.end()
    depth = 1
    index = start
    while index < len(source) and depth:
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
        index += 1
    if depth:
        return None
    body = source.find("{", index)
    return (start, index - 1, body) if body >= 0 else None


def _specialize_put_basis(subject_dir: Path, put_row: dict, args) -> tuple[dict | None, dict]:
    """Refuse default-value specialization: it is not a witness replay."""
    return None, {
        "status": "unrecoverable",
        "strategy": "witness-required",
        "reason": ("a PUT cannot be converted to a concrete replay by choosing "
                   "default arguments; recover its exact Stage-2 witness instead"),
        "identity": replay_identity(put_row),
    }


def _unsafe_specialize_put_basis(subject_dir: Path, put_row: dict, args) -> tuple[dict | None, dict]:
    """Legacy implementation retained temporarily for forensic comparison only."""
    source_test = Path(str(put_row.get("file") or ""))
    test_name = str(put_row.get("test") or "")
    try:
        source = source_test.read_text(errors="replace")
    except OSError as exc:
        return None, {"status": "unrecoverable", "reason": str(exc)}
    span = _function_parameter_span(source, test_name)
    if span is None:
        return None, {"status": "unrecoverable", "reason": "PUT function is absent"}
    param_start, param_end, body_open = span
    flat_source = source_test.parent.parent / "src" / "flat.sol"
    try:
        flat_text = flat_source.read_text(errors="replace")
    except OSError as exc:
        return None, {"status": "unrecoverable", "reason": str(exc)}
    declarations = []
    for index, raw in enumerate(_split_top_level_commas(source[param_start:param_end])):
        typ = _source_param_decl_type(raw)
        name = _source_sol_param_name(raw)
        value = _source_type_default_expr(typ or "", 7000 + index, flat_text)
        if not typ or not name or value is None:
            return None, {"status": "unrecoverable",
                          "reason": f"cannot specialize PUT parameter {raw!r}"}
        local_type = re.sub(r"\bcalldata\b", "memory", raw).strip()
        declarations.append(f"    {local_type} = {value};")
    suffix = hashlib.sha256((test_name + json.dumps(replay_identity(put_row),
                                                    sort_keys=True)).encode()).hexdigest()[:12]
    replay_test = f"test_replay_basis_{suffix}"
    rewritten = source[:param_start] + source[param_end:]
    name_match = re.search(r"\bfunction\s+" + re.escape(test_name), rewritten)
    if name_match is None:
        return None, {"status": "unrecoverable", "reason": "PUT name rewrite failed"}
    rewritten = (rewritten[:name_match.start()] + "function " + replay_test +
                 rewritten[name_match.end():])
    body_open = rewritten.find("{", name_match.start())
    rewritten = (rewritten[:body_open + 1] + "\n" + "\n".join(declarations) +
                 rewritten[body_open + 1:])

    generation_root = subject_dir / "concrete-replays" / ".generation"
    generation_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="specialized-", dir=generation_root))
    project = run_root / "project"
    (project / "src").mkdir(parents=True)
    (project / "test").mkdir()
    shutil.copy2(source_test.parent.parent / "foundry.toml", project / "foundry.toml")
    shutil.copy2(flat_source, project / "src" / "flat.sol")
    forge_std = (source_test.parent.parent / "lib" / "forge-std").resolve()
    (project / "lib").mkdir()
    os.symlink(forge_std, project / "lib" / "forge-std")
    replay_file = project / "test" / f"{replay_test}.t.sol"
    replay_file.write_text(rewritten)
    command = ["forge", "test", "--json", "--match-test", f"^{replay_test}\\(",
               "--match-path", f"test/{replay_file.name}"]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=project, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=args.forge_timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return None, {
            "status": "timeout",
            "strategy": "specialized-valid-put",
            "command": command,
            "wall_s": round(time.monotonic() - started, 3),
            "log_tail": (str(exc.stdout or "") + str(exc.stderr or ""))[-4000:],
            "generation_root": str(run_root),
        }
    try:
        forge_json = json.loads(completed.stdout)
    except json.JSONDecodeError:
        forge_json = {}
    green = completed.returncode == 0 and _forge_json_has_successful_test(
        forge_json, replay_test, f"test/{replay_file.name}")
    status = {
        "status": "ok" if green else "failed",
        "strategy": "specialized-valid-put",
        "command": command,
        "returncode": completed.returncode,
        "wall_s": round(time.monotonic() - started, 3),
        "log_tail": (completed.stdout + completed.stderr)[-4000:],
        "generation_root": str(run_root),
    }
    if not green:
        return None, status
    return {
        **put_row,
        "kind": "concrete",
        "file": str(replay_file),
        "test": replay_test,
        "forge_status": "Success",
        "valid_reference_test": True,
        "stage2_source": "certified-put-specialization",
        "stage2_witness_check": "CERTIFIED-BASIS-REPLAY",
        "oracle_classes": [],
        "b": False,
    }, status


def _annotate_result(subject_dir: Path, coverage: dict) -> None:
    result_path = subject_dir / "result.json"
    doc = _read_json(result_path)
    if not doc:
        return
    persistence = {
        **coverage,
        "manifest": str(subject_dir / "concrete-replays" / "manifest.json"),
        "updated_at": time.time(),
    }
    doc["concrete_replay_persistence"] = persistence
    if isinstance(doc.get("row"), dict):
        doc["row"]["concrete_replay_persistence"] = persistence
    _atomic_json(result_path, doc)


def migrate_case(case: str, subject_dir: Path, args) -> dict:
    valid_tests = _strict_valid_tests(subject_dir)
    if invalidation_applies(case, valid_tests, args.invalidation_ledger):
        store = subject_dir / "concrete-replays"
        quarantine = None
        if args.apply and store.exists():
            quarantine = store.with_name(
                f"concrete-replays.invalidated.{int(time.time())}.{os.getpid()}")
            os.replace(store, quarantine)
        return {
            "case": case,
            "subject_dir": str(subject_dir),
            "strict_valid_count": 0,
            "status": "invalidated-evidence",
            "invalidation_ledger": str(args.invalidation_ledger),
            "quarantined_store": str(quarantine) if quarantine else None,
            "actions": [],
            "generation": [],
            "coverage": persistence_coverage([], []),
        }
    manifest = load_manifest(subject_dir)
    if args.apply:
        repair_manifest_independence(subject_dir, manifest)
    planned_entries = list(manifest.get("entries") or [])
    report = {
        "case": case,
        "subject_dir": str(subject_dir),
        "strict_valid_count": len(valid_tests),
        "actions": [],
        "generation": [],
        "manifest_errors_before": audit_manifest(subject_dir, manifest),
    }
    if not valid_tests:
        report["status"] = "not-strict-valid"
        report["coverage"] = persistence_coverage([], manifest.get("entries") or [])
        return report

    for row in valid_tests:
        if row.get("kind") != "concrete":
            continue
        try:
            entry = persist_concrete_replay(subject_dir, row, dry_run=not args.apply)
        except ReplayPersistenceError as exc:
            report["actions"].append({"action": "refused", "reason": str(exc),
                                      "file": row.get("file"), "test": row.get("test")})
        else:
            report["actions"].append(entry)
            if not args.apply:
                planned_entries.append(entry)

    manifest = load_manifest(subject_dir)
    coverage_entries = (manifest.get("entries") or []) if args.apply else planned_entries
    coverage = persistence_coverage(valid_tests, coverage_entries)
    if args.apply and args.generate_missing:
        attempted = set()
        for missing in coverage["put_basis_missing"]:
            identity = (missing.get("path_function"), missing.get("unit"))
            if identity in attempted:
                continue
            attempted.add(identity)
            generated, generation = _generate_basis(subject_dir, missing, args)
            report["generation"].append(generation)
            for row in generated:
                try:
                    report["actions"].append(persist_concrete_replay(subject_dir, row))
                except ReplayPersistenceError as exc:
                    report["actions"].append({"action": "refused", "reason": str(exc),
                                              "file": row.get("file")})
        manifest = load_manifest(subject_dir)
        coverage = persistence_coverage(valid_tests, manifest.get("entries") or [])

        put_by_identity = {}
        for row in valid_tests:
            if row.get("kind") != "put":
                continue
            identity = replay_identity(row)
            key = (str(identity.get("path_function") or ""),
                   str(identity.get("unit") or ""),
                   str(identity.get("enc") if identity.get("enc") is not None else ""),
                   str(identity.get("piece") if identity.get("piece") is not None else ""))
            put_by_identity[key] = row
        for missing in coverage["put_basis_missing"]:
            key = (str(missing.get("path_function") or ""),
                   str(missing.get("unit") or ""),
                   str(missing.get("enc") if missing.get("enc") is not None else ""),
                   str(missing.get("piece") if missing.get("piece") is not None else ""))
            put_row = put_by_identity.get(key)
            if put_row is None:
                report["generation"].append({
                    "status": "unrecoverable",
                    "strategy": "specialized-valid-put",
                    "reason": "exact valid PUT artifact is absent",
                    "identity": list(key),
                })
                continue
            generated, generation = _specialize_put_basis(subject_dir, put_row, args)
            report["generation"].append(generation)
            if generated is not None:
                try:
                    report["actions"].append(
                        persist_concrete_replay(subject_dir, generated))
                except ReplayPersistenceError as exc:
                    report["actions"].append({
                        "action": "refused", "reason": str(exc),
                        "file": generated.get("file"),
                    })
        manifest = load_manifest(subject_dir)
        coverage = persistence_coverage(valid_tests, manifest.get("entries") or [])

    if args.apply and coverage["complete"]:
        for generation in report["generation"]:
            generation_root = generation.get("generation_root")
            if generation_root:
                shutil.rmtree(Path(generation_root), ignore_errors=True)
                generation["generation_root_cleaned"] = True

    if args.apply:
        try:
            report["generalization"] = annotate_generalization(
                subject_dir, valid_tests)
        except ReplayPersistenceError as exc:
            report["actions"].append({
                "action": "refused",
                "reason": str(exc),
                "stage": "generalization-annotation",
            })
    report["coverage"] = coverage
    manifest = load_manifest(subject_dir)
    report["manifest_errors_after"] = audit_manifest(subject_dir, manifest)
    refused = any(action.get("action") == "refused" for action in report["actions"])
    failed_generation = any(item.get("status") not in ("ok", "skipped")
                            for item in report["generation"])
    complete = (coverage["complete"] and not report["manifest_errors_after"]
                and not refused and not failed_generation)
    report["status"] = "complete" if complete else "incomplete"
    if args.apply:
        _annotate_result(subject_dir, coverage)
    return report


def _summary(reports: list[dict], *, total_cases: int, mode: str,
             in_progress: bool) -> dict:
    """Build the atomic live/final migration checkpoint."""
    return {
        "schema": "veriput-rq1-concrete-replay-migration/v1",
        "mode": mode,
        "in_progress": in_progress,
        "aggregate_scope": "completed-cases-only" if in_progress else "all-cases",
        "updated_at": time.time(),
        "case_count": total_cases,
        "completed_case_count": len(reports),
        "remaining_case_count": total_cases - len(reports),
        "strict_valid_cases": sum(row["strict_valid_count"] > 0 for row in reports),
        "complete_cases": sum(row["status"] == "complete" for row in reports),
        "incomplete_cases": sum(row["status"] == "incomplete" for row in reports),
        "invalidated_cases": sum(row["status"] == "invalidated-evidence" for row in reports),
        "migration_errors": sum(row["status"] == "migration-error" for row in reports),
        "put_basis_missing": sum(row["coverage"]["put_basis_missing_count"]
                                 for row in reports),
        "valid_concrete_missing": sum(
            row["coverage"].get("valid_concrete_missing_count", 0) for row in reports),
        "manifest_errors": sum(len(row.get("manifest_errors_after") or [])
                               for row in reports),
        "reports": sorted(reports, key=lambda row: row["case"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--apply", action="store_true",
                        help="persist artifacts; without this flag the command is a dry-run")
    parser.add_argument("--generate-missing", action="store_true",
                        help="with --apply, rebuild missing certified basis replays")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--forge-timeout", type=int, default=120)
    parser.add_argument("--memlimit-gib", type=int, default=16)
    parser.add_argument("--esbmc", type=Path, default=REPO / "build" / "src" / "esbmc" / "esbmc")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--invalidation-ledger", type=Path,
                        default=DEFAULT_INVALIDATION_LEDGER)
    args = parser.parse_args()
    args.invalidated_cases = invalidated_cases(args.invalidation_ledger)
    wanted = set(args.case)
    selected = [(case, path) for case, path in
                _case_dirs(args.result_root)
                if not wanted or case in wanted]
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    reports = []
    mode = "apply" if args.apply else "dry-run"
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(migrate_case, case, path, args): case
                   for case, path in selected}
        for future in as_completed(futures):
            case = futures[future]
            try:
                reports.append(future.result())
            except Exception as exc:  # noqa: BLE001 - preserve the other cases
                reports.append({
                    "case": case,
                    "strict_valid_count": 0,
                    "status": "migration-error",
                    "error": str(exc),
                    "coverage": persistence_coverage([], []),
                })
            if args.report:
                _atomic_json(args.report, _summary(
                    reports, total_cases=len(selected), mode=mode,
                    in_progress=True))
    reports.sort(key=lambda row: row["case"])
    summary = _summary(reports, total_cases=len(selected), mode=mode,
                       in_progress=False)
    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.report:
        _atomic_json(args.report, summary)
    sys.stdout.write(text)
    return 1 if (summary["incomplete_cases"] or summary["migration_errors"]
                 or summary["manifest_errors"] or summary["invalidated_cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
