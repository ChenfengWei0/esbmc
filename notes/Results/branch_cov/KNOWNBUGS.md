# Stage 0 — pilot KNOWNBUG index

> **2026-05-15 Stage 2C delta.** This page is the Stage-0/1
> (2026-05-14) snapshot. Stage 2C (commit `3d6d424b73`) removed the
> `bare smt_sort` SMT-backend abort. The aqua pin family was re-pinned:
> `cov_pilot_aqua2A_4lvl_addr_addr_bytes32_addr_readonly` **flipped
> KNOWNBUG→CORE** (now `Branch Coverage: 75%`); the other 7 aqua pins
> stay KNOWNBUG but now stop at independent pre-existing symex/IR walls
> (`value_set base_type_eq` / `with2t is_array_type` on deep
> nested-mapping *writes*), not `bare smt_sort`. Authoritative current
> state + per-pilot table: **`STAGE2C_FOLLOWUP_REPIN.md`**
> (fix closure: `STAGE2C_2d_RESULT.md`). The "18"/SIGABRT rows below
> are historical.

Generated 2026-05-14. 18 KNOWNBUG regression tests under
`regression/esbmc-solidity/cov_pilot_*/` pinning the current behaviour
of ESBMC `--branch-coverage-claims` on real-world 1inch contracts.
Each test passes today (KNOWNBUG verdict satisfied); when the
underlying issue is fixed, the test FAILS and must be flipped to CORE.

## Aggregate

`ctest -j 4 -R cov_pilot_` (from `build/`) on 2026-05-14:
**18 / 18 PASS**, total wall-clock 29.74 s.

| Class | # tests | Verdict pinned |
|---|---:|---|
| Crash | 1 | SIGABRT (`bare smt_sort`) |
| ~~Contract GOTO-gen SIGABRT~~ | 3 → 0 KNOWNBUG | **Stage 3 crash-fix FIXED 2026-05-15** (library-receiver `convert_type_expr` gate — sound, kept). EscrowDst→KNOWNBUG (the `Branches:90` it now emits is NOT a coverage win — it is the **`--contract` scoping bug**, see `COVERAGE_CONTRACT_SCOPING_ROOTCAUSE.md`); Farming/St1inch library-crash gone, KNOWNBUG on NEW independent blockers |
| Library per-function, no-branch | 13 | `No branch detected` |
| Library per-function, Reached: 0 | 1 | `Branches: 2, Reached: 0, Coverage: 0%` |
| **Total** | **18** | |

## Index

### Contract pins (4)

| # | Test directory | Source contract | Native (BRF / BRH / %) | ESBMC pinned (BRF / BRH / %) | Wall (ctest) |
|---|---|---|---:|---:|---:|
| 1 | `cov_pilot_aqua_Aqua/` | **Stage 1 minimal repro (16 lines)** ← was aqua/src/Aqua.sol flat | n/a (minimised) | SIGABRT `bare smt_sort` | 0.7 s |
| 2 | `cov_pilot_farming_FarmingPool/` | farming/contracts/FarmingPool.sol | 30 / 24 / 80 | KNOWNBUG (re-pinned `^Branch Coverage: [1-9]`): library `$address` crash FIXED; NEW indep. bug `getTotalSupply` in `struct BytesStatic` |
| 3 | `cov_pilot_st1inch_St1inch/` | st1inch/contracts/St1inch.sol | 58 / 31 / 53 | KNOWNBUG (re-pinned `^Branch Coverage: [1-9]`): library `$address` crash FIXED; now genuine ~92 s k-induction budget-burn (solo: 0% → KNOWNBUG PASS; ctest-Timeout only under -j4 load — accepted k-induction-timeout class, not a Stage-3 regression) |
| 4 | `cov_pilot_cross_chain_swap_EscrowDst/` | cross-chain-swap/contracts/EscrowDst.sol | 2 / 2 / 100 | **KNOWNBUG** (walked back from a wrong CORE flip, 2026-05-15). Library-receiver crash-fix lets it run, but `Branches : 90` is the **`--contract` scoping bug** output: 48 lib + 38 base-modifier-spliced + only 4 in `contract EscrowDst` (captured via `--show-claims`). Pinned `^Branches : 90$` + `^Branch Coverage: [1-9]$` (latter fails today → stable KNOWNBUG). Root cause + fix: `COVERAGE_CONTRACT_SCOPING_ROOTCAUSE.md` |

