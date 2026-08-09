# VeriPUT RQ1 handoff (2026-08-09)

Read this section first; the rest of the file is older coverage work.

## Current execution state (2026-08-09, canonical queue)

Authoritative queue command:

```sh
python3 notes/coverage/scripts/rq1_veriput_queue.py \
  --result-root /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT \
  --out-dir /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/triage-queues
```

Important accounting fix now landed locally: `rq1_veriput_triage.py` prefers
the canonical subject directory over `.redo.*` / `.adopted_from_*` archives
whenever the canonical `subjects/<subject_id>/result.json` exists.  A redo
archive is used only when no canonical result exists.  This matches
`rq1_veriput_run.py --redo`, which archives the old canonical directory and
writes the new result back to the canonical path.

Latest canonical aggregate:

- `valid-PUT-with-R1R2`: 153
- `valid-PUT-no-R1R2`: 52
- `valid-no-PUT`: 31
- `PUT-with-R1R2-but-no-width`: 1
- `no-valid`: 272

Latest queue split:

- `Done`: 153
- `P0`: 84
- `P1`: 2
- `P2`: 141
- `Archive`: 129

The queue TSVs now include two extra scheduling columns:

- `today_action`: what to do with the case today.
- `rerun_policy`: whether an ESBMC rerun is allowed before a named code fix.

Current action counts:

- `done`: 153
- `archive_r1r2_unobservable`: 40
- `archive_concrete_fallback`: 30
- `archive_no_candidate_assertion`: 9
- `archive_no_observable_width`: 1
- `archive_no_witness`: 69
- `archive_timeout_or_killed`: 90
- `archive_low_evidence_no_valid`: 111
- `repair_mapping_dynarray_renderer`: 3
- `repair_width_gate`: 1
- `inspect_artifact_no_valid`: 2

Do not blind-rerun the archived categories.  In particular:

- `cleared_not_certified_fallback` and `timeout_concrete_fallback` are valid
  concrete replay evidence, not certified PUT regions.  Turning non-payable
  value gates into fuzz tests without a certified width strategy would inflate
  the PUT count but weaken the methodology claim.
- Rollback paths can carry a real R0 exit oracle, but R1/R2 post-state rungs are
  usually unobservable on chain because revert restores storage.  They should
  not be rerun for R1/R2 unless the code changes what is observable.

Actionable small list before any benchmark rerun:

- `repair_mapping_dynarray_renderer`:
  - `peer182 / peer_solar__array-utils`
  - `peer182 / peer_solar__Greeter2`
  - `real203 / ensdomains__ens-contracts__StandaloneReverseRegistrar`
- `repair_width_gate`:
  - `bugfix124 / acfix_021_CVE_2018_19832`
- `inspect_artifact_no_valid`:
  - `peer182 / peer_ccsolbmc__ClockBoxContract`
  - `peer182 / peer_soltg__short_circuit_or_inside_branch`
Resolved stale-identity rows:

- `bugfix124 / pop_032_PuttyV2`
- `peer182 / peer_ccsolbmc__BERNIE`
- `peer182 / peer_ccsolbmc__HOTDOGE`
- `peer182 / peer_ccsolbmc__KOALA`

All four were rerun after the queue/triage commits with `--redo`,
`--timeout 600`, `--wrapper-grace 60`, `--memlimit-gib 12`, `--jobs 1`.
All four now have real `budget-exhausted/no-valid/raw=0` results rather than
stale resume errors.  Do not rerun them again without a new region strategy.

Verification already run after this queue/accounting change:

```sh
python3 -m py_compile \
  notes/coverage/scripts/rq1_veriput_triage.py \
  notes/coverage/scripts/rq1_veriput_queue.py
python3 scripts/test_rq1_veriput_triage.py
python3 scripts/test_solidity_path_put.py
```

Results: triage tests 7/7 passed; PUT tests 293/293 passed.

User constraints now:

- RQ1 results are due today. Speed matters more than exhaustive diagnosis.
- Do not run blind benchmark sweeps. A case run must have either (a) no existing
  result or (b) a specific code fix/hypothesis that can change that case.
