#!/usr/bin/env python3
"""Run the VeriPUT RQ1 generator over prepared benchmark subjects.

This is the production wrapper around the existing Stage-2 (`certify_all.py`)
and Stage-4 (`put_all.py`) drivers.  It is deliberately subject-scoped:
benchmark inputs are read from `/home/samson/workspace/VeriPUT/Results/*/subjects`,
while all generated artifacts are retained under
`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import subject_unit_manifest  # noqa: E402
import target_manifest  # noqa: E402
import unit_schedule  # noqa: E402
from veriput_recipe import STRONG_RECIPE_VERSION  # noqa: E402
from veriput_subjects import PreparedSubject  # noqa: E402

PUT_ALL = HERE / "put_all.py"
CONCRETE_FALLBACK_WITNESS_CHECKS = {
    "SUCCESSFUL",
    "COMPLETE-WITNESS-NO-COORDINATE",
}

DEFAULT_VERIPUT_ROOT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_RESULT_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1" / "VeriPUT"
DEFAULT_AST_CACHE_ROOT = Path("/tmp/veriput_rq1_ast_cache")
DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S = 0
DATASET_LABEL = {
    "peer182": "peer182",
    "bugfix124": "bugfix124",
    "stress243": "real203",
    "stress203": "real203",
    "real203": "real203",
}
TARGET_BENCHMARK_ARG = {
    "peer182": "peer182",
    "bugfix124": "bugfix124",
    "stress243": "stress203",
    "stress203": "stress203",
    "real203": "stress203",
}
PREPARED_DATASET_DIR = {
    "peer182": "Peer182",
    "bugfix124": "BugFix124",
    "stress243": "Stress243",
}


class RQ1RunError(ValueError):
    """The requested production run is unsafe or malformed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(text: str) -> str:
    keep = []
    for ch in str(text):
        keep.append(ch if ch.isalnum() or ch in "._-" else "_")
    return "".join(keep).strip("_") or "unnamed"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def validate_roots(veriput_root: Path, result_root: Path, ast_cache_root: Path) -> None:
    allowed_result = veriput_root / "Results" / "RQ1" / "VeriPUT"
    if not _is_under(result_root, allowed_result):
        raise RQ1RunError(
            f"--result-root must be under {allowed_result}; got {result_root}")
    for protected in (veriput_root / "Datasets", veriput_root / "Results"):
        if _is_under(ast_cache_root, protected):
            raise RQ1RunError(
                f"--ast-cache-root must not be under {protected}; got {ast_cache_root}")


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepare_case_dir(case_dir: Path, *, force_fresh: bool = False) -> None:
    if not case_dir.exists():
        return
    if force_fresh:
        suffix = f".redo.{int(time.time())}.{os.getpid()}"
        target = case_dir.with_name(case_dir.name + suffix)
        case_dir.rename(target)
        return
    if case_dir.joinpath("result.json").exists():
        return
    try:
        has_content = any(case_dir.iterdir())
    except OSError:
        has_content = True
    if not has_content:
        return
    suffix = f".incomplete.{int(time.time())}.{os.getpid()}"
    target = case_dir.with_name(case_dir.name + suffix)
    case_dir.rename(target)


def _latest_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("key")
        if key:
            out[key] = row
    return out


def target_rows(veriput_root: Path, benchmark: str, subject_ids: list[str],
                limit: int, order: str = "fast-first") -> tuple[str, list[dict]]:
    if benchmark not in TARGET_BENCHMARK_ARG:
        raise RQ1RunError(
            "--benchmark must be one of: " + ", ".join(sorted(TARGET_BENCHMARK_ARG)))
    target_arg = TARGET_BENCHMARK_ARG[benchmark]
    doc = target_manifest.build_manifest(veriput_root, [target_arg], "include")
    rows = [row for row in doc.get("targets") or [] if row.get("status") == "ok"]
    if subject_ids:
        wanted = set(subject_ids)
        rows = [row for row in rows if row.get("subject_id") in wanted]
    if order == "fast-first":
        rows = sorted(rows, key=lambda row: _target_cost_key(veriput_root, row))
    elif order != "dataset":
        raise RQ1RunError("--order must be dataset or fast-first")
    if limit:
        rows = rows[:limit]
    return DATASET_LABEL[benchmark], rows


def _target_cost_key(veriput_root: Path, row: dict) -> tuple[int, int, str]:
    bench = row.get("benchmark")
    subject_id = row.get("subject_id") or ""
    dirname = PREPARED_DATASET_DIR.get(bench)
    size = 1 << 60
    if dirname and subject_id:
        candidates = [
            veriput_root / "Results" / dirname / "subjects" / subject_id / "flat.sol",
        ]
        if bench == "bugfix124":
            candidates.append(
                veriput_root / "scripts" / "Results" / "workdirs"
                / "BugFix124" / "subjects" / subject_id / "flat.sol")
        for flat in candidates:
            try:
                size = flat.stat().st_size
                break
            except OSError:
                continue
    hints = len(row.get("units_hint") or [])
    # Hinted target rows tend to be narrower, but flat size dominates.
    return (size, -hints, subject_id)


def cached_subject(subject: PreparedSubject, ast_cache_root: Path,
                   dataset_label: str) -> PreparedSubject:
    ast_name = Path(subject.solast).name
    # certify_all.py re-applies --ast-cache-root using the prepared subject's
    # own benchmark key.  The cache must use that same namespace; the RQ1
    # dataset label (`real203`) is only an output label.
    _ = dataset_label
    cached = ast_cache_root / subject.benchmark / subject.benchmark_key / ast_name
    return subject.with_solast_path(str(cached.resolve()), source="rq1-cache")


def _unit_hints(row: dict, units: list[str]) -> dict:
    hints = list(row.get("units_hint") or [])
    unit_set = set(units)
    return {
        "source": "target-manifest.units_hint",
        "hinted_units": [name for name in hints if name in unit_set],
        "missing_unit_hints": [name for name in hints if name not in unit_set],
        "pending_unit_hints": [],
    }


def build_subject_schedule(subject: PreparedSubject, target_row: dict,
                           ast_cache_root: Path, case_dir: Path, *,
                           timeout_s: int, run_timeout_s: int,
                           memlimit_gib: int) -> dict:
    row = subject_unit_manifest.manifest_for_subject(
        subject,
        generate_ast=True,
        ast_timeout_s=60.0)
    if row.get("status") == "ok":
        units = (row.get("units") or {}).get("units") or []
        row["target"] = target_row
        row["unit_hints"] = _unit_hints(target_row, units)
    manifest = {
        "schema": "veriput-unit-manifest/v1",
        "generated_at": _utc_now(),
        "benchmark": subject.benchmark,
        "ast_cache_root": str(ast_cache_root),
        "summary": {
            "subjects": 1,
            "ok": 1 if row.get("status") == "ok" else 0,
            "missing_ast": 1 if row.get("status") == "missing-ast" else 0,
            "error": 1 if row.get("status") == "error" else 0,
            "units": len((row.get("units") or {}).get("units") or []),
        },
        "subjects": [row],
    }
    cert_out = str((case_dir / "cert" / "certify-results.jsonl").resolve())
    return unit_schedule.build_schedule(
        manifest,
        selection_strategy="priority",
        cert_out=cert_out,
        timeout_s=timeout_s,
        run_timeout_s=run_timeout_s,
        memlimit_gib=memlimit_gib,
        workdir=str((case_dir / "cert" / "work").resolve()))


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _maxrss_mb() -> float:
    # Linux reports ru_maxrss in KiB.
    return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0, 1)


