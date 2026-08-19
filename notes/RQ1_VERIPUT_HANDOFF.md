# VeriPUT RQ1/RQ2/RQ3 Handoff

Updated: 2026-08-19

This is the current handoff for the VeriPUT experiment pipeline.  Older notes
mixed retry workdirs, JSON rows, concrete anchors, and physical Foundry tests;
do not use those as paper denominators.

## Communication Rule

Keep user-facing progress reports short and split long results into separate
updates. Avoid pasting large code or log blocks; report only the experiment
conclusion, essential counts, and necessary artifact paths. This reduces UI
filter false positives in this Solidity verification work and must remain in
effect after conversation compaction.

## Definitions

VeriPUT's publication-facing output is a set of valid Foundry test units for
509 target contracts.  A retained PUT test unit must be:

- synthesized by VeriPUT from an ESBMC-certified path/region;
- present as a physical `.t.sol` function;
- Forge-green on the original target source under the fixed replay setting;
- parameterized as `test_put_*`.

If VeriPUT cannot generalize a path, it may retain a concrete replay.  That is
valid execution evidence, but it is not a PUT.

Every retained test must also preserve the Stage-2 witness as fixed replay
assertions.  These assertions are distinct from the generalized R0--R2
oracles: they record the concrete return components, readable scalar
post-state, exact emitted-log sequence, and exit status observed for the
witness.  A PUT keeps these witness-specific assertions inside the same
`test_put_*` function; they are not emitted as a second anchor test.

The fixed Forge replay setting for evaluation is:

```bash
--fuzz-runs 10000 --fuzz-seed 0x56657269505554
```

The seed is the ASCII bytes of `VeriPUT`.  This replay time is validation time;
it is not part of the 600s VeriPUT generation/oracle budget.

## Fixed Scope

The official RQ1 target denominator is 509 contracts:

| Dataset | Benchmark arg | Targets |
|---|---|---:|
| Patch-Bug-Bench | `bugfix124` | 124 |
| Peer-Reviewed-Contracts | `peer182` | 182 |
| Stress-Projects | `real203` | 203 |
| **Total** | | **509** |

`reprod_DCFToken` is part of the Patch-Bug-Bench 124 and must remain in the
denominator.

## Current Execution Snapshot

Live state as of 2026-08-19 18:20 (update this block, do not append to it):

- **Local host**: Full arm.  bugfix124 and peer182 are COMPLETE; real203 is
  resuming after a host reboot via `$VERIPUT_ROOT/resume_real203.sh`
  (80 remaining subjects named explicitly, no `--redo`).
  Log: `~/logs/campaign-real203-resume.log`.  AST cache: `~/.cache/`.
- **w2** (`invmut-w2`): the no-selection ablation for real203, sharded 20 at a
  time by `$VERIPUT_ROOT/w2_sharded.sh`, with the owner-side reaper pulling each
  shard back and freeing the remote copy.  Log:
  `/home/administrator/VeriPUT/nosel-real203-resume.log`.
- **w1**: removed from the experiment; see the operational-failures list.
- Experiment binary is the Release ESBMC: `$ESBMC_REPO/build-release-static/src/esbmc/esbmc`.
- `$VERIPUT_ROOT/Tools/VeriPUT` is synced and pushed; `SOURCE.json` verifies
  13 files with no hash mismatch and the standalone smoke passes.
- Both repositories are pushed: esbmc `fix/put-materialization-no-valid-stress-shard1`
  (`7f47fcbc3c`) and VeriPUT `exp/rq1-rq3-anchor-closure` (`0adf14f57c`).  The
  esbmc commit deliberately excludes the unrelated in-progress `src/` and
  `regression/` changes.

Current completed implementation evidence:

- Fixed replay assertions are fused into the same `test_put_*` body; generated
  Full PUT files must not contain separate `test_structural_anchor_*`,
  `test_cov_*`, or `test_concrete_replay_*` functions.
- R1/R2.1/R2.3 counterexample-guided input-part splitting is implemented and
  has a live physical smoke where one certified path produced four separate
  Forge-green physical PUT files.
- R2.2 boundary-observation bound expansion is implemented and closed on
  `peer_solar__DateTime.getWeekday`; see the short list below.
- No Region Refinement, No Test Oracle Refinement, and No_Cer_Reg are
  derivation-only arms from Full.  They must not rerun ESBMC.
- No Selection Strategy is the only RQ3 ablation that reruns VeriPUT, wired
  through `--path-cov-no-selection-strategy`.

Reporting rule: every progress report gives BOTH the case count against the 509
denominator AND the test-unit counts (raw / valid / PUT / concrete / R1R2).
`Results/results_all.py` is the reporting entry point; it is organised as tables
and split by research question (`--rq 1,2,3`, `--detail`, `--audit-detail`).

## Authoritative TODO

This is the single authoritative list of unfinished work. Update this list in
the same patch that completes or adds an item; do not maintain a second TODO in
another note.

### Current short list

- [x] Find one small official RQ1 R2.2 live smoke candidate and run it to
  completion.  **Closed on `peer_solar__DateTime.getWeekday`**, whose return
  `uint8((timestamp / DAY_IN_SECONDS + 4) % 7)` is non-monotone in its single
  certified coordinate, so the sampled endpoints cannot see the true range.
  Root: `Results/RQ1_KInduction_Fair600/smoke-r22-weekday-final`.
  Evidence: `boundary-observations.json` present; initial observed bound
  `return in [2, 4]` REFUTED; four counterexample-guided expansions on the
  unchanged input part, `[2,4] -> [2,5] -> [1,5] -> [1,6] -> [0,6]`; final
  `return in [0, 6]` reported `HOLDS` by ESBMC; the emitted
  `test_put_DateTime_getWeekday_path3p1_part_part0_r` carries
  `assertGe(...,0)` / `assertLe(...,6)` and passes 10,000 runs at seed
  `0x56657269505554`.
  **Budget note.** R2.2 absorbs exactly the one observable value each
  refutation reports, so the bound walks linearly and the previous hardcoded
  two rounds stopped short on real units (`DateTime.getHour` reached only
  `[0,20]` of a true `[0,23]`).  `BOUNDARY_OBSERVATION_REFINEMENT_ROUNDS` is now
  4 in `scripts/solidity_path_put.py`.  The expansion rule itself is unchanged
  and still `min`/`max` against the counterexample value.  A wide-range
  observable can still exhaust the budget; that is a measurement limit of R2.2,
  not a defect.
- [x] Produce matched Full/no-selection/no-region/no-test-oracle/no-cer-reg
  smoke roots and run `rq3_compare_smoke.py`.  **Passes**, `failures: []`.
  Roots: Full `Results/RQ1_KInduction_Fair600/smoke-rq3-full`, no-selection
  `Results/RQ3/No_selection_strategy/smoke-rq3-noselect`, and the three derived
  arms under `Results/RQ3/_smoke_derived/`.  Report:
  `smoke-comparison.json`.

  | arm | valid | PUT | concrete | PUT w/ R1R2 | R2.3 | wall_s |
  |---|---:|---:|---:|---:|---:|---:|
  | full | 42 | 42 | 0 | 22 | 48 | 330.9 |
  | no-selection | 42 | 42 | 0 | 22 | 48 | 330.7 |
  | no-region | 42 | 42 | 0 | - | - | - |
  | no-test-oracle | 42 | 42 | 0 | 0 | 0 | - |
  | no-cer-reg | 22 | 0 | 22 | 0 | 0 | - |

  Caveat: this subject pair does not discriminate no-selection, which matched
  Full exactly.  The degradation witnesses remain the `Thicc.transferFrom` and
  `AddressArrayUtilsContract.intersect` pairs recorded below; re-confirm one of
  them under the current driver before reporting RQ3.

- [x] Run a 20--30 target Full sample with the upgraded generator.  **30
  targets, 10 per benchmark, `--order dataset`.**  Roots
  `sample-full-bugfix124-w2-vgfix`, `sample-full-peer182-w1`,
  `sample-full-real203-w1`.

  | sample | valid | PUT | concrete | PUT rate | PUT w/ R1R2 |
  |---|---:|---:|---:|---:|---:|
  | bugfix124 | 111 | 97 | 14 | 87.4% | 43 |
  | peer182 | 150 | 141 | 9 | 94.0% | 58 |
  | real203 | 105 | 88 | 17 | 83.8% | 45 |
  | **total** | **366** | **326** | **40** | **89.1%** | **146** |

  Composition by `stage4_kind`: 246 `abi-value-gate`, 80 `certified-region`,
  33 `cleared-concrete-fallback`, 7 `certified-region-concrete-fallback`.
  Excluding the structural value-gate rows the PUT rate is 80/120 = 66.7%.
  **Decision taken: value-gate PUTs are retained and counted in the headline
  number.**  They are real reachable paths, they were already inside the
  denominator as concrete replays before the promotion (bugfix124 valid stayed
  at 111 across the change), and `stage4_kind` is recorded per row so the
  paper can split the two populations at any time.
  Open item deferred by decision: the paper's §Decision Points excludes
  compiler-inserted checks from path identity, and the nonpayable ABI gate is
  compiler-inserted.  The wording needs one sentence to cover it.

- [~] **509-target Full campaign**, `Results/RQ1_KInduction_Fair600/campaign-full-20260819/<benchmark>`,
  local host, `--jobs 8`.  State at 2026-08-19 18:20:

  | benchmark | cases | raw | valid | PUT | concrete | PUT share | R1/R2 | R1/R2 share |
  |---|---:|---:|---:|---:|---:|---:|---:|---:|
  | bugfix124 | **124/124 done** | 862 | 690 | 631 | 59 | 91.4% | 225 | 35.7% |
  | peer182 | **182/182 done** | 2146 | 1858 | 1778 | 80 | 95.7% | 711 | 40.0% |
  | real203 | 140/203 | 866 | 677 | 622 | 55 | 91.9% | 152 | 24.4% |
  | total | 446/509 | 3874 | 3225 | **3031** | 194 | **94.0%** | 1088 | 35.9% |

  Report BOTH levels every time: the 509 case denominator AND the test-unit
  counts.  The case count alone hides the deliverable, which is test units.
- [ ] Derive No Region Refinement, No Test Oracle Refinement, and No_Cer_Reg
  from the audited new Full corpus.  Do not rerun ESBMC for these three arms.
- [~] No Selection Strategy (the only rerun ablation) on w2 for real203,
  `Results/RQ3/No_selection_strategy/campaign-nosel-20260819-real203`, sharded
  20 subjects at a time and pulled back to the owner's host after each shard.
- [ ] Re-confirm a no-selection degradation witness under the current driver.
  w1 cannot serve: its within-host Full/no-selection pair on
  `peer_ccsolbmc__Thicc` returned raw=10/valid=0 for BOTH arms at 562s, i.e.
  budget-exhausted and non-discriminating.  Run the pair locally after the Full
  campaign instead; that is also the only hardware-clean comparison available.
- [ ] Run final RQ1 coverage and RQ2 mutation/real-bug evaluation only from
  the new audited corpus.

### Open quality defects in the 2026-08-19 corpus

**The expensive stage is already banked.**  Every case retains its Stage-2
journal (`<subject>/cert/certify-results.jsonl`; 124 + 182 + 140 present so
far), and `put_all.py --cert <journal>` drives Stage 3/4 straight from it.  So
every defect below that lives in oracle synthesis, emission, or persistence can
be repaired and re-derived WITHOUT redoing path enumeration or certification.
Only a Stage-2 change forces a full recampaign.  Finish the corpus first; it is
not wasted work.

- [x] **`persistence-error` discards Forge-green valid artifacts** -- FIXED
  (5 cases: `peer_soltg__constructor_state_variable_init`, `..._chain_al`,
  `..._diamond`, `peer_soltg__constructors`,
  `compound-finance__comet__MainnetBulkerWithWstETHSupp`).

  ROOT CAUSE.  The dropped rows are `constructor-revert-only` replays, whose
  oracle is an explicit `assert`/`require`/`revert` in the exact target source
  rather than a certified path of a callable unit -- so they have no
  `path_function`/`enc` and CANNOT have one.  `persist_concrete_replay` demanded
  that identity from every non-source-grounded row
  (`rq1_concrete_replay_store.py`), and its source-grounded exemption set listed
  only `source-grounded-manual-concrete-replay` and
  `source_grounded_callable_recovery`.  So persistence refused,
  `publishable_validity_keys` came back empty, `quarantine_unpersisted_validity`
  withheld every row, and the case published `valid=0` with
  `status=persistence-error` -- while the artifact on disk was
  `forge_status=Success` and `valid_reference_test=true`.

  Two other readers of the same fact already handled this stage2_source
  correctly and independently: `rq3_raw_authenticity_audit.py` classifies
  `source_constructor_revert_fallback and not path_function` as authentic, and
  `rq1_missing_put_recovery.py` maps it to `constructor-fallback`.  Persistence
  was the only reader that did not -- the two-readers-of-one-fact shape this
  repo keeps paying for.

  FIX, and it took TWO gates, which the stratified sample is what caught.  The
  first is the identity: add `source_constructor_revert_fallback` to the
  source-grounded exemption.  Re-run on the sample, the same 4 cases were STILL
  `persistence-error` -- but with a DIFFERENT reason, "concrete replay lacks
  structured witness oracle provenance", so the first gate had in fact cleared
  and a second one behind it had never been reached before.

  The deploy-revert row recorded no `concrete_oracles` at all.  The store
  already knows this shape -- `deterministic_replay_oracles` reads
  `new <Contract>(...)` under `unit == "__deploy__"` and binds the oracle to
  `target_contract` rather than a receiver, because there is no receiver until
  the deployment succeeds, which is exactly what the test asserts never happens.
  What was missing is the RECORDED claim the store cross-checks the test
  against.  `emit_no_unit_deploy_fallbacks` now writes it for
  `constructor-revert-only`.

  Verified end to end against the real published artifact
  (`peer_soltg__constructors`): the row now persists.  Regression tests assert
  all four directions -- persists without a path identity, persists in the full
  emitted deploy-revert shape, still refused when the oracle is unrecorded, and
  a verifier-derived row still requires an exact identity.  The first was
  mutation-checked.
- [x] **342 "PUTs built but never Forge-replayed" were a BOOKKEEPING ARTIFACT**
  -- FIXED, and the earlier reading of this number in this note was wrong.

  Every one is `kind=put forge_status=None`.  They are the PARENT record of a
  certified path that split into oracle input parts.  Each part is emitted as
  its own `.t.sol` with its own `test_put_*_part_*`; the parent keeps the
  UNSPLIT `test` name while its `file` already points at the FIRST part's file,
  so the pair names nothing that exists.  `b_report` replaces the parent with
  its children (`expand_stage4_test_unit_results`), so no Forge run ever reports
  under the parent name -- and the driver's put.json recovery loop, whose dedup
  guards both key on the parent's own name, appended it as an EXTRA raw
  artifact.  174 of the 342 sit in the same result row as their own children.

  The arithmetic closes on the affected subjects: `acfix_fixlink_DepositLog`
  raw=43 valid=36 with 7 phantoms; `peer_solar__DateTime` 23/18 with 5;
  `peer_soltg__constructor_5` 16/12 with 4.

  So these were never lost tests: `raw` was inflated.  Corrected, the corpus
  reads raw 4031 (not 4373) against valid 3668 -- **91.0% of raw artifacts
  become valid**, not 83.9%.  The remaining 168 parents are the Stage-4-timeout
  case where `b_report` wrote no put-summary row at all; there the children are
  exactly what the recovery exists to find.

  FIX: `_put_json_physical_records()` -- recover one row per emitted test unit,
  never the superseded parent, and recover nothing when a split's parts name no
  test.  Regression test
  `test_split_records_recover_their_parts_not_the_parent`; 124 runner tests pass.

