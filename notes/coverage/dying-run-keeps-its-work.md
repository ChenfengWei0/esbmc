# A dying path-coverage run used to keep nothing. Step 1: the payload.

`scope-and-resources.md` §2 measured the loss and named the change sites. This
is the record of what was actually built, in the order the loss requires. One
section per step; each step is its own commit with its own must-flip pair.

**Bookkeeping note on step 1's commit boundary.** The code below landed in
`d75df81ba3`, whose message is about `forge_roundtrip.py`. Two of us share this
worktree; the other agent ran `git add <one script>` followed by a bare
`git commit`, and `git commit` writes the WHOLE INDEX, so this work went in
under their message. History is deliberately NOT rewritten for it — the tree is
local and un-pushed, and a wrong message on one commit is a bookkeeping problem
while a rewrite under a shared worktree is a real one. This file is the honest
record instead. Both of us now use `git commit -F <msg> -- <paths>`, which
commits only the named paths.

---

## Step 1 — persist the counterexample payload when the claim is decided

### The measurement this exists for

The aqua whole-contract run at `--memlimit 8g` died **51.5 % through the solve**
having **DECIDED 938 claims and REFUTED 5** of that contract's 15 complete paths
— and produced no artefact at all. All five are in the 20 g run's F set, so all
five were genuine: `rawBalances:path:2`, `rawBalances:path:7`,
`safeBalances:path:2`, `safeBalances:path:14`, `ship:path:1756`.

The loss is structural, not bad luck:

| fact | site |
|---|---|
| `cov-report.json` is written exactly once | `bmc.cpp:1976`, gated `:1336` |
| from `report_coverage`, which sits AFTER the job loop | `bmc.cpp:3661-3670` |
| inside `run_thread`'s try | `bmc.cpp:2405` |
| the only verification-phase catch | `bmc.cpp:2559-2563` |

So a `bad_alloc` in any job unwinds **past** the report into the catch. A caught
OOM costs the entire report, not part of it.

### Why the payload had to come first

A mid-solve persistence mechanism already existed — the covered-set writer at
`bmc.cpp:3552-3553`, firing per witnessed claim — and it persisted **only stable
path ids**. Enabling it before persisting the payload would have converted a
witness lost to an OOM into a permanently payload-less `F`: the next round sees
the path via `path_witnessed_earlier`, does **not** re-instrument it, and reports
it under `ce_extraction.payload_absent_reason` forever. The round that could
still have produced the inputs is the round that skips the path. That is a
regression dressed as a fix, and it is the whole reason the ordering is fixed.

### What was built

**1. The covered set carries the payload** (`write_path_covered_set_atomic`).
On-disk `version` 2 → 3, and a version ≤ 2 file is now **refused on load**, so an
old payload-free file is rejected rather than silently read as "these paths have
no inputs". The fingerprint check stays first, so
`solidity_path_cov_covered_set_fail_closed`'s message is unchanged.
`bmc.cpp:1793`'s `payload_absent_reason` becomes a lookup: when the covered set
carries the payload it is emitted, labelled with its provenance (it was harvested
under a *different* run's bound and slicing configuration, and a consumer that
read it as this run's output would attribute this run's `bound` block to it).

**2. A CE journal, `cov-ce-journal.json`, with no opt-in flag.** The covered set
is only written under `--coverage-covered-set`, and `pathcov_collect.py` has
never passed it — the run that lost five witnesses was not passing one. The
journal is written whenever the run asked for the payload at all
(`--cov-report-json`), refreshed by an atomic `.tmp`+rename at the moment a path
is witnessed, and **never read back in**, so it cannot accumulate across runs or
change what a re-run does. `complete` is `false` on every incremental write and
`true` only on the one written beside the final report.

Both censuses are **read back out of the published file**, not taken from the
in-memory maps that produced them. A count of what the writer believes it wrote
would have printed correct-looking numbers throughout the period in which
nothing called `write_path_covered_set_atomic` at all.

### Testability is a shipped feature here

These paths only run on a run that does **not** reach a clean exit, and a
`test.desc` is one invocation with no environment of its own —
`testing_tool.py` additionally **strips** `--timeout` and `--memlimit`
(`UNSUPPORTED_OPTIONS`). There is no way to produce a dying run from a
regression except to ask the tool for one, so `--path-cov-fault-after N` is a
real option under DEBUG rather than a throwaway build. Its sibling
`--path-cov-fault-sigterm N` landed in the same commit and is **not** exercised
by step 1; it exists for step 2's signal arm.

### Must-flip pair

