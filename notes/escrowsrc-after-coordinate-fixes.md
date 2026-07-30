# EscrowSrc after the coordinate fixes: every failure has moved out of the budget cell

Re-measured 2026-07-30 on esbmc `5b8d618a19`, after four changes to what counts
as a coordinate: struct fields resolved, lowering artifacts dropped, unsettable
(immutable/constant) quantities pinned instead of generalised, and probe values
bounded by the coordinate's own type.

## Before and after

Every unit of EscrowSrc previously reported **zero generalisable coordinates**
and its paths failed as *"shrink round budget exhausted"*. All five now carry six
coordinates (the fields of `Immutables`, minus the padding member).

| unit | paths | coords | outcome now |
|---|---|---|---|
| `cancel` | 4 | 6 | 0/4 — one path vacuous under the pins, three refuted with no single-coordinate cut |
| `publicCancel` | 4 | 6 | 0/4 — round budget at 368 probes |
| `publicWithdraw` | 5 | 6 | 0/5 — round budget at 368 probes |
| `withdraw` | 5 | 6 | 0/5 — **completes at 290 probes**, see below |
| `withdrawTo` | 5 | 6 | 0/5 — round budget at 368 probes |

## `withdraw`, with the probe count matched to the budget

    [round] linear-refine: 89.8s wall, 6 coordinate(s), 5 path(s)
    [round] accounting: 230 of 290 probe(s) reached the solver;
            per-query wall: n=920 max=0.178s median=0.048s total=43.7s

The round FINISHES and the loop runs to certification. Its five paths:

* `enc=2` — region EMPTY under the pins. The path is excluded from this slice;
  the empty-region guard refuses it rather than certifying vacuously.
* `enc=6, 14, 30, 31` — **"refuted with no single-coordinate cut available"**,
  each with a NAMED divergence on struct fields (`immutables.amount`,
  `immutables.taker`, `immutables.timelocks`, plus `block.timestamp` where the
  paths disagree on it).

So not one path of this unit is in the budget cell any more. They are in the
cell proposition 11 describes: the witness differs on SEVERAL coordinates at
once, so no single-coordinate cut separates it.

## The cost statement, with the three numbers behind it

This is the first cost claim in the project that is not made from a bare wall
clock, and the numbers are what make it sayable:

* **no query hangs** — the maximum over 920 queries is 0.178s;
* **the median is 0.048s** — uniformly fast;
* **43.7s of solver time inside an 89.8s wall** — so roughly half the round is
  instrumentation and encoding per probe, not solving.

The round therefore scales with PROBE COUNT: 290 probes fit in 90s, 368 do not.
That is a volume statement, not a "the solver struggles" statement, and the
distribution is what distinguishes them. Every earlier attempt to characterise
this cost was made on runs that were simultaneously hitting the type-wrap defect,
so slow / hung / crashed were not separable.

## What is left, and what it is not

Not a ladder problem and not a search-power problem. What remains on this
contract is:

* **proposition 11** — multi-coordinate divergence, which needs a region shape
  that is not a single box, or an accepted split;
* **`block.timestamp`** — the paths disagree on it by construction (a timelock),
  so it can be neither pinned nor dropped and has to be probed as a coordinate
  (`--env-coord`, untried here);
* the vacuous-path case, which is correctly refused and is a statement about the
  pins rather than about the path.

## Addendum: `block.timestamp` promoted to a coordinate (`--env-coord`)

The paths of a timelocked function disagree on `block.timestamp` by
construction, so it can be neither pinned (that would contradict some path's own
counterexample) nor dropped (an unconstrained guard refuses certification). It
has to be probed. `--env-coord` existed for exactly this and had never been used
on real input.

Measured on `withdraw`, 7 coordinates, 5 paths:

    [round] linear-refine: 107.2s wall
    [round] accounting: 270 of 340 probe(s) reached the solver;
            per-query wall: n=1080 max=0.089s median=0.050s total=50.7s

It works: `block.timestamp` now appears in the divergence WITHOUT the
`[NOT a bounded coordinate]` tag it carried before, so the coordinate is doing
its job, and the round still finishes.

### ⚠ THE SHARPEST REMAINING LEAD, and it is new

Two paths now have a divergence on exactly ONE coordinate and still report that
no cut is available:

    enc=6:  the witness differs on: immutables.taker
            (path=730750818665451459101842416358141509832261238783, witness=0)
    enc=30: the witness differs on: immutables.taker
            (path=4294967295, witness=1)

A single differing coordinate is the case the shrink was built for, and both cuts
look legal on their face — keeping `[1, hi]` retains a path counterexample far
above the witness. Yet no `SHRINK SUGGESTION` was produced.

That is a well-specified discrepancy between the divergence report (which sees
one coordinate) and the tool's cut search (which finds none), and the two read
the SAME witness. It is the first time those two have disagreed on real input,
and it is a much narrower target than "the region does not converge".

NOT diagnosed here. The candidates, in the order worth testing: the cut search
requires the coordinate in `path_cov_certify_ce`, which the driver supplies under
a `param.field` name that may not round-trip; or the witness value used by the
report and the one used by the cut search come from different lookups; or the box
on that coordinate no longer contains the path's counterexample by the time the
last round runs. Each is a two-run experiment.