- [~] **A proved PUT fails its Forge replay** -- ROOT-CAUSED and the dominant
  half FIXED.  154 rows across 45 subjects (worst: `peer_ccsolbmc__BurnableERC20`
  raw=22 ALL red, `peer_ccsolbmc__ClockBoxContract` 16, `ERC-3643__TREXFactory`
  15).  The Forge log is written under the AST cache workdir and NOT published,
  and the cache was wiped by the host reboot -- so this was re-measured by
  running `forge test --json` directly on the 114 PUBLISHED projects.

  | where it fails | count |
  |---|---:|
  | `setUp()` -- the deployment reverts | **102** |
  | test body | 66 |

  `setUp()` reasons: 51 bare `EvmError: Revert`, 23 `BurnableERC20: supply
  cannot be zero`, 16 `Only owner can perform this operation`,
  5 `MaxCommitmentAgeTooLow()`, 4 `E_ZeroConversionPrice()`,
  3 `MarketUpdateTimelock::constructor: Delay must exceed minimum delay.`

  ROOT CAUSE for the named ones: `synthesize_minimal_emitted_case` defaults
  every scalar constructor parameter to its TYPE default, which is 0, and a
  constructor that rejects 0 reverts in `setUp()` -- so every test in that
  project is red before the certified call is ever made.  This is a DEPLOYMENT
  FIXTURE defect, not an oracle defect: the certified region constrains the
  call, not the constructor.

  FIX: `constructor_guard_param_overrides()` reads the constructor's own guards
  and deploys past them -- `require(P > 0)` / `require(P != 0)` /
  `if (P == 0) revert` -> 1, `require(P >= K)` / `if (P < K) revert` -> K,
  `require(P > K)` -> K+1, `if (a <= b) revert` -> b+1 -- resolving K through
  contract `constant`s including Solidity time units (`2 days` -> 172800).  A
  bound it cannot read (a state variable, an expression) yields NO override, so
  it never guesses.  The certified region's own constructor overrides still take
  precedence.  Verified against the four real failing sources:
  BurnableERC20 `initialBalance_`->1, ETHRegistrarController
  `_maxCommitmentAge`->1, PegStabilityModule `_conversionPrice`->1,
  MarketUpdateTimelock `delay_`->172800.  Test
  `test_constructor_guard_overrides_deploy_past_the_constructors_own_require`;
  506 solidity_path_put tests pass.

  **PILOT MEASURED** (`pilot-ctorfix2`, the 4 worst peer182 subjects, same 600s
  budget).  `peer_ccsolbmc__BurnableERC20` -- the worst case in the whole corpus
  -- goes from raw=22 valid=0 to raw=19 **valid=19** (16 PUT + 3 concrete):

  | | before | after |
  |---|---:|---:|
  | valid units | 4 | **24** |
  | valid PUTs | 4 | 21 |
  | no-valid cases | 3/4 | 1/4 |

  The one still red was `peer_ccsolbmc__ClockBoxContract`, and it turned out to
  be the SAME defect one level deeper: its constructor does
  `admin = _admin; ... mint(...)`, and `mint` carries `onlyAdmin`, which is
  `require(msg.sender == admin)`.  With `_admin` defaulted to a mock address the
  deployment reverts, because the deployer is the test contract.

  `_authority_state_vars_reachable_from_constructor()` resolves this THROUGH THE
  MODIFIER rather than guessing from the name: collect modifiers whose body is
  `require(msg.sender == X)`, collect the functions carrying them, and only when
  the constructor body actually CALLS one of those functions is the parameter
  assigned to `X` deployed as `address(this)`.  A variable called `owner` that no
  constructor-reachable check reads is left alone.  Four tests, including the two
  negative ones (uncalled authority, unguarded callee); 507 solidity_path_put
  tests pass.

- [ ] **RQ1 reporting is not yet wired to the campaign corpus.**  `results_all.py
  --rq 1` prints `--` for every VeriPUT coverage cell and skips the headline
  tables entirely.  Two separate causes, both to fix AFTER the single rerun:
  the coverage summaries for `veriput/{bugfix124,peer182,real203}` are missing or
  stale (the report says so in a GATE line rather than printing a number, which
  is the right behaviour), and the headline tables read a hardcoded
  `Results/RQ1/VeriPUT/campaign-timing/canonical-case-wall.json` that
  `--full-root` does not redirect.  The baselines (SolTG, CC-SolBMC, SolAR,
  SynTest) already report, so only the VeriPUT rows are missing.

- [ ] **PAPER-CRITICAL: an R2 rung is emitted over a WIDER region than the one
  it was proved on.**  Root-caused on
  `bugfix124/acfix_026_CVE_2019_15080` `transferOwnership` path 7, which is one
  of the 5 R2 rows failing on chain.  This is the only class that would
  contradict the mechanical-preservation claim, so it is written out in full.

  The contract is 3 lines: `transferOwnership(address _newOwner) onlyOwner
  { owner = _newOwner; }`.  The emitted PUT asserts two rungs:

      assertEq(_post_owner, uint256(uint160(_newOwner)), "owner: post == _newOwner");
      assertGt(_post_owner, _pre_owner,                  "owner: post > pre");

  The first always holds.  The second cannot hold over the region the test
  fuzzes: the entry state is ESTABLISHED as `state.owner$3 := msg.sender`, so
  `pre == msg.sender`, and the test bounds `msg.sender` over the whole address
  space while `_newOwner` is bounded to a band.  `post > pre` is false for every
  `msg.sender` above that band.

  It is NOT that ESBMC was asked the wrong question.  The assert spec carries
  `establish: [{"source": "msg.sender", "target": "state.owner$3"}]`, so the
  establishment was in the query.  The `assert/run.*.log` for this unit shows
  the rung was asked several times, once per oracle input part, with different
  regions:

      owner: post > pre   HOLDS     4
      owner: post > pre   REFUTED   3

  The rung is REGION-DEPENDENT and both verdicts are correct for their own part.
  What went wrong is downstream: a HOLDS from one part was adopted into a test
  whose bounds come from a different, wider part.  The emitted
  `_newOwner` interval ends at 1096126227998177188652763624537212264744096890878
  while the part-3 spec that produced a HOLDS ends at 2^160-1 -- the rung and the
  region it is emitted with do not come from the same query.

  So the fix is in rung ADOPTION, not in the verifier: every rung must stay bound
  to the exact part-region it was proved on, and a rung whose region differs from
  the emitted part's must be re-proved or dropped.  Until then this class is a
  soundness gap, not a Foundry flake, and it must not be filtered away.

  STILL OPEN, the rest of the 66 body failures: 23 `EvmError: Revert`, 15 `fixed witness
  state: N != 0` (the emitter's own entry-state self-check refusing, which is
  correct behaviour and a separate entry-state gap), 10 `next call did not
  revert as expected` (R0), 5 R2 relation/direction oracles false on chain,
  5 panics, 1 `vm.assume` rejected too many inputs.  The R2 ones are the only
  class that would contradict the mechanical-preservation claim and they must be
  root-caused individually, not filtered.  `Only owner ...` (16) needs a
  `vm.prank` deployer, not a constructor argument.

- [x] **The standalone tool package had silently drifted** -- FIXED with a sync
  script.  `Tools/VeriPUT/` is a COPY, and the campaign runs Stage 4 through it
  (`--stage4-driver .../Tools/VeriPUT/put_all.py`).  The constructor fix above
  landed in the ESBMC tree, passed 506 tests there, and then changed NOTHING in
  its first pilot -- the package still held the older `solidity_path_put.py`.  A
  drifted copy does not fail; it quietly measures the previous version, and the
  pilot result was nearly reported as "the fix does not work".  Re-run against
  the synced package, the same pilot went 4 valid -> 24.

  `Tools/VeriPUT/sync_from_esbmc.sh` now copies the 13 tracked files from
  `$ESBMC_REPO` (no absolute paths), regenerates `SOURCE.json` including the
  dirty flag, and reports what changed.  Run it before EVERY campaign, and
  before reading any pilot as evidence about a Stage-4 change.

- [ ] **Publish the Forge replay log into the case directory.**  It is written
  only under the AST cache workdir today, so the evidence disappears the moment
  the cache is cleared -- which is why the class above had to be re-measured
  from scratch.
- [ ] **`no-output` on library-shaped targets** (`safe-fndn__safe-smart-account__MultiSend`,
  `MultiSendCallOnly`, `compound-finance__comet__CometFactoryWithExtendedAss`).
  Decide whether these are legitimately unschedulable like the two `no-units`
  cases, or a scheduling gap.
- [ ] **R1/R2 headroom is in `abi-value-gate` PUTs.**  Measured over 3031 PUTs:

  | stage4 kind | PUT | R1/R2 | R0-only | rate |
  |---|---:|---:|---:|---:|
  | abi-value-gate | 2333 | 509 | 1824 | 21.8% |
  | certified-region | 654 | 579 | 75 | 88.5% |
  | getter-only | 22 | 0 | 22 | 0.0% |
  | getter-value-gate | 22 | 0 | 22 | 0.0% |

  Correcting an earlier claim in this note: the split is NOT "certified-region
  100% / everything else 0% by construction".  That was an artifact of partial
  bugfix124 data.  Value-gate PUTs DO carry R1/R2 in 509 cases, so the 1824
  without are a gap, not a definition.  The 1824 carry `exit` observation rungs
  only -- no `state` and no `return` rung was ever proposed for them, whereas
  the 509 that succeeded carry 13890 `return` and 5232 `state` rungs.  Only
  3.7% of the 1824 come from `peer_soltg__*` micro-benchmarks, where the unit
  is `pure` with no return value and the contract has no state variable, so
  R0-only is genuinely correct there.  The bulk are real token contracts
  (`peer_ccsolbmc__KOALA`, `LILY`, `Galaxium`, `DogeRocket`, ...) that do have
  observable state.  This is a Stage-3 gap and therefore re-derivable.

  **ROOT CAUSE FOUND -- this is not a missing feature, it is a defect this
  driver introduced.**  The ladder is not "failing to propose" oracles for these
  paths; ESBMC REFUSES the whole ladder before proposing anything:

      ladder_refusal: "the spec says path enc=2 has depth=0, the enumeration
      says 1.  The antecedent is `tr != enc || cnt != depth`, so a wrong depth
      is TRUE on every execution: every candidate would hold vacuously"

  Stage 4 guards every claim with `tr != enc || cnt != depth`
  (`src/goto-programs/goto_coverage.cpp:10160`).  The ABI value-gate promotion
  synthesised its certificate with `"depth": 0` hardcoded in
  `_abi_value_gate_structural_detail`, so for any path of non-zero depth the
  guard is true on every execution and the refusal is CORRECT.  The native
  Stage-2 producer writes the real depth for the same class of path in the same
  journal, which is exactly why 837 value-gate ladders ran and 804 did not.

  | value-gate ladder outcome | count | nature |
  |---|---:|---|
  | ladder ran | 837 | fine |
  | REFUSED: `depth=0` vs enumeration | **804** | this defect |
  | REFUSED: `path enc=1 is not among this unit's N enumerated path(s)` | **626** | same defect, `enc` also hardcoded |
  | REFUSED: no candidate formable (mapping / dynamic array lowered to a contract-scope global) | 333 | real limitation |
  | REFUSED: other | 45 | assorted |

  So 1430 of 2643 value-gate PUT rows lost their oracles to synthetic `(enc,
  depth)` bookkeeping, not to anything about the paths themselves.

- [x] **Carry the enumeration's depth into the promoted certificate.**  The real
  depth was already present in `not_certified_details[enc]["depth"]` (observed
  values 2 and 3), and the promotion was discarding that dictionary wholesale.
  It is now read before the drop and passed to
  `_abi_value_gate_structural_detail(enc, depth)`; a path with no recorded depth
  still falls back to 0.  Regression test:
  `test_value_gate_promotion_carries_the_enumeration_depth`.  121 runner tests
  pass.  Expected to unlock the 804.
- [x] **The `enc`-mismatch rows are the harder half** -- FIXED by fetching the
  enumeration.  They come from `_abi_value_gate_cert_row`, the last-resort
  static certificate, which fires precisely when Stage 2 produced no output at
  all -- so there was no enumeration to read `enc` from at synthesis time and
  the row hardcoded `enc=1, depth=0`, the record of a body that takes no
  decision.  Since `tr` starts at 1 and accumulates `tr = tr*2 + guard`, that is
  only ever right for a STRAIGHT-LINE unit; every branching unit refuses.

  MEASURED, not inferred.  Over the finished 509-case corpus every single
  `not among this unit's N enumerated path(s)` refusal is `enc=1` -- 694 of
  them, `paths=2` through `paths=10000`, with no other enc appearing at all.
  And 1036 rescue certificates were written whose unit has NO enumeration
  recorded anywhere in Python: Stage 2 timed out before writing a journal, which
  is the very condition that triggers the rescue.

  FIX: read the identities out of the instrumented GOTO.  `--goto-functions-only`
  stops after GOTO construction -- no solver, no k-induction -- and prints
  `ASSERT path_tr$N != <enc> || path_cnt$N != <depth> // <path_function>:path:K`
  for every enumerated path.  This is the same extraction
  `rq1_put_kinduction_revalidate.current_path_candidates` already depends on.
  New `_enumerate_unit_paths()` runs it and `_abi_value_gate_cert_row` now
  certifies the gate on EVERY enumerated path -- which is what
  `_promote_pin_excluded_value_gate_paths` already does for units whose Stage 2
  did finish, so the two value-gate routes finally agree.

  Verified against real ESBMC output on `bugfix124/pop_018_PrivatePool`
  `setFeeRate`, one of the refusing units: the enumeration returns
  `[(2,1), (6,2), (14,3), (15,3)]` in **3.7s**, and `enc=1` is indeed not among
  them.  ESBMC's own banner for that run reads `4 path(s) total`, matching the
  `paths=4` in the refusal it used to print.

  Fail-safe and bounded, both reported rather than silent: an empty enumeration
  (budget gone, frontend refusal, unit not reached by the focus filter) keeps
  the historical single-entry row and records
  `path_identity_source=unenumerated-single-path-assumption`; a unit wider than
  `ABI_VALUE_GATE_MAX_CERTIFIED_PATHS=64` keeps the shallowest 64 and records
  `certified_paths_dropped_over_cap`.  The hard-timeout call site is AT the
  deadline, so its enumeration is skipped rather than borrowing from the strict
  finalization reserve.

  Regression tests `test_value_gate_certificate_anchors_to_every_enumerated_path`
  and `test_unit_path_enumeration_reads_the_instrumented_goto`; 123 runner tests
  pass, and the new ones were mutation-checked (forcing the enumeration empty
  turns five assertions red).

  **PILOT MEASURED** (`pilot-encfix`, 6 bugfix124 subjects that logged the
  refusal, same 600s budget, same host):

  | | before | after |
  |---|---:|---:|
  | R1/R2 share of PUT | 32.1% | **49.4%** |
  | R1/R2 PUTs | 27 | 40 |
  | PUT rows | 84 | 81 |
  | cases with R1/R2 | 5/6 | 5/6 |
  | raw / valid units | 85 / 77 | 81 / 75 |

  The PUT count moves DOWN slightly and that is the expected trade, not noise: a
  rescued unit now offers up to 64 certified paths where it offered one, so
  Stage 4 spends more of the fixed case budget per unit.  PUT share of valid
  units stays 98.7%.  Report both levels.

  **Revised expectation.**  The earlier estimate in this note (35.3% -> ~50%,
  ceiling 71.9%) was built on the wrong model, namely that the gap was a
  proposal gap across 1131 state-bearing contracts.  The mechanism is now known
  and narrower but far more certain: 804 rows should regain a ladder from the
  depth fix alone.  Do not re-estimate -- measure.

