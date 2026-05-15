# ESBMC branch-coverage pilot — findings (2026-05-14)

> **2026-05-15 Stage 2C delta.** Finding (a) "aqua/Aqua SIGABRT
> `bare smt_sort`" is **fixed** by Stage 2C (commit `3d6d424b73`,
> closure `STAGE2C_2d_RESULT.md`). Post-fix the aqua read-only deep
> nested-mapping pilot flipped KNOWNBUG→CORE (`Branch Coverage: 75%`);
> the deep nested-mapping *write* pilots now stop at independent
> pre-existing symex/IR walls (`value_set base_type_eq` /
> `with2t is_array_type`), diagnosis deferred. Authoritative post-2C
> per-pilot table: **`STAGE2C_FOLLOWUP_REPIN.md`**. The Issue (a)
> section below is the historical 2026-05-14 record.

5-contract pilot run with the user-specified flag set
`--no-assertions --quiet --k-induction --unlimited-k-steps --memlimit 8g
--timeout 90 --branch-coverage-claims`. ESBMC version 8.2.0 at
`release-bundle/bin/esbmc` (built 2026-05-14 11:46, which includes
commit `9c08954c54` *[Solidity] auto-enable --no-assertions /
--no-symex-pointer-check under coverage*).

## TL;DR

5 distinct verdicts, NONE produce a coverage % comparable to the native
side. The pilot's value is in surfacing this **before** the 50-file
scale-up.

| # | Pilot | Verdict | ESBMC BRF / BRH / % | Native BRF / BRH / % | Notes |
|---|---|---|---:|---:|---|
| 1 | aqua / Aqua | **SIGABRT** | 166 / 0 / 0% | 12 / 12 / 100% | Hits the known `bare smt_sort` bug (`reference_smt_sort_solver_native.md`); ESBMC v8.2.0 aborts loudly at solver-encode time |
| 2 | farming / FarmingPool | SUCCESSFUL | 338 / 0 / 0% | 30 / 24 / 80% | Coverage tracker reports `Reached: 0` for all 338 detected branches across k=1..k=15 — see § "Why Reached: 0" |
| 3 | LOP / MakerTraitsLib | NO-TARGETS | n/a | 4 / 4 / 100% | `library`-only file → ESBMC: "No verification targets(contracts) were found in the program." `--contract <library-name>` is not accepted |
| 4 | st1inch / St1inch | SUCCESSFUL | 688 / 0 / 0% | 58 / 31 / 53% | Same Reached: 0 pattern as farming; 65 s wall, peak RSS 1.0 GB |
| 5 | cross-chain-swap / EscrowDst | SUCCESSFUL | 90 / 0 / 0% | 2 / 2 / 100% | Same Reached: 0 pattern; 12 s wall |

All 5 artifacts are captured under
`notes/Results/branch_cov/esbmc/logs/<bench>__<contract>/`.

## Issue (a) — aqua/Aqua SIGABRT

```
ESBMC internal error: bare smt_sort (id=4) reached to_solver_smt_sort<>;
this sort was produced by the tuple flattener and must only flow back
through the tuple_api codepath, not into a backend's mk_array_sort /
mk_const / mk_array_symbol.
```

Documented in `reference_smt_sort_solver_native.md` as the deliberate
loud-abort replacement for an earlier silent SIGSEGV. Pre-existing
ESBMC limitation — not introduced by this pilot. Aqua's Solidity is
heavy on user-defined-value-types (Balance is a `type Balance is
uint256`) and tuple-typed library helpers; the tuple-flattener carve-out
in `smt_conv.cpp` is missing a shape for the configuration aqua produces.

**Outcome:** unblocking aqua needs an ESBMC patch (out of pilot scope).
The artifact captures the abort message for triage. Aqua is **deferred**
from scale-up until the bare-smt_sort routing is fixed.

## Issue (b) — Reached: 0 for all 3 SUCCESSFUL pilots

The coverage tracker detects 90 / 338 / 688 branches (farming /
st1inch / EscrowDst-respectively cross-chain-swap) but reports
`Reached: 0` for every single one, at every k from 1 through 15.

**Confirmed not caused by `--no-assertions`**: dropping that flag from
the CLI gives the same `Reached: 0` result, because commit
`9c08954c54` auto-enables `--no-assertions` whenever any
`--branch-coverage*` flag is on (Solidity input path,
`esbmc_parseoptions.cpp:3072`).

**Working hypothesis** (needs ESBMC-side confirmation): the dispatcher-
loop neutralisation in coverage mode (`reference_coverage_mode_dispatcher_neutralization.md`)
collapses the harness `while(nondet_bool()) { switch(...) { ... } }` to
**zero iterations** rather than the documented "≤ 1 call". The
nondet-bool guard reaches FALSE on the first turn, so the switch body
that calls user functions is never entered, and the instrumented branch
assertions inside user code are never executed.

