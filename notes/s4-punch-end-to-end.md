# S4 (the PUNCH suggestion) exercised END TO END, and the must-flip half with it

Commit `cf05807a67` shipped the driver's punch branch with an explicit label:
"NOT YET EXERCISED END TO END ... to be run when the machine is free". This is
that run. Everything below is transcript, not reconstruction.

## The shape, and why the obvious fixture cannot do this job

The DRIVER's punch fires only where the TOOL's subtraction punch does not. The
tool punches when an intersecting sibling's outer box is a SINGLE POINT on some
coordinate; when it is, the region comes back already carrying `\ {v}` and the
certification succeeds on the first query, so the driver's branch is never
reached. `solidity_path_cov_punched_ce_independent` pins exactly that
tool-side case and therefore cannot exercise the driver-side one.

What is needed instead is a region the subtraction could NOT separate, whose
refuting witness lands strictly inside an interval. Forced deliberately:

```solidity
contract Punch {
    uint256 public sink;
    function f(uint8 x) external {
        require(x >= 40);
        require(x <= 44);
        require(x != 42);
        sink = x;
    }
}
```

* 42 is not a power of two, so the geometric ladder cannot land on it and the
  sibling `x == 42` never resolves to a point -- the tool has no single value to
  remove;
* the good domain {40,41,43,44} lies inside whatever interval the ladder does
  report for that sibling, so no side cut keeps this path's own counterexample;
* the subtraction is therefore degenerate, every region comes back
  `UNSEPARATED`, and the certification query is refuted with an interior
  witness.

Measured: five witnessed paths, all five reported `UNSEPARATED`.

## The pair

Identical command apart from `--max-holes`.

**`--max-holes 2`** -- the punch branch fires EIGHT times, across four paths:

    [punch enc=2]  x != 57   |R| 2.2926833668988606e+79
    [punch enc=2]  x != 159  |R| 2.2811041579751290e+79
    [punch enc=30] x != 56   |R| 3.2421784986448534e+78
    [punch enc=30] x != 28   |R| 3.1263864094075372e+78
    [punch enc=31] x != 56   |R| 3.2421784986448534e+78
    [punch enc=31] x != 28   |R| 3.1263864094075372e+78
    [punch enc=6]  x != 56   |R| 6.4843569972897069e+78
    [punch enc=6]  x != 51   |R| 6.3685649080523907e+78

Two per path, which is the budget, after which each path falls back to the side
cut it always used. So all four stated properties of the branch are observed in
ONE run: it fires, it is budgeted per coordinate, `|R|` is monotonically
decreasing across every punch (the C3 check is live, not decorative), and
exhausting the budget hands control back to the side cut.

**`--max-holes 0`, the default** -- ZERO punch lines. Pure side cuts,
byte-for-byte the pre-S4 behaviour:

    [shrink enc=2]  x (57, 255) -> (58, 255)
    [shrink enc=2]  x (58, 255) -> (59, 255)
    ...

That is the must-flip half. A branch that fired regardless of the budget, or one
that never fired at all, would show the same output in exactly one of these two
runs; they differ, in the direction the flag names.

## Two things the run says that were NOT predicted

**1. On this shape punching does not converge, and it ends up WIDER.** After
four rounds the punched run holds `x in [208, 255]` while the unpunched run
holds `x in [61, 255]`. The reason is the one the `--max-holes` help text
already states -- against an excluded set that is not a few points, a punch
removes one value per round while a side cut crosses the boundary at once -- and
here the excluded set is enormous, because `msg.value` is unconstrained and the
ABI gate refutes at every `x`. This is the first measurement of that caveat
rather than an argument for it, and it is the reason the flag is a BUDGET and
not a switch.

**2. Nothing certified, in either run, and that is not about punching.** Both
free coordinates are the problem: `state.sink` is a state variable the ladder
brackets at `lower in [0, 1)` (dropped as degenerate, so it keeps the full
uint256 range), and 15 environment quantities are unconstrained without
`--pin-env`. The driver says so on its own second line. Recorded here so the run
is not later quoted as "punching does not certify anything" -- it was never
given a region that could.

## THIRD RUN, and it is the strongest end-to-end result this pipeline has produced

The two runs above certify nothing because `state.sink` is unbounded and the
environment is unconstrained. Adding `--pin-env --level0` on the SAME contract:

    enc=6 : x in [0, 39]                CERTIFIED
    enc=14: x in [45, 255]              CERTIFIED
    enc=30: x in [42, 42]               CERTIFIED
    enc=31: x in [40, 44] \ {42}        CERTIFIED
    enc=2 : NOT CERTIFIED — region is EMPTY on x under the current pins

Read those against the source:

    require(x >= 40);   ->  the revert path is exactly x in [0, 39]
    require(x <= 44);   ->  the revert path is exactly x in [45, 255]
    require(x != 42);   ->  the revert path is exactly {42}
    the surviving path  ->  exactly {40, 41, 43, 44}

**Every region is the path's EXACT domain, not a subset of it, and the
non-convex one is expressed exactly** -- `[40, 44] \ {42}`, which is what
Definition 5 exists for and what no plain interval can hold.

They also partition the coordinate exactly: 40 + 211 + 1 + 4 = 256, the whole
`uint8`. That is C4's coverage half (`sum |R_i| <= |type product|`) holding with
EQUALITY, on a real run, which is the strongest form the check can take. It is
not asserted by the code yet -- only the disjointness half is -- and that is
worth adding.

`enc=2` is the ABI-gate path, and pinning `msg.value = 0` excludes it from the
slice. It is correctly reported as EMPTY rather than certified: before the
vacuity work of this session it would have printed as a certificate.

## So C2 IS now exercised

`ce_in_region` runs only on the SUCCESSFUL branch. That branch ran four times
here and passed four times, so the check is no longer pure-function-tested only.
Its twin, `certified_overlap`, ran over the four certified regions and found no
intersection -- which on this input is checkable by eye against the arithmetic
above.

## One measurement worth keeping about the ladder

The geometric bracket in this run hit the 180s budget and measured NOTHING
(`[bracket] {}`), and the two linear refine rounds that followed still produced
every exact region above. So on this shape the bracket contributed nothing that
the level-0 candidates plus refinement did not already give. One shape is not a
finding about the ladder; it is a reason to measure `--skip-bracket` against the
full pipeline on the real benchmarks rather than assume the bracket is load-
bearing.