- [~] **Pilot the depth fix.**  Running on w1 under a deliberately relaxed
  3000-second budget.  That budget is a MECHANISM CHECK, not a campaign
  setting: w1 is roughly 30x slower than the local host -- subjects that take
  13-20s locally hit `budget-exhausted raw=0` at 553s of the 600s budget there
  -- so its numbers must never be reported as campaign results.  The question
  the pilot answers is binary: does the ladder now run for a path that
  previously logged the depth refusal?  Re-measure the real conversion rate on
  the local host once the Full campaign finishes.

### Stratified-sample validation (the gate before the single rerun)

35 subjects -- 10 bugfix124, 14 peer182, 11 real203 -- drawn by even spread over
each defect-signature pool, excluding subjects any single-fix pilot was written
against, plus an unaffected control stratum.  Lists and the drawing rule are in
`VeriPUT/Notes/samples/`, in the repo rather than in tmpfs.  Same 600s budget,
same host.  `sample-v3` carries the first five fixes:

| | before | after |
|---|---:|---:|
| R1/R2 share of PUT | 24.6% | **43.9%** |
| PUT rows with R1/R2 | 76 | 125 |
| ladder ran | 223 | 291 |
| REFUSED depth mismatch | 61 | **0** |
| REFUSED enc not enumerated | 34 | 21 |
| phantom `forge=None` PUT rows | 28 | 16 |
| forge `green=False` rows | 28 | 25 |
| raw -> valid conversion | 71.8% | 73.3% |
| PUT share of valid | 91.5% | 91.1% |
| valid PUTs | 214 | 195 |
| cases with R1/R2 | 14/35 | 19/35 |

READ THE PUT COUNT HONESTLY.  Valid PUTs fall 9% and that is the budget trade,
confirmed per subject rather than assumed: `peer_ccsolbmc__AIRBets` goes 509s ->
571s and now saturates the 600s budget, returning 17 units where it returned 24,
every one of them with R1/R2 and with raw == valid == put.  A unit that used to
refuse its ladder instantly now runs it.  Whether that trade is the right one is
the author's call, and the lever is
`ABI_VALUE_GATE_MAX_CERTIFIED_PATHS` (currently 64).

WHAT THE SAMPLE CAUGHT that no single-fix pilot did:
  * the persistence fix had a SECOND gate behind it (recorded oracle
    provenance), so all 5 `persistence-error` cases were still failing;
  * 18 of 20 value-gate rescues never got their enumeration, because the rescue
    fires at the budget wall -- which is what moved the enumeration to one
    subject-wide run at case start.

Both are fixed and re-measured in `sample-v4`.  This is the reason the protocol
runs a sample before the rerun, and the reason the rerun stays at exactly one.

### Stage-2 non-certification budget (bugfix124 sample, preliminary)

| reason family | share | nature |
|---|---:|---|
| shrink budget exhausted, witness differs on a NON-coordinate | 42.9% | modelling gap, fixable |
| single-point check returned UNKNOWN, driver fails closed | 30.0% | solver limit, behaviour is correct |
| region VACUOUS | 21.4% | region search miss |
| statically inseparable sibling | 5.7% | intrinsic |

The 42.9% is one concrete cause: the external-call result (`extcall.__sent_result*`)
decides the branch but is not a coordinate, so shrink can never converge on a
separating region and the path degrades to the structural gate.  Promoting it
to a constrainable coordinate is the single highest-leverage Stage-2 change --
and the only item here that would force a full recampaign, so schedule it last,
after the baseline exists and the Stage-3/4 repairs above have been measured.

Correcting an earlier note: the 30.0% "no verdict" family is NOT a wiring
defect.  All 31 occurrences are the same sub-family -- the §Certification
single-point check returns UNKNOWN and the driver refuses to treat an undecided
answer as discharged.  That is correct conservative behaviour.

### Operational failures during the 2026-08-19 campaign, and their fixes

Each of these silently cost data or time.  They are recorded because every one
of them looked like success from the outside.

1. **A refused shard was recorded as done (w2, 103 subjects lost).**  The static
   memory gate refused `--jobs 4 x --memlimit-gib 10 = 40GiB` against
   `2.2 x MemAvailable(18.1GiB) = 39.8GiB` -- a 0.2 GiB margin that flipped once
   earlier shards warmed the page cache.  The shard script treated a non-zero
   exit as non-fatal, so shards 6-11 "finished" in zero seconds and the log
   still printed `ALL SHARDS DONE`.  Fixes: `--mem-fraction 6.0` (the real
   safety valve is the runtime `--stage-mem-fraction 0.60`; measured peak was
   9 GiB of 21), and the shard loop now prints
   `!!!!! shard i-j FAILED ... its subjects are NOT in the corpus`, counts
   failures, and exits non-zero with `SHARDS FINISHED WITH N FAILED SHARD(S)`
   instead of `ALL SHARDS DONE`.  A resume takes `START_AT=<n>`.
2. **w2 is WSL2; its "936 GiB free" is fiction.**  ext4 lives in a VHDX on a
   Windows C: drive with ~13 GiB free, and a VHDX only ever grows -- deleting
   files inside Linux never returns space to C:.  A full 203-case run needs
   ~10 GiB and had already crashed the VM once.  Fix: shard, and run a reaper on
   the owner's host that rsyncs each published subject back and deletes the
   remote copy, holding w2's high-water mark at ~20 GiB.  Also check `df -h` and
   `free -g` on any new host first: w2's `/tmp` is an 11 GiB **tmpfs**, so an
   AST cache placed there competes with the per-run memory limit.  It now lives
   on the 1 TB disk.
3. **The local host rebooted mid-campaign and `/tmp` was wiped.**  Published
   results survived (bugfix124 124/124 and peer182 182/182 intact), but the
   campaign log and AST cache did not.  Logs and caches now live under `$HOME`
   (`~/logs/`, `~/.cache/`).  Recovery must NOT rerun `run_full_campaign.sh`:
   it passes `--redo` over all three benchmarks and would delete the two
   finished ones.  `resume_real203.sh` instead names only subjects lacking a
   `result.json` via repeated `--subject-id`, and omits `--redo`.
4. **w1 is not usable for RQ3.**  Its within-host Full/no-selection pair on
   `peer_ccsolbmc__Thicc` returned raw=10/valid=0 for both arms at ~562s of the
   600s budget.  It cannot produce a baseline, so it cannot show degradation.

### Machine assignment for the 2026-08-19 campaign

The local Release ESBMC is a **static** binary, so remotes do not need to
rebuild: copy
`build-release-static/src/esbmc/esbmc` directly.  This matters for w1, which has
too little memory to build comfortably.

| Host | Arm | Benchmark | `--jobs` | `VERIPUT_ROOT` | `ESBMC_REPO` |
|---|---|---|---:|---|---|
| local | Full | bugfix124, then peer182, then real203 | 4 | `/home/samson/workspace/VeriPUT` | `/home/samson/workspace/esbmc` |
| w2 | no-selection | real203 | 2-3 | `/home/administrator/VeriPUT` | `/home/administrator/veriput_esbmc/repo` |
| w1 | no-selection | bugfix124 | 1 | `/root/VeriPUT` | `/root/workspace/ESBMC_Commit/esbmc` |

Operational notes learned the hard way:

- **Detach remote runs with `setsid`.**  `ssh host 'nohup ... &'` is reaped when
  the ssh session closes; the first w2 smoke died silently after one subject.
  Use `ssh -n host 'setsid nohup ... < /dev/null > log 2>&1 &'`.
- **w1's `ESBMC_REPO` is `/root/workspace/ESBMC_Commit/esbmc`.**  The other path
  the older notes mention, `/root/InvMut/invmut/esbmc`, has no build tree.
- **w2 had no `Datasets/`,** which `target_manifest.py` needs for the bugfix and
  stress target lists.  Syncing only `*.sol`, `*.csv`, `*.json` is ~692 MB
  instead of the full 5.7 GB.
- **The static `--jobs` guard refuses the documented local setting.**
  `--jobs 4 --memlimit-gib 12` is 48 GiB against a 70% MemAvailable ceiling, so
  it is refused outright.  The per-stage gate (`--stage-mem-fraction`, default
  0.60) still throttles each Stage-2/4 launch at runtime, so raising
  `--mem-fraction` is admission control only, not a real memory increase.
  Measured peak with 4 concurrent ESBMC processes was about 9 GiB.  The
  600-second case budget and the 12 GiB per-process cap are unchanged.

### Defects found and fixed while validating the pre-campaign gates

These were all found by measuring real sample output rather than by reading
summary JSON.  Each has a regression test.

1. **The nonpayable ABI value-gate path was degraded to a concrete replay.**
   Stage 2 auto-pins `msg.value == 0` so the body paths can certify, which by
   construction excludes the compiler's value-gate revert path from region
   search.  That path then fell through to `no-coordinate-concrete-fallback`,
   costing roughly one PUT per non-payable public/external unit.  On a 10-target
   `bugfix124` sample it was 31 of 61 recorded non-certifications and held the
   PUT rate at 55.9%.  `rq1_veriput_run.py` now promotes exactly those
   pin-excluded paths to the existing structural certificate over
   `msg.value in [1, 2**256-1]` and drops their duplicate concrete fallback, so
   one path never yields both a PUT and a concrete replay.  The promoted rows
   reuse `certification_source = structural-abi-gate-no-coordinate` so they take
   the established structural-anchor handling; how the row was reached is
   recorded in `driver_diagnostic.abi_value_gate_pin_promotion` and
   `promoted_from`.  Measured on the same 10 targets: PUT 62 -> 97, PUT rate
   55.9% -> 87.4%, valid unchanged at 111.  `acfix_real_BaseEscalationManager`
   went from 0/5 to 6/6.
   Only the literal pin-exclusion reason is promoted; every other
   non-certification is a search result and is left alone.

2. **Oracle input-part children carried no `put_json`.**  A split path emits one
   physical `test_put_*_part_*` per final part, but the row lookup indexed only
   the parent's `(file, test)`, so 19 of 36 valid rows on one subject had an
   empty `put_json`.  Both `rq3_derive_from_full.py` and `rq3_compare_smoke.py`
   read oracle counts from that file, so every derived ablation and the
   comparison gate itself refused.  The lookup now also indexes each
   `test_units` child under its own identity.

3. **`rq3_derive_from_full.py` read `valid_tests` from the wrong level.**  The
   runner writes `{"schema": ..., "row": {...}}` and keeps the rows inside
   `row`; the derivation read the envelope top level and reported
   "no strict-valid Full rows" for every current Full root.  It now accepts both
   shapes, as `rq3_compare_smoke.py` already did.

4. **Split paths collided on the concrete-basis identity.**  Each final oracle
   input part retains its own basis, but the identity was
   `(path_function, unit, enc, piece)`, so `--mode no-cer-reg` refused with
   "duplicate concrete basis".  The identity now includes the oracle input part,
   propagated as `oracle_input_part` on both the manifest origin
   (`replay_identity`) and the Full result row.  Unsplit rows carry no part and
   keep their previous identity.

5. **`--benchmark real203` selected 241 targets, not 203.**  The runner mapped
   both `real203` and `stress203` to the manifest selector `stress243`, which
   returns the wider 241-target Stress set; only the manifest's own `stress203`
   selector applies `prepared_ok_only` and returns 203.  The campaign would
   therefore have run a 547-target denominator while every document says 509,
   with no error anywhere.  Caught after w2 queued 241 rows and before any
   result was published.  `TARGET_BENCHMARK_ARG` now maps `real203` and
   `stress203` to `stress203`; `stress243` keeps the wider set under its own
   name.  Verified: 124 + 182 + 203 = 509.  Regression test:
   `test_official_benchmarks_resolve_to_the_509_target_denominator`.

6. **Public state getters were never scheduled once a subject had any unit.**
   `emit_no_unit_getter_fallbacks` fires only when the schedule contains no
   units at all, but `roulette`-style subjects now schedule `fallback` as a
   unit, which suppressed the rescue that the legacy corpus relied on to emit
   the `pastBlockTime` getter PUTs.  A scan found 28 of 49 sampled subjects
   holding 42 unscheduled zero-argument public-state getters, and 21 of the
   first 42 local campaign cases returned `status=no-output raw=0` because of
   it.  `emit_zero_yield_getter_fallbacks` now queries those getters, but only
   for a case that produced no valid artifact at all, so a productive target
   never loses budget to them.  Smoke: `roulette` 0 -> 2 PUTs and `ether_lotto`
   0 -> 4 PUTs, all Forge-green.

7. **A Stage-2 timeout discarded the structural ABI value gate.**  The
   nonpayable value-gate region is certified structurally -- the entry reverts
   for every `msg.value > 0` before its body runs -- so it needs no solver
   evidence.  It was nevertheless emitted only after Stage 2 finished, so a
   Stage-2 timeout on the last scheduled unit aborted the whole subject with
   `status=timeout` and no output at all.  Six of the first 23 local campaign
   cases were lost this way, every one of them an
   `unchecked_low_level_calls` batch-transfer contract whose unbounded
   `address[] memory` loop cannot converge inside the 600-second budget --
   and the legacy corpus does hold a value-gate PUT for exactly those
   subjects.  `_structural_abi_value_gate_rescue` now certifies the gate at
   the point where Stage 2 would otherwise abandon the unit, and only when
   that unit produced no Stage-4 candidate, so real Stage-2 evidence still
   wins.  Smoke on `rcx_unchecked_low_level_calls__0xa46edd...__TIPS`: 0 -> 1
   PUT, `test_put_EBU_transfer_path1p1`, Forge-green, matching the legacy
   artifact for that subject.  Regression tests:
   `test_stage2_timeout_rescue_certifies_the_structural_abi_value_gate` and
   `test_stage2_timeout_rescue_skips_units_that_already_have_candidates`.

8. **The strict-rerun publisher renamed a directory across filesystems.**
   `_publish_strict_certification_artifacts` moved the pre-created
   `cert/fixtures` directory into the staging root with a bare `os.replace`.
   The staging root lives under the AST cache, so on any host where that cache
   is not on the same mount as `VeriPUT/Results` the call raises
   `EXDEV: Invalid cross-device link` and the case dies as
   `status=error wall=0.0s` before Stage 2 runs.  Local never saw it because
   `/tmp` and `Results` share one disk there; w2 lost three `ensdomains`
   targets to it.  Moves now go through `_move_tree`, which falls back to a
   copy on EXDEV, matching what the Stage-4 publisher already did.  Regression
   test: `test_move_tree_falls_back_to_copy_across_filesystems`.

   Related, on the same host: w2's `/tmp` is an 11 GiB **tmpfs**, so pointing
   `--ast-cache-root` there put every AST cache and ESBMC workdir in RAM,
   competing with the 12 GiB per-run memory limit.  `remote_nosel.sh` now
   places the cache under `$VERIPUT_ROOT/.cache` on the 1 TB disk.  Check
   `df -h` on any new host before assigning it work.

