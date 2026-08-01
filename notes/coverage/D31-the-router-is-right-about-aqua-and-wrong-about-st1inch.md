# D31 — no backend fixes st1inch's F=0 (the prediction held), but the auto-router does not RETURN on it while an explicit flag does

**Measured 2026-08-01**, `notes/coverage/scripts/solver_arms.py`, binary mtime
`1785591755`, five backends x two vehicles, serial, `--memlimit 8g`, 180 s outer
per cell. The prediction was committed in `3dfa45cf4a` **before this was read**.

## The table

### CONTROL — `aqua safeBalances`, recorded at F=2

| backend | rc | wall | paths | F | U | solve band | reasons |
|---|---|---|---|---|---|---|---|
| auto | 1 | 3.5 s | 11 | **2** | 9 | 0.019-0.220 s | bounded-holds 9 |
| `--z3` | **-6** | 3.0 s | — | — | — | — | **SIGABRT, no report** |
| `--z3 --tuple-node-flattener` | 1 | 2.0 s | 11 | **2** | 9 | 0.002-0.070 s | bounded-holds 9 |
| `--cvc5` | 1 | 3.6 s | 11 | **2** | 9 | 0.018-0.224 s | bounded-holds 9 |
| `--bitwuzla` | 0 | 2.2 s | 11 | **0** | 11 | 0.022-0.062 s | bounded-holds 7, **solver-unknown 4** |

### QUESTION — `st1inch setFeeReceiver`

| backend | rc | wall | paths | F | U | reasons |
|---|---|---|---|---|---|---|
| auto | **timeout** | 180.2 s | — | — | — | one solve at 120.0 s, **no report** |
| `--z3` | 0 | 104.6 s | 5 | 0 | 5 | bounded-holds 2, solver-unknown 3 |
| `--z3 --tuple-node-flattener` | 0 | **103.8 s** | 5 | **0** | 5 | bounded-holds 2, solver-unknown 3 |
| `--cvc5` | **timeout** | 180.6 s | 5 | 0 | 5 | **unit-not-entered 5** (partial report) |
| `--bitwuzla` | **timeout** | 180.2 s | — | — | — | one solve at 121.0 s, **no report** |

## 1. THE PREDICTION HELD — reading 2

Only one backend both passed the control and returned on st1inch:
`--z3 --tuple-node-flattener`, and it gives **F = 0**. `auto`, `--cvc5` and
`--bitwuzla` did not return; a cell that does not return **contributes nothing,
not zero**, and is printed that way. `--z3` is VOID because it failed the control.

⇒ **No solver flag is the fix for st1inch's zero.** That is consistent with D30's
source-level mechanism (a 30-deep chain of 256-bit `mul`/`div` in the
constructor, inherited by every query) and with D14's measurement that z3's own
reason is `out of memory`, unchanged between 4 g and 16 g.

The falsifier D30 wrote down — "if any backend returns F>0, this explanation is
wrong or incomplete" — did not fire.

## 2. ⛔ BUT ROW 7's "let it auto-select" IS MEASURABLY WRONG FOR st1inch

`INVOCATION_DECISIONS` row 7 is **`solver — let it auto-select`, DECIDED (one
contract)**. On st1inch:

```
auto                             180 s, NO REPORT AT ALL
--z3 --tuple-node-flattener      103.8 s, complete report
```

**The auto-selection does not return where an explicit flag does.** The collector
already carries this as an `ENCODER_EXCEPTIONS` entry, but that entry was
justified by a *pre-fix* measurement; this is the first direct A/B of auto against
the flag on the current binary, and it reproduces.

What it buys is **report completeness, not coverage** — F is 0 either way. So
this does not move the gate. It does change what row 7 may claim: auto-selection
is not a decided default, it is a default that is measurably wrong on one of six
benchmarks and right on another (see 3).

## 3. AND THE ROUTER IS RIGHT ABOUT AQUA, ALSO MEASURED

Row 7 records that aqua auto-selects CVC5 with a stated reason: "detected
>=3-level nested-mapping shape; Bitwuzla aborts on the CONST_ARRAY-initialised
infinite mapping array". This run gives the counterfactual the row never had:

* `--bitwuzla` on aqua **fails the control** — F = 0 against the recorded 2, with
  four `solver-unknown`. The router's reason for avoiding it is vindicated.
* `--cvc5` reproduces F = 2, as the router's choice should.

⇒ The honest statement is not "the router is bad". It is **"the router is right
about aqua and wrong about st1inch, both measured"** — which is exactly why the
decision belongs in a table with per-benchmark evidence rather than as one global
default.

## 4. Two new facts about plain `--z3`

* **`--z3` alone SIGABRTs on aqua** (rc = -6, no report), while
  `--z3 --tuple-node-flattener` completes in 2.0 s. The struct-tag fix made plain
  z3 work on *st1inch*; aqua still crashes it. So the node flattener is doing
  real work on a *second* contract, for a reason not yet identified.
* Earlier the same day, `encoder_arms.py` found **`--z3 --tuple-sym-flattener`
  also SIGABRTs on aqua**. Two of the three z3 configurations abort on aqua and
  the third is the fastest cell in the whole control row (0.002-0.070 s).

⇒ Neither abort is explained here. Both are recorded as defects with a
reproduction, not as preferences.

## 5. `--cvc5` on st1inch produced a partial report reading `unit-not-entered 5`

Killed at 180 s, with a partial report whose five claims are all
`unit-not-entered` — i.e. it was killed before the unit executed, and the partial
mechanism correctly said so rather than reporting five undecided paths. Recorded
because `unit-not-entered` has been 0 across the whole corpus until now, and its
first appearance being an artefact of an outer kill is worth knowing before
anyone reads it as a reach result.

## What this does NOT say

**One unit** (`setFeeReceiver`) on st1inch and **one unit** (`safeBalances`) on
aqua. The control is what makes the st1inch cells readable at all; it does not
make them representative of either contract. And `--memlimit 8g` throughout: D14
already showed 16 g changes nothing on this contract, but nothing here re-tests
that for cvc5 or bitwuzla.
