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
| Contract Reached: 0 | 3 | `Branches: N, Reached: 0, Coverage: 0%` |
| Library per-function, no-branch | 13 | `No branch detected` |
| Library per-function, Reached: 0 | 1 | `Branches: 2, Reached: 0, Coverage: 0%` |
| **Total** | **18** | |

## Index

### Contract pins (4)

| # | Test directory | Source contract | Native (BRF / BRH / %) | ESBMC pinned (BRF / BRH / %) | Wall (ctest) |
|---|---|---|---:|---:|---:|
| 1 | `cov_pilot_aqua_Aqua/` | **Stage 1 minimal repro (16 lines)** ← was aqua/src/Aqua.sol flat | n/a (minimised) | SIGABRT `bare smt_sort` | 0.7 s |
| 2 | `cov_pilot_farming_FarmingPool/` | farming/contracts/FarmingPool.sol | 30 / 24 / 80 | 338 / 0 / 0 | 12.3 s |
| 3 | `cov_pilot_st1inch_St1inch/` | st1inch/contracts/St1inch.sol | 58 / 31 / 53 | 688 / 0 / 0 | 25.7 s |
| 4 | `cov_pilot_cross_chain_swap_EscrowDst/` | cross-chain-swap/contracts/EscrowDst.sol | 2 / 2 / 100 | 90 / 0 / 0 | 2.8 s |

### Library per-function pins (14)

All under `cov_pilot_lop_MakerTraitsLib_<fn>/`. Detailed ledger at
`STAGE0_LIBRARY_FUNCTION_PINS.md`.

Aggregated: 14 functions, Σ ESBMC BRF = 2, Σ ESBMC BRH = 0, native
file-level BRF = 4 / BRH = 4 / 100 %.

## Finding → stage mapping

| Pilot finding (from `PILOT_FINDINGS.md`) | Pinned by | Diagnostic stage |
|---|---|---|
| (a) bare smt_sort SIGABRT | `cov_pilot_aqua_Aqua` (now minimal 16-line repro post-Stage-1) | **Stage 1 closed** 2026-05-14, see `STAGE1_SIGABRT.md`; Stage 2 fix proposal sketched |
| (b) Reached: 0 (contracts) | 3 contract pins | Stage 3 (deferred) |
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
