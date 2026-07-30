# Census: 17% of the corpus's state variables are settable by a generated test

Run by `coordinate_settability_census.py` on the six flattened benchmark inputs,
2026-07-30. Read off the solc AST's `mutability` field, never inferred.

| input | state vars | mutable | immutable | constant | settable % |
|---|---|---|---|---|---|
| `aqua__Aqua` | 5 | 1 | 0 | 4 | 20% |
| `cross-chain-swap__EscrowDst` | 12 | 0 | 4 | 8 | **0%** |
| `cross-chain-swap__EscrowSrc` | 12 | 0 | 4 | 8 | **0%** |
| `farming__FarmingPool` | 26 | 8 | 2 | 16 | 31% |
| `limit-order-protocol__MakerTraitsLib` | 15 | 0 | 0 | 15 | **0%** |
| `st1inch__St1inch` | 73 | 15 | 36 | 22 | 21% |
| **total** | **143** | **24** | **46** | **73** | **17%** |

## Why this matters more than it looks

An `immutable` is fixed at construction; a `constant` lives in the code. Neither
is an input, and `vm.store` reaches neither. But the counterexample harvest
reports both under `entry_storage`, because the model makes them members of the
contract object — so the generalisation driver was turning them into FREE
coordinates and asking the verifier to range over them.

That hands the verifier an input space **wider than reality**. Certification over
such a coordinate cannot succeed: the witness simply moves the quantity, round
after round, until the shrink budget runs out. The failure then reads as weak
search, and the obvious response — a longer ladder, a bigger shrink budget — is
guaranteed not to help.

Measured, on the unit where this was found: `EscrowSrc.cancel` reported
*"shrink round budget exhausted"* on all four paths for exactly this reason. Both
of its free coordinates (`state.FACTORY`, `state.RESCUE_DELAY`) are `immutable`.
With them pinned instead of generalised, the honest output is that the unit has
**no generalisable coordinate at all** — its one real argument is a struct the
coordinate kinds cannot express. A coordinate-kind result, not a search result.

## How to read the number, and how not to

**This counts DECLARATIONS per flattened input, not the coordinates any
particular unit received.** A unit's coordinate list is the subset its own
counterexample mentions, so 17% is an upper bound on how much of a real
coordinate list is settable, not the figure itself. Quoting it as "83% of
coordinates were bogus" would be over-reading it.

What it does support: **the coordinate list contained non-settable quantities,
and on two of six inputs that was 100% of the state.** Any yield number collected
before this exclusion existed was measured on a coordinate set that included
quantities no test can set, and has to be re-collected rather than reinterpreted.

## The general rule this is an instance of

The three refusal families now in the driver all say the same thing in different
words — a coordinate must be something a generated test can actually set:

* **not scalar** (struct, mapping, dynamic array) — cannot be bounded at all;
* **not settable** (immutable, constant) — bounded fine, but no test can produce
  the value;
* **not expressible** (signed, bool, out-of-type decimal) — the bound itself
  would be built wrong.

Only the first was implemented when the corpus figures were first collected. The
second is this note. The third landed the same day (type-range publication).

## Reproduce

    python3 notes/coordinate_settability_census.py

It reads `notes/coverage/inputs/*.solast` and needs nothing else — no solver, no
run, no clock.
