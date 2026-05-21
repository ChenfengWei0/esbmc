#!/usr/bin/env bash
# Driver for the JSON-based coverage collector.
#
#   collect_all.sh esbmc   # rerun all 6 ESBMC entries
#   collect_all.sh native  # rebuild all 5 native_<project>.json from lcov.info
#   collect_all.sh all     # both, sequentially
#
# Each bench writes a single JSON; reruns OVERWRITE the section that ran
# (no append, no history accretion).
set -u
HERE=$(dirname "$(readlink -f "$0")")
PY=$HERE/collect.py
MODE=${1:-all}

ESBMC_BENCHES=(
  aqua_Aqua
  cross_chain_swap_EscrowDst
  cross_chain_swap_EscrowSrc
  farming
  limit_order_protocol
  st1inch_St1inch
)
NATIVE_PROJECTS=(
  aqua
  cross_chain_swap
  farming
  limit_order_protocol
  st1inch
)

run_esbmc() {
  echo "############ ESBMC collection ############"
  for b in "${ESBMC_BENCHES[@]}"; do
    echo "=== $b @ $(date -Iseconds) ==="
    python3 "$PY" esbmc "$b"
  done
}
run_native() {
  echo "############ Native collection ############"
  for p in "${NATIVE_PROJECTS[@]}"; do
    echo "=== $p @ $(date -Iseconds) ==="
    python3 "$PY" native "$p"
  done
}

case "$MODE" in
  esbmc)  run_esbmc ;;
  native) run_native ;;
  all)    run_native; run_esbmc ;;
  *) echo "usage: $0 {esbmc|native|all}"; exit 2 ;;
esac
printf '\a'