| direction | test | what it pins |
|---|---|---|
| fires | `solidity_path_cov_ce_journal_survives_death` | with `--cov-report-json` and `--path-cov-fault-after 1`: `CE journal cov-ce-journal.json updated after claim 1 of 4: 1 witnessed path(s) on disk, 1 with non-empty inputs (complete=false)`, and `Coverage report written to cov-report.json` never appears |
| stays dark | `solidity_path_cov_ce_journal_absent_without_report` | same contract, same four witnessed paths (`Path Status: F 4, I 0, U 0`), no `--cov-report-json`, and no journal line anywhere |

The pair establishes the right distinction: the journal follows the **request for
a payload**, not the presence of a witness. The claim index in the line
(`after claim 1 of 4`) is load-bearing — without it the message is compatible
with the old end-of-run write and the test would pass on a build that had
changed nothing about *when* the payload lands.

### Red before

Against the pre-change binary, snapshotted out of `build/` before the rebuild:

* the positive test fails with `ERROR: unrecognised option '--path-cov-fault-after'`;
* artefact level, same contract, same clean run with `--cov-report-json`: the old
  binary leaves `cov-report.json` and nothing else; the new one leaves
  `cov-ce-journal.json` as well, and `cov-report.json` is the same 14053 bytes —
  so the report a *surviving* run produces is untouched.

The journal's contents on that run are the deliverable, not a marker:
`inputs: a = 101`, `entry_storage: x = 0`, `final_state: x = 1`,
`path_id_stable: e3d346f76399dc6e`, `scoped_to_claim: true`.

### ctest

`solidity_path_cov` 78/78 (76 baseline + 2 new), `foundry_covgen` 41/41.
Re-verified against the current HEAD build after the commit boundary moved.

### Known cost, stated rather than discovered

Each publish serialises the whole witnessed set, i.e. quadratic in |F|. |F| is
single digits per unit on every contract measured so far, and the same shape was
already accepted for the covered-set writer. A contract with thousands of
witnessed paths would need an append-only journal instead.

---

## Step 2 — a dying run writes a report, explicitly marked PARTIAL

### THE NUMBER

Same command, same machine, same afternoon; only the binary differs.

```
esbmc aqua__Aqua.flat.sol.solast --sol aqua__Aqua.flat.sol \
  --solidity-path-coverage --contract Aqua --solidity-max-tx 1 \
  --cov-report-json --path-cov-max-goals 10000 --memlimit 8g
```

| | before | after |
|---|---|---|
| exit | **134** (SIGABRT, uncaught `std::bad_alloc`) | **2** |
| wall | 289 s | 330 s |
| claims decided | 938 | **921** |
| REFUTED (witnesses) | 5 | **5** |
| claims surviving into a report | **0** | **921** |
| witnesses surviving | **0** | **5, all with `inputs`** |
| `cov-report.json` | not written | **1 977 811 bytes, `partial: true`** |
| `cov-ce-journal.json` | did not exist | 12 745 bytes, 5 witnesses |

The five are the same five the 20 g run confirms genuine: `rawBalances:path:2`,
`rawBalances:path:7`, `safeBalances:path:2`, `safeBalances:path:14`,
`ship:path:1756`.

**921, not 938, and the difference is a cost this change introduces.** The
rescue reserves a 128 MiB cushion out of the 8 GiB budget, so the run reaches
17 fewer claims before dying. That is the honest trade: 17 claims of reach for
921 claims and 5 witnesses of retention.

### The three ways a run dies, and what each now produces

| death | mechanism | what survives |
|---|---|---|
| solver OOM / any throw out of the job loop | `try` around the loop, tail runs on the exception path, then `throw;` | full PARTIAL `cov-report.json` + journal |
| SIGALRM / SIGTERM / SIGINT | new path-coverage arm in the signal handler | signal-safe `[Coverage]` block on stdout + journal (no JSON — see below) |
| SIGKILL | — | journal only |

The signal arm cannot write JSON: it runs in a context where malloc, iostream
and the log mutex are all unsafe, and a handler that deadlocks on the allocator
turns "partial data" into "no data and a hang". It prints atomics through one
`write(2)` and says so in the text it prints. **That is exactly why the journal
had to land in step 1 before this arm existed.**

### The rescue itself ran out of memory, and that took a second attempt

The first build of this step DID reach the rescue on aqua — it printed
`Writing a PARTIAL report with the 938 of 1822 claim(s) decided so far`, got as
far as the `[Coverage]` block, and then threw a **second** `std::bad_alloc`
while building the JSON. `cov-report.json` was still not written and the process
still died with SIGABRT. Building a report for 2846 claims needs tens of
megabytes, and the process had just failed to get any.

