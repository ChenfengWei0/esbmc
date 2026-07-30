# The geometric bracket lays probes outside the coordinate's type, and they wrap

Found while running the punched-interval loop end to end on an `address`
coordinate. NOT fixed. Written down because the fix is a policy decision, and
because the symptom it produces looks like something else entirely.

## What happens

`geometric_values(UINT256_MAX)` in `scripts/solidity_path_generalise.py` lays one
probe per power of two up to 2^255, whatever the coordinate's real type is. On a
160-bit `address` coordinate every value above 2^160-1 is out of range. The bound
is built with `constant_int2tc` **on the coordinate's own type**, so those values
WRAP, and the probes they produce are about different values than the ones the
driver wrote down.

Measured, on the two-path `Gate2` contract (`require(to != BANNED)`), one run:

    [bracket] {..., 3: '... to upper in (2^255, 2^160-1], to lower in [2^255, 1)'}

Read that as written: the tool reports a *holding* lower bound of 2^255 on a
coordinate whose whole type tops out at 2^160-1. It is not a measurement of the
path; it is the wrapped probe answering about some other value.

## What it costs

The driver's next span is `(min lo, max hi)` across the brackets, so it becomes
`(2^255, 2^160-1)` — inverted. The tool then refused with

    ERROR: --path-cov-outer-box: coordinate 'to' has hi < lo

which at the time was an `abort()`, so the whole loop died with
`timeout: the monitored command dumped core`. That abort is now an `exit(1)` with
a message naming the coordinate and both bounds (regression
`solidity_path_cov_outer_box_inverted_span_refused`), so the run records a cause
instead of a core dump — but the underlying wrap is untouched.

Consequence for yield: **no address coordinate can complete the loop today.** The
end-to-end demonstration of punched intervals had to be done on a `uint256`
coordinate for exactly this reason, which is stated in that fixture's header so
the choice does not read as arbitrary.

## Why it is not a false certificate

Worth stating precisely, because the wrap sounds worse than it is. A wrapped
probe corrupts the OUTER BOX, and the outer box feeds the subtraction — so the
candidate region can be wrong. But the region is never trusted for being
subtracted: `--path-cov-certify` is an independent query and it would refute a
wrong region. The failure mode is therefore lost yield and a dead loop, not a
test labelled certified that is red on chain.

## Why it is not fixed here

The obvious fix — clamp the ladder to the coordinate's own type range — needs the
driver to KNOW that range, and it currently does not: the report gives values,
not types. The options are (a) have the tool publish each coordinate's type range
(it already computes exactly this for `path_cov_outer_box_type_range`), (b) have
the tool clamp probe values it is given and report the clamping, or (c) infer the
type driver-side, which is guessing.

(a) is almost certainly right and is small. It is a separate change with its own
acceptance criterion — a must-flip pair on an `address` coordinate showing the
bracket landing inside the type — and folding it into the punched-interval commit
would have meant one fixture failing for two unrelated reasons.

## The check that would have caught it earlier

The same one that now guards the certification query: **a spec decimal must fit
the coordinate's own type.** That check exists on the certify side (regression
`solidity_path_cov_certify_bound_out_of_type_refused`) and does not exist on the
outer-box side. The asymmetry is not principled — it is just where the work
stopped.