- Avoid wasting 600s on known low-yield buckets such as fast no-output,
  unsupported, or budget-exhausted cases unless the deliverable needs a final
  recorded failure row.
- Dataset inputs under `/home/samson/workspace/VeriPUT/Datasets` are read-only.
- RQ1 artifacts belong under
  `/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT`.
- JSON artifacts must retain raw and valid outputs, timing, concrete vs PUT,
  and R0/R1/R2 oracle class counts. Foundry replay/double oracle is outside the
  ESBMC generation timeout.

Current aggregate results from existing JSON after the source-named mapping
ladder fixes and before the OR path-guard benchmark validation:

| dataset | strong PUT with R1/R2 | valid PUT no R1/R2 | valid concrete-only | no valid |
|---|---:|---:|---:|---:|
| peer182 | 79 | 26 | 3 | 74 |
| bugfix124 | 40 | 21 | 5 | 57 |
| real203 | 23 | 12 | 23 | 145 |
| all | 142 | 59 | 31 | 276 |

Current queue split from `rq1_veriput_queue.py`:

- Done=142, P0=91, P1=4, P2=145, Archive=127.
- Primary P0 work is not "run more"; it is to clear specific failure
  mechanisms: `guard-nameability`, `mapping-dynarray-unrendered`,
  `return-no-holding-rung`, and raw R1/R2 PUTs with invalid replay width.
- Low-yield buckets (`rollback-unobservable`, `cert-no-coordinate`,
  `cleared_not_certified_fallback`, no-output/time-budget-only rows) should be
  recorded but not debugged first.

Landed/in-progress code change:

- `scripts/solidity_path_put.py` now expands path-guard coordinates across
  source/store scalar names and source/store mapping names before rendering
  `vm.assume` guards. This targets cases like CyberFox where raw R0/R1/R2 PUTs
  were generated but double-oracle validation failed because guards such as
  `_owner$413` and `_isBlackListedBot[account]` were dropped as "not nameable".
