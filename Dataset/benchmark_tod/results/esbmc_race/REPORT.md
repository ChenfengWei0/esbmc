# SolidiFi TOD re-run with `--tod-race-check=auto` (after F4/HARNESS_ORDER/F2 fixes)

Date: 2026-04-21
Binary: `build/src/esbmc/esbmc` @ commit `3408848546`

## What changed since the 2026-04-17 baseline

Three fixes to the Solidity-frontend TOD machinery landed between the
last SolidiFi run and this one:

| Commit | Change |
|---|---|
| `d7dcdadbbf` | **F4** — TOD harness synthesises shadow public getters (`__tod_get_<name>`) for internal/private scalar state vars and mappings.  Closes the "no state variable touched by both / verification trivially succeeds" gap. |
| `14701c0753` | **HARNESS_ORDER_BUG family** — race-mode harness threads ctor args through the test function and adds `memory` to reference-type params; skip emission entirely for non-returnable state-var types (arrays, structs) instead of writing `__tod_get_<x>` for things we did not inject a getter for. |
| `3408848546` | **F2** — `--tod-race-check=auto` now walks `linearizedBaseContracts` when enumerating candidate functions and state vars.  Injected TOD functions living on a parent interface (e.g. SolidiFi's `EIP20Interface`) are now in scope. |

## Methodology

- Benchmark: `Dataset/benchmark_tod/solidifi_tod_08/buggy_1..buggy_50`
  (SolidiFi TOD corpus, pragma-upgraded to `>=0.8.0`).
- Labels: `Dataset/benchmark_tod/solidifi_tod_active/labels.json`,
  where `tod_pairs[i]` enumerates the injected `(play,getReward)` or
  `(setReward,claimReward)` twins per numeric suffix.
- Runner: `Dataset/benchmark_tod/scripts/run_solidifi_race.py`.
  Phase 1 (discovery) invokes `esbmc contract.sol --contract <labeled>
  --tod-race-check=auto --dump-harness --bound --no-standard-checks`
  with a 25 s CPU ulimit.  Pair list is parsed from the
  `--tod-race-check: discovered N candidate pair(s)` line.
- Target contract per case is read from `labels.json` (authoritative),
  not guessed from the source — this fixes the buggy_18/19/20/50 edge
  case where the deployable contract name starts with an underscore
  or a lowercase letter and the earlier regex missed it.

## Headline numbers

| metric | previous (pre-fix) | this run |
|---|---:|---:|
| cases analysed | 54 (50 SolidiFi + 4 SmartBugs) | 50 SolidiFi |
| labelled pairs (SolidiFi only) | 881 (upgraded corpus had gaps) | **931** |
| auto-discovered pairs (SolidiFi only) | 808 (leaf-targeted) / 949 (inj.site) | **1568** |
| **discovery recall** | **60.4 %** | **100.0 %** |
| cases at 100 % recall | 33/50 | 50/50 |
| target-resolution errors | 4 | 0 |

The 27-label gap on the pre-fix `buggy_18` report disappears: the
leaf-contract target (`_Yesbuzz`) inherits from `ERC20Interface`,
where all 27 TODn functions were injected; walking
`linearizedBaseContracts` now catches them in one pass.

## Extras interpretation

`discovered - labels = 637` extras across the batch.  They split into
three buckets:

- **Classic ERC20 allowance race** (`approve` vs `transferFrom`):
  every token-style contract exposes this real TOD shape, so it
  appears in discovery even when SolidiFi did not "inject" it.
  Counts as a true positive on contract semantics, not a false
  positive of the auto mode.
- **Cross-suffix pair interactions** (`TOD14 vs TOD20` etc.):
  SolidiFi's suffixes are disjoint by construction, but the auto-mode
  read/write analysis sees overlap via the shared ERC20 `balances` /
  `allowed` maps when both functions call `.transfer` through the
  host contract.  These are coarse TOD shapes too — whether they
  count as real races depends on whether the shared map really is
  order-sensitive.  Precision audit is out of scope for this run.
- **Cross-function ambient races** (e.g. `freezeAccount vs transfer`):
  Likely real TODs in the host contract source that the paper's
  ground-truth labels don't include because SolidiFi only scored the
  injected twin.

## Verification phase (not run)

Full verification of 1568 pairs × ~10 s/pair = ~4 h of wall clock on
this WSL2 host; deferred per `feedback_no_interrupt_sleep.md`.  The
runner supports `--verify` to execute it when the user resumes.

## Files

- `summary.json` — per-case + aggregate stats
- `buggy_N.discover.log` — raw esbmc stdout/stderr for that case
- `../../scripts/run_solidifi_race.py` — the driver

## Known remaining issues (out of scope for this pass)

1. Verification-phase OOM on contracts with 20+ pairs (e.g. buggy_1:
   HotDollarsToken) under the 4 GB ulimit + CVC5 on inherited state.
   Mitigation on next pass: tod-jobs=2 + unwind=1 + tighter per-pair
   timeout.
2. `buggy_N` target inference regex pre-fix missed lowercase/underscore
   starters; fixed by reading from `labels.json`.  The AST-based
   contract detection in ESBMC proper is unaffected.
3. CSTK_CLT / COW / Mooniswap-class contracts still hit solver OOM on
   main-net bytecode sizes; this is a frontend×backend scale issue,
   not a harness-emit bug.
