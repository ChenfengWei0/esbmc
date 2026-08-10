#!/usr/bin/env python3
"""Launch a remote RQ1 worker loop for VeriPUT.

This is hard automation for the resource rule: local Codex edits code; the
remote host continuously runs selected RQ1/ESBMC cases and writes Results.  The
local agent must not wait idle for this worker.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
import subprocess
import time
from collections import defaultdict
from pathlib import Path


DEFAULT_TSV = Path("/tmp/veriput_no_valid_root_causes.tsv")
THEORY_TSV_MARKER = "theory_patch_id"
CE_TSV_MARKER = "ce_collection_id"
DEFAULT_REMOTE_HOST = "invmut-w2"
DEFAULT_REMOTE_ESBMC = Path("/tmp/veriput_esbmc_remote")
DEFAULT_REMOTE_VERIPUT = Path("/tmp/veriput_Ver iPUT_remote".replace(" ", ""))
DEFAULT_REMOTE_STATE = Path("/tmp/veriput_rq1_remote_state.json")
DEFAULT_LOCAL_RESULTS = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_LOCAL_VERIPUT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_REMOTE_PROGRESS = "/tmp/veriput_remote_progress.jsonl"
DEFAULT_REMOTE_LEASE_DIR = "/tmp/veriput_rq1_case_leases.d"
DEFAULT_REMOTE_WATCHDOG_SLEEP_S = 10
DEFAULT_REMOTE_STALE_PROC_S = 900
DEFAULT_REMOTE_LEASE_STALE_S = 1200
DEFAULT_REMOTE_LEASE_REFRESH_S = 30
DEFAULT_REMOTE_ADOPT_OUT = Path("/tmp/veriput_remote_adopt.json")
DEFAULT_REMOTE_INTERPRET_OUT = Path("/tmp/veriput_remote_interpret.json")
DEFAULT_REMOTE_BUILD_LOG = Path("/tmp/veriput_remote_build.log")
DEFAULT_SYNC_CODE_LOG = Path("/tmp/veriput_remote_sync_code.log")
DEFAULT_SYNC_VERIPUT_LOG = Path("/tmp/veriput_remote_sync_veriput.log")
DEFAULT_PULL_STATE = Path("/tmp/veriput_remote_pull_state.json")
SAFE_WORKER_GENERATION = "memory-progress-watchdog-v2"
REMOTE_LEASE_ACQUIRED = 0
REMOTE_LEASE_ACTIVE_HELD = 1
REMOTE_LEASE_TERMINAL = 2


PRIORITY_CATEGORIES = (
    "RUNNER_STAGE2_CONSUMED_STAGE4_BUDGET",
    "STAGE2_TIMEOUT_NO_STAGE4_MATERIALIZATION",
    "RUNNER_FIRST_UNITS_CONSUMED_SUBJECT_BUDGET",
    "RUNNER_STAGE2_NO_OUTPUT_EARLY_STOP",
    "RUNNER_EARLY_STOP_AFTER_NO_CANDIDATE_PREFIX",
    "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "CERTIFY_COUNTEREXAMPLE_REJECTED_NOT_CERTIFIED",
)


def remote_capacity(
    host: str,
    memlimit_gib: float,
    jobs_per_case: int,
    reserve_cores: int,
    reserve_mem_gib: float,
    max_case_parallel: int,
) -> dict:
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            "nproc; awk '/MemAvailable/{printf \"%.3f\\n\", $2/1024/1024}' /proc/meminfo",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    try:
        nproc = int(lines[0])
    except (IndexError, ValueError):
        nproc = 1
    try:
        mem_gib = float(lines[1])
    except (IndexError, ValueError):
        mem_gib = memlimit_gib
    usable_cores = max(1, nproc - max(0, reserve_cores))
    usable_mem = max(0.1, mem_gib - max(0.0, reserve_mem_gib))
    by_cpu = max(1, usable_cores // max(1, jobs_per_case))
    by_mem = max(1, int(usable_mem // max(0.1, memlimit_gib)))
    case_parallel = max(1, min(by_cpu, by_mem))
    if max_case_parallel > 0:
        case_parallel = min(case_parallel, max_case_parallel)
    return {
        "host": host,
        "reachable": proc.returncode == 0,
        "nproc": nproc,
        "mem_available_gib": round(mem_gib, 3),
        "memlimit_gib": memlimit_gib,
        "jobs_per_case": jobs_per_case,
        "reserve_cores": reserve_cores,
        "reserve_mem_gib": reserve_mem_gib,
        "max_case_parallel": max_case_parallel,
        "usable_cores": usable_cores,
        "usable_mem_gib": round(usable_mem, 3),
        "by_cpu": by_cpu,
        "by_mem": by_mem,
        "case_parallel": case_parallel,
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip(),
    }


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def run_logged_command(
    label: str,
    cmd: list[str],
    log_path: Path,
    state: dict,
    state_path: Path,
) -> dict:
    started_ts = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        entry = {
            "command": shell_join(cmd),
            "log": str(log_path),
            "pid": proc.pid,
            "started_ts": started_ts,
            "status": "running",
        }
        state[label] = entry
        write_state(state_path, state)
        returncode = proc.wait()
    entry.update({
        "done_ts": time.time(),
        "returncode": returncode,
        "status": "done" if returncode == 0 else "failed",
    })
    state[label] = entry
    write_state(state_path, state)
    return entry


def load_cases(tsv_path: Path, categories: set[str], limit: int,
               allow_uncovered_tsv: bool, ce_collection_only: bool) -> list[dict]:
    cases = []
    with tsv_path.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fieldnames = set(reader.fieldnames or [])
        required_marker = CE_TSV_MARKER if ce_collection_only else THEORY_TSV_MARKER
        if not allow_uncovered_tsv and required_marker not in fieldnames:
            raise SystemExit(
                f"{tsv_path} lacks required manifest marker {required_marker!r}")
        for row in reader:
            if categories and row.get("category") not in categories:
                continue
            if not allow_uncovered_tsv and not row.get(required_marker):
                continue
            if row.get("bench") == "peer182" and "contract080" not in row.get("subject", ""):
                continue
            cases.append(row)
            if limit > 0 and len(cases) >= limit:
                break
    return cases


def write_remote_case_file(host: str, cases: list[dict], remote_path: str) -> None:
    payload = "\n".join(
        "\t".join((case["bench"], case["subject"], case["category"]))
        for case in cases
    ) + "\n"
    proc = subprocess.run(
        ["ssh", host, f"cat > {shlex.quote(remote_path)}"],
        input=payload,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"failed to write remote case file: rc={proc.returncode}")


def stop_existing_remote_worker(host: str) -> dict:
    """Stop old remote RQ1/ESBMC workers before launching a safe generation."""
    script = r"""