def _proc_children(pid: int) -> list[int]:
    try:
        text = Path(f"/proc/{pid}/task/{pid}/children").read_text()
    except OSError:
        return []
    out = []
    for item in text.split():
        try:
            out.append(int(item))
        except ValueError:
            pass
    return out


def _proc_tree(pid: int) -> list[int]:
    seen = set()
    stack = [pid]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(_proc_children(current))
    return sorted(seen)


def _rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) if len(parts) >= 2 else 0
    except OSError:
        return 0
    return 0


def _rss_tree_mb(pid: int) -> float:
    return round(sum(_rss_kb(child) for child in _proc_tree(pid)) / 1024.0, 1)


def _tail_file(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace")


def _looks_oom(rc: int | None, text: str) -> bool:
    if rc in (-9, 137, -6, 134):
        return True
    lowered = text.lower()
    return any(token in lowered for token in (
        "std::bad_alloc",
        "bad_alloc",
        "out of memory",
        "cannot allocate memory",
        "memory exhausted",
        "enomem",
    ))


def run_command(argv: list[str], timeout_s: float, log_prefix: Path) -> dict:
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    stdout_path = log_prefix.with_suffix(".stdout.log")
    stderr_path = log_prefix.with_suffix(".stderr.log")
    timed_out = False
    maxrss_proc_mb = 0.0
    try:
        with stdout_path.open("w") as stdout_stream, stderr_path.open("w") as stderr_stream:
            proc = subprocess.Popen(argv,
                                    stdout=stdout_stream,
                                    stderr=stderr_stream,
                                    text=True,
                                    start_new_session=True)
            deadline = start + max(1.0, timeout_s)
            while proc.poll() is None:
                maxrss_proc_mb = max(maxrss_proc_mb, _rss_tree_mb(proc.pid))
                if time.monotonic() > deadline:
                    timed_out = True
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait()
                    break
                time.sleep(0.5)
            maxrss_proc_mb = max(maxrss_proc_mb, _rss_tree_mb(proc.pid))
            rc = proc.returncode
    except OSError as exc:
        rc = None
        stdout_path.write_text("")
        stderr_path.write_text(f"could not start: {exc}")
    wall_s = round(time.monotonic() - start, 3)
    stdout_tail = _tail_file(stdout_path)
    stderr_tail = _tail_file(stderr_path)
    combined = stdout_tail + "\n" + stderr_tail
    status = "timeout" if timed_out else ("ok" if rc == 0 else "error")
    if status == "error" and _looks_oom(rc, combined):
        status = "oom"
    return {
        "argv": argv,
        "rc": rc,
        "status": status,
        "timed_out": timed_out,
        "wall_s": wall_s,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "maxrss_proc_mb": maxrss_proc_mb,
        "maxrss_mb_after": _maxrss_mb(),
    }


def _certified_count(cert_path: Path, benchmark_key: str, unit: str) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("bucket") != "CERTIFIED":
            continue
        if row.get("unit") != unit:
            continue
        if (row.get("benchmark") or row.get("poc")) != benchmark_key:
            continue
        count += len(row.get("certified") or {})
    return count


def _cleared_concrete_fallback_count(cert_path: Path, benchmark_key: str,
                                     unit: str) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("unit") != unit:
            continue
        if (row.get("benchmark") or row.get("poc")) != benchmark_key:
            continue
        not_certified = row.get("not_certified") or {}
        details = row.get("not_certified_details") or {}
        if isinstance(details, list):
            detail_rows = {str(d.get("enc")): d for d in details
                           if isinstance(d, dict)}
        elif isinstance(details, dict):
            detail_rows = {str(k): v for k, v in details.items()
                           if isinstance(v, dict)}
        else:
            detail_rows = {}
        for enc in not_certified:
            detail = detail_rows.get(str(enc)) or {}
            if (detail.get("concrete_fallback") is True
                    and detail.get("witness_check")
                    in CONCRETE_FALLBACK_WITNESS_CHECKS):
                count += 1
    return count


def _timeout_concrete_fallback_count(cert_path: Path, benchmark_key: str,
                                     unit: str) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("unit") != unit:
            continue
        if (row.get("benchmark") or row.get("poc")) != benchmark_key:
            continue
        if not _cert_row_timed_out(row):
            continue
        if row.get("certified") or row.get("not_certified"):
            continue
        journal = row.get("partial_witness_journal") or {}
        if not isinstance(journal, dict):
            continue
        try:
            witness_count = int(journal.get("witness_count") or 0)
        except (TypeError, ValueError):
            witness_count = 0
        if witness_count <= 0:
            continue
        for path in journal.get("paths") or []:
            if not isinstance(path, dict):
                continue
            if not path.get("path_id") or not path.get("path_function"):
                continue
            try:
                path_witnesses = int(path.get("witness_count") or 0)
            except (TypeError, ValueError):
                path_witnesses = 0
            if path_witnesses > 0:
                count += 1
    return count


def summarize_certification(cert_path: Path) -> dict:
    summary = {
        "rows": 0,
        "bucket_counts": {},
        "exit_counts": {},
        "witness_counts": {},
        "certified_regions": 0,
        "not_certified_regions": 0,
        "timed_out_units": [],
        "oom_units": [],
        "driver_refusal_tags": {},
    }
    if not cert_path.exists():
        return summary
    buckets = Counter()
    exits = Counter()
    witnesses = Counter()
    refusals = Counter()
    timed_out_units = []
    oom_units = []
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        summary["rows"] += 1
        bucket = str(row.get("bucket") or "<missing>")
        buckets[bucket] += 1
        exit_code = row.get("exit")
        if exit_code is not None:
            exits[str(exit_code)] += 1
        witnessed = row.get("witnessed")
        if witnessed is None:
            witnesses["unknown"] += 1
        elif witnessed:
            witnesses["true"] += 1
        else:
            witnesses["false"] += 1
        summary["certified_regions"] += len(row.get("certified") or {})
        summary["not_certified_regions"] += len(row.get("not_certified") or {})
        unit = row.get("unit") or "<unknown>"
        if _cert_row_timed_out(row):
            timed_out_units.append(unit)
        if exit_code in (-9, 137) or str(bucket).upper() == "OOM":
            oom_units.append(unit)
        refusal = row.get("driver_refusal_tag")
        if refusal:
            refusals[str(refusal)] += 1
    summary["bucket_counts"] = dict(sorted(buckets.items()))
    summary["exit_counts"] = dict(sorted(exits.items()))
    summary["witness_counts"] = dict(sorted(witnesses.items()))
    summary["timed_out_units"] = sorted(set(timed_out_units))
    summary["oom_units"] = sorted(set(oom_units))
    summary["driver_refusal_tags"] = dict(sorted(refusals.items()))
    return summary


def _cert_row_timed_out(row: dict) -> bool:
    if row.get("exit") == 124 or str(row.get("bucket") or "").upper() == "TIMEOUT":
        return True
    diagnostic = row.get("driver_diagnostic") or {}
    progress = row.get("generalise_progress") or {}
    run_timeout = row.get("run_timeout_s") or progress.get("timeout_s")
    try:
        run_timeout = float(run_timeout)
        wall_s = float(row.get("wall_s") or 0)
    except (TypeError, ValueError):
        return False
    if run_timeout <= 0 or wall_s < max(1.0, run_timeout * 0.9):
        return False
    return (
        str(row.get("bucket") or "").upper() == "KILLED"
        and row.get("witnessed") is None
        and diagnostic.get("tag") == "esbmc-no-cov-report")


def _no_output_reason(cert_summary: dict) -> str:
    if cert_summary.get("timed_out_units"):
        units = ", ".join(cert_summary["timed_out_units"][:4])
        suffix = "" if len(cert_summary["timed_out_units"]) <= 4 else ", ..."
        return f"certification timed out before PUT artifacts: {units}{suffix}"
    if cert_summary.get("oom_units"):
        units = ", ".join(cert_summary["oom_units"][:4])
        suffix = "" if len(cert_summary["oom_units"]) <= 4 else ", ..."
        return f"certification OOM before PUT artifacts: {units}{suffix}"
    if cert_summary.get("rows") and not cert_summary.get("certified_regions"):
        buckets = cert_summary.get("bucket_counts") or {}
        if buckets:
            detail = ", ".join(f"{key}={value}" for key, value in buckets.items())
            return f"no certified regions: {detail}"
        return "no certified regions"
    return "no PUT or concrete replay emitted"


def _load_put_jsons(put_root: Path) -> list[dict]:
    out = []
    for path in sorted(put_root.rglob("put.json")):
        try:
            rec = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        rec["_put_json_path"] = str(path)
        out.append(rec)
    return out


def _row_is_no_oracle_put(row: dict, rec: dict) -> bool:
    if row.get("kind") != "put":
        return False
    stats = rec.get("stats") or {}
    if "asserts" in stats or "guarded_asserts" in stats:
        asserts = int(stats.get("asserts") or 0)
        guarded = int(stats.get("guarded_asserts") or 0)
        return asserts - guarded <= 0
    gates = row.get("gates") or {}
    return gates.get("assert") is False


def _row_is_disabled_concrete(row: dict) -> bool:
    if row.get("kind") != "concrete":
        return False
    test = row.get("test")
    file_name = row.get("file")
    if not test or not file_name:
        return False
    try:
        text = Path(str(file_name)).read_text(errors="replace")
    except OSError:
        return False
    enabled_rx = re.compile(r"\bfunction\s+" + re.escape(str(test)) + r"\s*\(")
    disabled_rx = re.compile(r"\bfunction\s+disabled_"
                             + re.escape(str(test)) + r"\s*\(")
    return enabled_rx.search(text) is None and disabled_rx.search(text) is not None


def _row_is_unsupported_concrete(row: dict) -> bool:
    if row.get("kind") != "concrete":
        return False
    if row.get("forge_status") == "Success" or row.get("valid_reference_test"):
        return False
    file_name = row.get("file")
    if not file_name:
        return False
    try:
        text = Path(str(file_name)).read_text(errors="replace")
    except OSError:
        return False
    return "UNSUPPORTED:" in text


def _has_oracle_class(test: dict, *labels: str) -> bool:
    present = {str(label) for label in (test.get("oracle_classes") or [])}
    return any(label in present for label in labels)


def _row_count(row: dict, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _legacy_quality_bucket(row: dict) -> str:
    valid = _row_count(row, "valid")
    if row.get("valid") is None:
        valid = (_row_count(row, "put_valid")
                 + _row_count(row, "concrete_valid"))
        if valid <= 0:
            valid = len(row.get("valid_tests") or [])
    put_valid = _row_count(row, "put_valid")
    if valid <= 0:
        return "no-valid"
    if put_valid <= 0:
        return "valid-no-PUT"
    valid_puts = [
        test for test in (row.get("valid_tests") or [])
        if test.get("kind") == "put"
        and test.get("valid_reference_test", True)
    ]
    if valid_puts:
        if any(_has_oracle_class(test, "R1", "R2") for test in valid_puts):
            return "valid-PUT-with-R1R2"
        return "valid-PUT-no-R1R2"
    if (_row_count(row, "valid_put_with_R1_or_R2") > 0
            or _row_count(row, "valid_put_with_R1") > 0
            or _row_count(row, "valid_put_with_R2") > 0):
        return "valid-PUT-with-R1R2"
    return "valid-PUT-no-R1R2"


def _strength_quality(put_summary: dict) -> dict:
    valid_tests = [
        test for test in (put_summary.get("valid_tests") or [])
        if test.get("valid_reference_test", True)
    ]
    valid_puts = [test for test in valid_tests if test.get("kind") == "put"]
    valid_puts_with_r1 = [
        test for test in valid_puts if _has_oracle_class(test, "R1")
    ]
    valid_puts_with_r2 = [
        test for test in valid_puts if _has_oracle_class(test, "R2")
    ]
    valid_puts_with_r1r2 = [
        test for test in valid_puts if _has_oracle_class(test, "R1", "R2")
    ]
    if not valid_tests:
        bucket = "no-valid"
    elif not valid_puts:
        bucket = "valid-no-PUT"
    elif not valid_puts_with_r1r2:
        bucket = "valid-PUT-no-R1R2"
    else:
        bucket = "valid-PUT-with-R1R2"
    return {
        "quality_bucket": bucket,
        "valid_put_with_R1": len(valid_puts_with_r1),
        "valid_put_with_R2": len(valid_puts_with_r2),
        "valid_put_with_R1_or_R2": len(valid_puts_with_r1r2),
        "valid_put_without_R1R2": (
            len(valid_puts) - len(valid_puts_with_r1r2)),
        "valid_concrete": sum(
            1 for test in valid_tests if test.get("kind") == "concrete"),
    }


def summarize_put_artifacts(put_root: Path) -> dict:
    emission = Counter()
    valid = Counter()
    timing = Counter()
    rows = []
    summary_paths = []
    for path in sorted(put_root.rglob("put-summary.json")):
        try:
            doc = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        summary_paths.append(str(path))
        em = doc.get("emission") or {}
        b = doc.get("deliverable_b") or {}
        v = b.get("valid_reference_tests") or {}
        emission["put"] += int(em.get("puts_emitted") or 0)
        emission["concrete"] += int(em.get("concrete_replays_emitted") or 0)
        valid["put"] += int(v.get("put") or 0)
        valid["concrete"] += int(v.get("concrete") or 0)
        tm = doc.get("timing") or {}
        generation_wall_s = tm.get("generation_wall_s")
        if generation_wall_s is None:
            generation_wall_s = tm.get("emission_wall_s")
        timing["stage4_generation_wall_s"] += float(generation_wall_s or 0.0)
        timing["stage4_emission_wall_s"] += float(
            tm.get("emission_wall_s") or 0.0)
        timing["foundry_replay_wall_s"] += float(
            tm.get("foundry_replay_wall_s") or 0.0)
        timing["put_all_wall_s"] += float(tm.get("total_wall_s") or 0.0)
        rows.extend(b.get("rows") or [])

    put_jsons = _load_put_jsons(put_root)
    by_file_test = {}
    by_test_candidates = {}
    for rec in put_jsons:
        test = rec.get("test")
        file_name = rec.get("file")
        if test and file_name:
            by_file_test[(str(file_name), str(test))] = rec
        if test:
            by_test_candidates.setdefault(str(test), []).append(rec)
    by_unique_test = {
        test: rows[0] for test, rows in by_test_candidates.items()
        if len(rows) == 1
    }

    raw_tests = []
    valid_tests = []
    deliverable_raw = Counter()
    deliverable_valid = Counter()
    deliverable_tests = set()
    for row in rows:
        test_name = row.get("test")
        file_name = row.get("file")
        rec = by_file_test.get((str(file_name), str(test_name)), {})
        if not rec and not file_name:
            rec = by_unique_test.get(str(test_name), {})
        if (row.get("refused") or _row_is_no_oracle_put(row, rec)
                or _row_is_disabled_concrete(row)
                or _row_is_unsupported_concrete(row)):
            continue
        entry = {
            "kind": row.get("kind"),
            "stage2_source": row.get("stage2_source"),
            "unit": row.get("unit"),
            "enc": row.get("enc"),
            "piece": row.get("piece"),
            "test": row.get("test"),
            "file": row.get("file"),
            "forge_status": row.get("forge_status"),
            "valid_reference_test": bool(row.get("valid_reference_test")),
            "b": bool(row.get("b")),
            "oracle_classes": rec.get("stats", {}).get("oracle_classes") or [],
            "put_json": rec.get("_put_json_path"),
        }
        raw_tests.append(entry)
        if entry["kind"]:
            deliverable_raw[entry["kind"]] += 1
        if entry["test"]:
            deliverable_tests.add(entry["test"])
        if entry["valid_reference_test"]:
            valid_tests.append(entry)
            if entry["kind"]:
                deliverable_valid[entry["kind"]] += 1

    if rows:
        emission = deliverable_raw
        valid = deliverable_valid

    oracle_label_counts = Counter()
    oracle_combo_counts = Counter()
    assertion_oracles = []
    for rec in put_jsons:
        test = rec.get("test")
        if rows and test not in deliverable_tests:
            continue
        details = rec.get("stats", {}).get("assertion_oracles") or []
        for detail in details:
            classes = tuple(detail.get("classes") or [])
            if not classes:
                continue
            for label in classes:
                oracle_label_counts[label] += 1
            oracle_combo_counts["+".join(classes)] += 1
            enriched = dict(detail)
            enriched["test"] = test
            enriched["put_json"] = rec.get("_put_json_path")
            assertion_oracles.append(enriched)

    summary = {
        "raw": int(emission["put"] + emission["concrete"]),
        "valid": int(valid["put"] + valid["concrete"]),
        "put_raw": int(emission["put"]),
        "put_valid": int(valid["put"]),
        "concrete_raw": int(emission["concrete"]),
        "concrete_valid": int(valid["concrete"]),
        "summary_paths": summary_paths,
        "raw_tests": raw_tests,
        "valid_tests": valid_tests,
        "put_json_count": len(put_jsons),
        "stage4_generation_wall_s": round(
            timing["stage4_generation_wall_s"], 3),
        "stage4_emission_wall_s": round(
            timing["stage4_emission_wall_s"], 3),
        "foundry_replay_wall_s": round(
            timing["foundry_replay_wall_s"], 3),
        "put_all_wall_s": round(timing["put_all_wall_s"], 3),
        "oracle_class_counts": dict(sorted(oracle_label_counts.items())),
        "oracle_class_combo_counts": dict(sorted(oracle_combo_counts.items())),
        "assertion_oracles": assertion_oracles,
    }
    summary.update(_strength_quality(summary))
    return summary


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _mem_available_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024.0 * 1024.0)
    except OSError:
        return 0.0
    return 0.0


def validate_jobs(args) -> None:
    if args.jobs <= 0:
        raise RQ1RunError("--jobs must be positive")
    if args.jobs == 1:
        return
    available = _mem_available_gib()
    committed = float(args.jobs * args.memlimit_gib)
    if available and committed > available * args.mem_fraction:
        raise RQ1RunError(
            f"--jobs {args.jobs} x --memlimit-gib {args.memlimit_gib} = "
            f"{committed:g}GiB exceeds {args.mem_fraction:.0%} of "
            f"MemAvailable ({available:.1f}GiB)")


def _stage_wall_s(stages: list[dict], stage_name: str) -> float:
    return sum(stage.get("wall_s") or 0.0 for stage in stages
               if stage.get("stage") == stage_name)


def _format_stage2_no_output_stop(stage2_wall_s: float) -> str:
    return (f"no output after {stage2_wall_s:.1f}s Stage 2; "
            "stopped before remaining units")


def _format_stage4_no_output_stop(stage4_wall_s: float) -> str:
    return (f"no output after {stage4_wall_s:.1f}s Stage 4; "
            "stopped before remaining units")


def _format_no_candidate_unit_stop(count: int) -> str:
    return (f"no Stage-2 candidate after {count} consecutive units; "
            "stopped before remaining units")


def _format_low_budget_concrete_only_skip(remaining_s: float,
                                          threshold_s: int) -> str:
    return (f"valid artifact already produced; {remaining_s:.1f}s remains "
            f"below the {threshold_s}s concrete-only Stage 4 floor")


def _format_put_saturated_concrete_only_skip(put_valid: int,
                                             threshold: int) -> str:
    return (f"{put_valid} valid PUT artifact(s) already produced; "
            f"concrete-only Stage 4 skipped at the {threshold}-PUT floor")


def _is_concrete_only_stage4(n_certified: int,
                             n_cleared_fallback: int,
                             n_timeout_fallback: int) -> bool:
    return (n_certified <= 0
            and (n_cleared_fallback + n_timeout_fallback) > 0)


def _should_skip_concrete_only_after_puts(put_summary: dict,
                                         threshold: int,
                                         n_certified: int,
                                         n_cleared_fallback: int,
                                         n_timeout_fallback: int) -> bool:
    if threshold <= 0:
        return False
    if int(put_summary.get("put_valid") or 0) < threshold:
        return False
    return _is_concrete_only_stage4(
        n_certified, n_cleared_fallback, n_timeout_fallback)


def _should_skip_low_budget_concrete_only_stage4(put_summary: dict,
                                                remaining_s: float,
                                                threshold_s: int,
                                                n_certified: int,
                                                n_cleared_fallback: int,
                                                n_timeout_fallback: int) -> bool:
    if threshold_s <= 0:
        return False
    if int(put_summary.get("valid") or 0) <= 0:
        return False
    if not _is_concrete_only_stage4(
            n_certified, n_cleared_fallback, n_timeout_fallback):
        return False
    return remaining_s < float(threshold_s)


def _should_stop_after_zero_output_stage4(stages: list[dict],
                                          put_summary: dict,
                                          threshold_s: int) -> bool:
    if threshold_s <= 0:
        return False
    if int(put_summary.get("raw") or 0) > 0:
        return False
    return _stage_wall_s(stages, "put") >= float(threshold_s)


def _should_stop_after_no_output_stage2(stages: list[dict],
                                        put_summary: dict,
                                        threshold_s: int,
                                        consecutive_units: int,
                                        min_consecutive_units: int = 2) -> bool:
    if threshold_s <= 0:
        return False
    if int(put_summary.get("raw") or 0) > 0:
        return False
    if consecutive_units < max(1, int(min_consecutive_units)):
        return False
    return _stage_wall_s(stages, "certify") >= float(threshold_s)


def _should_stop_after_no_candidate_units(consecutive_units: int,
                                          put_summary: dict,
                                          threshold_units: int) -> bool:
    if threshold_units <= 0:
        return False
    if int(put_summary.get("raw") or 0) > 0:
        return False
    return consecutive_units >= threshold_units


def _certify_argv_for_remaining(job: dict, remaining_s: float, run_timeout_s: int,
                                memlimit_gib: int,
                                unit_timeout_cap_s: int = 0) -> list[str]:
    budget_source = remaining_s
    if unit_timeout_cap_s > 0:
        budget_source = min(budget_source, float(unit_timeout_cap_s))
    budget = max(1, int(budget_source))
    run_budget = max(1, min(budget, int(run_timeout_s)))
    return unit_schedule.budgeted_certify_argv(
        [str(arg) for arg in job["certify_argv"]],
        timeout_s=budget,
        run_timeout_s=run_budget,
        memlimit_gib=memlimit_gib,
        workdir=job["certification_budget"]["workdir"])


def _put_argv(cert_path: Path, unit: str, benchmark_key: str, out_root: Path,
              remaining_s: float, memlimit_gib: int, forge_timeout: int) -> list[str]:
    budget = max(1, int(remaining_s))
    return [
        sys.executable,
        str(PUT_ALL),
        "--cert",
        str(cert_path),
        "--only",
        f"{benchmark_key}.{unit}",
        "--strong-recipe",
        "--emit-cleared-concrete-fallbacks",
        "--timeout",
        str(budget),
        "--forge-timeout",
        str(forge_timeout),
        "--memlimit-gib",
        str(memlimit_gib),
        "--out-root",
        str(out_root),
    ]


def run_subject(target_row: dict, dataset_label: str, args) -> tuple[dict, dict]:
    start = time.monotonic()
    subject_id = target_row["subject_id"]
    case_dir = Path(args.result_root) / dataset_label / "subjects" / _safe_name(subject_id)
    prepare_case_dir(case_dir, force_fresh=bool(args.redo))
    cert_path = case_dir / "cert" / "certify-results.jsonl"
    ast_cache_root = Path(args.ast_cache_root).expanduser().resolve()
    subject = subject_unit_manifest.resolve_subject(
        subject_id,
        benchmark=target_row["benchmark"],
        require_unit=False)
    subject = cached_subject(subject.with_inferred_solc_bin(), ast_cache_root, dataset_label)
    deadline = start + float(args.timeout)
    stages = []
    units_attempted = []
    result_status = "ok"
    failure_reason = None
    early_stop_reason = None
    consecutive_no_candidate_units = 0
    max_consecutive_no_candidate_units = 0
    low_budget_concrete_only_stage4_skips = []
    put_saturated_concrete_only_stage4_skips = []

    try:
        schedule = build_subject_schedule(subject,
                                          target_row,
                                          ast_cache_root,
                                          case_dir,
                                          timeout_s=args.timeout,
                                          run_timeout_s=args.esbmc_run_timeout,
                                          memlimit_gib=args.memlimit_gib)
    except Exception as exc:  # Fail-soft at subject granularity.
        result_status = "error"
        failure_reason = str(exc)
        schedule = {
            "schema": "veriput-unit-schedule/v1",
            "jobs": [],
            "summary": {},
        }

    _write_json(case_dir / "unit-schedule.json", schedule)
    jobs = list(schedule.get("jobs") or [])
    if result_status == "ok" and not jobs:
        result_status = "no-units"
        failure_reason = "target contract has no schedulable public/external units"

    for idx, job in enumerate(jobs, 1):
        if _remaining(deadline) < args.min_remaining_s:
            result_status = "budget-exhausted"
            failure_reason = "case budget exhausted before remaining units"
            break
        unit = job["unit"]
        units_attempted.append(unit)
        cert_argv = _certify_argv_for_remaining(job, _remaining(deadline),
                                                args.esbmc_run_timeout,
                                                args.memlimit_gib,
                                                args.stage2_unit_timeout_cap_s)
        cert_stage = run_command(cert_argv,
                                 _remaining(deadline) + args.wrapper_grace,
                                 case_dir / "logs" / f"{idx:03d}-{_safe_name(unit)}-certify")
        cert_stage.update({
            "stage": "certify",
            "unit": unit,
            "job_id": job.get("job_id"),
        })
        stages.append(cert_stage)
        if cert_stage["status"] in ("timeout", "oom"):
            result_status = cert_stage["status"]
            failure_reason = f"certify {unit}: {cert_stage['status']}"
            break
        if cert_stage["status"] != "ok":
            result_status = "error"
            failure_reason = f"certify {unit}: {cert_stage['status']}"
            break
        n_certified = _certified_count(cert_path, subject.benchmark_key, unit)
        n_cleared_fallback = _cleared_concrete_fallback_count(
            cert_path, subject.benchmark_key, unit)
        n_timeout_fallback = _timeout_concrete_fallback_count(
            cert_path, subject.benchmark_key, unit)
        n_stage4_candidates = (
            n_certified + n_cleared_fallback + n_timeout_fallback)
        if n_stage4_candidates <= 0:
            consecutive_no_candidate_units += 1
            max_consecutive_no_candidate_units = max(
                max_consecutive_no_candidate_units,
                consecutive_no_candidate_units)
            partial_put = summarize_put_artifacts(case_dir / "put")
            if _should_stop_after_no_candidate_units(
                    consecutive_no_candidate_units,
                    partial_put,
                    args.no_candidate_stage2_unit_stop_n):
                early_stop_reason = _format_no_candidate_unit_stop(
                    consecutive_no_candidate_units)
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            stop_s = args.no_output_stage2_stop_s
            min_no_candidate_units = (
                1 if args.no_candidate_stage2_unit_stop_n == 1 else 2)
            if _should_stop_after_no_output_stage2(
                    stages,
                    partial_put,
                    stop_s,
                    consecutive_no_candidate_units,
                    min_no_candidate_units):
                early_stop_reason = _format_stage2_no_output_stop(
                    _stage_wall_s(stages, "certify"))
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            continue
        consecutive_no_candidate_units = 0
        partial_put = summarize_put_artifacts(case_dir / "put")
        remaining_before_stage4 = _remaining(deadline)
        if _should_skip_concrete_only_after_puts(
                partial_put,
                args.skip_concrete_only_after_put_valid,
                n_certified,
                n_cleared_fallback,
                n_timeout_fallback):
            put_valid_before_skip = partial_put.get("put_valid") or 0
            skip_reason = _format_put_saturated_concrete_only_skip(
                put_valid_before_skip,
                args.skip_concrete_only_after_put_valid)
            put_saturated_concrete_only_stage4_skips.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "remaining_s": round(remaining_before_stage4, 3),
                "threshold_put_valid":
                    args.skip_concrete_only_after_put_valid,
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "raw_before_skip": partial_put.get("raw") or 0,
                "valid_before_skip": partial_put.get("valid") or 0,
                "put_valid_before_skip": put_valid_before_skip,
                "reason": skip_reason,
            })
            result_status = "budget-exhausted"
            failure_reason = skip_reason
            break
        if _should_skip_low_budget_concrete_only_stage4(
                partial_put,
                remaining_before_stage4,
                args.min_concrete_only_stage4_s,
                n_certified,
                n_cleared_fallback,
                n_timeout_fallback):
            skip_reason = _format_low_budget_concrete_only_skip(
                remaining_before_stage4, args.min_concrete_only_stage4_s)
            low_budget_concrete_only_stage4_skips.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "remaining_s": round(remaining_before_stage4, 3),
                "threshold_s": args.min_concrete_only_stage4_s,
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "raw_before_skip": partial_put.get("raw") or 0,
                "valid_before_skip": partial_put.get("valid") or 0,
                "reason": skip_reason,
            })
            result_status = "budget-exhausted"
            failure_reason = skip_reason
            break
        if _remaining(deadline) < args.min_remaining_s:
            result_status = "budget-exhausted"
            failure_reason = "case budget exhausted before Stage 4"
            break
        put_root = case_dir / "put" / _safe_name(unit)
        put_generation_budget_s = _remaining(deadline)
        put_argv = _put_argv(cert_path,
                             unit,
                             subject.benchmark_key,
                             put_root,
                             put_generation_budget_s,
                             args.memlimit_gib,
                             args.forge_timeout)
        # Stage 4's ESBMC/emission work is budgeted by --timeout and the
        # remaining case deadline passed above.  put_all.py then runs Foundry
        # as a second, refutation-only replay oracle; let that finish outside
        # the generation timeout so a slow replay does not reclassify completed
        # generation as a tool timeout.
        put_wrapper_timeout_s = (put_generation_budget_s + args.wrapper_grace
                                 + 2 * args.forge_timeout)
        put_stage = run_command(put_argv,
                                put_wrapper_timeout_s,
                                case_dir / "logs" / f"{idx:03d}-{_safe_name(unit)}-put")
        put_stage.update({
            "stage": "put",
            "unit": unit,
            "generation_budget_s": round(put_generation_budget_s, 3),
            "foundry_replay_outside_generation_timeout": True,
            "foundry_replay_timeout_s_per_run": args.forge_timeout,
            "certified_regions_for_unit": n_certified,
            "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
            "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
            "stage4_candidates_for_unit": n_stage4_candidates,
            "put_out_root": str(put_root),
        })
        stages.append(put_stage)
        if put_stage["status"] in ("timeout", "oom"):
            result_status = put_stage["status"]
            failure_reason = f"put {unit}: {put_stage['status']}"
            break
        partial_put = summarize_put_artifacts(case_dir / "put")
        if _should_stop_after_zero_output_stage4(
                stages, partial_put, args.zero_output_stage4_stop_s):
            early_stop_reason = _format_stage4_no_output_stop(
                _stage_wall_s(stages, "put"))
            result_status = "early-stop-no-output"
            failure_reason = early_stop_reason
            break
        stop_s = args.no_output_stage2_stop_s
        min_no_candidate_units = (
            1 if args.no_candidate_stage2_unit_stop_n == 1 else 2)
        if _should_stop_after_no_output_stage2(
                stages,
                partial_put,
                stop_s,
                consecutive_no_candidate_units,
                min_no_candidate_units):
            early_stop_reason = _format_stage2_no_output_stop(
                _stage_wall_s(stages, "certify"))
            result_status = "early-stop-no-output"
            failure_reason = early_stop_reason
            break

    put_summary = summarize_put_artifacts(case_dir / "put")
    cert_summary = summarize_certification(cert_path)
    wall_total_s = round(time.monotonic() - start, 3)
    completion_status = result_status
    budget_exhausted = completion_status == "budget-exhausted"
    early_stopped_no_output = completion_status == "early-stop-no-output"
    if budget_exhausted and put_summary["raw"] > 0:
        result_status = "ok"
    if early_stopped_no_output:
        result_status = "no-output"
    if result_status == "ok" and put_summary["raw"] == 0:
        result_status = "no-output"
        failure_reason = _no_output_reason(cert_summary)
    stage2_wall_s = round(_stage_wall_s(stages, "certify"), 3)
    stage4_wall_s = round(_stage_wall_s(stages, "put"), 3)
    generation_wall_s = round(
        stage2_wall_s + put_summary["stage4_generation_wall_s"], 3)
    row = {
        "key": f"gen:veriput:{subject_id}",
        "stage": "gen_veriput",
        "schema": "veriput-rq1-result-row/v1",
        "ts": round(time.time(), 3),
        "generated_at": _utc_now(),
        "host": socket.gethostname(),
        "n_concurrent": args.jobs,
        "mem_budget_mb": args.memlimit_gib * 1024,
        "tool_timeout_s": args.timeout,
        "esbmc_run_timeout_s": args.esbmc_run_timeout,
        "stage2_unit_timeout_cap_s": args.stage2_unit_timeout_cap_s,
        "cleared_concrete_fallbacks_enabled": True,
        "timeout_concrete_fallbacks_enabled": True,
        "no_output_stage2_stop_s": args.no_output_stage2_stop_s,
        "no_candidate_stage2_unit_stop_n": args.no_candidate_stage2_unit_stop_n,
        "max_consecutive_no_candidate_units": max_consecutive_no_candidate_units,
        "zero_output_stage4_stop_s": args.zero_output_stage4_stop_s,
        "min_concrete_only_stage4_s": args.min_concrete_only_stage4_s,
        "skip_concrete_only_after_put_valid":
            args.skip_concrete_only_after_put_valid,
        "low_budget_concrete_only_stage4_skips":
            low_budget_concrete_only_stage4_skips,
        "low_budget_concrete_only_stage4_skip_count":
            len(low_budget_concrete_only_stage4_skips),
        "put_saturated_concrete_only_stage4_skips":
            put_saturated_concrete_only_stage4_skips,
        "put_saturated_concrete_only_stage4_skip_count":
            len(put_saturated_concrete_only_stage4_skips),
        "early_stop_reason": early_stop_reason,
        "wall_cap_s": args.timeout + args.wrapper_grace,
        "status": result_status,
        "completion_status": completion_status,
        "budget_exhausted": budget_exhausted,
        "reason": failure_reason,
        "subject_id": subject_id,
        "benchmark": target_row["benchmark"],
        "dataset": dataset_label,
        "contract": target_row.get("contract"),
        "raw": put_summary["raw"],
        "valid": put_summary["valid"],
        "put_raw": put_summary["put_raw"],
        "put_valid": put_summary["put_valid"],
        "concrete_raw": put_summary["concrete_raw"],
        "concrete_valid": put_summary["concrete_valid"],
        "quality_bucket": put_summary["quality_bucket"],
        "valid_put_with_R1": put_summary["valid_put_with_R1"],
        "valid_put_with_R2": put_summary["valid_put_with_R2"],
        "valid_put_with_R1_or_R2": put_summary["valid_put_with_R1_or_R2"],
        "valid_put_without_R1R2": put_summary["valid_put_without_R1R2"],
        "raw_tests": put_summary["raw_tests"],
        "valid_tests": put_summary["valid_tests"],
        "oracle_class_counts": put_summary["oracle_class_counts"],
        "oracle_class_combo_counts": put_summary["oracle_class_combo_counts"],
        "assertion_oracles": put_summary["assertion_oracles"],
        "put_json_count": put_summary["put_json_count"],
        "cert_bucket_counts": cert_summary["bucket_counts"],
        "cert_exit_counts": cert_summary["exit_counts"],
        "cert_witness_counts": cert_summary["witness_counts"],
        "cert_timed_out_units": cert_summary["timed_out_units"],
        "cert_oom_units": cert_summary["oom_units"],
        "units_attempted": units_attempted,
        "units_scheduled": len(jobs),
        "generation_wall_s": generation_wall_s,
        "stage2_wall_s": stage2_wall_s,
        "stage4_wall_s": stage4_wall_s,
        "stage4_generation_wall_s": put_summary["stage4_generation_wall_s"],
        "stage4_emission_wall_s": put_summary["stage4_emission_wall_s"],
        "foundry_replay_wall_s": put_summary["foundry_replay_wall_s"],
        "put_all_wall_s": put_summary["put_all_wall_s"],
        "foundry_replay_outside_generation_timeout": True,
        "wall": wall_total_s,
        "wall_total_s": wall_total_s,
        "maxrss_mb": max(
            [stage.get("maxrss_proc_mb") or 0.0 for stage in stages] or [0.0]),
        "artifact_root": str(case_dir),
        "result_json": str(case_dir / "result.json"),
        "cert_jsonl": str(cert_path),
        "put_summary_paths": put_summary["summary_paths"],
        "raw_artifacts_retained": True,
        "valid_artifacts_retained": True,
        "recipe_version": STRONG_RECIPE_VERSION,
    }
    detail = {
        "schema": "veriput-rq1-case-result/v1",
        "row": row,
        "target": target_row,
        "subject": subject.to_record(),
        "schedule": {
            "path": str(case_dir / "unit-schedule.json"),
            "summary": schedule.get("summary") or {},
        },
        "stages": stages,
        "certification": cert_summary,
        "put": put_summary,
    }
    _write_json(case_dir / "result.json", detail)
    return row, detail