Two fixes, and both were needed:

* a 128 MiB block reserved before the solve and released as the first act of the
  rescue, returning it to the allocator's free list where the report can reuse
  it without growing the data segment (which is what `--memlimit` caps:
  `RLIMIT_DATA`). Untouched, so it costs address space — the resource under
  pressure — and no RSS.
* the rescue got its own `try`/`catch`, so a failure inside it cannot replace
  the original reason with its own. Without it, the log would blame the report
  writer for a run that died in the solver.

### The U-reason split, and the first version of it was wrong

`not-solved-this-run` means the simplifier folded the claim to `true` at symex
time — a property **of the claim**, identical on every re-run, that no budget
changes. A claim the loop never got to is a property **of the run**, for which a
bigger budget is the fix. One cell, two facts, opposite next actions. So
`run-died-before-solving` is a sixth token.

**The first implementation over-attributed and had to be corrected before it
shipped.** Keying only on "the run died" swept *every* undecided claim into the
new bucket: aqua reported **1826** paths lost to the death when ~901 were, and
`not-solved-this-run` read **0** on a contract whose true figure is 1024. The
fix is `claims_in_solve_loop` — the exact set of claim comments that survived
simplification and reached the equation, recorded once before the first solve.
With it:

```
bounded-holds            916
named-obstacle             0
not-solved-this-run     1024      <- exactly the documented figure
run-died-before-solving  802
solver-unknown             0
unit-not-entered          99
                        ----
                        2841 = U,  + 5 F = 2846 = paths_total
```

`audit_entry_liveness` also had to stop aborting on a partial run: its premise
("a unit with instrumented claims should have been entered") holds only for a
run that reached the end of its loop, and it runs BEFORE the JSON is written —
so aborting there would have destroyed the partial report on its way out. It
now warns and names the reason. That is the third time this check has accused a
correct run of a defect it did not have.

### Must-flip pair (three tests, both directions)

| direction | test | pins |
|---|---|---|
| fires (OOM) | `solidity_path_cov_partial_report_on_oom` | `Report Completeness: PARTIAL … std::bad_alloc`; the whole `U Reasons:` line with `not-solved-this-run 0, run-died-before-solving N`; **and** `Coverage report written to cov-report.json` |
| fires (signal) | `solidity_path_cov_partial_report_on_signal` | signal-safe block, `Claims Decided : 1 of 4`, the LOWER-BOUND disclaimer verbatim, **and** that no `cov-report.json` was written |
| stays dark | `solidity_path_cov_report_complete_when_it_concludes` | `Report Completeness: COMPLETE` present, `PARTIAL` absent anywhere, `Path Status: F 4, I 0, U 0` so "not partial" cannot mean "did nothing" |

The completeness line is emitted **unconditionally**, in both directions. A
marker that appears only on failure is indistinguishable, to a consumer that has
not been taught about it, from a marker that was forgotten. Same reason
`report["partial"]` is written as `false` on a complete run, and is duplicated
under `summary` — several existing readers open `summary` and never look at the
top level.

### Consumers taught to tell them apart

* `notes/coverage/scripts/report_summary.py` prints
  `** PARTIAL REPORT -- NOT A MEASUREMENT **` before any number, and prints
  `UNSTATED` for a report with no `partial` field rather than assuming complete.
* `notes/branch_gate.py` counts `partial_reports` and `unstated_reports` in its
  own columns and appends `(partial)` to the verdict. This matters more than it
  looks: `reportPresent` in the collector's index used to be a sound proxy for
  "this run measured something", because a killed run wrote nothing. It is not
  any more.

### Four existing tests were updated, deliberately

`solidity_path_cov_infeasible`, `..._focus_function_same_enumeration`,
`..._residual_unit_call_obstacle` and `..._foundry_obstacle_not_emitted` pin the
`U Reasons:` line, which is published with every slot present including the
zeros. Adding a token appends a slot. The new zero is now pinned in each of
them, which is the point of printing the zeros.

`solidity_path_cov_ce_journal_survives_death` (step 1) asserted that no report
was written. Step 2 deliberately changes that, so the assertion was **replaced**
rather than deleted: it now pins `Report Completeness: PARTIAL … std::bad_alloc`,
the same fact in the vocabulary the tool now has. Deleting it would have left
the journal line provable by a run that finished normally.

### Red before