set +e
for pid_file in /tmp/veriput_remote_worker.pid; do
  if [ -s "$pid_file" ]; then
    pid="$(cat "$pid_file" 2>/dev/null)"
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null
  fi
done
pkill -TERM -f '[r]q1_veriput_run.py|[c]ertify_all.py|[p]ut_all.py|[s]olidity_path_generalise.py|[b]uild/src/esbmc/esbmc|[v]eriput_remote_worker' 2>/dev/null
sleep 2
pkill -KILL -f '[r]q1_veriput_run.py|[c]ertify_all.py|[p]ut_all.py|[s]olidity_path_generalise.py|[b]uild/src/esbmc/esbmc|[v]eriput_remote_worker' 2>/dev/null
pgrep -af '[r]q1_veriput_run.py|[c]ertify_all.py|[p]ut_all.py|[s]olidity_path_generalise.py|[b]uild/src/esbmc/esbmc|[v]eriput_remote_worker' || true
"""
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "stopped": proc.returncode == 0,
        "stopped_ts": time.time(),
    }


def start_remote_worker(args, cases: list[dict]) -> dict:
    remote_cases = "/tmp/veriput_remote_cases.tsv"
    remote_log = "/tmp/veriput_remote_worker.log"
    remote_pid = "/tmp/veriput_remote_worker.pid"
    remote_progress = DEFAULT_REMOTE_PROGRESS
    write_remote_case_file(args.host, cases, remote_cases)

    cap = remote_capacity(
        args.host,
        args.memlimit_gib,
        args.jobs,
        args.reserve_cores,
        args.reserve_mem_gib,
        args.max_case_parallel,
    )
    capacity_parallel = max(1, int(cap["case_parallel"]))
    if args.case_parallel > 0:
        case_parallel = int(args.case_parallel)
        if not args.allow_unsafe_case_parallel:
            case_parallel = min(case_parallel, capacity_parallel)
    else:
        case_parallel = capacity_parallel
    case_parallel = max(1, case_parallel)
    max_proc_runtime_s = max(
        60,
        int(args.timeout + args.wrapper_grace + 2 * args.forge_timeout
            + args.remote_stale_proc_s))
    runner_memlimit_gib = int(math.ceil(float(args.memlimit_gib)))
    esbmc_rss_limit_kib = int(float(args.esbmc_rss_limit_gib) * 1024 * 1024)
    ce_collection_arg = (
        "      --ce-collection-only \\\n"
        if args.ce_collection_only else "")

    worker = f"""