def run_selected_subjects(rows: list[dict], dataset_label: str, journal: Path,
                          done: dict[str, dict], args) -> int:
    selected = [row for row in rows
                if f"gen:veriput:{row['subject_id']}" not in done]
    if not selected:
        return 0
    if args.jobs <= 1:
        attempted = 0
        for target_row in selected:
            print(f"[rq1] {dataset_label} {target_row['subject_id']} "
                  f"contract={target_row.get('contract')}", flush=True)
            row, _detail = run_subject(target_row, dataset_label, args)
            _append_jsonl(journal, row)
            write_dataset_manifest(Path(args.result_root), dataset_label, journal)
            attempted += 1
            print(f"[rq1] -> status={row['status']} raw={row['raw']} "
                  f"valid={row['valid']} put={row['put_valid']}/"
                  f"{row['put_raw']} concrete={row['concrete_valid']}/"
                  f"{row['concrete_raw']} bucket={row.get('quality_bucket')} "
                  f"wall={row['wall_total_s']}s",
                  flush=True)
        return attempted

    attempted = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {}
        for target_row in selected:
            print(f"[rq1] queued {dataset_label} {target_row['subject_id']} "
                  f"contract={target_row.get('contract')}", flush=True)
            futures[executor.submit(run_subject, target_row, dataset_label, args)] = target_row
        for future in as_completed(futures):
            target_row = futures[future]
            try:
                row, _detail = future.result()
            except Exception as exc:  # Subject-level fail-soft.
                now = round(time.time(), 3)
                row = {
                    "key": f"gen:veriput:{target_row['subject_id']}",
                    "stage": "gen_veriput",
                    "schema": "veriput-rq1-result-row/v1",
                    "ts": now,
                    "generated_at": _utc_now(),
                    "host": socket.gethostname(),
                    "n_concurrent": args.jobs,
                    "mem_budget_mb": args.memlimit_gib * 1024,
                    "tool_timeout_s": args.timeout,
                    "esbmc_run_timeout_s": args.esbmc_run_timeout,
                    "wall_cap_s": args.timeout + args.wrapper_grace,
                    "status": "error",
                    "completion_status": "error",
                    "budget_exhausted": False,
                    "reason": f"runner exception: {exc}",
                    "subject_id": target_row["subject_id"],
                    "benchmark": target_row.get("benchmark"),
                    "dataset": dataset_label,
                    "contract": target_row.get("contract"),
                    "raw": 0,
                    "valid": 0,
                    "put_raw": 0,
                    "put_valid": 0,
                    "concrete_raw": 0,
                    "concrete_valid": 0,
                    "quality_bucket": "no-valid",
                    "valid_put_with_R1": 0,
                    "valid_put_with_R2": 0,
                    "valid_put_with_R1_or_R2": 0,
                    "valid_put_without_R1R2": 0,
                    "wall": 0.0,
                    "wall_total_s": 0.0,
                    "maxrss_mb": 0.0,
                    "recipe_version": STRONG_RECIPE_VERSION,
                }
            _append_jsonl(journal, row)
            write_dataset_manifest(Path(args.result_root), dataset_label, journal)
            attempted += 1
            print(f"[rq1] done {target_row['subject_id']} -> "
                  f"status={row['status']} raw={row['raw']} valid={row['valid']} "
                  f"put={row['put_valid']}/{row['put_raw']} "
                  f"concrete={row['concrete_valid']}/{row['concrete_raw']} "
                  f"bucket={row.get('quality_bucket')} wall={row['wall_total_s']}s",
                  flush=True)
    return attempted


