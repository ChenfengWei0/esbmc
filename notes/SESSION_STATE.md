# Where the work stands (2026-07-30, evening)

Written to survive a context compaction. Read this, then
`notes/path-cov-assert-plan.md` and `notes/emitter-ce-value-loss-audit.md`.

## Landed today

| commit | what |
|---|---|
| `476fb89df7` | per-path DECISION SEQUENCES published — puts path coverage and branch coverage on one denominator |
| `1f890fb4dd` | external invocation scripts (`notes/coverage/scripts/pathcov_*`) + first three reproducers |
| `5ee20ea2e9` | **frontend fix**: a local declared with an initialiser inside an INHERITED function was zeroed |
| `4bd98cd328` | **collector fix**: the baseline collector silently rewrote its own scope when its source trees disappeared |
| `8fb9162cd1` | **gate fix**: a bar of 0 passed anything; stale `reports/` inflated the numerator; `N/A: 0 units` asserted a cause it never checked |
| `1a2eeea2de` | `t2_runnability.py` capped a unit at the SLICE REMAINDER and filed the artifact as a measured TIMEOUT |
| `3efdda1b18` | `reports/` reconciled with the journal; empty exclude list refused; `REFUSE` no longer the default verdict |
| `7c2440da67` | **the re-collected baseline** |

## The question that is still open

> "当前和 branch coverage 对齐或者更好了吗"

**Not answered yet, and no number from before today may be used.** Both sides
were collected with the buggy frontend, and the branch-coverage baseline is
additionally dated 2026-05-20 while the binary has taken two months of commits.

Sequence to answer it:

1. Re-baseline (branch coverage) — **DONE**, committed `7c2440da67`.
2. Re-collect the product side — `notes/coverage/scripts/pathcov_all.sh 180`.
   The pre-fix journals AND the pre-fix `reports/` trees are archived as
   `notes/coverage/pathcov/<bench>/{runs,index}.prefix-buggy-frontend.*` and
   `reports.prefix-buggy-frontend/`, so the collector starts from zero. 180s per
   run is twice the baseline's 90s outer budget per focused run, and that ratio
   is the justification — not a number picked to make something fit.
3. `python3 notes/branch_gate.py` for the gate table.

### The re-baseline result

Same inputs, same commands, today's binary against the dataset locked
2026-05-20:

| benchmark | denom | locked | re-collected |
|---|---|---|---|
| aqua_Aqua | 8 | 7 | 7 |
| cross_chain_swap_EscrowDst | 18 | 18 | 18 |
| cross_chain_swap_EscrowSrc | 16 | 16 | 16 |
| farming | 26 | 26 | 26 |
| limit_order_protocol | 3 | 3 | 3 |
| **st1inch_St1inch** | 86 | **83** | **72** |

`branchesTotal` unchanged everywhere (METHODOLOGY 8.2), checked rather than
asserted.

Five reproducing to the unit is what makes the sixth attributable: the
intervening two months of commits are inert for this measurement, and the only
benchmark that moved is the only one carrying the shape the inherited-local
fix addresses. Direction as expected — removing manufactured coverage lowers the
bar.

**A correction worth keeping:** an earlier reading of this same diff reported
"the baseline is dropping" from the per-function `rawReached` fields (8→2, 8→7,
9→8 on aqua). Those are single focused runs' raw branch-arm counts. The gate
uses `total.esbmcReached`, the union over all runs intersected with the
canonical decision lines, and that did not move at all. Report the field the
conclusion depends on, not the field that changed.

## THE MEASUREMENT CHANGED (read this before quoting any coverage number)

