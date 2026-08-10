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
DISPATCH_QUEUE = Path("/tmp/veriput_rq1_dispatch_queue.json")
MIN_PENDING_REPAIR_ASSIGNMENTS = 10

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


def _state_case_count(state: dict) -> int:
    worker = state.get("worker") if isinstance(state.get("worker"), dict) else {}
    try:
        return int(state.get("case_count") or worker.get("case_count") or 0)
    except (TypeError, ValueError):
        return 0


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
        "N": _state_case_count(state),
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


def _print_worker_mn_summary(watchdog_stdout: str) -> None:
    print("worker_progress_MN:")
    ticket_index = _repair_ticket_index()
    dispatch_index = _dispatch_subject_index()
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
            print(
                f"    recent={item.get('bench')}/{item.get('subject')}"
                f" status={item.get('status')} rc={item.get('rc')}"
                f" valid={item.get('valid')} put={item.get('put_valid')}"
                f" r1r2={item.get('r1r2')} ts={item.get('ts')}"
                f" feedback={_feedback_status(item, ticket_index, dispatch_index)}")
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
    _print_feedback_dispatch_summary(proc.stdout, watchdog_proc.stdout)
    if proc.returncode != 0:
        return proc.returncode
    return watchdog_proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