def write_dataset_manifest(root: Path, dataset_label: str, journal: Path) -> None:
    latest = _latest_rows(journal)
    status = Counter(str(row.get("status") or "<missing>") for row in latest.values())
    quality = Counter(
        str(row.get("quality_bucket") or _legacy_quality_bucket(row))
        for row in latest.values())
    doc = {
        "schema": "veriput-rq1-dataset-manifest/v1",
        "generated_at": _utc_now(),
        "dataset": dataset_label,
        "journal": str(journal),
        "summary": {
            "rows": len(latest),
            "raw": sum(row.get("raw") or 0 for row in latest.values()),
            "valid": sum(row.get("valid") or 0 for row in latest.values()
                         if row.get("valid") is not None),
            "put_raw": sum(row.get("put_raw") or 0 for row in latest.values()),
            "put_valid": sum(row.get("put_valid") or 0 for row in latest.values()),
            "concrete_raw": sum(row.get("concrete_raw") or 0 for row in latest.values()),
            "concrete_valid": sum(row.get("concrete_valid") or 0
                                  for row in latest.values()),
            "valid_put_with_R1": sum(row.get("valid_put_with_R1") or 0
                                     for row in latest.values()),
            "valid_put_with_R2": sum(row.get("valid_put_with_R2") or 0
                                     for row in latest.values()),
            "valid_put_with_R1_or_R2": sum(
                row.get("valid_put_with_R1_or_R2") or 0
                for row in latest.values()),
            "valid_put_without_R1R2": sum(
                row.get("valid_put_without_R1R2") or 0
                for row in latest.values()),
            "status": dict(sorted(status.items())),
            "quality_bucket": dict(sorted(quality.items())),
        },
    }
    _write_json(root / dataset_label / "manifest.json", doc)


