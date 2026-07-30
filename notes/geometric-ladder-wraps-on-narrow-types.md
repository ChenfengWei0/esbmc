# The geometric bracket laid probes outside the coordinate's type, and they wrapped

Found while running the punched-interval loop end to end on an `address`
coordinate. **FIXED** — this file now records the defect, the fix, and the
measurement on both sides of it, because the symptom it produced looked like
something else entirely and would otherwise be re-diagnosed from scratch.

## What happened

`geometric_values(UINT256_MAX)` in `scripts/solidity_path_generalise.py` laid one
probe per power of two up to 2^255, whatever the coordinate's real type was. On a
160-bit `address` coordinate every value above 2^160-1 is out of range. The bound
is built with `constant_int2tc` **on the coordinate's own type**, so those values
WRAPPED, and the probes they produced were about different numbers than the ones
the driver wrote down.

Measured, on the two-path `Gate2` contract (`require(to != BANNED)`), one run:

    [bracket] {..., 3: '... to upper in (2^255, 2^160-1], to lower in [2^255, 1)'}

Read that as written: a *holding* lower bound of 2^255 on a coordinate whose
whole type tops out at 2^160-1. Not a measurement of the path — a wrapped probe
answering about some other value.

## What it cost

The driver's next span is `(min lo, max hi)` across the brackets, so it became
`(2^255, 2^160-1)` — inverted. The tool refused with `coordinate 'to' has hi < lo`,
which at the time was an `abort()`, so the whole loop died with
`timeout: the monitored command dumped core`. **No address coordinate could
complete the loop at all**, which is why the first end-to-end demonstration of
punched intervals had to use a `uint256` coordinate.

## It was never a false certificate

Worth stating precisely, because the wrap sounds worse than it is. A wrapped probe
corrupts the OUTER BOX, and the outer box feeds the subtraction — so the candidate
region could be wrong. But a region is never trusted for having been subtracted:
`--path-cov-certify` is an independent query and refutes a wrong region. The
failure mode was lost yield and a dead loop, not a test labelled certified that is
red on chain.

## The fix, in two halves

**Publish the range.** The tool has always computed each coordinate's type range
(`path_cov_outer_box_type_range`, used to seed the free bound) and kept it to
itself. It is now printed:

    --path-cov-outer-box: coordinate 'to' has TYPE RANGE [0, 2^160-1] (160-bit unsigned)

The driver reads it and bounds `geometric_values` by it. That is the half that
addresses the cause: the component choosing the ladder now knows how wide the
coordinate is. It costs no extra run — the level-0 round already publishes it
before the bracket round is laid.

**Drop what still does not fit.** A spec can name a probe value out of range
directly, so the tool drops those before emitting and reports the count:

    WARNING: ... coordinate 'to' — DROPPED 1 probe value(s) that do not fit its
    type; 1 probe value(s) remain

Dropped rather than clamped: clamping invents a probe nobody asked for, while
dropping removes one that could not have meant anything — and the type maximum is
already seeded as the free outer bound, so nothing is lost. If NO probe survives,
the coordinate is recorded in `path_cov_refused_coords` so its absence reads as a
refusal rather than as a measured full-type bound.

## Measured after the fix

The same address contract, same driver invocation that previously dumped core:

    [bracket] {2: 'to upper in (128, 255], to lower in [255, 256)',
               3: 'to upper in (2^159, 2^160-1], to lower in [0, 1)'}
    === CERTIFIED REGIONS ===
      enc=2: to in [255, 255]
      enc=3: to in [0, 2^160-1] \ {255}

Both brackets inside the type, both paths certified, and the two regions are an
exact partition of the address space.

## Regressions

* `solidity_path_cov_outer_box_out_of_type_probe_dropped` — pins the TYPE RANGE
  line, the drop count, and (as a line-anchored negative lookahead) that the
  out-of-type probe produced no claim at all.
* `solidity_path_cov_outer_box_inverted_span_refused` — pins that a malformed
  span is `exit(1)` with a legible message rather than SIGABRT. Kept: the driver
  fix removes the way this arose in practice, not the possibility of it arriving
  from some other caller.

## The check that would have caught it earlier

The same one that guards the certification query: **a spec decimal must fit the
coordinate's own type.** That check existed on the certify side
(`solidity_path_cov_certify_bound_out_of_type_refused`) and not on the outer-box
side. The asymmetry was not principled — it was just where the work had stopped,
which is exactly the shape to look for when the same class of bug reappears in a
neighbouring component.
