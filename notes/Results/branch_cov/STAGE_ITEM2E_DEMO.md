# Item 2e — empirical demonstration (checked-in artifact)

The value of Item 2e is that a bounded run on a heavy contract that is
killed *mid-SMT-solve* (before `report_coverage()`) now still persists
every edge witnessed so far, so repeated bounded runs accumulate
monotonically. Below is the exact before/after on the real pilot
`cov_pilot_cross_chain_swap_EscrowDst` under `--coverage-whole-unit`
(the heavy ~80-edge case), `--timeout 20` (in-tool), outer `timeout 60`,
`--k-induction` (coverage MUST use k-induction). Captured this session.

Mechanism (verified `esbmc_parseoptions.cpp:114-120,520-521`):
`--timeout` is ALWAYS an unconditional `_exit(1)` from the SIGALRM
handler — never graceful, never returns to `report_coverage()`, can
land on any instruction. The Item-2 write-back is end-of-report only,
so it survives a timeout *only if* the run reached ≥1
`report_coverage()` before the alarm. For this heavy whole-unit case a
single claim solve exceeds the budget, so no report is ever reached →
end-only persistence saves nothing. Item 2e persists at the
per-`P_SATISFIABLE` hook *during* solving, before any report, so
progress survives the `_exit(1)` whenever it lands.

## Reproduce

```sh
cd regression/esbmc-solidity/cov_pilot_cross_chain_swap_EscrowDst
E=<build>/src/esbmc/esbmc ; J=/tmp/escrow2e.json ; rm -f $J $J.tmp
for i in 1 2 3 4; do
  timeout 60 $E contract.solast --sol contract.sol --contract EscrowDst \
    --coverage-whole-unit --branch-coverage-claims --k-induction \
    --unlimited-k-steps --memlimit 8g --timeout 20 --quiet \
    --no-assertions --coverage-covered-set $J >/dev/null 2>&1
  echo "run$i edges=$(grep -c '\"cond\"' $J) \
        json=$(python3 -c "import json;json.load(open('$J'));print('valid')" \
        2>/dev/null || echo CORRUPT)"
done
```

## Before Item 2e (end-only write-back)

Every bounded run is killed mid-solve before `report_coverage()`:

```
run1: timedout=1  covered-set: NOFILE (0 edges)
run2: timedout=1  covered-set: NOFILE (0 edges)
run3: timedout=1  covered-set: NOFILE (0 edges)
run4: timedout=1  covered-set: NOFILE (0 edges)
```

Zero cross-run progress — the heavy whole-unit case never persists
anything under a bounded budget.

## After Item 2e (incremental atomic flush at the P_SATISFIABLE hook)

```
run1: timedout=1  covered-set edges=31  json=valid
run2: timedout=1  covered-set edges=37  json=valid
run3: timedout=1  covered-set edges=37  json=valid
run4: timedout=1  covered-set edges=37  json=valid
```

Monotone non-decreasing (31 → 37 → converged), and the JSON parses
cleanly after *every* mid-solve kill — the atomic `tmp`+`rename`
publish never leaves a truncated/corrupt file.

## Scope note (honest)

St1inch / FarmingPool gain nothing here: their `Reached: 0` is the
pre-existing upstream GOTO-gen / lib-typed-receiver bug — zero edges
are *ever* witnessed regardless of budget or run count, so there is
nothing for Item 2/2e to accumulate. That is a separate,
separately-authorised investigation, not an Item 2e gap.

Deterministic ctest tripwire for the monotone/crash-safe invariant
(no timing dependence): `cov_jsonset_unreachable_seed_pass` — a
covered-set seeded with edges that are unreachable under the run's
harness; they can only stay credited if load never drops and the
write is atomic/non-truncating (Reached : 4 / 100%; a regression that
loses a committed edge ⇒ Reached : 2 / 50%).
