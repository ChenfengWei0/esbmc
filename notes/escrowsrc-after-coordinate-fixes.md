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
