# EscrowSrc.cancel: `FACTORY` and `msg.sender` are the same symbol

Re-measured 2026-07-30 on esbmc `cf5ce21e16`, after the type-wrap fix, because
that fix made a specific prediction worth testing: `cancel`'s four paths had all
failed with *"shrink round budget exhausted"* while bisecting the ADDRESS
coordinate `state.FACTORY`, and the wrap defect was a live candidate cause.

**The prediction is REFUTED. The wrap was not the cause.**

## What the fix did do, and what it did not

It applied. `state.FACTORY` is now laid over the address type instead of over
2^256 — the ladder went from 258+258 = 516 candidate values per direction to
161+258 = 420, and the refine spans start at `[0, 2^160-1]` rather than
`[0, 2^256-1]`.

The outcome did not move. Still 4 witnessed paths, still 0 certified, still the
same bisection on `state.FACTORY`
(292300...595 -> 429496731 -> 214748363 -> ...). So the budget exhaustion is not
a symptom of the wrap; it is its own thing, and this run is what separates them.

## The actual cause, from the source and then confirmed behaviourally

`contracts/BaseEscrow.sol:31`:

    address public immutable FACTORY = msg.sender;

`FACTORY` is `immutable` and initialised to the deployer's `msg.sender`, so in
the model the two are ONE symbol. That is read off the source, not inferred from
a symptom — and then confirmed by intervention rather than left as a reading:

| query | reported `msg.sender` | reported `FACTORY` |
|---|---|---|
| `state.FACTORY` pinned to **7** | **7** | **7** |

Pinning one moves the other. They are the same quantity under two names.

## Why that makes the loop unable to converge here

The driver treats them as two independent coordinates: `state.FACTORY` is
bounded (it resolves as a contract-object component) and `msg.sender` is an
environment quantity left free by default. Every refutation's witness therefore
"differs on `msg.sender`", which carries `[NOT a bounded coordinate]`, so no
single-coordinate cut over the BOUNDED set can separate it — and the shrink falls
back to halving `FACTORY`, forever, until the round budget runs out.

This is the **two-coordinate equality** case, on real input, with the source line
that creates it. It is a different open problem from the one punched intervals
solved: a coordinate compared against a CONSTANT is now handled exactly
(`[0, 2^160-1] \ {v}`), while a coordinate tied to ANOTHER coordinate still
collapses both towards a point. Definition 6 makes a region a PRODUCT of
per-coordinate sets, and a diagonal is not a product — that is proposition 11's
territory, not a missing implementation detail.

## Pinning the environment does not rescue it (and says why, correctly)

Re-run with `--pin-env`, which pins `msg.sender` to 0 where all four paths agree:

    enc=6:  ... every difference between the witness and this path's counterexample
            was on a quantity whose reported value contradicts the bound this query
            assumed ... NOT the empty-divergence case: it is a payload that could
            not be compared
            NOTE: msg.sender (=134127736, assumed in [0, 0]),
                  state.FACTORY (=134127736, assumed in [0, 134127735])

Both names report the SAME out-of-bound value, one above `FACTORY`'s assumed
upper bound. The trust check added earlier this session is doing exactly its job
on real input for the first time: it refuses to offer that as the discriminating
quantity, and it refuses to collapse into the empty-divergence bucket the
reach-gate number is built from. Without it these four paths would have been
filed as "the witness agrees on every scalar" — a reach-gate hit manufactured out
of a measurement problem.

⚠ NOT diagnosed here, and deliberately left open: WHY a pinned quantity reports
an out-of-bound value at all. The entry-time-versus-later distinction is the
named candidate (`FACTORY` binds the CONSTRUCTOR's `msg.sender`, while the
transaction's is re-havoc'd), and it is a candidate, not a conclusion.

## Two other things this run measured, both firsts

**A clean bracket cost.** Geometric bracket, 2 coordinates, 4 paths, ~420
candidate values per direction: **did not finish in 110 s**. Stated exactly that
way. Every earlier figure for this came from runs that were also hitting the wrap
defect, so "did not finish", "hung" and "crashed" were not separable — and "did
not finish" is NOT evidence of "too slow". This is the first measurement of the
bracket that is not contaminated, and it is still only an upper-bound-free
statement about one cap.

**The empty-box guard fired on real input.** Under the pins, `enc=2`'s
subtraction inverted both coordinates
(`state.FACTORY: (2^160-1, 0)`) and the driver refused it as EMPTY rather than
handing an unsatisfiable assumption to the query, which would have answered
SUCCESSFUL for want of any execution.

## What would actually move this unit

Not a wider ladder. Either

* a coordinate kind that can express `c1 == c2` (proposition 11 / definition 6),
  which is a method-layer change; or
* recognising `immutable` initialisers so `FACTORY` is not offered as a free
  coordinate at all — it is not an input a generated test can set independently,
  it is fixed at deployment. That is the same class as the already-recorded
  observation that a `constant` (`BANNED`) showed up in `entry_storage`.

The second is the cheaper one and is well-defined; neither is a ladder problem.