The gate in `branch_gate.py` measures which canonical decisions the VERIFIER's
exploration touched. That is a PROXY. The deliverable is a Foundry suite, and
the number that supports the claim is what THAT SUITE covers, measured by the
same tool the projects' own suites are measured with:
`notes/coverage/scripts/forge_roundtrip.py` (committed, self-contained -- it
needs no repository restoration, because the sources are in the flat and the
projects' own coverage is already in the locked JSON).

aqua_Aqua, 8 canonical decisions: **bar 7, native 6, OURS 2** -- while the proxy
said 4. Every reach number this project reported about itself before this is the
proxy kind.

Three defects found end to end, and what happened to each:

| defect | state | effect on the number |
|---|---|---|
| empty test body (names two witnessed paths, executes neither, PASSES because it does nothing) | FIXED in `collect()`, counted | files 6 -> 4, coverage unchanged -- proof they were worth nothing |
| a test the emitter ASSERTED exits normally that REVERTS | GUARDED at pipeline level (run every test, disable + count the red ones) | 1 red disabled, coverage unchanged |
| every recovered argument zero, and zeros alias to one mapping slot | de-aliased (distinct identities per parameter name) | **coverage UNCHANGED -- the aliasing hypothesis is REFUTED** |

**SECOND SAMPLE, and it narrows the table above.** `notes/emission-loss-four-samples.md`
(a THIRD sample has since landed there too — see the section at the end of this file):
farming is bar 26, native 26, OURS 10. Three of the aqua-derived claims do NOT
generalise -- on farming 170 of 174 defaulted arguments are UINT256 (not mapping
keys), there are ZERO empty-body refusals (aqua had 4), and the loss is spread
over four files with 14 RED tests as the dominant mechanism (aqua had 1). What
survives two samples is only the two-stage decomposition and its size: the
emitter loses about half of what the enumeration reaches (2 of 4; 10 of 18), and
the enumeration is itself below the bar (4 of 7; 18 of 26). Report the
mechanisms per benchmark, never merged.

The remaining gap on aqua is NOT an emitter defect: the uncovered branches sit
behind `require(balance.tokensCount == tokens.length)` on a mapping a fresh
deploy leaves empty, so they need state an EARLIER TRANSACTION establishes, and
everything runs at `--solidity-max-tx 1` from the post-constructor state with no
havoc. `forge_roundtrip.py --max-tx N` exists to test exactly that.

## SETTLED — the `dock` question below is ANSWERED, and by neither candidate

The section that follows is kept for its reasoning, but its two candidate causes
were BOTH WRONG. A counter added before any fix said so:

    0 dispatcher segment(s) acquired NO method
    2 reconstruction(s) had the coverage-claim FALLBACK blocked by a CONSTRUCTOR

There were no segments AT ALL (per-claim slicing removes the dispatcher's first
tx-guard — the very case the fallback exists to repair), and the fallback could
not run because `ctor_args` had pushed a constructor and the guard read
`calls.empty()`. TWO defects, and fixing the first alone changed nothing: the
fallback then ran and still emitted no call, because it derived the method from
the assert's SOURCE LOCATION and a complete-path claim has none. That second
defect was already fixed on the SEGMENT route and not here — and those two
routes are exactly the ones that cover for each other.

Both fixed (`2d35564b16`). `dock` emits 2 cases where it emitted 0. Whether
those cases COVER lines 2258/2260 is a separate measurement: both carry a
defaulted `ARRAY:ADDRESS` argument and both are revert-tolerant, so this is an
emission result, not yet a coverage one.

Three counters were kept rather than deleted with the fix
(`segments_without_method`, `fallback_rescued_ctor_only`,
`fallback_unsupported`) — a measurement removed once it has served one
investigation makes the next one start from a guess.

## THE OLD NEXT STEP (superseded — read the section above first)

`notes/coverage/scripts/emission_loss.py aqua_Aqua` names the whole
enumeration-to-emission loss as two lines, 2258 and 2260, and both are in
`dock` -- its loop header and the `require` inside it. `dock` is also one of the
two units whose emitted test had an EMPTY BODY, so its CALL was never
reconstructed; the arguments were never the question.

The distinction that matters and is not yet settled: a call whose ARGUMENT
cannot be rendered leaves a `// UNSUPPORTED: <C>.<m> has an argument type ESBMC
cannot yet render` comment in the emitted file. `dock` left NO comment at all,
which means the segment never reached that branch -- it was dropped earlier, in
`reconstruct()`. Those are different defects with different fixes.

To settle it:

1. Read `notes/coverage/forge_roundtrip/aqua_Aqua/_gen/Aqua__dock/run.log`
   (939 lines; it is the emission run's own output and may already name the
   reason).
2. If it does not, read `foundry_generator::reconstruct` in
   `src/goto-symex/foundry.cpp` and find where a segment is dropped without a
   comment -- the emitter audit calls this row 14, "segment with no method:
   nothing emitted, nothing counted", at roughly `foundry.cpp:2133`.
3. Whatever it is, it needs a COUNTER before it needs a fix, for the reason the
   last two emitter changes demonstrated: two hypotheses (argument aliasing, the
   transaction bound) were plausible, cheap to implement, and both refuted by
   measurement.

`dock(address app, bytes32 strategyHash, address[] calldata tokens)` takes a
dynamic array, and `_foundry_roundtrip/RESULTS.md`'s blocker matrix already
lists `ship`/`dock` as UNSUPPORTED for `bytes`/`address[]` -- so the dynamic
array is the likely cause, but "likely" is exactly what step 1 exists to
replace.

## The three audits, and what they changed

`notes/commensurability-audit.md`, `notes/interval-input-scope-and-plan.md` and
`notes/path-cov-assert-patch.md` were produced by three independent readers and
are the substance of this session. What they settled:

**The two sides are not commensurable in six ways, but we are not the ones
being flattered.** Two suspected flatterers were measured and are ZERO:
`notes/coverage/scripts/flatterers.py` finds no canonical decision owned by a
contract the baseline excluded (all six benchmarks), and every one of the 545
decision steps collected so far carries the flat itself as its `file`. More
decisively, `notes/coverage/scripts/setcmp.py` compares the two sides as SETS
rather than counts — which the gate structurally cannot do, since the numerator
is capped and the test is `ours >= bar` — and finds `only-product = 0` on every
file of every benchmark collected. Our reached set is a strict SUBSET of the
baseline's. The shortfall is real reach.

Four asymmetries remain and are now written into `branch_gate.py` rather than
left implicit: the solver budget (bar 60s inner / 90s outer per method, product
no inner timeout and 180s outer — favours us, and the bar is demonstrably cut
off mid-solve), the loop bound (bar k-induction unbounded, product forced
`--unwind 4` with `no-unwinding-assertions` unconditional — favours the bar),
the `require` lowering (guards one `not` apart, which the line-join makes
unobservable rather than absent), and the harness shape.

A false statement was removed from `branch_gate.py`'s own docstring: it claimed
degradation was "reported beside the gate". It is not — `degraded_call_sites`
only ever reached a `log_warning`.

**Interval inputs are sound where they are omitted and unsound where they are
vacuous.** An unbounded coordinate is universally quantified, so a certificate
over fewer coordinates is STRONGER, not wider — that direction is fine. But an
entry assumption that is semantically unsatisfiable makes every exit assertion
hold for want of an execution, and the four gates that exist are all SYNTACTIC
(`lo > hi`, duplicate name, punched empty, out of type). State variables are not
havoc'd, so `state.x in [0,0]` against a constructor that sets 7 is well-formed,
in-type, non-empty, and certifies vacuously with exit 0. There is no defence
today. Also worth a paper sentence: of 143 declared state variables across the
six inputs, only 24 are mutable, and three of the six are at 0%.

**Stage 3's premise is confirmed and its patch is written.** An exit read of
`member(sol:@_ESBMC_Object_<C>, field)` does observe the unit's writes, and the
object id is exactly `sol:@_ESBMC_Object_<C>#` — so the substring hazard is
closable by string equality. The patch also caught a defect in the plan's own
fixtures: the verdict-suppression regex it quoted does not exist in the tree,
and the weaker form would have let six refusal fixtures pass without refusing
anything.

## Subgoal status

1. **external invocation scripts** — done (`1f890fb4dd`).
2. **align with / beat branch coverage** — blocked on the two re-collections
   above. Known sub-blockers from the pre-fix run, all to be re-measured, none
   to be quoted: `ImmutablesLib` 0/8 on both Escrows, `FarmingPool` 4/12, and 15
   runs that produced no report.
3. **interval inputs** — not started.
4. **R0/R1/R2 assertions** — not started as code, but the design is no longer
   speculative. `notes/path-cov-assert-plan.md` carries the file-and-line plan,
   and its appendix records that the one load-bearing premise is **CONFIRMED**:
   an exit read of `member(sol:@_ESBMC_Object_<C>, field)` does observe the
   unit's writes. The frontend routes the write through a `this` POINTER, but
   symex dereferences before recording, `symex_assign_member` rewrites
   `a.c = e` into `a = with(a, c, e)`, and `slice.cpp:90` asserts every SSA lhs
   is a bare symbol — which `goto_coverage.cpp:2769-2782` already states, from
   measurement, is the object symbol.

   Six conditions came with it. The one that would otherwise be a silent hole:
   `resolve_coord` picks the contract object by SUBSTRING match on
   `scope_contract`, so `--contract Escrow` matches `_ESBMC_Object_EscrowSrc`
   and an empty `scope_contract` matches anything. Reading the wrong object
   makes `post == pre` hold vacuously with no error — on exactly the
   EscrowSrc/EscrowDst shape. The mode must match the unit's own contract
   exactly (`contract_of`, `goto_coverage.cpp:6601-6615`). Also:
   `coord_expressible` is the wrong gate for R1, since `==`/`!=` is expressible
   on the `bool` it refuses.

## Open, deliberately not guessed

- Why branch coverage reaches 38 edges at `--unwind 4` and 94 at `--unwind 8`
  on the real st1inch benchmark.
- Whether `cov_pilot_farming_FarmingPool` and `cov_pilot_aqua_Aqua_full` timed
  out before today's frontend fix. Ten of the thirteen non-`napp` regression
  timeouts are provably unaffected (their contracts contain no inheritance at
  all, so `is_inherited` is never set); those two and `cov_pilot_st1inch_St1inch`
  are the inheritance-heavy flattened projects, and only st1inch has measured
  before/after evidence (`0 VCC / 1167 assignments` → `45 VCC / 4321`).
- The Foundry emitter substitutes `0` / `address(0)` for a counterexample value
  it could not recover, and emits it as if it were the counterexample's own
  value (`foundry.cpp:1351-1352`). It also emits `vm.startPrank(address(0))` and
  `vm.warp(0)`, which cannot happen on chain. Twenty-one loss sites are
  tabulated in `notes/emitter-ce-value-loss-audit.md` with a proposed fix in the
  named-obstacle shape (mark → exclude → count on stdout). Not implemented.

## Environment facts worth not re-deriving

- `regression/testing_tool.py` STRIPS `--timeout` and `--memlimit`
  (`UNSUPPORTED_OPTIONS`, line 137), so a `test.desc` carrying them is bounded
  only by `ESBMC_REGRESS_TIMEOUT` (180s in this build). A timed-out test is an
  unconditional ctest failure in CORE *and* KNOWNBUG alike.
- `regression/CMakeLists.txt` discovers test directories at CONFIGURE time —
  a newly created regression directory needs `cmake .` in `build/` before ctest
  can see it.
- `notes/coverage-comparison/<project>/` no longer holds the 1inch source trees
  or `_results/lcov.info`. The scope input that depended on them is now pinned
  in `notes/coverage/inputs/own_contracts.json`; the native column is carried
  forward and labelled.

## Stage 3 fixtures: 11 written, 1 DECLINED (not missing)

`ctest -R solidity_path_cov_assert` runs 11, all green:

    r1_pair_written / r1_pair_unchanged        the must-flip pair, six rungs, opposite
    delta_fits / delta_tight / delta_wrapped   the delta rung, three states
    depth_mismatch_refused                     N3
    enc_absent_refused                         N2
    empty_vars_refused                         N1, entry condition (a)
    zero_candidates_refused                    N1, entry condition (b)
    refuses_mapping                            the second scan, refusal BY NAME
    vacuous_region_refused                     the non-vacuity witness

The plan lists a twelfth, `sign_ladder`. IT IS DECLINED, not outstanding.

Its stated purpose is to pin the batch property -- the `ladder summary -- 6
candidate(s)` line -- which the plan says the must-flip pair does not cover.
That is not true of the pair as written: `r1_pair_written/test.desc` pins all
six rungs individually AND pins the summary line with its count. A twelfth
fixture would therefore assert nothing the eleven do not already assert.

Adding it to reach 12/12 would be the exact thing this session refused in the
emitter a few commits earlier: a green test that tests nothing, counted as
emitted and counted as passing, distinguishable from a real one only by reading
it. The count is not the goal; the coverage of distinct properties is.

Recorded here so a reader finds a DECISION rather than a gap.

## Three samples, and where to read them

`notes/emission-loss-four-samples.md` is the current state of subgoal 2.

The ONE quantitative claim that survives FOUR benchmarks, **with its wording
now corrected**: across four benchmarks the emitted suite's forge lcov covers
**40-56%** of the canonical decisions the enumeration's F paths walk (aqua 2/4,
farming 10/18, EscrowSrc 3/6, EscrowDst 2/5).

