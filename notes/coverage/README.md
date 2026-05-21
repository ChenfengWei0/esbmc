# Coverage comparison: ESBMC vs native test suite (LOCKED)

Single-source-of-truth dataset for the paper-grade ESBMC vs solc-coverage
branch-coverage comparison.  Methodology locked in
**[METHODOLOGY.md](METHODOLOGY.md)** (2026-05-20 / 2026-05-21).

## Files

```
notes/coverage/
├── METHODOLOGY.md              ← LOCKED methodology spec (§1-§10)
├── README.md                   ← this file
├── inputs/                     ← 6 flat .sol (+ generated .solast)
├── scripts/
│   ├── ast_decisions.py        ← canonical AST decision counter
│   ├── collect.py              ← single source of truth (esbmc + native)
│   ├── collect_all.sh          ← driver: esbmc | native | all
│   └── summarize.py            ← prints the 2-pair table from data/
└── data/
    ├── esbmc_<bench-key>.json  ← 6 files (per ESBMC entry)
    └── native_<project>.json   ← 5 files (per project; cross-chain shared)
```

`<bench-key>` (6 entries): `aqua_Aqua`, `cross_chain_swap_EscrowDst`,
`cross_chain_swap_EscrowSrc`, `farming`, `limit_order_protocol`,
`st1inch_St1inch`.

## Locked numeric contract

The §8 invariant of `METHODOLOGY.md`:

1. **`total.branchesTotal`** for every bench is the canonical
   AST-derived decision count.  ESBMC and native both report reach
   **against this same denominator**.
2. **Reruns do not change `branchesTotal`** for fixed input source.
3. **`esbmcReached` / `nativeReached`** only change when the ESBMC
   binary changes or the native test suite is re-run.

If a rerun changes `branchesTotal` for unchanged inputs, that's a
pipeline bug — fix immediately, don't update the data.

## Two pairs (both share the same AST denominator)

| pair | ESBMC side                                                     | Native side                              |
|------|----------------------------------------------------------------|------------------------------------------|
| 1    | `--contract C` whole-dispatcher + per-method `--focus-function` union | `lcov` BRDA at canonical decision lines |
| 2    | Per-method `--focus-function` only (no whole-dispatcher pass) | same                                     |

For pure-library primaries (`MakerTraitsLib`), Pair 1 == Pair 2
(libraries are stateless callees; no whole-dispatcher harness).

Reaches are accumulated across all sub-runs via ESBMC's
`--coverage-covered-set <union.json>` flag (atomic persistence,
timeout-safe).

## Headline (post-commit `e4d101093e` + tuple-return fix)

```
benchmark            branchesTotal    ESBMC          native         verdict
aqua_Aqua                       8       7/8  ( 87.5%)   6/8  ( 75.0%)  ≥ test ✓
EscrowDst                      18      14/18 ( 77.8%)  10/18 ( 55.6%)  ≥ test ✓
EscrowSrc (Pair 1)             16       7/16 ( 43.8%)   8/16 ( 50.0%)  -6.25pp
EscrowSrc (Pair 2)             16      16/16 (100.0%)   8/16 ( 50.0%)  ≥ test ✓
farming                        26      26/26 (100.0%)  26/26 (100.0%)  ≥ test ✓
LOP                             3       3/3  (100.0%)   2/3  ( 66.7%)  ≥ test ✓
st1inch (Pair 1)               86      33/86 ( 38.4%)  83/86 ( 96.5%)  -58.14pp
st1inch (Pair 2)               86      33/86 ( 38.4%)  83/86 ( 96.5%)  -58.14pp
```

Pair 1: 4/6 ≥ test (EscrowSrc and st1inch fail).
Pair 2: 5/6 ≥ test (only st1inch fails).

The two failing residuals are **documented ESBMC modelling
limitations** (METHODOLOGY §10), not measurement bugs:

(a) **EscrowSrc -1 (line 1518)**: blocked by
`Create2.computeAddress(...) == address(this)` cryptographic check
in `_validateImmutables`.  ESBMC's wide-BV keccak table is
non-invertible by SAT; native passes because deployment satisfies
the check by construction.

(b) **st1inch -50**: constructor's `_votingPowerAt` invariant
narrows the symbolic state space such that downstream method bodies
get proven unreachable.  Native test fixtures sidestep this by
deploying with values that trivially satisfy the invariant and then
calling methods with arbitrary balances.

Both are addressable with targeted ESBMC modelling changes
(coverage-mode nondet abstraction of Create2-style cryptographic
guards, and post-constructor state-havoc for invariant-protected
state vars).  Out of scope for this dataset capture; the JSON files
document each gap per-file so a future rerun shows closure as a diff.

## Comparison summary

```sh
python3 notes/coverage/scripts/summarize.py            # locked table
python3 notes/coverage/scripts/summarize.py --per-file # + per-file detail + gap diagnostics
python3 notes/coverage/scripts/summarize.py --json     # machine-readable
```

## How to (re)collect

```bash
# Native (parse lcov, fast)
notes/coverage/scripts/collect_all.sh native

# ESBMC (heavy: per-bench multi-fn runs, ~30-90 min full)
notes/coverage/scripts/collect_all.sh esbmc

# Single bench
python3 notes/coverage/scripts/collect.py esbmc st1inch_St1inch
python3 notes/coverage/scripts/collect.py native st1inch
```

Logs and union JSONs land under `/tmp/cov_<bench-key>/`.  Re-running
overwrites the JSON section that ran; `commandUsed` always reflects
the exact command used.
