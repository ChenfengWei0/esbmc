#!/usr/bin/env bash
# Independent mechanism test for the "data even on UNKNOWN" fix
# (STAGE5_RESIDUAL_DIAG.md Stage G). NOT a testing_tool.py regression
# test on purpose: testing_tool strips --timeout and unconditionally
# fail()s any run that exceeds its own timeout WITHOUT regex-matching
# the partial output, so a perpetually-k-induction-timing-out case
# cannot be pinned green there. This script corroborates the actual
# fix the orchestrator/CI/manual pipeline relies on: when ESBMC is
# externally terminated mid-solve (SIGTERM, exactly how `timeout(1)`,
# CI and orchestrator.py bound it) it STILL emits the partial branch
# coverage from the async-signal-safe snapshot before exiting.
#
# Usage: mechanism_partial_cov_test.sh [path-to-esbmc]
# Exit 0 = mechanism works (partial coverage emitted on SIGTERM).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
ESBMC="${1:-$ROOT/build/src/esbmc/esbmc}"
case "$ESBMC" in /*) ;; *) ESBMC="$(cd "$(dirname "$ESBMC")" && pwd)/$(basename "$ESBMC")";; esac
DIR="$ROOT/regression/esbmc-solidity/cov_nested_mapping_write_uint256_kinduction_knownbug"

if [ ! -x "$ESBMC" ]; then echo "FAIL: esbmc not found at $ESBMC"; exit 2; fi

# External SIGTERM at 8s, NO esbmc --timeout — mirrors orchestrator.py
# (outer `timeout`) / CI / testing_tool's own kill path exactly.
out="$(cd "$DIR" && timeout -s TERM 8 "$ESBMC" contract.solast --sol contract.sol \
  --contract C --branch-coverage-claims --k-induction --unlimited-k-steps \
  --quiet --no-assertions 2>&1)"

echo "--- captured tail ---"
echo "$out" | tail -5
echo "---------------------"

if echo "$out" | grep -qE '^Branch Coverage: [0-9.]+%' \
   && echo "$out" | grep -q 'partial: run terminated before verification concluded'; then
  echo "PASS: partial branch coverage emitted on external SIGTERM (data-even-on-UNKNOWN)"
  exit 0
fi
echo "FAIL: no partial Branch Coverage line on SIGTERM — fix regressed"
exit 1
