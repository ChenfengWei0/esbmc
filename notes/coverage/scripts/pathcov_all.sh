#!/bin/sh
# Collect the remaining benchmarks, STRICTLY SERIALLY.
#
# One ESBMC process at a time, each with --memlimit 8g, because running several
# at once is what exhausted this machine once already. The serialisation is the
# `&&`-free sequential list below plus the fact that pathcov_collect.py itself
# never forks -- not a scheduler setting that could be overridden.
#
# Resumable: each run is journalled to runs.jsonl the moment it finishes, so
# re-invoking this continues instead of repeating.
# set -e: pathcov_collect.py sys.exit()s on a missing AST, and without it this
# loop printed "ALL DONE" after every benchmark had refused to run.
set -eu
S=/home/samson/workspace/esbmc/notes/coverage/scripts/pathcov_collect.py
T=${1:-120}
for b in cross_chain_swap_EscrowDst cross_chain_swap_EscrowSrc farming st1inch_St1inch aqua_Aqua limit_order_protocol
do
  echo "=== $b ==="
  python3 "$S" "$b" --timeout "$T"
done
echo "=== ALL DONE ==="
