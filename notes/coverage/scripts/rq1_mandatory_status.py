#!/usr/bin/env python3
"""Mandatory status prelude for every RQ1 progress reply.

Use this script before every user-facing progress update.  It calls the hard
ledger, so countdown/resource/subagent/remote/theoretical/actual RQ1 fields are
not reconstructed from memory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LEDGER = HERE / "rq1_no_valid_progress.py"
WATCHDOG = HERE / "rq1_watchdog_status.py"
DISPATCHER = HERE / "rq1_repair_dispatcher.py"
REVIEW_DISPATCHER = HERE / "rq1_review_dispatcher.py"
THEORY_CASES = HERE / "rq1_theory_covered_cases.py"
THEORY_CASES_OUT = Path("/tmp/veriput_rq1_theory_covered_cases.tsv")
DISPATCH_QUEUE = Path("/tmp/veriput_rq1_dispatch_queue.json")
PATCH_REVIEW_SUMMARY = HERE / "rq1_patch_review_summary.py"
MIN_PENDING_REPAIR_ASSIGNMENTS = 10
RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
STATUS_DELTA_CACHE = Path("/tmp/veriput_rq1_mandatory_status_snapshot.json")

SUMMARY_KEYS = (
    "implemented_progress_provisional_gross",
    "implemented_progress_provisional",
    "fully_integrated_progress_gross",
    "fully_integrated_progress",
    "theoretical_progress_gross",
    "theoretical_progress",
    "put_theoretical_progress_gross",
    "put_theoretical_progress",
    "r1r2_theoretical_progress_gross",
    "r1r2_theoretical_progress",
)

MANDATORY_REPORT_FIELDS = (
    "countdown_remaining_h",
    "actual_subjects",
    "actual_valid_cases",
    "actual_put_cases",
    "actual_r1r2_cases",
    "theoretical_progress",
    "implemented_progress_provisional",
    "resource_maximized",
    "active_subagents",
    "running_case_count",
    "theory_manifest_case_count",
)

WORKER_SPECS = (
    ("local-main", Path("/tmp/veriput_rq1_local_state.json"),
     Path("/tmp/veriput_local_progress.jsonl")),
    ("local-extra1", Path("/tmp/veriput_rq1_local_extra_state1.json"),
     Path("/tmp/veriput_local_extra_progress.jsonl")),
    ("local-extra2", Path("/tmp/veriput_rq1_local_extra_state2.json"),
     Path("/tmp/veriput_local_extra2_progress.jsonl")),
    ("local-extra3", Path("/tmp/veriput_rq1_local_extra_state3.json"),
     Path("/tmp/veriput_local_extra3_progress.jsonl")),
    ("local-oom", Path("/tmp/veriput_rq1_local_oom_state.json"),
     Path("/tmp/veriput_local_oom_progress.jsonl")),
    ("remote", Path("/tmp/veriput_rq1_remote_state.json"),
     Path("/tmp/veriput_remote_progress.jsonl")),
)


def _extract_key_values(text: str) -> dict[str, str]:
    out = {}
    for key in SUMMARY_KEYS:
        match = re.search(rf"^{re.escape(key)}=(.+)$", text, re.MULTILINE)
        if match:
            out[key] = match.group(1).strip()
    return out


def _extract_json_section(text: str, heading: str) -> dict:
    marker = f"{heading}:\n"
    start = text.find(marker)
    if start < 0:
        return {}
    brace = text.find("{", start + len(marker))
    if brace < 0:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[brace:index + 1])
                except json.JSONDecodeError:
                    return {}
                return value if isinstance(value, dict) else {}
    return {}


def _extract_countdown(text: str) -> dict:
    marker = "countdown:\n"
    start = text.find(marker)
    if start < 0:
        return {}
    out = {}
    for line in text[start + len(marker):].splitlines():
        if not line.startswith("  "):
            break
        key, sep, value = line.strip().partition("=")
        if sep:
            out[key] = value
    return out


def _json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl_rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _repair_ticket_index(
    path: Path = Path("/tmp/veriput_rq1_repair_tickets.jsonl"),
) -> set[tuple[str, str, str]]:
    index: set[tuple[str, str, str]] = set()
    for row in _jsonl_rows(path):
        bench = str(row.get("bench") or "")
        subject = str(row.get("subject") or "")
        bucket = str(row.get("result_bucket") or row.get("category") or "")
        if bench and subject:
            index.add((bench, subject, bucket))
            index.add((bench, subject, ""))
    return index


def _dispatch_subject_index(
    path: Path = Path("/tmp/veriput_rq1_dispatch_queue.json"),
) -> set[tuple[str, str, str]]:
    doc = _json_file(path)
    index: set[tuple[str, str, str]] = set()
    for assignment in doc.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        for row in assignment.get("subjects") or []:
            if not isinstance(row, dict):
                continue
            bench = str(row.get("bench") or "")
            subject = str(row.get("subject") or "")
            bucket = str(row.get("category") or row.get("result_bucket") or "")
            if bench and subject:
                index.add((bench, subject, bucket))
                index.add((bench, subject, ""))
    return index


def _count_tsv_rows(path: Path) -> int:
    try:
        with path.open() as stream:
            return max(0, sum(1 for _line in stream) - 1)
    except OSError:
        return 0


def _state_case_count(state: dict, rows: list[dict] | None = None) -> int:
    worker = state.get("worker") if isinstance(state.get("worker"), dict) else {}
    try:
        configured = int(state.get("case_count") or worker.get("case_count") or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured:
        return configured
    if rows is None:
        return 0
    cases = {
        (row.get("bench"), row.get("subject"))
        for row in rows
        if row.get("bench") and row.get("subject")
    }
    return len(cases)


def _state_pid(state: dict) -> object:
    worker = state.get("worker") if isinstance(state.get("worker"), dict) else {}
    return state.get("pid") or worker.get("pid")


def _state_memlimit(state: dict) -> object:
    worker = state.get("worker") if isinstance(state.get("worker"), dict) else {}
    return state.get("memlimit_gib") or worker.get("memlimit_gib")


def _state_rss_limit(state: dict) -> object:
    worker = state.get("worker") if isinstance(state.get("worker"), dict) else {}
    return state.get("esbmc_rss_limit_gib") or worker.get("esbmc_rss_limit_gib")


def _pid_alive(pid: object) -> str:
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return "unknown"
    return "true" if Path(f"/proc/{pid_i}").exists() else "false"


def _worker_progress_row(name: str, state_path: Path,
                         progress_path: Path) -> dict:
    state = _json_file(state_path)
    rows = _jsonl_rows(progress_path)
    latest_by_case = {}
    done = set()
    failed = []
    weak = []
    unknown_done = []
    for row in rows:
        key = (row.get("bench"), row.get("subject"))
        if key != (None, None):
            latest_by_case[key] = row
        status = str(row.get("status") or "")
        if status == "done":
            done.add(key)
            has_quality = any(k in row for k in ("valid", "put_valid", "r1r2",
                                                 "bucket"))
            if not has_quality:
                unknown_done.append(row)
                continue
            valid = int(row.get("valid") or 0)
            put_valid = int(row.get("put_valid") or 0)
            r1r2 = int(row.get("r1r2") or 0)
            if not (valid and put_valid and r1r2):
                weak.append(row)
        elif status and status != "running":
            failed.append(row)
    running = [
        row for row in latest_by_case.values()
        if str(row.get("status") or "") == "running"
    ]
    recent = [
        row for row in rows
        if str(row.get("status") or "") in {"done", "failed",
                                            "skipped-lease-held",
                                            "skipped-low-mem",
                                            "killed-over-rss"}
    ][-3:]
    last_mem = next(
        (row.get("mem_available_gib") for row in reversed(rows)
         if row.get("mem_available_gib") is not None),
        None,
    )
    pid = _state_pid(state)
    return {
        "worker": name,
        "state": str(state_path),
        "progress": str(progress_path),
        "pid": pid,
        "alive": _pid_alive(pid) if name != "remote" else "remote-probed",
        "M": len(done),
        "N": _state_case_count(state, rows),
        "running": len(running),
        "failed_or_skipped": len(failed),
        "weak_done": len(weak),
        "unknown_done": len(unknown_done),
        "mem_available_gib_last": last_mem,
        "memlimit_gib": _state_memlimit(state),
        "esbmc_rss_limit_gib": _state_rss_limit(state),
        "running_tail": running[-3:],
        "recent_tail": recent,
    }


def _feedback_status(row: dict, ticket_index: set[tuple[str, str, str]],
                     dispatch_index: set[tuple[str, str, str]]) -> str:
    bench = str(row.get("bench") or "")
    subject = str(row.get("subject") or "")
    bucket = str(row.get("bucket") or row.get("category") or "")
    key = (bench, subject, bucket)
    generic_key = (bench, subject, "")
    status = str(row.get("status") or "")
    valid = int(row.get("valid") or 0)
    put_valid = int(row.get("put_valid") or 0)
    r1r2 = int(row.get("r1r2") or 0)
    has_quality = any(k in row for k in ("valid", "put_valid", "r1r2",
                                         "bucket"))
    below_expected = status not in {"", "running", "done"} or (
        status == "done" and has_quality and
        (valid <= 0 or put_valid <= 0 or r1r2 <= 0))
    if not below_expected:
        return "expected_or_waiting_for_canonical_result"
    ticketed = key in ticket_index or generic_key in ticket_index
    dispatched = key in dispatch_index or generic_key in dispatch_index
    return (
        f"below_expected=true theory_decrement_applied={str(ticketed).lower()} "
        f"repair_dispatch_queued={str(dispatched).lower()}")


def _feedback_flags(row: dict, ticket_index: set[tuple[str, str, str]],
                    dispatch_index: set[tuple[str, str, str]]) -> tuple[bool, bool, bool]:
    bench = str(row.get("bench") or "")
    subject = str(row.get("subject") or "")
    bucket = str(row.get("bucket") or row.get("category") or "")
    status = str(row.get("status") or "")
    valid = int(row.get("valid") or 0)
    put_valid = int(row.get("put_valid") or 0)
    r1r2 = int(row.get("r1r2") or 0)
    has_quality = any(k in row for k in ("valid", "put_valid", "r1r2",
                                         "bucket"))
    below_expected = status not in {"", "running", "done"} or (
        status == "done" and has_quality and
        (valid <= 0 or put_valid <= 0 or r1r2 <= 0))
    key = (bench, subject, bucket)
    generic_key = (bench, subject, "")
    ticketed = key in ticket_index or generic_key in ticket_index
    dispatched = key in dispatch_index or generic_key in dispatch_index
    return below_expected, ticketed, dispatched


def _print_worker_mn_summary(watchdog_stdout: str) -> None:
    print("worker_progress_MN:")
    ticket_index = _repair_ticket_index()
    dispatch_index = _dispatch_subject_index()
    missing_ticket = []
    missing_dispatch = []
    for row in [_worker_progress_row(*spec) for spec in WORKER_SPECS]:
        if row["N"] == 0 and row["M"] == 0 and row["running"] == 0 \
                and row["failed_or_skipped"] == 0:
            continue
        print(
            f"  {row['worker']}={row['M']}/{row['N']}"
            f" running={row['running']}"
            f" failed_or_skipped={row['failed_or_skipped']}"
            f" weak_done={row['weak_done']}"
            f" unknown_done={row['unknown_done']}"
            f" pid={row['pid']} alive={row['alive']}"
            f" mem_last_gib={row['mem_available_gib_last']}"
            f" memlimit_gib={row['memlimit_gib']}"
            f" rss_limit_gib={row['esbmc_rss_limit_gib']}"
        )
        if row["unknown_done"]:
            print(
                "    HARD_WARN=WORKER_DONE_WITHOUT_RESULT_COUNTS;"
                "canonical_result_json_must_drive_feedback_and_dispatch=true"
            )
        if row["weak_done"] or row["failed_or_skipped"]:
            print(
                "    intervention_required=true;"
                "failure_or_weak_result_must_have_repair_ticket=true;"
                "theory_must_be_decremented_until_verified=true"
            )
        for item in row["running_tail"]:
            print(
                f"    running={item.get('bench')}/{item.get('subject')}"
                f" category={item.get('category')} ts={item.get('ts')}")
        for item in row["recent_tail"]:
            below, ticketed, dispatched = _feedback_flags(
                item, ticket_index, dispatch_index)
            if below and not ticketed:
                missing_ticket.append(item)
            if below and not dispatched:
                missing_dispatch.append(item)
            print(
                f"    recent={item.get('bench')}/{item.get('subject')}"
                f" status={item.get('status')} rc={item.get('rc')}"
                f" valid={item.get('valid')} put={item.get('put_valid')}"
                f" r1r2={item.get('r1r2')} ts={item.get('ts')}"
                f" feedback={_feedback_status(item, ticket_index, dispatch_index)}")
    print("failed_case_dispatch_status:")
    print(f"  missing_repair_ticket_count={len(missing_ticket)}")
    print(f"  missing_dispatch_count={len(missing_dispatch)}")
    for label, rows in (("missing_ticket", missing_ticket[-8:]),
                        ("missing_dispatch", missing_dispatch[-8:])):
        for item in rows:
            print(
                f"  {label}={item.get('bench')}/{item.get('subject')}"
                f" status={item.get('status')} bucket={item.get('bucket')}"
                f" valid={item.get('valid')} put={item.get('put_valid')}"
                f" r1r2={item.get('r1r2')}")
    if missing_ticket:
        print(
            "  HARD_ALERT=FAILED_OR_WEAK_CASE_WITHOUT_REPAIR_TICKET;"
            "refresh_worker_interpret_and_repair_dispatch=true")
    if missing_dispatch:
        print(
            "  HARD_ALERT=FAILED_OR_WEAK_CASE_NOT_ASSIGNED_TO_SUBAGENT;"
            "spawn_or_reuse_repair_subagent=true")
    try:
        watchdog = json.loads(watchdog_stdout)
    except json.JSONDecodeError:
        return
    remote = watchdog.get("remote_worker") if isinstance(
        watchdog.get("remote_worker"), dict) else {}
    if remote:
        print(
            "  remote_probe="
            f"running={remote.get('running_case_count')}"
            f" recent_done={remote.get('recent_done_count_in_tail')}"
            f" recent_failed={remote.get('recent_failed_count_in_tail')}"
            f" recent_oom={remote.get('recent_oom_count_in_tail')}"
            f" seconds_since_progress={remote.get('seconds_since_last_progress')}"
            f" leases={remote.get('leases')}")


def _result_counts(path: Path) -> tuple[int, int, int]:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return 0, 0, 0
    row = doc.get("row") if isinstance(doc.get("row"), dict) else doc
    put = doc.get("put") if isinstance(doc.get("put"), dict) else {}

    def as_int(obj: dict, key: str) -> int:
        try:
            return int(obj.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return (
        max(as_int(row, "valid"), as_int(put, "valid")),
        max(as_int(row, "put_valid"), as_int(put, "put_valid")),
        max(
            as_int(row, "valid_put_with_R1_or_R2"),
            as_int(put, "valid_put_with_R1_or_R2"),
        ),
    )


def _print_recent_canonical_results(limit: int = 8) -> None:
    rows = []
    for path in RESULTS_ROOT.glob("*/subjects/*/result.json"):
        text = str(path)
        if any(marker in text for marker in (
                ".redo.", ".superseded.", ".adopted_from_", ".incomplete.")):
            continue
        try:
            rel = path.relative_to(RESULTS_ROOT).parts
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if len(rel) < 4 or rel[1] != "subjects":
            continue
        rows.append((mtime, rel[0], rel[2], path))
    print("recent_canonical_results:")
    for _mtime, bench, subject, path in sorted(rows, reverse=True)[:limit]:
        valid, put_valid, r1r2 = _result_counts(path)
        if valid <= 0:
            bucket = "NO_VALID_AFTER_RUN"
        elif put_valid <= 0:
            bucket = "VALID_NO_PUT"
        elif r1r2 <= 0:
            bucket = "PUT_NO_R1R2"
        else:
            bucket = "VALID_PUT_R1R2"
        print(
            f"  result={bench}/{subject} valid={valid} put={put_valid}"
            f" r1r2={r1r2} bucket={bucket} path={path}")
        if bucket != "VALID_PUT_R1R2":
            print(
                "    intervention_required=true;"
                "theory_or_quality_must_be_decremented=true;"
                "repair_dispatch_required=true")


def _print_feedback_dispatch_summary(ledger_stdout: str,
                                     watchdog_stdout: str) -> None:
    feedback = _extract_json_section(ledger_stdout,
                                     "theoretical_validation_feedback")
    try:
        watchdog = json.loads(watchdog_stdout)
    except json.JSONDecodeError:
        watchdog = {}
    tickets = watchdog.get("repair_tickets_tail")
    if not isinstance(tickets, list):
        tickets = []
    dispatch = watchdog.get("repair_dispatch")
    if not isinstance(dispatch, dict):
        dispatch = {}
    dispatch_file = _json_file(DISPATCH_QUEUE)
    if dispatch_file:
        dispatch = dispatch_file
    review_dispatch = watchdog.get("review_dispatch")
    if not isinstance(review_dispatch, dict):
        review_dispatch = {}
    subagents = watchdog.get("subagents")
    if not isinstance(subagents, dict):
        subagents = {}
    repair_assignment_count = int(dispatch.get("assignment_count") or 0)
    review_assignment_count = int(review_dispatch.get("assignment_count") or 0)
    no_valid_invalidated = int(feedback.get("no_valid_invalidated_count") or 0)
    no_put_debt = int(feedback.get("no_put_quality_debt_count") or 0)
    no_r1r2_debt = int(feedback.get("no_r1r2_quality_debt_count") or 0)
    assigned_keys = set()
    for assignment in dispatch.get("assignments") or []:
        for subject in assignment.get("subjects") or []:
            assigned_keys.add((
                str(subject.get("bench") or ""),
                str(subject.get("subject") or ""),
                str(subject.get("category") or ""),
            ))
    print("failure_feedback_and_dispatch:")
    print(
        "  theory_decrement_applied=true"
        f" no_valid_invalidated={no_valid_invalidated}"
        f" no_put_quality_debt={no_put_debt}"
        f" no_r1r2_quality_debt={no_r1r2_debt}"
    )
    print(
        "  repair_dispatch="
        f"assignment_count={repair_assignment_count}"
        f" min_target={dispatch.get('min_assignment_target')}"
        f" write_owners={dispatch.get('write_owner_count')}"
        f" readonly_root_cause={dispatch.get('readonly_root_cause_count')}"
        f" total_weak_subjects={dispatch.get('total_weak_subjects')}"
        f" assigned_subject_capacity={dispatch.get('assigned_subject_capacity')}"
        f" base_bucket_count={dispatch.get('base_bucket_count')}"
        f" assignment_limit={dispatch.get('assignment_limit')}"
        f" pending_review_count={subagents.get('pending_review_count')}"
        f" active_subagents={subagents.get('active')}")
    active = int(subagents.get("active") or 0)
    if repair_assignment_count > active:
        print(
            "  HARD_ALERT=DISPATCH_PENDING_NOT_FULLY_SPAWNED;"
            f"assignments={repair_assignment_count};active_subagents={active};"
            f"spawn_or_reuse_needed={repair_assignment_count - active}")
    print(
        "  review_dispatch="
        f"assignment_count={review_assignment_count}"
        f" max_patches_per_assignment="
        f"{review_dispatch.get('max_patches_per_assignment')}"
        " rule=review_before_theory_net"
    )
    print(
        "  failed_case_intervention="
        f"repair_assignment_present={repair_assignment_count > 0}"
        f" review_assignment_present={review_assignment_count > 0}"
        f" theory_decrement_present="
        f"{no_valid_invalidated > 0 or no_put_debt > 0 or no_r1r2_debt > 0}"
        " rule=worker_failure_or_weak_result_refreshes_dispatch_immediately")
    if repair_assignment_count:
        print(
            "  HARD_ALERT=REPAIR_ASSIGNMENTS_PENDING;"
            "spawn_or_reuse_subagents_for_repair=true")
    if repair_assignment_count < MIN_PENDING_REPAIR_ASSIGNMENTS:
        print(
            "  HARD_ALERT=REPAIR_ASSIGNMENTS_BELOW_10;"
            "refresh_or_expand_dispatch_queue=true")
    if review_assignment_count:
        print(
            "  HARD_ALERT=REVIEW_ASSIGNMENTS_PENDING;"
            "do_not_count_pending_patches_as_theory=true")
    review_proc = subprocess.run(
        [sys.executable, str(PATCH_REVIEW_SUMMARY)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    review_summary = {}
    if review_proc.returncode == 0:
        try:
            review_summary = json.loads(review_proc.stdout)
        except json.JSONDecodeError:
            review_summary = {}
    counts = review_summary.get("counts") if isinstance(
        review_summary.get("counts"), dict) else {}
    print(
        "  patch_review_summary="
        f"accepted={counts.get('accepted')}"
        f" pending={counts.get('pending')}"
        f" needs_work={counts.get('needs-work')}"
        f" rejected={counts.get('rejected')}"
        " rule=accepted_only_counts_to_net_theory")
    for item in (review_summary.get("buckets") or {}).get("needs-work", [])[:8]:
        print(
            f"    needs_work_patch={item.get('slot')}/{item.get('patch_id')}"
            f" note={str(item.get('note') or '')[:120]}")
    for label, queue in (("repair_assignment", dispatch.get("assignments") or []),
                         ("review_assignment",
                          review_dispatch.get("assignments") or [])):
        if not isinstance(queue, list):
            continue
        for assignment in queue[:10]:
            print(
                f"    {label}={assignment.get('bucket_key')}"
                f" subjects={assignment.get('subject_count')}"
                f"/{assignment.get('bucket_subject_total')}"
                f" patches={assignment.get('patch_count')}"
                f" mode={assignment.get('mode')}"
                f" priority={assignment.get('priority')}"
                f" scope={','.join(assignment.get('write_scope') or [])}")
    for ticket in tickets[-5:]:
        ticket_category = str(ticket.get("result_bucket")
                              or ticket.get("category") or "")
        assigned = (
            str(ticket.get("bench") or ""),
            str(ticket.get("subject") or ""),
            ticket_category,
        ) in assigned_keys
        print(
            f"    ticket={ticket.get('bench')}/{ticket.get('subject')}"
            f" bucket={ticket_category}"
            f" valid={ticket.get('valid')} put={ticket.get('put_valid')}"
            f" r1r2={ticket.get('r1r2')}"
            f" assignment={'assigned' if assigned else 'unassigned'}"
            f" scope={','.join(ticket.get('suggested_write_scope') or [])}")


def _print_theory_manifest_status() -> None:
    proc = subprocess.run(
        [sys.executable, str(THEORY_CASES), "--out", str(THEORY_CASES_OUT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    case_count = _count_tsv_rows(THEORY_CASES_OUT)
    print("theory_worker_manifest:")
    print(f"  returncode={proc.returncode}")
    print(f"  path={THEORY_CASES_OUT}")
    print(f"  case_count={case_count}")
    print("  worker_input_rule=workers_must_use_this_tsv_by_default")
    if case_count <= 0:
        print(
            "  HARD_ALERT=NO_THEORY_COVERED_CASES_FOR_WORKERS;"
            "do_not_start_new_esbmc_workers_without_allow_uncovered_tsv=true")
    if proc.stderr:
        print(f"  stderr_tail={proc.stderr[-1000:]}")


def _print_ledger_summary(stdout: str) -> None:
    actual = _extract_json_section(stdout, "actual_rq1_progress")
    resources = _extract_json_section(stdout, "resource_maximization")
    countdown = _extract_countdown(stdout)
    print("mandatory_progress_summary:")
    for key in (
            "start_utc",
            "deadline_utc",
            "now_utc",
            "elapsed_h",
            "remaining_h",
            "expired",
    ):
        if key in countdown:
            print(f"  countdown_{key}={countdown[key]}")
    for key, value in _extract_key_values(stdout).items():
        print(f"  {key}={value}")
    for key in (
            "subjects",
            "valid_cases",
            "no_valid_cases",
            "put_cases",
            "no_put_cases",
            "r1r2_cases",
            "no_r1r2_cases",
    ):
        if key in actual:
            print(f"  actual_{key}={actual[key]}")
    print(f"  resource_maximized={bool(resources.get('maximized'))}")
    reasons = resources.get("reasons") or []
    if reasons:
        print("  resource_not_maximized_reasons=" + ";".join(map(str, reasons)))


def _required_field_snapshot(ledger_stdout: str,
                             watchdog_stdout: str) -> dict[str, object]:
    actual = _extract_json_section(ledger_stdout, "actual_rq1_progress")
    resources = _extract_json_section(ledger_stdout, "resource_maximization")
    countdown = _extract_countdown(ledger_stdout)
    summary = _extract_key_values(ledger_stdout)
    try:
        watchdog = json.loads(watchdog_stdout)
    except json.JSONDecodeError:
        watchdog = {}
    subagents = watchdog.get("subagents") if isinstance(
        watchdog.get("subagents"), dict) else {}
    active = (
        subagents.get("active_count") or subagents.get("active")
        or subagents.get("active_running_or_leased")
    )
    if active is None:
        match = re.search(r"^\s*active_subagents=(\d+)\s*$",
                          watchdog_stdout,
                          re.MULTILINE)
        if match:
            active = int(match.group(1))
    if active is None:
        active = 0
    local_running, remote_running, _done, _failed, _oom = _worker_counts(watchdog)
    return {
        "countdown_remaining_h": countdown.get("remaining_h"),
        "actual_subjects": actual.get("subjects"),
        "actual_valid_cases": actual.get("valid_cases"),
        "actual_put_cases": actual.get("put_cases"),
        "actual_r1r2_cases": actual.get("r1r2_cases"),
        "theoretical_progress": summary.get("theoretical_progress"),
        "implemented_progress_provisional":
            summary.get("implemented_progress_provisional"),
        "resource_maximized": resources.get("maximized"),
        "active_subagents": active,
        "running_case_count": int(local_running or 0) + int(remote_running or 0),
        "theory_manifest_case_count": _count_tsv_rows(THEORY_CASES_OUT),
    }


def _print_report_completeness(ledger_stdout: str,
                               watchdog_stdout: str) -> None:
    snapshot = _required_field_snapshot(ledger_stdout, watchdog_stdout)
    missing = [
        key for key in MANDATORY_REPORT_FIELDS
        if snapshot.get(key) is None or snapshot.get(key) == ""
    ]
    print("mandatory_report_completeness:")
    for key in MANDATORY_REPORT_FIELDS:
        print(f"  {key}={snapshot.get(key)}")
    print(f"  missing_count={len(missing)}")
    if missing:
        print(
            "  HARD_ALERT=STATUS_FIELD_MISSING;"
            f"missing={','.join(missing)}")


def _status_snapshot(ledger_stdout: str, watchdog_stdout: str) -> dict:
    snapshot = _required_field_snapshot(ledger_stdout, watchdog_stdout)
    feedback = _extract_json_section(ledger_stdout,
                                     "theoretical_validation_feedback")
    try:
        watchdog = json.loads(watchdog_stdout)
    except json.JSONDecodeError:
        watchdog = {}
    dispatch = _json_file(DISPATCH_QUEUE)
    review_proc = subprocess.run(
        [sys.executable, str(PATCH_REVIEW_SUMMARY)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    try:
        review = json.loads(review_proc.stdout) if review_proc.returncode == 0 else {}
    except json.JSONDecodeError:
        review = {}
    counts = review.get("counts") if isinstance(review.get("counts"), dict) else {}
    snapshot.update({
        "actual_no_valid_cases": _extract_json_section(
            ledger_stdout, "actual_rq1_progress").get("no_valid_cases"),
        "no_valid_invalidated":
            feedback.get("no_valid_invalidated_count"),
        "no_put_quality_debt":
            feedback.get("no_put_quality_debt_count"),
        "no_r1r2_quality_debt":
            feedback.get("no_r1r2_quality_debt_count"),
        "repair_assignment_count":
            dispatch.get("assignment_count") or len(dispatch.get("assignments") or []),
        "review_pending": counts.get("pending"),
        "review_accepted": counts.get("accepted"),
        "review_needs_work": counts.get("needs-work"),
        "review_rejected": counts.get("rejected"),
    })
    local_running, remote_running, recent_done, recent_failed, recent_oom = \
        _worker_counts(watchdog)
    snapshot.update({
        "local_running_case_count": local_running,
        "remote_running_case_count": remote_running,
        "recent_done_count_in_tail": recent_done,
        "recent_failed_count_in_tail": recent_failed,
        "recent_oom_count_in_tail": recent_oom,
    })
    return snapshot


def _print_status_delta(ledger_stdout: str, watchdog_stdout: str) -> None:
    current = _status_snapshot(ledger_stdout, watchdog_stdout)
    previous = _json_file(STATUS_DELTA_CACHE)
    changed = {
        key: {
            "old": previous.get(key),
            "new": value,
        }
        for key, value in current.items()
        if previous.get(key) != value
    }
    STATUS_DELTA_CACHE.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n")
    print("mandatory_status_delta:")
    print(f"  cache={STATUS_DELTA_CACHE}")
    print(f"  changed_count={len(changed)}")
    if changed:
        print("  status_unchanged=false")
        for key in sorted(changed):
            print(
                f"  changed={key} old={changed[key]['old']} "
                f"new={changed[key]['new']}")
    else:
        print("  status_unchanged=true")
        print("  full_status_suppressed=true")
        print(
            "  rule=do_not_repeat_fixed_status_when_no_tracked_number_changed")


def _hard_gate_exit_code(watchdog_stdout: str) -> int:
    """Return non-zero when mandatory resource gates are violated."""
    try:
        watchdog = json.loads(watchdog_stdout)
    except json.JSONDecodeError:
        return 3
    subagents = watchdog.get("subagents") if isinstance(
        watchdog.get("subagents"), dict) else {}
    active = int(
        subagents.get("active_count")
        or subagents.get("active")
        or subagents.get("active_running_or_leased")
        or len(subagents.get("active_details") or [])
        or 0)
    minimum = int(subagents.get("min_active_required") or 5)
    dispatch = _json_file(DISPATCH_QUEUE)
    assignments = int(dispatch.get("assignment_count") or len(
        dispatch.get("assignments") or []) or 0)
    if active < minimum and assignments > 0:
        print("mandatory_hard_fail:")
        print("  exit_code=2")
        print(
            "  reason=ACTIVE_SUBAGENTS_BELOW_MIN_WITH_PENDING_DISPATCH;"
            f"active={active};minimum={minimum};assignments={assignments}")
        print(
            "  required_action=spawn_or_reuse_subagents_before_reporting_progress")
        return 2
    if _count_tsv_rows(THEORY_CASES_OUT) <= 0:
        print("mandatory_hard_fail:")
        print("  exit_code=4")
        print(
            "  reason=NO_THEORY_COVERED_CASES_FOR_WORKERS;"
            "new_workers_must_not_start=true")
        return 4
    return 0


def _worker_counts(doc: dict) -> tuple[int, int, int, int, int]:
    local_worker = doc.get("local_worker") if isinstance(
        doc.get("local_worker"), dict) else {}
    remote_worker = doc.get("remote_worker") if isinstance(
        doc.get("remote_worker"), dict) else {}
    progress = doc.get("worker_progress") if isinstance(
        doc.get("worker_progress"), dict) else {}
    local_running = int(local_worker.get("running_case_count") or 0)
    remote_running = int(remote_worker.get("running_case_count") or 0)
    recent_done = int(progress.get("recent_done_count_in_tail") or 0)
    recent_done += int(remote_worker.get("recent_done_count_in_tail") or 0)
    recent_failed = int(progress.get("recent_failed_count_in_tail") or 0)
    recent_failed += int(remote_worker.get("recent_failed_count_in_tail") or 0)
    recent_oom = int(progress.get("recent_oom_count_in_tail") or 0)
    recent_oom += int(remote_worker.get("recent_oom_count_in_tail") or 0)
    return local_running, remote_running, recent_done, recent_failed, recent_oom


def _print_watchdog_hard_alerts(stdout: str) -> None:
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError:
        print("mandatory_hard_alerts:")
        print("  watchdog_json_parse_failed=true")
        return
    subagents = doc.get("subagents") if isinstance(doc.get("subagents"), dict) else {}
    remote_worker = doc.get("remote_worker") if isinstance(doc.get("remote_worker"), dict) else {}
    progress = doc.get("worker_progress") if isinstance(doc.get("worker_progress"), dict) else {}
    active = int(
        subagents.get("active_count")
        or subagents.get("active")
        or subagents.get("active_running_or_leased")
        or len(subagents.get("active_details") or [])
        or 0)
    minimum = int(subagents.get("min_active_required") or 5)
    local_running, remote_running, recent_done_count, recent_failed_count, \
        recent_oom_count = _worker_counts(doc)
    total_running = max(local_running, 0) + max(remote_running, 0)
    print("mandatory_hard_alerts:")
    print(f"  active_subagents={active}")
    print(f"  min_active_subagents={minimum}")
    if active < minimum:
        print(
            "  HARD_ALERT=ACTIVE_SUBAGENTS_BELOW_MIN;"
            f"spawn_or_reuse_now={minimum - active}"
        )
    else:
        print("  active_subagents_below_min=false")
        if active == minimum:
            print(
                "  HARD_WARN=ACTIVE_SUBAGENTS_AT_MINIMUM_MARGIN;"
                "prepare_more_assignments_now=true")
    print(f"  running_case_count={total_running}")
    print(f"  local_running_case_count={local_running}")
    print(f"  remote_running_case_count={remote_running}")
    if total_running <= 0:
        print("  HARD_ALERT=NO_CASES_RUNNING_ON_LOCAL_OR_REMOTE")
    recent_done = progress.get("recent_done_tail") or []
    recent_failed = progress.get("recent_failed_tail") or []
    recent_weak = progress.get("recent_weak_tail") or []
    print(f"  recent_done_count_in_tail={recent_done_count}")
    print(f"  recent_failed_count_in_tail={recent_failed_count}")
    print(f"  recent_oom_count_in_tail={recent_oom_count}")
    print(f"  recent_weak_count_in_tail={len(recent_weak)}")
    for label, rows in (
            ("recent_done", recent_done[-3:]),
            ("recent_failed", recent_failed[-3:]),
            ("recent_weak", recent_weak[-3:])):
        for row in rows:
            print(
                f"  {label}={row.get('bench')}/{row.get('subject')}"
                f" status={row.get('status')} valid={row.get('valid')}"
                f" put={row.get('put_valid')} r1r2={row.get('r1r2')}"
            )
    for agent in (subagents.get("active_details") or [])[:12]:
        print(
            f"  active_subagent={agent.get('agent_id')}"
            f" slot={agent.get('slot')}"
            f" task={agent.get('task')}"
            f" scope={','.join(agent.get('write_scope') or [])}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--applied", default="")
    parser.add_argument("--no-remote-probe", action="store_true")
    parser.add_argument("--no-hard-fail", action="store_true")
    args = parser.parse_args()

    print("fixed_rq1_report_format=v2")
    print(
        "fixed_rq1_report_sections="
        "countdown,actual_rq1,theory,resources,subagents,workers,"
        "failure_feedback,dispatch")
    dispatch_proc = subprocess.run(
        [sys.executable, str(DISPATCHER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    print("dispatch_refresh_status:")
    print(f"  returncode={dispatch_proc.returncode}")
    if dispatch_proc.stderr:
        print(f"  stderr_tail={dispatch_proc.stderr[-1000:]}")
    review_proc = subprocess.run(
        [sys.executable, str(REVIEW_DISPATCHER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    print("review_dispatch_refresh_status:")
    print(f"  returncode={review_proc.returncode}")
    if review_proc.stderr:
        print(f"  stderr_tail={review_proc.stderr[-1000:]}")

    cmd = [sys.executable, str(LEDGER), "--init-subagents"]
    if args.applied:
        cmd.extend(["--applied", args.applied])
    if args.no_remote_probe:
        cmd.append("--no-remote-probe")
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    _print_ledger_summary(proc.stdout)
    if proc.stderr:
        print(f"ledger_stderr_tail={proc.stderr[-1000:]}")
    print(proc.stdout, end="")
    print("watchdog_status:")
    watchdog_proc = subprocess.run(
        [sys.executable, str(WATCHDOG)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    print(watchdog_proc.stdout, end="")
    if watchdog_proc.stderr:
        print(f"watchdog_stderr_tail={watchdog_proc.stderr[-1000:]}")
    _print_watchdog_hard_alerts(watchdog_proc.stdout)
    _print_worker_mn_summary(watchdog_proc.stdout)
    _print_recent_canonical_results()
    _print_theory_manifest_status()
    _print_feedback_dispatch_summary(proc.stdout, watchdog_proc.stdout)
    _print_report_completeness(proc.stdout, watchdog_proc.stdout)
    _print_status_delta(proc.stdout, watchdog_proc.stdout)
    if proc.returncode != 0:
        return proc.returncode
    if watchdog_proc.returncode != 0:
        return watchdog_proc.returncode
    if args.no_hard_fail:
        return 0
    return _hard_gate_exit_code(watchdog_proc.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
