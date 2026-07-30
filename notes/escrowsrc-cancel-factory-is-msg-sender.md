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

## ⚠ SUPERSEDED IN PART — the operative cause is smaller than the above

Everything above is still true as measured, but it is not the top of the causal
chain, and reading it as such would send someone to build the wrong thing.

`FACTORY` is `immutable`. So is `RESCUE_DELAY`. Read straight off the solc AST,
which states `mutability` on every `VariableDeclaration` — not inferred from the
counterexamples, where "constant on every path" is equally true of ordinary
storage that happens not to vary:

| state variable | mutability |
|---|---|
| `FACTORY` | immutable |
| `RESCUE_DELAY` | immutable |
| `_ACCESS_TOKEN`, `PROXY_BYTECODE_HASH` | immutable |
| the other eight | constant |

**EscrowSrc declares twelve state variables and not one of them is mutable.** The
two the driver was generalising over are both fixed at construction, so no
generated test can set either. Offering them as free coordinates hands the
verifier an input space wider than reality, and certification over such a
coordinate cannot succeed — the witness just moves the quantity every round until
the shrink budget is gone. That is the "shrink round budget exhausted" this unit
reported, and it was never a search-power result.

With unsettable coordinates pinned at their counterexample value instead of
generalised, the honest output is:

    [coords] NO GENERALISABLE COORDINATE — 2 coordinate(s) are fixed at
    deployment (immutable/constant) and no test can set them: state.FACTORY,
    state.RESCUE_DELAY; 3 name(s) were refused as UNSUPPORTED because the
    coordinate kinds cannot express them: immutables,
    state.PROXY_BYTECODE_HASH, state._ACCESS_TOKEN.

`cancel(Immutables calldata immutables)` has exactly one real argument and it is
a struct the coordinate layer cannot express. So this unit has nothing to
generalise over at all, and its four paths correctly fall back to concrete
counterexample tests. **A COORDINATE-KIND result, not a search result.**

The `FACTORY == msg.sender` aliasing above remains a genuine finding about the
model and about the harvest, and the two-coordinate-equality gap is still real
and still open — it simply does not arise on THIS unit once `FACTORY` stops being
a coordinate.

## What would actually move this unit

Not a wider ladder, and — now that the immutables are out of the coordinate set —
not an equality coordinate either. What `cancel` needs is a coordinate kind for
its actual argument: a **struct**, bounded field by field. Definition 6 already
makes a region a product of per-coordinate sets, so a struct decomposed into its
scalar fields fits it without any method change; what is missing is the
resolution (`immutables.taker` is refused today as "not a coordinate shape at
all").

Still open, and NOT on this unit's critical path:

* a coordinate kind for `c1 == c2` (proposition 11 / definition 6) — real, and
  the `FACTORY`/`msg.sender` aliasing is a genuine instance of it, but it stops
  arising here once `FACTORY` is not a coordinate;
* why the harvest reports the CONSTRUCTOR's `msg.sender` under `env.msg.sender`.
  Named, measured, unfixed.

The census that produced this is worth repeating elsewhere: **the coordinate list
had non-settable quantities in it, and on this contract that was 100% of it.**
The same check should be run on every benchmark before any yield number is
quoted, because a coordinate no test can set makes certification fail for a
reason that has nothing to do with the method.