set -euo pipefail
cd {shlex.quote(str(args.remote_esbmc))}
export VERIPUT_ROOT={shlex.quote(str(args.remote_veriput))}
export RESULT_ROOT={shlex.quote(str(args.remote_veriput))}/Results/RQ1/VeriPUT
export ESBMC_BIN={shlex.quote(str(args.remote_esbmc))}/build/src/esbmc/esbmc
export LD_LIBRARY_PATH=/home/administrator/veriput_esbmc/local-libs:/home/administrator/veriput_esbmc/lib:${{LD_LIBRARY_PATH:-}}
REMOTE_LOG={shlex.quote(remote_log)}
REMOTE_PROGRESS={shlex.quote(remote_progress)}
REMOTE_LEASE_DIR={shlex.quote(DEFAULT_REMOTE_LEASE_DIR)}
MIN_MEM_GIB={int(math.floor(float(args.remote_min_mem_gib)))}
ESBMC_RSS_LIMIT_KIB={esbmc_rss_limit_kib}
MAX_PROC_RUNTIME_S={max_proc_runtime_s}
WATCHDOG_SLEEP_S={int(args.remote_watchdog_sleep_s)}
LEASE_STALE_S={int(args.remote_lease_stale_s)}
LEASE_REFRESH_S={int(args.remote_lease_refresh_s)}
RESET_LEASES_ON_START={1 if args.reset_remote_leases else 0}
case_parallel={case_parallel}
LEASE_ACQUIRED={REMOTE_LEASE_ACQUIRED}
LEASE_ACTIVE_HELD={REMOTE_LEASE_ACTIVE_HELD}
LEASE_TERMINAL={REMOTE_LEASE_TERMINAL}
export REMOTE_LOG REMOTE_PROGRESS REMOTE_LEASE_DIR
export MIN_MEM_GIB ESBMC_RSS_LIMIT_KIB MAX_PROC_RUNTIME_S
export WATCHDOG_SLEEP_S LEASE_STALE_S LEASE_REFRESH_S
export LEASE_ACQUIRED LEASE_ACTIVE_HELD LEASE_TERMINAL
mem_available_gib() {{
  awk '/MemAvailable/{{printf "%.0f\\n", $2/1024/1024}}' /proc/meminfo
}}
emit_progress() {{
  bench="$1"; subject="$2"; category="$3"; status="$4"; rc="$5"; note="$6"
  printf '{{"ts":"%s","bench":"%s","subject":"%s","category":"%s","status":"%s","rc":%s,"note":"%s","mem_available_gib":%s}}\\n' \\
    "$(date -Is)" "$bench" "$subject" "$category" "$status" "$rc" "$note" "$(mem_available_gib)" >> "$REMOTE_PROGRESS"
}}
emit_result_progress() {{
  bench="$1"; subject="$2"; category="$3"; status="$4"; rc="$5"; note="$6"
  subject_dir="$RESULT_ROOT/$bench/subjects/$subject"
  key="$(lease_key "$bench" "$subject")"
  interpret_out="/tmp/veriput_remote_interpret_$key.json"
  python3 notes/coverage/scripts/rq1_esbmc_result_interpret.py \\
    --results-root "$RESULT_ROOT" \\
    --subject-dir "$subject_dir" \\
    --out "$interpret_out" >> "$REMOTE_LOG" 2>&1
  interpret_rc=$?
  python3 - "$REMOTE_PROGRESS" "$bench" "$subject" "$category" "$status" "$rc" "$note" "$interpret_out" "$subject_dir" "$(mem_available_gib)" "$interpret_rc" <<PY
import json
import sys
import time

progress, bench, subject, category, status, rc, note = sys.argv[1:8]
interpret_out, subject_dir, mem_available_gib, interpret_rc = sys.argv[8:12]
row = {{}}
try:
    doc = json.load(open(interpret_out))
    rows = doc.get("rows") if isinstance(doc.get("rows"), list) else []
    if rows and isinstance(rows[0], dict):
        row = rows[0]
except Exception:
    row = {{}}
payload = {{
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "bench": bench,
    "subject": subject,
    "category": category,
    "status": status,
    "rc": int(rc),
    "note": note,
    "mem_available_gib": int(float(mem_available_gib)),
    "interpret_rc": int(interpret_rc),
    "bucket": row.get("bucket"),
    "valid": int(row.get("valid") or 0),
    "put_valid": int(row.get("put_valid") or 0),
    "r1r2": int(row.get("r1r2") or 0),
    "subject_dir": row.get("subject_dir") or subject_dir,
}}
with open(progress, "a") as stream:
    stream.write(json.dumps(payload, sort_keys=True) + "\\n")
PY
}}
lease_key() {{
  printf '%s__%s' "$1" "$2" | tr -c 'A-Za-z0-9_.=-' '_'
}}
acquire_case_lease() {{
  mkdir -p "$REMOTE_LEASE_DIR"
  key="$(lease_key "$1" "$2")"
  lease="$REMOTE_LEASE_DIR/$key"
  for _attempt in 1 2; do
    if mkdir "$lease" 2>/dev/null; then
      now_epoch="$(date +%s)"
      printf '%s\\n' "$now_epoch" > "$lease/updated_ts"
      printf '{{"ts":"%s","updated_ts":%s,"bench":"%s","subject":"%s","category":"%s","status":"running","pid":%s}}\\n' \\
        "$(date -Is)" "$now_epoch" "$1" "$2" "$3" "$$" > "$lease/state.json"
      return "$LEASE_ACQUIRED"
    fi
    status="$(sed -n 's/.*"status":"\\([^"]*\\)".*/\\1/p' "$lease/state.json" 2>/dev/null | head -1)"
    if [ "$status" = "done" ] || [ "$status" = "failed" ] || [ "$status" = "killed-over-rss" ]; then
      return "$LEASE_TERMINAL"
    fi
    updated="$(cat "$lease/updated_ts" 2>/dev/null || stat -c %Y "$lease" 2>/dev/null || echo 0)"
    now_epoch="$(date +%s)"
    age=$((now_epoch - updated))
    if [ "$status" != "done" ] && [ "$age" -ge "$LEASE_STALE_S" ]; then
      echo "[remote-rq1] $(date -Is) reclaim stale lease bench=$1 subject=$2 status=${{status:-unknown}} age_s=$age stale_s=$LEASE_STALE_S" >> "$REMOTE_LOG"
      rm -rf "$lease"
      continue
    fi
    break
  done
  return "$LEASE_ACTIVE_HELD"
}}
refresh_case_lease() {{
  key="$(lease_key "$1" "$2")"
  lease="$REMOTE_LEASE_DIR/$key"
  [ -d "$lease" ] || return 0
  now_epoch="$(date +%s)"
  printf '%s\\n' "$now_epoch" > "$lease/updated_ts"
  printf '{{"ts":"%s","updated_ts":%s,"bench":"%s","subject":"%s","category":"%s","status":"running","pid":%s}}\\n' \\
    "$(date -Is)" "$now_epoch" "$1" "$2" "$3" "$$" > "$lease/state.json"
}}
lease_heartbeat() {{
  while true; do
    sleep "$LEASE_REFRESH_S"
    refresh_case_lease "$1" "$2" "$3"
  done
}}
finish_case_lease() {{
  key="$(lease_key "$1" "$2")"
  lease="$REMOTE_LEASE_DIR/$key"
  mkdir -p "$lease"
  now_epoch="$(date +%s)"
  printf '%s\\n' "$now_epoch" > "$lease/updated_ts"
  printf '{{"ts":"%s","updated_ts":%s,"bench":"%s","subject":"%s","category":"%s","status":"%s","rc":%s,"pid":%s}}\\n' \\
    "$(date -Is)" "$now_epoch" "$1" "$2" "$3" "$4" "$5" "$$" > "$lease/state.json"
}}
kill_over_budget_esbmc() {{
  ps -eo pid=,rss=,comm= | awk -v limit="$ESBMC_RSS_LIMIT_KIB" '$3 ~ /esbmc/ && $2 > limit {{print $1}}' | while read -r pid; do
    [ -n "$pid" ] || continue
    echo "[remote-rq1] $(date -Is) kill esbmc pid=$pid over_rss_limit_kib=$ESBMC_RSS_LIMIT_KIB" >> "$REMOTE_LOG"
    kill -TERM "$pid" 2>/dev/null || true
    sleep 2
    kill -KILL "$pid" 2>/dev/null || true
  done
}}
kill_stale_rq1_processes() {{
  ps -eo pid=,etimes=,args= | awk -v limit="$MAX_PROC_RUNTIME_S" '
    $2 > limit && $0 ~ /rq1_veriput_run.py|certify_all.py|put_all.py|esbmc/ {{print $1}}
  ' | while read -r pid; do
    [ -n "$pid" ] || continue
    echo "[remote-rq1] $(date -Is) kill stale rq1/esbmc pid=$pid runtime_limit_s=$MAX_PROC_RUNTIME_S" >> "$REMOTE_LOG"
    kill -TERM "$pid" 2>/dev/null || true
    sleep 2
    kill -KILL "$pid" 2>/dev/null || true
  done
}}
remote_watchdog() {{
  while true; do
    kill_over_budget_esbmc
    kill_stale_rq1_processes
    mem_now="$(mem_available_gib)"
    if [ "$mem_now" -lt "$MIN_MEM_GIB" ]; then
      echo "[remote-rq1] $(date -Is) watchdog low_mem_gib=$mem_now min=$MIN_MEM_GIB; pausing new work" >> "$REMOTE_LOG"
    fi
    sleep "$WATCHDOG_SLEEP_S"
  done
}}
export -f mem_available_gib emit_progress emit_result_progress
export -f kill_over_budget_esbmc
export -f kill_stale_rq1_processes lease_key acquire_case_lease
export -f refresh_case_lease lease_heartbeat finish_case_lease
if [ "$RESET_LEASES_ON_START" -eq 1 ]; then
  rm -rf "$REMOTE_LEASE_DIR"
fi
mkdir -p "$REMOTE_LEASE_DIR"
remote_watchdog &
watchdog_pid=$!
trap 'kill "$watchdog_pid" 2>/dev/null || true' EXIT
round=0
while true; do
  round=$((round + 1))
  kill_over_budget_esbmc
  mem_now="$(mem_available_gib)"
  if [ "$mem_now" -lt "$MIN_MEM_GIB" ]; then
    echo "[remote-rq1] $(date -Is) round $round paused low_mem_gib=$mem_now min=$MIN_MEM_GIB" >> "$REMOTE_LOG"
    sleep {int(args.low_mem_sleep_s)}
    continue
  fi
  round_file="$(mktemp /tmp/veriput_remote_round_${{round}}.XXXXXX.jsonl)"
  export round_file
  echo "[remote-rq1] $(date -Is) round $round begin parallel=$case_parallel mem_gib=$mem_now min=$MIN_MEM_GIB" >> "$REMOTE_LOG"
  cat {shlex.quote(remote_cases)} | xargs -r -P "$case_parallel" -n 3 bash -lc '
    bench="$1"
    subject="$2"
    category="$3"
    [ -n "$bench" ] || exit 0
    mem_now="$(awk "/MemAvailable/{{printf \\"%.0f\\\\n\\", \\$2/1024/1024}}" /proc/meminfo)"
    if [ "$mem_now" -lt "$MIN_MEM_GIB" ]; then
      emit_progress "$bench" "$subject" "$category" "skipped-low-mem" 125 "preflight"
     echo "[remote-rq1] $(date -Is) skip $bench $subject low_mem_gib=$mem_now min=$MIN_MEM_GIB" >> "$REMOTE_LOG"
      exit 0
    fi
    acquire_case_lease "$bench" "$subject" "$category"
    lease_rc=$?
    if [ "$lease_rc" -eq "$LEASE_TERMINAL" ]; then
      printf "%s\\n" "{{\\"status\\":\\"terminal\\",\\"bench\\":\\"$bench\\",\\"subject\\":\\"$subject\\"}}" >> "$round_file"
      emit_progress "$bench" "$subject" "$category" "skipped-already-terminal" 0 "remote-lease-terminal"
      echo "[remote-rq1] $(date -Is) skip $bench $subject already_terminal" >> "$REMOTE_LOG"
      exit 0
    fi
    if [ "$lease_rc" -ne "$LEASE_ACQUIRED" ]; then
      printf "%s\\n" "{{\\"status\\":\\"held\\",\\"bench\\":\\"$bench\\",\\"subject\\":\\"$subject\\"}}" >> "$round_file"
      emit_progress "$bench" "$subject" "$category" "skipped-lease-held" 126 "remote-lease-active"
      echo "[remote-rq1] $(date -Is) skip $bench $subject active_lease_held" >> "$REMOTE_LOG"
      exit 0
    fi
    printf "%s\\n" "{{\\"status\\":\\"started\\",\\"bench\\":\\"$bench\\",\\"subject\\":\\"$subject\\"}}" >> "$round_file"
    emit_progress "$bench" "$subject" "$category" "running" 0 "start"
    echo "[remote-rq1] $(date -Is) start $bench $subject $category mem_gib=$mem_now" >> "$REMOTE_LOG"
    lease_heartbeat "$bench" "$subject" "$category" &
    lease_heartbeat_pid=$!
    python3 notes/coverage/scripts/rq1_veriput_run.py \\
      --veriput-root "$VERIPUT_ROOT" \\
      --benchmark "$bench" \\
      --subject-id "$subject" \\
      --result-root "$RESULT_ROOT" \\
      --timeout {int(args.timeout)} \\
      --esbmc-run-timeout {int(args.esbmc_run_timeout)} \\
      --wrapper-grace {int(args.wrapper_grace)} \\
      --forge-timeout {int(args.forge_timeout)} \\
      --memlimit-gib {runner_memlimit_gib} \\
      --jobs {int(args.jobs)} \\
      --esbmc "$ESBMC_BIN" \\
{ce_collection_arg}
      --redo >> "$REMOTE_LOG" 2>&1
    rc=$?
    kill "$lease_heartbeat_pid" 2>/dev/null || true
    wait "$lease_heartbeat_pid" 2>/dev/null || true
    status="done"
    [ "$rc" -eq 0 ] || status="failed"
    finish_case_lease "$bench" "$subject" "$category" "$status" "$rc"
    emit_result_progress "$bench" "$subject" "$category" "$status" "$rc" "finish"
    echo "[remote-rq1] $(date -Is) done $bench $subject rc=$rc" >> "$REMOTE_LOG"
  ' _
  kill_over_budget_esbmc
  started_count="$(grep -c '"status":"started"' "$round_file" 2>/dev/null || true)"
  terminal_count="$(grep -c '"status":"terminal"' "$round_file" 2>/dev/null || true)"
  held_count="$(grep -c '"status":"held"' "$round_file" 2>/dev/null || true)"
  rm -f "$round_file"
  echo "[remote-rq1] $(date -Is) round $round complete" >> {shlex.quote(remote_log)}
  echo "[remote-rq1] $(date -Is) round $round summary started=$started_count terminal=$terminal_count held=$held_count cases={len(cases)}" >> {shlex.quote(remote_log)}
  if [ "$started_count" -eq 0 ] && [ "$terminal_count" -ge {len(cases)} ]; then
    echo "[remote-rq1] $(date -Is) all cases terminal; stopping worker instead of stale skip loop" >> {shlex.quote(remote_log)}
    break
  fi
  if [ "$started_count" -eq 0 ] && [ "$held_count" -gt 0 ]; then
    echo "[remote-rq1] $(date -Is) no new starts; active leases held=$held_count; sleeping before retry" >> {shlex.quote(remote_log)}
  fi
  if [ "{int(args.loop)}" -eq 0 ]; then
    break
  fi
  sleep {int(args.sleep_s)}
done
"""
    launch = (
        f"nohup bash -l -c {shlex.quote(worker)} >/tmp/veriput_remote_nohup.out "
        f"2>&1 & echo $! > {shlex.quote(remote_pid)}; cat {shlex.quote(remote_pid)}"
    )
    proc = subprocess.run(
        ["ssh", args.host, launch],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "host": args.host,
        "started": proc.returncode == 0,
        "pid": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "remote_cases": remote_cases,
        "remote_log": remote_log,
        "remote_progress": remote_progress,
        "remote_pid": remote_pid,
        "case_count": len(cases),
        "case_parallel": case_parallel,
        "capacity_case_parallel": capacity_parallel,
        "capacity": cap,
        "runner_memlimit_gib": runner_memlimit_gib,
        "allow_unsafe_case_parallel": bool(args.allow_unsafe_case_parallel),
        "remote_min_mem_gib": args.remote_min_mem_gib,
        "esbmc_rss_limit_gib": args.esbmc_rss_limit_gib,
        "remote_watchdog": {
            "sleep_s": args.remote_watchdog_sleep_s,
            "max_proc_runtime_s": max_proc_runtime_s,
            "stale_proc_slack_s": args.remote_stale_proc_s,
        },
        "memory_watchdog": True,
        "progress_watchdog": True,
        "lease_watchdog": True,
        "remote_lease_dir": DEFAULT_REMOTE_LEASE_DIR,
        "remote_lease_stale_s": args.remote_lease_stale_s,
        "remote_lease_refresh_s": args.remote_lease_refresh_s,
        "reset_remote_leases": bool(args.reset_remote_leases),
        "safe_worker_generation": SAFE_WORKER_GENERATION,
        "loop": bool(args.loop),
        "sync_results_back": bool(args.sync_results_back),
        "local_result_rsync_target": args.local_result_rsync_target,
        "started_ts": time.time(),
    }


def start_local_pull_loop(args) -> dict:
    """Continuously pull remote Results back to the local RQ1 directory."""
    pull_log = "/tmp/veriput_remote_pull.log"
    pull_pid = "/tmp/veriput_remote_pull.pid"
    pull_progress = "/tmp/veriput_remote_pull_progress.jsonl"
    remote_results = f"{args.remote_veriput}/Results/RQ1/VeriPUT/"
    local_results = str(DEFAULT_LOCAL_RESULTS) + "/"
    pull = f"""
set -u
cd {shlex.quote(str(Path.cwd()))}
round=0
while true; do
  round=$((round + 1))
  adopt_rc=0
  interpret_rc=0
  dispatch_rc=0
  review_dispatch_rc=0
  echo "[remote-pull] $(date -Is) begin" >> {shlex.quote(pull_log)}
  rsync -a --partial --delay-updates {shlex.quote(args.host + ':' + str(remote_results))} \
    {shlex.quote(local_results)} >> {shlex.quote(pull_log)} 2>&1
  rsync_rc=$?
  postprocess_due=0
  if [ {int(args.pull_postprocess_every)} -le 1 ] || [ $((round % {int(args.pull_postprocess_every)})) -eq 0 ]; then
    postprocess_due=1
    python3 notes/coverage/scripts/rq1_results_adopt.py \
      --source-root {shlex.quote(str(DEFAULT_LOCAL_RESULTS))} \
      --results-root {shlex.quote(str(DEFAULT_LOCAL_RESULTS))} \
      --out {shlex.quote(str(args.remote_adopt_out))} >> {shlex.quote(pull_log)} 2>&1
    adopt_rc=$?
    python3 notes/coverage/scripts/rq1_esbmc_result_interpret.py \
      --results-root {shlex.quote(str(DEFAULT_LOCAL_RESULTS))} \
      --out {shlex.quote(str(args.remote_interpret_out))} >> {shlex.quote(pull_log)} 2>&1
    interpret_rc=$?
    python3 notes/coverage/scripts/rq1_repair_dispatcher.py \
      >> {shlex.quote(pull_log)} 2>&1
    dispatch_rc=$?
    python3 notes/coverage/scripts/rq1_review_dispatcher.py \
      >> {shlex.quote(pull_log)} 2>&1
    review_dispatch_rc=$?
  fi
  printf '{{"ts":"%s","round":%s,"rsync_rc":%s,"postprocess_due":%s,"adopt_rc":%s,"interpret_rc":%s,"dispatch_rc":%s,"review_dispatch_rc":%s,"remote_results":"%s","local_results":"%s"}}\\n' \
    "$(date -Is)" "$round" "$rsync_rc" "$postprocess_due" "$adopt_rc" "$interpret_rc" "$dispatch_rc" "$review_dispatch_rc" \
    {shlex.quote(args.host + ':' + str(remote_results))} {shlex.quote(local_results)} >> {shlex.quote(pull_progress)}
  printf '{{"ts":"%s","pid":%s,"round":%s,"rsync_rc":%s,"postprocess_due":%s,"adopt_rc":%s,"interpret_rc":%s,"dispatch_rc":%s,"review_dispatch_rc":%s,"pull_log":"%s","pull_progress":"%s","remote_results":"%s","local_results":"%s"}}\\n' \
    "$(date -Is)" "$$" "$round" "$rsync_rc" "$postprocess_due" "$adopt_rc" "$interpret_rc" "$dispatch_rc" "$review_dispatch_rc" \
    {shlex.quote(pull_log)} {shlex.quote(pull_progress)} \
    {shlex.quote(args.host + ':' + str(remote_results))} {shlex.quote(local_results)} > {shlex.quote(str(args.pull_state))}
  echo "[remote-pull] $(date -Is) done" >> {shlex.quote(pull_log)}
  sleep {int(args.pull_sleep_s)}
done
"""
    launch = (
        f"nohup bash -l -c {shlex.quote(pull)} >/tmp/veriput_remote_pull_nohup.out "
        f"2>&1 & echo $! > {shlex.quote(pull_pid)}; cat {shlex.quote(pull_pid)}"
    )
    proc = subprocess.run(
        ["bash", "-lc", launch],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "started": proc.returncode == 0,
        "pid": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "pull_log": pull_log,
        "pull_pid": pull_pid,
        "pull_progress": pull_progress,
        "pull_state": str(args.pull_state),
        "remote_results": args.host + ":" + str(remote_results),
        "local_results": local_results,
        "adopt_out": str(args.remote_adopt_out),
        "interpret_out": str(args.remote_interpret_out),
        "pull_sleep_s": args.pull_sleep_s,
        "postprocess_every": args.pull_postprocess_every,
        "started_ts": time.time(),
    }


def sync_command(args) -> list[str]:
    cmd = [
        "rsync",
        "-a",
        "--delete",
        "--exclude",
        ".git",
        "--exclude",
        "cache",
    ]
    if not args.sync_build:
        cmd += ["--exclude", "build"]
    cmd += [str(Path.cwd()) + "/", f"{args.host}:{args.remote_esbmc}/"]
    return cmd


def sync_veriput_command(args) -> list[str]:
    return [
        "rsync",
        "-a",
        "--delete",
        "--exclude",
        "Results/RQ1/VeriPUT",
        "--exclude",
        ".git",
        str(args.local_veriput) + "/",
        f"{args.host}:{args.remote_veriput}/",
    ]


def remote_build_command(args) -> list[str]:
    remote_script = f"""
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin:$PATH"
cd {shlex.quote(str(args.remote_esbmc))}
echo "[remote-build] $(date -Is) host=$(hostname) pwd=$PWD"
echo "[remote-build] PATH=$PATH"
command -v cmake
{args.remote_build_command}
echo "[remote-build] $(date -Is) done"
"""
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        args.host,
        f"bash -l -c {shlex.quote(remote_script)}",
    ]


def remote_preflight(args) -> dict:
    """Require the synced trees and executable before acquiring any case lease."""
    script = f"""
set -euo pipefail
test -d {shlex.quote(str(args.remote_esbmc))}
test -d {shlex.quote(str(args.remote_veriput))}
test -f {shlex.quote(str(args.remote_esbmc / 'notes/coverage/scripts/rq1_veriput_run.py'))}
test -f {shlex.quote(str(args.remote_esbmc / 'notes/coverage/scripts/rq1_esbmc_result_interpret.py'))}
test -x {shlex.quote(str(args.remote_esbmc / 'build/src/esbmc/esbmc'))}
"""
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", args.host,
         f"bash -lc {shlex.quote(script)}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ready": proc.returncode == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    parser.add_argument("--host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-esbmc", type=Path, default=DEFAULT_REMOTE_ESBMC)
    parser.add_argument("--remote-veriput", type=Path, default=DEFAULT_REMOTE_VERIPUT)
    parser.add_argument("--local-veriput", type=Path, default=DEFAULT_LOCAL_VERIPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_REMOTE_STATE)
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--ce-collection-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--esbmc-run-timeout", type=int, default=60)
    parser.add_argument("--wrapper-grace", type=int, default=60)
    parser.add_argument(
        "--forge-timeout",
        type=int,
        default=180,
        help="Foundry replay timeout passed through to rq1_veriput_run.py")
    parser.add_argument("--memlimit-gib", type=float, default=12.0)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--case-parallel",
        type=int,
        default=0,
        help="number of subjects to run concurrently on remote; 0 auto-computes "
             "from nproc and MemAvailable/memlimit")
    parser.add_argument(
        "--allow-unsafe-case-parallel",
        action="store_true",
        help="do not cap --case-parallel by the computed memory/CPU capacity")
    parser.add_argument(
        "--reserve-cores",
        type=int,
        default=1,
        help="remote CPU cores to leave free when auto-computing case parallelism")
    parser.add_argument(
        "--reserve-mem-gib",
        type=float,
        default=4.0,
        help="remote memory GiB to leave free when auto-computing case parallelism")
    parser.add_argument(
        "--max-case-parallel",
        type=int,
        default=1,
        help="upper bound for auto-computed remote subject parallelism; default "
             "1 is conservative after WSL2 memory pressure")
    parser.add_argument(
        "--remote-min-mem-gib",
        type=float,
        default=8.0,
        help="do not start a remote case when MemAvailable is below this GiB")
    parser.add_argument(
        "--low-mem-sleep-s",
        type=int,
        default=60,
        help="remote worker sleep interval when memory is below the threshold")
    parser.add_argument(
        "--esbmc-rss-limit-gib",
        type=float,
        default=14.0,
        help="kill remote esbmc processes whose RSS exceeds this GiB")
    parser.add_argument(
        "--remote-watchdog-sleep-s",
        type=int,
        default=DEFAULT_REMOTE_WATCHDOG_SLEEP_S,
        help="remote memory/process watchdog polling interval")
    parser.add_argument(
        "--remote-stale-proc-s",
        type=int,
        default=DEFAULT_REMOTE_STALE_PROC_S,
        help="extra seconds beyond wrapper/Foundry budget before killing stale "
             "remote RQ1/ESBMC processes")
    parser.add_argument("--remote-lease-stale-s",
                        type=int,
                        default=DEFAULT_REMOTE_LEASE_STALE_S)
    parser.add_argument("--remote-lease-refresh-s",
                        type=int,
                        default=DEFAULT_REMOTE_LEASE_REFRESH_S)
    parser.add_argument(
        "--reset-remote-leases",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="clear remote case leases before starting a fresh worker")
    parser.add_argument("--sync-code", action="store_true")
    parser.add_argument(
        "--remote-build",
        action="store_true",
        help="build ESBMC on the remote host after code sync, using a login shell")
    parser.add_argument(
        "--remote-build-command",
        default="./scripts/build.sh build",
        help="remote build command run from --remote-esbmc under bash -l -c")
    parser.add_argument("--remote-build-log", type=Path,
                        default=DEFAULT_REMOTE_BUILD_LOG)
    parser.add_argument("--sync-code-log", type=Path, default=DEFAULT_SYNC_CODE_LOG)
    parser.add_argument("--sync-veriput-log", type=Path,
                        default=DEFAULT_SYNC_VERIPUT_LOG)
    parser.add_argument(
        "--stop-existing",
        action="store_true",
        help="stop old remote RQ1/ESBMC worker processes before launching")
    parser.add_argument(
        "--sync-build",
        action="store_true",
        help="include the local ESBMC build tree when syncing code to remote")
    parser.add_argument("--sync-veriput", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--sleep-s", type=int, default=30)
    parser.add_argument("--sync-results-back", action="store_true")
    parser.add_argument("--allow-uncovered-tsv", action="store_true")
    parser.add_argument("--start-pull-loop", action="store_true")
    parser.add_argument("--pull-sleep-s", type=int, default=30)
    parser.add_argument("--pull-postprocess-every", type=int, default=1)
    parser.add_argument("--pull-state", type=Path, default=DEFAULT_PULL_STATE)
    parser.add_argument("--remote-adopt-out", type=Path,
                        default=DEFAULT_REMOTE_ADOPT_OUT)
    parser.add_argument("--remote-interpret-out", type=Path,
                        default=DEFAULT_REMOTE_INTERPRET_OUT)
    parser.add_argument(
        "--local-result-rsync-target",
        default=f"{DEFAULT_LOCAL_RESULTS}/",
        help="rsync target used from the remote host after each case")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    categories = set(args.category or PRIORITY_CATEGORIES)
    cases = load_cases(args.tsv, categories, args.limit,
                       args.allow_uncovered_tsv, args.ce_collection_only)
    if not cases and not args.allow_uncovered_tsv:
        raise SystemExit(
            "theory-covered manifest has zero cases; refusing to start remote "
            "worker until a reviewed+committed patch covers concrete subjects")
    by_category: dict[str, int] = defaultdict(int)
    for case in cases:
        by_category[case["category"]] += 1

    state = {
        "schema": "veriput-remote-rq1-worker/v1",
        "host": args.host,
        "phase": "planned",
        "case_count": len(cases),
        "input_tsv": str(args.tsv),
        "theory_manifest_required": not args.allow_uncovered_tsv,
        "allow_uncovered_tsv": bool(args.allow_uncovered_tsv),
        "categories": dict(sorted(by_category.items())),
        "jobs": args.jobs,
        "case_parallel_requested": args.case_parallel,
        "allow_unsafe_case_parallel": args.allow_unsafe_case_parallel,
        "reserve_cores": args.reserve_cores,
        "reserve_mem_gib": args.reserve_mem_gib,
        "max_case_parallel": args.max_case_parallel,
        "remote_min_mem_gib": args.remote_min_mem_gib,
        "low_mem_sleep_s": args.low_mem_sleep_s,
        "esbmc_rss_limit_gib": args.esbmc_rss_limit_gib,
        "remote_watchdog_sleep_s": args.remote_watchdog_sleep_s,
        "remote_stale_proc_s": args.remote_stale_proc_s,
        "remote_lease_stale_s": args.remote_lease_stale_s,
        "remote_lease_refresh_s": args.remote_lease_refresh_s,
        "reset_remote_leases": args.reset_remote_leases,
        "memlimit_gib": args.memlimit_gib,
        "timeout_s": args.timeout,
        "esbmc_run_timeout_s": args.esbmc_run_timeout,
        "forge_timeout_s": args.forge_timeout,
        "remote_esbmc": str(args.remote_esbmc),
        "remote_veriput": str(args.remote_veriput),
        "local_veriput": str(args.local_veriput),
        "dry_run": args.dry_run,
        "loop": args.loop,
        "sync_results_back": args.sync_results_back,
        "sync_build": args.sync_build,
        "remote_build": args.remote_build,
        "remote_build_command": args.remote_build_command,
        "remote_build_log": str(args.remote_build_log),
        "sync_code_log": str(args.sync_code_log),
        "sync_veriput_log": str(args.sync_veriput_log),
        "start_pull_loop": args.start_pull_loop,
        "pull_state": str(args.pull_state),
        "local_result_rsync_target": args.local_result_rsync_target,
        "safe_worker_generation": SAFE_WORKER_GENERATION,
    }
    write_state(args.state, state)
    if args.stop_existing and not args.dry_run:
        state["phase"] = "stopping-existing-worker"
        write_state(args.state, state)
        state["stop_existing"] = stop_existing_remote_worker(args.host)
    if args.sync_code:
        state["phase"] = "syncing-esbmc"
        write_state(args.state, state)
        cmd = sync_command(args)
        state["sync_command"] = shell_join(cmd)
        if not args.dry_run:
            state["sync_code"] = run_logged_command(
                "sync_code",
                cmd,
                args.sync_code_log,
                state,
                args.state,
            )
            if state["sync_code"]["returncode"] != 0:
                raise SystemExit("remote ESBMC sync failed; refusing remote worker start")
    if args.remote_build:
        state["phase"] = "remote-build"
        write_state(args.state, state)
        cmd = remote_build_command(args)
        state["remote_build_command_full"] = shell_join(cmd)
        if not args.dry_run:
            state["remote_build_result"] = run_logged_command(
                "remote_build_result",
                cmd,
                args.remote_build_log,
                state,
                args.state,
            )
            if state["remote_build_result"]["returncode"] != 0:
                raise SystemExit("remote ESBMC build failed; refusing remote worker start")
    if args.sync_veriput:
        state["phase"] = "syncing-veriput"
        write_state(args.state, state)
        cmd = sync_veriput_command(args)
        state["sync_veriput_command"] = shell_join(cmd)
        if not args.dry_run:
            state["sync_veriput"] = run_logged_command(
                "sync_veriput",
                cmd,
                args.sync_veriput_log,
                state,
                args.state,
            )
            if state["sync_veriput"]["returncode"] != 0:
                raise SystemExit("remote VeriPUT sync failed; refusing remote worker start")
    if not args.dry_run:
        state["phase"] = "remote-preflight"
        write_state(args.state, state)
        state["remote_preflight"] = remote_preflight(args)
        write_state(args.state, state)
        if not state["remote_preflight"]["ready"]:
            state["phase"] = "remote-preflight-failed"
            write_state(args.state, state)
            raise SystemExit(
                "remote preflight failed: synced ESBMC/VeriPUT tree or ESBMC binary "
                "is unavailable; refusing to acquire remote case leases")
    if not args.dry_run and cases:
        state["phase"] = "starting-worker"
        write_state(args.state, state)
        state["worker"] = start_remote_worker(args, cases)
    else:
        state["worker"] = {"started": False, "reason": "dry-run or no cases"}
    if args.start_pull_loop and not args.dry_run:
        state["local_pull_loop"] = start_local_pull_loop(args)
    else:
        state["local_pull_loop"] = {
            "started": False,
            "reason": "not requested or dry-run",
        }
    state["phase"] = "running" if state["worker"].get("started") else "not-started"
    write_state(args.state, state)
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