- Verified without ESBMC:
  `python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  and `python3 scripts/test_solidity_path_put.py` (`289/289`).
- Do not claim benchmark improvement until rerunning a directly affected case.

Execution strategy from here:

1. Stop blind sweeps. A benchmark rerun needs a named mechanism expected to
   change that case.
2. Commit/push script fixes before running a batch so each result has a stable
   code point.
3. Validate a mechanism on 1-2 direct-hit samples. For the OR guard change the
   first sample is `peer_ccsolbmc__AssetTransfer` because the old artifact
   dropped `msg.sender != InstanceBuyer && msg.sender != InstanceOwner` and had
   no oracle-skip blocker.
4. Expand only if the direct-hit sample improves. Otherwise go back to code,
   not queue execution.
5. Keep all RQ1 result artifacts under
   `/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT` with raw/valid tests,
   time stats, failure reason, concrete-vs-PUT, R0/R1/R2 classes, and
   double-oracle replay outcome. Dataset sources remain read-only.

In-progress code at the time this note was updated:

- `scripts/solidity_path_put.py` has a tested OR path-guard renderer:
  false side of `A && B` becomes one `vm.assume(!A || !B)` and true side of
  `A || B` becomes one `vm.assume(A || B)`.
- `python3 scripts/test_solidity_path_put.py` passes (`291/291`).
- Next step is commit/push that patch, then run exactly one validation:
  `peer182 / peer_ccsolbmc__AssetTransfer` with timeout 600s and 10GiB memory.

Update after commits `42a8b4e105` and `ee3ab785ea`:

- OR/AND path guard rendering is committed and pushed. Direct-hit validation:
  `peer_ccsolbmc__AssetTransfer` improved to `valid-PUT-with-R1R2`
  (`raw=11 valid=10 put=10/11`, 68s), and
  `peer_ccsolbmc__FrontRunner` improved to `valid-PUT-with-R1R2`
  (`raw=6 valid=6 put=6/6`, 363s).
- `peer_solar__LotteryMultipleWinners` and `peer_solar__GuardCheck` remained
  `valid-PUT-no-R1R2`, but their ordinary path guards are now rendered:
  current canonical artifacts show `path_guard_skipped=[]` for the relevant
  join/donate paths. Their remaining blocker is `slot candidates asked=0` plus
  no observable post-state on revert paths.
- Important wrapper semantics: `rq1_veriput_run.py --redo` renames the old
  canonical result directory to `.redo.<time>.<pid>` and writes the new result
  back to the canonical subject directory. Do not inspect the newest `.redo.*`
  directory as if it were the latest run; inspect
  `/Results/RQ1/VeriPUT/<dataset>/subjects/<subject_id>/`.
- New ESBMC internal patch under test: `src/goto-programs/goto_coverage.cpp`
  now allows signed bit-vector state variables to emit R1 equality rungs only
  in the Stage-3 assertion ladder. Stage-2 region/certification still uses the
  old `coord_expressible()` gate and continues refusing signed bounds, so the
  previous vacuous signed-interval risk is not reopened.
- Direct non-wrapper validation for `LotteryMultipleWinners.join` with the
  existing `assert/spec.json` produced:
  `state: post == pre HOLDS`, `state: post != pre REFUTED`, while ordering,
  interval and delta rungs stayed refused for signed. Next validation is a
  single canonical rerun of `peer_solar__LotteryMultipleWinners`; expected
  result is still one PUT, but now with an R1 oracle class.

Update after commits `7f99c72b10` and `49a6da2fb6`:

- Signed equality validation succeeded end-to-end:
  `peer_solar__LotteryMultipleWinners` improved to
  `valid-PUT-with-R1R2` (`raw=3 valid=3 put=1/1 concrete=2/2`, 291s).
  Its PUT now records `oracle_classes ["R0", "R1"]` and a state oracle
  `state: post == pre`.
- `return_value$__msgSender$N` is now rendered as the current `msg.sender`
  expression when building path guard assumes. This is intentionally limited
  to `__msgSender`; do not generalize arbitrary `return_value$_owner` or
  `return_value$_isOwner` to storage without a proof of the helper body.
- Direct-hit peer validation for the `__msgSender` alias succeeded:
  `peer_ccsolbmc__ChinaCoin`, `peer_ccsolbmc__KizunaInu`, and
  `peer_ccsolbmc__TESTDONTBUY` all improved to `valid-PUT-with-R1R2`.
  Each costs about 600s, so do not use this family for more sampling.
- Current aggregate after the two batches:
  `valid-PUT-with-R1R2=148`, `valid-PUT-no-R1R2=53`,
  `valid-no-PUT=31`, `no-valid=276`.

Update after commit `efb8cf5bfe`:

- Scalar assertion vars are now restored from ESBMC store aliases before
  sending `vars` to `--path-cov-assert`; e.g. `owner$3` becomes source
  layout name `owner`.
- Direct validation: `real203 / ensdomains__ens-contracts__Ownable` improved
  to `valid-PUT-with-R1R2` in 20s (`raw=3 valid=3 put=2/2 concrete=1/1`).
  The `isOwner` PUT now has `oracle_classes ["R0", "R1", "R2"]` over `owner`.
- Current aggregate after this validation:
  `valid-PUT-with-R1R2=149`, `valid-PUT-no-R1R2=52`,
  `valid-no-PUT=31`, `no-valid=276`.
- Cheap weak cases inspected but not worth rerunning without a new mechanism:
  `peer_soltg__branches_merge_variables_3` and `peer_soltg__while_1` are
  pure/local-assertion functions with no storage or return oracle to render;
  `peer_solar__Greeter2` needs string mapping slot support
  (`mapping(uint8 => string) helloByLang`) before R1/R2 can be emitted.

Update after commit `664dfde930`:

- Added `notes/coverage/scripts/rq1_veriput_queue.py`.
  It writes queue TSV/summary artifacts under
  `/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/triage-queues`.
- Current queue split from existing artifacts:
  P0=110, P1=4, P2=145, Archive=127, Done=123.
- P0 reason split: 60 valid-weak-with-dropped-guard, 8
  valid-weak-with-dropped-oracle, 42 valid-weak without a dropped-guard signal.
- The latest generator fix does two things:
  1. Safe path-guard splitting for `!(A && B)` and plain `A || B`.
  2. Path-guard-only state/mapping pre-read materialization, so guards over
     `owner`, `balances[msg.sender]`, `programOperators[msg.sender]`, etc. can
     render even when no R1/R2 oracle rung already read that coordinate.
- Verified without ESBMC:
  `python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py notes/coverage/scripts/rq1_veriput_queue.py`
  and `python3 scripts/test_solidity_path_put.py` (`291/291`).
- Next validation, when allowed, must be a tiny P0 dropped-guard sample only.
  Candidate commands were generated from the queue; do not run broad sweeps.

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
- Pending after this note unless already committed: `solidity_path_put.py`
  gained `--min-r2-esbmc-budget` (default 30s). R2 Forge prefilter is
  refutation-only, so it is now shortened or skipped when it would consume the
  verifier-backed R2 proof window. This was observed on
  `peer_ccsolbmc__ShibaJail.setCooldownEnabled`: fuzz found a source-assignment
  R2 candidate (`post == onoff`) and did not refute it, but the ESBMC R2 pass
  was launched with only a 1s timeout, leaving the test as R0-only.

Current aggregate snapshot after the peer resource-refusal recovery batch:

- `peer182`: total 182, valid 95, PUT 93, R1/R2 58; buckets
  `{'valid-PUT-with-R1R2': 58, 'no-valid': 87, 'valid-PUT-no-R1R2': 35,
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
- Session `57553`, peer resource-refusal recovery batch `EStack ChinaCoin
  goldinu SATURNITE`, `--jobs 2`, `--memlimit-gib 8`, `--redo`.
  - Confirmed the earlier 0.5s `certify ... error` rows were scheduler memory
    refusals, not semantic no-valid: `EStack` became valid PUT with R1/R2,
    `ChinaCoin` valid PUT no-R1/R2, `goldinu` valid with PUT+concrete and
    R1/R2, and `SATURNITE` valid PUT with R1/R2.
- Session `98264`, peer resource-refusal recovery batch `Ryujin PROGEV2 TOAD
  PONY`, `--jobs 2`, `--memlimit-gib 8`, `--redo`, currently running.

Concurrency rule as of this snapshot:

- Current ESBMC concurrency reached five child processes. Later RSS examples:
  `HOTDOGE` ~8.2GiB, `ShibaJail` ~10.2GiB, `eNew` ~8.5GiB, `PipiCoin` ~7.5GiB,
  with total MemAvailable around 7.6GiB and swap around 1.6GiB.
- Do not add another runner until one or two heavy ESBMC children exit. When
  memory frees, continue with the remaining stale peer no-valid queue, skipping
  subjects already in active sessions.

## VeriPUT RQ1 state, 2026-08-09 later CST

Additional pushed fixes:

- `4ff37e53e7 [scripts] Parse nested negated path guards`
  - `unwrap_decision_not()` now strips nested `!(...)` layers and toggles the
    path-condition polarity each time.
  - Fixes branch claims like `!(!(msg.sender == owner))`, which previously
    parsed as lhs ``!(msg.sender`` and made the generated PUT lose a path
    guard.
  - Verified with `python3 -m py_compile scripts/solidity_path_put.py
    scripts/test_solidity_path_put.py` and
    `python3 scripts/test_solidity_path_put.py` (283/283).
- `0dd4b7cb73 [scripts] Render unary boolean path guards`
  - `path_condition_from_branch_claim()` now renders simple boolean branch
    claims as numeric guards, e.g. `!(!_isBlackListedBot[account])` becomes
    `_isBlackListedBot[account] == 0` when the term is nameable.
  - Motivation: `peer_ccsolbmc__CyberFox.addBotToBlackList` produced a wide
    PUT with R0/R1 but `valid_reference_test=false`; its `put.json` showed
    `path_guard_skipped` for `!(!_isBlackListedBot[account])`, so the Foundry
    PUT could fuzz into an already-blacklisted account and violate the
    certified normal path. This fix is refutation-safe: unnameable terms still
    skip.
  - Verified with `python3 -m py_compile ...` and
    `python3 scripts/test_solidity_path_put.py` (285/285).

Updated peer recovery progress:

- `peer182` snapshot after Address/Lunar/ShibaSamurai but before pending
  StarNFTProxy/wLitiSale/BERNIE/ShibaKiyo and targeted CyberFox/ShibaJail/
  shibabread rerun: total 182, valid 106, PUT 103, R1/R2 67; buckets
  `{'valid-PUT-with-R1R2': 67, 'no-valid': 76, 'valid-PUT-no-R1R2': 36,
  'valid-no-PUT': 3}`.
- New successful peer recoveries since the older 95-valid snapshot include:
  `Ryujin`, `PROGEV2`, `PONY`, `DogeRocket`, `shibabread` (valid PUT but no
  R1/R2), `MayoOcho`, `TokenVesting`, `Galaxium` (valid concrete only),
  `Address`, `Lunar`, and `ShibaSamurai`.
- Still notable no-valid / weak cases from these batches:
  - `TOAD`: full 600s budget-exhausted no-valid.
  - `MiraNft`: full 600s budget-exhausted no-valid.
  - `BurnableERC20`: full 600s budget-exhausted no-valid.
  - `ClockBoxContract`: emitted one raw concrete fallback for `decimals`, but
    double-oracle valid count was 0.
  - `CyberFox`: old run emitted one raw PUT with R0/R1 and wide `account`, but
    double-oracle valid count was 0; targeted rerun started after the unary
    bool guard fix.

Active runner sessions at this snapshot:

- `98846`: peer batch `Address BurnableERC20 StarNFTProxy wLitiSale`.
  `Address` succeeded with valid PUT+R1/R2; `BurnableERC20` exhausted budget
  no-valid; `StarNFTProxy` and `wLitiSale` still running.
- `40240`: peer batch `ShibaSamurai Lunar BERNIE ShibaKiyo`.
  `Lunar` and `ShibaSamurai` succeeded with valid PUT+R1/R2; `BERNIE` and
  `ShibaKiyo` still running.
- `24264`: targeted rerun batch `CyberFox ShibaJail shibabread`, started after
  the unary bool guard fix. Purpose is to test whether the guard/R2-budget
  fixes improve `CyberFox` no-valid and `ShibaJail`/`shibabread` no-R1/R2.

Operational notes:

- Do not mutate `/home/samson/workspace/VeriPUT/Datasets`.
- Existing active runners were started with `--timeout 600 --wrapper-grace 60
  --memlimit-gib 8 --jobs 2 --redo`.
- Reruns are justified only when a code fix plausibly changes the result; avoid
  blind repeats of full-budget no-valid cases like `TOAD` until region/unit
  scheduling or ESBMC modeling changes.

## VeriPUT RQ1 state, 2026-08-09 09:55 CST

Current process audit at 09:04 CST showed no active VeriPUT/ESBMC runners.
Treat older "active runner" bullets above as historical, not authoritative.

Strict execution policy for the rest of today:

- Do not broad-rerun no-evidence no-valid cases.
- Rerun only cases directly hit by a new code fix, then expand only if the
  representative hit rate is good.
- A valid PUT must pass both gates: verifier-backed assertions and Foundry
  replay on the reference contract. Foundry replay is a refutation guard, not a
  proof.

Current aggregate after targeted reruns:

- `valid-PUT-with-R1R2`: 128
- `valid-PUT-no-R1R2`: 73
- `valid-no-PUT`: 31
- `PUT-with-R1R2-but-no-width`: 1
- `no-valid`: 276

New code fix in progress:

- Mapping slot names now stay source-named for all assertion-ladder inputs:
  `vars`, certified region entries, and mapping pins. ESBMC store aliases such
  as `balances$7` / `_allowances$496` are still used only to prove that the
  mapping is queryable through solc layout metadata.
- Motivation: `BasicToken.balanceOf` and ERC20-like cases had source-named
  `region` but `oracle_vars` or pins still used `state.<map>$id[...]`, causing
  `--path-cov-assert` to refuse with "region coordinate ... cannot be
  expressed".
- Unit test: `python3 scripts/test_solidity_path_put.py` passed (291/291).
- Direct-hit validation:
  - `bugfix124 rcx_reentrancy__0xb5e1...__SmartFix`: changed from
    `valid-PUT-no-R1R2` to `valid-PUT-with-R1R2`; latest `put.json` asks
    `balances[0]`, no unanswered mapping slot, R0/R1.
  - `peer182 peer_ccsolbmc__eNew`: changed from mapping-dynarray-unrendered
    `valid-PUT-no-R1R2` to `valid-PUT-with-R1R2`; wall about 593s.
  - `peer182 peer_solar__BasicToken` was rerun before the mapping-pin half of
    the fix and still showed `state.balances$7[0]` refusal from pins. It should
    be eligible for one justified rerun after this fix if time allows.

## VeriPUT RQ1 state, 2026-08-09 11:40 CST

New pushed fix:

- `db33c1d1cd [scripts] Repair Foundry replay guards`
  - Path-guard numeric literal rendering now converts 40-hex-digit literals to
    decimal before emitting `vm.assume(...)`. This fixes Solidity 0.8.x treating
    address-sized hex constants as EIP-55 address literals in numeric contexts.
  - Constructor replay now repairs zero-sender deployment pranks when the target
    constructor source explicitly forbids a zero sender, including
    `_mint(_msgSender(), ...)`.
  - Verified with `python3 scripts/test_solidity_path_put.py` (293/293).

Representative validations before spending ESBMC budget:

- `peer_ccsolbmc__CyberFox.addBotToBlackList`: copying the existing Foundry
  project to `/tmp` and replacing only the 40-hex guard literal with decimal
  made `forge test --json` report `test_put_CyberFox_addBotToBlackList_path31`
  as `Success`.
- `reprod_DCFToken.setCfg`: copying the existing Foundry project and replacing
  only `vm.startPrank(address(uint160(0)), address(uint160(0)))` with nonzero
  made two of the three `setCfg` PUTs pass Foundry; the failing one was a real
  R2 assertion refutation, not setUp failure.

Official one-shot reruns after the fix:

- `peer182 peer_ccsolbmc__CyberFox`: `raw=1 valid=1 put=1/1`, bucket
  `valid-PUT-with-R1R2`, wall `599.6s`, max RSS about `10.2GiB`.
- `bugfix124 reprod_DCFToken`: `raw=9 valid=7 put=7/9`, bucket
  `valid-PUT-with-R1R2`, wall `617.959s`, max RSS about `1.7GiB`.

Current aggregate after queue refresh:

- `valid-PUT-with-R1R2`: 151
- `valid-PUT-no-R1R2`: 52
- `valid-no-PUT`: 31
- `PUT-with-R1R2-but-no-width`: 1
- `no-valid`: 274

Queue status:

- `Done`: 151
- `P0`: 84
- `P1`: 2
- `P2`: 145
- Remaining canonical raw-no-valid cases are only:
  - `peer_ccsolbmc__ClockBoxContract`: one raw concrete fallback, no PUT; reason
    `cert-no-path`. Lower priority unless a concrete replay/constructor issue is
    found.
  - `peer_soltg__short_circuit_or_inside_branch`: two raw PUT artifacts with
    R0/R1/R2, but rendered width is `a=1`; one Forge success is still not B
    because width gate is false, and the other is a real assertion failure.
    The dropped `return_value$_f$N` path guard alone is not enough to make this a
    valid PUT, so do not spend an ESBMC rerun on it without a new width strategy.
