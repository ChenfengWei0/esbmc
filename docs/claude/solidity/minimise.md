# `esbmc-minimise` — Counter-example reducer

User-facing 1-minute overview. Implementation spec lives in
[`scripts/minimise/ALGORITHM.md`](../../../scripts/minimise/ALGORITHM.md)
and the methodology doc at
[`docs/minimise/algorithm.md`](../../minimise/algorithm.md).

## What it does

Given a Solidity contract whose ESBMC verification produces a
violation, `esbmc-minimise` emits a *smaller* source that still
reaches the same violation, so the user can read the counterexample
against a compact piece of code instead of the full contract.

It is a **three-phase reducer**:

1. **Phase 0 — dead-code sweep.** Deletes contracts / functions / state
   variables / modifiers that cannot contribute to the reported
   violation (no call edge, no storage touch, no shared inheritance
   path).
2. **Phase 1 — compile-driven syntactic closure.** Repeatedly asks
   `solc` what it needs to compile the remaining source. Re-adds the
   minimum set of declarations that solc says are missing, until
   compilation succeeds.
3. **Phase 2 — verifier-driven greedy reduction.** Re-runs ESBMC on
   the reduced source and deletes statements / locals that don't
   change the oracle (same violation is still reported). Falls back
   to keep-last on any change that breaks the oracle.

## Pipeline

```
esbmc contract.sol --contract C --overflow-check --cvc5 \
      --incremental-bmc \
      --dump-violation-info /tmp/violation.json

python scripts/minimise/minimise.py \
    --input contract.sol \
    --oracle /tmp/violation.json \
    --out reduced/ \
    --esbmc-flags "--contract C --overflow-check --cvc5 --incremental-bmc"
```

Outputs under `<out>/`:
- `reduced/<same names>.sol` — reduced program
- `manifest.json` — what was removed and why (phase-by-phase accounting)
- `violation_final.json` — oracle emitted by the final verifier run

## ESBMC-side flag

| Flag | Purpose |
|---|---|
| `--dump-violation-info <path>` | After a violation is found, ESBMC writes a structured JSON oracle (contract, function, bug_type, relative_loc, trace functions, locked symbols) to `<path>`. The minimiser consumes this as the ground-truth comparator. |

## Driver layout (`scripts/minimise/`)

| File | Purpose |
|---|---|
| `minimise.py` | CLI entry point; orchestrates the three phases |
| `oracle.py` | Oracle tuple + comparison + JSON I/O |
| `solc_driver.py` | `solc` subprocess + error parsing + AST I/O |
| `source_surgery.py` | Byte-range deletion / visibility rewrite |
| `phases/phase0_sweep.py` | Dead-code sweep |
| `phases/phase1_closure.py` | Compile-driven syntactic closure |
| `phases/phase2_reduce.py` | Verifier-driven greedy reduction |
| `esbmc_driver.py` | `esbmc` subprocess + oracle extraction |
| `manifest.py` | Manifest schema + writer |
| `examples/<target>/` | Known-answer targets (smoke + realistic) |

The code depends only on the standard library plus the already-present `solc` and `esbmc` binaries invoked as subprocesses.

## When to use

- You have a large, real-world contract (hundreds of lines) that
  fails ESBMC verification.
- You want a short repro to paste into a bug report, share with a
  colleague, or examine manually.
- You want to check whether the counterexample is *tight* — the
  minimal source already shows the bug, so adjacent unrelated code
  isn't confusing the analysis.

## When NOT to use

- The contract is already small. Minimisation is an iterative
  subprocess loop; its wall-time is "several ESBMC runs" so it does
  not make sense for <50-line contracts.
- The violation depends on multi-contract interaction at a
  granularity the dead-code sweep can't shrink (e.g. the whole
  library is needed). Phase 0 will leave it intact and you'll get
  limited shrinkage.
- You want to change the verification result. The minimiser is a
  *same-oracle* reducer — if the reduction doesn't preserve the
  violation, it backtracks. It does not perform root-cause
  analysis.

## Caveats

- The oracle is the exact `(contract, function, bug_type,
  relative_loc)` tuple. If the reducer accidentally produces a
  source whose violation is at a *different* line or function, it
  counts as "broke the oracle" and is rolled back. This is
  intentional: we want the *same* bug, not any bug.
- Phase 2 greedy reduction stops when no single-statement deletion
  preserves the oracle. It does not try larger transforms (e.g.
  function inlining, folding constants).
- `solc` is invoked on every Phase 1 / Phase 2 iteration. The
  reducer accepts only sources that compile cleanly under the user's
  solc version.