**It may NOT be worded "emission RETAINS x%".** That is causal, and the two
columns were not produced by the same runs: `emission_loss.py` read its
denominator from `notes/coverage/pathcov/<bench>/reports/` -- the SWEEP's runs
-- while the numerator came from the lcov of tests produced by
`forge_roundtrip.py`'s OWN esbmc runs. The two shared a benchmark name and a
180s timeout and nothing else, so a difference BETWEEN THE RUNS is
indistinguishable from a loss in the emitter. Found by an adversarial audit and
confirmed by reading `emission_loss.py:37` against `:56`.

FIXED FOR THE NEXT MEASUREMENT, not retroactively: `forge_roundtrip.py` now
passes `--cov-report-json`, so each emit run drops its own report in
`_gen/<tag>/`, and `emission_loss.py` prefers those and PRINTS which provenance
it used. The archived four-sample numbers were produced the old way and are
labelled `cross-run` when re-read; re-running the four benchmarks is what turns
the band into a retention rate. **Until that re-run lands, quote the band with
its wording, never with the word "retains".**

Quote the RANGE -- the fourth sample is what stopped it being a number that had
looked precise three times. Everything else taken from aqua alone was narrowed
by a later sample; read that file before quoting any mechanism as general.

Settled there on BOTH Escrows and worth not re-deriving: `ImmutablesLib` is 0/8
because those eight decisions were NEVER ENUMERATED, not because the emitter
dropped them (EscrowDst adds `EscrowDst.sol`'s own two). No emitter change can
touch them.

Re-run any sample with
`python3 notes/coverage/scripts/forge_roundtrip.py <bench> --timeout 180`
and read the per-line loss with
`python3 notes/coverage/scripts/emission_loss.py <bench>`.

## VeriPUT RQ1 state, 2026-08-09 06:58-07:10 CST

Active branch/remotes:

- Working branch: `feat/veriput-fuzz-first`.
- Push target: `E-SOL/feat/veriput-fuzz-first`.
- Do not push `upstream`; do not mutate `/home/samson/workspace/VeriPUT/Datasets`.
- RQ1 artifacts are under
  `/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT`.

Pushed fixes after the peer prepared fallback work:

- `dbd29420af [scripts] Use source scalar names for PUT ladders`
  - `scripts/solidity_path_put.py` no longer rewrites scalar state variables
    to solc `$id` store aliases for assertion-query variable names.
  - Reason: cases like `IdentityManager.convertAddress` certified normal paths
    and selected `_currentIndex`, but the PUT assertion ladder passed
    `_currentIndex$13`; ESBMC's state-component walker expects the source-level
    member name (`_currentIndex`). The symptom was `valid-PUT-no-R1R2` with
    `ladder_refusal`.
  - Verified with `python3 -m py_compile ...` and
    `python3 scripts/test_solidity_path_put.py` (282/282).
- `af8dee81c3 [scripts] Treat valid RQ1 artifacts as successful subjects`
  - `notes/coverage/scripts/rq1_veriput_run.py` now keeps
    `completion_status` for the real terminal state, but if a subject already
    has reference-valid generated tests the top-level `status` is `ok` and the
    original failure moves to `partial_failure_reason`.
  - Reason: `AIRBets` produced `raw=1 valid=1 put=1/1` with R2, but a later
    unit failure made the subject row say `status=error`; that is wrong for RQ1
    raw-valid accounting and confusing for triage.
  - Verified with `python3 -m py_compile notes/coverage/scripts/rq1_veriput_run.py
    notes/coverage/scripts/rq1_veriput_triage.py` and `git diff --check`.
- Pending after this note unless already committed: `rq1_veriput_run.py`
  gained a per-stage memory wait. Before each certify/put subprocess, it waits
  until `--memlimit-gib` fits 60% of current MemAvailable, mirroring
  `certify_all.py`'s internal guard. This prevents high-parallel runs from
  recording a 0.5s `certify ... error` when the inner tool refused to start for
  scheduler memory reasons. Waiting consumes the subject's 600s wall budget and
  is recorded as a separate `resource-wait` stage.

Current aggregate snapshot before the newest runners finish:

- `peer182`: total 182, valid 84, PUT 82, R1/R2 51; buckets
  `{'valid-PUT-with-R1R2': 51, 'no-valid': 98, 'valid-PUT-no-R1R2': 31,
  'valid-no-PUT': 2}`.
- `bugfix124`: total 124, valid 68, PUT 64, R1/R2 25; buckets
  `{'valid-PUT-with-R1R2': 25, 'no-valid': 56, 'valid-PUT-no-R1R2': 39,
  'PUT-with-R1R2-but-no-width': 1, 'valid-no-PUT': 3}`.
- `real203`: total 203, valid 58, PUT 35, R1/R2 23; buckets
  `{'valid-PUT-with-R1R2': 23, 'no-valid': 145, 'valid-PUT-no-R1R2': 12,
  'valid-no-PUT': 23}`.

Runner state:

- Session `96378`, peer batch `AIRBets TESTDONTBUY PipiCoin ShibaJail
  AnyswapV5ERC20 shibabread KizunaInu DogeRocket`, `--jobs 2`,
  `--memlimit-gib 12`, `--redo`.
  - Completed so far: `AIRBets` valid PUT with R2, `TESTDONTBUY` valid PUT
    without R1/R2.
  - This runner started before `af8dee81c3`, so completed rows may still carry
    the old contradictory `status=error` shape even when `valid > 0`.
- Session `9782`, peer batch `KOALA LILY TOAD PONY`, `--jobs 1`,
  `--memlimit-gib 12`, `--redo`.
  - Exited. `KOALA` ended no-valid after about 554s. `LILY` produced
    `raw=2 valid=2 put=2/2` with R1/R2 but kept old `status=error` because
    this runner predated `af8dee81c3`. `TOAD` and `PONY` failed instantly with
    no-valid/error; inspect logs before deciding whether they deserve a rerun.
- Session `63317`, peer batch `HOTDOGE eNew PORCUPINE RIAS`, `--jobs 2`,
  `--memlimit-gib 8`, `--redo`.
  - The first attempt with `--jobs 2 --memlimit-gib 12` was refused by the
    wrapper because `2 * 12GiB` exceeded 70% of current MemAvailable. The 8GiB
    cap is intentional for this batch.

Concurrency rule as of this snapshot:

- Current ESBMC concurrency reached five child processes. Later RSS examples:
  `HOTDOGE` ~8.2GiB, `ShibaJail` ~10.2GiB, `eNew` ~8.5GiB, `PipiCoin` ~7.5GiB,
  with total MemAvailable around 7.6GiB and swap around 1.6GiB.
- Do not add another runner until one or two heavy ESBMC children exit. When
  memory frees, continue with the remaining stale peer no-valid queue, skipping
  subjects already in active sessions.