### Hardware confounds the fixed 600-second budget

The case budget is wall time, so a slower host produces fewer PUTs for the same
code.  Measured on identical subjects and identical drivers:

| subject | arm | host | wall_s | valid/PUT |
|---|---|---|---:|---|
| BaseEscalationManager | Full | local | 94.6 | 6/6 |
| BaseEscalationManager | no-selection | local | 95.1 | 6/6 |
| BaseEscalationManager | no-selection | w2 | 241.5 | 6/6 |
| BaseEscalationManager | no-selection | w1 | 554.3 | 0/0 budget-exhausted |
| DepositLog | Full | local | 236.4 | 36/36 |
| DepositLog | no-selection | local | 235.6 | 36/36 |
| DepositLog | no-selection | w2 | 457.9 | 30/30 |

Locally the no-selection arm is indistinguishable from Full on both subjects.
On w2 the same arm loses six PUTs and takes twice as long; on w1 it produces
nothing at all.  **Running Full locally and no-selection on the remotes would
report that hardware gap as an RQ3 ablation effect.**  Any Full-versus-ablation
claim must therefore come from a single host.

Decision taken: w1 is dropped from generation.  Local is the RQ1 Full
authority for all 509 targets.  w2 runs *both* arms for `real203` so that
benchmark's RQ3 comparison is within-host; w2's Full numbers are RQ3 baseline
only and must never be merged into the RQ1 totals.

### P0: required before the final 509-target Full campaign

- [x] **Implement oracle input parts for R1/R2.1/R2.3.**
  - Add an explicit `OracleInputPart` representation containing region, holes,
    representative witness, inherited proved assertions, and stable `part_id`.
  - Extract controllable input coordinates from the exact failed assertion
    claim in `assert/cov-report.json`.
  - Split only a certified parent part, preserve an exact disjoint union, and
    requery the refuted candidate on eligible children. `UNDECIDED` omits the
    candidate without splitting. R2.2 must not use this splitter.
  - Materialize one `test_put_*_partN` per final part. Each part requires its
    own authenticated representative replay before fixed assertions can be
    attached; never attach the original witness to a child that excludes it.
  - Cap part count and split depth, merge adjacent parts with identical oracle
    sets, and fail closed when the counterexample coordinate cannot be rendered.
  - Files: `scripts/solidity_path_put.py` and
    `scripts/test_solidity_path_put.py`.
  - Done when tests cover one- and multi-coordinate splits, holes, budgets,
    inherited assertions, representative-witness binding, and a real
    Forge/ESBMC smoke emits multiple green part PUTs.
  - Current partial progress: `OracleInputPart`, exact binary splitting,
    hole projection, full representative-CE propagation, complete-CE
    coordinate extraction, and the verifier scheduler are implemented. Main Stage 4 now reuses the parent ladder
    verdict, writes a narrowed child assertion spec, runs a real k-induction
    `--path-cov-assert` query only for the child that excludes the refutation,
    and accepts only the exact rung from a final ladder summary. The default
    cap is four parts and split depth two; `put_all.py` passes both controls.
    Tests prove exact disjoint-union, forward/reverse splits, R2.2 exclusion,
    R1 undecided behavior, assertion inheritance, exact rung/CE association,
    and the common `[0,5]`/`[6,10]` partition for opposite R2.1 directions.
  - Publication safety is connected to physical emission and accounting:
    adjacent parts merge only when their proved oracle sets match, each final
    part emits one physical `test_put_*_part_*` function in its own `.t.sol`,
    and `put.json.test_units` records one child row per physical PUT.  The B
    gate expands those rows and refuses any child declaring
    `requires_fixed_replay_fusion=true` until a verified concrete
    `fixed_replay_fusion` record is attached.
  - Latest code progress: `OracleInputPart` now distinguishes the part
    representative coordinates from the complete CE needed for fixed replay.
    Root parts keep the selected Stage-2 witness CE; refutation children keep
    the failed assertion claim CE; witness children inherit their parent's CE.
    State names with ESBMC suffixes such as `state.y$17` are accepted when they
    uniquely normalize to the part coordinate `state.y`.
  - `oracle_input_part_suffix()` and
    `oracle_input_part_ladder_rows()` now define the physical materialization
    boundary.  A final part gets a stable Solidity-safe suffix, and only
    verifier-proved `(observable, assertion)` rows are converted into PUT ladder
    rows.  Fixed replay/R0 markers are intentionally excluded from this helper.
    `oracle_part_materialization_plan()` now produces fail-closed physical PUT
    plans only for parts with their own authenticated complete representative
    CE; it no longer falls back to the coordinate-only witness.
    `python3 scripts/test_solidity_path_put.py` passes 505/505 checks after this
    addition, and `python3 scripts/test_put_all_accounting.py` passes.
  - Latest `put_all.py` progress: `attach_certified_ce_anchor()` now accepts an
    explicit `representative_ce` while defaulting to the original certified CE.
    This is required for refutation-side child parts whose representative is an
    oracle-counterexample point rather than the original Stage-2 witness.  The
    accounting regression confirms the representative CE drives the fusion hash,
    exact parameter condition, and fixed return assertion.
  - `put_all.py` now generates a separate retained certified-basis replay for
    each child part that carries a complete representative CE.  Basis workdirs
    and test files receive a stable part suffix to avoid cross-part overwrite.
    After a basis replay is Forge-green, `attach_certified_ce_anchor()` fuses
    its fixed replay assertions into the matching child `test_put_*_part_*` and
    writes the fusion metadata back to that child row in the parent `put.json`.
    Structural ABI-gate/getter certificates do not create a retained basis
    replay, because their fixed replay evidence is the region-proved R0 call
    already inside the `test_put_*`.
  - Completed by the `veriput_transfer_physical_split_v8` live smoke documented
    below: one certified path split into four parts, all four received their own
    claim-bound fixed replay assertions, physical file, Forge row, and valid B
    result. All four also pass 10,000 fixed-seed fuzz runs.

- [x] **Add derivation-only `no-cer-reg` mode.**
  - Full already retains one Forge-green concrete basis for each published PUT
    in `retained_concrete_bases` and `<subject>/concrete-replays/manifest.json`.
  - Extend `notes/coverage/scripts/rq3_derive_from_full.py` with
    `--mode no-cer-reg`: select each strict-valid Full row's exact retained
    basis, preserve fixed return/state/event/exit assertions, remove every
    certified-region parameter and generalized R0--R2 assertion, and replay it
    with 10,000 runs and the fixed `VeriPUT` seed.
  - Refuse missing, duplicate, red, or identity-mismatched bases. Do not rerun
    ESBMC and do not use the legacy `run_rq3_no_cer_reg.py` corpus as final data.
  - Add positive, missing-basis, duplicate-basis, and identity-mismatch tests.
  - Completed: all four derivation tests pass; persisted test/source/log hashes
    and Forge success are checked again during extraction.

- [ ] **Run a live R2.2 counterexample-refinement smoke.**
  - Current regression injects exact failed-claim data and proves scheduling,
    but the completed acfix002 Full smoke did not exercise a refuted boundary
    observation.
  - Select a small path whose initial observed bound is refuted, confirm the
    next query uses the CE-derived endpoint on the unchanged input part, and
    require ESBMC `HOLDS` plus Forge success before accepting the emitted R2.2.
  - Avoid mapping-heavy or external-call-heavy units for this smoke.  The
    attempted `notifyRewardAmount` candidate reached a real assertion query but
    grew to about 7.7 GiB and was stopped; do not use it as evidence.

- [ ] **Run the strict Full-versus-ablation smoke comparison.**
  - `notes/coverage/scripts/rq3_compare_smoke.py` reports only strict-valid
    test units, PUT, concrete, PUT-with-R1/R2, R2.1/R2.2/R2.3 assertion counts,
    and generation wall time. It fails when any ablation has more PUTs or more
    R1/R2 PUTs than Full, and requires `no-cer-reg` to contain zero PUTs.
  - Example after producing matched roots:
    `python3 notes/coverage/scripts/rq3_compare_smoke.py --full FULL_ROOT
    --no-selection NO_SELECTION_ROOT --no-region NO_REGION_ROOT
    --no-test-oracle NO_TEST_ORACLE_ROOT --no-cer-reg NO_CER_REG_ROOT
    --out smoke-comparison.json`.
  - A zero-valid no-selection arm is a legitimate degraded result; a zero-valid
    Full root is refused as an invalid comparison baseline.

- [x] **Freeze and synchronize the final standalone tool package.**
  - After the preceding changes, copy the final driver into
    `$VERIPUT_ROOT/Tools/VeriPUT`, update `SOURCE.json`, and require identical
    SHA-256 hashes, standalone/ablation tests, `py_compile`,
    `pylint --errors-only`, and Stage-4 accounting tests.
  - Keep tool code repo-relative. No experiment result path may be hard-coded
    into the standalone package.
  - Current status: synchronized for the active smoke build.  The current
  SHA-256 values match between the ESBMC checkout and
    `$VERIPUT_ROOT/Tools/VeriPUT`: `solidity_path_put.py`
    `2b5baffcdab4b3919aa92bf424667a09bbf683f0d2137490e7668f7a0fc2f85d`,
    `put_all.py`
    `7fbc57ba41d4c36e9e7475bdb2f16e445aee6ab058681b5f313959de187ec6b1`,
    and `rq1_anchor_events.py`
    `3390f09e78bdc9b99e3660a7d169e35a3decbf69fa67859deb48ad8b6c910e92`.
    Keep rerunning this check after every driver patch before handing the
    package to another runner.

### P1: campaigns and derived corpora

- [ ] **Run a 20--30 target Full sample after all P0 implementation work.**
  Check strict replay persistence, fixed assertions inside PUTs, R2 subfamily
  accounting, multi-part identity, 10,000-run fixed-seed Forge validation, and
  confirm fuzz validation remains outside the 600-second generation budget.
  This sample is the first place to measure whether the upgraded generator
  changes the legacy 1460/1446/14 corpus shape; do not merge it into
  `$VERIPUT_ROOT/Results/RQ1/VeriPUT` without a clean audit.

- [ ] **Run the final Full campaign over exactly 509 targets.**
  The current 1460/1446/14 clean corpus is a legacy audited baseline, not the
  final result for the upgraded fixed-assertion and oracle-part implementation.

- [ ] **Derive No Region Refinement, No Test Oracle Refinement, and No_Cer_Reg
  from the new Full corpus.** These three arms must not rerun ESBMC. Run their
  exact physical tests through Forge after transformation.

- [ ] **Run No Selection Strategy as the only verifier rerun ablation.**
  Wiring and two one-target comparisons are complete; run all 509 targets with
  the same budget/toolchain as Full and verify it does not gain tests through a
  configuration mismatch.
  The command and dry-run check are recorded below; this is the arm most useful
  to hand to another runner.

- [ ] **Run final RQ1 coverage, RQ2 mutation/real-bug execution, and RQ3
  reporting.** Consume only newly audited corpora. Rebuild JSON summaries
  atomically from physical tests; do not merge legacy aggregate counters.

### Completed foundations

- [x] Fixed replay assertions are fused into the same `test_put_*` call.
- [x] Full retains a separate authenticated concrete basis for every published
  PUT and refuses publication when persistence is incomplete.
- [x] R2 rows are classified as R2.1, R2.2, or R2.3 in verifier and final
  oracle accounting.
- [x] R2.2 uses exact CE-guided output-bound expansion on an unchanged input
  part; sampled observations alone never count as proof.
- [x] No Region Refinement and No Test Oracle Refinement derivation logic and
  tests exist.
- [x] No Selection Strategy reaches ESBMC; the Thicc `transferFrom` paired smoke
  shows a wall-time degradation with the same PUT output, and the ArrayUtils
  `intersect` paired smoke shows Full producing two PUTs while no-selection
  produces one PUT and spends roughly 46x more wall time under the same budget.
- [x] Fixed Forge validation uses 10,000 runs and seed
  `0x56657269505554` (ASCII `VeriPUT`) outside the generation budget.

## Current Clean Corpus

Current audited legacy publication-facing corpus. Preserve it as a baseline,
but do not report it as the final upgraded Full result:

```text
$VERIPUT_ROOT/Results/RQ1/VeriPUT/
  bugfix124/artifacts/<subject>/<entry>/
  peer182/artifacts/<subject>/<entry>/
  real203/artifacts/<subject>/<entry>/
```

Current audited counts:

| Metric | Count |
|---|---:|
| target contracts | 509 |
| targets with at least one retained valid test | 494 |
| targets without retained valid tests | 15 |
| valid test units | 1460 |
| PUT test units | 1446 |
| no-PUT concrete replay test units | 14 |

Dataset split:

| Dataset | Valid test units |
|---|---:|
| `bugfix124` | 316 |
| `peer182` | 591 |
| `real203` | 553 |

Physical-file rule:

- PUT entries use `test_final.t.sol`.  Each current `test_final.t.sol` contains
  exactly one `test_put_*` function and no extra concrete/anchor test function.
- no-PUT entries use `test.t.sol`.
- The sibling `test.t.sol` in a PUT entry is retained concrete-basis material
  for RQ3 derivation/audit.  It is not counted as an additional Full VeriPUT
  test unit.

Audit command:

```bash
cd "$VERIPUT_ROOT"
python3 Scripts/VeriPUT/audit_evaluation_corpus.py \
  --corpus Results/RQ1/VeriPUT
```

Expected result: `status=ok`, `test_units=1460`, `put_test_units=1446`,
`no_put_test_units=14`, `targets=509`.

## Artifact Roots And Publish Rules

Use these roots deliberately:

| Root | Meaning |
|---|---|
| `$VERIPUT_ROOT/Results/RQ1/VeriPUT_legacy` | archived legacy RQ1 VeriPUT artifacts; keep for audit only |
| `$VERIPUT_ROOT/Results/RQ2/VeriPUT_legacy` | archived legacy RQ2 VeriPUT artifacts; keep for audit only |
| `$VERIPUT_ROOT/Results/RQ3/VeriPUT_legacy` | archived legacy RQ3 VeriPUT artifacts; keep for audit only |
| `$VERIPUT_ROOT/Results/RQ1_KInduction_Fair600/<tag>` | new Full VeriPUT 600s rerun outputs before publication |
| `$VERIPUT_ROOT/Results/RQ3/No_selection_strategy/<tag-or-window>` | no-selection rerun outputs before merge |
| `$VERIPUT_ROOT/Results/RQ3/No_region_refinement` | derivation-only output from a new audited Full corpus |
| `$VERIPUT_ROOT/Results/RQ3/No_test_oracle_refinement` | derivation-only output from a new audited Full corpus |
| `$VERIPUT_ROOT/Results/RQ3/VeriExploit/No_Cer_Reg` | derivation-only output from a new audited Full corpus |

Publication rule:

