# The branch-coverage gate, measured for the first time -- and where the gap went

`branch_gate.py` has been able to compute both sides for a while. Until now
nobody had run it and read the result. Run 2026-07-31:

| bench | denom | baseline P1 | baseline P2 | native | ours | gate vs P2 |
|---|---|---|---|---|---|---|
| aqua_Aqua | 8 | 7 | 7 | 6 | 4 | FAIL |
| cross_chain_swap_EscrowDst | 18 | 14 | 18 | 10 | 5 | FAIL |
| cross_chain_swap_EscrowSrc | 16 | 8 | 16 | 8 | 6 | FAIL |
| farming | 26 | 26 | 26 | 26 | 18 | FAIL |
| limit_order_protocol | 3 | 3 | 3 | 2 | - | N/A: 0 units |
| st1inch_St1inch | 86 | 72 | 72 | 83 | 0 | FAIL |

**0 of 5 measurable benchmarks clear the gate.** That is the honest headline and
it is not softened below. What follows is where the 121 missing canonical
decisions went, because the causes are not one thing and three of them are not
about the method at all.

`gap_attribution.py` buckets every MISSED decision by the definition that
syntactically encloses it, cross-referenced against the per-unit claim census
from the same reports. `collection_health.py` names every run that produced no
report. Both are new here; neither invokes esbmc.

## Cause 1 -- 27 runs killed at exactly 180 s

