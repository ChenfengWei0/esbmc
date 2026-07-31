# Two open dimensions of the invocation matrix, closed by measurement

Round 1 (`option-matrix-round1.md`) settled the transaction dimension and left
two things open. This closes both.

* **Scope** — is `--focus-function` worth what it costs? The configuration that
  removes its limit (drop the focus, one whole-contract run) had been tried
  ONCE, at `--memlimit 8g`, and died. 8 g was never chosen; it was copied from
  the branch-coverage collector. So "is whole-contract viable?" had never been
  asked.
* **Resources** — 27 runs killed at a 180 s outer timeout and 2 dead of solver
  OOM produced nothing at all. What exactly is lost, and is any of it
  recoverable?

Everything below was produced by ONE binary, snapshotted before the runs
(`build/src/esbmc/esbmc` copied to a scratch path) so that a concurrent rebuild
by another agent could not silently change the tool mid-experiment.

---

# DIMENSION 1 — SCOPE

## 1.1 The headline: whole-contract is viable, and 8 g was the whole problem

Same command, same input, same binary; only `--memlimit` differs.

| `--memlimit` | wall | peak RSS | exit | report | outcome |
|---|---|---|---|---|---|
| `8g` (the original attempt) | 312 s | — | -6 (SIGABRT) | **no** | `ERROR: Out of memory` → `std::bad_alloc` → `SIGABRT` |
| **`20g`** | **777.8 s** | **15.86 GiB** | 1 | **yes** | clean completion |

Units are stated once and not mixed: `--memlimit Ng` sets `RLIMIT_DATA` to
N x 2^30 bytes (`esbmc_parseoptions.cpp:691-708`, `read_mem_spec` `:331-364`),
and `/usr/bin/time -v` reports `Maximum resident set size` in KiB. The raw value
is 16 634 748 KiB.

Command (both):

```
esbmc aqua__Aqua.flat.sol.solast --sol aqua__Aqua.flat.sol \
  --solidity-path-coverage --contract Aqua --solidity-max-tx 1 \
  --cov-report-json --path-cov-max-goals 10000 --memlimit <N>
```

Peak RSS is **1.98x the 8 GiB cap**, so the first attempt could not have
finished at any point — it was not close. The run needs ~15.9 GiB and was given
8.

(RSS and `RLIMIT_DATA` are not the same quantity — the limit caps the data
segment, not the resident set — so 1.98x is an indication of the margin, not an
identity. The decisive fact is the pair of outcomes in the table, not the ratio.)

**Exit 1 is the SUCCESS signal here** and is not compared against anything: under
coverage a refuted claim is a witnessed path, so `VERIFICATION FAILED` is what a
productive run prints. Round 1 recorded that exit codes are not comparable
across strategies; every verdict below is read off `cov-report.json`, never off
an exit code.

Only one run was ever needed at 20 g, so the smallest limit that completes was
not bisected. What is established is the bracket: **> 8 GiB, and ≤ 20 GiB with
4.14 GiB of headroom at the peak.** A collector should ask for ~20 g, not 8.

## 1.2 The decision question: does whole-contract reach paths focus cannot?

The invocation contract's §2 says it should — dropping `--focus-function` is the
only configuration in which cross-function state can be built inside one
transaction, because the dispatch guards are independent.

**Measured answer: NO. Not one path.**

`fset_cmp.py` compares the SETS (unit id, `enc(pi)`), not the counts. Its premise
— that `enc` denotes the same path on both sides — is checked, not assumed:
`paths_total` is 2846 in every report.

```
whole                    |F| = 15
per-method + ship        |F| = 15
both                         = 15
ONLY whole                   = 0
ONLY per-method + ship       = 0
```

| unit | both | only whole | only per-method |
|---|---|---|---|
| Aqua.dock | 2 | 0 | 0 |
| Aqua.pull | 5 | 0 | 0 |
| Aqua.push | 2 | 0 | 0 |
| Aqua.rawBalances | 2 | 0 | 0 |
| Aqua.safeBalances | 2 | 0 | 0 |
| Aqua.ship | 2 | 0 | 0 |

### The confounder that had to be removed first, and it nearly produced the opposite answer