Evidence:
- Base case k=1 generates `0 VCC(s)` for the coverage assertions, even
  before slicing (with `--no-assertions` removed, the inductive step at
  k=2 generates `1597 VCC(s)` — but the coverage-claim count stays 0).
- All k-induction phases (base / forward / inductive) report the same
  Branches: N, Reached: 0 line, indicating the count is computed once
  pre-execution and never updated.
- Peak wall-clock for st1inch (65 s) is consistent with the inductive
  step doing real work on user-function bodies, yet Reached: 0 — meaning
  the bodies are being symbolically explored but the harness's coverage-
  tracking entry point is never crossed.

**To validate the hypothesis**: dump `--goto-functions-only` and check
whether `_ESBMC_Main*`'s call to `nondet_bool` is collapsed to constant
`false` post-neutralisation, vs the expected behaviour of inlining the
switch body to "execute every branch exactly once."

**Not validated in pilot scope.** This is the single biggest blocker for
the comparison; it must be diagnosed before scale-up makes sense.

## Issue (c) — Library `--contract` rejection

```
ERROR: No verification targets(contracts) were found in the program.
```

ESBMC's `--contract` flag accepts `contract` and `abstract contract`
declarations but not `library`. Pure-library files (LOP/MakerTraitsLib,
LOP/OffsetsLib, LOP/TakerTraitsLib, cross-chain-swap/ProxyHashLib,
cross-chain-swap/TimelocksLib, …) cannot be the verification entry
point.

**Workaround for scale-up** (out of pilot scope): wrap each library in a
thin harness contract that delegates every public function:

```solidity
contract _MakerTraitsLibHarness {
    using MakerTraitsLib for MakerTraits;
    function call_someFn(MakerTraits m, ...) external returns (...) {
        return m.someFn(...);
    }
    // … one wrapper per library public function …
}
```

Or **drop libraries from the comparison set** entirely (50 → ~43
files). The wrapper approach inflates branch counts further and shifts
ESBMC's harness behaviour; the drop approach is cleaner for a paper
comparison but reduces benchmark size.

## Issue (d) — Pragma version pinning

`cross-chain-swap` (`foundry.toml`) and `st1inch` (transitively, via
`@1inch/solidity-utils`) hard-pin `pragma solidity 0.8.23;`. The
default solc 0.8.30 rejects the strict-equality pragma with a
`Source file requires different compiler version` error.

**Workaround applied in pilot**: `run_pilot.sh` per-benchmark
`solc_bin_of()` switch points ESBMC at
`~/.solc-select/artifacts/solc-0.8.23/solc-0.8.23` for these two. Done.
Replicable for scale-up by extending the `case` arms.

## Issue (e) — Yul `[approx]` warnings

farming/FarmingPool emits **18 inline-assembly over-approximation
warnings** (Yul `mload`, `mstore`, `chainid`, `staticcall`, `call`,
`convert_failure`, …). Each Yul construct falls through to a havoc of
its outputs (per `reference_yul_lowering.md`).

This is normal ESBMC behaviour for 1inch's heavy use of inline assembly
optimisations, but it means farming's symex result is an
over-approximation. The artifact captures each warning with its source
file:line. For paper presentation, the % of pilot files emitting
`[approx]` should be noted.

## Recommendations

1. **Diagnose Issue (b) first** — without coverage non-zero on at least
   one real contract, the 50-file scale-up has no comparable data to
   collect. Suggested debug:
   - Run `--goto-functions-only` on `farming__FarmingPool.flat.sol`
     and check `_ESBMC_Main_FarmingPool`'s body — is the dispatcher
     loop collapsed, partially unrolled, or fully neutralised?
   - Cross-check against a regression test that DOES report non-zero
     coverage today (something under `regression/esbmc-solidity/cover_*`)
     — confirm that matches the expected behaviour, then diff.

2. **Defer issues (a), (c)** to follow-up patches — they're not blockers
   for the main comparison since they affect a minority of the 50 files.

3. **Apply Issue (d) per-benchmark solc pinning** to all of scale-up;
   the current `solc_bin_of()` is the template.

4. **Issue (e) is benign** — keep the `[approx]` warnings in the artifact
   for transparency; paper text should disclose.

## Artifact pointer

- Per-contract details: `notes/Results/branch_cov/esbmc/logs/<bench>__<contract>/`
- Aggregated TSV: `notes/Results/branch_cov/esbmc/per_contract.tsv`
- Native side (for comparison): `notes/Results/branch_cov/tests/per_file_branches.tsv`
- Pilot manifest: `notes/Results/branch_cov/_pilot_contracts.tsv`
