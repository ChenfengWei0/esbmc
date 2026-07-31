#!/usr/bin/env bash
# Isolation cells for notes/coverage/unwind-vs-strategy.md.
#
# --k-induction does TWO independent things to a --solidity-path-coverage run:
#   (T) the GOTO transform: process_goto_program runs goto_k_induction()
#       (esbmc_parseoptions.cpp:3812-3813) BEFORE the path-coverage block
#       (:4118), so the pass instruments a program that already carries
#       k-induction's havoc+assume loop preambles;
#   (K) the strategy loop: do_bmc_strategy overwrites the `unwind` option with
#       the current k_step at every phase (:2975, :3039, :3104).
#
# The main matrix always has both. These cells separate them:
#   inductive_step_only : (T) without (K)  -- `is_k_induction` at :3786-3788
#                         includes --inductive-step, but :2060-2064 does NOT
#                         dispatch it to do_bmc_strategy.
#   base_case_only      : neither          -- --base-case triggers neither.
#   incr_default        : (K) without (T)  -- --incremental-bmc is NOT in the
#                         is_k_induction disjunction at :3786-3788.
#                         (also run by the main matrix; repeated here so the
#                         three-way comparison sits in one directory)
#
# usage: unwind_vs_strategy_isolate.sh <esbmc-binary> <outdir> [timeout_s]
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
  if [ -f "$wd/done" ]; then echo "SKIP $name"; return; fi
  mkdir -p "$wd"
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

run_cell base_case_only --base-case
run_cell inductive_step_only --inductive-step
run_cell overflow_kind --k-induction --overflow-check

echo "ALL ISOLATION CELLS DONE"

# Appended after the first isolation round: --forward-condition is the third
# member of the {--base-case, --forward-condition, --inductive-step} group that
# satisfies --coverage-multi-tx's strategy gate WITHOUT invoking
# do_bmc_strategy (esbmc_parseoptions.cpp:2060-2064 lists none of them). The
# other two are measured: --base-case is clean, --inductive-step aborts. This
# closes the group so the claim covers all three rather than two.
run_cell forward_condition_only --forward-condition

echo "FORWARD CONDITION CELL DONE"
