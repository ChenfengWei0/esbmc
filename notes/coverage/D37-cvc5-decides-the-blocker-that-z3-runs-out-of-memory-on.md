# D37 — CVC5 decides the exact shape z3 runs out of memory on, and the auto-router picks the backend that is 200× slower on the easy half

**Measured 2026-08-01** on D36's 60-line reproduction. Five backends × two widths,
serial, `--memlimit 4g`, 200 s outer per cell. **This experiment was impossible
before D36**: on the real contract every backend either takes minutes or never
returns, so no backend comparison could be read. On a fixture that runs in
seconds it can.

## The matrix

| width | backend | paths | F | U | min solve | max solve | actually used |
|---|---|---|---|---|---|---|---|
| `uint64` | auto | 4 | **4** | 0 | 9.662 s | 13.677 s | AUTO → Bitwuzla |
| `uint64` | `--z3` | 4 | **4** | 0 | **0.051 s** | **0.061 s** | Z3 |
| `uint64` | `--z3 --tuple-node-flattener` | 4 | **4** | 0 | 0.038 s | 0.053 s | Z3 |
| `uint64` | `--cvc5` | 4 | **4** | 0 | 0.171 s | 0.303 s | CVC5 |
| `uint64` | `--bitwuzla` | 4 | **4** | 0 | 10.271 s | 14.117 s | Bitwuzla |
| `uint256` | auto | — | — | — | 120.230 s | 120.230 s | AUTO → Bitwuzla, **NO REPORT** |
| `uint256` | `--z3` | 4 | **0** | 4 | 3.876 s | 17.115 s | Z3, `out of memory` |
| `uint256` | `--z3 --tuple-node-flattener` | 4 | **0** | 4 | 3.893 s | 17.600 s | Z3, `out of memory` |
| **`uint256`** | **`--cvc5`** | 4 | **4** | **0** | **3.667 s** | **5.226 s** | **CVC5 — SOLVES IT** |
| `uint256` | `--bitwuzla` | — | — | — | 120.000 s | 120.000 s | Bitwuzla, **NO REPORT** |

## 1. The blocker is backend-sensitive after all

**CVC5 witnesses all four paths at `uint256` in 3.7–5.2 s**, on the exact shape
where z3 reports `out of memory` and bitwuzla does not return inside 120 s.

⇒ D31 concluded "the zero is NOT a backend choice". That conclusion was measured
on the REAL st1inch with a 180 s cap, and it stands **for that measurement**. What
it cannot support — and what this run contradicts — is the general claim about the
MECHANISM. The mechanism D36 isolated is one CVC5 handles.

**Correction of scope, not a withdrawal**: D31's cells are all still valid. What
changes is that "no backend fixes it" was read as a property of the difficulty,
and it is a property of the difficulty AT THAT BUDGET ON THAT CONTRACT.

## 2. ⛔ The auto-router's stated rule is contradicted on this shape

ESBMC prints, when it auto-selects here:

> `Solidity: auto-selecting 'bitwuzla' as SMT backend (Z3 is much slower on
> 256-bit bit-vector arithmetic).`

On this shape, measured:

* at `uint64`, **z3 is 200× FASTER than bitwuzla** (0.051 s against 10.271 s);
* at `uint256`, bitwuzla does not return at all while **CVC5 is the only backend
  that decides it**.

So the router picks the slowest backend on the easy half and a non-returning one
on the hard half, for a stated reason that this shape falsifies. That is a
concrete defect in the selection rule, not a preference — and it extends the
amendment `INVOCATION_DECISIONS` row 7 already carries (right about aqua, wrong
about st1inch): here it is wrong in a THIRD way, with the reason printed on
screen.

## 3. What this means for the corpus, and it is not small

**st1inch was collected with `--z3 --tuple-node-flattener`** — chosen because it
was the only configuration that produced a report at all, and recorded as such in
`ENCODER_EXCEPTIONS`. It is now measured to be the one backend family that cannot
decide the shape which dominates that contract.

D31 did try `--cvc5` on the real st1inch and it did not return in 180 s. **That
cap was chosen before any of this was known**, and the isolated blocker takes CVC5
3.7–5.2 s on a 60-line fixture against z3's failure. So the right follow-up is
CVC5 on the real contract with a budget chosen in the light of this, and it is
running.

⇒ If it lands, st1inch's `0/86` moves, and that is the first thing measured in
this whole session that could move the gate rather than explain it.

## What is NOT claimed

Nothing here says CVC5 will decide the real st1inch — the real contract carries
much more than this fixture, and D31 measured it not returning at 180 s. This says
the ISOLATED blocker is one CVC5 handles, which makes the retry worth its budget.
And nothing here reads the SMT conversion: why z3 exhausts memory on a
355-assignment formula it encodes in two milliseconds is still unexamined.