- Treat `$VERIPUT_ROOT/Results/RQ1/VeriPUT` as a clean audited corpus root,
  not as a scratch directory.
- New Full reruns should land under `Results/RQ1_KInduction_Fair600/<tag>`.
  After audit, extract the physical publication corpus into
  `Results/RQ1/VeriPUT/{bugfix124,peer182,real203}/artifacts/...`.
- Rebuild summaries from physical files and strict per-test evidence.  Do not
  merge stale aggregate counters from legacy `results.jsonl`, `manifest.json`,
  or old retry directories.
- Final `.t.sol` output rule: one retained publication test unit per file.
  PUT files contain one `test_put_*`; no-PUT retained concrete files contain
  one concrete replay test.  PUT files may keep retained concrete-basis
  material only as separate audit input, not as an extra test unit in the same
  final file.

## Code Layout

Keep tool code and experiment adapters separate:

| Path | Role |
|---|---|
| `$VERIPUT_ROOT/Tools/VeriPUT/` | Standalone VeriPUT tool scripts copied out of the ESBMC repo |
| `$VERIPUT_ROOT/Scripts/VeriPUT/` | Experiment runners for RQ1 coverage and RQ2 |
| `$ESBMC_REPO/notes/coverage/scripts/rq1_veriput_run.py` | Full VeriPUT/RQ3 generation runner |
| `$ESBMC_REPO/notes/coverage/scripts/rq3_derive_from_full.py` | Derive RQ3 no-region/no-test-oracle outputs from Full |
| `$ESBMC_REPO/scripts/solidity_path_generalise.py` | Stage 2/3 certification/generalization driver |
| `$ESBMC_REPO/scripts/solidity_path_put.py` | Stage 4 Foundry test constructor |

Standalone tool smoke:

```bash
python3 "$VERIPUT_ROOT/Tools/VeriPUT/test_standalone.py"
```

Current result: passed.

## Full VeriPUT Generation

Use environment variables, not hard-coded machine paths:

```bash
export VERIPUT_ROOT=/path/to/VeriPUT
export ESBMC_REPO=/path/to/esbmc
export ESBMC="$ESBMC_REPO/build-release-static/src/esbmc/esbmc"
export AST_CACHE_ROOT="${TMPDIR:-/tmp}/veriput_rq1_ast_cache"

python3 "$ESBMC_REPO/notes/coverage/scripts/rq1_veriput_run.py" \
  --veriput-root "$VERIPUT_ROOT" \
  --benchmark bugfix124 \
  --result-root "$VERIPUT_ROOT/Results/RQ1_KInduction_Fair600/<run-tag>" \
  --ast-cache-root "$AST_CACHE_ROOT" \
  --stage4-driver "$VERIPUT_ROOT/Tools/VeriPUT/put_all.py" \
  --esbmc "$ESBMC" \
  --timeout 600 \
  --esbmc-run-timeout 600 \
  --memlimit-gib 12 \
  --jobs <N> \
  --strict-case-wall-budget \
  --redo
```

Run one benchmark at a time with `--benchmark bugfix124`, `peer182`, or
`real203`.  Do not write a strict 600s rerun directly into
`Results/RQ1/VeriPUT`; publish/extract only after auditing.

Pre-run gates for Full:

- `"$ESBMC" --version` must report the expected local build.
- `python3 "$VERIPUT_ROOT/Tools/VeriPUT/test_standalone.py"` must pass.
- `python3 "$ESBMC_REPO/scripts/test_solidity_path_put.py"` must pass.
- `python3 "$ESBMC_REPO/scripts/test_put_all_accounting.py"` must pass.
- `python3 "$ESBMC_REPO/notes/coverage/scripts/rq3_compare_smoke.py"` must pass
  on matched Full/no-selection/no-region/no-test-oracle/no-cer-reg smoke roots
  before interpreting ablation trends.

## RQ3 Ablations

Two ablations are derived from Full VeriPUT and should not rerun ESBMC:

```bash
python3 "$ESBMC_REPO/notes/coverage/scripts/rq3_derive_from_full.py" \
  --full-root "$VERIPUT_ROOT/Results/RQ1/VeriPUT" \
  --out-root "$VERIPUT_ROOT/Results/RQ3/No_region_refinement" \
  --mode no-region-refinement \
  --forge-timeout 600

python3 "$ESBMC_REPO/notes/coverage/scripts/rq3_derive_from_full.py" \
  --full-root "$VERIPUT_ROOT/Results/RQ1/VeriPUT" \
  --out-root "$VERIPUT_ROOT/Results/RQ3/VeriExploit/No_Cer_Reg" \
  --mode no-cer-reg \
  --forge-timeout 600

python3 "$ESBMC_REPO/notes/coverage/scripts/rq3_derive_from_full.py" \
  --full-root "$VERIPUT_ROOT/Results/RQ1/VeriPUT" \
  --out-root "$VERIPUT_ROOT/Results/RQ3/No_test_oracle_refinement" \
  --mode no-test-oracle-refinement \
  --forge-timeout 600
```

Semantics:

- `no-region-refinement`: for Full rows whose region was obtained by
  refinement, replace the PUT with the retained concrete basis.  This requires
  the Full corpus to retain the concrete basis even when the final PUT exists.
- `no-test-oracle-refinement`: for PUTs whose R1/R2 assertions were introduced
  by oracle refinement, remove only those R1/R2 assertion blocks.  R0/exit
  assertions stay.

The runner enforces this: `rq1_veriput_run.py` rejects no-region and no-test
oracle refinement flags and tells users to use `rq3_derive_from_full.py`.

The only ablation that reruns VeriPUT is `no-selection-strategy`.

## No-Selection Strategy Rerun

This is the command to give collaborators.  It disables ESBMC path coverage
selection/degradation by adding `--path-cov-no-selection-strategy` to Stage 2.
It should usually produce worse results than Full because the decision space is
larger and region certification/refinement becomes harder.

```bash
export VERIPUT_ROOT=/path/to/VeriPUT
export ESBMC_REPO=/path/to/esbmc
export ESBMC="$ESBMC_REPO/build-release-static/src/esbmc/esbmc"
export AST_CACHE_ROOT="${TMPDIR:-/tmp}/veriput_rq3_no_selection_ast_cache"

python3 "$ESBMC_REPO/notes/coverage/scripts/rq1_veriput_run.py" \
  --veriput-root "$VERIPUT_ROOT" \
  --benchmark bugfix124 \
  --result-root "$VERIPUT_ROOT/Results/RQ3/No_selection_strategy" \
  --ast-cache-root "$AST_CACHE_ROOT" \
  --stage4-driver "$VERIPUT_ROOT/Tools/VeriPUT/put_all.py" \
  --esbmc "$ESBMC" \
  --timeout 600 \
  --esbmc-run-timeout 600 \
  --memlimit-gib 12 \
  --jobs <N> \
  --strict-case-wall-budget \
  --rq3-ablation no-selection-strategy \
  --no-selection-strategy \
  --redo
```

For multi-host runs, split by explicit target lists or `--active-window`; do not
let multiple hosts write the same subject directory at the same time.  Keep each
host's AST cache under local `$TMPDIR`, not under `$VERIPUT_ROOT/Results`.

Recommended multi-host layout after smoke checks:

| Host | Arm | Initial concurrency | Notes |
|---|---|---:|---|
| local | Full VeriPUT | 4--6 | 12 CPU / about 42 GiB RAM. Use Release ESBMC; monitor memory before raising jobs. |
| w2 | No Selection Strategy | 3 | 16 CPU / about 21 GiB RAM plus swap. Keep all writes on Linux disk, never under `/mnt/c`. |
| w1 | No Selection Strategy | 1 | 8 CPU / about 11 GiB RAM plus swap. Low-memory host; build Release locally before running. |

Remote handoff protocol:

1. `rsync` only the code/tool package and benchmark inputs needed by the arm.
   Do not copy historical `Results/RQ1/VeriPUT` retry workdirs to remotes.
2. Build ESBMC in Release mode on each host:
   `cmake -S "$ESBMC_REPO" -B "$ESBMC_REPO/build-release-static" -DCMAKE_BUILD_TYPE=Release ...`
   followed by `cmake --build "$ESBMC_REPO/build-release-static" -j<N>`.
3. Run the dry-run command below and then two real smoke subjects per host.
4. Inspect at least one generated `.t.sol`, `put.json`, and `put-summary.json`
   per host; do not rely only on aggregate JSON.
5. Only then start the assigned benchmark/window.  The output root must be
   host-specific or window-specific until final merge.

### w1/w2 setup notes

Use these host-local roots unless the machine has been intentionally
reconfigured:

| Host | `VERIPUT_ROOT` | `ESBMC_REPO` | Disk rule |
|---|---|---|---|
| w1 | `/root/VeriPUT` | `/root/InvMut/invmut/esbmc` or `/root/workspace/ESBMC_Commit/esbmc` | Keep AST cache and results on the Linux filesystem. |
| w2 | `/home/administrator/VeriPUT` | `/home/administrator/veriput_esbmc/repo` | Do not write to `/mnt/c`; it previously filled the Windows C drive. |

Each host should define the same variables before running:

```bash
export VERIPUT_ROOT=<host-local-VeriPUT>
export ESBMC_REPO=<host-local-esbmc>
export ESBMC="$ESBMC_REPO/build-release-static/src/esbmc/esbmc"
export AST_CACHE_ROOT="${TMPDIR:-/tmp}/veriput_rq3_no_selection_ast_cache"
export VERIPUT_FORGE_STD="$HOME/.cache/yarn/v6/npm-forge-std-1.11.0/node_modules/forge-std"
```

Release build template:

```bash
cmake -S "$ESBMC_REPO" -B "$ESBMC_REPO/build-release-static" \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_BITWUZLA=ON \
  -DENABLE_CVC5=ON \
  -DDOWNLOAD_DEPENDENCIES=ON
cmake --build "$ESBMC_REPO/build-release-static" -j$(nproc)
"$ESBMC" --version
```

If dependency downloads are slow on a remote, reuse an already-built Release
tree or sync the local build/toolchain cache; do not fill small system
partitions while rebuilding.

Concurrency rule:

- Start w2 at `--jobs 3`; raise to 4 only if free memory stays above 5 GiB and
  there is no sustained swap pressure.
- Start w1 at `--jobs 1`; raise to 2 only for very small subjects and only if
  free memory stays above 3 GiB.
- Keep local Full at `--jobs 4` initially; raise to 6 only after several
  600-second cases complete without memory pressure.
- When a host is running multiple shell workers manually, make sure their
  target windows are disjoint.  Never let two workers write the same
  `<benchmark>/subjects/<subject_id>` directory.
- Poll every 300 seconds during long campaigns:

```bash
free -h
pgrep -af 'rq1_veriput_run.py|put_all.py|solidity_path_put.py|esbmc|forge test' | head -50
find "$OUT_ROOT" -name result.json | wc -l
find "$OUT_ROOT" -name put-summary.json | wc -l
```

No-selection output root convention:

```bash
export OUT_ROOT="$VERIPUT_ROOT/Results/RQ3/No_selection_strategy/<host>-<benchmark>-<window>"
```

### How to configure concurrency

Use `--jobs` for the runner-level parallelism.  One job means one subject/case
worker.  Each worker can temporarily spawn ESBMC and Forge subprocesses, so
`--jobs` must be chosen by memory, not only CPU count.

Practical starting values:

```text
local Full:        --jobs 4   (raise to 6 only after memory is stable)
w2 no-selection:  --jobs 3   (raise to 4 only if free memory stays >5 GiB)
w1 no-selection:  --jobs 1   (raise to 2 only for small windows)
```

During long runs, check every 300 seconds:

```bash
free -h
ps -eo pid,ppid,pgid,stat,%mem,%cpu,etime,cmd --sort=-%mem | \
  rg 'rq1_veriput_run.py|put_all.py|solidity_path_put.py|esbmc|forge test' | head -30
find "$OUT_ROOT" -name result.json | wc -l
find "$OUT_ROOT" -name put-summary.json | wc -l
```

Adjust `--jobs` only at window boundaries.  Do not edit a running root in place
with a second runner; start a new disjoint `OUT_ROOT` for the next window.

Example w2 command after smoke passes:

```bash
python3 "$ESBMC_REPO/notes/coverage/scripts/rq1_veriput_run.py" \
  --veriput-root "$VERIPUT_ROOT" \
  --benchmark real203 \
  --result-root "$OUT_ROOT" \
  --ast-cache-root "$AST_CACHE_ROOT" \
  --stage4-driver "$VERIPUT_ROOT/Tools/VeriPUT/put_all.py" \
  --esbmc "$ESBMC" \
  --timeout 600 \
  --esbmc-run-timeout 600 \
  --memlimit-gib 12 \
  --jobs 3 \
  --strict-case-wall-budget \
  --rq3-ablation no-selection-strategy \
  --no-selection-strategy \
  --redo
```

Example w1 command after smoke passes:

```bash
python3 "$ESBMC_REPO/notes/coverage/scripts/rq1_veriput_run.py" \
  --veriput-root "$VERIPUT_ROOT" \
  --benchmark peer182 \
  --result-root "$OUT_ROOT" \
  --ast-cache-root "$AST_CACHE_ROOT" \
  --stage4-driver "$VERIPUT_ROOT/Tools/VeriPUT/put_all.py" \
  --esbmc "$ESBMC" \
  --timeout 600 \
  --esbmc-run-timeout 600 \
  --memlimit-gib 12 \
  --jobs 1 \
  --strict-case-wall-budget \
  --rq3-ablation no-selection-strategy \
  --no-selection-strategy \
  --redo
```

Dry-run check:

```bash
python3 "$ESBMC_REPO/notes/coverage/scripts/rq1_veriput_run.py" \
  --veriput-root "$VERIPUT_ROOT" \
  --benchmark bugfix124 \
  --result-root "$VERIPUT_ROOT/Results/RQ3/No_selection_strategy" \
  --ast-cache-root "$AST_CACHE_ROOT" \
  --stage4-driver "$VERIPUT_ROOT/Tools/VeriPUT/put_all.py" \
  --esbmc "$ESBMC" \
  --timeout 600 \
  --esbmc-run-timeout 600 \
  --memlimit-gib 12 \
  --jobs 1 \
  --limit 1 \
  --strict-case-wall-budget \
  --rq3-ablation no-selection-strategy \
  --no-selection-strategy \
  --dry-run
```

Expected dry-run fields: `rq3_ablation=no-selection-strategy`,
`no_selection_strategy=true`, `strict_case_wall_budget=true`, `wall_cap_s=600`.

## RQ1 Coverage Evaluation

Source line/branch/function coverage uses the clean VeriPUT corpus and the
frozen 509 target manifest:

```bash
cd "$VERIPUT_ROOT"
python3 Scripts/VeriPUT/run_source_coverage.py \
  --benchmark Patch-Bug-Bench \
  --execute \
  --fuzz-runs 10000 \
  --timeout 120
```

Benchmark names for coverage runners:

- `Patch-Bug-Bench`
- `Peer-Reviewed-Contracts`
- `Stress-Projects`

Path coverage requires the patched Foundry binary used by the other baselines:

```bash
cd "$VERIPUT_ROOT"
python3 Scripts/VeriPUT/run_path_coverage.py \
  --benchmark Patch-Bug-Bench \
  --forge "$PATH_TO_PATCHED_FORGE" \
  --execute \
  --fuzz-runs 10000 \
  --timeout 120
```

