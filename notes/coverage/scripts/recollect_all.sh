#!/usr/bin/env bash
# Re-collect the path-coverage corpus with the CURRENT build, serially.
#
# WHY A DRIVER AND NOT SIX COMMANDS. The six invocations are not interchangeable
# -- one benchmark needs a non-default encoder, one costs no ESBMC process at
# all, and every one of them must be told --fresh or it will silently resume a
# journal written by an older binary and reuse its reports. Encoding that as a
# script means the next re-collection is one action rather than six chances to
# forget one of them.
#
# THE MEMORY RULE IS ENFORCED HERE, NOT REMEMBERED. This project's standing rule
# is "never run ESBMC concurrently" -- it exhausted this machine once and forced
# a reboot. certify_all.py already discharged that rule into arithmetic: with a
# --memlimit on every process, how many fit is a calculation over a number the
# kernel publishes. The same arithmetic applies to a serial sweep sharing the
# machine with something else, e.g. a regression suite: this sweep needs
# MEMLIMIT_GIB of headroom, and 60% of MemAvailable has to cover it ON TOP of
# whatever is already running. So the script MEASURES and REFUSES rather than
# starting and hoping. Refusing is the point -- a sweep that starts and gets
# OOM-killed halfway produces a journal that looks like a real partial
# collection.
#
# ORDER IS DELIBERATE, cheapest-and-most-informative first:
#   1. st1inch     -- the one the encoder table exists for. Its previous
#                     collection was 22 runs / 22 killed / 0 reports, so this is
#                     the run that says whether the unblock actually holds on
#                     the real contract rather than on the reduced PoC.
#   2. aqua        -- 8 units, the smallest real benchmark.
#   3. the Escrows, farming -- larger.
#   limit_order_protocol is NOT here: every one of its units is a library the
#   collector refuses on soundness grounds, so it costs no ESBMC process and has
#   already been re-collected.
set -u

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMLIMIT_GIB=8
ORDER=(st1inch_St1inch aqua_Aqua cross_chain_swap_EscrowSrc
       cross_chain_swap_EscrowDst farming)

avail_gib() {
  # Reads the whole of /proc/meminfo and picks MemAvailable by NAME. Deliberately
  # not a line-extracting one-liner: this workspace bans those, and the reason
  # generalises -- a field picked by position breaks silently when the kernel
  # adds a line, whereas one picked by name fails loudly.
  python3 "$SCRIPTS/mem_available_gib.py"
}

headroom_ok() {
  local avail budget
  # FIRST the rule as originally stated, THEN the arithmetic. A headroom
  # calculation reads MemAvailable NOW, and another job's peak is in the
  # future -- measured on this very machine, one regression family ran at
  # 0.7 GiB per process and another at 3.6 GiB, so the low moment would have
  # said yes and been wrong by 12 GiB. So: refuse while any other ESBMC is
  # running, whoever started it.
  if ! python3 "$SCRIPTS/esbmc_busy.py"; then
    echo "REFUSING: another ESBMC is already running. A serial sweep sharing" \
         "the machine with an unrelated job is the concurrency this project's" \
         "rule is about; the headroom arithmetic below is for a sweep's own" \
         "parallel jobs, whose limits it sets, and does not transfer."
    return 1
  fi
  avail="$(avail_gib)"
  budget=$(( avail * 60 / 100 ))
  if [ "$budget" -lt "$MEMLIMIT_GIB" ]; then
    echo "REFUSING: MemAvailable ${avail} GiB, 60% of it is ${budget} GiB," \
         "which does not cover this sweep's ${MEMLIMIT_GIB} GiB --memlimit."
    echo "Something else is using the machine. Wait for it rather than" \
         "shrinking the limit: a smaller limit turns a scheduling decision" \
         "into a measurement change, and units start dying of the limit" \
         "instead of of the problem."
    return 1
  fi
  echo "  headroom ok: MemAvailable ${avail} GiB, budget ${budget} GiB," \
       "need ${MEMLIMIT_GIB} GiB"
  return 0
}

for bench in "${ORDER[@]}"; do
  echo "=============================================================="
  echo "== $bench"
  echo "=============================================================="
  if ! headroom_ok; then
    echo "STOPPING before $bench. Re-run this script to continue -- the"
    echo "benchmarks already done are skipped by their own journals, and the"
    echo "cross-build check makes reusing a stale one impossible."
    exit 2
  fi
  python3 "$SCRIPTS/pathcov_collect.py" "$bench" --fresh || {
    echo "== $bench FAILED (exit $?), continuing to the next"
  }
done

echo "=============================================================="
echo "all requested benchmarks attempted; now run:"
echo "  python3 notes/branch_gate.py"
echo "and read the 'What the product side actually saw' table BEFORE the"
echo "verdict column -- a row with killed or no-report runs is a lower bound,"
echo "not a measurement."