Run against the per-method collection **as it stands on disk**, the comparison
says `whole 15, per-method 13, ONLY whole 2` — both extras in `Aqua.ship`. Read
naively that is "whole-contract reaches two paths focus cannot", which is exactly
the conclusion §2 predicts and exactly the conclusion a count comparison would
have licensed.

It is false. The per-method collection has **no ship report at all**: its ship
run was killed at the 180 s outer timeout (`index.json`, `killedByOuterTimeout:
true`, `reportPresent: false`) — cause 1 of `gate-first-attribution.md`. The
difference was a *collection budget artifact*, not a reach difference.

Re-running the same focused unit with a real budget settles it:

```
esbmc ... --contract Aqua --focus-function ship --solidity-max-tx 1 \
      --cov-report-json --path-cov-max-goals 10000 --memlimit 20g
```

→ 292.4 s wall, peak RSS 7.87 GiB, report written, **F = 2**, and they are the
same two `enc` values whole-contract found (`ship:path:2`, `ship:path:1756`).
With that report in the union the difference is zero in both directions.

⇒ **The two configurations agree exactly, and the apparent advantage of whole
was one under-budgeted run.** A conclusion drawn from the counts alone would
have been wrong in the direction the theory predicted, which is the worst
direction to be wrong in.

### The agreement is much tighter than the F sets

Every per-claim verdict matches, not just the refuted ones. `bounded-holds` per
unit, summed across the per-method runs:

```
dock 61 + pull 12 + push 17 + rawBalances 1 + safeBalances 9 + ship 1707 = 1807
```

and the whole-contract run reports `bounded-holds 1807`. `not-solved-this-run`
is **1024 in every single report**, whole and focused alike. The claim census
closes exactly:

```
2846 instrumented path claims
- 1024 never reached the solver (simplified away at symex time)
= 1822 solved   ->  15 F + 1807 bounded-holds        (whole)
```

So the two configurations do not merely reach the same paths; they hand the
solver the same claims and get the same answers. On this contract the scope
dimension changes **nothing about the deliverable**.

## 1.3 What it costs

| | per-method (7 runs + 1 killed, re-run) | whole-contract |
|---|---|---|
| runs | 8 (2 library skips, 6 real) | 1 |
| total wall | **325.7 s** | **777.8 s** |
| peak RSS of any one run | **7.87 GiB** (ship) | **15.86 GiB** |
| claims solved | 1709 (ship) + 113 (others) = 1822 | 1822 |
| solver time | 230.2 s (ship), the other five 32.1 s of WALL in total | **598.6 s** |
| F | 15 | 15 |
| parallelisable | yes, runs are independent | no |

Per-method wall times from `index.json` (8 g, 180 s outer): `BalanceLib.load`
1.24, `BalanceLib.store` 1.18, `rawBalances` 2.31, `safeBalances` 2.69, `dock`
11.71, `pull` 10.86, `push` 3.33 — 32.1 s in total — plus ship at 292.35 s
measured here at 20 g.

Two caveats on that table, so neither number is read as more than it is:

* **Only ship's peak RSS was measured.** The other five ran at `--memlimit 8g`
  and completed, so their peak data segment was under 8 GiB; their actual RSS was
  not sampled. Ship dominates the per-method side either way (2733 of the 2846
  paths are its).
* **`claims solved` is reconstructed per unit**, as that unit's
  `F + bounded-holds`: dock 2+61, pull 5+12, push 2+17, rawBalances 2+1,
  safeBalances 2+9 = 113, and ship 2+1707 = 1709. The two sides sum to the same
  1822, which is the point of the row.

**Whole-contract is 2.4x slower and needs 2x the peak memory for an identical
result.** The reason is visible in the run's own numbers: the equation carries
every unit live, so the same claims are more expensive.

| | VCCs generated | assignments after slicing | slicing exemptions | s/solved claim |
|---|---|---|---|---|
| whole | 6938 | 3744 | 183 symbols | 0.329 |
| focus ship | 6825 | 2572 | 177 symbols | 0.135 |

## 1.4 The solver

Both runs auto-selected CVC5, with the warning printed verbatim at log line 2:

> `Solidity: detected >=3-level nested-mapping shape (or a >=2-level
> struct-valued mapping); auto-selecting 'cvc5' (Bitwuzla aborts on the
> CONST_ARRAY-initialised infinite mapping array — "Equality over constant
> arrays not fully supported" under assertion BMC, k-induction non-convergence
> under coverage). Override with --bitwuzla / --z3 / --boolector.`

**`--z3` was NOT tried, deliberately.** The fallback experiment was contingent on
CVC5 being the thing that ran out of memory; at 20 g CVC5 does not run out of
memory, so the contingency never fired and a `--z3` run would answer a question
nobody now has. Recording the reason rather than the absence: the auto-selection
is a soundness statement about this contract's shape, and overriding it to dodge
a memory problem that turned out not to exist would have traded a correct
backend for nothing.

For the record, the OOM message's own advice ("Retry with a different backend")
and the once-per-process CVC5 fallback at `esbmc_parseoptions.cpp:3172-3199` are
both inert here: the fallback only fires when the backend was auto-selected AND
is not already CVC5, which on this contract it always is.

## 1.5 Verdict on dimension 1

* Whole-contract **is viable** — 778 s, 15.86 GiB peak, clean report. The 8 g
  death was a badly chosen limit, not a property of the configuration.
* It reaches **nothing** per-method focus cannot, on this contract: F sets
  identical, `bounded-holds` identical, `not-solved-this-run` identical.
* It costs **2.4x wall and 2x peak memory**, and unlike per-method it cannot be
  parallelised or resumed.

⇒ **Keep `--focus-function` as the primary configuration.** Its cost is that
every run re-instruments all 2846 paths and re-runs symex; its benefit is that
each run is small enough to finish, which — see dimension 2 — is the only
property that produces a measurement at all.

**ONE CONTRACT, AND ITS NAME IS PART OF THE CLAIM.** Aqua's units are guarded by
`msg.sender`, `msg.value` and their own arguments, not by state another public
function must establish first. The units where dropping the focus COULD matter
are the ones with a large `bounded-holds` count that a *sibling* function writes
— `FarmingPool.deposit` (7/147) and `FarmingPool.withdraw` (7/96) are the
obvious candidates, and `withdraw` is guarded by exactly the state `deposit`
writes. A null result on Aqua does not transfer to them. What DOES transfer is
the method: run whole at ~20 g, compare F SETS with `fset_cmp.py`, and make sure
every per-method run in the union actually produced a report before believing
any difference.

---

# DIMENSION 2 — RESOURCES

## 2.1 Where `cov-report.json` is written, and what must be true

| fact | site |
|---|---|
| the write itself | `bmc.cpp:1976-1978` — `std::ofstream out("cov-report.json"); out << report.dump(2) << std::endl;` |
| gated on | `bmc.cpp:1336` — `if (options.get_bool_option("cov-report-json"))` |
| inside | free function `report_coverage`, `bmc.cpp:768-2002` |
| filename | hardcoded, relative to CWD. Two runs in one directory overwrite each other. |

`report_coverage` has exactly three call sites, and all three are end points:

* `bmc.cpp:2504-2510` — the `remaining_claims == 0` early return in `run_thread`;
* **`bmc.cpp:3661-3670`** — in `multi_property_check`, guarded by
  `bs && !fc && !is && !k-induction && !incremental-bmc`. This is the one a
  normal path-coverage run takes;
* `esbmc_parseoptions.cpp:2783, 2807, 2828, 2870, 2888, 2942` — the per-k-phase
  conclusions in `do_bmc_strategy`.

So the preconditions are: `--cov-report-json` given, and **control reaches the
end of `multi_property_check`**. Nothing else writes it.

## 2.2 The `bad_alloc` claim: VERIFIED, and the report is lost even when it is caught

`catch (std::bad_alloc &)` — 6 occurrences in the whole tree, no other spelling
(`catch(std::bad_alloc` and `catch (const std::bad_alloc` are both 0):

