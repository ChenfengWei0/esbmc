#!/bin/sh
# Is the biggest slice of the gate's shortfall a BUDGET problem or a SCALE one?
#
# Four units were killed by the 180s outer bound while their enumeration had
# already SUCCEEDED, and a path-coverage run killed by a timeout emits nothing
# at all -- so each contributed a hard zero:
#
#   EscrowDst.withdraw        units=4  paths=30
#   EscrowDst.publicWithdraw  units=4  paths=30
#   FarmingPool.exit          units=23 paths=1004
#   FarmingPool.rescueFunds   units=23 paths=9536
#
# The set comparison names exactly what they cost: ImmutablesLib's eight
# canonical decisions (763/776/789/802/815/828/841/854) are missing on BOTH
# Escrows, and they are reachable only through the contract methods that were
# killed. Same for EscrowDst.sol's two and most of FarmingPool's eight.
#
# So one measurement decides the largest open question in subgoal 2: give ONE of
# them a much larger budget and see whether it finishes.
#
#   finishes  -> the shortfall is a budget artefact. Re-run the sweep with a
#                budget that fits, and disclose it against the baseline's 90s
#                (which is an asymmetry in OUR favour and must be stated).
#   does not  -> it is a scale problem, and no budget the paper can defend will
#                close it. That is a real limitation and belongs in the text,
#                not in a longer timeout.
#
# EscrowDst.withdraw first because it is the SMALLEST of the four (30 paths):
# if thirty paths cannot be solved in twenty minutes, the answer is scale and
# the other three need not be run.
#
# Strictly serial, foreground, --memlimit 8g. Never run this while a sweep is
# running: two ESBMC processes at once is what exhausted this machine once.
set -eu
E=/home/samson/workspace/esbmc/build/src/esbmc/esbmc
I=/home/samson/workspace/esbmc/notes/coverage/inputs
F=$I/cross-chain-swap__EscrowDst.flat.sol
W=${1:-/tmp/budget_probe}
T=${2:-1200}

mkdir -p "$W"
cd "$W"
rm -f cov-report.json
echo "=== EscrowDst.withdraw, outer budget ${T}s (sweep used 180s) ==="
date -Iseconds
timeout -k 30s "${T}s" "$E" "$F.solast" --sol "$F" \
  --solidity-path-coverage --solidity-max-tx 1 --cov-report-json \
  --path-cov-max-goals 10000 --memlimit 8g \
  --contract EscrowDst --focus-function withdraw
rc=$?
date -Iseconds
echo "=== exit=$rc; report present: $([ -f cov-report.json ] && echo yes || echo NO) ==="