The pre-step-1 binary, snapshotted out of `build/`, on the identical aqua
command: exit 134, `terminate called after throwing an instance of
'std::bad_alloc'`, **no `cov-report.json`, no `cov-ce-journal.json`, nothing**.
`loss_census.py` on its log: 938 decided, 5 refuted, `cov-report.json written?
NO`, `ALL 938 decided claim(s) were discarded, 5 of them witnesses`.

### ctest

`solidity_path_cov` 81/81 (76 baseline + 2 from step 1 + 3 from step 2),
`foundry_covgen` 41/41.

---

## Step 2b — a per-claim solver budget, because everything above is mitigation

Steps 1 and 2 make the loss cheaper. They do not make it smaller, and the
distinction matters: raising `--memlimit`, lengthening the outer timeout and
keeping partial results all route **around** a query that does not finish.

**The case, measured on `St1inch.setFeeReceiver`** (from
`notes/coverage/pathcov/st1inch_St1inch/work/St1inch__setFeeReceiver/run.log`
via `claim_cost_profile.py`):

```
instrumented 275 path(s) across 39 unit(s)
VCCs                10 generated, 10 remaining
claims started      1   PASSED 0   FAILED 0
goto construction   13.4 s, symex 0.1 s  -> 13.5 s accounted
killed at 180 s
```

~166 s of a 180 s run inside the **first** solver query, which never returned;
the other nine claims were never asked. A few hundred MB in use against 40 GB
free. **Not a memory problem and not a path-count problem** — and no amount of
memory or wall clock fixes a query that does not terminate.

Path coverage decides one **independent** claim per job, so the right bound is
per claim, and there was none.

### `--path-cov-claim-timeout N` (seconds, default 120, 0 = unlimited)

Enforced by the **solver**, not by killing the process. Every backend already
receives `optionst` at construction, and under path coverage a fresh solver is
built per claim, so a per-check-sat limit *is* a per-claim limit.

| backend | enforcement | state |
|---|---|---|
| bitwuzla | `BITWUZLA_OPT_TIME_LIMIT_PER` (ms, per check) — set on the options object before `bitwuzla_new`, which is the only point options are consumed | **native** |
| cvc5 | option `tlimit-per` (ms, per check) | **native** |
| z3 | solver parameter `timeout` (ms, per check) | **native** |
| anything else (smtlib, boolector, mathsat, …) | none | **reported as `NOT ENFORCED: backend '<name>' has no per-query time limit`** |

The last row is the point of recording a *mechanism string* rather than a
boolean: a report carrying `claim_timeout_s: 120` for a run nothing bounded is
the exact shape of a guard that never fires while everything looks fine.

`cvc5`'s `tlimit-per` and not `tlimit`: the latter is cumulative over the
solver's lifetime, and under `--smt-during-symex` one solver serves every claim,
so a cumulative limit would abandon every claim *after* the budget instead of
each claim that exceeds it.

### How an abandoned claim is recognised

Every backend folds "unknown" into `P_ERROR` — `smt_convt::resultt` has no
`P_UNKNOWN` to fold into instead — so the result alone cannot tell an abandoned
query from a genuine solver failure. The wall clock separates them:

* SAT or UNSAT → **keep the verdict**, however late it arrived. The solver
  answered, and an answer is not something to discard over a stopwatch.
* a non-answer that took at least the budget (with 100 ms slack for clock
  granularity) → abandoned.

### `claim-budget-exceeded`, and why it is not one of the five existing tokens

| token | means |
|---|---|
| `solver-unknown` | the solver **answered** "I do not know" — it looked and gave up. Information. |
| `bounded-holds` | it answered "no witness at this exploration". |
| `not-solved-this-run` | never asked; the simplifier folded the claim away. |
| `run-died-before-solving` | the run died before reaching the claim. |
| **`claim-budget-exceeded`** | **we asked, the solver was still working, and we stopped it.** Nothing at all is known, and uniquely among the five the fix is a bigger budget. |

It gets its own verdict letter `'B'` in `claim_outcome` rather than a shade of
`'U'`, which forces an explicit `case` in `path_u_reason_token` — whose `default`
arm returns `""` so the caller hard-fails on an unrecognised verdict.

`summary.bound.claim_timeout_s` and `.claim_timeout_enforcement` are published
because **a capped run's U counts are not comparable with an uncapped run's**:
some of its U's mean "we stopped asking", and a reader diffing two reports
without those fields would read that as "no witness exists".

### Proof of concept — the one run, and what it does NOT show

Exactly the command in the brief, `St1inch --focus-function setFeeReceiver`,
`--memlimit 8g`. Artefacts kept under `notes/coverage/partial-report-poc/`.