| site | enclosing function | phase |
|---|---|---|
| `esbmc_parseoptions.cpp:3297` | `get_goto_program()` | GOTO construction |
| `esbmc_parseoptions.cpp:3376` | `create_goto_program()` | GOTO construction |
| `esbmc_parseoptions.cpp:3547` | `parse_goto_program()` | GOTO construction |
| `esbmc_parseoptions.cpp:4330` | `process_goto_program()` | GOTO construction |
| `esbmc_parseoptions.cpp:4544` | `preprocessing()` | `--preprocess` only |
| **`bmc.cpp:2559-2563`** | **`bmct::run_thread()`** (try opens at `bmc.cpp:2405`) | **verification — the ONLY one** |

*Correction to `path-coverage-invocation-contract.md` §10.5*, which lists four
GOTO-construction catches: there are five. The fifth, `preprocessing()` at
`:4544`, is only reachable under `--preprocess` (`doit()`, `:920-924`), so it is
not on the verification path and the section's conclusion is unaffected.

**No top-level handler**, confirmed by reading both files whole: `main.cpp` is 12
lines with no `try`; `parseoptions_baset::main` (`util/parseoptions.cpp:58-72`)
is `install_signal_catcher(); return doit();` with no `try`. Neither file
contains any `catch`.

### Is the report still lost when the exception IS caught? YES.

The chain, all in `bmc.cpp`:

```
run_thread()                       try opens          :2405
  └─ multi_property_check(...)     CALLED INSIDE IT   :2541-2542
       └─ std::for_each(jobs, job_function)           :3655   ← OOM thrown here
          report_simple_summary(summary)              :3658   ← skipped
          report_coverage(...)                        :3661-3670  ← SKIPPED
     catch (std::bad_alloc &) { return P_ERROR; }     :2559-2563
```

`report_coverage` sits **after** the job loop and **inside** the same `try`, so a
`bad_alloc` in any job unwinds straight past it into the catch. The claim is
confirmed: **a caught OOM costs the entire report, not part of it.** Every claim
already decided has a `claim_outcome` entry (`bmc.cpp:2927-2934`) and a
`path_ce` payload (`bmc.cpp:3467-3468`), and all of it dies with the process.

Two aggravations found while verifying:

* The `P_ERROR` the catch returns produces `ERROR: SMT solver failed`
  (`bmc.cpp:2175-2177`) and then
  `WARNING: The solver could not decide this query; treating it as inconclusive
  and continuing.` (`esbmc_parseoptions.cpp:3201-3205`). **"Continuing" is not a
  promise the run survives** — the next `bad_alloc` thrown anywhere outside
  `run_thread`'s own try reaches `std::terminate`. The aqua 8 g log shows exactly
  that sequence, four lines, ending in `SIGABRT`.
* Under `--parallel-solving` the jobs run on `std::thread`s (`bmc.cpp:3641-3649`).
  A `bad_alloc` on a worker thread does not unwind into `run_thread`'s try at
  all; it calls `std::terminate` directly. Not our configuration today, and a
  trap if it ever becomes one.

## 2.3 The signal path: VERIFIED, path coverage emits nothing

| fact | site |
|---|---|
| the rescue | `emit_branch_coverage_on_timeout()`, `esbmc_parseoptions.cpp:130-180` |
| **the gate** | `esbmc_parseoptions.cpp:132-133` — `if (!goto_coveraget::branch_cov_active.load(...)) return;` |
| SIGALRM (`--timeout`) | `timeout_handler`, `:182-193`, installed at `:678-679`, `_exit(1)` |
| SIGTERM/SIGINT (external kill) | `term_handler`, `:207-219`, installed **unconditionally** at `:687-688`, `_exit(143/130)` |

`branch_cov_active` has exactly one runtime writer in the whole tree:

```
goto_coverage.cpp:2325   branch_cov_active.store(true, std::memory_order_relaxed);
```