| bench | killed | what they would have covered |
|---|---|---|
| st1inch_St1inch | 22 / 22 | the entire benchmark (86 decisions) |
| farming | 2 | `FarmingPool.exit`, `FarmingPool.rescueFunds` (5 decisions) |
| cross_chain_swap_EscrowDst | 2 | `EscrowDst.withdraw`, `publicWithdraw` |
| aqua_Aqua | 1 | `Aqua.ship` (3 of aqua's 4 missing decisions) |

A path-coverage run killed from outside emits NOTHING -- the partial-result
rescue is gated on `branch_cov_active` -- so each of these contributes exactly
zero and the zero is indistinguishable in the gate table from a measured zero.
`branch_gate.py` already refuses to call these a measurement; what was missing
is that the outer budget was **180 seconds**, which is a collection parameter,
not a property of the method.

**st1inch's row is not a result.** 22 of 22 runs killed, 0 reports, and the
benchmark's own baseline is separately suspect: it was collected while the
frontend bug that zeroed initialised locals in inherited function bodies was
live, which inflated the branch-coverage side too. Both sides need re-collecting
before any st1inch number is used for anything.

## Cause 2 -- 12 runs aborted in seconds, and fixing it recovers NOTHING

```
ERROR: main symbol `claim' is ambiguous
ERROR: CONVERSION ERROR
```

The library route passes `--function <name>` with no contract qualification
(`pathcov_collect.esbmc_cmd`, the `library=True` branch), so a name declared by
more than one contract of the flat ends the run before instrumentation. Measured:
10 on farming (`claim`, `startFarming`, `stopFarming`, `farmed`,
`updateBalances`, all shared by FarmAccounting / FarmingLib / FarmingPool) plus
`TimelocksLib.get` on both Escrows.

**This was already known** -- `pathcov_collect.py`'s docstring records the same
count -- and `collection_health.py` rediscovering it is a check that the
recorded reason survives into the data, not a new finding.

What IS new is the measurement of what fixing it buys. Adding `--contract`:

```
$ esbmc ... --contract FarmingLib --function claim
instrumented 0 complete path(s) across 0 unit(s)
1 in-scope function(s) are internal/private and are therefore not units
EXIT=0
```

`FarmingLib.claim` is `internal`. It is not a unit, it has no path set, and it
never had one. **The abort hides an honest zero, not a lost measurement.** The
fix is worth making so the run reports its scope instead of crashing -- and
because `branch_gate.pathcov_reports_for` refuses to run at all when the report
count disagrees with the index -- but it recovers zero decisions and must not be
sold as recovering any.

### The part of cause 2 that is a live hazard

`--function` verifies a function in ISOLATION from an ARBITRARY contract state.
A counterexample it finds may depend on a state combination no
`constructor() -> tx sequence` can reach on chain, which for this project means
a RED test on the unmodified contract. That is why `--function` is banned from
the regressions.

Every library-route run so far reported 0 units, so the channel has never fired.
But `gap_attribution.py` lists `ImmutablesLib.protocolFeeAmountCd` and three
siblings as **external** -- library functions that ARE units by visibility. The
moment such a function enumerates a path, the library route will solve it in
isolated-state mode and the false counterexample becomes an emitted test with
nothing marking it.

⇒ This is the shape where a detector is conditional on something unknown: the
route is wired, it runs on every collection, and it has been correct only
because its inputs happened to be internal. It must be closed before the next
collection, not after it produces a red test.

## Cause 3 -- decisions the method cannot reach BY CONSTRUCTION

Two kinds, and they are a SCOPE difference between the metrics, not a reach
difference:

* **constructor bodies** -- farming 3, st1inch 3. A constructor is not a unit;
  complete-path enumeration never has a path set for it. Branch coverage
  instruments it like any other function and counts it in the denominator.
* **internal-only compilation units** -- limit_order_protocol's whole in-scope
  file is `internal` library code, so there are 0 units and structurally 0
  reach, against a baseline of 3/3. `branch_gate.py` already prints this as N/A
  rather than FAIL, which is right, and it also means the method cannot serve a
  library-only compilation unit at all.

## Cause 4 -- the residue, which IS the method's own question

After the three above, what remains is small and specific:

* `aqua.safeBalances` -- one decision, in a unit that WAS entered and produced F
  claims. The arm was simply never witnessed.
* `BaseEscrow` modifiers (`onlyValidSecret`, `onlyBefore`,
  `onlyAccessTokenHolder`), `_ethTransfer`, `EscrowDst._withdraw`,
  `ImmutablesLib`'s four internal fee helpers -- internal bodies whose decisions
  can only be covered through a call site that inlines them.

For the internal bodies this script CANNOT distinguish "no unit calls it" from
"the call site was withdrawn by degradation or the call-depth bound". Both
present identically. Separating them needs `degraded_call_sites` and
`sc_sites_over_cap` published in `cov-report.json`'s `summary`; today they exist
only as a `log_warning` and reach no reader.

## The dominant loss inside units that WERE entered

Not visible in the decision counts at all, and larger than any of the above.
Per-unit, of a unit's own paths:

| unit | F | bounded-holds |
|---|---|---|
| Aqua.dock | 2 | 61 |
| Aqua.pull | 5 | 12 |
| FarmingPool.deposit | 7 | 147 |
| FarmingPool.withdraw | 7 | 96 |
| FarmingPool.startFarming | 26 | 24 |

`bounded-holds` means the path held at this exploration -- no counterexample, so
no test. And the invocation contract states the structural reason:

> Under `--focus-function f`, every transaction is a call to `f`. A path of `f`
> guarded by state that only another public function can establish is
> unreachable at EVERY value of `--solidity-max-tx`. Raising the tx bound cannot
> fix it; only dropping `--focus-function` can.

The collector has a `--whole` mode that drops the focus and is documented in its
own source as "the only configuration in which cross-function state can be
established inside a single transaction". **It has never been run.** That is the
next experiment, and it is free in the sense that no code has to be written for
it -- only a separate output directory, because `index.json` is rewritten per
collection and a `--whole` run in the per-method directory makes that
collection unreadable.

## What this does NOT license

The gate result stands at 0/5. Nothing above converts a FAIL into a PASS; it
partitions 121 missing decisions into 27-runs-worth of unspent budget, 12 aborts
worth zero decisions, ~6 structurally out-of-scope, and a residue of roughly a
dozen that are the method's own to answer. The correct next actions are ordered
by that partition, not by which is easiest to write up.