Both runners materialize per-subject bundles from
`Results/RQ1/VeriPUT/*/artifacts/...`; they do not run tests directly from the
entry root.

## RQ2 Evaluation

RQ2 runs the same retained bundles against mutants or real bug variants.  A test
detects a bug/mutant when it fails for a reason other than benchmark setup
failure: compile mismatch, natural revert, or assertion failure are detections.

```bash
cd "$VERIPUT_ROOT"
python3 Scripts/VeriPUT/run_rq2.py \
  --bench BugFix124 \
  --fuzz-runs 10000 \
  --timeout 600 \
  --tag veriput
```

Use bench names `BugFix124`, `Peer182`, and `Stress243`.

## Current Validation

Completed local checks:

- `Tools/VeriPUT/test_standalone.py`: passed.
- `Scripts/VeriPUT/audit_evaluation_corpus.py --corpus Results/RQ1/VeriPUT`:
  passed with 1460/1446/14.
- `Scripts/VeriPUT/test_run_coverage_manifests.py`: passed.
- `Scripts/VeriPUT/test_run_rq2.py`: passed.
- `Scripts/VeriPUT/test_source_coverage_probe.py`: passed.
- `Scripts/VeriPUT/test_bundle.py`: passed.
- `Scripts/VeriPUT/test_path_coverage_subject.py`: passed.
- `notes/coverage/scripts/test_rq3_derive_from_full.py`: passed.
- `scripts/test_rq1_veriput_run.py`: passed, 109 tests.
- `scripts/test_solidity_path_put.py`: passed, 505/505 tests.
- `scripts/test_rq1_concrete_replay_store.py`: passed.
- fixed replay Foundry smoke: one test combining an exact return value, exact
  event emitter/topics/data, and a `vm.load` post-state assertion passed 1/1.
- `cmake --build build -j$(nproc)`: passed within 5 minutes during development.
- `build/src/esbmc/esbmc --help` contains `--path-cov-no-selection-strategy`
  in the development build.  Campaign runs must use the Release binary
  `$ESBMC_REPO/build-release-static/src/esbmc/esbmc`.

Smoke source coverage:

- BugFix: `acfix_002_Templedao`, 2 traps, both `rc=0`.
- Peer: `peer_ccsolbmc__AIRBets`, 2 traps, both `rc=0`.
- Stress: `ERC-3643__ERC-3643__AgentRole`, 2 traps, both `rc=0`.

Known non-blocker:

- `Scripts/VeriPUT/test_no_cer_reg_corpus.py` currently expects
  `Results/RQ3/No_Cer_Reg/rq3-valid-concrete-2140.frozen.json`; after archival
  the file is under `Results/RQ3/VeriPUT_legacy/No_Cer_Reg/`.  This is an RQ3
  legacy snapshot path issue, not a Full VeriPUT corpus issue.

## Fixed Replay Assertion Upgrade

The current generator now materializes witness-specific observations before
falling back to a bare exit assertion:

- scalar and tuple ABI returns are bound and asserted component by component;
- scalar storage values are read through the solc storage layout, including
  packed fields and supported mapping keys, and compared with the retained
  Stage-2 final state;
- retained events are checked as an exact ordered log sequence, including
  emitter, topics, and data;
- when none of those observations can be rendered, the existing R0
  normal/revert exit assertion remains the fallback.

Implementation and regression coverage:

```text
scripts/solidity_path_put.py
notes/coverage/scripts/rq1_anchor_events.py
notes/coverage/scripts/rq1_concrete_replay_store.py
scripts/test_solidity_path_put.py
scripts/test_rq1_concrete_replay_store.py
```

The frozen legacy No_Cer_Reg corpus is not valid as the final result for this
definition.  Its 2140 tests contain only call-status/normal-exit assertions
(1610/530) and no concrete return, state, or event value assertions.  Full
VeriPUT and No_Cer_Reg therefore need to be rematerialized with the upgraded
generator.  No_Cer_Reg may be derived from the Full run's retained concrete
basis, but it must preserve the fixed witness assertions while removing the
certified-region generalization.

R2 remains separate from this upgrade.  Its initial endpoints come from typed
certified coordinates, source literals, pre-state values, and bounded
depth-one arithmetic terms; witness values are fixed replay evidence, not R2
bounds over a certified region.

## Tool Readiness Before the Next Full Run

Do not start the final 509-target campaign yet.  The current implementation
state is:

| Arm | Status | Remaining work |
|---|---|---|
| Full VeriPUT | **core generation works; official R2.2 smoke pending** | Fixed witness fusion, retained bases, R2.2 refinement, R2 subfamily accounting, and R1/R2.1/R2.3 input-part splitting are implemented. A real inherited-getter smoke emits only `test_put_*` functions, fuses fixed replay return assertions into the PUT, and passes 10,000-run fixed-seed Forge replay. A real `transferOwnership` smoke fuses fixed event/state assertions and split-child fixed replay assertions into separate PUT files, all Forge-green. Remaining P0 is a small official RQ1 live R2.2 counterexample-refinement smoke and a strict Full-versus-ablation comparison. |
| No Region Refinement | **derivation implemented, blocked on Full** | `notes/coverage/scripts/rq3_derive_from_full.py` replaces a refined PUT with its authenticated retained concrete basis.  Its tests pass, but the final arm depends on a new Full run containing the upgraded bases. |
| No Test Oracle Refinement | **derivation implemented; Full input pending** | The derivation script removes only `VERIPUT_ORACLE_REFINEMENT` blocks and preserves fixed replay/R0 assertions.  The packaged regression now confirms that the obsolete Namespace flag cannot create a weaker Stage-4 rerun.  The older `Scripts/VeriPUT/derive_rq3_no_test_oracle.py`, which strips assertions more broadly, must not be used for the final arm. |
| No Selection Strategy | **rerun wiring and discriminating smokes complete; full campaign pending** | The flag reaches all verifier layers. DepositLog and ArrayUtils degraded relative to Full; acfix002 transferOwnership is a wiring-only non-regression smoke. Run this arm over 509 targets only after the Full tool is frozen. |
| No_Cer_Reg | **derivation implemented; new Full input pending** | `rq3_derive_from_full.py --mode no-cer-reg` selects exact Forge-green retained bases and revalidates hashes/identity. The frozen 2140-test legacy corpus has only R0 and is not final. |

Current checks at this boundary:

```text
scripts/test_solidity_path_put.py                         PASS 505/505
scripts/test_put_all_accounting.py                       PASS (includes real Forge fusion)
scripts/test_rq1_concrete_replay_store.py                PASS
scripts/test_rq1_veriput_run.py                          PASS 109/109
notes/coverage/scripts/test_rq3_derive_from_full.py      PASS 4/4
Tools/VeriPUT/test_stage4_ablation_options.py            PASS
Tools/VeriPUT/test_standalone.py                         PASS
Tools/VeriPUT/put_all.py + sibling solidity_path_put.py   PASS standalone smoke
Tools/VeriPUT/SOURCE.json hash check                      PASS 13/13
```

`attach_certified_ce_anchor()` now retains its historical name but implements
the required fusion rather than creating a second test function.  It first
validates the exact CE hash, path/region identity, setup equality, source
binding, witness fingerprint, and structured oracle metadata.  Only after the
retained basis itself is Forge-green does `put_all.py` rewrite the Full PUT,
then rerun Forge on the modified file.  The result is one `test_put_*`
function containing generalized assertions plus a conditional fixed-witness
block such as:

```solidity
if (x == x_pi) {
    // exact return/state/event assertions observed for x_pi
}
```

The target call occurs exactly once.  Return values are bound at that call;
storage is read after it; event recording surrounds it.  R0 remains the
region-wide exit assertion and is not duplicated inside the witness block.

## Boundary-Observation R2 Extension

The active R2 proposer now has a conservative boundary-observation candidate
source in `scripts/solidity_path_put.py`.  The pre-existing candidates still
come from nameable coordinates, region endpoint literals, source constants,
pre-state values, simple AST assignments, and a bounded typed arithmetic
grammar.

The next extension is boundary-observation-guided R2 candidate generation:

1. choose points from the inclusive Stage-2 integer box, starting with the
   authenticated witness and coordinate-wise lower/upper substitutions rather
   than all exponential corners (currently capped at five points);
2. replay every point from the same fresh fixture and collect numeric return,
   and readable post-state observations (currently capped at four uint
   observables);
3. propose observed minima/maxima as R2 absolute/delta bounds;
4. send every proposal through the existing ESBMC region proof gate;
5. never emit a sampled-only claim.  Forge is only the candidate generator and
   refuter; ESBMC `HOLDS` under the existing k-induction recipe is required.

### Family-specific oracle refinement decision

The paper and implementation must use different refinement operations for the
three generalized candidate shapes:

- R2.1 is a direction relation between persistent post-state and pre-state,
  such as `post >= pre` or `post <= pre`.
- R2.2 is only a boundary-observation bound: its endpoints are the minimum and
  maximum values observed by concretely replaying the authenticated witness
  and selected boundary inputs.  An arbitrary interval candidate is not R2.2.
- R2.3 contains source/typed relations constructed from coordinates, certified
  region endpoints, pre-state terms, declared constants, source literals, and
  the bounded arithmetic grammar.  Thus `post <= amount` and a bound built
  directly from input-region endpoints are R2.3, not R2.2.
- R1, R2.1 direction relations, and R2.3 source-derived relations split the
  certified input part at a verifier counterexample and recheck the same
  assertion on the resulting parts.  Both children remain covered by the
  certified path region; a child that cannot prove the assertion retains R0
  and every assertion proved before the split.
- R2.2 boundary-observation bounds keep the input part fixed.  If ESBMC
  refutes `[m, M]` with observable value `v`, update the candidate to
  `[min(m, v), max(M, v)]` and recheck it over the same part.  Stop on proof,
  undecided, budget exhaustion, or a type-wide/uninformative bound.
- Splitting an R2.2 input part changes its boundary points.  Such a child must
  be replayed at its new boundary points and receives a newly generated bound;
  this is `partition-and-regenerate`, not refinement of the old bound, and is
  not the default strategy.

Current implementation status at this handoff boundary:

- R2.2 is connected end to end.  `boundary_observation_r2_spec()` emits only
  the initial observed bound.  A refuted query is associated with its exact
  failed claim in `assert/cov-report.json`; the observed counterexample value
  then expands the lower or upper bound for the next query.  Refined queries
  have distinct files and retain the original certified input part.
- Every emitted/proved R2 row is classified as R2.1, R2.2, or R2.3.  The
  classification is retained in `r2_verifier_row_accounting`, oracle detail,
  and `r2_subfamily_counts`, so the three families can be reported separately.
- Oracle input-part partitioning for R1/R2.1/R2.3 is connected to physical
  emission and accounting.  A refuted candidate may split the certified input
  part, and each final child part is emitted as its own `test_put_*_part_*`
  file only after an authenticated representative replay is available.
- The remaining validation gap is specifically R2.2 on an official RQ1 subject:
  we still need a small live case where the first observed bound is refuted,
  the bound expands from the verifier counterexample value on the same input
  part, and the final R2.2 assertion is proved by ESBMC and Forge-green.

The implementation reuses `build_put()` for fixture/environment construction,
then removes every fuzz parameter and replaces it with a scalar value from the
authenticated CE plus the selected endpoint.  Missing values, complex types,
rollback/revert paths, or insufficient time beyond the reserved ESBMC proof
budget cause a fail-closed skip.  Boundary collection is capped at 60 seconds
inside the 600-second generation budget; the final fixed-seed 10,000-run Forge
validation remains outside that budget.  Raw probe evidence is written as
`boundary-observations.{t.sol,json,stderr}` in the Stage-4 workdir.

Validation completed on 2026-08-18:

- Python regression: `scripts/test_solidity_path_put.py` passes 505/505;
- Stage-4 accounting, RQ3 derivation, and the RQ1 runner pass; the latter is
  109/109.  `py_compile` and `pylint --errors-only` are clean;
- an isolated real Foundry fixture for `f(uint256 x) { y=x/2; return y; }`
  observed witness/lower/upper points `x={50,10,100}`;
- six deterministic probes recovered both state and return values
  `{25,5,50}` and proposed `post(y) in [5,50]` plus
  `return in [5,50]`;
- the initial observation smoke validates proposal generation.  Ordinary
  Stage 4 still requires ESBMC `HOLDS`; sampled values alone never emit an
  assertion.

The standalone tool runner accepts `ESBMC_REPO`, `VERIPUT_FORGE_STD`, and
`VERIPUT_PUT_DRIVER`.
Defaults are repo-relative; do not add machine-specific absolute paths to the
tool package.

The old fixed geometric widening ladder has been removed. The active R2.2
implementation reads the exact failed claim's observable value and expands the
candidate bound from that value on the unchanged input part. It never emits the
full uint256 type interval as an informative oracle and never treats sampled or
Forge-green behavior as proof.

This extension is expected primarily to move tests from
`valid-PUT-no-R1R2` to `valid-PUT-with-R1R2`.  It may increase total PUT count
only where the missing behavioral oracle currently blocks materialization.
Measure it first on a 20--30 case `PUT-no-R1R2` sample before enabling it for
the 509-target run.

## Audit Rules

- Report physical retained test units, not every `**/*.t.sol` under historical
  workdirs.
- Do not count PUT entry `test.t.sol` as an extra Full test; it is retained
  concrete-basis evidence for derivation/audit.
- Do not use old obligation-ledger numbers as the paper denominator.
- Do not trust stale `entry_dir` paths inside copied legacy `entry.json`; the
  authoritative file location is the current entry directory containing that
  `entry.json`.
- After any corpus change, rerun `Scripts/VeriPUT/audit_evaluation_corpus.py`
  before running RQ1 coverage or RQ2.

## Latest Full Smoke Status

Latest checked Full smoke:

```sh
VERIPUT_ROOT="$VERIPUT_REPO" \
python3 notes/coverage/scripts/rq1_veriput_run.py \
  --veriput-root "$VERIPUT_REPO" \
  --benchmark real203 \
  --subject-id balancer__balancer-v3-monorepo__DynamicWeightedLPOracle \
  --unit decimals \
  --result-root "$VERIPUT_REPO/Results/RQ1_KInduction_Fair600/smoke-full-dynamic-decimals-observed-return6" \
  --ast-cache-root /tmp/veriput_smoke_ast_cache \
  --stage4-driver notes/coverage/scripts/put_all.py \
  --esbmc build-release-static/src/esbmc/esbmc \
  --timeout 600 --esbmc-run-timeout 600 --memlimit-gib 12 \
  --jobs 1 --strict-case-wall-budget --redo
```

Result: PASS.  `result.json.row` reports `raw=2`, `valid=2`,
`put_valid=2`, `concrete_valid=0`, `valid_put_with_R1_or_R2=0`,
`wall_total_s=40.209`.  The two generated physical files each contain exactly
one `test_put_*` and no `test_structural_anchor_*` or
`test_concrete_replay_*`.  The normal path includes the Foundry-observed fixed
return assertion `uint8(18)`, not the stale ESBMC model value `0`.  A manual
fixed-seed check on `test_put_DynamicWeightedLPOracle_decimals_path3p1` passed
with `--fuzz-runs 10000 --fuzz-seed 0x56657269505554`.