inside `goto_coveraget::branch_coverage()` (`goto_coverage.cpp:2109-2329`). The
only other occurrence is the static init `{false}` at `goto_coverage.cpp:94` and
the read at `esbmc_parseoptions.cpp:132`. Checked explicitly against every other
coverage entry point — `assertion_coverage` (1921-1927),
`branch_function_coverage` (1945-2025), `k_path_coverage` (2374-2620),
**`solidity_path_coverage` (3001-7674)**, `condition_coverage` (7786-7916) —
**none of them writes it**, and `branch_coverage()` is called only from
`esbmc_parseoptions.cpp:3990` under `--branch-coverage{,-claims}`. The same
holds for the four numbers the handler would print: `total_branch_atomic`,
`covered_set_mode`, `live_reached`, `covered_run` are written only at
`goto_coverage.cpp:2320-2324` (inside `branch_coverage()`) and at
`bmc.cpp:1026/1029` and `bmc.cpp:3565/3574`, all four of the latter under
`if (is_branch_cov)`.

⇒ On a `--solidity-path-coverage` run `branch_cov_active` is **false for the
entire process**, the handler returns at its first line, and a run killed by
SIGALRM, SIGTERM or SIGINT emits **nothing at all**. Confirmed.

## 2.4 What it actually costs, on a real case

`loss_census.py` over the aqua whole-contract 8 g log (1.1 MB, read whole).

| | 8 g (died) | 20 g (finished) |
|---|---|---|
| `Solving claim` lines | 939 | 1822 |
| PASSED (claim held) | 933 | 1807 |
| **FAILED (REFUTED = witness in hand)** | **5** | **15** |
| claims decided | **938** | 1822 |
| `cov-report.json` written | **NO** | yes (log line 35688) |

The 8 g run died **51.5 % of the way through the solve** and threw away:

* **938 decided claims**, and
* **5 of the 15 counterexamples** — a third of the deliverable —
  `rawBalances:path:2`, `rawBalances:path:7`, `safeBalances:path:2`,
  `safeBalances:path:14`, `ship:path:1756`. All five are in the 20 g run's F set,
  so all five were genuine.

Its termination sequence, verbatim from lines 17130-17135:

```
ERROR: Out of memory
ERROR: SMT solver failed
WARNING: The solver could not decide this query; treating it as inconclusive and continuing. ...
terminate called after throwing an instance of 'std::bad_alloc'
  what():  std::bad_alloc
```

### A figure in the premise was wrong, and the reason is a trap worth naming

The 8 g run was on record as having *"5100+ claims solved including five
REFUTED"*. It solved **938**. The log contains two different `✓ PASSED` lines
that mean opposite things:

| | site | shape | meaning |
|---|---|---|---|
| **A** | `bmc.cpp:2888` | `✓ PASSED: '<claim> at <loc>'` — location INSIDE the quotes, line ends with `'` | solved, came back UNSAT. Decided work. |
| **B** | `symex_main.cpp:82-85` | `✓ PASSED: '<comment>' at <loc>` — location AFTER the quote, and it frequently renders EMPTY so the line ends in a bare `at` | `do_simplify` folded the claim to `true`; it never reached `assertion()`, was never solved, and lands in the report as `U` / `not_solved_this_run` (`bmc.cpp:1495-1498`) |

There are **5116 lines of shape B** (1364 distinct claims) against 933 shape-A
PASSED (plus 5 shape-A FAILED) = 938 decided. The run's own line
`Generated 6938 VCC(s), 1822 remaining after simplification` closes the
arithmetic exactly: 6938 − 1822 = 5116. Counting B as solved inflates "work
lost" by precisely the number of claims the run never did — and the tick is
green either way.

One residual to state rather than leave for a reader to trip over: 1364 distinct
shape-B claims but `not-solved-this-run: 1024` in the report. They are not
supposed to be equal. Shape B fires per symex *branch*, so a claim simplified
away on one branch and generated on another appears in both populations; the
1024 are the claims for which NO branch produced a VCC. The number the report
publishes is the one to quote.

`loss_census.py` reports A and B separately and refuses to merge them. Its own
first version matched **0** shape-B lines on a log containing 5116, because the
pattern required a non-empty location; the fix and the reason are in the source.

### The mechanism that WOULD have saved part of it already exists and was not enabled

`bmc.cpp:3552-3553`, inside the per-claim job, immediately after a claim is
witnessed:

```cpp
if (is_path_cov && !goto_coveraget::path_covered_outpath.empty())
  goto_coveraget::write_path_covered_set_atomic();
```

