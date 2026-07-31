#!/usr/bin/env bash
# Can a bounding strategy be made to AGREE with the enumeration bound?
#
# The main matrix shows --unwind N is inert under a strategy: it sets
# path_cov_unwind (the enumeration bound, esbmc_parseoptions.cpp:4288-4293) but
# do_bmc_strategy then overwrites the symex `unwind` option with k_step at every
# phase (:2975, :3039, :3104), and on aqua/dock the k-loop terminates at k=2 for
# every N. So symex never reaches the depth the goal set was enumerated at.
#
# --base-k-step N starts the k-loop AT N (esbmc_parseoptions.cpp:2658, :2752),
# so the FIRST base case runs with symex unwind == N. If that is the whole fix,
# these cells should reproduce the no-strategy verdict (F=2, bounded-holds=61
# at N=4). --max-k-step must be > --base-k-step or the run aborts (:2669-2675).
#
# usage: unwind_vs_strategy_align.sh <esbmc-binary> <outdir> [timeout_s]
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

run_cell incr_u4_bk4 --unwind 4 --incremental-bmc --base-k-step 4 --max-k-step 5
run_cell kind_u4_bk4 --unwind 4 --k-induction    --base-k-step 4 --max-k-step 5

# --- controls for question A: does a symex bound BELOW the enumeration bound
# --- delete witnesses on THIS unit, with no strategy in the picture? ---------
# nostrat_u4_us62_1 emulates the strategy's arithmetic without the strategy:
#   enumeration bound 4 (--unwind 4 -> path_cov_unwind = 4), symex bound on
#   dock's own loop 62 forced to 1 (--unwindset overrides max_unwind per loop,
#   src/goto-symex/symex_goto.cpp:530-531).
# nostrat_u4_nosimplify is the POSITIVE CONTROL: the one configuration already
#   known to collapse F 2 -> 0 on this exact unit
#   (notes/coverage/certify-vs-assert-vacuity.md §3-4). If it does not
#   reproduce under this snapshot, nothing else in this file about witness loss
#   can be trusted either.
run_cell nostrat_u4_us62_1     --unwind 4 --unwindset 62:1
run_cell nostrat_u4_nosimplify --unwind 4 --no-simplify
run_cell kind_u4_nosimplify    --unwind 4 --no-simplify --k-induction

echo "ALL ALIGN CELLS DONE"
