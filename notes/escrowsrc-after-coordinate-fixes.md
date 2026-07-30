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

### Diagnosed: the `param.field` name does not round-trip into the cut search

Candidate 1 of the three above, confirmed by a single run rather than reasoned
about. A certification query on `withdraw` enc=6 with the box and the
counterexample BOTH naming `immutables.taker`:

    VERIFICATION FAILED
    (no `SHRINK SUGGESTION`, and no `no single-coordinate shrink` either)

The absence of BOTH messages is what pins it. `audit_certify_witness` prints one
or the other whenever `any_named` is true, and `any_named` is set for every
bounded coordinate on which the witness could be looked up. Neither message means
the lookup failed on every coordinate — and the run cannot have failed earlier,
because a refutation with an EMPTY `inputs` aborts with the witnessless-refutation
error, which did not fire.

The mechanism is in `witness_of`: it searches `ce.inputs` by the coordinate's own
name and by a `state.`-stripped variant, and the harvest keys `inputs` by the
PARAMETER'S BASE NAME. A struct parameter is stored once, under `immutables`, as
a pretty-printed aggregate. So `immutables.taker` matches nothing, in any of the
three maps.

That is the whole of it: struct fields became bound-able coordinates, and the
cut search was never taught the same name shape. The divergence report sees one
differing coordinate because the DRIVER decomposes the aggregate; the tool's cut
search does not, so it sees none.

Fix shape, not implemented here: `witness_of` must resolve a dotted coordinate
against the aggregate rendering stored under its base name — the same
decomposition the driver already performs, which argues for the harvest keying
struct inputs by field in the first place so that neither side has to parse a
rendering. That is the better fix and the larger one.

Until then the honest reading of "no single-coordinate cut available" on a
STRUCT-FIELD coordinate is: **the cut search could not see the witness**, not
that no cut exists. The two paths above are wrongly attributed to proposition 11.

### ✅ FIXED, and it retracts the proposition-11 attribution above