This fires **per witnessed claim, mid-solve**, and writes atomically
(`.tmp` + rename, `goto_coverage.cpp:173-185`). It is enabled by
`--coverage-covered-set <file>` → `esbmc_parseoptions.cpp:4163` →
`goto_coverage.cpp:3099`. `pathcov_collect.py` never passes it. Had it been
passed, the 8 g run would have left all five witnesses' identities on disk.

**But only their identities.** `write_path_covered_set_atomic`
(`goto_coverage.cpp:148-186`) writes a version, a fingerprint, and a list of
stable path ids — **no inputs, no env, no post-state**. Worse, the next round
would then see those paths via `path_witnessed_earlier` and *not re-instrument
them*, so their payload would be reported as
`ce_extraction.payload_absent_reason` (`bmc.cpp:1793-1801`) permanently.

⇒ Turning on the existing mechanism **without** also persisting the payload
converts a lost witness into a permanently payload-less `F`. That is a
regression dressed as a fix, and it is why the change-site list below pairs the
two.

## 2.5 Change-site list — what has to change for a PARTIAL report

Not implemented here. Each entry is the exact site and the exact obligation.

### A. Make a caught OOM keep the work (smallest change, biggest return)

| # | site | change |
|---|---|---|
| A1 | `bmc.cpp:3655` (sequential) and `:3641-3649` (parallel) | wrap the per-job call in its own `try`/`catch (std::bad_alloc &)` so ONE job's OOM does not unwind the loop. Record the claim as `solver-unknown` and continue; if memory is genuinely exhausted the next job rethrows and A2 catches it. |
| A2 | `bmc.cpp:3658-3670` | move `report_simple_summary` + `report_coverage` out of the straight-line tail into a scope that runs on the exception path too (RAII guard, or `try { for_each } catch(...) { report_coverage(...); throw; }`). This alone converts the aqua 8 g run from 0 claims reported to 938. |
| A3 | `bmc.cpp:1976` | when the report is written from an exception path, stamp the summary — e.g. `summary.partial = true`, `summary.partial_reason = "std::bad_alloc during job N of M"`, `summary.claims_attempted` / `claims_total`. **A partial report that does not say it is partial is worse than none**: 938/1822 claims reads as a complete run with a lot of `not-solved-this-run`. |
| A4 | `bmc.cpp:1495-1498` | the U-reason token for claims never reached because the run died must NOT be `not-solved-this-run` (which today means "simplified away"). Add a distinct token, e.g. `run-died-before-solving`, and add it to `goto_coverage.cpp:202-207` `path_u_reason_tokens()`. Without this the two causes are indistinguishable in the very report that is supposed to explain every U. |

### B. Make an external kill emit something for path coverage

| # | site | change |
|---|---|---|
| B1 | `esbmc_parseoptions.cpp:132-133` | the gate `if (!branch_cov_active) return;` is the whole bug. It needs a second, mode-correct arm rather than a widened condition — the branch-cov numerator/denominator are meaningless under path coverage. |
| B2 | `goto_coverage.cpp:3001-7674` (`solidity_path_coverage()`) | set a `path_cov_active` atomic plus `total_paths_atomic` at instrumentation time, mirroring `goto_coverage.cpp:2320-2325`. This is the missing writer: today path coverage sets **none** of the five signal-safe atomics. |
| B3 | `bmc.cpp:2918-2935` (the tri-state ledger) | bump signal-safe atomic counters `live_F` / `live_decided` alongside the `claim_outcome` write, under the existing mutex. The handler cannot walk `claim_outcome` (a `std::map`, possibly mid-mutation); it can only read atomics. |
| B4 | `esbmc_parseoptions.cpp:130-180` | add the path-coverage arm to `emit_branch_coverage_on_timeout` (and rename it — it is no longer branch-specific), printing `F <live_F>, decided <live_decided>, total <total_paths>` plus an explicit `(partial: run terminated before verification concluded)`. Must stay strictly async-signal-safe: atomic loads, stack buffer, one `write(2)`. |
| B5 | — | stdout is a **lower bound and carries no CE payload**. It cannot replace the report; it exists so a killed run is distinguishable from a run that reached nothing. Say so in the text it prints. |

