#!/usr/bin/env python3
"""Mandatory status prelude for every RQ1 progress reply.

Use this script before every user-facing progress update.  It calls the hard
ledger, so countdown/resource/subagent/remote/theoretical/actual RQ1 fields are
not reconstructed from memory.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LEDGER = HERE / "rq1_no_valid_progress.py"
WATCHDOG = HERE / "rq1_watchdog_status.py"
DISPATCHER = HERE / "rq1_repair_dispatcher.py"
REVIEW_DISPATCHER = HERE / "rq1_review_dispatcher.py"

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


def _print_ledger_summary(stdout: str) -> None:
    actual = _extract_json_section(stdout, "actual_rq1_progress")
    resources = _extract_json_section(stdout, "resource_maximization")
    print("mandatory_progress_summary:")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--applied", default="")
    parser.add_argument("--no-remote-probe", action="store_true")
    args = parser.parse_args()

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
    if proc.returncode != 0:
        return proc.returncode
    return watchdog_proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