Latest event/state fixed-replay Full smoke:

```sh
VERIPUT_ROOT="$VERIPUT_REPO" \
python3 notes/coverage/scripts/rq1_veriput_run.py \
  --veriput-root "$VERIPUT_REPO" \
  --benchmark bugfix124 \
  --subject-id acfix_002_Templedao \
  --unit transferOwnership \
  --result-root "$VERIPUT_REPO/Results/RQ1_KInduction_Fair600/smoke-full-acfix002-transferownership-fixed-fusion6" \
  --ast-cache-root "${TMPDIR:-/tmp}/veriput_smoke_multipart_ast_cache" \
  --stage4-driver notes/coverage/scripts/put_all.py \
  --esbmc build-release-static/src/esbmc/esbmc \
  --timeout 600 --esbmc-run-timeout 600 --memlimit-gib 12 \
  --jobs 1 --strict-case-wall-budget --redo
```

Result: PASS.  `result.json.row` reports `raw=1`, `valid=1`,
`put_valid=1`, `concrete_valid=0`, `valid_put_with_R1_or_R2=1`,
`wall_total_s=77.775`.  The retained PUT file
`StaxLPStakingCovTest_0_StaxLPStaking_transferOwnership_put15p1.t.sol`
contains exactly one `test_put_*` function and no `test_cov_*`,
`test_concrete_replay_*`, or `test_structural_anchor_*` function.  Its fixed
replay block records the exact `OwnershipTransferred(address,address)` log and
post-state storage values inside the same `test_put_*` body.  A manual
fixed-seed check on
`test_put_StaxLPStaking_transferOwnership_path15p1` passed with
`--fuzz-runs 10000 --fuzz-seed 0x56657269505554`.

Earlier standalone Stage-4 smoke:

```sh
VERIPUT_FORGE_STD="$HOME/.cache/yarn/v6/npm-forge-std-1.11.0/node_modules/forge-std" \
python3 "$VERIPUT_REPO/Tools/VeriPUT/put_all.py" \
  --cert "$VERIPUT_REPO/Results/RQ3/No_selection_strategy/_smoke-one-fusion3-20260818/bugfix124/subjects/acfix_fixlink_DepositLog/cert/certify-results.jsonl" \
  --only 'bugfix124__acfix_fixlink_DepositLog.sol:@C@DepositLog@F@setApprovedLogger#145' \
  --strong-recipe --timeout 70 --forge-timeout 180 --memlimit-gib 12 \
  --out-root /tmp/veriput-standalone-fusion-1787055793 \
  --retain-certified-concrete-replays \
  --esbmc "$ESBMC_REPO/build-release-static/src/esbmc/esbmc"
```

Result: PASS.  `put-summary.json` reports 3 reference-valid generated tests:
2 PUT and 1 concrete replay.  Both PUTs are R1/R2-backed and Forge-green; the
concrete replay is also Forge-green.  The emitted PUTs now establish exact
entry-state and environment values before the target call; the retained
concrete replay carries fixed witness state assertions.  This closes the
previous `c0` versus `address(c0)` storage-cheatcode compile failure.  This
run intentionally does not set `VERIPUT_PUT_DRIVER`; it proves the packaged
`Tools/VeriPUT/put_all.py` uses its sibling `solidity_path_put.py`.

Regression status after the same patch:

```text
python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py  PASS
python3 scripts/test_solidity_path_put.py                                            PASS 505/505
```

Runner smoke after the Stage-2 scratch repair:

```text
Full DepositLog, 180s:
  root: Results/RQ1/VeriPUT/_smoke-full-depositlog-1787055982
  result: status=ok, raw=29, valid=13, PUT=0/15, concrete=13/14,
          wall_total_s=151.968
No Selection DepositLog, 180s:
  root: Results/RQ3/No_selection_strategy/_smoke-noselect-depositlog-1787055982
  result: status=no-output, raw=0, valid=0, PUT=0/0, concrete=0/0,
          wall_total_s=47.019
  evidence: driver logs and run-config include --path-cov-no-selection-strategy.

Full acfix_002_Templedao, 600s:
  root: Results/RQ1_KInduction_Fair600/_smoke-full-acfix002-persistence-1787057458
  result: status=ok, raw=20, valid=10, PUT=10/19, concrete=0/1,
          quality=valid-PUT-with-R1R2, wall_total_s=558.074
  replay persistence: complete=true, strict_valid=true,
                      put_basis_missing_count=0
No Selection acfix_002_Templedao:
  root: Results/RQ3/No_selection_strategy/_smoke-noselect-acfix002-1787056228
  result: status=no-output, valid=0, wall_total_s=444.46
  interpretation: on this PUT-producing target the no-selection arm also
                  degrades, rather than producing more tests than Full.

No Selection acfix_002_Templedao transferOwnership, latest fixed-fusion driver:
  Full root:
    Results/RQ1_KInduction_Fair600/smoke-full-acfix002-transferownership-fixed-fusion6
  No-selection root:
    Results/RQ3/No_selection_strategy/smoke-acfix002-transferownership-fixed-fusion6
  Full result:
    valid=1, PUT=1, R1/R2=1, wall_total_s=77.775
  No-selection result:
    valid=1, PUT=1, R1/R2=1, wall_total_s=77.975
  comparison:
    notes/coverage/scripts/rq3_compare_smoke.py passes; both arms report
    r2_3=3 and no R2.1/R2.2.  The no-selection root records
    --path-cov-no-selection-strategy in run-config/result/put metadata.
  interpretation:
    This is a wiring and non-regression smoke only.  The selected unit has too
    little path-combination pressure to show degradation, so it must not replace
    the Thicc and AddressArrayUtils degradation witnesses below.

Thicc.transferFrom paired smoke, Release ESBMC:
  Full root:
    Results/RQ1_KInduction_Fair600/_smoke-full-thicc-transferFrom-release-pair1
  No-selection root:
    Results/RQ3/No_selection_strategy/_smoke-noselect-thicc-transferFrom-release-pair1
  Full result:
    valid=1, PUT=1, R1/R2=0, wall_total_s=10.988
    Stage 2 first pass: 2049 claims, no witness, 6.044s.
    Stage 4: one Forge-green R0 PUT.
  No-selection result:
    valid=1, PUT=1, R1/R2=0, wall_total_s=453.694
    Stage 2: --path-cov-no-selection-strategy stayed in ESBMC enumeration
    until the 449s unit budget was killed.
    Stage 4: same Forge-green R0 PUT.
  Interpretation:
    This is a cost-degradation witness, not a PUT-count-degradation witness.
    The final .t.sol files are byte-identical and both pass fixed-seed
    10000-run Forge replay with seed 0x56657269505554.  The ablation is worse
    because it spends about 74x more Stage-2 time before reaching the same
    fallback.

AddressArrayUtilsContract.intersect paired smoke, Release ESBMC:
  Full root:
    Results/RQ1_KInduction_Fair600/smoke-full-arrayutils-intersect-fixed-fusion11
  No-selection root:
    Results/RQ3/No_selection_strategy/smoke-arrayutils-intersect-fixed-fusion11
  Full result:
    valid=2, PUT=2, R1/R2=0, wall_total_s=9.775
    Stage 2: 2 witnessed paths, 2 structural ABI-gate certified rows.
    Stage 4: 2 Forge-green PUTs.  Each retained `.t.sol` contains exactly one
    `test_put_*`, includes the fixed replay marker inside that function, and
    has no `test_cov`, `test_concrete_replay`, or `test_structural_anchor`.
    The final 10000-run Forge replay with seed 0x56657269505554 returns rc=0.
    Structural ABI-gate certificates no longer emit retained
    `basis_concrete` workdirs, because there is no exact solver CE basis to
    preserve for no-region derivation.
  No-selection result:
    valid=1, PUT=1, R1/R2=0, wall_total_s=451.85.
    Stage 2 stayed in ESBMC enumeration for almost the whole 449s unit budget
    before producing a single Forge-green structural ABI-gate PUT.
  Interpretation:
    This is the better smoke witness for RQ3 no-selection: removing selection
    turns a fast 2-PUT Full result into a much slower 1-PUT result under the
    same 600s case budget.  `rq3_compare_smoke.py` passes for this pair.
```

Run command note: `veriput_subjects.py` reads `VERIPUT_ROOT` at import time.
Set `VERIPUT_ROOT` in the environment as well as passing `--veriput-root`;
otherwise prepared Peer182/BugFix124 subjects may not be found even when the
CLI flag is correct.

The runner now writes Stage-2 certification scratch outside `Results`, under
`--ast-cache-root` (`rq1-stage2-cert` for Full and
`rq3-no-selection-strategy-cert` for the no-selection arm), then publishes the
durable cert bundle into the case result.  This avoids `certify_all.py`'s
guard against using `Results` as a mutable verifier scratch directory.

Important distinction: `/tmp/veriput-setapproved-env-fusion-5` is the old
failed smoke and must not be used as evidence.  It generated retained concrete
files with `vm.load(c0, ...)` / `vm.store(c0, ...)`, which Foundry rejects
because the cheatcodes require an `address`.

### Live oracle-part smoke (2026-08-19)

`transferOwnership` exposed and closed four pre-campaign defects:

1. Current ESBMC synthesized assertion failures print `FAILED: 'claim at'`;
   the old parser accepted only `FAILED: 'claim at file ...'`, so it silently
   discarded every refinement counterexample.
2. One state-exact child query returns all ladder rungs for that variable.  The
   driver now caches all returned verdicts instead of repeating the same ESBMC
   query once per rung.
3. Oracle input parts are emitted as separate physical `.t.sol` files.  Each
   file contains exactly one `test_put_*`; Forge gating and accounting are now
   per physical child rather than one row for a multi-function file.
4. Every child retains its complete verifier claim as well as its input CE.
   Its basis replay preserves the original emitted setup, renders the exact
   child calldata/environment/entry state, and derives fixed return/state/event
   assertions only from that same claim.  Missing or mismatched evidence still
   fails closed.

Evidence root: `/tmp/veriput_transfer_physical_split_v8`.  For
`transferOwnership` path 15, the certified region split into four physical
parts; all 4 are reference-valid PUTs with R1/R2.  Each final file contains one
test function and `VERIPUT_FIXED_REPLAY_ASSERTIONS`.  All four passed Foundry
with 10,000 fuzz runs and seed `0x56657269505554` (ASCII `VeriPUT`).  The Full
generation budget excludes this final fuzz validation.

Regression gate after these changes:

```text
python3 scripts/test_solidity_path_put.py  PASS 505/505
python3 -m py_compile scripts/solidity_path_put.py notes/coverage/scripts/put_all.py  PASS
```

---

# 2026-08-20 02:15 — state at hand-off, and the plan in flight

## The standing requirement

Full pipeline over all 509 targets within 12 hours (deadline ~14:00 on
2026-08-20), `no_selection_strategy` on w2, the other three arms derived.
Success is judged on three things only, all relative to v1:

1. as few `no-valid` cases as possible, and any that remain must be the
   contract's fault, not ours (a `no-valid` caused by picking the wrong target
   contract is OUR fault);
2. the PUT : concrete-replay conversion rate;
3. the R1/R2 share — a low share reads as "the method cannot synthesise".

Plus a hard requirement: the motivation example's two mutants must be killed.
And `no_selection_strategy` MUST come out worse than Full; if it does not, an
optimisation is leaking into the ablation arm.

## Order of work, and why this order

Motivation first, campaign second. The motivation example is the only open item
that can still force a CODE change, and a code change invalidates a finished
campaign. Freeze the code on a green kill matrix, then run the campaign once.

## What is already established (measured, not argued)

- `withdraw` enumerates 14 path targets at `--focus-function`, 15 at
  `--scope deposit,withdraw --max-tx 2`.
- At `--focus-function` only 2 targets are feasible and BOTH revert, so line 30
  — the line both mutants change — never executes. That is why the emitted PUT
  passes on base, M1 and M2 alike. It is a COVERAGE result, not an oracle-
  strength result.
- At `--scope deposit,withdraw --max-tx 2`: F=7, of which **3 are normal-exit**.
  Witnesses include `amount=1, deposits[0]=1` and `amount=49134` against a large
  balance. Line 30 executes on those.
- A hand-written ideal PUT confirms the kill is reachable with **R0 alone**:
  base passes 2000 fuzz runs on all three shapes; M1 and M2 both die with
  `panic: arithmetic underflow or overflow`. No stronger oracle is needed.
  (`R0` no-discount, `R0` with-discount, and an `R2.3` net relation all kill.)
- Stage 4 already has the normal-exit path: `normal_exit_asserted`
  (`scripts/solidity_path_put.py:15469`) plus the retention rule at `:20486`
  demanding exactly one `normal-exit / expected=True` completion oracle.

So enumeration works, Stage 4 works, and the oracle suffices. **Certification of
a normal-exit path is the only unproven link.**

## In flight right now

Three certification arms on `motivation_FeeVault.withdraw`, 2100s each,
`--strong-recipe`, 10 GiB, running concurrently:

| arm | `--scope` | `--max-tx` | out |
|---|---|---|---|
| A | `deposit,withdraw` | 2 | `cert_A.jsonl` |
| B | `deposit,setDiscount,withdraw` | 3 | `cert_B.jsonl` |
| C | `whole` | 2 | `cert_C.jsonl` |

Scratch root: `/tmp/claude-1000/-home-samson-workspace-VeriPUT/a79873f0-.../scratchpad/feevault`.
Drivers staged next to it: `mot_stage4_kill.sh <arm> <scope> <max-tx>` and
`mot_killmatrix.sh <arm>`.

A prior attempt at `--scope deposit,withdraw --max-tx 2` with 1800s and eight
coordinates (I had added `--slot-coord` and `--no-auto-pin-value` by hand)
CERTIFIED NOTHING — it spent the whole budget in the probe ladder and never
issued a single `--path-cov-certify` query. The three arms above drop those two
hand-added switches, which takes the coordinate count from 8 to 6.

## The Stage-2 coordinate bug: found, fixed, then REVERTED

`filter_unreferenced_state_coords` matched a coordinate to the AST dependency
closure by splitting on `$` alone, leaving any subscript attached. So
`state.deposits[msg.sender]` was looked up as `deposits[msg.sender]`, missed a
closure holding `deposits`, and was dropped as "outside the target closure" —
while `state.discountBps$23[msg.sender]` split to `discountBps` and was KEPT.
Whether a mapping-entry coordinate survives depends on whether ESBMC happened to
give it a lowering suffix. That is an accident, and it is real.

Correcting it (commit `ec64fab815`) was REVERTED in `c239b2c43b` because it buys
nothing and costs conversion:

- `P28_MapMin` (`notes/coverage/poc/P28_MapMin.sol`, one mapping, 4 paths): **no
  change at all**. The coordinate the filter drops is re-added by the
  counterexample harvest immediately afterwards — certified 2/4 either way,
  level-0 0.7s and refine ~10.6s either way.
- 35-subject stratified sample (`sample-v5` vs `sample-v6`, identical config):
  raw→valid **81.4% → 76.6%**, valid→PUT **91.3% → 85.0%**.
  `ETHRegistrarController` and `PoolPauseHelper` went from `certify ok` to
  `certify oom` at 12 GiB, and the first then fell to `zero-yield-getter-
  fallback` on all 7 remaining units — **a `no-valid` case manufactured by
  widening the coordinate set**, which is exactly requirement 1 above.