### Library per-function pins (14)

All under `cov_pilot_lop_MakerTraitsLib_<fn>/`. Detailed ledger at
`STAGE0_LIBRARY_FUNCTION_PINS.md`.

Aggregated: 14 functions, Σ ESBMC BRF = 2, Σ ESBMC BRH = 0, native
file-level BRF = 4 / BRH = 4 / 100 %.

## Finding → stage mapping

| Pilot finding (from `PILOT_FINDINGS.md`) | Pinned by | Diagnostic stage |
|---|---|---|
| (a) bare smt_sort SIGABRT | `cov_pilot_aqua_Aqua` (now minimal 16-line repro post-Stage-1) | **Stage 1 closed** 2026-05-14, see `STAGE1_SIGABRT.md`; Stage 2 fix proposal sketched |
| (b) ~~Reached: 0~~ → GOTO-gen SIGABRT (contracts) | 3 contract pins | **Stage 3 DIAGNOSED + FIXED** 2026-05-15, see `STAGE3_REACHED0_DIAG.md`. Root cause = `convert_type_expr` lowers `address(this)` to `member_exprt(<library struct>, "$address")` inside `library` bodies. Fix: `struct_type_has_component` gate → `_ESBMC_enclosing_contract_address` (DELEGATECALL model) — **sound, kept**. The crash-fix only *unblocked* these from aborting; it did NOT make EscrowDst's coverage correct. EscrowDst→KNOWNBUG (walked back from a wrong CORE flip): its `Branches : 90` is the separate `--contract` scoping bug (`COVERAGE_CONTRACT_SCOPING_ROOTCAUSE.md`), not a 41.1% coverage win. Farming/St1inch library-crash gone, KNOWNBUG on 2 NEW independent blockers (`getTotalSupply`/BytesStatic; k-induction budget-burn). 130/130 gauntlet, cppcheck clean |
| (b) Reached: 0 (library function) | `cov_pilot_lop_MakerTraitsLib_useBitInvalidator` | Stage 3 (deferred) |
| (c) Library coverage methodology | 14 library pins (per-function `--function`) | Stage 4 (refinement) |
| (d) solc version pinning | embedded in `contract.solast` (pre-generated) | resolved |
| (e) Yul `[approx]` warnings | captured in `farming/_results/` (Stage 0 by-product, not blocking) | informational |

Each pin's `test.desc` regex captures the current (failing) behaviour
exactly. A future fix that flips the verdict will fail the regex
match, alerting the maintainer to flip KNOWNBUG → CORE.

## Reproduction

```bash
# Regenerate test directories from notes/Results/branch_cov/esbmc/inputs/:
bash notes/Results/branch_cov/esbmc/stage0_generate.sh

# Register new dirs with cmake (one-time after generation):
cd build && cmake . && cd ..

# Run all pilot KNOWNBUG tests:
cd build && ctest -j 4 --output-on-failure -R cov_pilot_
# Expected: 18 / 18 PASS in ~30 s wall-clock.
```

## Artifacts

- Per-test source: `regression/esbmc-solidity/cov_pilot_*/{contract.sol, contract.solast, test.desc}`
- Per-pilot generator: `notes/Results/branch_cov/esbmc/stage0_generate.sh`
- Background: `notes/Results/branch_cov/PILOT_FINDINGS.md`
- Library ledger: `notes/Results/branch_cov/STAGE0_LIBRARY_FUNCTION_PINS.md`
- Scope-clarification memory: `feedback_function_flag_scope.md` (`--function` ban exempts library coverage tests)