`witness_of` now resolves a dotted coordinate against the aggregate stored under
its base name (depth-1 fields, exactly the driver's decomposition). Same query,
before and after, `withdraw` enc=6:

    before:  VERIFICATION FAILED, and no suggestion of any kind
    after:   PUNCH SUGGESTION  ... add immutables.taker != 4294967295 to `holes`
             SHRINK SUGGESTION ... retry with immutables.taker in [...]

**There was a cut, on a single coordinate, all along.**

⚠ **RETRACTED**: the two `withdraw` paths recorded above as instances of
proposition 11 are NOT. They were a name that did not round-trip between the
driver and the tool. A wrong attribution to a method-layer proposition is exactly
the kind of claim that gets quoted, so this is a retraction and not a tidy-up.

The genuinely multi-coordinate cases (enc=14, enc=31 — `block.timestamp` plus
several struct fields) are still multi-coordinate, but every attribution on this
contract was collected under the same defect and must be RE-MEASURED before it is
trusted, not reinterpreted.

### Re-measured after the fix: the loop now SHRINKS, and lands in the budget cell honestly

`withdraw`, 6 coordinates, 5 paths, 3 shrink rounds:

| path | before the fix | after |
|---|---|---|
| `enc=2` | region EMPTY under pins | unchanged — correctly refused |
| `enc=6` | "no single-coordinate cut available", **0 shrinks** | **3 shrinks**, then budget |
| `enc=14` | "no single-coordinate cut available", 0 shrinks | **3 shrinks**, then budget |
| `enc=30` | "no single-coordinate cut available", 0 shrinks | **3 shrinks**, then budget |
| `enc=31` | "no single-coordinate cut available", 0 shrinks | **3 shrinks**, then budget |

Every non-vacuous path now makes real progress each round where it previously
made none. The failure is "shrink round budget exhausted" — a budget statement,
and this time a true one, with the per-round boxes printed to show it.

⚠ But the shape of that progress is the one already on record as degenerate: the
cut HALVES `immutables.amount` round after round
(2^256-1 → 2^255-1 → ...), which is the same bisection that
`state.FACTORY` showed before punched intervals existed. Halving reaches a point
in ~256 rounds, so "raise the shrink budget" is not the answer here either.

What it points at is the same two mechanisms already built: level 0 (is the real
constraint an equality?) and the punched interval (is the excluded set a few
points?). Neither has been tried on a struct-field coordinate — the level-0
candidates come from the siblings' counterexamples, which for `immutables.amount`
are now available where before there was no coordinate at all.

That is the next experiment, and it is a cheap one.

### Level 0 says NO on the struct fields, so the halving is not what it looked like

Ran on the same `withdraw`:

    [level0] enc=6  single-point on: (none)
    [level0] enc=14 single-point on: (none)
    [level0] enc=30 single-point on: (none)
    [level0] enc=31 single-point on: (none)

(`enc=2`'s four "points" are the vacuous case, correctly flagged by the
one-value-candidate warning.)

So on every live path the constraint on `immutables.amount` is a genuine
inequality, not an equality — **level 0 does not apply, and neither does the
punched interval**: the excluded set is a half-space, not a few points. The two
mechanisms the previous entry pointed at are both ruled out, by measurement,
before any effort went into them.

What remains is simpler and was self-inflicted. Every run on this unit used
`--skip-bracket`, which starts each coordinate at its FULL type range. The shrink
then cuts at the witness, the witness lands near the middle, and the result is
bisection — with nothing having bracketed the boundary first. That is exactly the
job the geometric bracket exists to do (locate the boundary within a factor of
two, in one batch), and it was skipped for budget reasons.

So the honest reading of `withdraw` is a TRADE BETWEEN TWO BUDGETS, not a method
gap: pay for the bracket round and the shrink should not have to bisect; skip it
and the shrink bisects from the full type. Both halves have now been measured
separately (the bracket at 258 values per coordinate did not finish in 110s; the
shrink without it halves), and neither has been measured TOGETHER at a budget
that lets both run.

That is the next experiment, it is well-defined, and it is the one that decides
whether this unit's remaining failure is a cost statement or a method statement.
Nothing above should be quoted as either until it is run.

### ★ THE COST ANSWER: the bracket is EMISSION-bound, not solve-bound

The experiment named above — bracket and shrink together, at a budget that lets
both work:

    [round] geometric-bracket: 300.1s wall, 6 coordinate(s),
            ~1548 candidate value(s) per direction, 5 path(s)
    [round] accounting: per-query wall: n=148 max=0.074s median=0.048s total=6.9s

**148 queries reached the solver in five minutes, and 6.9 of those 300 seconds
were spent solving.** The other ~293 went to instrumenting and encoding roughly
ninety thousand claims (1548 values x 6 coordinates x 2 directions x 5 paths).

So the bracket's cost is **claim emission**, not solving — the opposite of what
every reading of a bare wall clock suggested. "The round did not finish" was
taken to mean the ladder was too long FOR THE SOLVER; the solver never saw more
than 148 of them.

Consequences:

* "bound the geometric ladder" is a **driver policy** change whose target is the
  number of claims EMITTED, not the number solved;
* the earlier "did not finish in 110s on 2 coordinates" and this "300s on 6" are
  the same phenomenon scaled by coordinate count;
* **no round measured in this whole session was solve-bound.** Every per-query
  figure collected — max 0.03–0.18s, median ~0.05s — was far from the constraint.

That last point inverts an assumption this work carried for weeks, and it is now
measured rather than assumed.

### The rate, isolated at last: ~0.26 s per CLAIM, and it is neither instrumentation nor solving

Claim count varied on one coordinate and one path, everything else fixed:

| claims emitted | GOTO creation | symex | total wall |
|---|---|---|---|
| 4 | 1.19 s | 0.11 s | 1.9 s |
| 16 | 1.17 s | 0.10 s | 4.5 s |
| 64 | 1.17 s | 0.10 s | **16.6 s** |

**GOTO creation and symex are CONSTANT.** So the cost is not the instrumentation
that emits the claims either — which is what "emission-bound" was taken to mean
one entry ago. Total wall grows with claim count while both upstream phases do
not move at all.

The remainder is the **per-claim work inside the multi-property loop**: each
claim is sliced and encoded separately before it is solved. At 64 claims,
16.6 s total against ~3.2 s of solving leaves ~0.21 s per claim of slice and
encode.

    ~0.26 s per claim total, of which ~0.05 s is the solver.

That reconciles every earlier figure. The 6-coordinate bracket lays
1548 x 6 x 2 x 5 ≈ 92,880 claims; at 0.26 s each that is about **6.7 hours**, so
"did not finish in 300 s" was never close, and the 148 queries that did reach the
solver are about what 300 s buys at this rate.

**And it supplies the number that was missing for `--claim-budget`.** A round
with a wall budget of T seconds affords roughly `T / 0.26` claims — about 1150
for a 300 s round. That is a measurement rather than a guess, and it is
per-machine, so it belongs in the flag's help rather than hard-coded as policy.

⚠ Correction to the entry above: "the round is EMISSION-bound" was right that the
cost scales with the number of claims and wrong about WHERE. Instrumentation is
flat; the per-claim slice-and-encode inside multi-property is what grows. The
practical conclusion — bound the number of claims — is unchanged, which is why
the previous entry's control is still the right one.