### C. Make the witnesses themselves survive (the part that is actually the deliverable)

| # | site | change |
|---|---|---|
| C1 | `goto_coverage.cpp:148-186` (`write_path_covered_set_atomic`) | it persists only stable ids. Extend the on-disk record to carry each `F`'s `path_ce_t` (inputs / env / entry_storage / final_state), or add a sibling writer that does. **This is the blocking item**: without it, enabling the mechanism below makes the loss permanent instead of temporary (see 2.4). Bump `out["version"]` — it is 2 today — so an old file is rejected rather than silently read as payload-free. |
| C2 | `bmc.cpp:3552-3553` | the mid-solve incremental write already exists and already fires per witnessed claim. Once C1 lands, nothing here changes except that it now saves something worth saving. |
| C3 | `bmc.cpp:1793-1801` | `payload_absent_reason` currently says "its counterexample values are in the report of the round that witnessed it". After C1 that is a testable claim rather than a hope; if the payload was persisted, emit it instead of the excuse. |
| C4 | `notes/coverage/scripts/pathcov_collect.py:73-94` (`esbmc_cmd`) | pass `--coverage-covered-set <out_dir>/covered.json`. **Do not do this before C1.** |
| C5 | `notes/coverage/scripts/pathcov_collect.py:68` | `MEMLIMIT = "8g"` is the copied number that produced this whole question. It is a module constant with no CLI override; make it a flag and default it from the machine. |
| C6 | `notes/coverage/scripts/pathcov_collect.py:69` and `pathcov_all.sh:15` | the collector's own default is `DEFAULT_OUTER_TIMEOUT = 300`, but the sweep script defaults to `T=${1:-120}` and the aqua collection actually ran at 180 (`index.json`, `outerTimeoutSeconds: 180`). Aqua's ship needs 292 s at 20 g; st1inch's 22 of 22 runs all died at 180 s. Any budget below ~600 s is choosing to lose runs, and the two defaults must stop disagreeing. |

### D. Diagnostics that already exist and reach no reader

| # | site | change |
|---|---|---|
| D1 | `goto_coverage.h:451` `degraded_call_sites`, and the goal-cap truncation counter | publish both in `cov-report.json`'s `summary`. `gap_attribution.py` currently CANNOT distinguish "no unit calls this internal body" from "the call site was withdrawn by degradation or the depth bound" — they present identically, and today these exist only as a `log_warning`. |

## 2.6 Verdict on dimension 2

* A run that dies loses **everything**: the report is written once, at the end,
  inside the try that the exception unwinds (`bmc.cpp:3661-3670` vs `:2405`/`:2559`).
* An externally killed path-coverage run loses everything **and prints nothing**,
  because the only rescue is gated on an atomic no path-coverage code path ever
  writes (`esbmc_parseoptions.cpp:132` vs `goto_coverage.cpp:2325`).
* Quantified on the one case that can be checked against a successful re-run:
  **938 decided claims and 5 of 15 witnesses discarded**, at 51.5 % completion.
* It is recoverable. A2+A3 are a few lines and recover the whole 938. C1 is the
  one that recovers the witnesses, and it must land before C4.

---

## Reproduction

```
# dimension 1
notes/coverage/scripts/report_summary.py <cov-report.json> ...
notes/coverage/scripts/fset_cmp.py --a <whole>/cov-report.json <per-method>/*.json

# dimension 2
notes/coverage/scripts/loss_census.py <run.log> ...
notes/coverage/scripts/log_classify.py <run.log> --fold 5
```

Runs were serial from this side, each inside `setsid timeout` with an explicit
`--memlimit`, on a 43 GiB machine shared with another agent's `--memlimit 6g`
run; RSS was sampled from `/proc` throughout and the combined measured peak
stayed under 24 GiB against a MemAvailable that never fell below 22 GiB.

One process-hygiene note worth keeping: **`pgrep -x esbmc` cannot see these
runs.** The binary was snapshotted under a different name, and `comm` is
truncated to 15 characters, so the pre-run check prescribed for this machine
matches nothing while two multi-GB solvers are live. Check by cmdline
(`/proc/*/cmdline`), not by `comm`.