| | budget OFF (`--path-cov-claim-timeout 0`) | 10 s | 120 s (shipped default) |
|---|---|---|---|
| outcome | **killed by the outer timeout at 300 s** | exit 0 in 103 s | exit 0 in 996 s |
| `cov-report.json` | **not written** | written | written |
| completeness | — | `COMPLETE` | `COMPLETE` |
| `claim-budget-exceeded` | — | 5 | 5 |
| **F (witnesses gained)** | — | **0** | **0** |

**STATED PLAINLY: on this unit the budget bought a report, not a witness.** All
five of `setFeeReceiver`'s claims are too hard for the solver at 120 s, so the
run that used to be killed with nothing now terminates and says precisely which
five claims it could not afford — `claim-budget-exceeded 5`, and every other
U-reason bucket zero. That is the difference between a lost run and a measured
one, and it is *not* the same as recovering the other claims. The brief's
criterion "the other nine claims are decided and present in the report" is
**not** met here: this unit has 5 claims, not 10, and all 5 exceed the budget.

The "one claim abandoned, the rest decided" shape is demonstrated instead by the
regression fixture below, where it is reproducible in about a second rather than
resting on one 16-minute run.

`bound.claim_timeout_enforcement` names
`bitwuzla: native BITWUZLA_OPT_TIME_LIMIT_PER` in all three runs.

### Must-flip pair — the workload varies, not the flag

| direction | test | budget | result |
|---|---|---|---|
| fires | `solidity_path_cov_claim_budget_abandons_and_continues` | `1` | 10 paths across 2 units; the 256-bit multiplication obligation is abandoned and **the other nine are decided and witnessed** — `10 of 10 instrumented path claim(s) reached the solver`, `Path Coverage: 90%`, `Path Status: F 9, I 0, U 1`, and `claim-budget-exceeded 1` as the only non-zero U-reason |
| stays dark | `solidity_path_cov_claim_budget_not_hit_when_cheap` | `1` | four cheap paths, `claim-budget-exceeded 0`, `F 4, I 0, U 0` |

This fixture is the real proof of the property the PoC could not show: one claim
costs its own budget and **nothing else**. `10 of 10 … reached the solver` says
every claim was asked, `Path Coverage: 90%` says nine were answered, and
`Report Completeness: COMPLETE` says the loop ran to its end rather than
stopping at the expensive claim. Counts that could drift with solver
nondeterminism (how many of the seven `hard` paths are slow on a given machine)
are matched as `[1-9][0-9]*`; the exact numbers pinned are the ones that are
structural.

Both run at the **same** `--path-cov-claim-timeout 1`, so the pair establishes
that the token tracks what the query cost rather than whether the flag was
passed — the property that would silently break if the "did it exceed?" test
keyed on the flag instead of the elapsed time.

`Report Completeness: COMPLETE` is the load-bearing assertion in the positive
direction: the marker is only COMPLETE when the per-claim job loop ran to its
end, so a build that abandoned the query by dying, or by stopping the loop,
cannot produce it. That is a stronger statement than counting surviving claims,
because it is a fact about the loop rather than about how many claims happened
to be cheap.

### Preserved artefacts

`notes/coverage/partial-report-poc/` — every number quoted above is re-derivable
from these without re-running anything:

```
aqua_8g_before/base.log                 loss_census.py -> 938 decided, 5 refuted,
                                        "cov-report.json written? NO"
aqua_8g_after/{cov-report.json,         report_summary.py -> PARTIAL, 921 of 1822
  cov-ce-journal.json,new.log}          decided, F 5, "F claims carrying inputs 5/5"
st1inch_budget_off/before.log           killed at 300 s, no report
st1inch_budget_10s/…                    complete report, claim-budget-exceeded 5
st1inch_budget_120s_default/…           complete report at the shipped default
```

### ctest after this step

`solidity_path_cov` **85/85**, `foundry_covgen` **41/41** (126/126 together).

Five tests needed their `U Reasons:` line extended by one slot, for the third
time in this piece of work: the breakdown is published with EVERY token present
including the zeros, so adding a token is a visible, intended change to that
line. It was checked the right way round rather than assumed — the full output
of `solidity_path_cov_focus_function_same_enumeration` shows
`instrumented 8 complete path(s) across 2 unit(s)`, the `2.67x` distribution,
`3 of 8 … reached the solver`, `Complete Paths : 8`, `Reached : 3` and
`Path Status: F 3, I 0, U 5` all **unchanged and matching**, with the seventh
slot as the sole mismatch. Enumeration did not move.