`state_coord_source_name` was kept: `_pin_source_name` split on the first dot
and cut inside `guesses[msg.sender].block`; the helper fixes that for free. The
filter's accident is now PINNED BY TESTS so it cannot be silently "fixed" again
without re-reading the measurement.

**Current code therefore equals the `sample-v5` configuration**, which is the
best measured baseline and the one the campaign should run on.

## What `sample-v6` also revealed, independent of the bug

PUT totals are dominated by `structural-abi-gate-no-coordinate` rows — the
Stage-2-timeout rescue that emits one ABI value-gate certificate per enumerated
path (cap 64). On `pop_042_VaultAdapter` a single unit's `certify timeout`
produced **33** of them; when that unit certified cleanly instead, the same
subject produced **3**, with the 9 real `cleared-concrete-fallback` rows
unchanged. Over 25 shared subjects: `esbmc-certify` 113 → 122,
`structural-abi-gate-no-coordinate` 175 → 138.

These rows assert only that a `nonpayable` entry rejects value — a property of
the compiler, not of the contract, with `ORACLE: none emitted -- NOT ONE RUNG
HOLDS` in the test itself. They emit nothing under `no_cer_reg`.

## Campaign, when the code is frozen

Local Full: `VeriPUT/run_full_campaign.sh` (TAG currently
`campaign-full-20260819`; set a fresh tag). 509 targets, 600s, `--jobs 8
--mem-fraction 3.0 --memlimit-gib 12`. It was started once at 02:06 and killed
at 02:09 to put motivation first — no usable output, remove the tree before
rerunning.

w2 (`invmut-w2`, 16 CPU / 21 GiB / 936 GiB on `/`; **`/mnt/c` has 9.6 GiB — never
write there**) is READY:

- `~/veriput_esbmc/repo/build-release-static/src/esbmc/esbmc` md5
  `ee97b4fd541f0a70f529f8c89fbcb842`, **identical to local**;
- `notes/coverage/scripts/`, the three `scripts/solidity_*.py`, and
  `~/VeriPUT/Tools/VeriPUT/` all rsynced from this tree;
- `~/VeriPUT/{bugfix124,peer182,real203}_subjects.txt` = 124/182/203 = 509;
- `~/VeriPUT/w2_all509.sh` staged: sharded (20/shard), deletes the AST cache
  after every shard, **hard-fails on a refused shard**, `--jobs 5
  --memlimit-gib 10 --mem-fraction 6.0`, `--rq3-ablation no-selection-strategy
  --no-selection-strategy`;
- `forge` at `~/.foundry/bin`, `solc` at `~/bin`. Both must be on PATH — the
  script exports them.

w2 has NOT been smoke-tested end to end yet. Do that on one subject before
launching all 509.

Then derive `no_cer_reg`, `no_region_refinement`, `no_test_oracle_refinement`
from the audited Full with `rq3_derive_from_full.py`.

## Still open, not addressed today

- The R2 rung/region provenance gap: a rung proved on one input part is adopted
  with another part's bounds (`acfix_026_CVE_2019_15080 transferOwnership`
  path 7, `owner: post > pre` HOLDS 4× / REFUTED 3×). Fix belongs in rung
  adoption.
- 66 body-level Forge failures unlocated; `green=False` was 19 in sample-v5.
- `fuzz_params` recorded as 0 for the FeeVault PUT despite two `bound()`
  parameters.
- `results_all.py --rq 1` still prints `--` for VeriPUT coverage because of a
  hardcoded `Results/RQ1/VeriPUT/campaign-timing/canonical-case-wall.json`.

## 02:40 — the metric that decided the stage-2 revert, and its reversal

The campaign is judged on **PUT : concrete replay**, not on valid → PUT. I had
been reading the second, reverted the stage-2 closure-filter correction on it,
and that was wrong. Same 35 subjects, same config, only the correction differs:

| | no-valid | PUT : concrete | R1/R2 share of valid PUTs |
|---|---|---|---|
| without the correction (`sample-v5`) | 1 | 232 : 26 = **89.9%** | 129/232 = **55.6%** |
| with it (`sample-v6`)                | 2 | 198 : 24 = **89.2%** | 137/198 = **69.2%** |

So the correction is **+13.6pt on the R1/R2 share** and flat on conversion. Its
only real cost was one case: `ETHRegistrarController` OOM'd at 12 GiB.

Reinstated in `49a18c2232` with `STATE_SLOT_COORD_BUDGET = 6`, which caps only
SUBSCRIPTED state coordinates (plain scalars are never capped), selects
shortest-first then lexicographically so two runs of one configuration cannot
disagree, and NAMES whatever it cuts in the dropped list. 507 + generalise tests
pass; `Tools/VeriPUT` re-synced.

`no-valid` on `sample-v5` was `ERC-3643__TREXFactory` — `put setIdFactory:
timeout`, i.e. the case budget, not the contract. That one is ours and is still
open.

The ablation is genuinely weaker by construction, not by tuning:
`--path-cov-no-selection-strategy` (`src/esbmc/options.cpp:917`) "disables
call-site selection/degradation ... every internal call remains expanded in the
path identity", so the same 600s budget buys strictly fewer certified paths.
First evidence: `acfix_002_Templedao` gives `raw=19 valid=15 put=15` under Full
and `raw=13 valid=13 put=13` under no-selection on w2.

w2 SMOKE PASSED end to end: `acfix_002_Templedao -> status=ok raw=13 valid=13
put=13/13 bucket=valid-PUT-with-R1R2 wall=570s`.

Metrics helper (the three numbers, on any result root):
`scratchpad/metrics.py <result-root> [label]`.

## 04:30 — campaign in flight, w2 lost, motivation diagnosed

**Local Full 509** started 03:39 (`campaign-full-v2`, `--jobs 6`). At 71 cases
the pace is ~1.5 cases/min, i.e. finishing about 09:15. Partial metrics at 51
cases, all better than the sample baselines:

| | Full (partial) | sample-v5 | sample-v6 |
|---|---|---|---|
| no-valid | **0** | 1 | 2 |
| PUT : concrete | **117 : 0 = 100%** | 89.9% | 89.2% |
| R1/R2 share of valid PUTs | **60.7%** | 55.6% | 69.2% |

**w2 is DOWN.** It ran shards 1-20 of bugfix124 (26 cases) at `--jobs 5`, which
extrapolated to 13.5 h and would have missed the deadline, so it was restarted
from shard 21 at `--jobs 9` (16 CPU, 21 GiB, load had been 5.0 — badly
underused). Minutes later the host stopped answering ping entirely, not just
ssh. It is a WSL2 guest on a Windows box; nothing here can wake it.

PLAN: let local Full finish first (~09:15), then run `no_selection` LOCALLY on
the whole box (`--jobs 10`), which at the observed local rate is ~4 h and lands
before 14:00. A watcher is polling w2 every 5 minutes; if it returns, run the
arm there instead and keep the local box free. Shards 1-20 of bugfix124 on w2
are complete and need not be redone (`~/VeriPUT/Results/RQ3/
No_selection_strategy/campaign-nosel-v2/`, logs `nosel-v2.log` and
`nosel-v2b.log`).

**Motivation — the cost model is now measured, and the lever is found.**
Level-0 cost is `coordinates x candidate values x paths`. Each factor was tested
separately:

| lever | result |
|---|---|
| more time (A2, 2400s) | level-0 alone took 996.6s; ZERO certify queries |
| fewer probes (L1/L2, `--probes 1..2`, `--probe-witnesses 2..4`) | "~12 candidate values per direction" UNCHANGED — that flag does not control it. Level-0 got *slower* (1494s) under contention |
| `--ce-materialize` (bypass Stage 2 entirely) | 0 candidates on all three journals: enumeration reports `--path-cov-probe was too expensive for this unit`, so no witness pool was ever written |
| **fewer coordinates (P1, `--pin`)** | **level-0 996.6s -> 35.7s**, free set 5-6 -> 1 |

P1 pins `state.discountBps$23[msg.sender]=0`, `block.number=0`,
`block.timestamp=0` on top of the strong recipe, everything else unchanged. The
no-discount path is sufficient: the hand-written ideal PUT kills BOTH mutants
through `test_R0_no_discount` alone. The pins narrow the slice to enc=127, 6,
62 — and **enc=127 is one of the three witnessed normal-exit paths**
(`amount=1, deposits[0]=1`, returns). Its `linear-refine` round then took
626.5s, so the remaining risk is that refine, not level-0.

If P1 times out in refine, the next arm is `--refine-rounds 1` plus
`--pin-extcall` (both `.call` success bits pinned at the witness, which is
exactly the successful-withdraw path) — attacking the third factor, path count,
which is the only one not yet reduced.

## 05:00 — the motivation example's real blocker: a pin that empties the region

Four arms failed before the cause was visible, and each one eliminated a
hypothesis rather than merely burning budget:

| arm | hypothesis | measured result |
|---|---|---|
| A2 (2400s) | not enough budget | level-0 ALONE took 996.6s; zero certify queries |
| L1/L2 | probe budget too large | "~12 candidate values per direction" UNCHANGED under `--probes 1..2` / `--probe-witnesses 2..4` — that flag does not control it |
| — | `--ce-materialize` can bypass Stage 2 | 0 candidates on all three journals; enumeration reports `--path-cov-probe was too expensive for this unit`, so no witness pool was ever written |
| P1 (`--pin`) | too many coordinates | **level-0 996.6s -> 35.7s**, free set 5-6 -> 1, and it REACHED certification |

Reaching certification is what exposed the actual cause. All three paths in the
pinned slice came back:

```
witness_check: VACUOUS
"the certification query witnessed NO execution admitted by it that walks this
 path, so every exit assert held for want of an execution"
```

enc=127's witness is `state.deposits[0] = 1`, and the pin set contained
`msg.value = 0`. `deposit()` is `deposits[msg.sender] += msg.value`, so with
msg.value pinned at 0 across the whole transaction sequence **no sequence can
establish a non-zero balance** — the region is empty, and every assertion in it
holds for want of an execution.

The pin has TWO sources and both had to go:

1. `--pin-agreed-establishable-env` pins env quantities all witnessed paths
   agree on, and the surviving paths all have `msg.value = 0` because `withdraw`
   itself takes no value. Dropping the flag was NOT enough.
2. **`--no-auto-pin-value` is off by default**, i.e. msg.value IS pinned to 0
   automatically on a unit the source declares non-payable
   (`solidity_path_generalise.py:7934`). The help says this default "is
   deliberately NOT the conservative one, because it is not a policy: a
   non-payable function's ABI gate reverts every call carrying value, so no
   input with msg.value != 0 reaches the body."

That reasoning is exactly right for the FINAL call and exactly wrong for the
SETUP transactions in a multi-transaction scope. `--scope deposit,withdraw`
needs `deposit{value: v}` with `v > 0`; the auto-pin forbids it.

**This is a method-level constraint, not a tuning detail**, and it belongs in
the write-up next to A8/B3: a pin is a statement about the whole bounded
transaction sequence, so it must be consistent with the ESTABLISHABILITY of the
entry state, and a per-call ABI fact does not generalise to the sequence.

The tool behaved correctly throughout — it reported VACUOUS rather than
counting an empty region as certified, and said in as many words that "an
undecided answer is not a discharged one". The defect was in how the arms were
configured, not in the verifier.

P4 therefore runs `--no-auto-pin-value` on top of P1's coordinate pins, with
bracketing on, `--refine-rounds 1` and `--pin-extcall`.

## Campaign watch

Two `no-valid` cases so far, both `TimelockController`
(`acfix_032_CVE_2021_39167`, `acfix_033_CVE_2021_39168`), both
`status=budget-exhausted`: `certify:timeout` x2 then
`zero-yield-getter-fallback:timeout` x4, nothing emitted inside 600s. The target
contract is the right one (it is the CVE's subject), so this is OUR budget, not
the contract being untestable. Tally these at the end.

## 2026-08-20 — motivation: three independent causes, all found by reading logs

Every earlier motivation arm (P1, P3, P4, P5) failed for reasons that had
nothing to do with the hypotheses I was testing (time, probes, coordinates).
The logs named all three:

1. **Pin collision.** I passed `--pin 'state.discountBps$23[msg.sender]=0'`.
   The generaliser already proposes that same name as a mapping slot
   coordinate, so the spec bounded it twice and ESBMC refused the query
   outright: *"the coordinate is bounded TWICE in this spec; two bounds on one
   name can intersect to an empty box ... Certification is not attempted."*
   No certification query was ever issued on any arm carrying that pin. The
   "VACUOUS" I recorded earlier was this refusal, not an empty region.
   Fix: drop the pin. The slot coordinate covers it, at full range, which is
   what the motivation needs anyway — both mutants alter discount arithmetic.

2. **Wrong transaction alphabet.** `cert_DRY` and `cert_tx2` were both
   `scope=focus`, i.e. `withdraw` alone. Only 2 paths are witnessed that way and
   neither is a successful withdrawal, so Stage 4 could only ever emit the
   revert-path PUT — which is why it passed on M1 and M2. The successful path
   (enc=127) needs `--scope deposit,withdraw --max-tx 2`.

3. **Per-run budget, not the unit budget.** `certify_all.py --run-timeout`
   defaults to **180s** and is what binds the driver; `--timeout` alone does
   nothing for it. Under scope `deposit,withdraw` at max-tx 2 the unit has
   **635 path claims**; at 180s the enumeration covered 2 and left 633
   undecided, certified enc=6 (the revert path) and stopped. Re-run as `tx2t`
   with `--timeout 3600 --run-timeout 3600`.

## 2026-08-20 — Stage-4 bug: a commented-out constructor was read as a constructor

`_source_constructor_params_from_source` matched `constructor(` on the raw
contract chunk. `peer_soltg__exampl` declares no constructor -- its only such
text is `//  constructor(uint i) {a = i;}` -- so the emitter deployed
`new A(0)`, every emitted test failed to compile with *"Wrong argument count
for function call: 1 arguments given but expected 0"*, and the subject produced
0 valid tests (bucket `no-valid`, status `ok`, which is what made it look like
a method failure rather than a crash). Fixed in `7bdedbfbba` by masking
comments and strings first, as every other constructor reader in that module
already does; two regression tests added. A scan of all three benchmarks finds
`exampl` to be the only affected subject, so the campaign stays internally
consistent and only that one subject needs re-running.

## 2026-08-20 06:49 — scheduling decision: the two campaigns run concurrently

w2 never came back, so no-selection has to run on the same box. Measured rates:
bugfix124 124 cases in 111 min at `--jobs 6`; peer182 at ~1.6 cases/min. Full
projects to ~10:40. Sequentially, no-selection could not start before then and
would land ~14:30, past the deadline. Measured memory is not the constraint --
31 concurrent esbmc processes held 7.8 GiB total (0.25 GiB avg) against 32 GiB
available, so the 12 GiB per-process cap is nearly never reached.

So no-selection was started at 06:49 alongside Full, at `--jobs 4` against
Full's 6 on a 12-core box. **Caveat to record with the numbers:** Full's first
251 cases ran uncontended and the rest run under load, while no-selection runs
under load throughout. The per-case wall stays 600s for both, unchanged.
