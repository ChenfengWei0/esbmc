#!/usr/bin/env bash
# Hardened ESBMC wrapper — enforces ulimit + timeout per feedback_esbmc_unwind.md §7.
# Usage: run_esbmc_one.sh <paper_name> <contract_name> [--extra-flags ...]
set -e
PAPER_NAME="$1"
shift
CONTRACT="$1"
shift

ROOT=/home/samson/workspace/esbmc
SRC_DIR="$ROOT/Dataset/transracer_50/sources/$PAPER_NAME"
OUT_DIR="$ROOT/Dataset/transracer_50/results/$PAPER_NAME"
mkdir -p "$OUT_DIR"

ESBMC="$ROOT/build/src/esbmc/esbmc"
[ -x "$ESBMC" ] || { echo "esbmc binary missing: $ESBMC"; exit 2; }

# 600 s wall + 540 s CPU + 4 GB virtual memory
cd "$SRC_DIR"
timeout 600 bash -c "
  ulimit -v 4000000
  ulimit -t 540
  exec '$ESBMC' contract.sol \
    --contract '$CONTRACT' \
    --tod-race-check=auto --tod-jobs=1 \
    --bound --unwind 3 --no-unwinding-assertions \
    --cvc5 \
    $*
" > "$OUT_DIR/run.stdout" 2> "$OUT_DIR/run.stderr"
rc=$?
echo "$rc" > "$OUT_DIR/run.exitcode"
echo "[$PAPER_NAME] exit=$rc stdout=$(wc -c < $OUT_DIR/run.stdout) bytes"
exit $rc