def build_dry_run(args) -> dict:
    dataset_label, rows = target_rows(Path(args.veriput_root), args.benchmark,
                                      args.subject_id, args.limit, args.order)
    return {
        "schema": "veriput-rq1-dry-run/v1",
        "generated_at": _utc_now(),
        "dataset": dataset_label,
        "result_root": args.result_root,
        "ast_cache_root": args.ast_cache_root,
        "timeout_s": args.timeout,
        "esbmc_run_timeout_s": args.esbmc_run_timeout,
        "no_output_stage2_stop_s": args.no_output_stage2_stop_s,
        "no_candidate_stage2_unit_stop_n": args.no_candidate_stage2_unit_stop_n,
        "zero_output_stage4_stop_s": args.zero_output_stage4_stop_s,
        "min_concrete_only_stage4_s": args.min_concrete_only_stage4_s,
        "skip_concrete_only_after_put_valid":
            args.skip_concrete_only_after_put_valid,
        "memlimit_gib": args.memlimit_gib,
        "jobs": args.jobs,
        "order": args.order,
        "subjects": [{
            "subject_id": row.get("subject_id"),
            "benchmark": row.get("benchmark"),
            "contract": row.get("contract"),
            "units_hint": row.get("units_hint") or [],
        } for row in rows],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--veriput-root", default=str(DEFAULT_VERIPUT_ROOT))
    ap.add_argument("--benchmark", required=True,
                    choices=sorted(TARGET_BENCHMARK_ARG),
                    help="peer182, bugfix124, or real203/stress203")
    ap.add_argument("--subject-id", action="append", default=[],
                    help="restrict to one prepared subject id. Repeatable")
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N selected target subjects")
    ap.add_argument("--order", choices=("fast-first", "dataset"),
                    default="fast-first",
                    help="subject order before --limit. fast-first sorts by "
                         "prepared flat.sol size to get early throughput")
    ap.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    ap.add_argument("--ast-cache-root", default=str(DEFAULT_AST_CACHE_ROOT))
    ap.add_argument("--timeout", type=int, default=600,
                    help="whole subject generation budget, seconds")
    ap.add_argument("--esbmc-run-timeout", type=int, default=600,
                    help="per ESBMC invocation budget inside certification, "
                         "seconds. The whole subject still gets --timeout")
    ap.add_argument("--stage2-unit-timeout-cap-s", type=int,
                    default=DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S,
                    help="if positive, cap each Stage-2 unit's whole "
                         "certify_all.py budget to this many seconds while "
                         "leaving --esbmc-run-timeout as the per-ESBMC-run "
                         "cap. Default 0 preserves the old remaining-subject "
                         "budget behavior")
    ap.add_argument("--wrapper-grace", type=int, default=60,
                    help="subprocess cleanup/writeout slack outside the tool budget")
    ap.add_argument("--min-remaining-s", type=int, default=20,
                    help="do not start another stage with less than this many seconds")
    ap.add_argument("--no-output-stage2-stop-s", type=int, default=0,
                    help="if positive, stop trying remaining units in a subject "
                         "after this many cumulative Stage-2 seconds when no "
                         "raw artifact has been produced")
    ap.add_argument("--no-candidate-stage2-unit-stop-n", type=int, default=0,
                    help="if positive, stop trying remaining units in a subject "
                         "after this many consecutive Stage-2 units produce no "
                         "certified region and no cleared concrete fallback, "
                         "provided no raw artifact has been produced. Default "
                         "0 preserves old scheduling")
    ap.add_argument("--zero-output-stage4-stop-s", type=int, default=0,
                    help="if positive, stop trying remaining units in a subject "
                         "after this many cumulative Stage-4 seconds when "
                         "Stage 4 has run candidate rows but no raw artifact "
                         "has been produced. Default 0 preserves old scheduling")
    ap.add_argument("--min-concrete-only-stage4-s", type=int, default=90,
                    help="after at least one valid artifact exists, do not "
                         "start a Stage-4 pass whose only candidates are "
                         "concrete fallbacks unless at least this many "
                         "generation seconds remain. Set 0 to disable")
    ap.add_argument("--skip-concrete-only-after-put-valid", type=int, default=2,
                    help="after this many valid PUT artifacts have already "
                         "been emitted for a subject, do not start another "
                         "Stage-4 pass whose candidates are only concrete "
                         "fallbacks. Set 0 to disable")
    ap.add_argument("--memlimit-gib", type=int, default=12,
                    help="per-ESBMC memory budget passed to Stage 2/4")
    ap.add_argument("--jobs", type=int, default=1,
                    help="number of prepared subjects to run concurrently")
    ap.add_argument("--mem-fraction", type=float, default=0.70,
                    help="refuse --jobs when jobs*memlimit exceeds this "
                         "fraction of current MemAvailable")
    ap.add_argument("--forge-timeout", type=int, default=180)
    ap.add_argument("--resume", action="store_true",
                    help="skip subject keys already present in results.jsonl")
    ap.add_argument("--redo", action="store_true",
                    help="run selected subjects even if results.jsonl already has a row")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        veriput_root = Path(args.veriput_root).expanduser().resolve()
        result_root = Path(args.result_root).expanduser().resolve()
        ast_cache_root = Path(args.ast_cache_root).expanduser().resolve()
        validate_roots(veriput_root, result_root, ast_cache_root)
        if (args.timeout <= 0 or args.esbmc_run_timeout <= 0
                or args.wrapper_grace < 0 or args.memlimit_gib <= 0
                or args.no_output_stage2_stop_s < 0
                or args.no_candidate_stage2_unit_stop_n < 0
                or args.stage2_unit_timeout_cap_s < 0
                or args.zero_output_stage4_stop_s < 0
                or args.min_concrete_only_stage4_s < 0
                or args.skip_concrete_only_after_put_valid < 0):
            raise RQ1RunError("timeouts and --memlimit-gib must be positive; "
                              "--no-output-stage2-stop-s and "
                              "--no-candidate-stage2-unit-stop-n and "
                              "--stage2-unit-timeout-cap-s and "
                              "--zero-output-stage4-stop-s and "
                              "--min-concrete-only-stage4-s and "
                              "--skip-concrete-only-after-put-valid must be "
                              "non-negative")
        if args.esbmc_run_timeout > args.timeout:
            raise RQ1RunError("--esbmc-run-timeout must not exceed --timeout")
        validate_jobs(args)
        args.veriput_root = str(veriput_root)
        args.result_root = str(result_root)
        args.ast_cache_root = str(ast_cache_root)
        if args.dry_run:
            print(json.dumps(build_dry_run(args), indent=2, sort_keys=True))
            return 0

        dataset_label, rows = target_rows(veriput_root, args.benchmark,
                                          args.subject_id, args.limit, args.order)
        journal = result_root / dataset_label / "results.jsonl"
        done = _latest_rows(journal) if args.resume and not args.redo else {}
        for target_row in rows:
            if f"gen:veriput:{target_row['subject_id']}" in done:
                print(f"[rq1] skip recorded {target_row['subject_id']}")
        attempted = run_selected_subjects(rows, dataset_label, journal, done, args)
        if attempted == 0:
            write_dataset_manifest(result_root, dataset_label, journal)
        return 0
    except (OSError, RQ1RunError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
