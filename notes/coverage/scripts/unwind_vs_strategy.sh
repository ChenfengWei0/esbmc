#!/usr/bin/env bash
# Matrix over (--unwind N) x (bounding strategy) for --solidity-path-coverage.
#
# Question: --solidity-path-coverage fixes the path ENUMERATION bound at
# instrumentation time and installs its own --unwind 4; do_bmc_strategy
# overwrites `unwind` with the current k_step at every phase. This runs the
# cross product so the disagreement is measured rather than argued.
#
# One esbmc at a time, setsid + timeout so a timeout kills the whole group,
# --memlimit 6g. Verdict is read off the REPORT (report_summary.py), never off
# the exit code -- exit codes are known NOT to be comparable across strategies.
#
# usage: unwind_vs_strategy.sh <esbmc-binary> <outdir> [per-cell-timeout-s]
set -u

ESBMC_BIN="${1:?usage: $0 <esbmc-binary> <outdir> [timeout_s]}"
OUT="${2:?usage: $0 <esbmc-binary> <outdir> [timeout_s]}"
TMO="${3:-600}"

REPO=/home/samson/workspace/esbmc
IN="$REPO/notes/coverage/inputs/aqua__Aqua.flat.sol"

mkdir -p "$OUT"

run_cell () {
  local name="$1"; shift
  local wd="$OUT/$name"
  if [ -f "$wd/done" ]; then
    echo "SKIP $name (already done)"
    return
  fi
  mkdir -p "$wd"
  # never start while another esbmc of ours is up
  # Concurrency gate. NOT `pgrep -x esbmc`: that matches `comm`, which the
  # kernel truncates to 15 chars, so a snapshot binary named
  # esbmc_snapshot_unwind presents as `esbmc_snapshot_` and NEVER matches --
  # the guard then reports zero while multi-GB solvers are live. The gate
  # reads /proc/<pid>/cmdline, matches on the FLAG, and excludes the
  # `timeout` wrapper (whose own cmdline carries the flag too). Cells run
  # strictly one at a time here, so no run of ours is live at this point.
  if ! python3 "$REPO/notes/coverage/scripts/esbmc_gate.py" \
       --max-runs 1 --budget-mb 6000; then
    echo "GATE REFUSED for $name -- not starting" >&2
    return 1
  fi
  echo "RUN  $name : $*"
  local t0 t1
  t0=$(date +%s)
  ( cd "$wd" && setsid timeout -k 30s "${TMO}s" "$ESBMC_BIN" \
      "$IN.solast" --sol "$IN" \
      --contract Aqua --solidity-path-coverage --solidity-max-tx 1 \
      --focus-function dock --memlimit 6g --cov-report-json \
      "$@" >"$wd/run.log" 2>&1 )
  local rc=$?
  t1=$(date +%s)
  printf 'cell=%s rc=%s wall=%ss argv_extra=%s\n' "$name" "$rc" "$((t1-t0))" "$*" > "$wd/meta.txt"
  touch "$wd/done"
  echo "DONE $name rc=$rc wall=$((t1-t0))s"
}

# --- no strategy: the pass's own bound, and explicit bounds around it --------
run_cell nostrat__default
for u in 1 2 3 4 6 8; do
  run_cell "nostrat__unwind$u" --unwind "$u"
done

# --- k-induction ------------------------------------------------------------
run_cell kind__default --k-induction
for u in 1 2 3 4 6 8; do
  run_cell "kind__unwind$u" --unwind "$u" --k-induction
done

# --- incremental-bmc --------------------------------------------------------
run_cell incr__default --incremental-bmc
for u in 1 2 3 4 6 8; do
  run_cell "incr__unwind$u" --unwind "$u" --incremental-bmc
done

echo "ALL CELLS DONE"
