# ESBMC `--tod-balance-check` vs SolidiFI injected-TOD benchmark

Benchmark source: `Dataset/benchmark_tod/solidifi_tod_08/buggy_{1..50}`
(0.8-upgraded SolidiFI injected-TOD contracts; ground-truth pairs from
`solidifi_tod_active/labels.json`).

SolidiFI injects ether-flow balance races (SWC-114 style: `.transfer`
redirect via `winner_TODn = msg.sender` or `reward_TODn = msg.value`),
so `--tod-balance-check` is the matching ESBMC check.

Per-contract strategy: pick the FIRST ground-truth pair from
`labels.json` (specific pair, not auto) to control OOM.  Hardened
wrapper (`timeout 300 + ulimit -v 4GB + ulimit -t 270`).  Sequential
outer loop.  Full ESBMC command in each `cmd` file under per-case
results dir.

## Results summary

Total cases: 49 (buggy_1..50, one index missing from upstream).

| verdict | count | meaning |
|---|---:|---|
| TOD_FOUND          | **0**  | `__tod_balance_check` assertion fired |
| CLEAN              | 10 | verification succeeded on the labelled pair |
| FAILED_OTHER       |  5 | non-TOD assertion fired (overflow / require / etc.) |
| PARSE_ERROR        | 14 | emitted harness rejected by solc (often HARNESS_ORDER_BUG) |
| CONVERSION_ERROR   |  7 | ESBMC converter trips on frontend-level type error |
| CRASH              |  6 | solver OOM or ESBMC abort |
| HARNESS_EMIT_BUG   |  3 | harness emitter missing `memory` keyword on array param |
| UNKNOWN            |  4 | verdict not matched by parser |

## Interpretation

**0 TOD found** is the headline.  Two contributing factors:

1. **ESBMC-side pipeline issues affect 34/49 cases**.  HARNESS_ORDER_BUG
   (inherited contracts reordered incorrectly), CONVERSION_ERROR
   (address-vs-contract type ambiguity), CRASH (CVC5 OOM), and
   HARNESS_EMIT_BUG (`address[] p` missing `memory`) together account
   for the bulk of the non-verdicts.  These surface as soon as the TOD
   harness tries to wrap a derived SolidiFI contract, not after any
   meaningful verification work.

2. **Of the 15 cases that DID reach a real verdict, 10 verified CLEAN**
   on the first labelled pair.  Two hypotheses worth checking before
   concluding ESBMC misses balance-TOD entirely:
   - a. The SolidiFI injection pattern B (setReward/claimReward) writes
     `reward_TODn = msg.value` between transfers.  ESBMC's TOD
     harness currently uses the fresh-IS model for param instances
     (per the bound-drive decision earlier in this session that drive
     is suppressed when TOD is active for c1/c2 sync), so both c1 and
     c2 start with `claimed_TODn == false` and `reward_TODn == 0`.
     The race requires a specific initial `reward_TODn` value to
     differentiate the two orderings — not reachable from fresh IS.
   - b. The harness `require(address(c1) != address(c2))` isolates
     c1/c2 storage, but does NOT pin their pre-race state to the
     same value.  Under this harness shape a balance-TOD pair is
     fundamentally equivalent to "each c runs independently" —
     which is what `CLEAN` reports.  Balance-TOD needs a shared
     state model (one storage, two orderings).

Both point at the same underlying limitation: the current race/balance
TOD harness model does not match TransRacer's semantics of "two
orderings from the SAME pre-race state".  Fixing this needs harness
redesign (snapshot/restore a single instance's state and run both
orderings on it), not just a bound-mode drive.

## Per-case table

See `solidifi_balance_summary.json` for the machine-readable verdicts
(case, pair, exit_code, verdict, reason).
