# VeriPUT Engineering Memory

Last updated: 2026-08-07

This document is the durable handoff state for VeriPUT. It records facts that
were established by reading the paper, the work order, the implementation, and
the existing run artefacts. It is not an experiment result and must not be used
as one. The user explicitly requested this file, overriding the older work-order
rule against creating new Markdown files.

## 2026-08-07 current benchmark breadth signal

User budget update:

- Treat the final per-case ESBMC certification budget as 600s.
- Keep the normal small-run memory cap at 8GiB unless the user explicitly says
  otherwise.
- Fuzz is still refute-only.  It can cheaply find counterexamples for bad
  assertions/regions/instrumentation, but it cannot prove a region or a PUT.

Current 600s / 8GiB breadth smoke:

- Root: `/tmp/veriput_current_smoke_20260807_065046`.
- Scope: 9 benchmark units, 3 each from Peer/BugFix/Stress, using cached ASTs
  and writing only under `/tmp`.
- Stage2 unit results:
  - Overall certified units: 4 / 9 = 44.4%.
  - Peer: 0 / 3.
  - BugFix: 1 / 3.
  - Stress: 3 / 3.
  - Buckets: 4 `CERTIFIED`, 3 `NO-WITNESS-UNDECIDED`, 1 `NO-PATH`,
    1 `NOT-CERTIFIED`.
- Stage2 certified regions:
  - 13 total certified regions from the 4 certified units.
  - Peer contributed 0 regions.
  - BugFix contributed 1 region.
  - Stress contributed 12 regions.
- Stage4 on those 13 certified regions:
  - Reference-valid generated tests: 13 / 13.
  - Strict PUT/B: 9 / 13 = 69.2%.
  - Concrete replay fallback: 4 / 13 = 30.8%.
  - Forge replay was green for all emitted PUT/concrete tests.

Latest-code Peer spot check after static-obstacle scheduling:

- Root: `/tmp/veriput_latest_peer_probe`.
- Ran 3 Peer jobs, 600s certify budget embedded, 700s outer runner timeout,
  8GiB, `jobs=1`.
- Results:
  - `AIRBets.approve`: `NO-PATH`, 0 certified / 0 not, about 1s.
  - `Arcadia_Token.approve`: `NO-PATH`, 0 certified / 0 not, about 9s.
  - `Animalia.transfer`: `NO-WITNESS-UNDECIDED`, 0 certified / 0 not,
    about 128s; 132/132 claims were named-obstacle paths.

Interpretation:

- The current emitter is not the main bottleneck: when Stage2 produces certified
  regions, Stage4 currently gives 100% reference-valid tests on this smoke.
- The main bottleneck is Stage2 witness/region production, especially Peer.
- Peer failures are mixed:
  - Some ERC20-like simple methods are genuinely `NO-PATH` within the current
    bounded model.
  - Some transfer-like methods hit named/static obstacles before useful region
    certification.
  - Recursive-helper preflight is useful for avoiding wasted solver budget, but
    breadth scheduling should avoid spending early slots on those units.
- Campaign planning already marks bounded-holds/no-witness as retryable with a
  `--max-tx 2` strategy, but the default priority policy still prefers fresh
  attempt-1 jobs before retrying attempt-2 jobs.  That is good for breadth, but
  not for immediately rescuing a known no-witness Peer unit.

Follow-up fix after this spot check:

- `certify_all.py` now records `empty_witness_obstacles` by compacting
  `cov-report.json` named-obstacle `u_reason_detail` values into
  `detail -> count`.  This keeps the JSONL small while preserving the cause
  that the old generic `empty_witness_reason` dropped.
- `unit_campaign_plan.py` now distinguishes:
  - generic named-obstacle no-witness: still non-retryable;
  - residual gated-unit depth obstacle (`unit still calls another UNIT's own
    body unexpanded`): retryable with `--max-tx 2`,
    `--esbmc-arg=--unwind=8`, `--probe-witnesses 0`, and no probe ladder.
- Probe-claim explosion retry now disables path probes (`--probe-witnesses 0`)
  instead of merely reducing max witnesses.  The refusal is caused by the
  branch-arm x exit probe universe, so keeping any path probe rebuilds the same
  product.

Animalia transfer validation:

- Original `Animalia.transfer` 600s/8GiB run:
  `NO-WITNESS-UNDECIDED`, 132/132 named-obstacle paths, about 128s.
- `--unwind=8` while keeping probes:
  `NO-WITNESS-UNKNOWN` in about 2s because path probes needed 19344 claims and
  exceeded `--path-cov-max-goals 10000`.
- `--unwind=8 --probe-witnesses 0`:
  `NO-PATH`, 372/372 bounded-holds, about 12s.
- `--max-tx 2 --unwind=8 --probe-witnesses 0`:
  `NO-PATH`, 372/372 bounded-holds, about 33s.

Interpretation:

- The retry strategy is still useful: it converts a structural named-obstacle
  into a decided bounded result and avoids a probe-universe explosion.
- It did not rescue `Animalia.transfer` into a certified region.  The remaining
  Peer failure is likely entry-state / owner-balance feasibility, or the
  complete-path assertion polarity/entry modelling, rather than just call-depth
  expansion.

## 2026-08-07 point-region concrete fallback

Why this was changed:

- The 600s mini-batch found one Stage4 loss:
  `peer182 / peer_ccsolbmc__BasicProvenance.Complete` enc=7 was a certified
  point region (`msg.sender == 0`) with no rendered coordinate wider than one
  value.
- Old behavior refused the row as `NOT PARAMETERIZED`.  That was correct for
  PUT accounting, but it lost a reference-valid generated test: the emitter's
  own concrete replay existed and was green.
- User asked for two numbers: valid tests on the reference contract, and the
  split between concrete replay tests and PUTs.  Refusing the point row made
  that second number inaccurate.

Code change:

- `scripts/solidity_path_put.py`
  - Adds `ConcreteFallback` for regions that certify but render no wide
    coordinate.
  - Adds `assemble_concrete_source()`, which keeps exactly the selected
    `test_cov_*` replay, removes the other concrete replays from the emitted
    file, rewrites imports for the Forge project layout, and gives the test
    contract a unique name.
  - Main Stage4 emission catches `ConcreteFallback`, writes a concrete replay
    artefact, and writes `put.json` with `kind: "concrete"`.
  - The R2 Forge prefilter also catches `ConcreteFallback`; a non-parameterized
    candidate probe is treated as unrenderable for fuzz refutation, not as a
    Stage4 crash.  Fuzz remains refute-only.
- `notes/coverage/scripts/put_all.py`
  - `kind == "concrete"` rows no longer increment `PUTs emitted`.
  - `put-summary.json` now includes `emission.concrete_replays_emitted`.
  - `deliverable_b.rows[*].kind` distinguishes `put` from `concrete`.
  - `deliverable_b.valid_reference_tests` records total/PUT/concrete green
    generated tests.
  - B remains strict PUT B: concrete rows are not counted as B.

Validation:

- `python3 -m py_compile scripts/solidity_path_put.py notes/coverage/scripts/put_all.py scripts/test_solidity_path_put.py scripts/test_put_all_accounting.py`
  passed.
- `python3 scripts/test_solidity_path_put.py` passed:
  234 / 234 registered tests.
- `python3 scripts/test_put_all_accounting.py` passed.
- `git diff --check -- scripts/solidity_path_put.py notes/coverage/scripts/put_all.py scripts/test_solidity_path_put.py scripts/test_put_all_accounting.py`
  passed.

Real single-case Stage4 validation:

- Command used existing Stage2 cert only; Stage2 was not rerun:
  `python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_minibatch_20260807_054341/peer182__peer_ccsolbmc__BasicProvenance__Complete.jsonl --only peer182__peer_ccsolbmc__BasicProvenance.Complete --out-root /tmp/veriput_basicprovenance_concretefix2_20260807_055952 --scope focus --max-tx 1 --timeout 600 --memlimit-gib 8 --strong-recipe`
- Summary:
  `/tmp/veriput_basicprovenance_concretefix2_20260807_055952/put-summary.json`
- Result:
  - `emission.certified_region_rows = 2`
  - `emission.puts_emitted = 1`
  - `emission.concrete_replays_emitted = 1`
  - `deliverable_b.b = 1`
  - `deliverable_b.refused = 0`
  - `deliverable_b.forge_seen.put.Success = 1`
  - `deliverable_b.forge_seen.concrete.Success = 1`
  - `deliverable_b.valid_reference_tests = {"total": 2, "put": 1, "concrete": 1}`

Effect on the earlier mini-batch if re-emitted under this code:

- Strict PUT B over the 9 certified regions remains 8 / 9: the point row is
  intentionally not a PUT.
- Reference-valid generated tests over those certified regions becomes 9 / 9:
  8 PUT + 1 concrete replay.
- Case-level all sampled units remains 4 / 6 because the two BugFix units still
  stop at Stage2 `NO-WITNESS-UNKNOWN`.

## 2026-08-07 current small-wave reference-valid rate

User policy update:

- Final per-case ESBMC timeout is now 600s.
- User asked for speed and a small broad measurement that reports:
  reference-valid generated tests on the unmodified contract, split into
  concrete replay tests and parameterized unit tests.
- The wave below is intentionally small. It is a status snapshot, not a
  publishable corpus result.

What was run:

- No files under `/home/samson/workspace/VeriPUT/Datasets` were modified.
- Output root:
  `/tmp/veriput_wave_20260807_060455`.
- To save ESBMC time, two older Stage2 certification JSONL files were reused
  and re-emitted through the current Stage4 code at `c8023171f9`:
  - BugFix:
    `/tmp/veriput_sample_v10_20260806_212550/certify-bugfix124.jsonl`
    -> `/tmp/veriput_wave_20260807_060455/put-bugfix-oldcert-current`.
  - Stress:
    `/tmp/veriput_sample_v10_20260806_212550/certify-stress203.jsonl`
    -> `/tmp/veriput_wave_20260807_060455/put-stress-oldcert-current`.
- The current 600s mini-batch rows from
  `/tmp/veriput_minibatch_20260807_054341` were included, with
  `BasicProvenance.Complete` corrected by the concrete fallback validation at
  `/tmp/veriput_basicprovenance_concretefix2_20260807_055952`.

Measured sample:

- 11 unit rows total:
  - 6 from the 600s mini-batch.
  - 3 from the older BugFix cert file.
  - 2 from the older Stress cert file.
- 8 / 11 unit rows produced at least one reference-valid generated test
  = 72.7%.
- 8 / 8 unit rows with at least one certified region produced at least one
  reference-valid generated test = 100%.
- 3 / 11 unit rows still fail before emission because Stage2 does not produce a
  certified region (`NO-WITNESS-UNKNOWN` / no path class failures).

Certified-region / emitted-test accounting:

- Certified region rows measured through current Stage4: 16.
- Reference-valid generated tests: 16 / 16 = 100%.
- Strict PUT B: 15 / 16 certified region rows = 93.8%.
- Valid generated-test split:
  - 15 / 16 are PUTs = 93.8%.
  - 1 / 16 is a concrete replay fallback = 6.2%.

Interpretation:

- The current emitter is not the bottleneck on this small wave: once Stage2
  certifies a region, Stage4 produced a green generated test for every measured
  row.
- The remaining end-to-end loss is Stage2 path witnessing/certification, mainly
  early no-witness/no-path rows. This is where the next speed/quality work
  should focus before expanding to a larger benchmark wave.

## 2026-08-07 benchmark sampling throughput fix

Why this was changed:

- Larger benchmark waves need to enumerate prepared subjects quickly without
  hand-picking around broken rows.
- A real smoke attempt over the first 30 Stress subjects used to abort at
  `ProjectOpenSea__seaport__Seaport` because its prepared `meta.json` has
  `status='compile-failed'`.
- The existing priority schedule also packed scarce sample slots into one
  subject when a subject had many state-changing units, which distorted the
  case-level success-rate picture.

Code change:

- `notes/coverage/scripts/subject_unit_manifest.py`
  - `--subject-id` and full benchmark scans now convert unusable prepared
    subjects into row-local `status: error` manifest rows instead of aborting
    the whole manifest.
  - Target-manifest behavior already had this fail-soft shape; this makes the
    ordinary benchmark scanner match it.
- `notes/coverage/scripts/unit_schedule.py`
  - Adds `--selection-strategy round-robin-subject`.
  - This preserves priority sorting inside the job pool, then cycles by
    `(benchmark, subject_id)` so a small sample covers more subjects before
    taking the second or third unit from the same subject.

Validation:

- `python3 -m py_compile notes/coverage/scripts/subject_unit_manifest.py notes/coverage/scripts/unit_schedule.py scripts/test_veriput_subjects.py scripts/test_unit_schedule.py`
  passed.
- `python3 scripts/test_veriput_subjects.py` passed: 21 tests.
- `python3 scripts/test_unit_schedule.py` passed.
- `git diff --check -- notes/coverage/scripts/subject_unit_manifest.py notes/coverage/scripts/unit_schedule.py scripts/test_veriput_subjects.py scripts/test_unit_schedule.py`
  passed.

Real read-only smoke:

- Command:
  `python3 notes/coverage/scripts/subject_unit_manifest.py --benchmark stress243 --limit 30 --ast-cache-root /tmp/veriput_ast_cache_stress_20260807 --out /tmp/veriput_wave_20260807_060455/stress-limit30-manifest-afterfix.json`
- Result:
  `subjects=30 ok=10 missing_ast=19 error=1 units=81 skipped=4`.
- The error row is now retained instead of aborting:
  `ProjectOpenSea__seaport__Seaport status='compile-failed'`.
- Round-robin-subject schedule:
  `/tmp/veriput_wave_20260807_060455/stress-limit30-schedule-rrsubject.json`
  with `--limit 12` produced 12 jobs across 10 subjects.

Impact:

- This does not change ESBMC modelling or PUT semantics.
- It makes the next 20-30 unit benchmark wave cheaper to prepare and less
  biased: bad prepared subjects are counted once and skipped, and scarce
  verification attempts are spread across subjects.

Follow-up target relevance improvement:

- Ordinary benchmark scans now also turn prepared metadata
  `changed_functions` into `unit_hints` with source
  `prepared-metadata.changed_functions`.
- This is intentionally different from name-based filtering:
  constructor-like names such as `owned` are not guessed away, because in
  BugFix they can be the actual changed target unit.
- Unit scheduling already treats hinted units as priority 0, so this makes
  BugFix samples spend early attempts on changed functions when the prepared
  subject names them.

Validation for the metadata hint change:

- `python3 -m py_compile notes/coverage/scripts/subject_unit_manifest.py scripts/test_veriput_subjects.py`
  passed.
- `python3 scripts/test_veriput_subjects.py` passed: 22 tests.
- `python3 scripts/test_unit_schedule.py` passed.
- `git diff --check -- notes/coverage/scripts/subject_unit_manifest.py scripts/test_veriput_subjects.py`
  passed.

Fresh wave plan prepared, not executed:

- Root:
  `/tmp/veriput_fresh_wave_plan_20260807_061251`.
- Manifests were generated from existing AST caches only; no ESBMC and no solc
  were started.
- Initial 24-job plan:
  - Peer: 8 jobs across 8 subjects.
  - BugFix: 8 jobs across 8 subjects.
  - Stress: 8 jobs across 8 subjects.
  - All jobs carry `--timeout 600 --run-timeout 600 --memlimit-gib 8`.
- After metadata hints, BugFix was regenerated as:
  `/tmp/veriput_fresh_wave_plan_20260807_061251/bugfix-schedule-hinted.json`.
  Its first 8 jobs contain 7 priority-0 changed-function units:
  `Owned.owned` x3, `L1Block.setL1BlockValues` x2,
  `DepositLog.approvedToLog`, and
  `DnGmxBatchingManager.executeBatchDeposit`; the remaining slot is
  `StaxLPStaking.setRewardDistributor`.

## 2026-08-07 fresh benchmark smoke and early-failure diagnosis

Fresh Stage2/Stage4 smoke:

- Root:
  `/tmp/veriput_fresh_wave_plan_20260807_061251`.
- All ESBMC attempts used the scheduled `--timeout 600 --run-timeout 600
  --memlimit-gib 8`; runner outer timeout was 700s and `--jobs 1`.
- Initial Stage2 jobs:
  - Peer old schedule:
    `AIRBets.initialize2` -> `NO-PATH`, 0 certified.
  - Peer old schedule:
    `Arcadia_Token.abc1` -> `NO-PATH`, 0 certified.
  - BugFix hinted schedule:
    `acfix_026_CVE_2019_15080 / Owned.owned` ->
    `NO-WITNESS-UNKNOWN`, 0 certified.
  - BugFix hinted schedule:
    `acfix_030_CVE_2021_34272 / Owned.owned` ->
    `NO-WITNESS-UNKNOWN`, 0 certified.
  - Stress:
    `ERC-3643__ERC-3643__AgentRole.addAgent` -> `CERTIFIED`,
    5 witnessed, 4 certified, 1 not-certified, 50s.
- Stage4 was run only for the Stress cert:
  `/tmp/veriput_fresh_wave_plan_20260807_061251/stress-put-fresh1`.
  Result: 4 / 4 certified region rows became reference-valid generated tests:
  2 PUT + 2 concrete fallback. Strict PUT B = 2 / 4.

Scheduling correction from this smoke:

- The first Peer rows were zero-interface state-changing units
  (`initialize2`, `abc1`) and produced no paths.
- `unit_schedule.py` now lowers non-hinted state-changing units with
  zero parameters and zero returns to priority 2
  (`zero-interface-state-changing`).
- Hinted units still stay priority 0; this avoids guessing away real changed
  targets such as BugFix `owned`.
- Regenerated Peer prioritized schedule:
  `/tmp/veriput_fresh_wave_plan_20260807_061251/peer-schedule-prioritized.json`.
  Its first 8 rows are now parameterized state-changing units such as
  `AIRBets.transfer`, `Arcadia_Token.transfer`, and `Animalia.transfer`.

Fresh prioritized Peer check:

- Ran first two prioritized Peer rows:
  - `AIRBets.transfer`
  - `Arcadia_Token.transfer`
- Both returned immediately as `NO-WITNESS-UNDECIDED`.
- Their `driver.log` reveals the real reason is not a solver timeout:
  `target call closure reaches direct self-recursive function/helper wrapper(s):
  SafeMath.div/2, SafeMath.sub/2`. This is the existing recursive-helper
  preflight refusal.

BugFix early failure check:

- The two `Owned.owned` rows are also not solver timeouts.
- Their `driver.log` shows ESBMC started path instrumentation but exited before
  `cov-report.json`:
  `ERROR: function call: argument "c:string.c@4751@F@memset@s" type mismatch:
  got array, expected pointer`, with `[run] EXIT -6`.
- This is an ESBMC/frontend/operational-model issue, not a region or Stage4
  issue.

Diagnostic code change:

- `notes/coverage/scripts/certify_all.py`
  - `result_driver_diagnostic()` now tags recursive-helper preflight refusals
    as `recursive-helper-preflight-refused` and records the helper names.
  - It also tags ESBMC no-report failures as `esbmc-no-cov-report` and records
    the `ERROR:` line plus run exit code when present.
- This does not change Stage2 bucket semantics; it makes no-witness rows
  machine-attributable so the next sample can report why rows failed without
  manually opening `driver.log`.

Validation:

- `python3 -m py_compile notes/coverage/scripts/certify_all.py scripts/test_certify_all_partial_journal.py notes/coverage/scripts/unit_schedule.py scripts/test_unit_schedule.py`
  passed.
- `python3 scripts/test_certify_all_partial_journal.py` passed.
- `python3 scripts/test_unit_schedule.py` passed.
- `git diff --check -- notes/coverage/scripts/certify_all.py scripts/test_certify_all_partial_journal.py notes/coverage/scripts/unit_schedule.py scripts/test_unit_schedule.py`
  passed.
- New parser output on real logs:
  - Peer transfer: `recursive-helper-preflight-refused`, helpers
    `SafeMath.div/2`, `SafeMath.sub/2`.
  - BugFix `Owned.owned`: `esbmc-no-cov-report`, error
    `memset` array/pointer mismatch, exit `-6`.

## 2026-08-07 600s mini-batch PUT success snapshot

User policy update:

- Final per-case ESBMC timeout is 600s.
- User needs speed and asked for a small broad sample that gives an intuitive
  current success-rate picture: generated tests valid on the reference contract,
  split into concrete replay vs parameterized unit tests.
- Outputs stayed under `/tmp`; no Dataset/Results contract files were modified.

Code/memory state before the run:

- Handoff memory update for Stage-4 summary JSON was committed and pushed:
  `61d95d942b [docs] Record VeriPUT PUT summary output`.
- Branch remains `feat/veriput-fuzz-first`, pushed to `E-SOL`.

AST preheat:

- Peer/BugFix compact ASTs were generated into:
  - `/tmp/veriput_ast_cache_peer_20260807`
  - `/tmp/veriput_ast_cache_bugfix_20260807`
- Stress needed `--use-inferred-solc-bin`; compact ASTs were generated into:
  `/tmp/veriput_ast_cache_stress_20260807`.
- This preheat starts `solc` only, not ESBMC, and does not spend verification
  attempts.

Mini-batch root:

- `/tmp/veriput_minibatch_20260807_054341`
- Summary:
  `/tmp/veriput_minibatch_20260807_054341/batch-summary.json`

Common Stage-2/Stage-4 settings:

- Stage 2:
  `--recipe-version veriput-strong/15-relation-establish --scope focus
  --max-tx 1 --timeout 600 --run-timeout 600 --memlimit-gib 8 --jobs 1
  --probes 8 --refine-rounds 2 --shrink-rounds 4
  --safety-retreat-after-tiny-cuts 2 --claim-budget 0 --level0
  --level0-perturb --probe-witnesses 8 --probe-ladder
  --probe-ladder-budget 4 --skip-bracket --env-coord-disagreed
  --pin-agreed-establishable-env --pin-agreed-state --max-holes 1
  --max-region-pieces 1 --cut-policy spec --state-struct-fields
  --slot-coords 8 --static-uncontrolled-inseparable
  --esbmc-arg=--overflow-check --esbmc-arg=--div-by-zero-check
  --esbmc-arg=--path-cov-arith-resolve`
- Stage 4:
  `put_all.py --scope focus --max-tx 1 --timeout 600 --memlimit-gib 8
  --strong-recipe`.

Raw sample:

- `peer182 / peer_ccsolbmc__MayoOcho.transfer`
  - Stage2: 87.6s, witnessed 5, certified 4, not-certified 1.
  - Stage4: 22.1s, certified-region rows 4, B=4.
  - Forge: PUT Success 4, concrete Success 0.
  - All 4 emitted PUTs have fuzz inputs and oracle assertions.
- `peer182 / peer_ccsolbmc__Ballot.vote`
  - Stage2: 9.8s, witnessed 2, certified 1, not-certified 1.
  - Stage4: 2.2s, certified-region rows 1, B=1.
  - Forge: PUT Success 1, concrete Success 0.
- `peer182 / peer_ccsolbmc__BasicProvenance.Complete`
  - Stage2: 3.6s, witnessed 3, certified 2, not-certified 1.
  - Stage4: 3.1s, certified-region rows 2, B=1.
  - Forge: PUT Success 1, concrete Success 0.
  - One certified region was refused by Stage4; this is the only observed
    certified-region-to-PUT loss in the mini-batch.
- `bugfix124 / acfix_015_CVE_2018_10666.setOwner`
  - Stage2: 1.2s, `NO-WITNESS-UNKNOWN`, certified 0.
  - No Stage4 run because there was no certified region.
- `bugfix124 / acfix_022_CVE_2018_19833.transfer`
  - Stage2: 0.6s, `NO-WITNESS-UNKNOWN`, certified 0.
  - No Stage4 run because there was no certified region.
- `stress243 / ERC-3643__ERC-3643__ClaimTopicsRegistry.addClaimTopic`
  - Stage2: 340.8s, witnessed 3, certified 2, not-certified 1.
  - Stage4: 51.1s, certified-region rows 2, B=2.
  - Forge: PUT Success 2, concrete Success 0.
  - This is the first direct signal in this round that Stress can fit under
    600s but Stage2 certification cost is already the bottleneck.

Measured rates from this tiny sample:

- Case-level, all sampled units: 4 / 6 units generated at least one valid
  reference test = 66.7%.
- Case-level, units with a certified region: 4 / 4 generated at least one
  valid reference test = 100%.
- Certified-region-level: 8 / 9 certified regions became valid reference tests
  = 88.9%.
- Valid-test split: 8 / 8 are parameterized unit tests, 0 / 8 are concrete
  replay tests.
- Forge outcome among emitted PUTs: 8 Success, 0 Failure, 0 other.

Interpretation:

- Do not treat the 6-unit denominator as a corpus success rate; the sample is
  intentionally tiny and manually selected to get a fast picture.
- Current Stage4 emitter health looks good on certified regions: 88.9% B in
  this sample, with all successes as actual PUTs.
- The immediate throughput bottleneck is still Stage2/path witnessing and
  certification.  The two BugFix rows failed before PUT generation
  (`NO-WITNESS-UNKNOWN`), and the Stress row consumed 340.8s before Stage4.
- The next code target for speed should be scheduling/prioritization and
  no-witness diagnosis, not blindly expanding Stage4.  The only Stage4-specific
  loss observed here is the one refused BasicProvenance region.

## 2026-08-06 unit campaign budget and summary fix

Context:

- The first small benchmark sample was useful as a diagnosis but is not a
  clean measurement. The schedules were run with an outer `unit_schedule_run.py`
  timeout/memlimit, but each job's inner `certify_all.py` argv still used its
  defaults: rows recorded `unit_timeout_s=600`, `run_timeout_s=180`.
- A Peer timeout also exposed an infrastructure bug: `subprocess.TimeoutExpired`
  can carry bytes stdout/stderr even when `text=True`, and the runner tried to
  JSON-serialize those bytes.
- `certify_result_summary.py` mismatched schedule rows and result rows for
  prepared subjects because schedule jobs used population names such as
  `bugfix124`, while `certify_all.py` rows use the prepared `benchmark_key`,
  e.g. `bugfix124__acfix_fixlink_DepositLog`.
- A follow-up budget-clean smoke attempt under
  `/tmp/veriput_budgetclean_v10_20260806_214230` showed one more
  infrastructure issue: `certify_all.py --workdir` defaulted to
  `/tmp/certify_all`, so the same unit under a new budget/recipe refused to
  reuse stale scratch artefacts from an older configuration. Those
  `DRIVER-REFUSED [workdir]` rows are not method results.

Code change:

- `unit_schedule.py` now makes executable schedules self-describing:
  default job argv carries the first-attempt budget
  `--timeout 60 --run-timeout 60 --memlimit-gib 8`.
- `unit_campaign_plan.py` now rewrites every next-attempt job argv to the
  selected policy:
  attempt 1 = `60s/8GiB`, attempt 2 = `120s/8GiB`,
  attempt 3 = `600s/10GiB`. The runner still carries the same outer timeout and
  memory cap as a process-level guard.
- Schedules now also embed `--workdir`. With `--cert-out`, the scratch root is
  derived from the result file's parent and the budget; campaign retry schedules
  add the attempt number. This keeps different budgets from colliding in
  `/tmp/certify_all`.
- `unit_schedule_run.py` now decodes timeout stdout/stderr tails before writing
  the JSONL journal, so a timed-out job remains resumable and auditable.
- `certify_result_summary.py` now matches schedule jobs through prepared
  subject aliases, preferring `subject.benchmark_key` when present. It also
  separates hash/nondet static-inseparable reasons from external-call static
  inseparability.

Read-only sample re-summary after the fix:

- BugFix old sample:
  `/tmp/veriput_sample_v10_20260806_212550/certify-bugfix124.jsonl` with its
  schedule now reports `missing_scheduled_units=0`, `gate=ready`,
  `5 certified / 0 not / 5 witnessed` over the two witnessed DepositLog units;
  the DnGmxBatchingManager row remains `NO-WITNESS-UNKNOWN`.
- Stress old sample:
  `/tmp/veriput_sample_v10_20260806_212550/certify-stress203.jsonl` with its
  schedule now reports `missing_scheduled_units=0`, `2 certified / 8 not / 10
  witnessed`, gate `degraded` only because the certified path rate is 0.2.
- Peer old sample:
  `/tmp/veriput_sample_v10_20260806_212550/certify-peer182.jsonl` correctly
  reports two genuinely missing certification rows: `transfer` timed out at the
  runner layer and `approve` was not attempted in that diagnostic run.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_result_summary.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule_run.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile ...` over the changed
  scheduler, campaign, runner, summary, pipeline, and tests passed.
- `git diff --check` passed.

## 2026-08-06 salvage benchmark smoke after ad52c97

Ran a new small benchmark sample after partial CE journal salvage.  All outputs
were under `/tmp`; no Dataset or Results contract was modified.

Plans:

- BugFix:
  `/tmp/veriput_bench_salvage_bugfix_20260806_235923`
- Stress:
  `/tmp/veriput_bench_salvage_stress_20260806_235923`
- Peer:
  `/tmp/veriput_bench_salvage_peer_20260806_235924`

Execution:

- `unit_schedule_run.py`, serial `--jobs 1`.
- Runner outer timeout 75s, certify wrapper timeout 70s, ESBMC
  `--run-timeout 60`, memory 8GiB.
- AST cache reused from `/tmp/veriput_bench_ast_cache_20260806`.

Results:

- BugFix attempt-1 sample: 4/4 runner-ok and 4/4 `CERTIFIED`.
  - `DepositLog.approvedToLog`: `1 certified / 1 not / 2 witnessed`, 2.8s.
  - `DepositLog.setApprovedLogger`: `2 certified / 1 not / 3 witnessed`,
    11.7s.
  - `EtherLotto.play`: `1 certified / 2 not / 3 witnessed`, 23.7s.
  - `DepositLog.logCreated`: `1 certified / 1 not / 2 witnessed`, 1.5s.
- Stress attempt-1 sample on `ClaimTopicsRegistry`: 3/3 runner-ok.
  - `init`: still `KILLED` at 60s with no partial witness journal.
  - `addClaimTopic`: now `CERTIFIED`, `1 certified / 0 not / 1 witnessed`,
    65.7s wall.  `driver.log` says:
    `[enumerate] salvaged 1 witnessed path(s) from partial cov-ce-journal.json
    (6/277 claims decided); regions still require independent certification`.
  - `removeClaimTopic`: now `CERTIFIED`, `1 certified / 0 not / 1 witnessed`,
    67.9s wall.  `driver.log` says:
    `[enumerate] salvaged 1 witnessed path(s) from partial cov-ce-journal.json
    (13/227 claims decided); regions still require independent certification`.
- Peer attempt-1 sanity:
  - `AIRBets.initialize2`: `NO-PATH`, about 1s.
  - `AIRBets.transfer`: `NO-WITNESS-UNDECIDED`, about 0.1s.  This is not a
    journal-salvage case; it remains a witness-discovery/reachability
    classification problem.

Replan after the stress sample:

- `unit_campaign_plan.py` over the stress schedule/journal/cert rows reports
  `completed_ok=2`, `cert_weak={"no certified regions": 1}`,
  `pending_by_attempt={"2": 1}`, `selected_attempt=2`.
- The only attempt-2 job is `ClaimTopicsRegistry.init`, with ESBMC
  `--run-timeout 120`, wrapper timeout 130, runner timeout 135, 8GiB.
- A first attempt-2 runner invocation immediately refused before starting
  ESBMC because `certify_all.py` correctly detected that the existing
  `certify-results.jsonl` rows were produced under HEAD `ad52c97e6b` while the
  tree was now at `e61e0cbfca`.  This was an identity guard, not an ESBMC
  attempt.  The actual attempt-2 run used a separate cert JSONL/workdir:
  `/tmp/veriput_bench_salvage_stress_20260806_235923/certify-results-a2-actual.jsonl`
  and
  `/tmp/veriput_bench_salvage_stress_20260806_235923/certify-work-a2_actual_t130_r120_m8`.
- Actual `ClaimTopicsRegistry.init` attempt 2 finished in 89.1s with
  `NO-COORDINATE`: 2 witnessed paths, 0 certified, 0 not, complete
  `cov-report.json`/`cov-ce-journal.json`, and no `generalise-result.json`
  because the driver exits at the coordinate-kind gate.  The two paths are the
  ABI reject/body split (`enc=2` reject, `enc=52` body) plus initializer-state
  decisions; there are no function inputs, and every establishable environment
  and entry-state quantity agreed and was pinned.  This is not a salvage
  failure and not fixed by more ladder/refine budget.

Current go/no-go:

- This is strong evidence that partial-journal salvage is worth keeping:
  two stress units that previously ended as 60s KILLED now certify on the first
  attempt because the partial path witness is reused and then independently
  certified.
- Do not full-sweep stress yet.  First expand to a slightly larger stratified
  sample across already cached/preheated stress subjects, and schedule
  attempt-2 only for weak rows such as `init`.
- BugFix is ready for broader sampling.

Code follow-up retained after the smoke:

- `solidity_path_generalise.py` now writes `enumeration-salvage.json` when a
  partial CE journal seeds enumeration, removes stale sidecars before a new
  enumeration, and embeds the metadata under
  `generalise-result.json["enumeration_source"]["salvage"]`.
- `certify_all.py` now copies that metadata into each result row as
  `enumeration_salvage`.  This fixes observability: successful salvage no longer
  has to be inferred by grepping `driver.log`, and the later certification
  `cov-ce-journal.json` cannot overwrite the evidence trail.
- `certify_all.py` also no longer parses `[coords] mapping dependency policy
  ...` prose as a legacy coordinate line.  This was exposed by the `init`
  attempt-2 row, where the runner summary said "1 free coordinate" even though
  the driver correctly printed `NO GENERALISABLE COORDINATE`.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py
  notes/coverage/scripts/certify_all.py
  scripts/test_certify_all_partial_journal.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `git diff --check -- scripts/solidity_path_generalise.py
  scripts/test_solidity_path_generalise.py notes/coverage/scripts/certify_all.py
  scripts/test_certify_all_partial_journal.py` passed.
- After the parser follow-up, the same py_compile,
  `scripts/test_solidity_path_generalise.py`,
  `scripts/test_certify_all_partial_journal.py`, and `git diff --check`
  commands passed again.

## 2026-08-07 balanced cached-stress smoke

Ran a second, more balanced stress-only sample across the three cached stress
subjects.  All outputs stayed under `/tmp`; no Dataset or Results contract was
modified.

Plan:

- Pipeline out dir:
  `/tmp/veriput_stress_balanced_20260807_002507`
- Source cache:
  `/tmp/veriput_bench_ast_cache_20260806`
- Full cached stress schedule had 31 unit jobs across:
  - `ERC-3643__ERC-3643__ClaimTopicsRegistry`
  - `ERC-3643__ERC-3643__IdentityRegistryStorage`
  - `balancer__balancer-v3-monorepo__OwnableAuthentication`
- Manual `/tmp`-only filtered schedule:
  `/tmp/veriput_stress_balanced_20260807_002507/next-unit-schedule-balanced6.json`
  with 6 units:
  `IdentityRegistryStorage.addIdentityToStorage`,
  `IdentityRegistryStorage.bindIdentityRegistry`,
  `IdentityRegistryStorage.storedIdentity`,
  `OwnableAuthentication.forceTransferOwnership`,
  `OwnableAuthentication.transferOwnership`,
  `OwnableAuthentication.getActionId`.

Execution:

- `unit_schedule_run.py`, serial `--jobs 1`.
- Runner outer timeout 75s, certify wrapper timeout 70s, ESBMC
  `--run-timeout 60`, memory 8GiB.

Results:

- 6/6 runner-ok.
- Buckets: `CERTIFIED=2`, `KILLED=3`, `NO-COORDINATE=1`.
- Certified:
  - `OwnableAuthentication.transferOwnership`: `2 certified / 1 not /
    3 witnessed`, 63.3s.
  - `IdentityRegistryStorage.storedIdentity`: `1 certified / 1 not /
    2 witnessed`, 8.0s.
- Killed after useful work:
  - `IdentityRegistryStorage.addIdentityToStorage`: `KILLED`, 5 witnessed,
    4 free coords, level-0 decided all 5 paths in 3.4s, partial CE journal
    salvage sidecar says `89/116` claims decided and 40 witnesses.
  - `IdentityRegistryStorage.bindIdentityRegistry`: `KILLED`, 1 witnessed,
    1 free coord, level-0 in 1.3s, salvage sidecar `2/214` claims decided and
    8 witnesses.  Refine reached a full address-space region before timeout.
  - `OwnableAuthentication.forceTransferOwnership`: `KILLED`, 1 witnessed,
    1 free coord, level-0 in 2.3s, salvage sidecar `1/303` claims decided and
    8 witnesses.  Refine reached a full address-space region before timeout.
- Coordinate-kind:
  - `OwnableAuthentication.getActionId`: `NO-COORDINATE`, 2 witnessed.
    Driver correctly says unsupported coordinate kinds: `selector`,
    `selector.length`, and `state._actionIdDisambiguator`; more ladder/refine
    budget will not turn this into a parameterized test.

Diagnosis:

- The large current stress bottleneck is now after witness discovery: many
  units have usable partial witnesses and quick level-0/refine, but the wrapper
  kills before certification can finish/write `generalise-result.json`.
- This supports running attempt-2 on the KILLED-with-salvage rows, rather than
  blindly expanding the attempt-1 sample.
- `NO-COORDINATE` rows should be separated from failures in summaries; they are
  real coordinate-kind limits for PUT generation, not timeout failures.

Code follow-up retained:

- `certify_all.py::result_enumeration_salvage()` now falls back to
  `enumeration-salvage.json` when `generalise-result.json` was not written
  because the driver timed out.  This keeps the salvage evidence on KILLED rows.
- `certify_all.py` no longer parses `[coords] STATE PINNED ...` prose as a
  legacy coordinate line.  This was exposed by `getActionId`, where the driver
  correctly printed `NO GENERALISABLE COORDINATE` but the parser reported fake
  free coordinates from the state-pin sentence.
- Offline validation on the balanced6 artefacts confirmed the sidecar fallback
  reads the three KILLED rows' salvage metadata, and `getActionId` parses with
  `coords=[]`, `coords_line=None`.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  notes/coverage/scripts/certify_all.py
  scripts/test_certify_all_partial_journal.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_all.py
  scripts/test_certify_all_partial_journal.py` passed.

## 2026-08-06 partial CE journal salvage in generalise

Current answer to "can we start broad benchmark testing?":

- `bugfix124` is ready for broader sampling.
- `stress203/243` is still not ready for a full sweep.  It is ready for a small
  stratified sample after this patch, because killed 60s enumeration runs can
  now reuse their already-refuted path witnesses instead of throwing them away.
- Keep ESBMC attempt budgets at the agreed ladder:
  attempt 1 = 60s/8GiB, attempt 2 = 120s/8GiB, attempt 3 = 600s/10GiB.
  Wrapper grace remains separate and does not change ESBMC's `--run-timeout`.

Change retained:

- `scripts/solidity_path_generalise.py` now removes stale
  `cov-ce-journal.json` before enumeration, matching the existing stale
  `cov-report.json` guard.
- If ESBMC exits without `cov-report.json`, `enumerate_paths()` tries to build
  a partial enumeration report from a fresh `cov-ce-journal.json`.
- The synthetic report is explicitly `partial=true` and records
  `veriput_salvage.from = cov-ce-journal.json`.  It is refutation-only evidence
  for candidate generation; every region still goes through the normal ESBMC
  certification query before it can count as proved.
- The payload reader now accepts both complete-report dicts and CE-journal
  list-shaped `[{name,value}]` payloads.  Journal env names such as
  `msg_value` and `block_timestamp` are normalized back to `msg.value` and
  `block.timestamp`.  `extcall_returns` accepts either `symbol` or `name`.

Offline confirmation:

- Parsed the real stress sample journal at
  `/tmp/veriput_bench_grace_stress_20260806_233417/certify-work-a1_t70_r60_m8/certify-results/stress243__ERC-3643__ERC-3643__ClaimTopicsRegistry/addClaimTopic/cov-ce-journal.json`.
- It produced one partial witnessed claim for `addClaimTopic:path:31`,
  `claims_decided=6`, `claims_total=277`, 19 scalar coordinates, 8 additional
  witnesses, and no scalar-coordinate refusals.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check -- scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py`
  passed.

## 2026-08-06 benchmark speed sample and timeout layering

Sampled cached benchmark subjects from peer182, bugfix124, and stress243 with
attempt-1 budget semantics: ESBMC run timeout 60s, memory 8GiB, serial jobs.
The AST cache used for sampling was external to the datasets:
`/tmp/veriput_bench_ast_cache_20260806`.

Observed:

- `bugfix124__acfix_fixlink_DepositLog.approvedToLog` certified quickly:
  `1 certified / 1 not / 2 witnessed`, about 3s.
- `bugfix124__acfix_fixlink_DepositLog.setApprovedLogger` certified quickly:
  `2 certified / 1 not / 3 witnessed`, about 12s.
- `peer182__peer_ccsolbmc__AIRBets.initialize2` was `NO-PATH` quickly.
- `peer182__peer_ccsolbmc__AIRBets.transfer` stayed
  `NO-WITNESS-UNDECIDED`; the recursive-helper preflight avoided wasting a
  full ESBMC run.
- `stress243__ERC-3643__ERC-3643__ClaimTopicsRegistry.init` and
  `addClaimTopic` hit the outer runner timeout at 60s.
- `addClaimTopic` still produced useful partial artifacts:
  `cov-ce-journal.json` had `claims_decided=6`, `claims_total=277`,
  `partial=true`, one path witness (`path:31`) and 8 concrete witness inputs.
  This is a solver/enumeration/probe-volume bottleneck, not the earlier
  frontend abort class.

Important diagnosis:

- Before this fix, `unit_schedule_run.py --timeout`, `certify_all.py
  --timeout`, and `certify_all.py --run-timeout` were all set to the same
  attempt budget (60/120/600).  The outer runner could kill `certify_all.py`
  exactly when the internal ESBMC run timed out, losing status rows and partial
  journals.
- Keep the agreed ESBMC budgets unchanged: 60s/8GiB, 120s/8GiB,
  600s/10GiB.  Add only wrapper grace:
  `certify_all.py --timeout = ESBMC run timeout + 10s`, and
  `unit_schedule_run.py --timeout = certify timeout + 5s`.
- Attempt-1 generated schedules therefore show `--timeout 70
  --run-timeout 60`, and the outer runner shows `--timeout 75.0`.  Attempt-3
  shows `--timeout 610 --run-timeout 600`, outer `615.0`.
- `benchmark_pipeline_plan.py` now forwards `runner_timeout_s` into
  `next_action`, so copyable commands expose the distinction.

Current go/no-go for broad benchmark testing:

- Ready for a small stratified benchmark sample across peer/bugfix/stress using
  the corrected wrapper timeouts.
- Not ready for full broad benchmark yet.  Stress contracts can produce partial
  witness data but may spend the first 60s solving hundreds of path-cov claims;
  use the corrected logging first, then decide whether to reduce probe volume
  or add a partial-journal salvage path before running many units.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  notes/coverage/scripts/unit_campaign_plan.py
  notes/coverage/scripts/benchmark_pipeline_plan.py
  scripts/test_unit_campaign_plan.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/unit_campaign_plan.py
  notes/coverage/scripts/benchmark_pipeline_plan.py
  scripts/test_unit_campaign_plan.py scripts/test_benchmark_pipeline_plan.py`
  passed.

## 2026-08-06 benchmark grace sample and partial-journal tracking

After the timeout-layering fix, ran a small cached benchmark sample.  All
outputs were under `/tmp`; datasets/contracts were not modified.

Plans:

- Combined peer/bugfix plan:
  `/tmp/veriput_bench_grace_sample_20260806_233358`.
- Stress-only plan:
  `/tmp/veriput_bench_grace_stress_20260806_233417`.

Executed with attempt-1 semantics: ESBMC `--run-timeout 60`, certify wrapper
`--timeout 70`, outer runner `--timeout 75`, memory 8GiB, jobs 1.

Results:

- bugfix sample, first 3 jobs: 3/3 runner-ok and 3/3 certified.
  - `DepositLog.approvedToLog`: `CERTIFIED`, `1 certified / 1 not /
    2 witnessed`, about 3s.
  - `DepositLog.setApprovedLogger`: `CERTIFIED`, `2 certified / 1 not /
    3 witnessed`, about 12s.
  - `EtherLotto.play`: `CERTIFIED`, `1 certified / 2 not / 3 witnessed`,
    about 23s.
- stress sample, first 3 jobs on `ClaimTopicsRegistry`: 3/3 runner-ok, but all
  three driver verdicts were `KILLED` at 60s with no certified regions:
  `init`, `addClaimTopic`, `removeClaimTopic`.
- For stress, wrapper grace worked: `unit-run-stress3.jsonl` has complete rows
  instead of outer-runner timeouts.
- `addClaimTopic` and `removeClaimTopic` left `cov-ce-journal.json` partial
  artifacts.  `addClaimTopic`: `claims_decided=6`, `claims_total=277`, 1 path
  witness with 8 concrete witnesses.  `removeClaimTopic`: `claims_decided=13`,
  `claims_total=227`, 1 path witness with 8 concrete witnesses.  `init` had no
  surviving journal.

Code retained:

- `certify_all.py` now reads a fresh `cov-ce-journal.json` from the unit
  workdir and writes a compact `partial_witness_journal` summary into the JSONL
  row.  This is refutation-only evidence; it does not change `bucket`, does not
  add certified regions, and does not promote KILLED to CERTIFIED.
- `unit_campaign_plan.py` treats rows with only partial witnesses as weak with
  reason `partial witness journal only`, so they are retryable under the
  60/120/600 campaign policy.
- Quality matching now accepts the real prepared-subject shape where
  `certify_all.py` writes `benchmark=<benchmark_key>` but the schedule job uses
  `benchmark=<population>` plus `subject_id=<subject>`.
- `benchmark_pipeline_plan.py` auto-enables the quality gate from an existing
  default `certify-results.jsonl` under `--out-dir`, and prioritises runnable
  unit campaigns over AST preheat when some cached units are already schedulable.
  Full denominator can still be preheated later; speed/debug iterations no
  longer get forced into AST preheat first.

Measured planner check:

- Replanning the old stress sample with
  `--journal unit-run-stress3.jsonl --cert-jsonl certify-results.jsonl` now
  reports `cert_quality_enabled=true`, `cert_weak={"no certified regions": 3}`,
  `pending_by_attempt={"2": 3}`, and `next_action=run-unit-campaign` with
  attempt 2 budget (`timeout_s=120`, runner `135.0`).
- Future stress runs made after this commit should report
  `partial witness journal only` instead of only `no certified regions` when
  the partial journal survives.

Current speed diagnosis:

- bugfix is ready for broader sampling.
- stress should not be full-swept yet.  The bottleneck is not a frontend crash;
  it is stage-1 path/probe enumeration volume.  Each solver claim is usually
  cheap, but hundreds of claims plus setup consume the 60s window before a
  complete report appears.
- Next useful work is either:
  1. one 120s/8GiB second-attempt sample on these 3 stress jobs, now that
     campaign requeues them correctly; or
  2. implement true journal-to-witness salvage in `solidity_path_generalise.py`
     so partial `cov-ce-journal.json` can seed candidate regions without waiting
     for a complete `cov-report.json`.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_guards.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  notes/coverage/scripts/certify_all.py
  notes/coverage/scripts/unit_campaign_plan.py
  notes/coverage/scripts/benchmark_pipeline_plan.py
  scripts/test_certify_all_partial_journal.py
  scripts/test_unit_campaign_plan.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `git diff --check` on the changed scripts/tests passed.

## 2026-08-06 recursive-helper preflight for witness discovery stalls

Context:

- `AIRBets.transfer` in the Peer sample was killed at the first 60s/8GiB
  attempt before any `cov-report.json` or `generalise-result.json` was written.
  That is not a region/R1/R2 failure: the run never reached witnessed-path
  enumeration.
- Static source/AST ground truth: the target's call closure reaches flattened
  SafeMath helpers shaped as unconditional direct self-recursive wrappers:
  `SafeMath.sub/2` and `SafeMath.div/2` are literally `return sub(a, b);` /
  `return div(a, b);` in that flattened input. `AIRBets.approve` does not hit
  this preflight.

Change retained:

- `scripts/solidity_path_generalise.py` now has
  `direct_recursive_helpers_in_unit_closure(ast, contract, unit)`.
- Before starting ESBMC enumeration, the driver refuses only this narrow shape:
  a function/helper whose whole body is `return f(args...)` with the same
  name/arity, and only when it is reachable from the target unit's AST call
  closure.
- The call closure follows solc `referencedDeclaration` IDs. Calls without a
  reference ID are ignored by the closure rather than guessed by global
  name/arity, because false negatives only lose this speed guard while false
  positives would pollute benchmark rows.
- The refusal prints the existing machine-readable empty-witness line:
  `[enumerate] no witnessed path for this unit, ⛔ and it is NOT a result: ...`
  so `notes/coverage/scripts/certify_all.py` classifies it as
  `NO-WITNESS-UNDECIDED`, not `KILLED` or `CRASHED`.
- Escape hatch: `--allow-recursive-helper-enumeration`. The flag is recorded in
  `run-config.json`, so runs with and without the preflight cannot reuse a
  workdir silently.

Semantics:

- This is refutation/triage only. It does not prove unreachable paths and does
  not certify a PUT. It merely avoids spending an ESBMC path-discovery timeout
  on a flattened helper with no source-level base case.
- Ordinary recursive functions with a base-case shape are not refused by this
  check.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/certify_all.py`
  passed.
- `git diff --check` passed.
- Pure AST check on the real AIRBets AST:
  `transfer -> ['SafeMath.div/2', 'SafeMath.sub/2']`, `approve -> []`.

Next:

- It is now reasonable to start a small, budget-clean benchmark sample. Use
  newly generated schedules or `unit_campaign_plan.py`'s `next-unit-schedule`;
  do not reuse the old `/tmp/veriput_sample_v10_20260806_212550/*.json`
  schedules for official measurements because their job argv did not carry the
  first-attempt inner budget.

## 2026-08-06 budget-clean benchmark smoke

Run root:

- `/tmp/veriput_budgetclean_v10_20260806_214820`
- Reused only `/tmp` unit manifests and AST cache from the earlier diagnostic
  sample; no Dataset/Results file was modified.
- Every certification row records `unit_timeout_s=60`, `run_timeout_s=60`,
  `memlimit_gib=8`.
- Runner was serial with outer `--timeout 75 --memlimit-gb 8`.

Results:

- BugFix / `DepositLog.approvedToLog`:
  `CERTIFIED`, `2 certified / 0 not / 2 witnessed`, 3.2s.
- BugFix / `DepositLog.setApprovedLogger`:
  `CERTIFIED`, `3 certified / 0 not / 3 witnessed`, 15.6s.
- Stress / `AgentRole.addAgent`:
  `KILLED`, `0 certified / 0 not / 5 witnessed`, 60.0s. The log says Level0
  had decided all 5 paths at 1.4s, so this is not a path-discovery problem; it
  is certification/refinement work exceeding the first-pass budget.
- Peer / `AIRBets.initialize2`:
  `NO-PATH`, 0.9s.
- Peer / `AIRBets.transfer`:
  `KILLED`, witnessed unknown, 60.0s. This unit does not even reach the
  witnessed-path accounting within the first-pass budget.

Interpretation:

- The campaign plumbing is now reliable enough for small-scale benchmark
  sampling.
- Full benchmark is still premature. The first-pass sample already separates
  three bottlenecks:
  easy strong cases (`DepositLog`), certification-after-Level0 budget failures
  (`AgentRole.addAgent`), and pre-witness heavy units (`AIRBets.transfer`).
- Next optimization target should be the `AgentRole.addAgent` shape first,
  because it has witnesses and Level0 decisions early. That makes it a better
  debugging target than `AIRBets.transfer`, where the failure is earlier in
  generation/enumeration/symex.

## 2026-08-06 AgentRole region-policy update

Problem shape:

- Stress / `AgentRole.addAgent` was the cleanest first-pass failure:
  5 witnessed paths and Level0 decisions appeared in about 1.4s, but v10 still
  timed out at 60s with `0 certified / 0 not / 5 witnessed`.
- The v10 free coordinates were `_agent`, `msg.value`, and `state._owner`.
  All witnessed paths agreed on `msg.sender == 4294967295`, but
  `--env-coord-disagreed` only promoted environment quantities that disagreed,
  so the owner/sender guard was certified with `msg.sender` unconstrained.

Code and recipe changes:

- `solidity_path_generalise.py` now supports
  `--pin-agreed-establishable-env`. It pins only environment quantities the PUT
  emitter can reproduce and on which every witnessed path agrees, such as
  `msg.sender`, `block.chainid`, `block.timestamp`, `block.number`,
  `block.basefee`, `block.prevrandao`, `block.coinbase`, and `tx.gasprice`.
  Unsupported environment values such as `tx.origin`, `msg.data`, `msg.sig`,
  and `block.gaslimit` stay unconstrained instead of becoming certified regions
  that a PUT cannot establish.
- `certify_all.py` forwards the new flag and records
  `pin_agreed_establishable_env` on every certification row.
- The shared recipe went through two diagnostic versions:
  - `veriput-strong/11`: kept `--no-auto-pin-value` and added the agreed
    establishable env pin.
  - `veriput-strong/12`: keeps the v11 env pin but removes
    `--no-auto-pin-value` from the main benchmark recipe, restoring the
    default nonpayable `msg.value == 0` body slice. The value-gate arm remains
    available by explicitly passing `--no-auto-pin-value`, but it is no longer
    the main benchmark route.

Read-only benchmark confirmation:

- v11 single-unit retry:
  `/tmp/veriput_v11_agentrole_20260806_220203`.
  Result: `KILLED`, `0 certified / 0 not / 5 witnessed`, 60s. It correctly
  pinned `msg.sender` and split owner/body paths:
  - enc 12: `state._owner in [0, 4294967294]`
  - enc 14: `state._owner == 4294967295`
  - enc 55: `state._owner in [0, 4294967294]`
  - enc 63: `state._owner == 4294967295`
  but leaving `msg.value` free to cover the ABI value-gate path kept the unit
  too expensive for first-pass certification.
- v12 single-unit retry:
  `/tmp/veriput_v12_agentrole_20260806_220529`.
  Result: `CERTIFIED`, `4 certified / 1 not / 5 witnessed`, 45s under
  `60s/8GiB`. The not-certified path is enc=2, the nonpayable ABI value-gate
  reject path, explicitly excluded by the `msg.value == 0` slice. The four body
  paths were certified with two free coordinates, `_agent` and `state._owner`,
  and pins including `msg.value == 0` and `msg.sender == 4294967295`.

Current interpretation:

- This is not an ESBMC internal modeling bug and not an instrumentation failure.
  The decisive gap was the certification policy: agreed, PUT-establishable
  environment values need to be pins, and the main benchmark recipe should be
  body-first rather than value-gate-first.
- v12 is ready for a small stratified benchmark sample. It is still too early
  for a full benchmark sweep: `AIRBets.transfer` remains a pre-witness heavy
  case, and value-gate coverage should eventually be handled as a separate
  recorded arm or structural ABI-gate path class rather than by slowing every
  main-recipe unit.

## 2026-08-06 benchmark population handoff

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` contracts were not modified.
- `/home/samson/workspace/VeriPUT/Results` files were not modified.
- All checks here were read-only Python/CSV/JSON manifest reads. No solc,
  Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run was
  started.

Ground truth:

- `peer182` is the prepared Peer population under
  `Results/Peer182/subjects`, and the current manifest reports 182 usable
  `contracts_080` subjects. The old non-080 peer arm is intentionally ignored.
- `bugfix124` is the prepared BugFix population under
  `Results/BugFix124/subjects`, and the current manifest reports 124 usable
  fix/bug pairs.
- `stress203` / `real203` is not the same as "all stateful rows in
  `Datasets/Stress-Projects/TARGETS.csv`". The CSV has 243 rows, 242
  `include=yes` rows, and 213 `STATEFUL` rows. The RQ1 population is the
  prepared-ok subset under `Results/Stress243/subjects`: 203 rows with
  `meta.status == ok` and `flat.sol` present. The remaining prepared subject
  dirs are 32 `compile-failed` and 7 `flatten-failed`.
- The 203 usable stress subjects include 175 `STATEFUL`, 12 `CONFIG_ONLY`,
  7 `UNDETERMINED`, 1 `MIXIN`, and 8 `LIB_LIKE` targets by TARGETS metadata.
  This matches the existing Stress runner's `usable_subjects()` rule and the
  VeriPUT notes that report "243 prepared -> 203 usable".

Code consequence:

- `notes/coverage/scripts/target_manifest.py` now keeps the old `stress243`
  behavior for TARGETS-level auditing, but treats requested `stress203` as the
  prepared-ok population by consulting
  `Results/Stress243/subjects/*/meta.json`.
- For `stress203`, `--stress-scope` no longer accidentally narrows the
  population to the 213/175 stateful view; it selects the same 203 subjects as
  the existing RQ1 runner.
- `scripts/test_target_manifest.py` has a fixture that includes a STATEFUL
  compile-failed stress target and a CONFIG_ONLY prepared-ok target, locking in
  the distinction between `stress243` TARGETS auditing and `stress203`
  prepared-ok evaluation.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/target_manifest.py notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_target_manifest.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_target_manifest.py`
  passed: 2/2 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- Read-only real-tree smoke:
  `target_manifest.build_manifest(root, ["stress203"], "stateful")` and
  `target_manifest.build_manifest(root, ["stress203"], "include")` both report
  203 ok stress targets, while `["stress243"], "include"` reports 242 ok
  TARGETS rows.

## 2026-08-06 `block.timestamp` PUT establishment

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` contracts were not modified.
- `/home/samson/workspace/VeriPUT/Results` files were not modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Ground truth:

- Read-only source scans over prepared `flat.sol` files showed
  `block.timestamp` is common: roughly 48 Peer subjects, 52 BugFix subjects,
  and 108 Stress prepared flat files mention it.
- Common patterns include cooldown/time-lock writes such as
  `cooldown[to] = block.timestamp`, `unlockTime = block.timestamp + delay`,
  and state updates guarded by current time.
- Previously the PUT emitter treated `block.timestamp` like an unestablishable
  environment coordinate. Any certified region containing a singleton or wide
  timestamp slice could be refused even though Foundry can set call-time
  timestamp with `vm.warp`.

Code shape:

- `scripts/solidity_path_put.py` now treats `block.timestamp` as an
  establishable environment coordinate alongside `msg.sender` and the existing
  narrow `msg.value` low-level-call rewrite.
- A singleton timestamp region or pin emits `vm.warp(<value>)` before the
  target call.
- A wide timestamp region emits a `uint256 p_block_timestamp` PUT parameter,
  applies the certified `bound()` plus any holes, and calls
  `vm.warp(p_block_timestamp)`.
- The insertion keeps `vm.prank` as the last cheatcode before the target call:
  `vm.warp` is inserted before the governing prank when one exists.
- Other environment coordinates such as `tx.origin` and most `block.*` values
  still fail closed unless a dedicated establishment mechanism is added.
- Source-R2 now recognizes `block.timestamp` as a numeric environment endpoint
  when it is present in the rendered coordinate set, enabling source-prioritized
  candidates for patterns such as `stamp = block.timestamp` and
  `total += block.timestamp`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 180/180 tests.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 `block.number` PUT establishment

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` contracts were not modified.
- `/home/samson/workspace/VeriPUT/Results` files were not modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Ground truth:

- Read-only scans showed `block.number` is less frequent than timestamp but
  still present in prepared subjects: roughly 5 Peer subjects, 9 BugFix
  subjects, and 2 Stress prepared flat files mention it.
- The main expected shapes are block-height guards and bad-randomness /
  old-blockhash cases. These are useful for real-bug regression because a PUT
  that cannot establish block height may be forced back to a single concrete
  replay or refused as outside the certified environment slice.

Code shape:

- `scripts/solidity_path_put.py` now uses the same block-environment helper for
  `block.timestamp` and `block.number`.
- A singleton block-number region or pin emits `vm.roll(<value>)` before the
  target call.
- A wide block-number region emits a `uint256 p_block_number` PUT parameter,
  applies the certified `bound()` plus any holes, and calls
  `vm.roll(p_block_number)`.
- `vm.roll` is inserted before a governing `vm.prank`, preserving the invariant
  that the prank remains the last cheatcode before the target call.
- Source-R2 now recognizes `block.number` as a numeric environment endpoint
  when it is rendered, enabling candidates such as `height = block.number` and
  `total += block.number`.
- `tx.origin` remains fail-closed; this change does not rely on Forge prank
  overload semantics for origin.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 182/182 tests.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 struct-contained mapping source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- A read-only Dataset scan showed many real contracts use state shapes such as
  `voters[msg.sender].weight`, `_roles[role].members[account]`, and
  `vaultBatchingState.userDeposits[receiver]`.
- Before this change, `storage_layout()` handled top-level mappings and
  top-level struct scalar fields, but not mappings stored inside a top-level
  struct.
- The slot-name parser also only accepted a bare mapping base (`bal[k].field`),
  so a correct coordinate like `vault.userDeposits[who].amount` could not be
  parsed for pin/oracle rendering.
- Source-R2's `slot_lhs()` likewise stopped at an identifier-backed state var
  and could not treat `vault.userDeposits` as the mapping base.

Code change:

- `parse_slot_name()` now accepts a dotted mapping base before the key list,
  while keeping the post-key member tail separate:
  `vault.userDeposits[who].amount` parses as base `vault.userDeposits`, key
  `who`, tail `.amount`.
- Added shared mapping-layout expansion for top-level mappings and mappings
  contained inside top-level structs, including nested mapping key chains and
  packed struct-valued mapping fields.
- Top-level struct layout expansion now recurses through nested inplace struct
  members for scalar fields and separately records mapping members in `maps`.
- Source-R2 now reconstructs a dotted state path from `MemberAccess` chains, so
  both direct access and storage aliases resolve to the same coordinate:
  `vault.userDeposits[who].amount`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 175/175 tests.
- `git diff --check -- scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed before this note update.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 enum state-machine source-R2

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` stayed read-only. The peer/stress
  contracts were only scanned as ground truth, and any solc AST inspection used
  `/tmp`.
- No POC ESBMC attempt was consumed. This change was verified with pure Python
  tests only.

Ground truth:

- The peer dataset contains many enum state machines such as
  `BasicProvenance.StateType` and `RefrigeratedTransportation.StateType`.
- A source assignment like `State = StateType.Completed` should seed an exact
  R2 query `State: post == 2`, where `2` is the enum member ordinal in the
  declaration. This is stronger than merely proving the path is reachable and
  is useful for mutation/vulnerability regression because state-transition
  bugs usually change the final enum state.
- `delete State` should seed `State: post == 0`, matching Solidity's default
  enum value.
- This enhancement deliberately does not make enum calldata parameters fuzz
  parameters. `lift_kind()` still only emits bounded Foundry parameters for
  bool/address/uint. Enum parameter support needs a separate renderer decision
  because the PUT signature and `bound()` cast rules are different.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now indexes
  enum member ids for the selected contract/base scopes and records each
  member's declaration ordinal.
- `literal_term()` recognizes enum `MemberAccess`/`Identifier` nodes whose
  `referencedDeclaration` matches an indexed enum member and whose
  `typeString`/state type agree, then emits a literal ordinal term.
- `zero_term()` now treats enum state/storage slots as reset-to-zero values.
- `type_coord_kind()` treats enum types as identity-like (`id`) for source-R2
  equality. This is only for source/query typing; it does not lift enum
  calldata in the final Foundry PUT.
- Return terms of enum type can use enum literal RHSs as identity literals, but
  the high-value path for this change is state-slot assignment.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 176/176 tests.
- Read-only smoke on `/tmp/BasicProvenance.solast` generated from the peer
  dataset contract produced `['2']` for `BasicProvenance.Complete` with
  evidence `State: post == StateType.Completed`.

## 2026-08-06 `_msgSender()` source-R2 alias

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` stayed read-only.
- No POC ESBMC attempt, benchmark certification run, Forge, or fuzz run was
  started.

Ground truth:

- The peer `contracts_080` set contains many OpenZeppelin-style
  `_msgSender()` uses in owner, approve, allowance, balance, and anti-bot
  logic. A read-only scan showed examples in `CyberFox.sol`, `SOTH.sol`,
  `ShibaJail.sol`, `Lunar.sol`, `EStack.sol`, `Thicc.sol`, and others.
- For source-R2 purposes, `_msgSender()` should be treated as `msg.sender`
  only when the AST proves the helper is the canonical no-arg address function
  whose only body statement is `return msg.sender;`.
- This remains gated by rendered environment coordinates for RHS equality:
  `owner = _msgSender()` is mined only when `msg.sender` is in
  `rendered_coords`. Existing mapping-key semantics are preserved:
  `_balances[_msgSender()] = amount` can still name the exact slot as
  `state._balances[msg.sender]`, and later verifier/emitter stages decide
  whether that slot is usable for the concrete PUT.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now indexes
  function definitions in the selected contract/base scopes.
- It registers a `msg.sender` alias only for no-arg functions with one
  `address` return parameter and a single `Return` expression equal to
  `msg.sender`.
- `env_coord_name()` recognizes calls to those helper ids as `msg.sender`,
  which lets existing source-R2 assignment/return logic mine
  `post == msg.sender` without adding a new term kind.
- `key_name()` now reuses `env_coord_name()`, so canonical helper calls can
  serve as mapping slot keys under the existing `msg.sender` slot-key text.
- Nontrivial address helpers such as `return owner;` are refused closed.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 177/177 tests.
- `git diff --check` passed before this note update.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 one-level helper-call source-R2 inlining

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` stayed read-only.
- No POC ESBMC attempt, benchmark certification run, Forge, or fuzz run was
  started.

Ground truth:

- ERC20/OZ-style public units often expose their semantic storage write only
  through an internal helper. Example:
  `approve(spender, amount) -> _approve(_msgSender(), spender, amount)`, while
  `_approve(owner, spender, amount)` writes
  `_allowances[owner][spender] = amount`.
- A strong PUT should prioritize the exact mapping-slot R2 query
  `_allowances[msg.sender][spender]: post == amount` rather than relying on a
  broad mechanical candidate product to rediscover it.
- This is not proof. The source miner only moves that row to the front of the
  existing R2 batch; ESBMC still certifies and fuzz can only refute.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now indexes
  function definitions in the selected contract/base scopes by declaration id.
- When the target body contains a source-level `FunctionCall` whose callee id
  resolves to one of those functions, the miner performs one-level effect
  inlining: formal parameter ids are temporarily aliased to actual argument
  AST expressions, then the callee body is walked by the existing source-R2
  assignment logic.
- The inliner is deliberately bounded at depth 1 and restores all local alias
  state afterward, so helper-local aliases cannot leak into the caller and
  recursive/helper chains are not expanded indefinitely.
- This composes with the previous `_msgSender()` alias and mapping-slot key
  recovery, so `_approve(_msgSender(), spender, amount)` yields
  `allowances[msg.sender][spender]: post == amount`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 178/178 tests.

## 2026-08-06 SafeMath-style source-R2 expressions

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` stayed read-only.
- No POC ESBMC attempt, benchmark certification run, Forge, fuzz, or solc run
  was started.

Ground truth:

- Peer `contracts_080` still contains many SafeMath-style calls even under
  Solidity 0.8, especially ERC20-like paths:
  `_allowances[sender][_msgSender()].sub(amount)`,
  `_balances[recipient].add(amount)`,
  `_tTotal.mul(maxTxPercent).div(...)`, and return helpers such as
  `rAmount.div(currentRate)`.
- Without recognizing method-call arithmetic, source-R2 sees `FunctionCall`
  and misses strong rows such as:
  - `allowance[msg.sender][spender]: pre - post == amount`;
  - `balances[to]: post - pre == amount`;
  - `return == ((amount * 3) / 2)` for chained quote-style helpers.
- These are still only prioritized candidates. ESBMC certifies them; fuzz can
  only refute.

Code shape:

- `scripts/solidity_path_put.py` now has `method_op_parts()` inside
  `source_assignment_r2_specs()` for one-argument member calls named
  `add`, `sub`, `mul`, or `div`.
- `numeric_endpoint_term()` translates those calls into the existing
  structured R2 `op` term. Chained calls are supported by recursion, with
  division accepted only when the divisor term is a nonzero literal.
- `sub(x, "error")` and `div(x, "error")` are accepted as SafeMath overloads
  by ignoring the second argument only when it is a source string literal.
  `add`/`mul` still require one argument.
- `self_update_delta()` recognizes `slot.add(x)` and `slot.sub(x)` when the
  method receiver is the same scalar/mapping slot being assigned, so the miner
  emits exact inc/dec deltas in addition to endpoint equalities.
- `return_term()` recognizes the same method-call arithmetic for single-value
  returns.
- No SafeMath `require` semantics are modeled in Python; this is syntax-driven
  candidate prioritization only.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 178/178 tests.
- `git diff --check` passed before this note update.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

Important caveat found immediately afterward:

- This change proves the external tool can name, propose, and render Foundry
  storage-slot oracles for `vault.userDeposits[who].amount`.
- It does NOT yet prove ESBMC's internal `--path-cov-assert` ladder can certify
  such dotted mapping bases. Internal `goto_coverage.cpp` currently resolves
  mapping slots through `store_syms`, while Solidity frontend comments indicate
  struct-internal mappings use a `mapping_t` field plus an
  `_ESBMC_inf_<path>` backing pool. That may require an internal verifier
  extension or an external guard before using these candidates in benchmark
  runs.
- Do not count this item as end-to-end success until a small synthetic ESBMC
  ladder run confirms that `state.vault.userDeposits[who].amount` is accepted
  and produces judged rows.

## 2026-08-06 struct-contained mapping fail-closed guard

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- Reading `src/goto-programs/goto_coverage.cpp` showed the
  `--path-cov-assert` slot ladder resolves mapping slots through
  `store_syms`, which is built from contract-scope globals named under
  `sol:@C@<Contract>@...`.
- Reading Solidity frontend mapping code showed struct-internal mappings are
  initialized as `mapping_t` fields backed by `_ESBMC_inf_<path>` globals, not
  as a contract-scope store naturally named `vault.userDeposits`.
- Therefore the previous external naming/layout support for
  `vault.userDeposits[who].amount` is useful renderer groundwork but must not
  be sent to ESBMC's current ladder as a certifiable candidate.

Code change:

- Added `map_esbmc_certifiable()`, `queryable_mapping()`, and
  `esbmc_certifiable_maps()` in `scripts/solidity_path_put.py`.
- The main PUT flow now derives `query_maps` from full solc `maps` and uses
  `query_maps` for:
  - certified-region slot reuse,
  - dependency-driven slot proposals,
  - region and pin entries passed to `--path-cov-assert`,
  - R2 variable width lookup,
  - source-assignment R2 mining.
- `storage_layout()` can still identify dotted mapping bases, and
  `parse_slot_name()` can still parse them, but `propose_slot_vars()` now
  skips them with an explicit message until ESBMC gains internal `mapping_t`
  ladder support.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 175/175 tests.
- `git diff --check -- scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed before this note update.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 enum mapping-key source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- R2 endpoint typing already treats enums as identity coordinates, so enum
  parameters can be lifted and carried through PUT signatures.
- The storage-layout mapping key whitelist still rejected solc labels such as
  `enum C.Status`, so `mapping(Status => uint256)` slots never reached
  `maps`, slot proposals, source-R2 candidates, or the PUT renderer.
- The same whitelist also accidentally accepted dynamic `bytes`, despite the
  adjacent comment explaining why dynamic mapping keys must not be addressed
  with `abi.encode`.

Code change:

- Added `map_key_type_ok()` and allowed solc enum key labels in the value-type
  mapping-key whitelist.
- Tightened the `bytes` branch to admit only fixed `bytes1` through `bytes32`;
  dynamic `bytes` remains refused because its storage-key hashing rule is not
  the value-type `abi.encode(key, slot)` rule used by the PUT slot oracle.
- No special renderer path was needed: existing `slot_key_expr()` accepts a
  declared parameter and `map_slot_expr()` emits `abi.encode(param, slot)`,
  which is the correct value-type key rule for enum parameters.
- The synthetic AST ground truth now checks that
  `mapping(enum C.Status => uint256) byStatus; byStatus[s] = amount; return
  byStatus[s];` yields `byStatus[s]: post == amount` and
  `return == state.byStatus[s]`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 173/173 tests.
- `git diff --check -- scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed before this note update.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 nested mapping storage alias source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- The storage-alias source-R2 support covered `Bal storage row = bal[who]`;
  `row.amount` then resolved to `bal[who].amount`.
- A related nested-mapping idiom still needed a key-preservation rule:
  `mapping(address => uint256) storage inner = two[token]; inner[who] = amount`.
- Expanding only the alias base would lose the later `who` index. The correct
  coordinate is `two[token][who]`.

Code change:

- When `slot_lhs()` expands a local storage alias, it now reapplies the
  original expression's remaining `IndexAccess` nodes after the alias base and
  before any member tail.
- This makes source-R2 mining work for nested mapping storage aliases while
  retaining the prior struct-field alias behavior.
- The new synthetic AST test checks both setter and return candidates:
  `two[token][who]: post == amount` and
  `return == state.two[token][who]`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  172/172 tests.
- No POC ESBMC attempt was consumed.

## 2026-08-06 storage local alias source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- Real Solidity code often writes through local storage aliases, for example
  `Box storage b = box; b.count = amount` or
  `Bal storage row = bal[who]; row.amount += amount`.
- The source-first R2 miner already handled direct
  `box.count` / `bal[who].amount` spellings, but it did not connect the local
  storage pointer back to the original state coordinate.
- That lost strong candidates such as `post == amount`,
  `post == state.bal[who].amount + amount`, and exact deltas on mapping-value
  struct fields.

Code change:

- `source_assignment_r2_specs()` now keeps a separate `local_storage_aliases`
  table for local declarations or rebinding assignments whose AST says
  `storage`.
- `slot_lhs()` and `state_member_lhs()` expand only those storage aliases before
  resolving state coordinates.
- Ordinary local aliases still feed numeric/source terms as before, but memory
  aliases such as `Box memory b = box` are deliberately not treated as state
  writes or entry-state reads.
- Alias invalidation now clears the storage-alias table alongside the ordinary
  alias table on mutation/delete, preventing stale coordinates.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  171/171 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- No POC ESBMC attempt was consumed.

## 2026-08-06 top-level struct member source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- Mapping values whose leaf is a struct were already expanded into per-field
  coordinates such as `bal[k].amount`.
- Top-level struct state variables were still skipped by `storage_layout()`
  because the previous scalar-slot branch rejected any solc type with
  `members`.
- That meant source-level units such as `return box.count`,
  `box.count = amount`, and `box.count = box.count + amount` could not enter
  the source-first R2 queue even though the PUT renderer and verifier can read
  the underlying slot word once the coordinate is named.

Code change:

- Added `_storage_layout_struct_members()` and made `storage_layout()` expand
  top-level struct scalar fields as `box.field` layout entries.
- The expansion uses `base_slot + member.slot`, `member.offset`, and
  `member.numberOfBytes`, and still skips aggregate or dynamic members instead
  of guessing an unreadable word.
- `source_assignment_r2_specs()` now recognizes direct state-member accesses
  as state coordinates on RHS and as state targets on LHS.
- The same coordinate support covers struct-field getter returns, simple
  setters, `+=`/`-=`, `box.field = box.field +/- x`, unary `++`/`--`, and
  `delete`.
- This remains a candidate-prioritization change only. Fuzz can refute these
  candidates cheaply, but ESBMC is still the proof authority.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  170/170 tests.
- No POC ESBMC attempt was consumed.

## 2026-08-06 post-state R2 entry-state endpoint rendering

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- Source R2 can now propose strong equalities such as
  `post == (state.allowance[msg.sender][spender] - amount)`.
- ESBMC can certify such structured terms, but the PUT renderer only knew how
  to spell lifted calldata/environment coordinates. A term endpoint containing
  `state.*` could therefore be proved and then dropped as "rung shape not
  rendered".

Code change:

- Added `post_rung_term_spellings()` to enumerate structured endpoint spellings
  from post-state equality, absolute, and delta R2 rungs.
- During post-state oracle rendering, every `state.*` coordinate mentioned by a
  structured R2 term is materialized as an entry-state pre-read before the unit
  call.
- Scalar state coordinates use `slot_read_expr`; mapping/member coordinates use
  the existing safe `parse_slot_name` / `slot_key_expr` / `map_slot_expr` /
  `slot_read_expr_at` machinery.
- The materialized pre-read is inserted into both `coord_ident` and
  `coord_ident_abs`, so equality/absolute/delta endpoints can all spell it.
- Existing own-variable pre-reads also register `state.<var>` as the entry
  coordinate, avoiding duplicate reads on self-referential structured terms.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  168/168 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 local alias invalidation for source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- The local-alias source miner remembered simple aliases such as
  `uint fee = amount`, but it could keep using that alias after `fee += 1`,
  `fee++`, or `delete fee`.
- Such a stale candidate would still need ESBMC certification and therefore
  would not become a proof by itself, but it wastes R2 candidate budget and can
  crowd out stronger candidates on real benchmark units.

Code change:

- `source_assignment_r2_specs()` now invalidates a local alias when the local is
  assigned with any non-`=` operator.
- Unary `++`, `--`, and `delete` on a known local also invalidate its alias.
- A later plain `local = expr` still creates a fresh alias, so straight-line
  redefinitions remain mineable.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  167/167 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 mapping-slot getter return R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- ESBMC's `resolve_coord` already supports `state.<mapping>[<key>]` and nested
  mapping/member coordinates in `--path-cov-assert`.
- The external source miner did not propose `return == state.bal[k]` for getter
  bodies such as `return bal[k]` or `return bal[msg.sender]`.
- The PUT renderer also skipped mapping-slot state coordinates when rendering
  return rungs, so even a certified `return == state.bal[k]` could not become a
  Foundry assertion.

Code change:

- `source_assignment_r2_specs()` now recognizes RHS mapping slot reads as
  structured entry-state coordinates, e.g. `state.bal[who]` and
  `state.bal[msg.sender]`.
- Numeric slot reads are available through `delta_term`, so they feed return
  equality, direct equality, and one-level arithmetic R2 terms.
- Return-oracle pre-read planning now supports mapping slots by using the same
  `parse_slot_name`, `slot_key_expr`, `map_slot_expr`, and
  `slot_read_expr_at` machinery as post-state mapping oracles.
- If the return type is bool, pre-read slot words are exposed to the renderer
  as `(_ret_pre_slot != 0)` so bool return assertions remain well typed.
- Existing mapping self-subtract tests now expect the stronger additional
  equality `post == (state.allowance[msg.sender][spender] - amount)`, alongside
  the delta rung.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  166/166 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 source-R2 local alias expansion

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this source-miner change.

Finding:

- Many real Solidity units compute a local temporary and then return it or use
  it as a state/mapping delta, e.g. `uint fee = amount * 3; total += fee;`
  or `return fee`.
- Before this change, source-R2 mining only understood parameters, state entry
  coordinates, environment coordinates, constants, literals, and direct AST
  arithmetic. A local identifier hid the useful expression, so strong candidates
  were not even sent to ESBMC.

Code change:

- `source_assignment_r2_specs()` now records simple function-local aliases from
  `VariableDeclarationStatement` initializers and straight-line assignments to
  known locals.
- The existing term builders expand those aliases before mining literals,
  constants, coordinates, mapping keys, return terms, and numeric endpoints.
- Compound updates and self-updates now use the numeric endpoint miner, so
  `x += fee` and `x = x + fee` can produce one-level arithmetic delta terms
  such as `(amount * 3)`.
- Mapping keys can also use aliases, e.g. `address caller = msg.sender;
  bal[caller] += amount` names the certified slot as `bal[msg.sender]`.
- This is still a proposal-stage enhancement only: ESBMC must certify any
  emitted R2 row before the PUT renderer may turn it into an oracle.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  164/164 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 ESBMC return structured-R2 retset guard

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, POC attempt, or benchmark certification run was
  started.

Finding:

- Python could now propose source R2 rows such as `return == ok`, but ESBMC's
  bool-return path emitted only the built-in `return == false` /
  `return == true` rungs and returned before processing structured R2 equality
  candidates.
- More importantly, structured return R2 rungs for numeric returns reused the
  shared post-state emitter without the `!retset` vacuity guard. The legacy
  return rungs already guard every candidate as `!retset || claim`; structured
  return R2 must obey the same contract, otherwise an exit that reaches the
  path but does not execute a RETURN can make the ghost's default value look
  like a real returned value.

Code change:

- `emit_structured_rungs` in `src/goto-programs/goto_coverage.cpp` now accepts
  an optional `vacuous_guard`.
- State and mapping structured R2 calls keep the default empty guard, so their
  formulas are unchanged.
- Return structured R2 calls pass `no_ret`; their emitted candidates are now
  `!retset || defined(term) && relation`.
- Bool returns now emit their built-in true/false rungs and then also process
  structured bool equality candidates, enabling internally certified rows such
  as `return == ok`.

Verification:

- `cmake --build build --target esbmc -j2` passed. The build printed existing
  Solidity address model array-bounds warnings while regenerating `sol64.goto`;
  it then linked and reported `Built target esbmc`.
- `clang-format --dry-run --Werror src/goto-programs/goto_coverage.cpp`
  passed.
- `git diff --check -- src/goto-programs/goto_coverage.cpp
  notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 bool-coordinate return oracle rendering

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Finding and change:

- Source R2 mining already produced bool-coordinate return candidates such as
  `return == ok`.
- The Foundry renderer only emitted bool return rungs for `return == true` and
  `return == false`, so a certified `return == ok` rung could be dropped as
  "not renderable".
- `return_rung_assertions()` now keeps the true/false special cases and then
  renders any structured bool equality it can spell from `r2_terms`, producing
  `assertEq(_put_ret, p_ok, ...)`.
- If ESBMC/report text uses the numeric spelling for a bool literal
  (`return == 0` or `return == 1`), the renderer still emits
  `assertFalse`/`assertTrue` rather than an invalid `assertEq(bool, uint)`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  163/163 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 cast-wrapped return R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Code change:

- `source_assignment_r2_specs()` now carries the single return type into
  return-source R2 mining, using the same conservative type-conversion unwrap
  rules already used for storage and mapping assignments.
- It can mine stronger return equalities for:
  - `return uint256(amount)` when the function returns `uint256`;
  - `return uint128(amount)` when the function returns `uint128`;
  - `return address(who)` when the function returns `address`;
  - `return uint256(msg.value)` when `msg.value` is a rendered numeric coord;
  - named returns such as `out = uint256(amount)`;
  - one-level arithmetic such as `return uint256(amount) + uint256(7)`.
- It still refuses unsafe narrow-then-wide mining such as
  `return uint128(amount)` for a `uint256` return, because that is not the same
  value relation unless ESBMC proves the range side condition elsewhere.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  162/162 tests.
- `git diff --check -- scripts/solidity_path_put.py
  scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md` passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 1. Current repository state

- Working branch: `feat/veriput-fuzz-first`
- Original takeover snapshot: `5efe5b3252`
  (`[solidity] Strengthen parameterized path test synthesis`)
- Latest pushed commit when the st1inch attempt-3 replay repair began:
  `bb9443d52f` (`[solidity] Recognize all corpus benches in PUT sweep`)
- Latest pushed commit before the no-fuzz inventory classifier work:
  `a83fc1715b` (`[solidity] Explain weak VeriPUT POC oracles`)
- Pushed remote branch: `E-SOL/feat/veriput-fuzz-first`
- Snapshot checks:
  - `python3 -m py_compile scripts/solidity_path_generalise.py
    scripts/test_solidity_path_generalise.py scripts/solidity_path_put.py
    scripts/test_solidity_path_put.py notes/coverage/scripts/put_all.py`:
    passed after the pending script patch.
  - `python3 scripts/test_solidity_path_generalise.py`: passed after the
    pending script patch.
  - `python3 scripts/test_solidity_path_put.py`: 132/132 passed after the
    pending script patch.
  - `git diff --check` on the touched scripts/docs: passed after the pending
    script patch.
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile
    notes/coverage/scripts/poc_ground_truth.py
    scripts/test_poc_ground_truth.py`: passed after the no-fuzz inventory
    classifier patch.
  - `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_poc_ground_truth.py`:
    passed after the no-fuzz inventory classifier patch.
  - Read-only `poc_ground_truth.py --only ... --limit 1` smokes passed for
    `D11_Bytes32Equality.takeBytes32`, `P26_TypeMatrix.takeBool`, and
    `F02_SetterFocus.seed`. These runs did not call solc, Forge, fuzzing, or
    ESBMC.
- POC run after the script patch:
  - `aqua_Aqua__Aqua__rawBalances` Stage 2 attempt 2, 120s/8GiB:
    passed, `2 certified / 0 not / 2 witnessed`.
  - `aqua_Aqua__Aqua__rawBalances` Stage 3 attempt 2, 120s/8GiB:
    passed, `B = 2 of 2`.
- Not run after the script patch beyond rawBalances attempt 2: C++
  rebuild, CTest, or other POCs. Other experiments are using the machine, and
  a real POC run is a scarce measurement, not a compile check.
- The worktree contains many untracked generated artefacts. They were
  deliberately excluded from the snapshot. Do not delete or add them in bulk.

## 2. Authority and success criteria

The authority order for engineering decisions is:

1. The user's latest instruction in chat.
2. `/home/samson/workspace/paper_review/WORKORDER.md` for the frozen method and
   per-POC workflow.
3. `notes/VeriPUT.tex` for the paper method.
4. Existing implementation and historical notes, which may differ from the
   method and may contradict one another.

The user's current execution constraints tighten the work order:

- Make every code-level change that can be justified statically before running
  a real POC.
- Each POC may be rerun through ESBMC at most three times:
  60s/8GiB, then 120s/8GiB, then at most 600s/10GiB.
- Do not run a POC merely to answer a question already answered by logs, source,
  unit tests, a small synthetic regression, or GOTO inspection.
- `/home/samson/workspace/VeriPUT/Datasets` and the prepared `Results`
  subjects are shared with another running experiment. Treat both trees as
  read-only unless the user explicitly authorizes a preheat/write pass. In
  particular, do not modify Dataset contracts. Current readiness checks use
  stdout pipes only and pass neither `--out`, `--journal`, nor
  `--generate-ast`.
- Before spending a POC run, read the POC source and existing pathcov artefacts
  and write down the expected path, input region, slot region, and assertion
  oracle. Treat this as the ground truth for debugging.
- Current official per-POC retry ladder:
  - attempt 1: 60s ESBMC timeout, 8 GiB memlimit;
  - attempt 2: 120s ESBMC timeout, 8 GiB memlimit;
  - attempt 3: 600s ESBMC timeout, 10 GiB memlimit.
- The eventual global generalisation target is at least 70%. This is a delivery
  threshold, not permission to abandon the remaining paths without attribution.
- Fuzz is a refutation tool only. It may cheaply reject a bad region, bad
  instrumentation, bad fixture, or bad R1/R2 candidate by finding a concrete
  violating execution. It must never be counted as proof that a region or
  assertion universally holds.

The only deliverable counted as B is a generated `.t.sol` for a real corpus
contract that simultaneously has:

1. at least one fuzz parameter;
2. a certified width greater than one, with provenance from a measured ladder
   or sibling subtraction, not only a `+/-1` neighbourhood probe;
3. at least one non-vacuous assertion over post-state or return value;
4. a green Forge run on the unmodified contract; and
5. no manual construction in its provenance.

No assertion means no PUT. A replay-only test is not B.

### Two compatible closure metrics

Do not collapse these into one number:

- **Per-POC closure:** every witnessed path must either produce B or receive a
  method-level unsupported attribution with the required evidence. This is the
  work-order's local 100% rule.
- **Global conversion:** at least 70% of supported witnessed paths should
  produce B. Unsupported paths are excluded only after an explicit, evidenced
  method attribution. Timeouts, crashes, unknowns, missing verdicts, and bad
  orchestration are not unsupported paths and stay in the denominator.

Before B becomes positive, funnel counts are diagnostic only. After that, every
reported rate must identify the binary, commit, POC configuration, scope, and
time/unwind budgets. Never aggregate rows from mixed binaries or configurations.

Recommended funnel counters are:

- witnessed paths;
- supported witnessed paths;
- paths with a certified non-point region and valid width provenance;
- certified regions that render and compile;
- rendered tests with a non-vacuous antichain oracle;
- Forge-green B artefacts.

## 3. Frozen method in implementation terms

The intended pipeline is source-only and path-scoped:

1. Enumerate complete path identities `(enc, depth)` and classify each as
   refuted, proved, or open. A bounded no-counterexample result is open, never
   proved unreachable.
2. Obtain multiple witnesses where useful, choose user-controllable coordinates,
   bracket each path's feasible values, subtract sibling regions, refine, retain
   finite holes, and certify the entire product region.
3. Generate assertion candidates and certify every candidate over that same
   region. Keep the implication antichain.
4. Reproduce the same fixture and environmental assumptions in Foundry, render
   the certified region using `bound` plus `vm.assume` for holes, and fuzz it.

The first fixture version is a mandatory three-step process:

1. ask the verifier for one completing deployment;
2. deterministically fill values absent from the witness; and
3. replay that concrete deployment once and use its resulting state for every
   unit query.

Constructors must not remain in unit queries.

The first external-behaviour model supports only EOA/no-code destinations and
deterministic no-callback stubs with fixed success/revert and fixed return data.
The current unbounded nondeterministic dispatcher re-entry is not this method.

Assertion candidates, in increasing expressive power, are:

- `post == pre`, `post != pre`;
- order and strict-order relations, with mirrors;
- absolute intervals;
- direction-safe delta intervals;
- `post == e`.

For interval endpoints and `e`, the frozen grammar at depth one mechanically
enumerates rendered coordinates, pre-state reads, declared constants, and
integer literals using sum, difference, product, and division by a literal.
It must not read symbolic path expressions or perform data-flow substitution.
Candidate definedness, including overflow, truncation, and division by zero,
must be decided in the same verifier query as the candidate.

Assertion refutation never splits a region. Only dense holes may cause a region
to be rendered as several hole-free pieces, and every piece inherits the same
already-certified assertions.

## 4. What is implemented now

The snapshot contains substantial improvements over the paper prototype:

- complete path IDs with decision sequences, multi-exit handling, return/revert
  tracking, ABI nonpayable gates, internal-call expansion, and path caps;
- outer boxes, sibling subtraction, refinement, certification, shrinking, and
  finite holes;
- arithmetic counterexample re-solving;
- R1 state ordering/change/equality ladders;
- scalar return, mapping-slot, nested-mapping, and struct-member candidates;
- rollback handling that drops unobservable post-state claims and keeps an exit
  assertion;
- implication-antichain filtering;
- multiple witness enumeration and member-anchored per-path probe ladders;
- state and environment pinning controls;
- width provenance, guarded-assertion accounting, binary provenance, refusal
  buckets, and fresh per-invocation logs;
- stage drivers and a single-POC entry point.
- read-only POC ground-truth inventory with `Contract.unit` filtering,
  per-unit status buckets, raw weak PUT evidence, and compact
  `weak_detail_tags` for no-oracle/no-fuzz triage.

The Python unit tests exercise many of these mechanisms, including state pins,
mapping members, sender/value gates, R2 proposal, rollback, antichains, guarded
assertions, and width provenance. This is implementation coverage, not proof
that the official POC entry point enables the mechanisms.

## 5. Current gaps from the frozen method

### 5.0 Aqua bytes32 mapping-key status

Static diagnosis on 2026-08-06 identified a concrete driver-side cause for the
Aqua `push`/`safeBalances` outer-refine abort class:

- The source slot access is genuinely
  `_balances[maker][app][strategyHash][token]`.
- `strategyHash` has type `bytes32`, and ESBMC reports it as a `BytesStatic`
  aggregate such as `{ .data = { 0 } }`.
- `coord_values()` is right to refuse `strategyHash` as a fuzz coordinate.
- The stage-2 slot generator was wrong to emit
  `state._balances[maker][app][strategyHash][token].tokensCount`, because the
  verifier-side slot resolver then receives an aggregate key where the Solidity
  frontend's normal mapping model uses `bytes_static_to_mapping_key`.
- The driver now computes the same key literal as the frontend,
  `(len << 248) | bytes_static_to_uint(data)`, only when witnessed CE inputs
  agree on that bytesN slice. For `bytes32(0)`, the literal is
  `0x2000000000000000000000000000000000000000000000000000000000000000`.
- ESBMC's internal path-cov coordinate resolver also now shares one
  mapping-key parser between region assumptions and assertion observables:
  decimal and `0x` literals become uint256 keys, while stale/manual aggregate
  keys such as `state._balances[...][strategyHash]` fail closed before
  building `index2tc`.
- Offline checks against the real Aqua reports now propose:
  - `push`:
    `state._balances[maker][app][0x2000...0000][token].amount` and
    `.tokensCount`;
  - `safeBalances`:
    the same literal with `token0` and `token1`, both `.amount` and
    `.tokensCount`.
- A 2026-08-06 pure replay of the existing `Aqua.push` Stage-1 report plus the
  real solc AST through the current code confirms that `push` now proposes only
  the source slot
  `state._balances[maker][app][0x2000...0000][token].{amount,tokensCount}`.
  It proposes no stale `[strategyHash]` aggregate key and no guessed
  cross-product slot such as `[maker][maker]`.

This is a source-slice restriction from the witnessed CE, not a proof and not a
new fuzz dimension. If witnessed bytesN values disagree, the slot is refused
rather than passed to ESBMC as a bad aggregate-key coordinate.

### 5.0.1 Mapping struct leaf type ranges

Static diagnosis from the first `Aqua.rawBalances` PUT attempt exposed a second
driver-side slot issue:

- Stage 2 correctly proposed the literal-key slots
  `_balances[maker][app][0x2000...][token].amount` and `.tokensCount`.
- It did not carry the struct member's elementary type into the generated
  region, so `.tokensCount` inherited the default `uint256` range
  `[0, UINT256_MAX]` instead of its declared `uint8` range `[0, 255]`.
- ESBMC then correctly refused the assertion ladder before solving:
  the high endpoint did not fit the coordinate's own type.
- The implemented script patch extends `mapping_state_vars()` with an optional
  fourth tuple element `{tail: leaf_type}` for struct-valued mappings, adds
  `mapping_slot_type_ranges()`, and seeds stage-2 `type_ranges` before the
  first ladder. This makes `Balance.amount` use `uint248` and
  `Balance.tokensCount` use `uint8` even when no counterexample payload carries
  the slot value.
- The same patch updated `solidity_path_put.py` so
  `REFUSING THE LADDER on region coordinate ...` is parsed as a refusal. A
  future ladder refusal must stop PUT emission instead of producing an
  oracle-free fuzz replay.

Validated by `Aqua.rawBalances` attempt 2:

- Stage 2 cert now records `.tokensCount in [0, 255]` and `.amount in
  [0, 2^248-1]`.
- Stage 3 no longer refuses the ladder. Path `enc=3` emits a fuzzed normal
  getter PUT with `maker/app/token` parameters and return assertions
  `return.0 == 0`, `return.1 == 0`; path `enc=2` emits the value/revert oracle.
- The B table reports `B = 2 of 2`.
- The same pure replay for `Aqua.push` now reports `.amount in [0, 2^248-1]`
  and `.tokensCount in [0, 255]` for the literal-key source slot. Its currently
  checked-in `certify_gate.jsonl` remains a stale pre-fix artefact because it
  still contains `[strategyHash]`, guessed cross-product slots, and `uint256`
  leaf ranges.

### 5.1 Fixture and environment

- The frozen three-step fixture is not fully implemented. Constructors can
  remain entangled with unit queries.
- The external-call model still permits unbounded nondeterministic re-entry.
- The emitter establishes and checks mainly sender, value, and selected state
  slots. Other verifier-side environment pins can be emitted as `NOT CHECKED`,
  which breaks assumption matching.
- A wide `state.*` region is not automatically a usable Foundry fuzz coordinate:
  the generated test cannot havoc an arbitrary deployed storage field unless a
  reproducible setter or fixture mechanism exists.

### 5.2 Region policy

- Coordinate selection is global for a unit run, but different path classes
  need different coordinates and pins.
- A Cartesian region cannot express cross-coordinate relations such as
  `msg.sender == state._owner`. The practical method-compatible first version
  is to fuzz one side and pin/reproduce the other, not to promote both into a
  rectangle.
- `--max-region-pieces` can split on certification cuts. That conflicts with
  the frozen rule that only dense holes split a region. The option is currently
  off by default and must stay out of the official recipe unless redefined.
- `--skip-bracket` can avoid the historically dominant geometric claim batch,
  but its resulting width still needs valid provenance from probes, refinement,
  or sibling subtraction.

### 5.3 R2 language

R2 is materially incomplete:

- The C++ assertion query accepts only a decimal literal or one resolvable name
  as each absolute/delta endpoint.
- The Python proposer supplies only numeric or identity function parameters.
- It asks absolute equality to a parameter, exact directional delta to a
  parameter, and a second-stage `[0, parameter]` delta cap after exact-delta
  refutation.
- It does not enumerate pre-state atoms, constants, source literals, arithmetic
  depth-one terms, or general `post == e`.
- R2 is opt-in and the official POC stage does not enable it.

Therefore the current implementation can find useful setter/deposit-shaped
properties, but it does not implement the paper's fixed candidate grammar.

### 5.4 Driver wiring

- The official `poc_one.py` stage-two path invokes `certify_all.py` defaults
  rather than a single explicitly versioned strong recipe.
- Multi-witness probes, member ladders, environment-coordinate promotion,
  agreed-state pinning, skip-bracket, and R2 are mostly available as switches
  but are not enabled coherently by the official entry point.
- A function existing behind a switch is not pipeline functionality until its
  call site enables it and records the configuration.

### 5.5 Current no-fuzz PUT attribution

The read-only inventory now preserves raw `weak_details` and also emits compact
`weak_detail_tags`. The tags are diagnostic; they do not make a weak PUT count
as B.

Static smoke classification on existing artefacts:

- `D11_Bytes32Equality.takeBytes32`: certified paths exist, but the emitted
  PUTs have no fuzz parameter. The wide region is over the derived coordinate
  `b.length` plus dropped `state.tag`. This points to a bytesN
  derived-coordinate/model gap, not a missing proof run.
- `P26_TypeMatrix.takeBool`: the checked-in PUT artefact still says `bool`
  cannot be bounded. Current `scripts/solidity_path_put.py` already supports
  bool lifting, so this is classified as a stale bool-unliftable note plus
  dropped state, not a current bool emitter bug.
- `F02_SetterFocus.seed`: the only wide region is `state.bal`, and the emitter
  drops it because entry state is not havoced by the generated test. This is a
  fixture/state-establishment problem, not a fuzz-parameter synthesis target
  under the current PUT shape.

## 6. Queue-head evidence: `farming__Distributor__setDistributor`

Do not rerun this POC yet. Existing logs answer the current design questions.

Stage one witnessed five paths: 2, 12, 13, 14, and 15.

The default certification record was killed at approximately 300 seconds with
four coordinates and no certificate:

- `distributor_`
- `state._distributor`
- `state._owner`
- `state._totalSupply`

A derived-coordinate run certified paths 12-15 in about 115 seconds by:

- using `distributor_` and `msg.sender` as coordinates;
- pinning agreed state;
- pinning `msg.value == 0`; and
- skipping the geometric bracket.

Path 2 was excluded in that run because it is the nonpayable ABI rejection path
and requires `msg.value > 0`, while the body paths require `msg.value == 0`.
A separate value-gate run certified path 2 but made the body paths expensive or
uncertifiable under its global coordinate choice.

This is strong evidence for a per-path policy, not another global flag search:

- ABI nonpayable rejection path: promote/generalise `msg.value` and do not pin
  it to zero.
- Body paths: pin `msg.value == 0`, promote `msg.sender`, and reproduce/pin the
  agreed owner state.

The two historical arms cover 5/5 paths only as separate configurations. They
do not yet constitute one reproducible official run, and their binaries and
timestamps are not a valid current yield measurement.

## 7. Why PUT generation is difficult

Low yield is not one failure mode. It must be classified before changing code.

### 7.1 Instrumentation/path-identity failures

Symptoms:

- many path bits arise from compiler safety checks or lowering details rather
  than source control flow;
- semantically one source path fragments into many identities;
- decisions depend on nondeterministic values the test cannot set;
- a source branch disappears or a reported path cannot be replayed.

Known examples from historical diagnostics:

- Escrow assembly was degraded to nondeterministic operations, inflating the
  apparent witness set with paths controlled by unrenderable values.
- Aqua received extra path distinctions from compiler/lowering checks, while
  other source distinctions could be hidden by lowering.

Current `path_decisiont` explicitly names the synthetic ABI gate but has no
general origin taxonomy. Conditional GOTOs and folded conditional assignments
can become path decisions unless marked skipped. This is insufficient to state
which decisions define source paths and which merely constrain them.

Required origin classes should include at least:

- source branch or source-level require;
- ABI dispatch/payability gate;
- compiler-inserted safety check;
- frontend/lowering helper;
- external-model decision;
- instrumentation-only decision.

Source and supported ABI decisions may define path identity. Safety checks
should constrain certification without fragmenting source paths. Lowering or
external-model nondeterminism that the Foundry test cannot reproduce must be
modeled or explicitly attributed as unsupported, never silently treated as a
test coordinate.

### 7.2 Region-policy failures

Symptoms:

- the path is replayable at a point but every product box is refuted;
- counterexamples repeatedly change an environment or state value absent from
  the coordinate set;
- widening two related coordinates admits executions that leave the path;
- the claim batch is enormous but solver time is a small portion of wall time.

The central distinction is whether the path identity is wrong or the chosen
projection is wrong. Multiple witnesses are the cheapest discriminator:

- variation within one path proves a coordinate is not intrinsically a point;
- disagreement outside the current coordinates identifies a missing separator;
- stable equality between two values indicates a relational guard that a
  Cartesian product cannot represent.

Historical geometric ladders emitted roughly 258 candidates per coordinate and
direction and could create around 90,000 claims. In one recorded 300-second run
only 148 claims reached solving and solver time was 6.9 seconds. This does not
prove the residual time is a single named phase, but it does prove that adding
more eager rungs is the wrong response.

### 7.3 Oracle/emission failures

Symptoms:

- a region certifies but no assertion survives;
- an assertion is proved but cannot be rendered or reproduced;
- Forge compiles but the test is red on the unmodified contract;
- guarded assertions pass vacuously after a tolerated revert.

Important rules:

- Revert rolls state back, so state R1/R2 assertions are unobservable on a
  confirmed revert path. Keep an R0 exit/revert oracle instead.
- A `try/catch` wrapper cannot make a change assertion sound. A swallowed revert
  leaves state unchanged and falsifies `post != pre` or strict movement claims.
- Guarded assertions must be counted separately and cannot alone satisfy the
  strength gate unless their guard is proved live.
- A wide certified state interval is useless if the fixture cannot establish
  the chosen state in Foundry.
- Every verifier assumption used by certification must be established or
  checked by the emitted test.

### 7.4 Orchestration failures

Symptoms:

- repeated cold starts;
- timeout loses a whole eager batch;
- a later R2 pass overwrites the first query's log;
- defaults omit implemented mechanisms;
- mixed configuration rows are aggregated as one result.

These are external-call/driver defects. They are not solver limitations and
must not be reported as method boundaries.

## 8. Fuzz-first strategy

There are two different fuzz-first mechanisms. They solve different problems.

### 8.1 ESBMC multi-witness probing before region certification

`--probe-witnesses N` uses the existing all-witnesses path enumeration and does
not require a separate ESBMC process. Witnesses are already attributed to path
IDs. `--probe-ladder` anchors candidate boundaries at the observed per-path
member minimum/maximum and doubles outward. `--probe-ladder-budget N` prevents
the member ladders from recreating the eager geometric explosion.

This helps by:

- proving non-point behaviour before expensive certification;
- discovering omitted environment/state separators;
- pruning ladder candidates already known to be inside the path;
- bracketing near observed members rather than near zero;
- distinguishing a true singleton from a bad coordinate projection.

One witnessed value proves nothing about width. Neighbour perturbation remains
a diagnostic and cannot establish B's width provenance.

The official recipe should make the probe budget explicit and recorded. The
budget must be selected statically from unit arity/path count or a fixed global
cap, not tuned by rerunning the same POC.

### 8.2 Forge fuzz refutation before assertion certification

`fuzz_prefilter_verdicts()` parses one Forge output into `REFUTED`,
`NOT-REFUTED`, and `NOT-RUN`. Its soundness direction is correct:

- a concrete failing fuzz input refutes a candidate and can save a verifier
  query;
- a fuzz pass never proves a candidate and every survivor still needs ESBMC;
- an absent test is `NOT-RUN`, never a pass.

The implementation comment records a historical diagnostic: 1456 of 1470
complementary ladder pairs had one refuted side, and a 256-draw Forge run took
milliseconds versus tens of seconds for an assertion query. These figures are
not current experiment results, but they justify prioritising the mechanism.

Crucially, the parser has no production call site. It is referenced only by its
unit tests. Therefore Forge prefiltering currently saves zero queries.

A correct integration needs to:

1. generate temporary probe tests for mechanically enumerated candidates;
2. compile and execute all expected probe names in one Forge invocation;
3. hard-fail or mark `NOT-RUN` when any expected probe name is absent;
4. drop only fuzz-refuted candidates;
5. send every survivor to the verifier in batched claims; and
6. keep fuzz evidence separate from proof evidence in the artefact.

Fuzz-first should reduce verifier load, not replace certification.

## 9. Stronger R1 and R2

Strength is measured by fault-detection value and logical implication, not by
the number of assertions.

### 9.1 R0 and R1

R0 should explicitly describe normal return, revert class, or emitted event
where reproducible. R1 should cover every observable scalar state component,
touched mapping slot, and scalar/tuple return member that the fixture can read.

Keep all proved candidates not implied by another proved candidate. Examples:

- `post > pre` implies `post != pre` and `post >= pre`, so retain the strict
  relation and drop the weaker two.
- `post == pre` implies both non-strict directions; retain equality.
- an exact nonzero delta implies strict movement and inequality.

Frame properties are valuable: unchanged state outside the modified slot often
detects corruption that a single intended-update assertion misses. They must be
generated only for reproducibly readable state, and large untargeted candidate
sets should be fuzz-prefiltered before solver certification.

### 9.2 R2 depth-one grammar

The first complete implementation should build a typed expression AST, not pass
arbitrary expression strings through the current endpoint parser.

Atom classes:

- rendered fuzz/environment coordinates;
- entry snapshots of readable state and mapping slots;
- contract constants;
- integer literals from the target unit's source text.

Depth-one terms:

- every atom;
- typed `a + b`, `a - b`, and `a * b`;
- `a / k` where `k` is a nonzero integer literal.

Use commutativity canonicalisation for addition and multiplication, type/width
filtering, constant folding, and syntactic deduplication before any query. Do
not inspect the path's symbolic expression or trace assignments.

Candidate families should include:

- `post == e`;
- `lo <= post <= hi` with generated terms as endpoints;
- direction-safe exact or bounded deltas using generated terms.

Definedness belongs in the same implication as the assertion. A candidate whose
term can overflow, truncate, or divide by zero over the region must be refuted
or declined, not certified under an implicit mathematical-integer semantics.

To control cost:

- statically type-check and deduplicate first;
- fuzz-refute candidates in one generated Forge suite;
- batch all survivors for one path into one ESBMC assertion query where the
  existing per-candidate verdict mechanism permits it;
- preserve partial verdicts if a batch times out;
- use a deterministic cap and report every candidate excluded by that cap.

The current hard cap of six R2 queries is visible but structurally inefficient:
it spends one ESBMC process per endpoint. The target is one batched survivor
query per path, not six cold starts.

## 10. Priority order before the next real POC run

No real POC should run until all applicable static work below is complete and
covered by Python tests or small synthetic ESBMC regressions.

### P0: coherent official path policy

- Make path coordinate/pin policy per path class, especially separating ABI
  value-gate paths from body paths.
- Encode one versioned, recorded strong recipe in `poc_one.py`/
  `certify_all.py`; do not rely on a human passing the right flag combination.
- Ensure every certification environment pin is reproducible and checked by
  the emitter.
- Finish the three-step fixture boundary so constructor effects are fixed before
  unit enumeration/certification.
- Keep certification-cut region splitting disabled.

This priority is expected to turn the queue-head's two historical partial arms
into one coherent 5/5 attempt.

### P1: path-origin and unsupported attribution

- Add decision-origin metadata and define which origins contribute to source
  path identity.
- Exclude instrumentation and compiler safety checks from path splitting while
  retaining them as semantic constraints.
- Detect nondeterministic lowering/external-model decisions that cannot be
  reproduced and emit method-level evidence instead of futile region searches.
- Preserve partial per-candidate/per-path results at timeout and distinguish
  slow, killed, crashed, and solver-unknown outcomes.

### P2: useful fuzz-first and full oracle grammar

- Turn on bounded multi-witness/member probing in the official recipe.
- Integrate Forge candidate refutation into the production call graph.
- Implement typed depth-one R2 expressions and same-query definedness.
- Batch surviving R2 candidates and retain the implication antichain.

### P3: deterministic external behaviour

- Replace unbounded dispatcher re-entry with the frozen EOA/no-code and
  deterministic no-callback stub model.
- Mirror the exact choice and return/revert data in emitted Foundry fixtures.

This is essential for external-call POCs but should not block a queue-head unit
that contains no relevant external call.

## 10.1 Real benchmark target bridge status

The POCs are diagnostic only. The eventual benchmark target set is read from
`/home/samson/workspace/VeriPUT/Datasets` plus prepared subject metadata under
`/home/samson/workspace/VeriPUT/Results`.

Current target-manifest policy:

- `peer182`: only prepared `contracts_080` subjects are targets. The
  unupgraded Peer contract is skipped/ignored.
- `bugfix124`: `Datasets/Patch-Bug-Bench/summary.csv` supplies the bug/fix
  pair, target contract, and changed functions as unit hints.
- `stress243`: `Datasets/Stress-Projects/TARGETS.csv` supplies target
  contracts. `stress203` is accepted as a CLI alias for the current prepared
  key, but readiness summaries are reported under `stress243`.
- Difficulty assumption from the user remains:
  `peer < bugfix124 <= stress203/stress243`.

The bridge scripts in `notes/coverage/scripts/` are intentionally staged:

- `target_manifest.py`: read-only target manifest, no solc/Forge/ESBMC.
- `subject_unit_manifest.py`: maps target rows to prepared subject rows and
  enumerates public/external target-contract units from compact ASTs when they
  already exist. By default it does not invoke solc. It can preheat with
  `--generate-ast`, and can explicitly promote a solc path inferred from
  `meta.compile.cmd` with `--use-inferred-solc-bin`.
- `subject_unit_manifest.py --ast-cache-root <dir>` redirects compact AST reads
  and writes to an external cache path
  `<dir>/<benchmark>/<benchmark_key>/flat.sol.solast`. This keeps prepared
  `Results` subjects read-only. Without `--generate-ast`, the option only
  checks cache hits/misses and creates no directories or files.
- `certify_all.py --subject-* --ast-cache-root <dir>` uses the same cache
  layout for Stage-2 prepared-subject runs. Its dry-run/list-units mode prints
  both the flat source and the exact AST path, so a scheduled run can be
  audited before spending ESBMC.
- `veriput_readiness.py`: summarizes target->unit readiness without invoking
  solc/Forge/ESBMC.
- `ast_preheat_schedule.py`: expands `missing-ast` rows from a
  `veriput-unit-manifest/v1` into concrete per-subject
  `subject_unit_manifest.py --generate-ast` preheat jobs. It is read-only and
  refuses to schedule jobs unless an external `--ast-cache-root` is present, so
  the generated commands do not write `.solast` files back into prepared
  `Results` subjects. Inferred-solc jobs include
  `--use-inferred-solc-bin`; explicit-solc jobs do not.
- `ast_preheat_run.py`: consumes a `veriput-ast-preheat-schedule/v1` and runs
  each job's `preheat_argv` with a JSONL journal. Real execution requires
  `--journal`; `--dry-run` prints pending jobs without executing them. Resume
  skips only journal rows whose status is `ok`, so failed/missing rows remain
  retryable. Each job is validated to contain both `--generate-ast` and
  `--ast-cache-root`; start failures and non-ok subject rows are journaled as
  retryable failures instead of aborting the whole batch. Optional
  `--memlimit-gb` applies an inherited address-space cap to each solc-backed
  preheat child process and is recorded in both the run summary and journal
  rows.
- `ast_preheat_journal.py`: summarizes an `ast_preheat_run.py` JSONL journal
  and, when given the original schedule, emits a retry schedule containing jobs
  whose latest journal row is not `ok` plus jobs never attempted. This is
  read-only unless `--out` or `--retry-out` is passed.
- `ast_preheat_campaign_plan.py`: read-only controller for bounded AST
  preheat batches. It consumes a base `veriput-ast-preheat-schedule/v1` plus
  zero or more `ast_preheat_run.py` journals, treats only latest `ok` rows as
  completed, stops rescheduling jobs after `--max-attempts` non-ok attempts,
  and writes the next filtered preheat schedule only when
  `--next-schedule-out` is passed. The default batch size is 32 and the
  default preheat child memlimit is 8GiB. Selection defaults to `priority`
  (current schedule order: Peer before BugFix before Stress), and
  `--selection-strategy round-robin-benchmark` is available when the next
  batch should sample every benchmark early to expose solc/metadata problems
  sooner. The emitted `next_run` contains both argv arrays and shell-quoted
  commands: `dry_run_argv`/`dry_run_cmd` and `runner_argv`/`runner_cmd`. Both
  include `--memlimit-gb`, but only `runner_argv`/`runner_cmd` intentionally
  runs solc-backed preheat jobs. Copy `dry_run_cmd` first to audit the filtered
  schedule and journal resume set. The planner itself never invokes solc,
  Forge, fuzzing, ESBMC, or preheat jobs.
- `unit_manifest_gate.py`: post-preheat gate for a
  `veriput-unit-manifest/v1`. It reports `blocked`, `degraded`, or `ready`,
  counts unique unit certification jobs, duplicate prepared-subject/unit jobs,
  prepared errors, pending hints, and missing changed-function hints. It never
  invokes solc/Forge/fuzzing/ESBMC.
- `unit_schedule.py`: expands a `veriput-unit-manifest/v1` into concrete
  per-unit `certify_all.py --subject-* --unit ...` jobs. It is also read-only:
  it never invokes solc, Forge, fuzzing, or ESBMC. Target-hinted units are
  priority 0, other enumerated public/external units are priority 1. Duplicate
  prepared-subject/unit jobs are deduplicated. Apply `--limit` after priority
  sorting so small dry schedules keep changed-function hints first.
- `unit_schedule_run.py`: consumes a `veriput-unit-schedule/v1` and runs each
  job's `certify_argv` with JSONL resume. Real execution requires `--journal`;
  `--dry-run` prints pending certification jobs without executing them. A
  runner `ok` means the `certify_all.py` command completed successfully; the
  actual certified/not-certified PUT verdict remains in `certify_all.py`'s own
  output JSONL. Optional `--memlimit-gb` applies an inherited address-space cap
  to the certifier process and its ESBMC children. When the input schedule was
  written by `unit_campaign_plan.py`, each dry-run/run summary and every
  journal row carries `campaign_policy` and `campaign_attempt` metadata.
- `unit_schedule_journal.py`: summarizes a `unit_schedule_run.py` JSONL journal
  and, when given the original unit schedule, emits a retry schedule containing
  jobs whose latest journal row is not `ok` plus jobs never attempted. This is
  read-only unless `--out` or `--retry-out` is passed. It reports latest status
  by benchmark and schedule priority, so failed priority-0 changed-function
  units can be triaged before spending a longer certification pass.
- `certify_result_summary.py`: summarizes `certify_all.py --out` JSONL after
  `unit_schedule_run.py` has completed. This is the machine-readable Stage-2
  quality gate: it counts witnessed/certified/not-certified/no-verdict paths,
  certified region shapes (`wide`, `point`, `unparsed`), not-certified reason
  buckets, schedule priority buckets, duplicate rows, and scheduled units with
  no certification row. It is read-only unless `--out` is passed and never
  invokes solc, Forge, fuzzing, ESBMC, PUT emission, or certification jobs.
- `poc_ground_truth.py`: read-only POC inventory for pre-run ground-truth
  audit. It joins hand-written POC source comments containing `EXPECTED`, old
  or current `certify_gate.jsonl`/certification JSONL rows, and existing
  `put.json` artefacts. It reports per contract/unit witnessed/certified/
  not-certified paths, coordinates, pins, PUT counts, oracle/fuzz counts, and a
  conservative `strong_shape` flag (`fuzz_params > 0`, `asserts > 0`, and at
  least one non-point region coordinate). It also reports weak PUT reasons:
  `no-fuzz-params`, `no-oracle`, and `no-wide-region`. Use `--only
  Contract.unit`, `--contract`, `--unit`, or `--poc` to narrow the table before
  a scarce POC run. Each unit also receives a conservative
  `ground_truth_status`: `no-certification-row`, `no-certified-paths`,
  `certified-no-put`, `no-strong-put`, `partial-strong-put`, or
  `ready-strong`. Weak PUTs also carry `weak_details`, preserving concrete
  `oracle_skipped` and `ladder_refusal` strings so `no-oracle` can be split
  into constant/immutable tautologies, stale region-coordinate refusals,
  candidate-formation refusals, and truly undifferentiated rows. It never
  invokes solc, Forge, fuzzing, ESBMC, or PUT emission, and writes only when
  `--out` is explicitly passed.
- `unit_campaign_plan.py`: read-only controller for the agreed per-unit
  certification gradient: attempt 1 is 60s/8GiB, attempt 2 is 120s/8GiB,
  attempt 3 is 600s/10GiB. It consumes a base unit schedule plus zero or more
  `unit_schedule_run.py` JSONL journals, partitions jobs by next attempt,
  counts completed/exhausted jobs, and can write the next attempt's filtered
  `veriput-unit-schedule/v1`. It never invokes solc, Forge, fuzzing, ESBMC, or
  certification jobs; the emitted `next_run.runner_argv`/`runner_cmd` are only
  auditable command suggestions, and `next_run.dry_run_argv`/`dry_run_cmd` are
  the corresponding `unit_schedule_run.py --dry-run` audit commands. Attempt
  accounting is by highest observed
  `campaign_attempt` for a job; old rows without that field fall back to the
  order of `--journal` arguments. Repeated non-`ok` rows in the same journal
  remain visible in `status_attempts`, but count as one spent attempt for
  budget progression. When `--cert-jsonl <certify_all-out.jsonl>` is passed,
  runner `ok` is not enough: the job is completed only if its latest Stage-2
  certification rows contain at least one certified region and meet the
  certified-path-rate threshold (default 0.70). Runner-ok jobs with missing or
  weak cert rows are scheduled for the next attempt, and strong historical cert
  rows can complete a job even without a runner journal.
- `benchmark_pipeline_plan.py`: read-only top-level planner that stitches the
  target manifest, unit manifest, unit-manifest gate, AST-preheat schedule,
  AST-preheat campaign plan, unit schedule, unit campaign plan, and optional
  certification-result summary into one auditable
  `veriput-benchmark-pipeline-plan/v1`. It never invokes solc, Forge, fuzzing,
  ESBMC, or certification jobs. It requires an external `--ast-cache-root` so
  prepared-subject AST writes are never implied, and it writes child JSON
  documents only when `--out-dir` is explicitly supplied. Without `--out-dir`,
  it keeps child docs in memory instead of writing temporary files. With
  `--out-dir`, it also gives concrete default journal paths under that
  directory for the next AST-preheat and unit-campaign runner argv, but does
  not create those journal files. Its `summary.next_action` is the intended
  first triage point: `preheat-ast`, `run-unit-campaign`,
  `certification-ready-for-put`, or a blocker-inspection action. When the
  selected action is runnable, `summary.next_action` now mirrors the relevant
  current-stage command block from `next_runs`: `command_kind`,
  `dry_run_argv`/`dry_run_cmd`, `runner_argv`/`runner_cmd`, and budget fields.
  This makes the summary alone enough to audit the next command after a
  context compact.

As of the latest read-only census on 2026-08-06:

- Combined target rows: 548.
- Unit-manifest status: 509 `missing-ast`, 39 `error`, 0 `ok`.
- By benchmark:
  - `peer182`: 182 `missing-ast`.
  - `bugfix124`: 124 `missing-ast`.
  - `stress243`: 203 `missing-ast`, 39 `error`.
- Stress prepared errors:
  - 32 `prepared-status:compile-failed`;
  - 7 `prepared-status:flatten-failed`.
- Missing AST preheat classification:
  - `bugfix124`: 124 `preheatable_missing_ast`;
  - `peer182`: 182 `preheatable_missing_ast`;
  - `stress243`: 51 `preheatable_missing_ast`, 152
    `inferable_solc_bin`, 0 true `missing_solc_bin`.
- Solc path availability for those 509 missing-AST rows:
  - `bugfix124`: 124 `explicit_executable`;
  - `peer182`: 182 `explicit_executable`;
  - `stress243`: 51 `explicit_executable`, 152
    `inferred_executable`.
- Stress inferred solc buckets from historical `meta.compile.cmd`:
  - `solc-0.8.35`: 96 inferred, 46 explicit;
  - `solc-0.8.15`: 35 inferred, 5 explicit;
  - `solc-0.8.17`: 19 inferred;
  - `solc-0.8.19`: 1 inferred;
  - `solc-0.8.26`: 1 inferred.
- Bugfix pending unit hints: 381 changed-function hints pending AST
  enumeration. These hints are priorities, not filters.
- The 509 missing-AST rows contain one duplicate prepared subject:
  `stress243__ensdomains__ens-contracts__Controllable`. AST preheat should
  deduplicate by prepared subject, so the current preheat job denominator is
  508 unique subject jobs, not 509 target rows.

Interpretation:

- The current real-benchmark blocker is not yet region synthesis or ESBMC
  proof strength. The first blocker is missing compact ASTs for 509 usable
  target rows, corresponding to 508 unique prepared subjects.
- For Stress, most apparent `missing_solc_bin` rows were not real metadata
  loss. The successful flatten/compile record contains the exact solc path in
  `meta.compile.cmd`, so the tool now reports this as `inferable_solc_bin` and
  can use it only when explicitly requested.
- A read-only `stat/access` check confirms every explicit and inferred solc
  path needed for those 509 missing-AST rows currently exists and is
  executable. The remaining write-sensitive step is generating the `.solast`
  files themselves.
- The AST write-sensitive step no longer has to touch VeriPUT `Results`:
  once authorized, run `subject_unit_manifest.py` with both `--ast-cache-root`
  and `--generate-ast` to preheat an external cache, then rerun the manifest
  against that cache to enumerate units. Stage-2 certification must receive the
  same `--ast-cache-root`, otherwise it will look beside `flat.sol` in the
  prepared subject directory. Keep using `--use-inferred-solc-bin` for Stress
  rows whose solc path comes from `meta.compile.cmd`.
- Do not run the preheat pass while the user's other experiment depends on
  Dataset/Results immutability. When authorized, preheat in shards with journals
  and no ESBMC.
- Read-only schedule smoke on the empty external cache path
  `/tmp/veriput-empty-ast-cache-schedule-20260806-codex` produced:
  `jobs=0`, `skipped_rows=548`, `skipped_by_status={"error":39,
  "missing-ast":509}` and did not create the cache directory. This is expected
  until AST preheat/enumeration succeeds.
- Read-only AST-preheat schedule smoke on the empty external cache path
  `/tmp/veriput-ast-preheat-schedule-20260806-codex` produced:
  `jobs=508`, `by_benchmark={"bugfix124":124,"peer182":182,
  "stress243":202}`, `by_solc_source={"explicit":357,"inferred":151}`,
  `duplicate_rows=1`, `unschedulable=0`, `skipped_by_status={"error":39}` and
  did not create the cache directory. These jobs are only a plan; running their
  `preheat_argv` would intentionally invoke solc and write external cache
  files.
- Read-only preheat runner dry-run smoke on
  `/tmp/veriput-ast-preheat-run-dry-20260806-codex` selected 508 jobs:
  `selected=508`, `pending=508`, `already_done=0`, and did not create the
  cache directory. This exercises the complete scheduling pipeline through
  `ast_preheat_run.py --dry-run`, still without invoking solc.
- Read-only journal summary smoke with an empty journal and the current full
  preheat schedule produced: `attempt_rows=0`, `never_attempted=508`,
  `retry_jobs=508`, proving the retry schedule preserves all pending unique
  subject jobs.
- Read-only unit-manifest gate smoke on the current no-AST manifest produced:
  `gate_status=blocked`, blockers `missing compact AST rows remain`,
  `changed-function hints are still pending AST enumeration`, and
  `no ok subject rows with enumerated units`; summary `rows=548`,
  `status={"error":39,"missing-ast":509}`, `unique_unit_jobs=0`,
  `duplicate_subject_rows=1`, `ready_for_unit_schedule=false`. The matching
  `unit_schedule.py` smoke still emits 0 jobs and reports 548 skipped rows.
- Read-only unit runner dry-run smoke on the current no-AST manifest produced
  `selected=0`, `pending=0`, `already_done=0` and did not create the external
  cache path. This is expected until the post-preheat gate is no longer
  `blocked`.
- Read-only unit journal summary smoke with an empty journal and the current
  no-AST unit schedule produced: `schedule_jobs=0`, `skipped_rows=548`,
  `attempt_rows=0`, `retry_jobs=0`, `never_attempted=0`, and did not create the
  external cache path.
- Read-only unit campaign smoke with an empty journal and the current no-AST
  unit schedule produced: `schedule_jobs=0`, `skipped_rows=548`,
  `selected_attempt=null`, `selected_jobs=0`, `next_jobs=0`,
  `next_attempt=null`, `distinct_attempts_max=0`; it wrote only an empty
  next-attempt schedule under `/tmp` and did not create the external cache
  path.
- Read-only unit campaign smoke with both an empty runner journal and an empty
  `certify_all.py --out` JSONL produced: `schedule_jobs=0`,
  `skipped_rows=548`, `cert_quality_enabled=true`,
  `selected_attempt=null`, `selected_jobs=0`, `next_jobs=0`; it did not create
  the external cache path.
- Read-only certification-result summary smoke with an empty `certify_all.py
  --out` JSONL and the current no-AST unit schedule produced:
  `schedule_jobs=0`, `skipped_rows=548`, `gate=blocked`,
  blockers `no certification rows` and `no certified regions`,
  `certified_regions=0`, `missing_scheduled_units=0`; it did not create the
  external cache path.
- Read-only benchmark pipeline smoke on 2026-08-06 used an empty external cache
  path under `/tmp` and `--out-dir` under `/tmp`:
  `benchmark_pipeline_plan.py --benchmark peer182 --benchmark bugfix124
  --benchmark stress243 --ast-cache-root <tmp-cache> --out-dir <tmp-out>`.
  It produced `targets=548`, unit manifest `missing_ast=509`, `error=39`,
  `ok=0`, `pending_unit_hints=381`, `ast_preheat_jobs=508`, `unit_jobs=0`,
  `campaign.selected_jobs=0`, and `summary.next_action.action=preheat-ast`.
  It also produced an AST-preheat campaign summary
  `jobs=508,pending=508,selected_jobs=32,completed_ok=0,exhausted=0`, wrote a
  bounded `next-ast-preheat-schedule.json` with 32 jobs
  (`by_benchmark={"peer182":32}`), and emitted a concrete runner argv pointing
  at `<tmp-out>/ast-preheat-run.jsonl` with outer timeout 90s, `--memlimit-gb
  8.0`, and one worker. The paired `dry_run_cmd` is identical except for a
  final `--dry-run`, and should be copied first before running the solc-backed
  `runner_cmd`. The generated `next-ast-preheat-schedule.json` records
  `outer_memlimit_gb=8.0` in its summary. The external AST cache directory and
  both runner journal files were not created; only the requested child JSON
  docs under `/tmp/<out>` were written, including the empty
  `next-unit-schedule.json` artifact. This is now the preferred single
  read-only command for restoring the full benchmark denominator state and the
  next bounded action after a context compact.
- Read-only benchmark pipeline smoke with
  `--ast-preheat-selection-strategy round-robin-benchmark` on 2026-08-06 still
  produced `summary.next_action.action=preheat-ast` and selected 32 AST
  preheat jobs, but mixed the first batch across benchmarks:
  `by_benchmark={"peer182":11,"bugfix124":11,"stress243":10}` and
  `by_solc_source={"explicit":32}`. The first jobs alternated Peer, BugFix,
  Stress. This is the faster smoke strategy when the goal is to discover
  benchmark-wide preheat breakage before spending several Peer-only batches.
  The pipeline JSON now also exposes copyable shell strings:
  `next_runs.ast_preheat.dry_run_cmd` and `runner_cmd`. The smoke confirmed
  only `dry_run_cmd` contains `--dry-run`; neither command was executed, and it
  did not create the external AST cache or runner journal.
- A follow-up read-only benchmark pipeline smoke on 2026-08-06 with the same
  three benchmarks and `--ast-preheat-selection-strategy round-robin-benchmark`
  confirmed `summary.next_action.command_kind=ast_preheat`,
  `summary.next_action.memlimit_gb=8.0`, and the same dry-run/runner split
  directly in `summary.next_action`: `dry_run_cmd` contains `--dry-run` while
  `runner_cmd` does not. The selected batch size remained 32. The external AST
  cache directory and runner journal path were not created.
- Read-only `poc_ground_truth.py --limit 3` smoke on 2026-08-06 reported:
  `poc_sources=65`, `sources_with_expected=51`, `cert_rows=362`,
  `put_rows=218`, `unit_rows=171`, `strong_shape_puts=169`, and zero bad JSONL
  or `put.json` documents. It did not run solc/Forge/fuzz/ESBMC. This is now
  the cheap first command for building a POC/unit ground-truth table before
  spending one of the three allowed ESBMC attempts.
- Read-only filtered `poc_ground_truth.py --only Aqua.rawBalances --limit 1`
  smoke on 2026-08-06 reported one matched unit out of 171, four existing
  `put.json` rows, three `strong_shape` PUTs, and one weak PUT whose reason was
  `no-oracle`; its unit status is `ready-strong`. Filtered `--only Aqua.dock`
  reported one matched unit, four PUTs, zero `strong_shape` PUTs, weak reasons
  `{"no-oracle": 4}`, and unit status `no-strong-put`. Filtered
  `--contract P05_Hole --unit pick` reported one matched unit, three existing
  PUTs, all three `strong_shape`, and surfaced the source `EXPECTED` block
  about rendering `x in [lo, hi] \ {42}` with `vm.assume(x != 42)`. All smokes
  were read-only and did not invoke solc/Forge/fuzz/ESBMC.
- Follow-up filtered `poc_ground_truth.py --only Aqua.dock --limit 1` smoke
  with `weak_details` showed why this is not a simple emitter bug: two rows
  dropped `_DOCKED` because solc's layout does not list it (constant/immutable
  tautology), one stale row carried the old aggregate-key `strategyHash` ladder
  refusal, and one old row remained undifferentiated. Filtered
  `--only P19_ReturnShapes.tern_lit --limit 1` showed `no-oracle` caused by
  ladder refusal before assertion formation: every state variable was refused
  because the contract state is mapping/dynamic-array shaped rather than
  ordinary contract-object components. Both smokes were read-only.

## 11. One-POC, one-ESBMC-rerun protocol

For each POC:

1. Read every existing stage record, command line, per-invocation log, refusal,
   counterexample, and generated test. Build a path-by-path table without
   running ESBMC.
2. Classify each missing B into instrumentation/path identity, region policy,
   oracle/emission, driver/configuration, or evidenced method unsupported.
3. Implement all general fixes justified by that evidence. Add Python unit
   tests and minimal synthetic regression inputs. Do not use the real POC as a
   debugger.
4. Build once and run focused unit/regression checks once. These do not spend
   the POC run, but they must not launch the real POC contract.
5. Dry-read the final `poc_one.py` command and recorded configuration. Confirm
   binary freshness, output isolation, timeout, unwind, probe budget, fixture,
   per-path policy, R2 mode, and Forge stage.
6. Spend the single real POC ESBMC run through `poc_one.py`. It must execute the
   whole intended pipeline, not one diagnostic arm.
7. Do not rerun that POC. If paths remain unconverted, use the one run's logs
   for attribution and fix the pipeline before selecting a new POC. Ask the
   user before any exception to the one-run limit.

The run record must contain enough information to reproduce the exact cell:
commit, binary hash/mtime, POC ID, scope, all non-default switches, fixture ID,
coordinate policy, budgets, solver, unwind settings, result buckets, certified
regions, width provenance, assertion verdicts, emitted file, and Forge result.

## 12. Practical 70% strategy

The fastest credible route to at least 70% is not more geometric search. It is
to remove systematic losses in funnel order:

1. **Make witnessed paths honest.** Origin-aware path identity prevents the
   denominator from being inflated by compiler/lowering artefacts.
2. **Use per-path projections.** ABI gates, authorization guards, and body paths
   should not share one global coordinate policy.
3. **Probe before bracket.** Multi-witness evidence selects coordinates and
   anchors a bounded ladder without extra ESBMC processes.
4. **Reproduce all assumptions.** A certificate that Foundry cannot establish
   is not convertible.
5. **Refute cheaply, prove once.** Forge removes obviously false R1/R2
   candidates; ESBMC certifies only survivors in batches.
6. **Complete the depth-one grammar.** Setter, transfer, deposit, withdrawal,
   fee, cap, and frame properties then have a mechanical route to strong
   assertions without path-expression mining.
7. **Name real method boundaries.** Unsupported assembly/lowering or external
   behaviour must be evidenced, not hidden as timeout or singleton regions.

Expected impact by failure class:

- Queue-head ABI/body split: per-path policy directly addresses all five known
  paths.
- Owner/modifier guards: fuzz sender while reproducing owner state; do not widen
  both sides of an equality in one rectangle.
- Setters: identity `post == parameter` candidates.
- Deposits/withdrawals: exact and capped directional delta candidates, followed
  by depth-one fee/rate expressions.
- Mapping-heavy units: named touched slots and frame assertions.
- Assembly or unmodelled external calls: origin-aware attribution or the frozen
  deterministic model, rather than endless region shrinking.

The 70% number must be measured only after the official recipe is fixed and the
binary is frozen. Historical corpus numbers are hypothesis evidence only.

## 13. Historical diagnostics that must not be reported as current yield

The repository contains several useful but incomparable result families:

- an old skip-bracket record with 95 rows and historical certified regions;
- a pieces-corpus record with 45 rows, 113 witnessed paths, and 10 certificates;
- old stage-one funnel totals across aqua, two escrow contracts, farming, and
  st1inch;
- queue-head derived-coordinate and value-gate arms produced by different
  binaries/configurations.

They are valuable for locating failure modes. They are invalid as a current
conversion percentage because they mix binaries, policies, scopes, budgets, and
method versions. The authoritative current B count is zero until a fresh,
officially configured generated real-corpus `.t.sol` satisfies all five gates.

## 14. Immediate next action

Do not run `farming__Distributor__setDistributor` yet.

The next engineering session should convert the P0/P1/P2 items above into one
static implementation batch, beginning with the official driver call graph and
per-path coordinate policy. Every change should be tested without the real POC.
Only after the complete batch builds and focused tests pass should the single
queue-head run be spent.

## 15. 2026-08-05 takeover audit: actual production call graph

The comments and the executable call graph currently disagree in one important
place.

`poc_one.py --stage 1` invokes `pathcov_collect.py`. That process runs ESBMC in
path-coverage mode and archives a `cov-report.json` under the selected POC cell.
`poc_one.py --stage 2` invokes `certify_all.py`, which invokes
`solidity_path_generalise.py`. The generalise driver unconditionally deletes
its work directory's `cov-report.json` and invokes ESBMC again in
`enumerate_paths()`. It has no option that imports the stage-one report.

Therefore the statement in `poc_one.py` that stage two "reads [the stage-one
counterexample set] back" is false in the current implementation. Stage one is
an independently archived gate measurement; it is not an input to region
synthesis. This duplicates path enumeration and also permits the two stages to
use different witness settings. In particular, the strong region recipe wants
`--all-witnesses --max-witnesses N`, while the stage-one collector currently
does not request them.

The production entry point is also incomplete after certification:

- `poc_one.py` exposes only stages 1 and 2;
- it never invokes `put_all.py` / `solidity_path_put.py`;
- consequently it never requests R1/R2, emits the final PUT, or runs the five
  Foundry gates as one official POC pipeline;
- `put_all.py --propose-r2` remains opt-in and spends one ESBMC process per
  proposed endpoint, up to the current hard cap of six.

The single-POC rule applies to the full official pipeline, not to one diagnostic
arm. The pipeline may internally make the batched enumeration, region, certify,
and assertion queries required by the method, but after the binary and recipe
are frozen it may be launched for a given real POC only once. Synthetic unit
tests and purpose-built regression contracts do not consume that real-POC run.

## 16. Memory and process contract

The requested POC memory limit is exactly 8 GiB per ESBMC process.

- `pathcov_collect.py` already passes the hardcoded `--memlimit 8g`.
- `solidity_path_generalise.py` defaults to `--memlimit 8g`.
- `solidity_path_put.py` defaults to `--memlimit 8g`.
- `certify_all.py` defaults to `--memlimit-gib 8`, checks
  `jobs * memlimit <= 60% of MemAvailable`, and defaults to `--jobs 1`.

Relying on those defaults is insufficient for the official command. The POC
entry point must pass and print `--memlimit-gib 8` explicitly, every child must
record it, and the recipe must keep `jobs=1`. A timeout must kill the complete
process group; all three current outer drivers already contain process-group
cleanup because killing only the Python child previously left an ESBMC
grandchild alive with its full memory allowance.

No real POC ESBMC process was launched during this audit.

## 17. ESBMC option and modelling facts used by the design

The following are source-level facts, not assumptions inferred from help text.

1. Solidity path coverage forces multi-property/base-case operation, protects
   counterexample symbols only with `--cov-report-json`, and rejects
   `--multi-fail-fast` because abandoned claims would be indistinguishable from
   unreachable ones.
2. Solidity coverage starts from `--no-standard-checks`; path/branch coverage
   also disables user/library assertions and symbolic-execution pointer checks.
   Bounds, division-by-zero, overflow, and related checks are not automatically
   part of source path identity. Positive check flags are separate semantic
   obligations and must not be confused with decision points.
3. Complete paths are identified by both `tr` and `cnt`. Each kept decision
   updates `tr = 2 * tr + arm` and increments `cnt`; exit claims deny the pair
   `(enc, depth)`. Using only `enc` would merge different-length sequences.
4. `--solidity-max-tx N > 0` emits N straight-line transaction bodies. Under a
   coverage mode, max-tx zero does not restore an unbounded transaction loop:
   coverage neutralises the dispatcher back edge unless
   `--coverage-multi-tx` is selected, so zero effectively leaves one pass.
5. With no explicit unwind, path coverage installs unwind 4 and suppresses
   unwinding assertions. The same unwind currently bounds Solidity loops,
   internal-call expansion, and nondeterministic external-call re-entry.
6. `--path-cov-max-goals` first degrades the selected internal-call granularity
   and only then truncates as a last resort. `--path-cov-claim-timeout` is a
   per-claim budget and reports `claim-budget-exceeded`; it is not the Python
   process timeout.
7. `--path-cov-outer-box` is one batched ladder query. Its global `pin` list is
   part of every path antecedent, while per-path coordinate values can differ.
   `--path-cov-certify` independently assumes one box and checks path identity
   at every exit, with a separate non-vacuity witness. Its explicit
   `RESULT: CERTIFIED|REFUTED|VACUOUS|UNDECIDED|UNDECIDED-TRUNCATED` line is
   authoritative; the final generic verification line is not.
8. `--path-cov-assert` assumes the certified region and checks a batch of R1/R2
   candidates at the target path's exits. Its candidate verdict table and
   non-vacuity result are authoritative.
9. `--path-cov-fixture` exists in the frontend. A fixture can skip the
   constructor and assign concrete scalar integer/address/bool state before the
   transaction driver. No production Python driver creates or passes such a
   fixture, and it does not yet implement the paper's required witness, fill,
   and concrete replay protocol.
10. Solidity external calls may nondeterministically re-enter the contract
    dispatcher, causing unwind-dependent path multiplication. The frozen
    deterministic EOA/no-code and no-callback stub policy is not implemented.

## 18. Queue-head path anatomy from archived evidence

The queue head remains `farming__Distributor__setDistributor`. Its source shape
is an `onlyOwner` guard followed by a zero-address guard and one state update.
The archived stage-one report contains exactly five witnessed complete paths:

| enc | depth | ABI value gate | owner relation | distributor | exit |
|---:|---:|---|---|---|---|
| 2 | 1 | reject, `msg.value != 0` | not entered | irrelevant | rollback revert |
| 12 | 3 | body, `msg.value == 0` | non-owner | zero | rollback revert |
| 13 | 3 | body, `msg.value == 0` | non-owner | nonzero | rollback revert |
| 14 | 3 | body, `msg.value == 0` | owner | zero | rollback revert |
| 15 | 3 | body, `msg.value == 0` | owner | nonzero | normal return/write |

The report records the first decision with `synthetic_abi_gate: true`. That is
enough to distinguish the ABI reject path from body paths without a new ESBMC
query. The generalise parser currently discards this decision metadata.

The two historical partial arms are diagnostic only:

- the derived-coordinate arm pinned `msg.value=0`, pinned agreed entry state,
  promoted the disagreed sender, and certified body paths 12--15 while excluding
  path 2 by construction;
- the value-gate arm left `msg.value` free and certified path 2 as
  `[1, uint256.max]`, but did not combine the owner-state and sender policy that
  made the body paths certifiable.

They were produced by different configurations and binaries and cannot be
summed. A coherent official attempt must admit both sides of the ABI gate in
one recorded policy. The least invasive candidate is to make `msg.value` a
coordinate when the synthetic gate is present, pin the agreed owner state, and
let each path's certified box specialize the value to either zero or the
nonzero interval. Explicit path-class grouping remains the fallback if the
shared coordinate batch is too expensive or loses sibling-cut quality.

## 19. Static defects that must be fixed before that attempt

### 19.1 Work-directory provenance is incomplete

`run_config()` conditionally records `env_coord_disagreed` and
`pin_agreed_state`, but `stamp_workdir()` compares only `CONFIG_FIELDS`, and
that tuple omits both keys. It also omits `skip_bracket`, explicit environment
coordinates, pins, level-0/perturb settings, probe-ladder budget, refine/shrink
budgets, holes/pieces, fixture identity, solver arguments, timeout semantics,
and other options that change the generated questions. Two incompatible arms
can therefore share and overwrite fixed-name files while passing the stamp.
The stamp must compare a complete canonical measurement configuration, with a
version field and compatibility handling only for genuinely equivalent old
defaults.

### 19.2 The official sweep cannot express all relevant budgets

The generalise driver accepts `--claim-budget`, but `certify_all.py` neither
exposes nor records it. Note that this budget thins only a geometric bracket
whose values are generated by Python; it does not cap refine claims generated
from a range by ESBMC. It must be wired and described accurately, not presented
as a universal query cap.

### 19.3 Width provenance can be falsely labelled

`put_all.py` always records `probes`, and `solidity_path_put.py` currently treats
the presence of either `probe_ladder` or `probes` as evidence that a ladder
derived a region's width. Under `--skip-bracket`, `probes` controls refinement
but no geometric bracket ran. A wide interval can consequently be labelled
ladder-derived when it came only from full-type initialization/refinement.
Width provenance must be explicit per mechanism (multi-witness member span,
sibling subtraction, geometric/per-path ladder, certified shrink/cut) and may
not be inferred from a nonzero default.

### 19.4 A certified `msg.value` interval is emitted as one value

The historical path-2 certificate is `msg.value in [1, uint256.max]`, but the
emitted Foundry test keeps `.call{value: 1}`. The test therefore does not fuzz
the certified coordinate and its apparent argument fuzzing is irrelevant to
the ABI reject path. The emitter already rewrites `msg.sender`; it must also:

- introduce a bounded `uint256 p_msg_value` parameter;
- apply certified holes with `vm.assume`;
- rewrite the governing low-level call to `{value: p_msg_value}`;
- fund the actual sender before the prank with `vm.deal(sender, p_msg_value)`;
- record the environment coordinate as established and count its actual width.

The first implementation should be deliberately narrow: only rewrite an
existing low-level call that already has a `{value: ...}` option. Inventing a
value-bearing call for arbitrary emitted call shapes requires a separate
semantic check.

### 19.5 Fuzz-first exists only in pieces

Multi-witness path collection is production-wired and costs no extra ESBMC
process, but it is off in the official recipe. `fuzz_prefilter_verdicts()` can
classify Forge results for assertion candidates and has tests, but has no
production call site. The sound policy is asymmetric: a concrete Forge failure
may drop/refute a candidate; a Forge pass proves nothing and every survivor
must still be sent to ESBMC. Fuzz evidence and proof evidence must be recorded
separately.

### 19.6 R2 remains below the paper-strength target

The C++ endpoint resolver accepts a decimal or one resolvable name. The Python
proposer mostly emits parameter identity, absolute `post in [p,p]`, exact
directional delta, and a second-pass `[0,p]` cap. It does not implement a typed
depth-one grammar over parameters, selected environment coordinates, readable
pre-state, and literals, and it spends one cold ESBMC process per endpoint.
This should become one typed candidate table and one batched survivor query per
path, with definedness checked under the same region assumption.

## 20. Frozen implementation order after the audit

The next edits should be made in this order and validated without the real
queue-head contract:

1. Fix configuration stamping and wire/record the missing budget and explicit
   8 GiB recipe fields.
2. Preserve decision metadata from enumeration and make the ABI gate policy
   admit both body and reject paths coherently. Start with one shared coordinate
   batch; add path-class grouping only if a synthetic regression demonstrates
   that it is needed.
3. Correct width provenance and implement low-level-call `msg.value` lifting,
   including sender funding and Foundry-facing tests.
4. Wire one versioned strong recipe through the single-POC driver and add a
   complete stage that reaches assertion synthesis, R2 mode, emission, and the
   Foundry B gates. Keep every subprocess at `--memlimit 8g` and jobs one.
5. Implement the frozen fixture boundary before using any certificate whose
   entry state cannot be reproduced by the emitted test.
6. Add decision-origin taxonomy and deterministic external-call treatment
   before attacking POCs whose denominator or reach depends on those models.
7. Integrate Forge refutation and replace per-endpoint R2 cold starts with a
   typed batched query.

Only after these applicable static changes, Python tests, a build, and focused
synthetic ESBMC regressions pass may the queue-head POC be launched once. Its
command must state `--memlimit-gib 8` explicitly and archive the recipe version,
binary identity, all path-policy decisions, every query verdict, generated PUT,
and Forge result.

## 21. 2026-08-05 static implementation batch

The first four items in Section 20 have now been implemented without launching
a real POC ESBMC process.

- Generalisation work directories use a versioned, fail-closed configuration
  stamp covering input/AST/binary identities and the semantic switches that
  shape enumeration, coordinates, region search, and solver calls.
- Stage two can import exactly one stage-one enumeration index/report pair. It
  validates schema, file and binary identities, contract/unit, transaction
  depth, scope, witness count, memory limit, solver arguments, and structured
  command arguments before reuse. A mismatch is a refusal, never a fallback to
  a second silent enumeration.
- Stage-one reports retain decision metadata and classify the synthetic ABI
  gate. Duplicate encodings retain one consistent metadata record.
- The official runner now has three serial stages: enumerate, certify, then
  R1/R2 plus PUT emission and Forge gates. It is still impossible to name two
  POCs in one invocation.
- The versioned `veriput-strong/1` recipe explicitly uses eight witnesses,
  level-zero perturbation, a geometric probe ladder, agreed-state pins,
  renderable disagreed environment coordinates, finite holes/pieces, jobs one,
  and 8 GiB per ESBMC process.
- Solver/encoder exceptions are selected once by `pathcov_collect.py` and
  propagated to all three stages. In particular, st1inch consistently receives
  `--z3 --tuple-node-flattener`; later stages no longer silently fall back to a
  different backend.
- Width provenance now records geometric bracketing and sibling subtraction
  explicitly. A nonzero probe count no longer falsely claims a ladder-derived
  width when the bracket was skipped.
- A certified interval over `msg.value` can become a bounded fuzz parameter for
  an existing low-level value call. The emitted test rewrites the value option,
  funds the actual sender before the prank, preserves the original statement's
  exit check, and records the coordinate as established.
- Environment coordinates the emitter cannot reproduce, such as unsupported
  block or transaction quantities, now refuse emission. Automatic promotion is
  restricted to `msg.sender` and `msg.value` because those are the two current
  Foundry emitter paths that establish the verifier assumption.
- `put_all.py` accepts the recorded dispatcher set, transaction depth, solver
  arguments, timeout, memory, and per-POC output root. It forwards R2 opt-in and
  keeps derivation provenance in the PUT command.
- `--fresh` no longer deletes a stage-one collection. It atomically moves the
  whole directory to a unique `.superseded.<time_ns>` name. Stage-two redo
  already preserves its prior result and scratch tree similarly.

Static verification after this batch:

- Python byte compilation passed for all eight changed scripts.
- `scripts/test_solidity_path_generalise.py` passed.
- `scripts/test_solidity_path_put.py` passed all 110 declared tests.
- farming and a real st1inch POC completed three-stage dry runs. The printed
  commands show 8 GiB in all ESBMC stages, stage-two report reuse, and solver
  propagation. Dry runs start no ESBMC process.
- A synthetic process-tree timeout check killed both a parent shell and its
  background child. Stage one, stage two, the POC supervisor, PUT ESBMC calls,
  and Forge now use process-group cleanup where an outer timeout can fire.
- `git diff --check` passed. Pylint's only error is the pre-existing
  `best[0]` union-inference warning in `choose_refinement`; blame places that
  line in an older commit and the current batch does not touch it.

## 22. Memory and timeout semantics after the batch

The exact resource contract is now:

- stage one receives and records `--memlimit 8g` and requests at most eight
  witnesses in the one enumeration process;
- stage two receives `--memlimit-gib 8 --jobs 1`, imports stage one's report,
  and gives each generalisation-driver invocation its recorded run timeout;
- every ESBMC call made by PUT receives `--memlimit 8g` and is wrapped by
  `setsid timeout -k 30s`;
- Forge is checked twice per output project, each invocation in its own process
  group with a 300-second default timeout;
- stage one and stage two retain an aggregate supervisor timeout because each
  supervises one bounded unit invocation;
- stage three deliberately has no one-invocation aggregate timeout. One region
  requires the base ladder and can require up to six serial R2 calls, and one
  POC can have several certified regions. Killing that complete sweep after one
  ESBMC allowance discards valid later regions. Its external children remain
  individually bounded, so this does not create an unbounded ESBMC or Forge
  process.

The queue-head official attempt has still not been spent.

## 23. What is still not implemented

The following must not be reported as complete merely because the strong recipe
has switches with similar names.

1. The work order's probe rule is not satisfied yet. `--all-witnesses` gathers
   several models for a path-coverage claim, but witness blocking currently
   includes irrelevant nondeterministic quantities and historically produced
   only two or three distinct published points from eight requests. The required
   branch/condition/k-path probe pass that discovers variation outside the
   current coordinate set has no production call or archived fire/silent
   controls.
2. Forge-first assertion filtering is parser-only. The tested
   `fuzz_prefilter_verdicts()` correctly treats a concrete failure as REFUTED
   and a pass as only NOT-REFUTED, but no generated probe suite feeds it and the
   ESBMC assertion spec cannot yet omit only the refuted rungs. No production
   query has been saved by fuzzing yet.
3. Mapping slots are not dependency-selected. The strong recipe uses zero
   blanket slot coordinates because inherited ERC20 mappings multiply every
   region round even for a scalar setter such as `setDistributor`. The next
   implementation needs an AST/call/modifier dependency walk that names only
   stores read or written by the target; zero is a conservative temporary
   policy, not the final mapping policy.
4. R1 is still bounded by the current scalar/return candidate emitter and by
   what the test can read after normal return. Rollback paths correctly keep
   only the observable exit-kind oracle, but richer selected mapping and struct
   components still need dependency-aware candidates.
5. R2 remains the opt-in parameter identity/exact delta/cap proposer with a
   six-query cap. The frozen typed depth-one grammar over rendered coordinates,
   pre-state, constants, and literals, with same-query definedness and one
   batched survivor table, is not implemented.
6. The frozen three-step constructor fixture and deterministic external-call
   model are not implemented. POCs that need unreproducible deployment state or
   currently explode through nondeterministic dispatcher re-entry must be
   attributed to those method gaps, not attacked by repeated region shrinking.

## 24. Failure attribution before any further real run

The observed difficulty must be split before selecting a repair:

- **Instrumentation/path identity:** missing or merged `(enc, depth)`, synthetic
  ABI/constructor/internal-call decisions, goal degradation/truncation,
  irrelevant witness blocking, nondeterministic external re-entry, or a report
  that cannot preserve the coordinate payload. These require an ESBMC-side
  minimal reproducer and cannot be repaired by shrinking a region.
- **Invocation:** wrong solver/encoder, scope, transaction depth, unwind,
  memory/time budget, witness count, stale report, or inconsistent stage
  configuration. These require a one-switch contrast and are now mostly guarded
  by the versioned manifests and unified runner.
- **Region policy:** a witnessed path exists, but its relevant coordinate is
  omitted, unrenderable, correlated with another coordinate, lost to sibling
  subtraction, or collapsed by an exhausted refinement policy. This is where
  multi-witness coordinate discovery, ABI gate classification, environment
  lifting, and dependency-selected state coordinates apply.
- **Oracle policy:** a region certifies but no non-vacuous, observable antichain
  candidate survives. This is where Forge refutation, stronger R1, typed R2,
  return values, mapping slots, rollback observability, and definedness apply.
- **Emitter/roundtrip:** the certificate constrains a quantity the Foundry test
  does not establish, the generated source does not compile, the test is red on
  the unmodified contract, or the five gates read stale output. These are
  emission failures, not evidence that the path has no region or oracle.

The next work is static: implement the genuine probe pass, dependency-guided
slot selection, and the typed batched R2 interface on synthetic fixtures. Then
perform a low-concurrency build and focused regressions. Only after those gates
pass should `farming__Distributor__setDistributor` consume its one allowed
official rerun, with 8 GiB printed and recorded end to end.

## 25. Probe architecture: facts established before implementation

### 25.1 Existing coverage modes cannot be composed by adding flags

The coverage dispatch runs condition, branch, branch-function, k-path, and
Solidity complete-path passes independently and in that order. They mutate the
same GOTO program and publish through the same static `all_claims` set. The
Solidity path pass explicitly clears that set; branch-function later replaces it
with every instrumented coverage assertion then present. Reporting has a second
independent precedence order (`branch`, `branch-function`, `k-path`,
`solidity-path`, `condition`, `assertion`), while the path-only JSON decoration
still runs whenever the path boolean is set. Therefore a command containing
both path and branch-function coverage does not mean intersection, sequencing,
or attribution. It produces a mixed assertion universe interpreted by two
incompatible reporters. Such output is invalid evidence.

### 25.2 A branch-point counterexample carries only a prefix by construction

`branch_function_coverage()` inserts its assertions immediately before each
GOTO. The selected assertion constrains execution only up to that decision. The
claim slicer disables the other assertions, and the dependency slicer removes
later assignments unless they are separately protected. Even if later `tr/cnt`
assignments are protected, the violated branch assertion itself does not require
the execution to reach any function exit. Reading the then-current accumulator
would label a path prefix as a complete path. This is not repaired by harvesting
more fields or disabling compact traces.

Consequently a genuine branch/condition probe attributable to a complete path
must delay its goal to an exit. The mechanically sound shape is:

1. initialise one reachability latch per selected decision arm at unit entry;
2. update those latches when their decision is evaluated;
3. retain the ordinary complete-path `tr/cnt` observation;
4. assert each unreached latch at unit exits, so every violating model is a
   complete execution rather than a prefix;
5. publish the branch goal and the observed `(unit, tr, cnt)` separately;
6. group and deduplicate payloads by `(unit, tr, cnt)`, never by branch claim.

This must be a dedicated hybrid mode. It must not run the ordinary branch pass
and ordinary path pass on the same mutated program. Its report needs its own
schema and coverage type so no existing branch/path dataset changes silently.

### 25.3 Path multi-witness collection remains useful but is not R16 by itself

`--all-witnesses` already re-solves one encoded complete-path claim repeatedly,
so every returned model is correctly attributed to the same `(enc, depth)` and
costs no second symex/encoding. `probe_seed.py` and the generaliser consume this
correctly: variation proves a coordinate is not a point; lack of variation
proves nothing. This is valuable member seeding and should remain enabled in the
strong recipe.

It is not sufficient to claim R16 is implemented. The work order explicitly
requires branch-function/condition (or k-path) probes and asks whether quantities
outside the current coordinate set separate paths. Current all-witness blocking
also ranges over every collected nondeterministic symbol. Archived farming data
shows that irrelevant harness environment, especially `block.difficulty`, can
consume most of an eight-witness budget while producing only two or three
distinct published points. The temporary nondet census in `witnesses.cpp`
documents this gap; no production filter calls it away.

The hybrid probe therefore needs two payload classes: renderable coordinates
(arguments, reproducibly established environment, fixture entry state and
supported mapping slots) and observed non-coordinate quantities (for example a
deterministic external-call result). Harness allocator/dispatcher choices are
neither. Only the first two classes may drive witness blocking, and every
outside quantity that varies while `(unit,tr,cnt)` stays fixed is evidence for a
coordinate-promotion decision. A single observed value is never evidence of
constancy.

### 25.4 Existing mapping support is broad; selection is still syntactic

The current implementation is already stronger than the frozen first-version
paper boundary. The Python side reads mapping declarations from the solc AST,
supports parameter and `msg.sender` keys, nested mapping key products, and
scalar fields of mapping struct values. The ESBMC assertion pass resolves each
key at entry, snapshots it, indexes one array level per mapping level, and emits
R1 plus absolute/delta R2 candidates. The Foundry emitter computes the matching
storage slot, including struct member offsets. These are implemented mechanisms,
not future work.

What is missing is dependency-guided selection. `propose_slot_coords()` forms a
type-compatible cross product and truncates it by list order. It does not use the
target unit's guards, state reads, or probe evidence to rank the slots that can
actually separate paths. On a deeply nested store this spends the coordinate
and assertion budgets on plausible but irrelevant keys before the relevant one.
The required static change is selection/ranking and explicit cap accounting,
not another mapping encoder.

### 25.5 Current R2 is depth zero and process-inefficient

The production R2 proposer currently uses a parameter name as an endpoint. It
can ask absolute equality-shaped intervals and exact/capped directed deltas, and
the ESBMC pass batches all variables sharing that one endpoint in one query.
Identity endpoints are width-filtered; antichain reduction exists. This recovers
setters such as `post == argument`, but it is not the frozen depth-one language.

Missing pieces are a typed term AST over rendered coordinates, pre-state reads,
contract constants and source integer literals; depth-one `+`, `-`, `*`, and
division by a nonzero literal; form (7) `post == e`; same-query definedness; and
one batch of all fuzz-surviving terms per path. The present cap of six endpoint
specifications still means up to six cold ESBMC processes. Also,
`fuzz_prefilter_verdicts()` is unit-tested but has no production call site, so
the advertised fuzz-first cost reduction currently does not occur.

## 26. Static implementation order after the investigation

The safe order is now fixed:

1. add the dedicated exit-latched hybrid probe schema and synthetic fire/silent
   controls, without changing ordinary branch or path coverage;
2. make its blocking/payload classification exclude harness-only nondeterminism
   and emit coordinate-promotion evidence;
3. feed that evidence into deterministic mapping-slot ranking;
4. replace endpoint strings with typed depth-one R2 terms, integrate one-sided
   Forge refutation, and submit all survivors in one ESBMC batch per path;
5. build at low concurrency and run only focused synthetic regressions;
6. freeze the strong recipe, then spend the queue-head POC's single official
   8-GiB run.

No real POC, Forge corpus run, or benchmark run was used to establish the facts
in Sections 25--26; they come from source inspection and archived outputs.

## 27. Exit-latched probe implementation and synthetic controls

The dedicated hybrid probe is now implemented without composing the two
ordinary coverage passes. `--path-cov-probe` requires Solidity path coverage,
branch-function coverage, all-witness enumeration, and at least two witnesses.
The branch-function pass is suppressed; the Solidity path pass owns one boolean
latch per conditional-GOTO arm, updates it at the decision, and checks it only
at the unit's physical exits. Probe claims are kept out of the complete-path
denominator and have their own outcomes and report schema.

Every probe counterexample explicitly reads the protected runtime `tr/cnt`
ghosts and is grouped by the observed `(unit, path_id, decision_depth)`. The
multi-witness blocker keeps scalar function-local Solidity nondeterminism and
reproducible `msg.sender`/`msg.value`, while dropping harness-only and aggregate
nondeterminism. The report separates varying rendered coordinates from varying
entry-state, other-environment, and external-return quantities, so the latter
are coordinate-promotion evidence rather than silently spent witness budget.
Stage-one collection, direct generalisation, and imported-report validation all
carry and verify the new probe flags. Probe witnesses are merged into the same
whole-vector member pool as ordinary path witnesses only after path attribution.

Two focused Solidity regressions lock both required controls. The fire case
uses constructor-selected storage and a branch in a payable target: at least one
branch-arm goal fires, every observation is attributed, and variation outside
the current input coordinate set is reported. The silent case is a branchless
payable target and reports zero goals and zero observations. Both descriptors
carry `--memlimit 8g`; together they passed serially in 2.15 seconds. Three
pre-existing path-coverage regressions (multi-witness reporting, default single
witness, and slicing-protected payload) also passed serially in 2.92 seconds.
The two Python pipeline test modules pass, including a new fail-closed check
that a stage-one manifest requesting probes cannot omit `--path-cov-probe`.

The low-concurrency ESBMC target build passed. Refreshing CMake exposed an
unrelated existing Bitwuzla dependency problem (`cadical/cadical.hpp` and
`cadical/tracer.hpp` absent while its nested Meson build runs); CMake still
completed and generated the tests, and the already configured ESBMC binary and
the focused Z3-backed regressions ran successfully. No real POC was launched,
so the queue-head attempt remains unspent.

## 28. Dependency-selected oracle candidates

Region and oracle stages now consume one shared solc-reference dependency
closure from `scripts/solidity_ast_dependencies.py`. The closure starts at the
target implementation, follows modifiers and transitive implemented calls, and
orders referenced state declarations by call distance. An unreadable AST or an
unresolved target returns `None`, never an implicit all-mappings fallback.

This closes a former split policy. Region coordinates were dependency-filtered,
but the PUT driver independently regenerated every type-compatible mapping key
product. The oracle query now uses the same closure for mapping slots and for
ordinary scalar state. On the queue-head AST, `setDistributor` resolves to
exactly `_distributor` and `_owner`; inherited ERC20 mappings and
`_totalSupply` no longer consume assertion claims. The old helper was removed
from the generaliser rather than retained as a second implementation.

The PUT unit suite includes a negative control that an unrelated nested mapping
is named as excluded while the dependent one still produces both sender- and
parameter-keyed slots. The existing generaliser tests still verify direct,
modifier, and helper references plus the real queue-head closure.

## 29. Typed single-batch R2

The production R2 path no longer calls ESBMC once per endpoint. One additional
spec per certified path carries all selected variables and three structured
candidate vectors: equality terms, absolute ranges, and direction-safe delta
ranges. The strong recipe is version 6 and records `--r2-depth 1`, a 96-term
deterministic prefix, and a global 128 structured-R2-candidate cap. Both kinds
of truncation are logged as `NOT ASKED`; neither creates another solver
process. Candidate allocation first round-robins candidate kinds within each
variable and then round-robins variables, so every variable receives one claim
before any variable receives its second whenever the budget permits it.

The term grammar currently supports depth zero or one. Atoms are the candidate's
own pre-state, rendered numeric/identity coordinates, integer literals from the
target body, and literal-valued constants from the target contract's linearized
bases. Operations are `+`, `-`, `*`, and division by a nonzero literal. The
ESBMC side consumes JSON term trees directly, snapshots coordinates at entry,
checks narrowing by round-trip cast, checks arithmetic overflow/underflow, and
conjoins definedness and interval ordering with the candidate in the same
claim. An undefined term is REFUTED, never vacuously accepted. The Foundry side
renders only terms present in the generated lookup and uses the actual lifted
identifier, including renamed parameters and established `msg.sender`.

The implication antichain now removes an exact interval when the equivalent
`post == term` also holds. Structured postconditions and absolute intervals are
success-guarded under a revert-tolerant call; only a delta whose lower endpoint
is the literal zero remains safe after a swallowed rollback. This prevents a
strong setter oracle from making the unmodified contract red merely because
the wrapper tolerated a revert.

Verification completed without a real POC:

- single-thread ESBMC target build: passed;
- Python byte compilation: passed;
- generaliser checks: passed;
- PUT checks: 124/124 passed;
- typed-term Solidity regression: passed in about one second with 8 GiB set;
- existing named mapping-delta regression: passed;
- exit-latched probe fire and silent controls: passed.

The typed regression proves a normal-path `post == pre + 7`, refutes
`post == pre + amount`, proves exact absolute and delta forms, and refutes an
overflowing `pre + UINT256_MAX` term in the same query. It also contains a
must-flip control: a variable enters at `UINT256_MAX`, exits at `6`, and asks
`post == pre + 7`; definedness makes it REFUTED, while deleting the overflow
guard would make the wrapped equality HOLDS. CMake refresh again
printed the known nested Bitwuzla/CaDiCaL header failures but returned success;
the ESBMC target itself built and the configured regressions executed.

Two term-language gaps remain explicit. Cross-variable pre-state atoms are not
yet in the grammar: `pre` means the current candidate's own entry value. Constants
whose initializer requires Solidity constant folding are skipped rather than
re-evaluated by Python.

## 30. Production R2 fuzz refutation, and its exact boundary

The version-6 official recipe enables `--fuzz-r2-prefilter`. After the R1
ladder has identified state direction and the typed R2 batch has been generated,
the PUT driver emits one temporary Foundry test function per selected R2
candidate and executes them in one Forge process. Each function carries a
128-bit random marker. A candidate is removed only when Forge reports
`Failure` and the failure reason starts with the exact full label: marker,
variable, and candidate text. `Success`
is recorded as `NOT-REFUTED`, never `HOLDS`; an absent test, malformed JSON,
timeout, compile/setup failure, or unrelated panic is `NOT-RUN`. Every
`NOT-REFUTED` and `NOT-RUN` candidate remains in the one ESBMC R2 batch.

The Forge source size is bounded independently of the R2 proof language. The
official prefix is 128 candidate functions with 256 fuzz draws each. Candidates
outside that prefix are not discarded: each is recorded as outside the Forge
budget and retained for ESBMC. `put.json` records requested, selected, rendered,
actually run, refuted, not-refuted, and not-run counts plus every candidate's
test name, marker, verdict, and reason. The temporary test filename includes
the process id and nanosecond timestamp, is removed on every normal/error/timeout
path, and cannot overwrite an existing project test. The exact generated source,
Forge JSON, and stderr remain under the PUT work directory as evidence.

A synthetic payable setter validated the complete production call chain without
using a real POC. With 12 terms, all 26 candidates were rendered in one Forge
run; 24 received labeled concrete counterexamples, two survived, and the single
ESBMC batch proved exactly `post == pre + 7` and `post - pre == 7`. The final
generated PUT passed 32 Foundry fuzz runs on the unmodified contract. A second
run with a candidate budget of eight recorded 26 requested, eight selected and
refuted, and 18 `NOT-RUN`; its ninth candidate explicitly said it was outside
the budget and retained for ESBMC. The ESBMC batch then received all 18
survivors. Python checks remain 120/120 green.

This does not implement region fuzzing or R1 fuzz filtering. Region fuzzing
needs a sound executable predicate for "this input followed the same path".
Today path identity and the branch/condition latches exist only in the internal
GOTO instrumentation; the emitted Foundry replay has no dynamic path-id oracle.
Treating equal call success/revert status as equal path would merge distinct
branches sharing an exit and is unsound. A future region-fuzz layer must first
export those latches into source-level instrumentation, or provide an equivalent
path discriminator. Until then, region candidates are probed and certified by
ESBMC, and no result may claim they were fuzz-prefiltered. The real queue-head
POC remains unspent.

## 31. Review hardening before the first real POC

The post-implementation verifier and code reviewer found production-path
gaps. All were resolved before spending the queue-head run.

First, dependency policy is now `solc-reference-closure/3`. Arity was not a
sufficient overload identity: `f(uint256)` and `f(address)` have the same
arity. The exact report identity has the
`sol:@C@<contract>@F@<unit>#<solc-node-id>` shape, so stage 2 now writes it into
`generalise-result.json`, both certification sweep writers copy it into the
CERTIFIED row, and stage 4 passes it back as `--path-function`. PUT selects the
fresh emission claim by that full identity and uses the trailing node id for
parameters, returns, dependency closure, and source literal collection. Legacy
certification rows without the field remain readable only if simple unit plus
path id resolves to one unique path function; overload ambiguity is a hard
refusal. Same-arity synthetic overloads touch disjoint state, use different
parameter and return types, and contain different source literals; the tests
verify that all four readers select the same declaration.

The same exact identity also reaches every solver query. Outer-box, geometric,
refine, certification, R1, and typed R2 specs write the mangled identity rather
than returning to the simple source name after enumeration. Both certification
drivers accept `--path-function` for an explicitly selected overload. This is
required independently of AST selection: exact parameter facts beside a region
query that merged two overloads would still be a certificate about the wrong
unit.

Second, the assertion protocol now supports `vars_policy: state-exact`. The
official driver always writes this policy and an explicit `vars` array,
including an empty one. It is an exact whitelist of state observables only;
return-value rungs remain independently enabled. Therefore an empty dependency
closure cannot fall back to scanning every state variable, a slot-only closure
cannot re-enable unrelated scalar frame conditions, and naming state no longer
suppresses a useful return oracle. The focused `exact_empty_state` regression
uses an empty state whitelist on a contract with unrelated storage and proves
that only the three return candidates are emitted. Legacy specs that omit the
policy retain their old all-state behavior.

Third, typed R2 is proposed only for variables whose R1 table contains the
unsigned ordering pair. Boolean state and boolean mapping slots therefore
produce no Python R2 request and are named as omitted. The C++ consumer also
refuses a hand-written structured bool spec instead of silently counting it as
asked while emitting no claim. The `bool_r2_refused` regression pins that
protocol boundary.

Fourth, `--r2-candidate-budget 128` is a global cap on actual structured R2
claims, not merely on source terms or Forge probes. Baseline non-vacuity, R1,
and return claims in that same query are additional and recorded separately by
the ladder. The structured cap is recorded in
`put.json`; omitted claims are explicitly NOT ASKED. The official Forge prefix
is also 128, so every solver candidate is eligible for cheap refutation unless
rendering itself is unsupported.

The cap scheduler was subsequently tightened after review. The earlier
round-robin was over `(variable, candidate-kind)` queues, so if the number of
queues exceeded the budget, late variables could still receive no claim. The
current two-level scheduler gives each variable one candidate per global lap
and rotates equality/absolute/delta locally. A 50-variable control under the
official 128-candidate cap retains exactly 128 claims and represents all 50
variables. If there are more variables than budget, the unavoidable unserved
count is printed explicitly as NOT ASKED.

`r2_candidates()` uses the same two-level order before applying the independent
Forge prefix, so lowering only `--fuzz-r2-candidate-budget` cannot return to a
variable-major prefix. Empty, rollback, and entirely unrenderable Forge paths
now write the full accounting schema: requested, selected, rendered, ran,
refuted, not-refuted, and not-run. The invariant
`requested = refuted + not_refuted + not_run` therefore remains checkable even
when no Forge process was issued.

Overload identity also owns persistence and artifacts, not only queries.
Explicit overload certification uses `(benchmark-or-poc, unit, path_function)`
as its resume key and a `__pf<node-id>` workdir suffix. Stage 4 gives each
path-function row a separate PUT workdir. A truly overloaded Solidity source
name receives `_pf<node-id>` in its generated test function, test contract, and
`.t.sol` filename; non-overloaded functions keep the historical names. The B
gate reads the exact `put.json.test` name, so it cannot reconstruct away that
identity during `--forge-only` reporting.

`scripts/test_solidity_path_put_forge.py` is a no-mock integration test over a
temporary real Foundry project. It exercises production assembly, Forge 1.7.1
JSON, full-label attribution, filtering, budget retention, and temporary-file
cleanup. Its three-candidate control produced one labeled REFUTED candidate,
one NOT-REFUTED candidate that remained for ESBMC, and one budget-excluded
NOT-RUN candidate that also remained. It is registered in CTest with an
explicit skip code only when Forge or the checked-out forge-std dependency is
unavailable; a protocol or assertion failure is red. The final static gate is:
ESBMC target build passed; 124/124 PUT tests passed; generaliser tests passed;
the real Forge integration passed; and six serial focused Solidity CTests
passed in 5.16 seconds with 8-GiB descriptors. No real POC, benchmark, corpus,
or sweep was run. The queue-head attempt remains unspent.

## 32. Queue-head POC attempt and journal salvage repair

The queue-head official POC attempt has now been spent:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__setDistributor --stage all --fresh
```

It ran under recipe `veriput-strong/6`, `--timeout 60`,
`--memlimit-gib 8`, `jobs=1`, and `--solidity-max-tx 1`. Stage 1 focused
`Distributor.setDistributor` through `--contract FarmingPool` and instrumented
5 complete paths, but the outer 60s timeout killed the run before ESBMC wrote
`reports/Distributor__setDistributor.json`. Stage 2 therefore recorded
`NO-WITNESS-UNKNOWN`, and Stage 3 correctly refused an empty PUT sweep. This is
not evidence that PUT generation or R1/R2 synthesis failed; they never received
any certified region.

The important observation is that the killed run did leave
`work/Distributor__setDistributor/cov-ce-journal.json` with
`claims_decided=29`, `claims_total=35`, and 5 witnessed feasible path claims.
Each path had up to 8 witness payloads. The loss was in the external pipeline:
`pathcov_collect.py` only copied a completed `cov-report.json` into
`reports/`, so a useful partial CE journal was treated the same as no witness
at all.

The repair is deliberately one-sided. `pathcov_collect.py` now converts a
live CE journal into a Stage-2 enumeration report only when the journal already
contains solver-refuted feasible path witnesses. The synthetic report is stamped
`partial=true` and records `veriput_salvage.from = cov-ce-journal.json`; it
does not claim full coverage and does not turn undecided claims into evidence.
It normalizes journal list-shaped payloads into the dict-shaped report schema
that `solidity_path_generalise.py` already consumes, including `msg.sender` /
`block.timestamp` environment names, input coordinates, entry storage, final
state, extcall extras, per-path witnesses, and summary counters. Legacy
journals that lack `path_depth` recover it from ESBMC's path encoding
(`tr = 1; tr = tr*2 + guard_value`), i.e. `floor(log2(path_id))`; future
journals should not need that fallback.

ESBMC's own CE journal writer now emits `path_id`, `path_depth`,
`path_function`, and report-style `condition` for every witnessed path. The
depth comes from the existing `path_decision_depth` map, the same metadata used
by the path assertion/certification checks. This makes future timeout salvage
unambiguous and keeps overload identity available before the full report exists.

Verification after the repair:

```
python3 scripts/test_pathcov_collect.py
python3 scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_put.py
cmake --build build --target esbmc -j2
ctest --test-dir build -R '^regression/scripts/pathcov_collect$' \
  --output-on-failure
ctest --test-dir build -R \
  'regression/esbmc-solidity/solidity_path_cov_ce_journal_survives_death|regression/esbmc-solidity/solidity_path_cov_ce_journal_absent_without_report' \
  --output-on-failure
```

All passed. The CMake regeneration still printed the known nested
Bitwuzla/CaDiCaL missing-header failures before falling back to the already
found Bitwuzla 0.8.2; the final `esbmc` target built successfully. `cppcheck`
on `src/goto-programs/goto_coverage.cpp` reported only the existing
`unknownMacro` parser limitation at `Forall_goto_program_instructions`, not a
must-fix warning.

Concrete implication for the next real POC: a timeout after some path claims
are refuted should no longer produce a zero-region Stage 2 solely because the
final coverage report was not written. It will still be honest partial
evidence: only paths with actual CE payloads enter certification, and ESBMC
must still certify any region before PUT generation.

## 33. Updated POC retry budget

The per-POC ESBMC budget rule has changed. The old "one official ESBMC campaign
per POC" limit is replaced by a three-attempt gradient, still serial and still
not an invitation to iterate after every one-line edit:

1. Attempt 1: 60 seconds, 8 GiB memory limit.
2. Attempt 2: 120 seconds, 8 GiB memory limit.
3. Attempt 3: 600 seconds maximum, 10 GiB memory limit.

The first two attempts preserve the old 8-GiB discipline; the third is the
maximum escalation cell and may use 10 GiB. The rule applies per POC, not per
individual ESBMC subprocess inside a stage: the official POC driver may run
Stage 1/2/3 under one attempt's timeout/memory settings, then escalate only if
the attempt fails to produce a usable PUT result. Fuzz remains refutation-only:
extra attempts can prove/certify with ESBMC, but fuzz may only remove candidates
or expose cheap counterexamples.

`notes/coverage/scripts/poc_one.py` now has `--attempt 1|2|3`, and this is the
preferred way to invoke the official ladder. `--long` remains only as a legacy
manual override and should not be used for accounting unless there is a separate
reason to leave the ladder.

Consequences for already consumed POCs:

- `farming__Distributor__setDistributor`: attempt 1 was spent at 60s/8GiB and
  failed before Stage 2 because Stage 1 did not publish a completed report.
  The journal salvage repair means attempt 2 may reuse the stronger pipeline
  shape, but it must be counted as the second official attempt for this POC.
- `farming__Distributor__distributor`: attempt 1 was spent at 60s/8GiB. Stage 1
  fully covered 2/2 paths; Stage 2 timed out after level0/bracket/refine with
  no certified region. Attempt 2 may run at 120s/8GiB unless a code-level fix
  removes the need first.

## 34. Distributor getter attempt 2 and Stage-3 bottleneck

`farming__Distributor__distributor` has now also spent attempt 2:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__distributor --stage all --fresh --attempt 2
```

The official ladder used 120 seconds and 8 GiB. Stage 1 completed in 22.3s and
published a complete 2/2 report. Stage 2 completed in 45.8s and certified both
paths:

- path 2, the synthetic non-payable ABI reject path:
  `msg.value in [1, UINT256_MAX]` plus pinned entry state.
- path 3, the body path:
  `msg.value == 0` plus the same pinned entry state.

This is the expected result after the structural ABI-gate shortcut: the only
decision in the complete path is the compiler/front-end ABI `msg.value` gate,
so the split is certified without spending a generic region-certification
search on a trivial one-branch partition.

Stage 3 initially crashed before any PUT measurement because
`notes/coverage/scripts/put_all.py` referenced `plabel` while constructing the
per-piece workdir but only computed `encs`. The fix is to derive the same
`p<K>` suffix used by the emitter:

```
pf_label = path_function_artifact_suffix(path_function)
plabel = f"p{piece}" if piece else ""
wd = os.path.join(
    OUT, "_wd", f"{bench}__{unit}{pf_label}__{enc}{plabel}{arm}")
```

After that one driver fix, rerunning Stage 3 only under the same attempt-2
budget completed in 143.7s:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__distributor --stage 3 --attempt 2
```

The attempt-2 result was intentionally NOT a generalized PUT success:

- path 2 emitted
  `FarmingPoolCovTest_FarmingPool_distributor_put2.t.sol` with one fuzzed
  coordinate (`msg.value`), but no non-exit oracle. It is a rollback/reject
  path. Storage and return rungs are dropped because the observable post-state
  after a revert is restored state, and the return rungs are vacuous.
- path 3 has the useful getter oracle (`return == 0` holds and `return != 0`
  is refuted), but the certified region renders no parameterized coordinate:
  `msg.value` is pinned to 0 and all state is pinned/established. The emitter
  therefore refuses it as not parameterized instead of pretending a concrete
  replay is a PUT.

At that older head the POC reported `B = 0`: one path had width without a
counted oracle, the other had an oracle without width. The first half was later
fixed by the Stage-1 value/revert accounting work; the second half remains a
real methodological bottleneck, not an ESBMC timeout. Getter-style units over
constant or fully pinned tx-1 entry state are low-value targets for
parameterized unit tests unless the method intentionally supports
parameterized state setup. Fuzz cannot prove the getter body path; it can only
cheaply refute bad assertions or bad regions when there is an exposed runtime
parameter to vary.

Attempt 3 has now been spent for Stage 3 only:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__distributor --stage 3 --cell gate --attempt 3
```

The tier was 600s/10GiB; wall time was 89.9s. Current head
`52c643bb8c` produced:

- path 2: `B`. The PUT fuzzes `msg.value`, preserves the non-payable low-level
  value call, counts/asserts the exit-kind oracle, and is Forge green.
- path 3: still refused as `NOT PARAMETERIZED`. It has the expected
  `return == 0` oracle, but the certified region renders no coordinate with
  width greater than one: `msg.value == 0`, and all state is constructor-pinned
  or established.

The current result is therefore `B = 1 of 2`; no further official attempt
remains for this POC under the three-tier budget.

## 35. setDistributor ground truth and structural decision fast path

`farming__Distributor__setDistributor` attempt 2 has now been spent under the
official 120s/8GiB ladder:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__setDistributor --stage all --fresh --attempt 2
```

Stage 1 completed in 118.3s and wrote a full 5/5 report, so the journal salvage
path was not needed for this run. Stage 2 then timed out at 120s:

```
KILLED, 0 certified / 0 not / 5 witnessed
5 path(s) reached NO verdict
level 0 HAD decided 5 of them at 16.2s
3 free coordinate(s): distributor_, msg.sender, msg.value
```

The important diagnosis is that the source-level ground truth is simple and
should not need the geometric/refine ladder at all:

```solidity
modifier onlyOwner() { _checkOwner(); _; }
function _checkOwner() internal view virtual {
    if (owner() != _msgSender()) revert OwnableUnauthorizedAccount(_msgSender());
}
function setDistributor(address distributor_) public virtual onlyOwner {
    if (distributor_ == address(0)) revert ZeroDistributorAddress();
    emit DistributorChanged(distributor_);
    _distributor = distributor_;
}
```

With the tx-1 entry slice pinned to `state._owner == 1` and
`state._distributor == 0`, the expected complete-path regions are:

- enc=2: ABI non-payable reject,
  `msg.value != 0`; `distributor_` and `msg.sender` are irrelevant to the body.
- enc=12: body path, non-owner rollback, zero distributor:
  `msg.value == 0`, `msg.sender != 1`, `distributor_ == 0`.
- enc=13: body path, non-owner rollback, nonzero distributor:
  `msg.value == 0`, `msg.sender != 1`, `distributor_ != 0`.
- enc=14: owner call, zero-distributor rollback:
  `msg.value == 0`, `msg.sender == 1`, `distributor_ == 0`.
- enc=15: owner call, normal success:
  `msg.value == 0`, `msg.sender == 1`, `distributor_ != 0`.

The report's enc=13 shows `DistributorChanged` and `_distributor` updated even
though the path is a rollback revert. That is not evidence of a chain-observable
post-state; the Solidity revert model can continue through later instrumentation
after the rollback marker. The existing emitter is right to drop layer-2/3
storage/event rungs on rollback paths. For PUT value, enc=15 is the strong path:
it should fuzz `distributor_` over nonzero addresses and assert the semantic
oracle `_distributor post == distributor_` (plus related R1/R2 rungs if ESBMC
proves them). Enc=2/12/13 are mostly exit-kind-only negative paths; enc=14 is a
point revert path.

The code repair is a structural decision-region fast path in
`scripts/solidity_path_generalise.py`. It recognizes only complete paths whose
recorded decisions are simple `==` / `!=` clauses over a rendered coordinate
and a constant or pinned state getter:

- ABI `msg.value` gate,
- `_msgSender()` resolved to `msg.sender`,
- `return_value$_<state>$N` resolved only when `state.<state>` is pinned,
- address-like coordinates bounded to `[0, 2^160-1]`, `msg.value` to
  `[0, 2^256-1]`,
- coordinate-to-coordinate constraints are refused and fall back to the old
  ladder.

If every witnessed path of the unit is covered by that grammar, Stage 2 now
skips level0, witness-pool probes, per-path ladders, geometric bracket, linear
refine, and ESBMC certification queries. The certification source is recorded
as `structural-simple-decision`. This is still proof-side logic, not fuzz:
it certifies only the product region implied by the already-enumerated complete
path decision sequence.

Validation without consuming another ESBMC POC attempt:

```
python3 scripts/solidity_path_generalise.py ... \
  --enumeration-index notes/coverage/pathcov/farming__poc_Distributor_setDistributor_gate/index.json \
  --enumeration-report notes/coverage/pathcov/farming__poc_Distributor_setDistributor_gate/reports/Distributor__setDistributor.json \
  --level0 --level0-perturb --probe-witnesses 8 --probe-ladder ...
```

With the existing Stage-1 report, this completed in 0.047s, printed no ESBMC
solver round, and wrote 5/5 certified regions. Running the real wrapper stage
only:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__setDistributor --stage 2 --fresh --attempt 2
```

completed in 0.5s with `CERTIFIED, 5 certified / 0 not / 5 witnessed`. This
rerun reused the already-spent Stage-1 report and did not start a new ESBMC
enumeration/certification run. Stage 3 has deliberately not been rerun yet:
before spending the remaining POC budget, inspect whether the emitter will spend
unnecessary ladder/R2 ESBMC queries on rollback paths whose only observable
oracle is the exit kind, and prioritize enc=15 as the expected strong PUT.

Stage 3 now has that first scheduling guard. `notes/coverage/scripts/put_all.py`
reads `exit_kind` from the Stage-1 enumeration report named by each certify row
and emits normal-exit certified regions before rollback/unknown ones. This does
not change any region, oracle, or proof claim; it only changes the order in
which expensive PUT/R2 ESBMC work is attempted. For `setDistributor`, a
`--forge-only` dry measurement over the refreshed cert file now prints enc=15
first, followed by enc=2/12/13/14. This means the remaining attempt-3 Stage 3
run should try the only expected strong PUT before spending time on exit-only
negative paths.

Attempt 3 was then spent for Stage 3 only:

```
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__setDistributor --stage 3 --attempt 3
```

It exited 0 after 744s wall clock. The configured attempt tier was
600s/10GiB; that limit applies to the ESBMC subprocesses, while the wrapper
serially ran five certified regions. The scheduling guard worked: enc=15 ran
first and produced the expected strong PUT:

- region: `msg.value == 0`, `msg.sender == 1`,
  `distributor_ in [1, 2^160-1]`, entry state `_owner == 1`,
  `_distributor == 0`, `_totalSupply == 0`;
- fuzz: one parameter, `distributor_`;
- oracle: 19 post-state assertions, including the important semantic assertion
  `_distributor post == distributor_`;
- B gate: yes.

The remaining regions behaved as exit/rollback cases:

- enc=2 wrote a value-gate PUT with `msg.sender`, `msg.value`, and
  `distributor_` fuzzed, but the old stats ledger reported 0 oracle assertions
  even though the emitted test contains the concrete `assertFalse(ok5, "value
  sent to a non-payable entry must revert")`. This was a bookkeeping bug, not
  an absence of an exit oracle.
- enc=12 and enc=13 wrote rollback PUTs with two fuzz parameters and one
  exit-kind assertion each. Their post-state rungs are deliberately dropped:
  the ladder observed pre-rollback intermediate state, not chain-observable
  storage after revert.
- enc=14 refused: every rendered coordinate was width one
  (`msg.sender == 1`, `distributor_ == 0`, `msg.value == 0`), so emitting it
  would be a replay point wearing PUT syntax.

The Stage-3 table from that run was therefore `PUTs emitted = 4/5`,
`B = 3/5 emitted PUT(s)`, with enc=14 excluded as refused. This is the old
artefact's measured result. Do not quote it as the result of the new code below
unless Stage 3 is re-emitted.

Two follow-up code fixes are now in the working tree and have not consumed
another POC run:

1. `scripts/solidity_path_put.py` now skips the ESBMC R2 pass for rollback
   paths after the first assertion ladder has already identified
   `ROLLBACK revert`. Previously the Forge R2 prefilter was skipped, but the
   R2 candidate batch still went to ESBMC and was later thrown away before
   emit because rollback post-state is unobservable. This saves the slowest
   part of enc=12/13/14-style paths without changing any observable oracle.
2. The same file now counts the emitter's explicit low-level value-gate
   `assertFalse(ok...)` call as `exit_kind_asserts` in `stats.asserts`. The
   matcher is deliberately narrow: arbitrary user assertions such as
   `assertFalse(c0.flag())` are not classified as exit-kind oracles. This fixes
   the B Gate-3 accounting for enc=2-like PUTs: a nonpayable value-gate test
   with a real `assertFalse(ok)` should not be reported as assertion-free.

Focused verification already run:

```
python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py
python3 scripts/test_solidity_path_put.py
ctest --test-dir build -R '^regression/scripts/solidity_path_put$' --output-on-failure
```

All three passed. No additional `setDistributor` POC rerun has been spent after
these two code fixes.

## 34. POC ground truth before the next ESBMC spend

The next ESBMC run must not be used to discover source-level expectations. The
following is the current static ground truth from source, Stage-1 reports, and
old cert arms.

### `aqua_Aqua__Aqua__push`

Source shape:

```solidity
function push(address maker, address app, bytes32 strategyHash,
              address token, uint256 amount) external {
    Balance storage balance = _balances[maker][app][strategyHash][token];
    (uint248 prevBalance, uint8 tokensCount) = balance.load();
    require(tokensCount > 0 && tokensCount != _DOCKED, ...);
    balance.store(prevBalance + amount.toUint248(), tokensCount);
    IERC20(token).safeTransferFrom(msg.sender, maker, amount);
    emit Pushed(maker, app, strategyHash, token, amount);
}
```

Gate-cell Stage 1 currently witnesses only the nonpayable value path and the
inactive-strategy rollback path:

- `path:2`: ABI nonpayable reject, expected region `msg.value != 0`; body
  arguments irrelevant; only exit-kind oracle expected.
- `path:6`: body entered with `msg.value == 0`, then rollback because the
  entry mapping slot has `tokensCount == 0`; source condition is
  `!(tokensCount > 0 && tokensCount != 255)`. This path is not a strong
  post-state PUT in gate cell because the state write is rolled back and the
  relevant state is a nested mapping slot not established by a one-tx fixture.

Old evidence: default cert arm killed at 300s/8GiB; `--skip-bracket` arm
certified `path:6` in about 18s with wide regions over
`amount/app/maker/token` plus `msg.value == 0`. That says the geometric bracket
was the cost center, not the Solidity model. The source-level useful artefact
for this cell is exit-kind coverage, not a state oracle.

### `aqua_Aqua__Aqua__safeBalances`

Source shape:

```solidity
function safeBalances(address maker, address app, bytes32 strategyHash,
                      address token0, address token1) external view
    returns (uint256 balance0, uint256 balance1)
{
    (uint248 amount0, uint8 tokensCount0) =
        _balances[maker][app][strategyHash][token0].load();
    require(tokensCount0 > 0 && tokensCount0 != _DOCKED, ...);
    balance0 = amount0;
    (uint248 amount1, uint8 tokensCount1) =
        _balances[maker][app][strategyHash][token1].load();
    require(tokensCount1 > 0 && tokensCount1 != _DOCKED, ...);
    balance1 = amount1;
}
```

Gate-cell Stage 1 also witnesses only:

- `path:2`: ABI nonpayable reject, expected region `msg.value != 0`; exit-kind
  oracle only.
- `path:6`: body entered with `msg.value == 0`, first token inactive because
  entry mapping slot has `tokensCount0 == 0`; rollback before the second return
  value is semantically available.

Old evidence mirrors `push`: default killed at 300s/8GiB, `--skip-bracket`
certified `path:6` in about 19s with wide regions over
`app/maker/token0/token1` plus `msg.value == 0`. The expected PUT surface is
again exit-kind for the gate cell. A strong return oracle requires an artefact
cell that first establishes active mapping slots, e.g. through `ship`/`push`,
not this one-tx post-constructor gate cell.

### `farming__FarmingPool__deposit`

Source shape:

```solidity
function deposit(uint256 amount) public virtual {
    _mint(msg.sender, amount);
    if (balanceOf(msg.sender) > _MAX_BALANCE) revert MaxBalanceExceeded();
    STAKING_TOKEN.safeTransferFrom(msg.sender, address(this), amount);
}
```

Relevant internal decisions:

- `_mint` rejects `msg.sender == 0`.
- `_update` adjusts `_totalSupply` and `_balances[msg.sender]`, and FarmingLib
  `updateBalances` introduces `amount > 0 && from != to` and zero-address
  branches.
- The final `safeTransferFrom` introduces an external-call success/failure
  split that a generated test cannot choose unless a deterministic mock/stub
  fixture is part of the cell.

Gate-cell Stage 1 witnesses seven paths: `2`, `26`, `27`, `246`, `247`,
`3622`, `3623`.

- `path:2`: ABI nonpayable reject, `msg.value != 0`, expected exit-kind-only
  PUT. This is structurally certifiable and should not consume the heavy ladder.
- `path:26`/`27`: same settable inputs and state, differ only in
  `extcall.success`; both are therefore inseparable by any product region over
  generated-test inputs.
- `path:246`/`247`: same issue, only `extcall.success` separates rollback vs
  normal continuation around the external transfer.
- `path:3622`/`3623`: same issue plus very large `amount`; old shrink logs show
  the loop chasing `amount` while the remaining discriminator is still
  external-call behavior.

`pathcov_predict.py` already reports this gate cell as NO-GO for exactly those
three inseparable pairs. That is not a proof failure and not a budget failure:
the cell is asking a generated PUT to control a callee behavior bit. Correct
next actions are either:

- static attribution/early-stop those paths and keep only value-gate work in
  the gate cell; or
- define an artefact/stub cell where the ERC20 mock deterministically realizes
  the desired success/revert behavior, and record the extcall pin as fixture
  provenance.

### Policy implication

For the next official attempt, do not spend ESBMC on the geometric bracket for
these POCs. The old Aqua arms demonstrate that `--skip-bracket` reaches the
same useful certification quickly, while `deposit` demonstrates a separate
uncontrolled-extcall limitation that no bracket can repair.

Implemented policy update after this investigation:

- `notes/coverage/scripts/poc_one.py` strong recipe is now
  `veriput-strong/7` and includes `--skip-bracket` by default. The rest of the
  strong recipe still keeps level0, perturbation, witness probes, probe ladder,
  refinement, state pinning, environment promotion, and slot-coordinate support.
- `scripts/solidity_path_generalise.py` now has a refutation-only static filter
  for witnessed siblings that agree on every generated-test-settable payload
  value and differ only on `extcall.*`. It is off by default and is enabled by
  `notes/coverage/scripts/poc_one.py` only for the official gate cell, because
  an artefact/stub cell may intentionally realize external-call success or
  failure. The filter only fires when both sibling payloads actually contain the
  differing `extcall.*` value; asymmetric/missing harvest evidence is not enough
  to attribute the split. Those paths are recorded as `NOT_CERTIFIED` with a
  method-level reason, `concrete_fallback=false`, and removed from region
  search; no ESBMC bracket/refine/certification query is started for them.
- If every witnessed path is removed by that attribution, the driver disables
  level0/probe/bracket/refine and writes only the not-certified reasons. The
  `enumerated` field still records the original Stage-1 witnessed path set.
- `certify_all.py`, `certify_summary.py`, and `certify_arms.py` now record or
  compare `static_extcall_inseparable`, so a static-attribution arm cannot be
  silently aggregated with a normal certification arm.
- `scripts/solidity_path_generalise.py` accepts the legacy Stage-1 POC index
  format used by most `notes/coverage/pathcov/*_poc_*_gate/index.json` files:
  no `veriput-pathcov-collection/2` schema, command stored as `cmd` rather than
  `cmdArgv`, and no probe-witness provenance. The compatibility path still
  checks source path, AST path, contract, unit set, max-tx, memlimit, focus
  function, report directory/tag, and solver flags. It prints a warning that
  witness-pool widening is unavailable for those legacy reports instead of
  forcing a Stage-1 rerun.

Focused checks already run after the change:

```
python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/poc_one.py
python3 scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_put.py
ctest --test-dir build -R '^regression/scripts/solidity_path_generalise$' --output-on-failure
ctest --test-dir build -R '^regression/scripts/solidity_path_put$' --output-on-failure
git diff --check
python3 notes/coverage/scripts/poc_one.py aqua_Aqua__Aqua__push --stage 2 --attempt 1 --dry-run
python3 notes/coverage/scripts/poc_one.py farming__FarmingPool__deposit --stage 2 --attempt 1 --dry-run
python3 notes/coverage/scripts/poc_one.py aqua_Aqua__Aqua__safeBalances --stage 2 --attempt 1 --dry-run
```

The dry-runs confirmed the official Stage-2 command now carries
`--skip-bracket`, `--static-extcall-inseparable`, `--timeout 60`,
`--run-timeout 60`, and `--memlimit-gib 8` for attempt 1.

Actual POC spend after this change:

```
python3 notes/coverage/scripts/poc_one.py farming__FarmingPool__deposit --stage 2 --attempt 1 --fresh
```

Result: completed in 0.3s wall, reused the legacy Stage-1 report, and started no
enumeration ESBMC process. It produced `7` witnessed paths, `1` certified region,
and `6` static `NOT_CERTIFIED` attributions:

- certified `enc=2`: structural `msg.value != 0` ABI value-gate region, with
  `amount`, `msg.sender`, `msg.value`, and
  `state._balances[msg.sender]` free over their full admissible ranges except
  `msg.value in [1, uint256_max]`.
- not certified `enc=26/27`, `246/247`, `3622/3623`: each sibling pair differs
  only on `extcall.success`; all six have `concrete_fallback=false`.

This matches the expected ground truth above. The first two wrapper invocations
before the legacy fix did not start ESBMC: one refused stale resume data, the
next wrote a `DRIVER-REFUSED` row at the enumerate-import check.

## 2026-08-06 Aqua gate-cell handoff

User retry budget is now official per PoC: attempt 1 = 60s/8GiB, attempt 2 =
120s/8GiB, attempt 3 = 600s/10GiB. Fuzz may be used anywhere as a cheap
refutation channel, including region/probe sanity, but never as proof.

Ground truth read from the Aqua source and solc AST:

- `push(maker, app, strategyHash, token, amount)` reads
  `_balances[maker][app][strategyHash][token]`, then requires
  `tokensCount > 0 && tokensCount != _DOCKED`, then stores the updated amount
  and calls `IERC20(token).safeTransferFrom(msg.sender, maker, amount)`.
- `safeBalances(maker, app, strategyHash, token0, token1)` reads two slots:
  `_balances[maker][app][strategyHash][token0]` and
  `_balances[maker][app][strategyHash][token1]`, and has the same active-token
  checks against `_DOCKED`.
- Both gate-cell Stage 1 reports witness two paths: `enc=2` is the ABI
  nonpayable reject (`msg.value != 0`), and `enc=6` is the body path with
  `msg.value == 0` reaching the inactive/docked balance guard.

Driver fixes implemented from these runs:

- Immutable/constant state coordinates, e.g. `state._DOCKED`, remain semantic
  pins in the printed/report region but are omitted from ESBMC query pins.
  They are not runtime inputs a generated PUT can set, and proving without the
  query assumption is stronger than asking ESBMC to resolve an unresolvable
  coordinate.
- Structural decision regions are now partially pre-certified. If one path
  has a simple source/ABI decision (`enc=2: msg.value != 0`) and a sibling path
  needs heavy region search, the simple path is removed from ladder/refine so
  the hard sibling cannot hide a usable certified gate.
- `unit_mapping_slot_accesses` reads concrete `IndexAccess` chains from the
  target's solc-resolved callable closure. `propose_slot_coords` spends mapping
  slot budget on those source slots first and suppresses the same mapping's
  guessed same-type cross product. This replaced Aqua's old wrong candidates
  such as `_balances[maker][maker][strategyHash][app]` with the real source
  slots above.
- Enumeration import now accepts raising Stage 2 memlimit over a completed
  Stage 1 report's memlimit, while still refusing a lower requested memlimit or
  any other identity mismatch. This is needed by attempt 3: legacy Aqua Stage 1
  was collected at 8GiB, while official attempt 3 certifies at 10GiB.

Actual Aqua POC spend:

- `aqua_Aqua__Aqua__push`
  - attempt 1, 60s/8GiB: no certification; `state._DOCKED` was wrongly sent as
    a query pin and rejected as unresolvable.
  - attempt 2, 120s/8GiB: after query-pin/partial-structural fix, result
    `1 certified / 1 not / 2 witnessed`. `enc=2` certified structurally;
    `enc=6` died in linear-refine with ESBMC `SIGABRT`.
  - attempt 3, 600s/10GiB: first wrapper call was `DRIVER-REFUSED` at 0s
    because Stage 1 said 8GiB and Stage 2 asked 10GiB; no ESBMC process was
    started. After the memlimit compatibility fix, the effective attempt ran
    12.4s and still produced `1 certified / 1 not / 2 witnessed`. That run is
    now known to be stale for the current coordinate logic: its saved
    `outer.json` still contains `[strategyHash]` aggregate slots, guessed
    `[maker][maker]...` cross-products, and `uint256` ranges for `Balance`
    leaves.
- `aqua_Aqua__Aqua__safeBalances`
  - attempt 1, 60s/8GiB: same query-pin refusal shape as push.
  - attempt 2, 120s/8GiB: `1 certified / 1 not / 2 witnessed`; `enc=2`
    structurally certified, `enc=6` hit ESBMC `SIGABRT`.
  - attempt 3, 600s/10GiB: ran 10.1s after source-slot suppression. Free
    coordinates reduced to the semantically expected nine:
    `app`, `maker`, `msg.value`, token0/token1, and the four real balance
    leaf slots. Result remains `1 certified / 1 not / 2 witnessed`; `enc=6`
    still aborts during linear-refine.

Current conclusion: `safeBalances` and `rawBalances` have already validated the
literal bytes32 slot and struct-leaf range fixes with official POC runs. For
`push`, the old SIGABRT artefacts do not describe the current query shape, and
there is no saved failed-round command from the pre-diagnostic run. Do not
spend more Aqua POC retries unless the budget policy is explicitly reopened; if
that happens, the first thing to inspect is whether the new
`failed-rounds/*.outer.json` contains only the literal-key source slot and
typed `uint248/uint8` leaves.

Immediate diagnostic patch after the Aqua attempts:

- `solidity_path_generalise.py` now records the exact ESBMC command line in
  every `run()` log, both for normal exits and timeouts.
- If an outer-box round exits abnormally or times out, the driver writes
  `<workdir>/failed-rounds/<kind>-NNN.outer.json`,
  `<kind>-NNN.log`, and `<kind>-NNN.meta.json`. The metadata keeps the round
  kind, failure classification, wall time, captured command, and filenames.
- This does not consume a POC retry by itself and does not certify anything.
  Its purpose is to make the next abort reproducible without rerunning the
  whole POC pipeline again.

POC source-level ground truth has been split into
`notes/VeriPUT_poc_ground_truth.md`. Read that file before spending another
attempt: it records the expected path, input region, and PUT oracle for Aqua
and Farming POCs, including the distinction between fuzz-refutable candidates
and ESBMC-certified regions.

## 2026-08-06 current safeBalances PUT repair

This section supersedes the older Aqua safeBalances diagnosis above for the
current branch state. Before spending a POC attempt, read
`notes/VeriPUT_poc_ground_truth.md` and check four facts from source plus
Stage-1/Stage-2 artefacts:

1. expected path and exit kind;
2. expected input region;
3. expected storage/input dependency region;
4. expected observable PUT oracle.

For `aqua_Aqua__Aqua__safeBalances`, that ground truth is now concrete:

- `enc=2`: ABI nonpayable reject. Region `msg.value != 0`; strong PUT is a
  low-level value call that asserts `ok == false`.
- `enc=6`: body path enters with `msg.value == 0` and reverts on the inactive
  first-token balance guard. The certified source dependency region is
  `maker/app/token0/token1` plus the four `_balances[maker][app][literal
  bytes32(0)][token{0,1}].{amount,tokensCount}` leaf slots fixed at zero.
  `state._DOCKED == 255` is a semantic constant pin, not an assert-query
  coordinate. Strong PUT is still exit-kind: the call must revert. Post-state
  and return-value rungs are unobservable on chain for this path.

Official spend in this branch:

```sh
python3 notes/coverage/scripts/poc_one.py \
  aqua_Aqua__Aqua__safeBalances --stage 2 --cell gate --attempt 1 --fresh
```

This consumed Stage-2 attempt 1 for safeBalances under 60s/8GiB. It completed
in 27.6s wall with `2 witnessed / 2 certified / 0 not certified`. The body
path `enc=6` certified the expected literal-key source slots, not the old
`strategyHash` guessed slot cross product.

```sh
python3 notes/coverage/scripts/poc_one.py \
  aqua_Aqua__Aqua__safeBalances --stage 3 --cell gate --attempt 1
```

This consumed Stage-3 attempt 1 for safeBalances. It emitted `enc=2` as a
strong value-gate PUT, but emitted `enc=6` as an assertion-free `try/catch`
replay. Two PUT-side defects were identified without needing another POC run:

- `put_all.py` read Stage-1 `exit_kind` but did not pass it into
  `solidity_path_put.py`, so ordinary `revert` paths were not turned into
  exit-kind oracles unless the assertion ladder printed the special rollback
  warning.
- `solidity_path_put.py` passed semantic pins such as `state._DOCKED` to
  `--path-cov-assert`, where ESBMC cannot resolve them as storage coordinates.
  It also regenerated guessed mapping slot candidates instead of first reusing
  the literal-key mapping slots already certified in Stage 2.

Current code repair:

- `put_all.py` passes `--exit-kind` from the Stage-1 report to
  `solidity_path_put.py`.
- `solidity_path_put.py` treats `exit_kind == "revert"` like the observable
  layer-1 oracle it is: for a revert-tolerant `try/catch`, it drops
  post-state/return rungs and emits `assertFalse(_put_ok, ...)` without marking
  the path as rollback.
- R2 ESBMC and Forge R2 prefilter are skipped for any reverting path because
  R2 rows are post-state/delta claims and would be dropped before emission.
- The PUT assertion spec filters out unqueryable semantic pins, while keeping
  them visible as semantic pins in the generated PUT/report.
- The assertion ladder asks first about mapping-member coordinates already
  present in the certified region. For Aqua this means literal-key
  `_balances[maker][app][0x20...][token].amount/tokensCount`, not guessed
  `[strategyHash]` candidates.

Validation already done without consuming another ESBMC POC attempt:

```sh
python3 -m py_compile scripts/solidity_path_put.py \
  scripts/test_solidity_path_put.py notes/coverage/scripts/put_all.py
python3 scripts/test_solidity_path_put.py
git diff --check -- scripts/solidity_path_put.py \
  notes/coverage/scripts/put_all.py scripts/test_solidity_path_put.py
```

`scripts/test_solidity_path_put.py` now has 131/131 checks green, including
regressions for literal-key certified region slots, semantic-pin filtering, R2
skip on ordinary revert paths, and Stage-1 revert paths becoming exit-kind
oracles. The next real validation should be exactly one safeBalances Stage-3
rerun at attempt 2:

```sh
python3 notes/coverage/scripts/poc_one.py \
  aqua_Aqua__Aqua__safeBalances --stage 3 --cell gate --attempt 2
```

Expected result: `enc=2` stays B; `enc=6` becomes B with a fuzzed
`maker/app/token0/token1` input region, zeroed literal-key balance slots, and
`assertFalse(_put_ok, ...)` as the oracle.

That validation has now been spent. The command above ran under 120s/8GiB and
exited 0 after 96.6s wall. Result:

- `B = 2 of 2 emitted PUT(s)`.
- `enc=2`: 5 fuzz coordinates (`msg.value`, `maker`, `app`, `token0`,
  `token1`) and one exit-kind assertion. This is the ABI nonpayable value
  reject.
- `enc=6`: 4 fuzz coordinates (`maker`, `app`, `token0`, `token1`), four
  established zero literal-key balance leaf slots with readback checks, and
  one exit-kind assertion:
  `assertFalse(_put_ok, "path enc=6 exits through a REVERT: ...")`.
- The assertion ladder still refuses literal bytes32 slot keys with:
  `the key ... cannot be expressed: the key does not resolve to an input of
  this unit`. This means no R1/R2 slot row was proved for the literal-key
  slots. It is a future ESBMC slot-coordinate resolver/modeling issue. It does
  not invalidate the current gate-cell PUT because the observable oracle for
  this path is the rollback/revert exit, not post-state.

Focused internal replay after the C++ resolver fix:

```sh
timeout -k 30s 120s build/src/esbmc/esbmc \
  notes/coverage/poc_units/aqua_Aqua__Aqua__safeBalances/inputs/aqua__Aqua.flat.sol.solast \
  --sol notes/coverage/poc_units/aqua_Aqua__Aqua__safeBalances/inputs/aqua__Aqua.flat.sol \
  --contract Aqua --solidity-path-coverage --solidity-max-tx 1 \
  --memlimit 8g --result-only --focus-function safeBalances \
  --path-cov-assert notes/coverage/poc_units/aqua_Aqua__Aqua__safeBalances/put_gate/_wd/aqua_Aqua__safeBalances__pf2909__6__certify_gate/assert/spec.json \
  --cov-report-json
```

This was a focused assertion-spec replay, not a `poc_one.py` stage rerun. It
finished in 18.2s under the 120s/8GiB envelope and no longer hard-refused the
literal `0x2000...0000` bytes32 mapping key. The ladder emitted 29 candidates:
17 HOLDS and 12 REFUTED. For each of the four certified `_balances` leaf slots,
`post == pre`, `post >= pre`, and `post <= pre` HOLDS, while change/strict-order
candidates are REFUTED. On this rollback path those HOLDS mean the verifier
models state restoration correctly; they are useful internal R1 rows, but the
chain-observable PUT oracle is still the revert exit-kind assertion.

Manual note: in this shell/tool environment, `setsid timeout ...` returned
after early child output and did not wait for ESBMC. Use plain `timeout` for
manual focused replays. The Python drivers still use their own subprocess
wrapper and previously produced complete logs with `setsid`.

Do not spend safeBalances again for this code change. The next useful work is
another POC's ground-truth-first repair, or promoting these now-queryable R1
slot rows into emitted assertions only for non-reverting paths where post-state
is observable on chain.

## 2026-08-06 current farming PUT progress

These entries are after commit `52c643bb8c`.

### `farming__Distributor__distributor`

Stage 3 attempt 3 has been spent:

```sh
python3 notes/coverage/scripts/poc_one.py \
  farming__Distributor__distributor --stage 3 --cell gate --attempt 3
```

Budget was 600s/10GiB; wall time was 89.9s. Current result:

- `B = 1 of 2`.
- `enc=2` is now B: one fuzz coordinate (`msg.value`), measured width from the
  certified ABI value region, and one exit-kind oracle on the low-level
  non-payable value call.
- `enc=3` is still correctly refused as not parameterized. The ladder proves
  the getter return (`return == 0`), but the emitted Foundry test has no
  rendered coordinate with width greater than one: `msg.value == 0`, and every
  state quantity is constructor-pinned or established.
- No official retry remains for this POC.

### `farming__FarmingPool__deposit`

Stage 3 attempt 1 was spent under 60s/8GiB and failed before any PUT could be
measured:

```sh
python3 notes/coverage/scripts/poc_one.py \
  farming__FarmingPool__deposit --stage 3 --cell gate --attempt 1
```

The ESBMC emit substep timed out at 60.1s with no `.cov.t.sol`. This is an
emission-timeout outcome, not a region/oracle failure.

Stage 3 attempt 2 was then spent under 120s/8GiB:

```sh
python3 notes/coverage/scripts/poc_one.py \
  farming__FarmingPool__deposit --stage 3 --cell gate --attempt 2
```

Wall time was 144.3s because the 120s ESBMC emit finished at 118.2s and Forge
then ran the B gate. Current result:

- `B = 1 of 1 emitted PUT`.
- The emitted path is `enc=2`, the ABI non-payable reject path.
- It fuzzes `msg.sender`, `msg.value`, and `amount`.
- It carries one exit-kind oracle: the low-level value call must fail.
- Forge was green after the driver disabled one red concrete replay from the
  emitter's original coverage suite.
- The six other witnessed deposit paths remain method-level unsupported in
  Stage 2: each pair differs only in external-call behavior
  (`STAKING_TOKEN.safeTransferFrom` success/failure) that a plain generated PUT
  cannot choose without a deterministic ERC20 fixture.

Only attempt 3 remains for `farming__FarmingPool__deposit`, but the supported
certified path is already B; do not spend it unless a later code change
requires regression confirmation.

## 2026-08-06 return structured R2 support

Implemented and verified a code-level repair for getter-style return oracles.
This did not spend any POC ESBMC attempt.

What changed:

- `scripts/solidity_path_put.py`
  - `propose_r2_batch` now treats `return` as an R2 target when the unit has a
    single scalar return and the `retlive` witness is REFUTED.
  - Region/pin scalar state coordinates are offered as structured R2 terms,
    e.g. `state._distributor`, without lifting them into the Foundry fuzz
    signature.
  - `return_rung_assertions` now renders structured return rungs such as
    `return == state._distributor` and structured return intervals when their
    endpoints can be spelled by the emitted PUT.
  - The emitted PUT pre-reads scalar state coordinates needed by return rungs
    with `vm.load` before the unit call. This is a read of the certified entry
    state, not a state fuzz coordinate and not a proof of arbitrary havoced
    storage.
- `src/goto-programs/goto_coverage.cpp`
  - the existing structured R2 helper now accepts a text subject (`post` or
    `return`) and flags controlling `pre`/delta support.
  - state and mapping structured R2 keep the previous `post` behavior.
  - return structured R2 emits equality/absolute rungs with `return == ...` /
    `return in [...]`, and refuses `pre` terms or delta terms for returns.
- Added regression
  `regression/esbmc-solidity/solidity_path_cov_assert_return_structured_state`
  pinning `return == state.owner`.

Verification run:

```sh
cmake --build build --target esbmc -j2
python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py
python3 scripts/test_solidity_path_put.py
/home/samson/workspace/esbmc/build/src/esbmc/esbmc \
  regression/esbmc-solidity/solidity_path_cov_assert_return_structured_state/contract.sol \
  --contract GetterR2 --solidity-path-coverage --solidity-max-tx 1 \
  --path-cov-assert \
  regression/esbmc-solidity/solidity_path_cov_assert_return_structured_state/spec.json
```

The direct ESBMC regression run exits with `VERIFICATION FAILED`, as expected
for assertion-ladder mode, and prints:

- `return: return == state.owner  HOLDS`
- `ladder summary -- 4 candidate(s): 2 HOLDS, 2 REFUTED`

An existing return regression,
`solidity_path_cov_assert_refuses_mapping`, was also replayed directly and still
prints:

- `return: return == 0  REFUTED`
- `return: return != 0  HOLDS`
- `ladder summary -- 9 candidate(s): 4 HOLDS, 5 REFUTED`

Impact on POC ground truth:

- This enables a future fresh `Distributor.distributor` assert ladder to ask
  and certify `return == state._distributor` instead of only `return == 0`.
- It does not by itself make the normal getter path parameterized. If the
  certified region still renders only `msg.value == 0` and constructor-pinned
  state, the PUT remains a deterministic oracle and should still be refused by
  the floor test. This is intentional: state pre-read is an oracle endpoint,
  not a fuzz dimension.

## 2026-08-06 Stage2/Stage4 accounting repair

User rule now in force:

- Before spending a POC ESBMC attempt, first inspect the POC source and write
  down the expected path/input-region/assertion shape as ground truth.
- Official per-POC retry ladder:
  - attempt 1: 60s, 8 GiB;
  - attempt 2: 120s, 8 GiB;
  - attempt 3: 600s, 10 GiB maximum.
- Fuzz may be used as a cheap refutation layer for probes, regions,
  instrumentation and R2/oracles. It cannot prove a region or assertion; every
  survivor still needs the ESBMC proof gate.

Implemented without spending any POC ESBMC attempt:

- `notes/coverage/scripts/certify_all.py` and `certify_poc.py` now preserve the
  driver's machine-readable `not_certified` rows from `generalise-result.json`
  as `not_certified_details`.
- `notes/coverage/scripts/put_all.py` now prints Stage-2 path accounting for
  the selected unit(s): witnessed paths, certified paths, not-certified paths,
  concrete fallbacks, method-level unsupported paths, legacy/unknown detail, and
  no-verdict gaps.
- Existing old cert JSONL files are still readable. If they predate
  `not_certified_details`, a row with `static_extcall_inseparable` and an
  `external-call behavior` reason is classified as method-level unsupported for
  accounting only.
- Added pure test `scripts/test_put_all_accounting.py`.

Why this matters:

- `solidity_path_generalise.py` already classifies external-call-only sibling
  splits as method-level `NOT_CERTIFIED` when
  `--static-extcall-inseparable` is passed.
- Stage4 previously iterated only `certified` regions and printed `B = ... of
  emitted PUT(s)`, so unsupported not-certified paths could disappear from the
  final denominator unless a human read the Stage2 log.
- The new accounting keeps unsupported paths visible without counting them as
  B and without pretending fuzz/Foundry proved anything.

Verification run:

```sh
python3 -m py_compile \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/certify_poc.py \
  notes/coverage/scripts/put_all.py \
  scripts/test_put_all_accounting.py
python3 scripts/test_put_all_accounting.py
python3 scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_put.py
```

## 2026-08-06 Bool input and bool R2 support

Implemented without spending any real POC ESBMC attempt.

What changed:

- The external PUT emitter now treats `bool` unit parameters as a liftable
  two-point coordinate. A certified region `[0, 1]` becomes a `bool` fuzz
  parameter with no numeric `bound()` cast; singleton regions become
  `true`/`false`; holes become `vm.assume(p != true/false)`.
- Typed R2 proposal now has a bool-only lane. If a state variable has the bool
  equality ladder (`post == pre` / `post != pre`) and the unit exposes a bool
  coordinate, the proposer asks only structured equality such as
  `post == emergencyExit_`.
- The C++ `--path-cov-assert` consumer now accepts structured equality over
  bool state variables and bool coordinates/literals. It still refuses bool
  interval and delta specs. This keeps the old soundness boundary: bool has no
  ordering or arithmetic R2.
- Generated Foundry oracles compare storage bits against
  `(flag_ ? uint256(1) : uint256(0))`, because `vm.load` reads a `uint256`
  storage word while the Solidity call parameter is `bool`.

Why it matters for POCs:

- `st1inch.setEmergencyExit(bool)` has a clean semantic ground truth:
  owner-only, non-payable, normal owner path writes
  `state.emergencyExit == emergencyExit_`.
- Before this patch, the bool argument was not a useful R2 endpoint and a
  strong PUT for that unit would either be deterministic or miss the main
  post-state relation.
- With this patch, the expected high-value PUT for the normal path is:
  `msg.sender == owner`, `msg.value == 0`, `emergencyExit_ in {false,true}`,
  bare call, assert `emergencyExit` storage bit equals the bool argument.

Verification run:

```sh
python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py
python3 scripts/test_solidity_path_put.py
cmake --build build --target esbmc -j2
./build/src/esbmc/esbmc \
  regression/esbmc-solidity/solidity_path_cov_assert_bool_r2_equality/contract.sol \
  --contract BoolR2Eq --solidity-path-coverage --solidity-max-tx 1 \
  --path-cov-assert \
  regression/esbmc-solidity/solidity_path_cov_assert_bool_r2_equality/spec.json \
  --memlimit 8g
./build/src/esbmc/esbmc \
  regression/esbmc-solidity/solidity_path_cov_assert_bool_r2_refused/contract.sol \
  --contract BoolR2 --solidity-path-coverage --solidity-max-tx 1 \
  --path-cov-assert \
  regression/esbmc-solidity/solidity_path_cov_assert_bool_r2_refused/spec.json \
  --memlimit 8g
```

Expected direct ESBMC results:

- `solidity_path_cov_assert_bool_r2_equality` exits with
  `VERIFICATION FAILED`, which is normal for assertion-ladder mode, and prints
  `flag: post == b  HOLDS` plus `flag: post == true  REFUTED`.
- `solidity_path_cov_assert_bool_r2_refused` exits before the ladder summary
  with the bool interval refusal. This pins that bool R2 equality is supported
  without opening bool ordering/arithmetic.

## 2026-08-06 st1inch setEmergencyExit attempt 1

Official attempt spent:

- POC: `st1inch_St1inch__St1inch__setEmergencyExit`
- Cell: `gate`
- Attempt: 1, therefore 60s / 8 GiB.
- Command: `python3 notes/coverage/scripts/poc_one.py
  st1inch_St1inch__St1inch__setEmergencyExit --stage all --cell gate
  --attempt 1 --fresh`

Observed result:

- Stage 1 used `--z3 --tuple-node-flattener` and the current strong runner's
  `--path-cov-probe --all-witnesses --max-witnesses 8`.
- Stage 1 was killed by the 60s outer timeout with no `cov-report.json` and no
  `cov-ce-journal.json`. The run record is under
  `notes/coverage/pathcov/st1inch_St1inch__poc_St1inch_setEmergencyExit_gate/`
  and records head `7563ef3ad1`, binary mtime `1785965824`,
  `pathsInstrumented: 3`, `degradedUnits: 12`, and `asmApproxSites: 16`.
- Because `pathcov_collect.py` returned success despite no report,
  `poc_one.py` continued. Stage 2 then hit a driver bug: `certify_all.py`
  required `notes/coverage/forge_roundtrip/st1inch_St1inch/emit.jsonl`, even
  though this is a single POC-unit run with its own `poc.json` and Stage-1
  `index.json`. It wrote an empty `certify_gate.jsonl`.
- Stage 3 correctly refused the empty selection:
  `--only 'st1inch_St1inch.setEmergencyExit' selected NONE`.

Code repair after attempt 1, without spending another POC ESBMC run:

- `notes/coverage/scripts/certify_all.py` now has a POC-only unit-list fallback:
  if the corpus `emit.jsonl` is absent but the caller provided one
  `--unit` plus a matching `--enumeration-index`, the Stage-1 collection
  manifest is accepted as the unit authority. This fallback is fail-closed for
  benchmark mismatch, multi-unit sweeps, and mismatched `onlyUnits`.
- `notes/coverage/scripts/pathcov_collect.py` now returns nonzero for a
  restricted `--only` run that produces zero reports. This makes `poc_one.py`
  stop at the real Stage-1 boundary instead of letting Stage2/Stage3 convert a
  timeout into an empty measurement.
- Added pure test `scripts/test_poc_stage_drivers.py`.

Verification run:

```sh
python3 -m py_compile \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/pathcov_collect.py \
  scripts/test_poc_stage_drivers.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_put_all_accounting.py
```

Next rule for this POC:

- attempt 2 remains available: 120s / 8 GiB.
- Do not run attempt 2 until the driver repair above is committed/pushed.
- If attempt 2 still dies in Stage 1, the next code-level target is Stage1
  speed/triage, not bool R2. Candidate fixes to inspect before attempt 3:
  reducing probe witness overhead for st1inch, solver/encoder arm choice for
  this single unit, or a cheap fuzz/refute prepass outside ESBMC's internal
  path instrumentation.

## 2026-08-06 st1inch setEmergencyExit attempt 2 and fixture prep

Official attempt spent:

- POC: `st1inch_St1inch__St1inch__setEmergencyExit`
- Cell: `gate`
- Attempt: 2, therefore 120s / 8 GiB.
- Command:
  `python3 notes/coverage/scripts/poc_one.py
  st1inch_St1inch__St1inch__setEmergencyExit --stage all --cell gate
  --attempt 2 --fresh`

Observed result:

- Stage 1 again timed out before producing `cov-report.json`.
- The previous fail-closed repair worked: `pathcov_collect.py` refused success
  for the restricted `--only setEmergencyExit` run because no Stage-1 report
  existed, so Stage 2 and Stage 3 did not convert the timeout into an empty PUT
  measurement.
- The log shows conversion/symex are not the bottleneck:
  - GOTO program processing: about 2.2s.
  - Symex: about 0.1s.
  - The solver phase is the bottleneck. Z3 returned
    `unknown (reason: out of memory)` on multiple complete-path/probe claims,
    spending about 13-26s per claim before the outer timeout killed the run.
- The unit itself has only three complete paths. The hard formula comes from
  deployment/constructor state, especially ERC20 string and voting-power setup,
  not from `setEmergencyExit(bool)` body complexity.

Ground truth for the remaining official attempt:

- Normal useful PUT path: owner sender, zero value, and
  `emergencyExit_ in {false,true}`.
- Expected R2/oracle: after the bare call,
  `state.emergencyExit == emergencyExit_`.
- Non-owner and nonpayable paths are valid gate/revert behavior but are not the
  high-value parameterized setter PUT.

Code prepared before spending attempt 3:

- `notes/coverage/poc_units/st1inch_St1inch__St1inch__setEmergencyExit/poc.json`
  now declares a gate fixture for this POC:
  skip the constructor and set only `_owner = 1`.
- `notes/coverage/scripts/poc_one.py` materializes the declared fixture as
  `fixture_gate.json` and passes it to every stage as:
  `--path-cov-fixture <fixture>`.
- `notes/coverage/scripts/pathcov_collect.py` accepts repeated
  `--esbmc-arg=...`, appends those args to Stage-1 ESBMC commands, and records
  the exact full ESBMC argument list in `index.json`. This is required because
  Stage 2 validates that the enumeration was produced under the same solver,
  encoder, and fixture assumptions.
- Stage 2 and Stage 3 also receive the same solver/fixture args from
  `poc_one.py`, keeping the certificate and emitted PUT aligned with Stage 1.

Validation already done without another POC ESBMC run:

```sh
python3 -m py_compile \
  notes/coverage/scripts/poc_one.py \
  notes/coverage/scripts/pathcov_collect.py \
  notes/coverage/scripts/certify_all.py \
  scripts/test_poc_stage_drivers.py
python3 scripts/test_poc_stage_drivers.py
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setEmergencyExit \
  --stage all --cell gate --attempt 3 --fresh --dry-run
```

Next rule:

- Only attempt 3 remains for this POC: 600s / 10 GiB.
- Run it only after the fixture plumbing is committed and pushed.
- If it still OOMs, do not spend more st1inch runs unless the budget is
  explicitly reopened. The next code-level target should be Stage-1 claim
  scheduling/reporting: solve complete paths before probes, write partial
  reports/journals earlier, or provide a no-probe/low-probe arm for fixture
  runs.

## 2026-08-06 st1inch setEmergencyExit attempt 3 result

Official attempt spent:

- POC: `st1inch_St1inch__St1inch__setEmergencyExit`
- Cell: `gate`
- Attempt: 3, therefore 600s / 10 GiB.
- Stage-1/2 command first run:
  `python3 notes/coverage/scripts/poc_one.py
  st1inch_St1inch__St1inch__setEmergencyExit --stage all --cell gate
  --attempt 3 --fresh`
- Stage-3 replay command after script repairs:
  `python3 notes/coverage/scripts/poc_one.py
  st1inch_St1inch__St1inch__setEmergencyExit --stage 3 --cell gate
  --attempt 3`

Result:

- Stage 1 succeeded under the gate fixture in about 70s and produced one report
  with the three expected `setEmergencyExit(bool)` paths.
- Stage 2 certified all three witnessed paths:
  `3 certified / 0 not / 3 witnessed`.
- Stage 3 emitted three PUTs and the final Forge gate was green for all:
  `B = 3 of 3 emitted PUT(s)`.
- Delivered PUTs:
  - enc=7 normal owner path: 1 fuzz parameter (`emergencyExit_`), 4
    post-state assertions. Dependency region selected only `emergencyExit` and
    `_owner`; mappings were excluded by solc-reference closure. This is the
    high-value setter PUT and includes the expected
    `state.emergencyExit == emergencyExit_` behavior.
  - enc=2 nonpayable value gate: 3 fuzz parameters (`msg.sender`,
    `msg.value`, `emergencyExit_`), 1 exit-kind oracle.
  - enc=6 non-owner revert: 2 fuzz parameters (`msg.sender`,
    `emergencyExit_`), 1 exit-kind oracle.

Repairs made during attempt 3 without re-running ESBMC Stage 1/2:

- `put_all.py` initially treated `st1inch_St1inch` as an unknown benchmark key;
  the corpus table now includes both `limit_order_protocol` and
  `st1inch_St1inch`, and `scripts/test_put_all_accounting.py` checks the PUT
  sweep table against the collection table.
- PUT storage oracles initially read `address(c0)`, but the emitted st1inch
  preamble deploys interface mocks before the target and calls `c1`. The PUT
  emitter now derives the actual receiver of the lifted unit call and uses that
  address for state pins, pre/post reads, mapping reads, and landing checks.
- Foundry replay initially tried to mirror ESBMC's skipped constructor with
  `type(St1inch).runtimeCode`, which Solidity rejects for contracts with
  immutables. The st1inch POC fixture now carries Foundry-only replay metadata:
  deploy with a legal constructor value
  `expBase = 999999952502977889`, then overwrite `_owner = 1` with `vm.store`.
  The legal interval was computed from the contract's own integer
  `_votingPowerAt` checks as
  `[999999952502977513, 999999952502978265]`.
- `poc_one.py` now preserves fixture `foundry` metadata when materializing
  `fixture_gate.json`; otherwise Stage 3 sees only the verifier fixture and
  cannot replay constructor-sensitive contracts in Foundry.

Validation after the repairs:

```sh
python3 -m py_compile \
  notes/coverage/scripts/poc_one.py \
  scripts/test_poc_stage_drivers.py \
  scripts/solidity_path_put.py \
  scripts/test_solidity_path_put.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_solidity_path_put.py
git diff --check -- \
  notes/coverage/scripts/poc_one.py \
  scripts/test_poc_stage_drivers.py \
  scripts/solidity_path_put.py \
  scripts/test_solidity_path_put.py \
  notes/coverage/poc_units/st1inch_St1inch__St1inch__setEmergencyExit/poc.json
```

Important accounting rule:

- Do not rerun st1inch `setEmergencyExit` through ESBMC. Its three official
  attempts are spent, and the POC now has complete gate-cell closure at B=3/3.
  Further work should move to another POC or to generic code-level fixes.

## 2026-08-06 next st1inch setter batch

The next fast path is to reuse the `setEmergencyExit` fixture/replay repair on
the other St1inch owner-gated setters before spending any new POC attempt.
These POCs previously had no fixture in `poc.json`, so their gate-cell path
coverage had to reason through the full constructor. Existing no-fixture
pathcov evidence:

- `setFeeReceiver`: 111.84s at 8 GiB, report present but only 2/5 verdicts
  preserved; the other 3 were solver-unknown.
- `setMaxLossRatio`: 78.07s at 8 GiB, report present but all 5 paths were
  solver-unknown.

Fixture patch applied:

- `st1inch_St1inch__St1inch__setFeeReceiver`
- `st1inch_St1inch__St1inch__setMaxLossRatio`
- `st1inch_St1inch__St1inch__setMinLockPeriodRatio`
- `st1inch_St1inch__St1inch__setDefaultFarm`

All four gate fixtures now skip the constructor for ESBMC, set `_owner = 1`,
and carry the same Foundry replay metadata used by `setEmergencyExit`:

```solidity
c1 = new St1inch(
    mk_IERC20_oneInch_,
    999999952502977889,
    address(uint160(7300))
);
```

The `expBase` value is inside the contract-checked valid interval
`[999999952502977513, 999999952502978265]`.

Ground truth before the next run:

- `setFeeReceiver(address feeReceiver_)`:
  - value gate: `msg.value != 0`, exit-kind oracle only;
  - non-owner paths: `msg.value == 0`, `msg.sender != 1`, rollback
    exit-kind oracle; zero/nonzero `feeReceiver_` may split before/after the
    body but post-state is not chain-observable;
  - owner zero path: `msg.value == 0`, `msg.sender == 1`,
    `feeReceiver_ == 0`, rollback exit-kind oracle;
  - owner nonzero normal path: `msg.value == 0`, `msg.sender == 1`,
    `feeReceiver_ in [1, 2^160-1]`, oracle
    `state.feeReceiver post == feeReceiver_`.
- `setMaxLossRatio(uint256 maxLossRatio_)`:
  - value gate: `msg.value != 0`, exit-kind oracle only;
  - non-owner paths: `msg.value == 0`, `msg.sender != 1`, rollback
    exit-kind oracle;
  - owner overflow path: `msg.value == 0`, `msg.sender == 1`,
    `maxLossRatio_ > 1e9`, rollback exit-kind oracle;
  - owner normal path: `msg.value == 0`, `msg.sender == 1`,
    `maxLossRatio_ in [0, 1e9]`, oracle
    `state.maxLossRatio post == maxLossRatio_`.
- `setMinLockPeriodRatio(uint256 minLockPeriodRatio_)`: same shape as
  `setMaxLossRatio`, with oracle
  `state.minLockPeriodRatio post == minLockPeriodRatio_`.
- `setDefaultFarm(address defaultFarm_)`:
  - value gate and non-owner paths are the same owner-gate shape;
  - `defaultFarm_ == 0` on the owner path is a normal setter path with oracle
    `state.defaultFarm post == 0`;
  - nonzero `defaultFarm_` also depends on external
    `Plugin(defaultFarm_).TOKEN() == this`, so nonzero success/revert paths may
    require a deterministic plugin fixture or method-level extcall
    attribution rather than plain generated-test inputs.

Validation before any real POC attempt:

```sh
python3 -m json.tool notes/coverage/poc_units/st1inch_St1inch__St1inch__setFeeReceiver/poc.json >/dev/null
python3 -m json.tool notes/coverage/poc_units/st1inch_St1inch__St1inch__setMaxLossRatio/poc.json >/dev/null
python3 -m json.tool notes/coverage/poc_units/st1inch_St1inch__St1inch__setMinLockPeriodRatio/poc.json >/dev/null
python3 -m json.tool notes/coverage/poc_units/st1inch_St1inch__St1inch__setDefaultFarm/poc.json >/dev/null
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage all --cell gate --attempt 1 --dry-run
```

Recommended next spend:

- Start with `st1inch_St1inch__St1inch__setMaxLossRatio`, attempt 1
  (60s/8GiB), because its source has no address zero special-case and no
  external call. If attempt 1 times out near the old 78s no-fixture baseline,
  attempt 2 is the likely first decisive run.

Attempt 1 result:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage all --cell gate --attempt 1 --fresh
```

- Stage 1 hit the 60s outer timeout but salvaged a partial journal report.
- The partial report decided only 2 feasible paths, enc=14 and enc=15, with
  33 undecided paths and `decisionSequences = null`.
- Stage 2 correctly refused the partial report:
  `DRIVER-REFUSED ... msg.value pin not seen`. This is not a proof failure.
  A partial report with no decision sequence cannot justify the synthetic ABI
  value gate or a complete unit path set.
- Stage 3 then refused the empty certification file. No B result was measured.

Code-level speed repair before attempt 2:

- `notes/coverage/scripts/poc_one.py` now lets a POC cell override
  `probe_witnesses`; the default remains 8 for the strong recipe.
- When a cell sets `probe_witnesses: 0`, Stage 1 runs without
  `--path-cov-probe --all-witnesses`, Stage 2 receives the same
  `--probe-witnesses 0`, and `--probe-ladder` is omitted because it requires a
  witness pool.
- The four simple st1inch setter gate cells above now set
  `probe_witnesses: 0`. They are relying on the simple-decision/structural
  fast path, not on multi-witness bracketing.

Validation:

```sh
python3 -m py_compile notes/coverage/scripts/poc_one.py scripts/test_poc_stage_drivers.py
python3 scripts/test_poc_stage_drivers.py
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage all --cell gate --attempt 2 --dry-run
```

The dry run confirms `--probe-witnesses 0` in Stage 1 and Stage 2, and no
`--probe-ladder` in Stage 2. Attempt 2 remains available and should be the next
official spend for this POC: 120s/8GiB.

Attempt 2 result:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage all --cell gate --attempt 2 --fresh
```

- Stage 1 completed in 38.6s with a full, non-partial report:
  `F_feasible_with_ce=5`, `covered=5`, `U=0`, `decisionSequences` present.
- Stage 2 timed out at 120s/8GiB before certifying anything:
  `0 certified / 0 not / 5 witnessed`, with 5 paths at no verdict. The log
  showed level0 had already decided 4 of them around 36s, but the driver still
  entered a second probe/refine path instead of using the source decision tree.
- Root cause was structural fast path incompleteness, not an ESBMC modeling
  failure:
  - `structural_decision_region` only accepted `==` and `!=`, so
    `maxLossRatio_ > _ONE_E9` forced the expensive ladder.
  - `_ONE_E9` was not resolved as a constant; the solc AST spells its literal
    as `value: "1e9"`.
  - Stage 2 did not import scalar state fixed by `--path-cov-fixture`, so
    `return_value$_owner$1` could not be resolved unless `state._owner` happened
    to appear in `entry_storage`.

Code-level repair after attempt 2:

- `scripts/solidity_path_generalise.py` structural decisions now accept
  `==`, `!=`, `<`, `<=`, `>`, `>=`, including the required ESBMC claim
  inversion (`x > c` means path condition `x <= c`; `!(x > c)` means
  `x > c`).
- Ordered product regions intersect coordinate boxes directly:
  `coord <= c` tightens `hi`, `coord > c` tightens `lo`, and stale holes are
  clipped after a bound update.
- Literal Solidity integer constants visible in the target contract's
  linearized base chain are extracted from the AST, including exact scientific
  integer literals such as `1e9`.
- Stage 2 imports scalar fixture state pins from `--path-cov-fixture`, e.g.
  `state._owner==1`, and refuses explicit `--pin` conflicts.

Validation without spending a new POC ESBMC attempt:

```sh
python3 -m py_compile \
  scripts/solidity_path_generalise.py \
  scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_generalise.py
timeout 60s python3 -u scripts/solidity_path_generalise.py \
  --enumeration-index notes/coverage/pathcov/st1inch_St1inch__poc_St1inch_setMaxLossRatio_gate/index.json \
  --enumeration-report notes/coverage/pathcov/st1inch_St1inch__poc_St1inch_setMaxLossRatio_gate/reports/St1inch__setMaxLossRatio.json \
  ... --esbmc-arg=--path-cov-fixture \
  --esbmc-arg=/home/samson/workspace/esbmc/notes/coverage/poc_units/st1inch_St1inch__St1inch__setMaxLossRatio/fixture_gate.json
```

The offline Stage 2 run reused the existing Stage 1 report, printed
`no enumeration ESBMC process was started`, imported `state._owner==1`, derived
structural regions for all 5 witnessed paths, and wrote
`5 certified region(s), 0 not certified, over 5 witnessed path(s)` in 0.19s.
It printed `No ESBMC certification query is started` for each path.

Expected structural regions for `setMaxLossRatio` gate now match ground truth:

- enc=2: `msg.value != 0`, value-gate reject.
- enc=12: `msg.value == 0`, `msg.sender != 1`,
  `maxLossRatio_ > 1000000000`.
- enc=13: `msg.value == 0`, `msg.sender != 1`,
  `maxLossRatio_ <= 1000000000`.
- enc=14: `msg.value == 0`, `msg.sender == 1`,
  `maxLossRatio_ > 1000000000`.
- enc=15: `msg.value == 0`, `msg.sender == 1`,
  `maxLossRatio_ <= 1000000000`.

Next official spend for this POC is attempt 3. Prefer running Stage 2 and
Stage 3 only, reusing the complete attempt-2 Stage 1 report:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage 2 --cell gate --attempt 3
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage 3 --cell gate --attempt 3
```

Attempt-3 budget remains 600s/10GiB. Operationally use a 300s checkpoint, but
do not split the official long proof window unless a log shows a clear driver
or fixture error.

Attempt 3 result:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage 2 --cell gate --attempt 3 --fresh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMaxLossRatio \
  --stage 3 --cell gate --attempt 3
```

- Stage 2 used `--fresh` only because the old cert file named commit
  `729c9da0b7`; the first non-fresh invocation refused before starting ESBMC.
- Stage 2 then reused the complete Stage 1 report and finished in 0.4s:
  `CERTIFIED, 5 certified / 0 not / 5 witnessed`.
- Stage 3 finished in 381.6s and emitted 5 PUTs.
- Final B: `4 of 5 emitted PUT(s)`.
  - B: enc=15 owner normal, enc=2 value gate, enc=12 non-owner/overflow,
    enc=13 non-owner/non-overflow.
  - Not B: enc=14 owner/overflow rollback.

enc=14 failure diagnosis:

- Region was correct: `msg.sender == 1`, `msg.value == 0`,
  `maxLossRatio_ > 1000000000`, `state._owner == 1`.
- The generated PUT was wrong: it copied the shared concrete replay body whose
  comment/call shape came from the sibling normal path:
  `// [asserted] path exits normally; ... c1.setMaxLossRatio(maxLossRatio_);`.
- For this path the call must revert. Since the call was a bare high-level call
  rather than `try/catch` or low-level `.call`, the emitter should have inserted
  `vm.expectRevert()` and counted it as the layer-1 exit-kind oracle.
- This is a PUT emitter bug, not a region bug and not an ESBMC modeling issue.

Code-level repair after attempt 3:

- `scripts/solidity_path_put.py` now treats rollback/revert path exit oracles
  as one of three legal shapes:
  - existing low-level value-gate `assertFalse(ok)`;
  - existing `vm.expectRevert()`;
  - newly inserted `vm.expectRevert()` for a bare high-level call.
- It still rewrites `try ... catch {}` to clear `_put_ok` when guarded
  assertions or catch-based exit assertions need it.
- It rewrites the inherited normal-exit call comment when it inserts
  `vm.expectRevert()`, so generated text no longer says both normal and revert.
- `scripts/test_solidity_path_put.py` now has
  `test_a_ROLLBACK_bare_call_gets_expectRevert_layer_1_oracle`, directly
  covering the St1inch `setMaxLossRatio` enc=14 shape.

Validation after the repair:

```sh
python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py
python3 scripts/test_solidity_path_put.py
```

Both passed. Do not rerun `setMaxLossRatio` unless the budget is explicitly
reopened: its three official attempts are now spent. The fix should benefit the
next rollback/overflow owner-gated setter POCs, especially
`setMinLockPeriodRatio`.

## 2026-08-06 st1inch setMinLockPeriodRatio gate

Ground truth:

```solidity
function setMinLockPeriodRatio(uint256 minLockPeriodRatio_) external onlyOwner {
    if (minLockPeriodRatio_ > _ONE_E9) revert MaxLossOverflow();
    minLockPeriodRatio = minLockPeriodRatio_;
    emit MinLockPeriodRatioSet(minLockPeriodRatio_);
}
```

Expected paths mirror `setMaxLossRatio`:

- value gate: `msg.value != 0`, exit-kind oracle only.
- non-owner paths: `msg.value == 0`, `msg.sender != 1`, rollback exit-kind
  oracle; source still splits on `minLockPeriodRatio_ > 1e9`.
- owner overflow path: `msg.value == 0`, `msg.sender == 1`,
  `minLockPeriodRatio_ > 1e9`, rollback exit-kind oracle.
- owner normal path: `msg.value == 0`, `msg.sender == 1`,
  `minLockPeriodRatio_ in [0, 1e9]`, desired strongest oracle
  `state.minLockPeriodRatio post == minLockPeriodRatio_`.

Attempt 1:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setMinLockPeriodRatio \
  --stage all --cell gate --attempt 1 --fresh
```

Budget: 60s/8GiB per ESBMC process.

Result:

- Stage 1 completed in 42.8s with a report, 0 killed.
- Stage 2 reused the report and finished in 0.6s:
  `CERTIFIED, 5 certified / 0 not / 5 witnessed`.
- Stage 3 finished in 581.8s and emitted 5 PUTs.
- Final B: `5 of 5 emitted PUT(s)`.

Notes:

- This confirms the structural region fix on another `_ONE_E9` setter.
- This also confirms the bare rollback `vm.expectRevert()` emitter repair:
  owner overflow path enc=14 now carries 1 exit-kind oracle and is green.
- The owner normal path enc=15 was B but did not recover the strongest setter
  R2 under attempt1's 60s/8GiB limit. Its log says the R2 batch produced no
  delta rows before timeout, so the emitted oracle was `_owner post == pre`
  rather than `minLockPeriodRatio post == minLockPeriodRatio_`. For aggregate
  success-rate accounting this is acceptable; for oracle-strength experiments,
  the next code-level improvement is a direct setter-oracle proposal from the
  source assignment `minLockPeriodRatio = minLockPeriodRatio_`, not simply a
  longer blind R2 batch.

Do not rerun this POC unless explicitly reopened. Attempts 2 and 3 were not
spent, but attempt 1 already achieved B=5/5.

## 2026-08-06 source-assignment R2 fast path

Code-level repair for the weak normal-setter oracle above:

- `scripts/solidity_path_put.py` now has `source_assignment_r2_specs`.
- It mines only the narrow source shape `stateVar = parameter` inside the
  selected target function body.
- It only proposes the candidate when:
  - the left side is a visible state variable in the storage layout;
  - the right side is one of the unit parameters;
  - that parameter is already rendered as a PUT coordinate for this certified
    region.
- The source does not prove the oracle. It only asks a smaller, more semantic
  R2 query first; ESBMC still has to certify the generated
  `post(stateVar) == parameter` assertion.
- When such a source assignment exists, the broad mechanical R2 batch is skipped
  for that path because the setter oracle is the query we actually need and the
  128-candidate mechanical batch was too expensive under attempt1.

Validation:

```sh
python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py
python3 scripts/test_solidity_path_put.py
```

Both passed (`140 test(s) ran`). A pure AST-level probe on the real
`setMinLockPeriodRatio` `.solast` produced exactly:

```text
R2 source assignment candidate minLockPeriodRatio: post == minLockPeriodRatio_
```

This probe used no ESBMC attempt. The next simple owner-gated setter should
therefore get a stronger normal-path R2 candidate without spending the 60s
attempt on a broad blind R2 batch.

## 2026-08-06 st1inch setFeeReceiver gate

Ground truth:

```solidity
function setFeeReceiver(address feeReceiver_) public onlyOwner {
    if (feeReceiver_ == address(0)) revert ZeroAddress();
    feeReceiver = feeReceiver_;
    emit FeeReceiverSet(feeReceiver_);
}
```

The constructor also calls `setFeeReceiver(feeReceiver_)`, but the POC gate
fixture skips deployment for ESBMC and pins only `_owner = 1`, so the method
entry state is the intended unit-level gate state rather than the expensive
constructor/voting-power state.

Attempt 1:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setFeeReceiver \
  --stage all --cell gate --attempt 1 --fresh
```

Budget: 60s/8GiB per ESBMC process.

Result:

- Stage 1 completed in 48.7s with a report, 0 killed.
- Stage 2 reused the report and finished in 0.6s:
  `CERTIFIED, 5 certified / 0 not / 5 witnessed`.
- Stage 3 finished in 529.4s.
- Final B table:
  - B: enc=15, enc=2, enc=12, enc=13.
  - REFUSED: enc=14.

Important observations:

- enc=15 normal path used the source-assignment R2 fast path:
  `feeReceiver: post == feeReceiver_  HOLDS`.
  It emitted a strong PUT with one fuzz parameter (`feeReceiver_`) and two
  post-state asserts (`feeReceiver == feeReceiver_`, `_owner == pre`).
- enc=2, enc=12, and enc=13 are rollback/value-gate paths. The emitter
  correctly dropped unobservable post-state/R2 candidates and kept a layer-1
  exit-kind oracle.
- enc=14 is `msg.value == 0`, `msg.sender == 1`, `feeReceiver_ == 0`. This is
  a singleton rendered input region: every rendered coordinate is width one, so
  there is no real parameter left to fuzz. The refusal is structural, not a
  solver timeout, region failure, or rollback instrumentation bug. A second
  attempt is unlikely to change this unless the tool grows support for
  parameterizing pre-state setup with something like `vm.store`.

Accounting recommendation:

- Count `setFeeReceiver` as 4 parameterized green PUTs out of 5 certified
  regions for the gate cell. This is above the 70% target.
- Do not spend attempt 2 on this POC just to chase enc=14. The missing row is
  not an ESBMC-strength issue; it is a singleton-region/parameterization policy
  issue.

## 2026-08-06 st1inch setDefaultFarm attempt1 diagnosis

Ground truth:

```solidity
function setDefaultFarm(address defaultFarm_) external onlyOwner {
    if (defaultFarm_ != address(0) &&
        Plugin(defaultFarm_).TOKEN() != this) revert DefaultFarmTokenMismatch();
    defaultFarm = defaultFarm_;
    emit DefaultFarmSet(defaultFarm_);
}
```

Important shape:

- `defaultFarm_ == 0` avoids the external `Plugin(...).TOKEN()` call and should
  be a normal setter path with oracle `defaultFarm post == defaultFarm_`.
- `defaultFarm_ != 0` splits on external-call behavior. Some siblings may be
  method-level unsupported unless a deterministic plugin fixture is added.
- Source-assignment R2 does see `defaultFarm = defaultFarm_` and can ask the
  small setter oracle when a normal path is certified.

Attempt 1:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setDefaultFarm \
  --stage all --cell gate --attempt 1 --fresh
```

Budget: 60s/8GiB per ESBMC process.

Result:

- Stage 1 completed in 40.2s with a fresh fixture-backed report.
- The report improved from the old no-fixture `7/7 U solver-unknown` to
  `7/7 F`, path coverage 100%, with 7 witnesses.
- Stage 2 timed out/killed at 60s:
  `KILLED, 0 certified / 0 not / 7 witnessed`.
- Stage 3 had no certified regions and exited 2.

Stage 2 diagnosis:

- The driver log shows level0 decided 4 paths by 38.2s:
  - enc=24: `msg.value == 0`
  - enc=25: `msg.value == 0`
  - enc=28: `msg.sender == 1, msg.value == 0`
  - enc=29: `msg.sender == 1, msg.value == 0`
- It then entered level0b to re-probe `msg.value: [0] -> [0, 1]`, because
  single-value candidates were considered vacuity-risk. This consumed the 60s
  attempt before any result could be used.
- Here the risk is avoidable: the same Stage1 report already witnessed each
  path under the same fixture. A concrete member with `msg.value == 0` proves
  the antecedent is not empty for the purpose of the level0 vacuity warning.
  This does not prove the final region; it only avoids an unnecessary
  refute-oriented neighbor probe.

Code-level repair after attempt1:

- `scripts/solidity_path_generalise.py` now checks whether a one-value
  level0 point is already confirmed by this path's known witness vector under
  the current non-conflicting pins.
- If so, that coordinate is not sent to level0b's vacuity probe.
- Missing fixture pins in the witness payload do not disqualify the member,
  because the Stage1 report was generated under the fixture; an explicitly
  present conflicting pin still disqualifies it.
- `scripts/test_solidity_path_generalise.py` covers both cases with pure Python
  tests.

Validation:

```sh
python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_generalise.py
```

Both passed.

Next spend:

- Use `setDefaultFarm` attempt2 at 120s/8GiB after this repair. Stage1 does not
  need to be rerun if the existing fresh attempt1 report is reused; the code
  change is in Stage2 generalisation.

Attempt 2:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setDefaultFarm \
  --stage 2 --cell gate --attempt 2 --fresh
```

Budget: 120s/8GiB per ESBMC process. Stage1 was not rerun; Stage2 reused the
fresh attempt1 enumeration report.

Result:

- Stage2 still timed out/killed at 120s:
  `KILLED, 0 certified / 0 not / 7 witnessed`.
- The previous code repair did work: `level0_vacuity_risk` was empty, and the
  log no longer entered level0b. It confirmed `msg.value == 0` by witnessed
  members.
- The new bottleneck was after refine:
  - enc=2, enc=13, and enc=15 were already structural simple-decision regions.
  - enc=24/25/28/29 remained `UNSEPARATED`.
  - The driver then tried to certify enc=24 anyway and timed out before it could
    write a usable final result.

Second code-level repair:

- `extcall_inseparable_failures` now also detects sibling paths whose
  generated-test-settable payload is identical and whose only split is the arm
  of the same `NONDET(...)` decision.
- This is the `Plugin(defaultFarm_).TOKEN() != this` external-return split in
  `setDefaultFarm`.
- A pure JSON-level probe over the real attempt1 Stage1 report now classifies
  exactly enc=24, enc=25, enc=28, and enc=29 as statically inseparable, while
  leaving enc=2, enc=13, and enc=15 available for structural certification.

Validation:

```sh
python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_generalise.py
```

Both passed. Next spend is attempt3 at 600s/10GiB. Because the four nonzero
external-call siblings should now be removed before region search, Stage2 should
only need to certify the three zero/value/owner structural regions.

Attempt 3:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setDefaultFarm \
  --stage 2 --cell gate --attempt 3 --fresh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__setDefaultFarm \
  --stage 3 --cell gate --attempt 3
```

Budget: 600s/10GiB per ESBMC process.

Result:

- Stage2 finished in 0.5s:
  `CERTIFIED, 3 certified / 4 not / 7 witnessed`.
- The 4 not-certified paths are method-level unsupported external-call siblings
  on the `Plugin(defaultFarm_).TOKEN() != this` nondet return split.
- Stage3 finished in 242.2s.
- Final B table for the 3 certified regions:
  - B: enc=2 and enc=13.
  - REFUSED: enc=15.

Details:

- enc=2 is the ABI value-gate rollback path. It emitted a PUT with
  `msg.sender`, `msg.value`, and `defaultFarm_` fuzz parameters plus one
  exit-kind oracle.
- enc=13 is the non-owner zero-defaultFarm rollback path. It emitted a PUT with
  `msg.sender` and `defaultFarm_` fuzz parameters plus one exit-kind oracle.
- enc=15 is the owner zero-defaultFarm normal path. Source-assignment R2 proved
  `defaultFarm: post == defaultFarm_  HOLDS`, but the certified rendered region
  is singleton: `defaultFarm_ == 0`, `msg.sender == 1`, `msg.value == 0`.
  The emitter correctly refused it as not parameterized.

Accounting recommendation:

- Count this POC as `2/3` parameterized green PUTs among certified regions, with
  `4/7` witnessed paths explicitly excluded as method-level unsupported.
- Do not spend more `setDefaultFarm` runs under the current gate fixture. The
  remaining nonzero paths require a deterministic plugin fixture or another
  external-call realization policy, not a longer ESBMC timeout.
- The two code-level fixes from this POC are generic and should help other units:
  witnessed level0 points avoid redundant vacuity probes, and same-payload
  `NONDET(...)` decision siblings are classified before they drag certification
  into large unsupported regions.

## 2026-08-06 Aqua pull pre-run classification

Ground truth:

```solidity
function pull(address maker, bytes32 strategyHash, address token,
              uint256 amount, address to) external {
    Balance storage balance = _balances[maker][msg.sender][strategyHash][token];
    (uint248 prevBalance, uint8 tokensCount) = balance.load();
    balance.store(prevBalance - amount.toUint248(), tokensCount);

    IERC20(token).safeTransferFrom(maker, to, amount);
    emit Pulled(maker, msg.sender, strategyHash, token, amount);
}
```

Old Stage-1 report:

- 17 instrumented paths.
- 5 witnessed paths: enc=2, enc=58, enc=59, enc=62, enc=63.
- enc=2 is the ABI non-payable value gate.
- enc=58/59 and enc=62/63 are sibling pairs with the same generated-test-settable
  payload and different `safeTransferFrom` success/failure arms.

Code-level repair before spending the current POC:

- `extcall_inseparable_failures` now also recognizes same-payload siblings whose
  only split is a `success` / `!success` branch inside known external-call helper
  functions such as `safeTransferFrom`.
- A pure JSON-level probe over the existing `Aqua.pull` Stage-1 report now
  classifies exactly enc=58, enc=59, enc=62, and enc=63 as method-level
  unsupported external-call siblings. enc=2 remains available.

Validation:

```sh
python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py
python3 scripts/test_solidity_path_generalise.py
```

Both passed. The next real spend should be `aqua_Aqua__Aqua__pull` attempt1
under 60s/8GiB. Expected Stage2: one certified value-gate region, four
method-level unsupported external-call regions. Expected Stage3: one B PUT for
enc=2 with a low-level value-call exit-kind oracle.

## 2026-08-06 Aqua pull attempt1 crash and witness-filter repair

Official attempt spent:

```sh
python3 notes/coverage/scripts/poc_one.py \
  aqua_Aqua__Aqua__pull --stage all --cell gate --attempt 1 --fresh
```

Result:

- Stage 1 exited after 3.27s wall clock with ESBMC exit code `-11`
  (SIGSEGV). It did not time out and did not write `cov-report.json`.
- The command was correctly configured for attempt1: 60s outer timeout,
  8GiB memlimit, `--path-cov-probe --all-witnesses --max-witnesses 8`.
- ESBMC internally auto-selected CVC5 because Aqua has a deep nested mapping
  shape, so this was not a missing solver flag.
- The crash happened after the first path claim was refuted and while
  `--all-witnesses` was enumerating additional path-probe witnesses. The log
  repeatedly warned that aggregate nondets such as contract-object values had
  unresolved components before the process segfaulted.

Code-level repair made without spending another POC attempt:

- `collect_nondet_values` now accepts a `path_probe_replayable_only` mode.
- In that mode it filters before querying the solver model, keeping only scalar
  Solidity function-local nondets and replayable EVM environment values
  (`msg.sender`, `msg.value`).
- This matches the existing BMC-side path-probe policy, but moves it ahead of
  aggregate model materialisation. Path-probe blocking clauses cannot replay
  harness objects or aggregate contract instances anyway, so asking CVC5 to
  materialise them only created crash exposure without increasing the generated
  region.
- The pre-model skipped count is still included in
  `path_probe_nondets_dropped`, so the coverage summary does not falsely claim
  those quantities were never seen.

Validation so far:

```sh
make -C build -j2 esbmc
git diff --check -- src/esbmc/bmc.cpp \
  src/goto-symex/witnesses.cpp src/goto-symex/witnesses.h
build/src/esbmc/esbmc --version
```

The build completed and the binary reports `ESBMC version 8.2.0 64-bit x86_64
linux`. The next official spend for `aqua_Aqua__Aqua__pull` is attempt2 under
120s/8GiB, but do not run it until this repair is committed and pushed.

Attempt2 was then spent after commit `6bbf0183c4`:

```sh
python3 notes/coverage/scripts/poc_one.py \
  aqua_Aqua__Aqua__pull --stage all --cell gate --attempt 2 --fresh
```

It failed in the same shape:

- Stage 1 exited after 2.87s with ESBMC exit code `-11`.
- No report was produced; Stage 2 and Stage 3 did not run.
- The run record names binary head `6bbf0183c4` and the 120s/8GiB attempt2
  config.
- The log still printed the full nondet census and unresolved aggregate warnings
  for `nondet$symex::nondet39` and `nondet$symex::nondet44`, proving the first
  repair did not apply to this claim.

Root cause of the missed repair:

- `pull:path:15` is a normal complete-path claim, not `is_probe_claim`, even
  when the run is using `--path-cov-probe`.
- The replayable-only filtering therefore has to be controlled by the run-level
  option `is_path_cov && path-cov-probe`, not by the individual claim being a
  probe claim.

Second code-level repair, made after attempt2 and before any attempt3 spend:

- `bmc.cpp` now computes `path_probe_replayable_only` once per claim loop from
  `is_path_cov && options.get_bool_option("path-cov-probe")`.
- `collect_nondet_values` receives that run-level flag, so normal complete-path
  claims in a path-probe run also skip non-replayable aggregate model queries.
- The defensive post-filter and dropped-count accounting now use the same
  run-level flag.

Validation:

```sh
clang-format -n --Werror src/esbmc/bmc.cpp \
  src/goto-symex/witnesses.cpp src/goto-symex/witnesses.h
make -C build -j2 esbmc
```

Both passed. `aqua_Aqua__Aqua__pull` now has only attempt3 remaining
(600s/10GiB). Do not spend it until this second repair is committed and pushed.

Attempt3 was then spent after commit `aeea521f12`:

```sh
python3 notes/coverage/scripts/poc_one.py \
  aqua_Aqua__Aqua__pull --stage all --cell gate --attempt 3 --fresh
```

It also failed in Stage 1:

- Stage 1 exited after 2.79s with ESBMC exit code `-11`.
- No report was produced; Stage 2 and Stage 3 did not run.
- The 600s/10GiB final attempt for this POC is now spent. Do not rerun
  `aqua_Aqua__Aqua__pull` under the current budget policy.
- The second repair did take effect: the final log's all-witnesses census no
  longer contains aggregate contract objects or unresolved-component warnings.
  It contains only `msg_value`, `maker`, `token`, `amount`, and
  `SafeERC20.safeTransferFrom.success`.

New diagnosis from the final log:

- The remaining non-replayable coordinate is the callee-local
  `SafeERC20.safeTransferFrom.success` nondet. It is not an input a generated
  PUT can supply; it is exactly the external-call success/failure split already
  handled by static extcall sibling classification.
- Therefore path-probe witness blocking should not treat every scalar Solidity
  function-local nondet as replayable. It should keep source-level call
  parameters and replayable env only, not callee-local nondet returns.

Third code-level repair, made after all Aqua pull attempts were spent:

- `is_path_probe_replayable_nondet` now additionally receives the SSA source
  location and keeps Solidity source values only when they are parameter-like
  (the current frontend emits these parameter nondet assignments with an empty
  source location in the census).
- Scalar callee-local nondets such as `SafeERC20.safeTransferFrom.success`,
  which carry a real Solidity file/line location, are filtered out before model
  querying and before building a blocking tuple.
- The header comment was updated to say "parameter-like values" rather than
  "function-local values".

This repair still needs compile/format validation and commit/push. It cannot be
validated on `aqua_Aqua__Aqua__pull` without violating the three-attempt budget.

## 2026-08-06 st1inch disabled ERC20 entries prep

Next high-probability POCs:

- `st1inch_St1inch__St1inch__approve`
- `st1inch_St1inch__St1inch__transfer`
- `st1inch_St1inch__St1inch__transferFrom`

Source ground truth:

```solidity
function approve(address, uint256) public pure override(IERC20, ERC20)
    returns (bool)
{
    revert ApproveDisabled();
}

function transfer(address, uint256) public pure override(IERC20, ERC20)
    returns (bool)
{
    revert TransferDisabled();
}

function transferFrom(address, address, uint256)
    public pure override(IERC20, ERC20) returns (bool)
{
    revert TransferDisabled();
}
```

Expected gate-cell paths for each unit:

- `msg.value != 0`: Solidity non-payable ABI value gate, rollback/exit-kind
  oracle only. Function parameters are still call data and can be fuzzed.
- `msg.value == 0`: the function body is entered and immediately reverts with
  the disabled-entry custom error. No post-state or return-value oracle is
  meaningful; the right PUT oracle is `vm.expectRevert()` around the high-level
  call.

Old no-fixture Stage-1 artefacts:

- `approve`: 2 paths, both `solver-unknown`, 0 witnessed; 48.21s.
- `transfer`: 2 paths, both `solver-unknown`, 0 witnessed; 43.98s.
- `transferFrom`: 2 paths, both `solver-unknown`, 0 witnessed; 44.19s.

Those old rows were collected with no fixture, so ESBMC still paid for
constructor/voting-power state even though these pure disabled entries read no
contract state. They are not useful Stage-1 universes.

Code/config repair before spending a new official attempt:

- Added a gate fixture to all three POCs:
  - `skip_constructor: true`;
  - valid Foundry constructor metadata:
    `mk_IERC20_oneInch_, 999999952502977889, address(uint160(7300))`;
  - empty ESBMC state, because these pure disabled methods read no storage.
- Added `probe_witnesses: 0` to all three gate cells. Multi-witness probe
  enumeration is unnecessary for a two-path always-revert function and is the
  part most exposed to the current all-witnesses crash class.

Validation without spending ESBMC:

```sh
for p in approve transfer transferFrom; do
  python3 -m json.tool \
    notes/coverage/poc_units/st1inch_St1inch__St1inch__${p}/poc.json \
    >/dev/null
done
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__approve \
  --stage all --cell gate --attempt 1 --fresh --dry-run
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__transfer \
  --stage all --cell gate --attempt 1 --fresh --dry-run
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__transferFrom \
  --stage all --cell gate --attempt 1 --fresh --dry-run
```

Dry-run result:

- All three use attempt1 budget: 60s/8GiB.
- Stage 1 passes `--probe-witnesses 0` and `--path-cov-fixture`.
- Stage 2 passes the same fixture plus `--probe-witnesses 0` and omits
  `--probe-ladder`.
- Stage 3 passes the fixture and Z3 tuple-flattener solver flags.

Recommended next official spend: run `approve` attempt1 first. If it reaches
Stage 3, expected B is 2/2. If it still reports solver-unknown under the
fixture, stop and inspect the path claim/GOTO lowering before running
`transfer` or `transferFrom`.

## 2026-08-06 ESBMC retry budget policy update

Per-POC ESBMC spend should now be treated as an adaptive three/four-step budget,
not a blind fixed ladder:

- attempt 1: 60s / 8GiB;
- attempt 2: 120s / 8GiB;
- final proof spend: either one 600s / 10GiB run, or a diagnostic split of
  300s / 10GiB + 300s / 10GiB.

Selection rule:

- Keep the final run as 600s when the earlier attempts show that insertion,
  region, fixture, and candidate assertions are basically correct, and the only
  remaining issue is solver proof time. Two independent 300s runs do not
  accumulate solver effort and are not equivalent to one 600s proof run.
- Split into 300s + 300s when the POC is still diagnostic: crash, solver
  unknown with little signal, suspicious region, unstable fixture, or visibly
  over-broad/under-semantic candidates. The first 300s run is for information;
  the second 300s run should only be spent after inspecting the log and making
  a code/config/region change or after deciding that a different option set is
  justified.
- Do not repeat the same 300s configuration merely because it timed out. If
  nothing changed and the run only needs more proof time, spend one 600s run
  instead.

Fuzz policy remains refute-only. It can cheaply find counterexamples for
candidate PUT assertions, region choices, and replay/instrumentation mistakes,
but it must never be counted as proof that an assertion or region is correct.

## 2026-08-06 st1inch approve attempt1 result and PUT-emitter repair

Official spend:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__approve \
  --stage all --cell gate --attempt 1 --fresh
```

Budget tier used: attempt1, 60s / 8GiB.

Result:

- Stage 1 completed in 31.3s and produced a path report.
- Stage 2 completed in 0.8s and certified 2/2 witnessed paths:
  - enc=2: `msg.value in [1, 2^256-1]`, non-payable value-gate revert;
  - enc=3: `msg.value in [0, 0]`, body enters and immediately reverts.
- Stage 3 completed without crashing but emitted 0 PUTs:
  - enc=2 REFUSED;
  - enc=3 REFUSED.

Root cause was outside ESBMC proving:

- The concrete Foundry replay emitted by coverage had dropped irrelevant
  calldata arguments:
  - high-level body-revert case: `try c1.approve() {} catch {}`;
  - value-gate case: `abi.encodeWithSignature("approve()")`.
- The Solidity declaration is `approve(address,uint256)`.
- `scripts/solidity_path_put.py` correctly refused to rewrite by position when
  declared arity 2 did not match emitted arity 0, but that made always-revert
  ERC20 entries unproductive even though Stage 2 proved the path independent of
  calldata.

Code-level repair:

- `scripts/solidity_path_put.py` now detects omitted concrete calldata
  arguments when the AST declaration has more parameters than the emitted call.
- If each omitted parameter has a renderable scalar type (`bool`, `address`,
  `uint*`), the PUT emitter:
  - gives anonymous parameters stable names (`arg0`, `arg1`, ...);
  - completes the high-level or low-level call;
  - updates low-level `abi.encodeWithSignature` strings, e.g.
    `approve()` -> `approve(address,uint256)`;
  - lifts the omitted calldata parameters as full-domain fuzz inputs.
- Unsupported omitted parameter types still fail closed.
- This is sound for the st1inch disabled entries because the certified region
  leaves those calldata parameters unconstrained: they do not affect the path,
  and the only oracle is the revert exit kind.

Validation, without spending another ESBMC run:

```sh
python3 scripts/test_solidity_path_put.py
```

Result: 142/142 tests passed. Added regression coverage for both failing shapes:

- `test_missing_replay_args_become_full_domain_fuzz_inputs`;
- `test_missing_low_level_value_gate_args_update_abi_signature`.

Do not rerun `st1inch approve` immediately just to see the expected B change if
we are still preserving attempts. The next justified run can be either
`transfer` attempt1, which should exercise the same repair on a sibling disabled
entry, or `approve` attempt2 if we specifically want to measure the repaired
row. Under the adaptive policy, this is a diagnostic fix, not a proof-time
timeout.

## 2026-08-06 st1inch transfer attempt1 result and assembled-source repair

Official spend:

```sh
python3 notes/coverage/scripts/poc_one.py \
  st1inch_St1inch__St1inch__transfer \
  --stage all --cell gate --attempt 1 --fresh
```

Budget tier used: attempt1, 60s / 8GiB.

Result:

- Stage 1 completed in 41.1s and produced a path report.
- Stage 2 completed in 0.6s and certified 2/2 witnessed paths.
- Stage 3 confirmed the omitted-calldata repair on a real POC:
  - enc=2 wrote `St1inchCovTest_St1inch_transfer_put2.t.sol`, with fuzz
    parameters `msg.value`, `arg0`, `arg1`, and one exit-kind oracle;
  - enc=3 wrote `St1inchCovTest_St1inch_transfer_put3.t.sol`, with fuzz
    parameters `arg0`, `arg1`, and one exit-kind oracle.

Observed B gate:

- Text emission counters were strong: 2/2 emitted, 2/2 with fuzz, 2/2 with
  oracle, 2/2 with both.
- B remained 0/2 because forge could not compile the generated project:
  `Wrong argument count for function call: 0 arguments given but expected 2`.
- The compile error came from retained original `test_cov_*` concrete replay
  functions in the assembled PUT test contract:
  - `try c1.transfer() {} catch {}`;
  - `abi.encodeWithSignature("transfer()")`.
- The generated `test_put_*` functions themselves had already been repaired.

Second code-level repair:

- `assemble_put_source` now drops original `test_cov_*` replay functions from
  the PUT project after inserting `test_put_*`.
- The replay cases are Stage-1 preamble/source material, not B deliverables.
  Keeping stale replay calls in the same project lets an already-repaired PUT
  row fail before forge can measure it.
- The deployment preamble, mocks, fixture constructor replay, and generated PUT
  functions are retained.

Validation, without spending another ESBMC run:

```sh
python3 -m py_compile \
  scripts/solidity_path_put.py scripts/test_solidity_path_put.py
python3 scripts/test_solidity_path_put.py
git diff --check -- \
  scripts/solidity_path_put.py \
  scripts/test_solidity_path_put.py \
  notes/VeriPUT_handoff_memory.md
```

Result: 143/143 tests passed. Added regression coverage:

- `test_assembled_put_source_drops_stale_concrete_replays`.

Do not rerun `transfer` attempt2 only to regenerate the same row after this
source-level fix. The next official spend should be a fresh sibling POC
(`transferFrom` attempt1) or an explicit measurement run if we decide that
confirming B on this exact unit is worth the attempt.

## 2026-08-06 anti-overfit reminder

The POCs under `notes/coverage/poc_units` are diagnostic tools for hardening
VeriPUT, not the final evaluation target. The later benchmark run will use
`/home/samson/workspace/VeriPUT/Datasets`.

Do not introduce POC-specific special cases just to raise a POC's B number. A
change should be accepted only when it fixes a general pipeline obligation, for
example:

- a certified path should produce a PUT whose fuzz inputs cover every calldata
  parameter the proof left unconstrained and the Solidity type system can render;
- a PUT project should measure generated `test_put_*` rows, not fail because a
  retained Stage-1 concrete replay is stale;
- fuzz may refute bad generated assertions or region choices, but never proves
  them.

The st1inch disabled-entry repairs above satisfy this standard because they fix
generic emitted-call reconstruction and PUT-project assembly behavior; they are
not keyed on the st1inch contract, disabled ERC20 names, or any one path id.

### Dataset target distribution, not POC distribution

Static survey on 2026-08-06 confirmed that the later evaluation target is not
the diagnostic POC queue. It is the material under
`/home/samson/workspace/VeriPUT/Datasets`, plus the prepared subject layouts in
`/home/samson/workspace/VeriPUT/Results`.

The important target shapes are:

- `Datasets/Stress-Projects`: ten production repositories, 243 target rows in
  `TARGETS.csv`. `Results/Stress243/prepare_subjects.py` has already converted
  these into the common baseline layout
  `Results/Stress243/subjects/<sid>/{flat.sol,meta.json}`. Stage 1 reports
  203 usable flat subjects, 32 compile failures, and 7 flatten failures. Every
  downstream baseline consumes the flat single-file subject plus a target
  contract name, not a hand-written POC fixture.
- `Datasets/Peer-Reviewed-Contracts`: 182 positive-control contracts from
  CC-SolBMC, SolTG, SynTest, and SolAR. Both original tool-native sources and
  `contracts_080` ports exist; the 0.8 ports are the inputs suitable for
  VeriPUT/SuMo/Forge, while original versions are for baseline controls. This
  means VeriPUT fixes must tolerate upgraded but ABI-checked legacy contracts,
  not assume modern production project structure only.
- `Datasets/Patch-Bug-Bench`: 124 bug/fix pairs. `Results/BugFix124` prepares
  each case in the same subject layout as Stress243, with `flat.sol` as the
  reference fix and `bug.sol` kept beside it for the real-bug kill question.
  A useful VeriPUT integration must preserve this reference/mutant distinction
  and cannot rely on POC-only pathcov directory conventions.

Additional distribution facts that matter for code design:

- Stress projects are assembly-heavy. The survey documents 160/243 targets
  reaching assembly and 78 tier-1/tier-2 own or inherited assembly cases.
  Path-origin attribution, unsupported evidence, and source-level path grouping
  must therefore handle Yul/lowering/model decisions generally; treating every
  witnessed decision as a fuzzable source branch will over-count paths on the
  real benchmark.
- The common campaign runner records timeout, memory, wall/cpu time, RSS, and
  status separately. VeriPUT benchmark integration should follow that discipline:
  append-only journals, resumable keys, explicit memory/time budgets, and
  distinct `timeout` / `oom` / `crash` / `unsupported` / `no-oracle` outcomes.
- The real benchmark interface is centered on prepared flat subjects and
  manifests. A repair is general only if it works from source/AST/metadata
  available for those subjects. A repair that depends on a `poc_units` name,
  a disabled-entry hand fixture, or one historical path id is overfitting and
  must be rejected.

Immediate implication: before spending more POC attempts, inspect how the
current ESBMC-side `poc_one.py`/`certify_all.py`/`put_all.py` flow would be
fed from `Results/Stress243/subjects`, `Results/Peer182`, and
`Results/BugFix124/subjects`. The next code changes should close mismatches in
that general interface: target-contract selection, flat-file AST generation,
constructor/fixture establishment, source-path attribution, emitted PUT
assembly, and benchmark journal fields.

### Static interface audit after the Dataset survey

The lower-level ESBMC/VeriPUT drivers are more general than the current POC
orchestration:

- `scripts/solidity_path_generalise.py` accepts explicit
  `--sol --ast --contract --unit --scope --max-tx`, so it can in principle run
  on any prepared flat subject if the caller supplies a correct AST, target
  contract, unit, scope, fixture flags, and budget.
- `scripts/solidity_path_put.py` also accepts explicit
  `--sol --ast --contract --unit --enc --region`, so PUT emission is likewise
  not intrinsically tied to the POC directory layout.

The current blockers are in the outer orchestration:

- `notes/coverage/scripts/pathcov_collect.py` is still built around the locked
  `collect.py` benchmark table. Its ad-hoc `--sol` mode deliberately supports
  only `--scope whole`, because it has no project tree and therefore no trusted
  callable-unit enumeration. A Stress/BugFix prepared flat subject also lacks
  the original project tree, so the current ad-hoc path cannot run the gate
  cell for one target unit.
- `notes/coverage/scripts/certify_all.py` still has a hard-coded
  `BENCHMARKS` table for six historical corpus entries. `poc_one.py` works
  around that by passing a POC's private `VERIPUT_INPUTS_DIR` plus the same
  historical benchmark key. That does not scale to arbitrary
  `Results/Stress243/subjects/<sid>/flat.sol` or
  `Results/BugFix124/subjects/<sid>/flat.sol`.
- `notes/coverage/scripts/put_all.py` repeats the same `BENCHES` table and
  resolves corpus sources either from `poc_units/<pid>/inputs` or the deleted
  shared `notes/coverage/inputs`. Unknown benchmark keys are skipped rather
  than resolved from a subject manifest.
- `poc_one.py` is correctly narrow for POC accounting, but its input contract
  is explicitly `notes/coverage/poc_units/index.json`/`poc.json`. It should not
  become the benchmark runner by accreting special cases. The benchmark runner
  should share the same stage functions/recipes but read subject manifests.

General repair direction:

1. introduce a manifest-backed subject resolver whose record contains at least
   `{benchmark, subject_id, flat_sol, solast, contract, unit, scope, max_tx,
   fixture?, solc/build metadata?}`;
2. make Stage 1, Stage 2, and Stage 3 accept that resolved subject explicitly,
   while keeping `poc_one.py` as a thin POC adapter over the same resolved
   shape;
3. keep the existing POC retry accounting separate from benchmark campaign
   accounting. POC attempts are debugging budget; Stress/Peer/BugFix rows need
   append-only benchmark journals with the campaign's own time/memory fields;
4. avoid re-implementing callable-unit discovery for flat subjects with a
   weak regex. For prepared benchmarks, use the existing target manifest or
   `meta.json` contract/unit metadata; for general source, fail closed unless
   a trusted source of unit names is supplied.

This is an orchestration/interface issue first. Do not spend ESBMC POC attempts
to diagnose it; unit-test the subject resolver and command construction
directly, then use one fresh POC or one tiny synthetic subject only to validate
that the shared path still invokes the existing drivers correctly.

### Unit granularity caveat for the benchmark resolver

Sampling the prepared benchmark metadata showed that the subject-level manifest
is not yet a unit-level worklist:

- Stress `meta.json` records `subject_id`, repo, target `contract`, original
  source path, solc binary/version, compile rung, and status. It does not list
  the target contract's public/external unit names. `TARGETS.csv` records
  `named_entry_points` and `writing_entry_points` counts, but not the names.
- Peer182 `meta.json` likewise records the target `contract`, target-rule
  provenance, source file, peer arm, solc version, and whether the original
  subject had asserts. It is still a contract-level subject, not a PUT unit
  list.
- BugFix124 `meta.json` adds `changed_functions`, but that field is about the
  patch and should not be reused as the callable-unit universe. It can guide an
  RQ2-focused bug-kill slice, but using it as the only VeriPUT unit set would
  silently exclude reachable public/external entry points.

Therefore the benchmark adapter needs an explicit unit-list source. Acceptable
sources are:

- a trusted precomputed unit manifest generated from the same flattened source
  and solc AST;
- an explicit command-line unit selection for a diagnostic/smoke run; or
- an AST-based enumerator that is scoped to the target contract, not a regex
  over Solidity text.

The old `collect.py::enumerate_own_callable_functions()` is useful as a
reference for AST walking, but its project-own-file filter depends on historical
flat markers and project labels. For prepared flat subjects, the safer first
benchmark adapter is target-contract scoped: enumerate public/external
functions declared or inherited in the selected contract as reported by the
Solidity AST/ESBMC target model, and record any ambiguity or unsupported
fallback/receive/proxy-only target explicitly.

Do not derive the real benchmark's denominator from the POC split. The POC
split is a debugging subset with hand fixtures and per-unit input directories;
the benchmark denominator must be a manifest over Stress/Peer/BugFix prepared
subjects plus their chosen unit enumeration rule.

## 2026-08-06 prepared-subject adapter progress

Implemented a first safe bridge from VeriPUT prepared benchmark subjects to the
existing Stage-2/Stage-3 drivers, without running ESBMC:

- New `notes/coverage/scripts/veriput_subjects.py` resolves prepared subjects
  in `Results/Stress243/subjects`, `Results/Peer182/subjects`, and
  `Results/BugFix124/subjects`. It requires an explicit unit because the
  prepared `meta.json` files are contract-level, not unit-level manifests.
- `certify_all.py` now accepts `--subject-dir` or `--subject-id` plus exactly
  one `--unit`. It bypasses the historical six-entry `BENCHMARKS` table only
  on that explicit prepared-subject path, constructs the lower-level
  `solidity_path_generalise.py --sol --ast --contract --unit` command, and
  writes a `subject` block into every future result row from this path.
- `certify_all.py --dry-run` now resolves inputs and prints the exact child
  command without launching ESBMC, appending JSONL rows, writing driver logs, or
  generating a missing AST. This is the cheap way to validate Dataset command
  construction before spending a proof budget.
- `put_all.py` now reads the `subject` block from a cert row. For such rows it
  resolves `flat.sol`, `flat.sol.solast`, and the target contract directly from
  the row rather than from the old `BENCHES` table or POC private input
  directories. Gate 5 now treats prepared-subject rows as corpus rows.

Validation completed without consuming any POC/ESBMC attempt:

```sh
python3 -m py_compile \
  notes/coverage/scripts/veriput_subjects.py \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/put_all.py \
  scripts/test_veriput_subjects.py \
  scripts/test_poc_stage_drivers.py \
  scripts/test_put_all_accounting.py
python3 scripts/test_veriput_subjects.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_put_all_accounting.py
python3 notes/coverage/scripts/certify_all.py \
  --subject-dir /home/samson/workspace/VeriPUT/Results/Stress243/subjects/balancer__balancer-v3-monorepo__ProtocolFeeHelper \
  --unit getProtocolFeePercentageCache \
  --out /tmp/veriput_subject_resolver_smoke.jsonl \
  --dry-run
git diff --check -- \
  notes/coverage/scripts/veriput_subjects.py \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/put_all.py \
  scripts/test_veriput_subjects.py
```

The dry-run constructed the expected Stage-2 command over the Stress prepared
subject and reported that `flat.sol.solast` would be generated before a real
run. No ESBMC process, Forge run, POC attempt, or benchmark proof run was
started.

Remaining gap after the first prepared-subject adapter:

- Need a persisted unit enumeration manifest and denominator policy. The first
  adapter can resolve or list one subject's AST-backed function units, but it
  does not yet materialize a Stress/Peer/BugFix-wide worklist.
- Need a small prepared-subject Stage-1 adapter, or an explicit decision that
  Stage 2 will consume an existing enumeration report. The current patch
  addresses Stage 2/3 source resolution and command construction, not full
  end-to-end benchmark scheduling.

### Prepared-subject unit enumeration progress

The first AST-backed unit enumerator now exists:

- `veriput_subjects.py::enumerate_subject_units()` reads the prepared subject's
  compact solc AST, finds the target contract from `meta.json`, follows
  `linearizedBaseContracts`, and returns named public/external
  `FunctionDefinition` units scoped to that target contract.
- Overrides/inherited duplicates are deduplicated by unit name for the current
  first adapter. This matches the existing `--focus-function <unit>` interface,
  which also names a unit by function name unless an overload path-function is
  supplied separately.
- `fallback`/`receive` and public state variable getters are reported in a
  `skipped` list with reasons. They are ABI entry points but not currently
  usable as named `--focus-function` units backed by a FunctionDefinition, so
  they must not silently enter the denominator as if tested.
- `certify_all.py --subject-* --list-subject-units` exposes this enumeration
  without launching ESBMC. If the AST is missing and `--dry-run` is present, it
  reports that the AST would be generated and exits without writing it.

Validation:

```sh
python3 -m py_compile \
  notes/coverage/scripts/veriput_subjects.py \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/put_all.py \
  scripts/test_veriput_subjects.py \
  scripts/test_poc_stage_drivers.py \
  scripts/test_put_all_accounting.py
python3 scripts/test_veriput_subjects.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_put_all_accounting.py
python3 notes/coverage/scripts/certify_all.py \
  --subject-dir /home/samson/workspace/VeriPUT/Results/Stress243/subjects/balancer__balancer-v3-monorepo__ProtocolFeeHelper \
  --list-subject-units --dry-run
```

Also verified on a temporary prepared subject with an existing compact AST:
`--list-subject-units` printed `own` and listed `fallback` as skipped. This
used only `/tmp` files and started no ESBMC/Forge process.

Remaining denominator work:

- Persist a unit-manifest for Stress/Peer/BugFix rather than requiring
  per-subject ad-hoc enumeration at run time.
- Decide how to account for overloads, public-state getters, fallback/receive,
  and proxy-only targets in the benchmark denominator. The current code records
  skips, but the paper/evaluation needs an explicit denominator policy.

### Unit manifest builder progress

The prepared-subject unit enumeration can now be materialized into a benchmark
manifest without starting ESBMC:

- New `notes/coverage/scripts/subject_unit_manifest.py` builds a
  `veriput-unit-manifest/v1` document for `stress243`, `peer182`, or
  `bugfix124`.
- Default behavior is read-only with respect to solc: if a subject has no
  compact AST, its row is `missing-ast`. This makes a safe census possible
  before spending any compile or proof budget.
- `--generate-ast` is explicit and invokes the subject's recorded `solc_bin` to
  create a missing AST before enumeration. This still does not start ESBMC.
- `--subject-id`, `--subject-root`, and `--limit` make it possible to build or
  preheat the manifest in bounded shards instead of accidentally sweeping the
  entire Dataset.
- `veriput_subjects.py::unit_manifest()` records summary counts:
  `subjects`, `ok`, `missing_ast`, `error`, `units`, and `skipped`.

Validation:

```sh
python3 -m py_compile \
  notes/coverage/scripts/veriput_subjects.py \
  notes/coverage/scripts/subject_unit_manifest.py \
  scripts/test_veriput_subjects.py \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/put_all.py
python3 scripts/test_veriput_subjects.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_put_all_accounting.py
python3 notes/coverage/scripts/subject_unit_manifest.py --benchmark stress243 --limit 3
python3 notes/coverage/scripts/subject_unit_manifest.py --benchmark peer182 --limit 2
python3 notes/coverage/scripts/subject_unit_manifest.py --benchmark bugfix124 --limit 2
```

The real prepared-subject samples all returned `missing-ast`, which matches the
current disk state: `Results/Stress243/subjects`, `Results/Peer182/subjects`,
and `Results/BugFix124/subjects` currently have `meta.json`/`flat.sol` but no
prebuilt `*.solast`. No ESBMC/Forge process and no POC attempt was used.

Next practical acceleration step:

- Preheat compact ASTs with `subject_unit_manifest.py --generate-ast` in small
  shards, recording the manifest after each shard. This is a compile-only
  cost, not a proof run, and unlocks fast deterministic unit denominator
  construction before any ESBMC POC/benchmark attempt.

### Resumable AST preheat support

`subject_unit_manifest.py` now has the controls needed for safe AST preheating:

- `--shard i/n` selects sorted subject positions by modulo after subject
  discovery. This lets a large population be split without changing the
  manifest schema or subject order.
- `--journal <path>` appends one JSONL row per processed subject and `fsync`s
  each row. If a long solc preheat is interrupted, completed subject results
  are already durable.
- `--resume-journal <path>` skips subject ids whose latest successful journal
  row has `status == "ok"`; non-ok rows are retried.
- The manifest summary now includes `skipped_resume` so a resumed shard cannot
  look like it simply had fewer subjects.
- `generated_at` is present on the CLI-built manifest, matching the library
  manifest schema.

Validation without ESBMC:

```sh
python3 -m py_compile \
  notes/coverage/scripts/subject_unit_manifest.py \
  notes/coverage/scripts/veriput_subjects.py \
  scripts/test_veriput_subjects.py \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/put_all.py
python3 scripts/test_veriput_subjects.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_put_all_accounting.py
python3 notes/coverage/scripts/subject_unit_manifest.py \
  --benchmark stress243 --limit 3 --shard 0/2
```

The `--limit 3 --shard 0/2` smoke run selected three sorted Stress subjects
from the shard and reported all as `missing-ast`, as expected. The added tests
cover shard selection and journal resume on temporary prepared subjects with
prebuilt compact ASTs. No solc invocation was needed for those tests, and no
ESBMC/Forge/POC run was started.

### Dataset target-contract alignment and AST safety

User alignment, 2026-08-06:

- POCs are diagnostic only. They are useful for checking the tool, but the
  eventual benchmark work is under `/home/samson/workspace/VeriPUT/Datasets`.
  Code changes must not bake in POC-specific shortcuts or success criteria.
- Dataset subjects already have a target contract, similar to the POC target
  contract idea: the target is not "all contracts in the file". The prepared
  `Results/*/subjects/*/meta.json` layout mirrors this with a `contract` field,
  and current unit enumeration remains target-contract scoped.
- Difficulty gradient should be treated as:
  `peer < bugfix124 <= stress203`. Existing scripts currently expose the
  prepared Stress population under the historical key `stress243`; keep that
  key compatible until the real Dataset/Results naming is normalized.

AST preheating is now safer:

- `veriput_subjects.py::generate_solast()` writes solc output to a unique temp
  file and atomically installs it with `os.replace()` only after solc returns
  `rc == 0`.
- Failed or timed-out solc invocations delete the temp file and do not leave an
  empty or partial `flat.sol.solast`, so interrupted preheat shards can be
  retried without poisoning later unit enumeration.
- `subject_unit_manifest.py --ast-timeout <seconds>` controls per-subject solc
  time when `--generate-ast` is explicitly set. Default is 60s. The manifest
  records `ast_timeout_s`, and each row records structured `ast` metadata:
  `generated/status/path`, plus `wall_s` and `stderr_tail` when solc actually
  runs.
- `ensure_solast()` remains as the compatibility wrapper used by `certify_all`,
  but now inherits the atomic write behavior.

Validation without ESBMC/Forge:

```sh
python3 -m py_compile \
  notes/coverage/scripts/veriput_subjects.py \
  notes/coverage/scripts/subject_unit_manifest.py \
  scripts/test_veriput_subjects.py \
  notes/coverage/scripts/certify_all.py \
  notes/coverage/scripts/put_all.py
python3 scripts/test_veriput_subjects.py
python3 scripts/test_poc_stage_drivers.py
python3 scripts/test_put_all_accounting.py
python3 notes/coverage/scripts/subject_unit_manifest.py \
  --benchmark peer182 --limit 2 --ast-timeout 1
git diff --check -- \
  notes/coverage/scripts/veriput_subjects.py \
  notes/coverage/scripts/subject_unit_manifest.py \
  scripts/test_veriput_subjects.py
```

The smoke manifest was read-only (`generate_ast=false`) and reported two
`peer182` subjects as `missing-ast`, preserving their target contracts from
metadata. No ESBMC run and no POC attempt was consumed.

### Dataset target manifest bridge

Added `notes/coverage/scripts/target_manifest.py` as the no-proof entry point
from real VeriPUT benchmark metadata to a frozen target list:

- Output schema: `veriput-eval/target/v1`; each row is
  `veriput-eval-target/v1`.
- It never invokes solc, Forge, fuzzing, or ESBMC. It only reads existing
  Dataset/Results metadata and validates that referenced source files exist.
- `bugfix124` reads `Datasets/Patch-Bug-Bench/summary.csv`. Each row preserves
  exactly one `target_contract`, records both `bug` and `fix` variants, and
  converts semicolon-separated `changed_functions` into `units_hint`. This
  implements the README's previously missing JSON target manifest bridge.
- `stress243` reads `Datasets/Stress-Projects/TARGETS.csv`. Default
  `--stress-scope include` selects rows with `include=yes`; optional
  `--stress-scope stateful` restricts further to `STATEFUL`.
- `peer182` reads the prepared `Results/Peer182/subjects/*/meta.json` target
  metadata, because `prepare_peer.py` already records the auditable target rule
  and alternatives needed to avoid CC-SolBMC multi-contract-file ambiguity.
- Peer has one non-upgraded/unavailable paper contract in the raw Dataset
  history. Ignore it for VeriPUT runs: the benchmark denominator is the
  `contracts_080` prepared population. `target_manifest.py` now skips any peer
  prepared row whose metadata is not `source_080 == true` or whose `source_file`
  is not under `contracts_080/`.
- `--benchmark stress203` is accepted only as an input alias and normalizes to
  the current prepared key `stress243`. Do not report `stress203` as a proven
  disk denominator yet: current files yield Stress denominators of 242
  (`include=yes`) or 213 (`STATEFUL`), plus the historical `TARGETS.csv` total
  of 243 including the one excluded mixin.

Real Dataset/Results census, no ESBMC/Forge/solc:

```sh
python3 notes/coverage/scripts/target_manifest.py --benchmark peer182
# ok=182 error=0
python3 notes/coverage/scripts/target_manifest.py --benchmark bugfix124
# ok=124 error=0
python3 notes/coverage/scripts/target_manifest.py --benchmark stress243
# ok=242 error=0  (include=yes)
python3 notes/coverage/scripts/target_manifest.py \
  --benchmark stress203 --stress-scope stateful
# normalized benchmark stress243, ok=213 error=0
python3 notes/coverage/scripts/target_manifest.py \
  --benchmark peer182 --benchmark bugfix124 --benchmark stress243
# ok=548 error=0  (182 + 124 + 242)
```

Validation:

```sh
python3 -m py_compile \
  notes/coverage/scripts/target_manifest.py \
  scripts/test_target_manifest.py
python3 scripts/test_target_manifest.py
```

This is still only target discovery, not unit enumeration and not PUT
generation. Next wiring step is to feed this manifest into subject/unit
enumeration so the denominator is: target contract first, then target's
public/external units, with `bugfix124.units_hint` available as a prioritization
hint rather than a hard filter.

### Target manifest to unit manifest bridge

`notes/coverage/scripts/subject_unit_manifest.py` now accepts
`--target-manifest <veriput-eval/target/v1.json>`.

Behavior:

- Only `status=ok` target rows are enumerated.
- Each target row is resolved through the prepared `Results/*/subjects`
  metadata, not through POC fixtures.
- Target contract mismatch is fail-closed: the row becomes `status=error`
  with both the target manifest contract and prepared subject contract recorded.
- The original target row is attached to the unit row as `target`.
- `units_hint` is preserved as priority metadata, not a filter:
  - If AST enumeration succeeds, matching hints are `hinted_units`; hints not
    found in enumerated public/external units are `missing_unit_hints`.
  - If AST is absent or enumeration did not run, hints are
    `pending_unit_hints`, because absence has not been proven.
- Summary now records `hinted_units`, `missing_unit_hints`, and
  `pending_unit_hints`.

Real no-proof smoke:

```sh
python3 notes/coverage/scripts/target_manifest.py --benchmark bugfix124 | \
  python3 notes/coverage/scripts/subject_unit_manifest.py \
    --target-manifest /dev/stdin --limit 3
# subjects=3 missing_ast=3 pending_unit_hints=5
```

That smoke is the expected ground-truth state before AST preheat: the first
three BugFix targets have changed-function hints, but their compact AST files
are not present yet, so we cannot classify hints as matched/missing. No solc,
Forge, fuzzing, ESBMC, or POC attempt was consumed.

Full real target->unit no-proof census, 2026-08-06:

```sh
python3 notes/coverage/scripts/target_manifest.py \
  --benchmark peer182 --benchmark bugfix124 --benchmark stress243 | \
python3 notes/coverage/scripts/subject_unit_manifest.py \
  --target-manifest /dev/stdin
```

Summary:

- total target rows: 548
- `peer182`: 182 `missing-ast`, 0 error
- `bugfix124`: 124 `missing-ast`, 0 error
- `stress243`: 203 `missing-ast`, 39 error
- pending unit hints: 381, all from targets whose AST has not been enumerated

Interpretation:

- The user-provided `stress203` name aligns with the currently usable prepared
  Stress subset: `TARGETS.csv` has 242 `include=yes` rows, but 39 corresponding
  `Results/Stress243/subjects/*/meta.json` rows are not usable
  (`status=compile-failed` or equivalent), leaving 203 prepared targets.
- Those 203 still need compact AST preheat before target-contract unit
  enumeration can produce a real unit denominator.
- Do not run ESBMC before this is resolved; otherwise proof attempts would be
  spent without a verified unit denominator or changed-function prioritization
  map.

### Readiness report

Added `notes/coverage/scripts/veriput_readiness.py`.

Input: a `veriput-unit-manifest/v1` JSON document. Output:
`veriput-readiness/v1`.

It is read-only: it never invokes solc, Forge, fuzzing, ESBMC, and should not
be run with an `--out` path under `/home/samson/workspace/VeriPUT` while other
experiments depend on that tree.

The report groups:

- status by benchmark (`missing-ast`, `error`, `ok`)
- prepared error buckets, e.g. `prepared-status:compile-failed`
- changed-function hint state (`pending_unit_hints`, `hinted_units`,
  `missing_unit_hints`)
- missing AST preheat readiness by solc key
- `preheatable_missing_ast` versus `missing_solc_bin`
- small sample rows for missing AST, prepared errors, pending hints, and
  missing hints

Real read-only readiness smoke:

```sh
PYTHONDONTWRITEBYTECODE=1 \
python3 notes/coverage/scripts/target_manifest.py \
  --benchmark peer182 --benchmark bugfix124 --benchmark stress243 | \
PYTHONDONTWRITEBYTECODE=1 \
python3 notes/coverage/scripts/subject_unit_manifest.py \
  --target-manifest /dev/stdin | \
PYTHONDONTWRITEBYTECODE=1 \
python3 notes/coverage/scripts/veriput_readiness.py - --sample-limit 3
```

Key output:

- status: `error=39`, `missing-ast=509`
- by benchmark:
  - `bugfix124`: `missing-ast=124`
  - `peer182`: `missing-ast=182`
  - `stress243`: `missing-ast=203`, `error=39`
- prepared errors:
  - `stress243`: `prepared-status:compile-failed=32`,
    `prepared-status:flatten-failed=7`
- hints:
  - `bugfix124`: `pending_unit_hints=381`
- preheat:
  - `bugfix124`: `preheatable_missing_ast=124`
  - `peer182`: `preheatable_missing_ast=182`
  - `stress243`: `preheatable_missing_ast=51`, `inferable_solc_bin=152`,
    0 true `missing_solc_bin`
- missing AST solc buckets:
  - `bugfix124`: `solc-0.8.29=117`,
    `solc-0.8.29 --optimize --optimize-runs 200 --via-ir=6`,
    `solc-0.8.29 --via-ir=1`
  - `peer182`: `solc-0.8.29=182`
  - `stress243`: `inferred:solc-0.8.35=96`,
    `inferred:solc-0.8.15=35`, `inferred:solc-0.8.17=19`,
    `inferred:solc-0.8.19=1`, `inferred:solc-0.8.26=1`,
    plus explicit `solc-0.8.35=46`, `solc-0.8.15=5`

Implication for next work:

1. Do not run ESBMC/PUT yet.
2. Preheat AST first into an external cache, not into Dataset/Results. The 306
   straightforward explicit-solc rows are `bugfix124=124`, `peer182=182`; the
   Stress rows add 51 explicit-solc rows and 152 inferable-solc rows when
   `--use-inferred-solc-bin` is intentionally enabled.
3. Use `ast_preheat_schedule.py` first to audit the exact per-subject preheat
   commands; it should show 508 unique schedulable subject jobs, 1 duplicate
   target row, and 0 unschedulable rows on the current real target set when an
   external cache root is supplied.
4. Use `ast_preheat_run.py --dry-run --journal <external.jsonl>` to audit the
   pending/resume set before executing. Real preheat execution must pass an
   external journal path and can be sharded/resumed without rerunning rows that
   already reached `ok`.
   Use `ast_preheat_journal.py --schedule <schedule.json> --retry-out <retry.json>`
   after any interrupted run to summarize failures and rebuild the retry set.
5. After preheat, rerun `subject_unit_manifest.py` against the same
   `--ast-cache-root`, then run `unit_manifest_gate.py`. Only when the gate is
   not `blocked` should `unit_schedule.py` produce priority-ordered per-unit
   `certify_all.py --subject-* --unit ...` jobs.
   Audit that schedule with `unit_schedule_run.py --dry-run --journal <jsonl>`
   before real certification. For real runs, pass an explicit external journal
   and the agreed memory cap through `--memlimit-gb`. After any interrupted or
   completed unit run, use
   `unit_schedule_journal.py --schedule <schedule.json> --retry-out <retry.json>`
   to summarize latest status and build the next retry schedule before spending
   the next time/memory gradient.
   Prefer `unit_campaign_plan.py <base-schedule.json> --journal <a1.jsonl> ...`
   as the controlling layer: it encodes the default 60s/8GiB, 120s/8GiB, and
   600s/10GiB attempts, writes the filtered schedule for the next attempt, and
   prints the exact `unit_schedule_run.py` argv to audit before execution.
   Once `unit_schedule_run.py` reports completed certifier commands, summarize
   the actual Stage-2 result JSONL with
   `certify_result_summary.py <cert-out.jsonl> --schedule <attempt-schedule.json>`.
   Use that gate, not runner exit codes alone, to decide whether certified
   path coverage and region strength are sufficient for PUT emission and later
   mutation/vulnerability-regression experiments. Also pass the same result
   JSONL into the next campaign decision via
   `unit_campaign_plan.py <base-schedule.json> --journal <a1.jsonl> --cert-jsonl <cert-out.jsonl>`;
   otherwise runner-ok units with weak or missing certified regions would be
   incorrectly treated as done.
6. Separately inspect the 39 Stress prepared errors; 32 compile-failed and 7
   flatten-failed are not unit-denominator rows until fixed or explicitly
   excluded by benchmark policy.

## 2026-08-06 bytesN ABI-length model repair

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No POC ESBMC attempt was consumed. All solver runs below were ESBMC
  regression/temp-copy checks.

Problem:

- Solidity fixed bytes (`bytes1` ... `bytes32`) lower to `BytesStatic {
  data[32], length }`.
- `assign_param_nondet` passed a whole-struct nondet for bytesN parameters, so
  `.length` was free. This admits calldata-impossible states and refuted a
  Solidity tautology: `b == bytes32(uint256(b))`.
- The older obvious fix, `bytes_static_from_uint(nondet_uint256(), N)`, proved
  the tautology but hid the payload nondet from witness/Foundry recovery.
- A later attempted fix, `with(nondet_struct, length=N)`, also proved the
  tautology but still broke Foundry recovery (`DEFAULTED BYTES4`, 1 case).

Final code shape:

- `src/solidity-frontend/solidity_convert_call.cpp` keeps scalar/bytesN harness
  parameters as direct `get_nondet_expr(t, nondet_scalar)` arguments. This
  preserves the raw `nondet$symex::...` symbol for counterexample harvesting.
- `src/solidity-frontend/solidity_convert_modifier.cpp` prepends, at function
  entry, `ASSUME(param.length == N)` for every function argument carrying
  `#sol_bytesn_size`.
- This pins ABI-legal bytesN length in the verifier without changing the
  recoverable payload expression at the call site.
- The redundant `has_body` conjunct in the same wrapper block was removed after
  cppcheck reported it is always true in that scope.

Verification:

- Build:
  `timeout 5m cmake --build build -j2`
  passed. Existing warning remains:
  `solidity_convert_call.cpp:2811 unused parameter 'base'`.
- Cppcheck on changed Solidity frontend files passed with no output:
  `git diff --name-only --diff-filter=d HEAD | grep 'src/solidity-frontend/.*\.\(cpp\|h\)$' | xargs -r cppcheck ...`
- Direct regression:
  `timeout 60s bash -lc 'ulimit -Sv 8388608; build/src/esbmc/esbmc regression/esbmc-solidity/solidity_bytesn_param_length_free_knownbug/contract.sol --contract C --solidity-max-tx 1'`
  returned `VERIFICATION SUCCESSFUL`.
- CTest:
  `timeout 60s bash -lc 'ulimit -Sv 8388608; cd build && ctest -R solidity_bytesn_param_length_free_knownbug --output-on-failure'`
  passed 1/1.
- GOTO inspection confirmed the intended split:
  dispatcher call remains
  `roundTrip(..., NONDET(struct BytesStatic ...))`, while function entry has
  `ASSUME b.length == 32`.
- Foundry temp-copy check:
  `foundry_covgen_bytesN_fail` copied to `/tmp`, then run with
  `--branch-coverage --generate-foundry-testcase --no-assertions` under
  60s/8GiB. It generated `2 case(s)`, emitted no `DEFAULTED` warning, and the
  generated calls used exact-width payloads:
  `poke(bytes4(0x12345678))` and `poke(bytes4(0xffffffff))`.
  The constructor argument rendered as `bytes4(0x00000000)` in that run; it is
  semantically irrelevant to the covered branch and still exact-width.

## 2026-08-06 return-value R2 repair for pure/view PUT strength

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No POC ESBMC attempt was consumed. The ESBMC validation used a `/tmp`
  synthetic copy shaped like `P19_ReturnShapes.tern_lit`.

Finding:

- The old P19 `put.json` files are stale artifacts: their coverage reports do
  not carry `return_value_known`, and their assertion specs predate the current
  `vars_policy: state-exact` writer.
- Current ESBMC already materializes scalar return ghosts. A `/tmp` synthetic
  `return x > y ? 10 : 20` run produced `return_value_known=true`, and
  `--path-cov-assert` emitted `retlive`, `return == 0`, and `return != 0`.
- With `--propose-r2`, ESBMC also proved the strong R2 rows
  `return == 20 HOLDS` and `return in [20, 20] HOLDS`.

Bug fixed:

- `propose_r2_batch` generated structured terms containing `pre` for the
  synthetic `return` target. ESBMC correctly refused those candidates because
  a return value has no entry snapshot.
- `run_r2_passes` only merged state-shaped R2 rows (`post ...`). It ignored
  return-shaped rows (`return == ...`, `return in [...]`), then logged the pass
  as empty. The final PUT kept only the weak R1 assertion `return != 0` even
  though ESBMC had proved `return == 20`.

Final code shape:

- `scripts/solidity_path_put.py` now has `r2_term_mentions_pre(term)`.
- For `RETURN_VAR`, typed R2 candidates filter out any structured term that
  directly or indirectly mentions `pre`; state variables still keep the full
  grammar including `pre + amount` and delta terms.
- `run_r2_passes` now recognizes return R2 rows:
  `return == <non-baseline>` and `return in [lo, hi]`.
- Empty R2 pass diagnostics now say `NO R2 ROW`, not `NO DELTA ROW`, because
  R2 now includes return equality/absolute rows as well as state delta rows.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 145/145 tests.
- `/tmp/veriput-return-smoke-*` synthetic Stage-4 run with
  `--propose-r2 --timeout 60 --memlimit 8g` generated a PUT with 2 fuzz
  parameters and 4 return assertions, including:
  `assertEq(uint256(_put_ret), 20, "return: return == 20");`.

Expected impact:

- P19-style no-state/pure-return units should no longer fall into
  `no-oracle:ladder-refusal` after a fresh Stage-4 rerun with current ESBMC and
  `--propose-r2`.
- This is not POC overfitting: the repair applies to every scalar-return unit
  whose strong oracle is a returned value rather than a storage slot.

## 2026-08-06 shared strong recipe for benchmark unit scheduling

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No POC ESBMC attempt was consumed. Validation was Python compile/unit tests
  and read-only command construction.

Finding:

- `poc_one.py` already ran Stage 2 under `veriput-strong/7` and Stage 3 with
  `--propose-r2 --fuzz-r2-prefilter`.
- The prepared-subject benchmark path did not: `unit_schedule.py` generated
  `certify_argv` with only `--subject-dir`, `--subject-benchmark`, `--unit`,
  optional AST cache, and `--out`. A Dataset/Results campaign would therefore
  run the weaker `certify_all.py` defaults even though the POC path had the
  stronger recipe.
- This is a general orchestration bug, not a POC-region issue. It affects
  Stress/Peer/BugFix scheduling before any ESBMC query is spent.

Code shape:

- Added `notes/coverage/scripts/veriput_recipe.py` as the single home for:
  `STRONG_RECIPE_VERSION = "veriput-strong/7"`,
  `strong_certify_args(...)`, and `strong_put_args()`.
- `poc_one.py` now imports the shared recipe instead of carrying its own copy;
  its public `strong_certify_args` name still exists through the import, so the
  existing POC driver tests keep working.
- `unit_schedule.py` now appends the shared Stage-2 strong recipe to every
  prepared-subject `certify_argv` and records `recipe_version` in the schedule.
  The generated benchmark jobs now carry the same important switches as the POC
  recipe: `--skip-bracket`, level0 perturbation, witness probes/ladders,
  agreed-state pinning, env-disagreed coordinates, state struct fields, and
  `--slot-coords 8`.
- Stage 4 is still controlled by `put_all.py`; POC Stage 3 imports the shared
  `strong_put_args()` for `--propose-r2` and fuzz-refute prefilter. A future
  benchmark Stage-4 runner should reuse that same helper rather than spelling
  the R2/fuzz switches again.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/veriput_recipe.py notes/coverage/scripts/poc_one.py notes/coverage/scripts/unit_schedule.py scripts/test_unit_schedule.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed and now asserts that the written unit schedule includes
  `veriput-strong/7`, `--skip-bracket`, and `--pin-agreed-state`.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_poc_stage_drivers.py`
  passed, including the `probe_witnesses=0` no-ladder case.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py` and
  `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule_run.py` passed.

## 2026-08-06 shared strong Stage-4 switch

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No POC ESBMC attempt was consumed. Validation was Python compile/tests and a
  `poc_one.py --dry-run` command inspection.

Finding:

- After the shared Stage-2 recipe repair, `put_all.py` still exposed typed R2
  and the Foundry refutation prefilter only as low-level independent switches.
  `poc_one.py` had to spell all of them, and a future benchmark Stage-4 runner
  would have had to copy the same list again.
- That is exactly the drift that had made benchmark Stage 2 weaker than POC
  Stage 2. The Stage-4 fix should therefore be one explicit method switch, not
  another copied flag bundle.

Code shape:

- `notes/coverage/scripts/veriput_recipe.py` now exposes named constants for
  the strong PUT settings:
  auto-unwind 1, R2 depth 1, R2 term budget 96, R2 candidate budget 128,
  fuzz runs 256, and fuzz R2 candidate budget 128.
- `notes/coverage/scripts/put_all.py` now accepts `--strong-recipe`. It applies
  those constants after argparse fills defaults, enabling `--propose-r2` and
  `--fuzz-r2-prefilter` and forcing `--auto-unwind 1`. The old low-level
  switches remain available for ablation arms.
- `put_all.py` prints `Stage-4 recipe : veriput-strong/7` when that switch is
  active, so a PUT table records that the strong path was requested.
- `notes/coverage/scripts/poc_one.py` Stage 3 now passes only
  `--strong-recipe` to `put_all.py` instead of spelling the R2/fuzz list.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/veriput_recipe.py notes/coverage/scripts/put_all.py notes/coverage/scripts/poc_one.py scripts/test_put_all_accounting.py scripts/test_poc_stage_drivers.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_put_all_accounting.py`
  passed and checks that `--strong-recipe` expands to auto-unwind 1, typed R2,
  and fuzz-refute settings.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_poc_stage_drivers.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/poc_one.py aqua_Aqua__Aqua__push --stage 3 --attempt 1 --dry-run | rg -- '--strong-recipe|--propose-r2|fuzz-r2|put_all.py'`
  showed the Stage-3 command now invokes `put_all.py ... --strong-recipe`.

Follow-up status:

- Closed by the next section: `benchmark_pipeline_plan.py` now emits a copyable
  Stage-4 `put_all.py --strong-recipe` command when certification is ready.

## 2026-08-06 benchmark Stage-4 PUT command planning

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No POC ESBMC attempt was consumed. This is read-only orchestration wiring and
  Python unit coverage only.

Finding:

- The benchmark pipeline could already identify `certification-ready-for-put`,
  but that next action did not provide the actual Stage-4 command. After context
  compaction, that would force rereading the runner contract or invite another
  weak/manual flag bundle.

Code shape:

- `notes/coverage/scripts/benchmark_pipeline_plan.py` now builds
  `next_runs.stage4_put` when a certification summary gate is `ready`.
- The planned command is:
  `put_all.py --cert <cert-jsonl> --strong-recipe --timeout <s> --memlimit-gib <gib> --forge-timeout <s> --out-root <root>`.
- Defaults are benchmark-safe planning defaults: ESBMC timeout 600s, ESBMC
  memory 8 GiB, Forge timeout 300s, and output root `<out-dir>/put-roundtrip`
  when `--out-dir` is set. These are planner defaults, not a POC retry-policy
  override.
- `summary.next_action` copies the same `runner_cmd`, `runner_argv`, output
  root, recipe version, and budget fields, so the JSON summary has a single
  copyable next command.
- The recipe string comes from `veriput_recipe.STRONG_RECIPE_VERSION` rather
  than a local hardcode.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed and now checks the certification-ready path selects
  `command_kind == "stage4_put"` with `--strong-recipe`.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_put_all_accounting.py`
  passed, confirming the planned Stage-4 switch still expands to the shared
  strong PUT recipe.
- `git diff --check -- notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_benchmark_pipeline_plan.py notes/VeriPUT_handoff_memory.md`
  passed.
- `python3 -m pylint notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_benchmark_pipeline_plan.py`
  was run. It still exits 28 on existing style debt such as import-position
  after `sys.path` setup, missing docstrings, one broad exception in the local
  test runner, and existing encoding warnings; the one actionable line-length
  warning was fixed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.

## 2026-08-06 protected benchmark write-path guard

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark proof run was
  started.

Finding:

- `benchmark_pipeline_plan.py` is read-only with respect to executing work, but
  its optional planning outputs and generated next-run commands can point to
  future write paths. The user explicitly warned that another experiment
  depends on Dataset contents, so relying only on operator discipline is too
  brittle.

Code shape:

- The pipeline planner now rejects planned write paths under
  `<veriput-root>/Datasets` or `<veriput-root>/Results` before it reads target
  metadata or writes child docs.
- Protected planned-write arguments are:
  `--ast-cache-root`, `--out-dir`, `--cert-out`,
  `--next-ast-preheat-journal`, `--next-journal`, and `--put-out-root`.
- Existing input journals/result JSONLs remain readable inputs; this guard is
  about paths the planner or its emitted next-run commands would write later.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed and now checks all six protected planned-write arguments fail closed
  under Dataset/Results.
- `git diff --check -- notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_put_all_accounting.py` and
  `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `python3 -m pylint notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_benchmark_pipeline_plan.py`
  was run. It still exits 28 on existing style debt such as import-position
  after `sys.path` setup, missing docstrings, complexity/statement count, a
  broad exception in the local test harness, and existing encoding warnings.
  No new line-length warning remains.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.

## 2026-08-06 protected AST generation entry point

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark proof run was
  started.

Finding:

- The benchmark planner now refuses write paths under Dataset/Results, but a
  direct `subject_unit_manifest.py --generate-ast` call could still write
  `flat.sol.solast` back into a real prepared subject directory when
  `--ast-cache-root` was omitted. That is too easy to trigger during AST
  preheat debugging and conflicts with the current "do not touch Dataset/Results"
  operating rule.

Code shape:

- `subject_unit_manifest.py` now fails closed before invoking solc when:
  - `--generate-ast` targets a subject under `<VERIPUT_ROOT>/Results` without
    an external `--ast-cache-root`; or
  - `--generate-ast --ast-cache-root` points under `<VERIPUT_ROOT>/Datasets` or
    `<VERIPUT_ROOT>/Results`.
- Synthetic `/tmp` prepared subjects without a VeriPUT Results root are still
  allowed to generate ASTs in place. That preserves the atomic-write regression
  tests for the lower-level generator while protecting real benchmark trees.
- External AST cache generation remains the supported preheat path and keeps
  `solast_source == "cache"` in subject records.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/subject_unit_manifest.py scripts/test_veriput_subjects.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_veriput_subjects.py`
  passed and now includes a temporary `VERIPUT_ROOT`/`Results/Stress243/subjects`
  fixture proving both no-cache and Results-cache generation are refused.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_ast_preheat_schedule.py` and
  `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- Real refusal smoke:
  `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/subject_unit_manifest.py --benchmark peer182 --subject-id peer_ccsolbmc__AIRBets --generate-ast`
  returned `rc=1` with
  `requires external --ast-cache-root; refusing to write under /home/samson/workspace/VeriPUT/Results`.
- `python3 -m pylint notes/coverage/scripts/subject_unit_manifest.py scripts/test_veriput_subjects.py`
  was run. It exits 20 on existing style debt such as import-position after
  `sys.path` setup, missing docstrings, unspecified encoding, and the local
  tests' subprocess calls without explicit `check`. No line-length warning was
  reported.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 mapping-slot source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Mapping-heavy contracts need stronger R2 than generic mechanical terms can
  cheaply find. Common ledger shapes are direct slot writes such as
  `balances[msg.sender] += amount`, `allowance[msg.sender][spender] -= amount`,
  and `allowance[msg.sender][spender] = amount`.
- This is not allowed to guess storage names. A source-prioritized mapping R2
  candidate is safe only when:
  - the AST left-hand side can be reconstructed as an exact mapping slot name;
  - every key is either a declared parameter with a type matching solc's storage
    layout key type, or `msg.sender` for an address-keyed level;
  - the slot's `<mapping><tail>` is present in the solc-derived `maps` table.
- Fuzz remains refute-only. This change only changes which candidates are asked
  first; ESBMC still has to certify each survivor.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now accepts the
  solc-derived `maps` table and treats readable mapping slots like scalar state
  variables for source-prioritized R2 candidate mining.
- It recognizes:
  - `m[k] += renderedUintOrLiteral` as `post - pre == term`;
  - `m[k] -= renderedUintOrLiteral` as `pre - post == term`;
  - `m[k] = m[k] + term` / `m[k] = term + m[k]` / `m[k] = m[k] - term`;
  - `m[k] = renderedParamOrLiteral` as `post == term`.
- Nested mapping names preserve key order (`m[a][b]`) by peeling
  `IndexAccess` inside-out and then reversing to source order.
- Key type mismatches fail closed. For example, an `address` parameter is not
  accepted as a key for `mapping(uint256 => uint256)`.
- The Stage-4 main flow now passes `maps=maps` into
  `source_assignment_r2_specs()`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `151 test(s) ran, 151 declared in this module`.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 unary-update source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Solidity nonces and counters commonly use `++` / `--` rather than explicit
  assignment syntax. These are semantically one-step deltas, so asking ESBMC
  `post - pre == 1` or `pre - post == 1` first is stronger and cheaper than
  waiting for the mechanical R2 term product.
- This remains a source-prioritized candidate only. ESBMC must still certify it;
  fuzz can only refute it.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now recognizes
  `UnaryOperation` nodes with operator `++` or `--`.
- It emits:
  - unsigned scalar `x++` / `++x` as inc-by-one;
  - unsigned scalar `x--` / `--x` as dec-by-one;
  - readable mapping-slot `_nonces[msg.sender]++` / `--` analogously, with the
    same solc-layout `maps` gating as assignment-shaped mapping candidates.
- Other unary operations (`delete`, arithmetic negation, logical negation) are
  not mined.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `152 test(s) ran, 152 declared in this module`.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 delete-zero source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- `delete` is a common reset idiom in Solidity. For unsigned integers and bools,
  it gives a strong endpoint candidate: post-state equals zero/false.
- The rule is deliberately conservative:
  - unsigned scalar state: `delete count` -> `post == 0`;
  - bool scalar state: `delete ready` -> `post == false` encoded as verifier
    literal `0`;
  - readable unsigned/bool mapping slot:
    `delete balances[msg.sender]` -> `post == 0`;
  - address clears are skipped for now until address literal endpoint semantics
    are pinned by a separate verifier/emitter test.
- As with other source R2 mining, this only prioritizes the query. ESBMC still
  proves or rejects the candidate, and fuzz only refutes.

Code shape:

- `scripts/solidity_path_put.py` now has a `zero_term()` helper for bool and
  unsigned zero endpoints.
- `source_assignment_r2_specs()` recognizes `UnaryOperation` with operator
  `delete` and emits source-prioritized equals candidates for readable scalar
  state or mapping slots.
- Mapping delete candidates reuse the same `slot_lhs()` and solc `maps` gating
  added for assignment-shaped mapping slot R2.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `153 test(s) ran, 153 declared in this module`.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 address-zero source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Owner/admin/fee receiver style contracts often implement renounce/reset logic
  as `delete owner` or `owner = address(0)`. A strong PUT should ask the
  verifier for `post == 0` before spending candidate budget on broad mechanical
  R2 terms.
- The existing renderer already represents address storage reads as masked
  `uint256`, and R2 structured literal `0` renders as Solidity literal `0`.
  Therefore `post == 0` is a well-formed address-zero endpoint assertion.
- Fuzz remains refute-only; ESBMC still certifies any candidate before the PUT
  emits it.

Code shape:

- `scripts/solidity_path_put.py` `zero_term()` now includes address,
  contract, and interface state values in the source `delete` rule.
- Added `address_zero_term()` for the exact source AST shape
  `address(0)`: a `FunctionCall` type conversion whose callee is an
  `ElementaryTypeNameExpression` named `address` and whose only argument is the
  unitless numeric literal `0`.
- Direct assignments now mine `owner = address(0)` and
  `owners[msg.sender] = address(0)` as source-prioritized `post == 0`
  candidates, with mapping slots still gated by solc `maps`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `154 test(s) ran, 154 declared in this module`.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 environment-coordinate source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Environment-driven units often have their strongest postcondition in
  `msg.sender` or `msg.value`, for example `owner = msg.sender`,
  `paid = msg.value`, `total += msg.value`, and
  `balances[msg.sender] += msg.value`.
- These are source-priority candidates only when the environment coordinate is
  already rendered in the certified region. The miner does not introduce hidden
  `msg.*` assumptions; it only reuses region inputs that the harness already
  exposes.
- `msg.sender` is treated as an id endpoint. `msg.value` is treated as a
  numeric endpoint or numeric delta. Mapping slots remain gated by the solc
  storage-layout-derived `maps` table and the earlier exact slot-name recovery.
- Fuzz is still only a cheap refutation layer. ESBMC remains the only
  certification step for any source R2 candidate.

Code shape:

- `scripts/solidity_path_put.py` now recognizes `msg.sender` and `msg.value`
  AST member accesses as coordinate terms when their kind matches the target
  storage or return type and the coordinate appears in `rendered_coords`.
- Direct scalar and exact mapping-slot assignments now mine:
  `post == msg.sender`, `post == msg.value`, and
  `post - pre == msg.value` for `+=`/`-=` style updates.
- The same coordinate helper is shared by return-expression mining, so a
  rendered `return msg.sender` or `return msg.value` can also be prioritized
  when its declared return kind matches.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `155 test(s) ran, 155 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 arithmetic-assignment source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Fee, cap, quote, and normalization code commonly writes state from a simple
  expression over the unit input, for example `fee = amount + 7`,
  `scaled = 2 * amount`, `paidLess = msg.value - 1`, and
  `quote[msg.sender] = amount * 3`.
- The structured R2 grammar and Foundry renderer already support one-level
  arithmetic endpoint terms. The missing piece was source prioritization for
  direct storage/mapping assignments; without it, these strong endpoints could
  be pushed behind broad mechanical candidates.
- The rule remains conservative: operands must be already-rendered numeric
  coordinates or unitless decimal literals; division is accepted only when the
  RHS is a nonzero literal; nested arithmetic is not mined by this direct
  assignment rule. Fuzz can only refute these; ESBMC still certifies.

Code shape:

- `scripts/solidity_path_put.py` now has `numeric_endpoint_term()` for
  one-level `+`, `-`, `*`, and safe `/` structured terms.
- Direct unsigned scalar and exact mapping-slot assignments now add
  source-prioritized `post == (<expr>)` candidates using that helper. Existing
  direct coord/literal candidates deduplicate through the same candidate key.
- Mapping arithmetic endpoints still reuse solc `maps` and exact slot-name
  recovery, so this is not a whole-mapping guess.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `156 test(s) ran, 156 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 rendered-state-coordinate source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Stage 4 already passes scalar `state.<field>` region/pin coordinates into
  the typed R2 batch when they are rendered. ESBMC's `--path-cov-assert` can
  resolve them as entry-state coordinates, and the Foundry renderer can spell
  them when the PUT establishes that state slice.
- The source-priority miner did not use those rendered state coordinates on the
  RHS of source assignments. This missed strong candidates such as
  `mirror = seed`, `next = seed + amount`, `total += seed`,
  `savedOwner = owner`, and `copiedReady = ready` when `state.seed`,
  `state.owner`, or `state.ready` were already part of the certified region.
- The rule remains gated by `rendered_coords`. It does not invent new
  entry-state assumptions, and it deliberately stays on scalar state variables;
  mapping-slot state coordinates are left to the existing explicit slot naming
  path.
- Fuzz remains refute-only. ESBMC remains the only proof step for the resulting
  R2 row.

Code shape:

- `scripts/solidity_path_put.py` `coord_term()` now maps a RHS state-variable
  identifier to `state.<name>` when that exact coordinate is rendered with the
  expected `num`/`id`/`bool` kind.
- `delta_term()` now accepts rendered numeric scalar state coordinates, enabling
  source-prioritized exact delta rows such as `post - pre == state.seed`.
- This composes with the previous arithmetic endpoint rule, so one-level terms
  like `(state.seed + amount)` are prioritized without extending the structured
  R2 grammar.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `157 test(s) ran, 157 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 type-conversion-wrapped source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Real Solidity frequently wraps values in explicit type conversions before
  storing them, e.g. `small = uint128(amount)`, `total += uint256(amount)`,
  `owner = address(who)`, `paid = uint256(msg.value)`, and mapping-slot writes
  such as `quote[msg.sender] = uint256(amount)`.
- The source-priority miner previously saw the outer `FunctionCall` and missed
  the inner coordinate/literal, so strong R2 endpoints were displaced by broad
  mechanical candidates.
- The unwrap is conservative. Numeric conversions are peeled only when the cast
  target equals the assignment target type; this allows `uint128(amount)` into
  a `uint128` field while refusing to simplify `uint128(amount)` assigned to a
  `uint256` field. Identity/bool casts are peeled only when their endpoint
  kind matches the assignment target kind. ESBMC still owns the proof; if a
  narrowed coordinate is not defined over the certified region, the structured
  R2 row cannot hold.

Code shape:

- `scripts/solidity_path_put.py` now has a local `type_conversion_arg()` helper
  inside `source_assignment_r2_specs()`.
- `coord_term()`, `delta_term()`, `numeric_endpoint_term()`, and
  self-update-delta mining now pass the destination type when analyzing RHS
  expressions, so cast-wrapped direct endpoints, deltas, one-level arithmetic
  operands, and exact mapping-slot endpoints are mined consistently.
- Existing `address(0)` handling remains separate, so a zero-address reset is
  still recorded as `post == 0` / `address(0)` evidence rather than treated as
  a numeric coordinate.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `158 test(s) ran, 158 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 constant-identifier source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- Solidity contracts often write from named constants:
  `fee = MAX_FEE`, `total += STEP`, `ready = READY`,
  `owner = ZERO_ADDRESS`, or `bal[msg.sender] = LIMIT`.
- `source_r2_literals()` already mined numeric constants as mechanical term
  atoms, but source-priority R2 did not connect an assignment RHS identifier
  back to the constant declaration. That meant a strong endpoint/delta could be
  buried behind generic candidate ordering.
- The new rule is intentionally small: it only interprets constant declarations
  whose value is a unitless decimal numeric literal, bool literal, or exact
  `address(0)` type conversion. Complex constant expressions are not evaluated
  by the source miner.
- Fuzz remains refute-only. ESBMC still certifies every resulting row.

Code shape:

- `scripts/solidity_path_put.py` now indexes constant `VariableDeclaration`
  nodes across the target contract's linearized scope chain.
- `constant_term()` converts RHS identifiers that name compatible literal
  constants into structured R2 literal terms while preserving the constant name
  in evidence.
- Constant terms feed direct scalar endpoints, exact mapping-slot endpoints,
  and numeric delta mining through `delta_term()`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `159 test(s) ran, 159 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 mapping-literal-key source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- The exact mapping-slot R2 miner could name slots keyed by unit parameters and
  `msg.sender`, but not source-literal keys such as `count[7]`,
  `flagged[true]`, `owners[address(1)]`, or address hex literals.
- ESBMC and the PUT emitter already understand slot names whose key text is a
  decimal or hex literal. Reusing that existing slot-name grammar lets the
  source miner ask strong `post == amount` / exact delta rows for literal-keyed
  stores without another verifier pass.
- The new source key support is intentionally narrower than Solidity's full key
  grammar. It emits uint/int numeric literals, bool literals as `1`/`0`, and
  address numeric/hex literals. It does not emit `bytesN` literal keys because
  the emitter's literal slot-key path casts literals as `uint256(...)`; that is
  safe for uint/address/bool numeric ABI padding, but can be wrong for bytesN
  left-aligned ABI encoding.
- As usual, this only prioritizes candidates. Fuzz can refute; ESBMC certifies.

Code shape:

- `scripts/solidity_path_put.py` `key_name()` now recognizes safe literal
  mapping keys by expected solc key type.
- Address keys accept unitless numeric literals, `address(<unitless number>)`,
  `address(0)`, and compact hex string literals up to 160 bits.
- Bool keys render as `1`/`0`, matching the existing literal key grammar.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `160 test(s) ran, 160 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 mapping-constant-key source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Reasoning:

- After literal-key slot mining, a common adjacent source shape was still
  missed: `m[KEY] = amount`, where `KEY` is a contract constant. Real contracts
  often use constants for sentinel buckets, default IDs, or fixed owner/flag
  keys.
- The source miner should not hand the constant name to the emitter because the
  slot-name grammar is about verifier/PUT coordinate text, not Solidity lexical
  scope. Instead, it folds simple safe constant values to the same literal key
  text already supported by `slot_key_expr()`.
- Supported constant keys are deliberately narrow: uint/int numeric literals,
  bool literals as `1`/`0`, and address constants expressed as numeric or hex
  literals, including `address(<literal>)`.
- `bytesN` constant keys remain refused for the same reason as raw bytesN
  literal keys: the emitter's literal-key path casts as `uint256(...)`, while
  bytesN ABI encoding has different alignment semantics. Complex constant
  expressions also remain uninterpreted.
- Fuzz remains refute-only. ESBMC still certifies any resulting slot R2 row.

Code shape:

- `scripts/solidity_path_put.py` now has `address_literal_key()` and
  `constant_key_name()` inside `source_assignment_r2_specs()`.
- `key_name()` checks `constant_key_name()` before direct literal matching, so
  nested mapping keys can mix parameters, `msg.sender`, safe literal keys, and
  safe constant-folded keys through the existing slot walker.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: `161 test(s) ran, 161 declared in this module`.
- `git diff --check` on the touched files passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 named-return source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- Solidity functions commonly use named return parameters:
  `returns (uint256 out) { out = amount * 2; }`.
- The previous source-return R2 rule recognized explicit `return expr`, but
  not direct assignments to the single named return variable. That left these
  getter/pure units dependent on mechanical typed-R2 ordering even though the
  source body already identifies the strongest return equality.
- This can be fixed without another verifier pass because return candidates
  already share the typed Stage-4 R2 batch.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now records the
  declaration IDs of a single named return parameter when its return type is an
  R2 endpoint type.
- A direct `=` assignment to that return parameter is translated to a structured
  `return == term` source candidate using the same conservative return-term
  grammar as explicit `return expr`.
- Compound assignments to named return parameters are still skipped. Supporting
  them would require local variable lifecycle analysis, which is outside this
  source-priority pass.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  150 tests ran, 150 declared.
- `git diff --check` on the touched files: passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 source return R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- Pure/view and getter-shaped units often expose their strongest oracle in the
  return expression itself, e.g. `return amount + 7`, `return ok`, or
  `return true`.
- Typed R2 can mechanically generate many return candidates, but the source
  expression is a strong priority signal. Without using it, a precise return
  equality can be displaced by unrelated mechanical terms under the global
  candidate budget.
- Return candidates can still share the existing Stage-4 typed R2 verifier
  query. They do not need a separate ESBMC pass, and a candidate remains only a
  question until `--path-cov-assert` proves it.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now also
  recognizes single-value `Return` nodes when the declared return type is an
  R2 endpoint type.
- Supported source return terms are deliberately narrow:
  - rendered numeric/id/bool parameters;
  - unitless decimal numeric literals;
  - bool literals;
  - one-level numeric `+`, `-`, `*`, and division by a nonzero literal.
- Multi-return whole-value source candidates are skipped. Existing member
  return rendering remains the authority for tuple returns.
- State-coordinate return expressions are not newly inferred here. That avoids
  inventing pre-state reads outside the existing rendered-coordinate pipeline.
- Main Stage-4 rendered-coordinate construction now preserves bool parameters
  as `kind == "bool"` instead of folding every non-address lifted parameter
  into `num`; this lets bool source return candidates and bool R2 equality use
  the existing bool path.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  150 tests ran, 150 declared.
- `git diff --check` on the touched files: passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 source self-update delta R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- The typed Stage-4 R2 grammar can mechanically ask strong accumulator claims
  such as `post == pre + amount` and `post - pre == amount`.
- But the source-prioritized path only handled direct endpoint setters. For
  source shapes like `balance += amount`, `limit -= x`, or
  `state = state + 7`, the strongest delta oracle depended on mechanical term
  ordering and candidate budget instead of being placed first by the unit's own
  syntax.
- This is a general corpus pattern, not a POC-specific one: update/withdraw/
  accounting functions often expose their semantic oracle as a self-update
  rather than a plain setter.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now recognizes
  simple unsigned self-updates:
  - `state += renderedUintParam` and `state += unitlessLiteral` as an `inc`
    delta candidate.
  - `state -= renderedUintParam` and `state -= unitlessLiteral` as a `dec`
    delta candidate.
  - `state = state + term`, `state = term + state`, and `state = state - term`
    for the same restricted term forms.
- Source delta candidates are structured `deltas` entries with `lo == hi`, so
  they still go through `--path-cov-assert` and are not treated as proof by the
  Python side.
- The rule is deliberately conservative: the target state must be a visible
  `uint*` storage variable, parameter terms must be rendered unsigned numeric
  coordinates, and subdenominated literals such as `2 seconds` are skipped.
- Source candidates are now grouped by variable and `candidate_count` counts
  equals/abs/deltas rather than variables.
- `merge_source_r2_specs()` now handles source `equals`, `abs`, and `deltas`,
  de-duplicates them against the typed batch, and still keeps them in the same
  single R2 verifier query.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  149 tests ran, 149 declared.
- `git diff --check` on the touched files: passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 numeric-literal source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- `source_r2_literals()` already mines integer literals globally, and the typed
  mechanical R2 batch can ask literal endpoint candidates. However, that path
  does not know which state variable was assigned from the literal in the
  target function body.
- Under a finite `--r2-candidate-budget`, the strongest literal setter oracle
  for `uintState = 7` can be displaced by mechanical candidates for other
  variables or unrelated literals.
- The source-assignment R2 path is a better priority signal: it is still proved
  by ESBMC, but it asks the endpoint that the target unit itself writes first.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now recognizes
  direct assignments from a unitless Solidity numeric literal to a visible
  `uint*` state variable and emits a structured decimal literal term such as
  `{"kind": "literal", "value": "7"}`.
- Literal source candidates continue to merge into the same typed Stage-4 R2
  batch, so this does not add an extra ESBMC R2 pass.
- Subdenominated literals such as `2 seconds` are deliberately skipped. Treating
  them as plain decimal atoms at this layer would erase Solidity unit semantics
  before the verifier sees the actual lowered expression.
- This is not POC-specific: the rule is gated by the AST assignment shape, the
  normalized state-variable type, and the real storage layout.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  148 tests ran, 148 declared.
- `git diff --check` on the touched files: passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 source R2 merge keeps mechanical candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- After source-assignment R2 was added, Stage 4 used the source spec as a
  replacement: if a direct setter candidate existed, the typed mechanical R2
  batch was skipped.
- That makes the setter oracle cheaper, but it can silently lose other strong
  candidates on the same path: return-value R2, delta terms, literal/range
  terms, and candidates for other state variables.
- Running source and typed as two specs would recover strength but cost another
  ESBMC R2 invocation per path. The better shape is one verifier query whose
  `vars` carries both candidate families.

Code shape:

- `scripts/solidity_path_put.py` now has `merge_source_r2_specs()`.
- Stage 4 now always builds the typed mechanical R2 batch, then merges any
  source-assignment candidates into that same batch.
- Source candidates are inserted ahead of mechanical candidates for their
  variable, de-duplicated by rendered structured term, and the merged spec is
  marked `kind = typed+source-assign`.
- The `--r2-candidate-budget` cap is preserved: if the source candidates would
  push the merged batch over budget, only mechanical tail candidates are removed
  and logged as NOT ASKED. Source candidates keep priority because they come
  from the unit's own assignment syntax and are typically the strongest setter
  oracle.
- This improves PUT strength without increasing the number of ESBMC R2 passes
  for a path.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  148 tests ran, 148 declared.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 bool-literal source R2 candidates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- Stage-4 R2 already proposed strong endpoint candidates such as
  `post == amount` for direct setter assignments and `post == flag_` when the
  PUT lifted a bool parameter.
- It did not propose the equally common source shape `boolState = true/false`
  unless a bool coordinate happened to be rendered. For benchmark contracts this
  loses a strong mutation-sensitive oracle: a path that sets a pause/enable/
  initialized flag should be able to certify `post == true` or `post == false`,
  not only the weaker R1 equality/changed pair.
- Fuzz remains refute-only in this path. The new candidate can be removed by a
  concrete Forge failure, but a Forge pass is still not proof; surviving
  candidates remain ESBMC-certified by `--path-cov-assert`.

Code shape:

- `scripts/solidity_path_put.py` `source_assignment_r2_specs()` now indexes
  state variable type strings and recognizes AST assignments from a source-level
  bool literal to a visible bool state variable.
- The generated structured R2 term stays verifier-native decimal literal
  `{"kind": "literal", "value": "1"}` or `"0"`; this matches ESBMC's structured
  term parser, which accepts unsigned decimal literals and prints bool terms as
  `true`/`false` when the candidate type is bool.
- `r2_terms_from_specs()` now records aliases `true -> 1` and `false -> 0` for
  literal terms. This lets the final Foundry renderer consume ESBMC rows such as
  `post == true` even though the original spec used decimal `1`.
- This is not a POC-specific rule: it is gated by the AST state-variable type,
  the real storage layout, and the ordinary Stage-4 source assignment pathway.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py` passed:
  146 tests ran, 146 declared.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 direct CLI protected write guards

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, benchmark AST preheat, or
  benchmark certification run was started.

Finding:

- Runner-level guards protect old schedules at execution time, but several
  direct CLI entry points could still be invoked by hand and write under
  Dataset/Results:
  - `ast_preheat_schedule.py --out`, and generated `--ast-cache-root`.
  - `unit_schedule.py --out`, generated `certify_all.py --out`, and generated
    `--ast-cache-root`.
  - `subject_unit_manifest.py --out` and `--journal`.
  - `certify_all.py --out`, `--workdir`, and `--ast-cache-root`.
- `certify_all.py --ast-cache-root` is treated as a write path because direct
  prepared-subject runs may generate missing compact ASTs through that cache.

Code shape:

- Reused `veriput_path_guard.ensure_path_not_protected` in the direct CLI
  planners/runners above.
- `certify_all.py` now rejects protected `--out`, `--workdir`, and
  `--ast-cache-root` immediately after `argparse`, before subject resolution,
  AST generation, workdir creation, or JSONL append.
- `unit_schedule_run.py` now also rejects protected `certify_argv
  --ast-cache-root`, not only `--out`, so malformed schedules fail in dry-run
  before starting a child process.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/ast_preheat_schedule.py notes/coverage/scripts/unit_schedule.py notes/coverage/scripts/unit_schedule_run.py notes/coverage/scripts/subject_unit_manifest.py notes/coverage/scripts/certify_all.py scripts/test_ast_preheat_schedule.py scripts/test_unit_schedule.py scripts/test_unit_schedule_run.py scripts/test_veriput_subjects.py scripts/test_certify_all_guards.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_ast_preheat_schedule.py`
  passed and now checks protected schedule output and AST cache refusal.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed and
  now checks protected schedule output, cert output, and AST cache refusal.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule_run.py` passed
  and now checks protected `certify_argv --ast-cache-root` refusal.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_veriput_subjects.py` passed
  and now checks protected manifest `--out` and `--journal` refusal using a
  temporary `VERIPUT_ROOT`.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_guards.py`
  passed and checks `certify_all.py` refuses protected `--out`, `--workdir`,
  and `--ast-cache-root` before subject resolution.
- Related tests also passed:
  `scripts/test_benchmark_pipeline_plan.py` and
  `scripts/test_put_all_accounting.py`.
- `git diff --check` passed before this note update.
- `python3 -m pylint ...` was run on touched scripts/tests. It exits 28 on
  existing style debt: import-position after local `sys.path` setup, missing
  docstrings, unspecified encodings, subprocess calls without `check`,
  complexity/duplicate-code warnings, and existing large-function warnings in
  `certify_all.py`.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 runner-level protected write guards

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started.

Finding:

- The planner and `subject_unit_manifest.py` now protect the normal AST/cache
  entry points, but direct runner execution could still bypass the planner with
  an old or hand-written schedule. The unsafe write targets were:
  - `ast_preheat_run.py`: generated AST cache path inside each `preheat_argv`,
    and the real-run `--journal`.
  - `unit_schedule_run.py`: `certify_all.py --out` inside each `certify_argv`,
    and the real-run `--journal`.
  - `put_all.py`: Stage-4 `--out-root`, which creates Forge projects and PUT
    artefacts.

Code shape:

- Added `notes/coverage/scripts/veriput_path_guard.py` with shared helpers for:
  protected `<VERIPUT_ROOT>/Datasets` and `<VERIPUT_ROOT>/Results` detection,
  fail-closed planned-write validation, and robust `--flag value` /
  `--flag=value` argv parsing.
- `ast_preheat_run.py` now refuses schedules whose `--ast-cache-root` points
  under Dataset/Results, including dry-run validation, and refuses real journals
  under Dataset/Results before any journal read/write.
- `unit_schedule_run.py` now refuses schedules whose `certify_argv --out`
  points under Dataset/Results, refuses real journals under Dataset/Results,
  and rejects negative `--memlimit-gb`.
- `put_all.py` now refuses `--out-root` under Dataset/Results before
  `os.makedirs(OUT)`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/veriput_path_guard.py notes/coverage/scripts/ast_preheat_run.py notes/coverage/scripts/unit_schedule_run.py notes/coverage/scripts/put_all.py scripts/test_ast_preheat_run.py scripts/test_unit_schedule_run.py scripts/test_put_all_accounting.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_ast_preheat_run.py` passed
  and now checks protected AST cache and preheat journal refusal.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule_run.py` passed
  and now checks protected certification output, unit journal, and negative
  memlimit refusal.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_put_all_accounting.py` passed
  and now checks protected Stage-4 `--out-root` refusal before output creation.
- Related existing tests also passed:
  `scripts/test_benchmark_pipeline_plan.py`,
  `scripts/test_veriput_subjects.py`,
  `scripts/test_ast_preheat_schedule.py`, and `scripts/test_unit_schedule.py`.
- `git diff --check` passed before this note update.
- `python3 -m pylint ...` was run on the touched scripts/tests. It exits 28 on
  existing style debt: import-position after `sys.path` setup, missing
  docstrings in old scripts/tests, unspecified encodings, subprocess calls
  without `check`, complexity/duplicate-code warnings, and an existing unused
  variable in `put_all.py`. The new helper's own missing-docstring warnings were
  fixed afterward.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 modifier suffix source-R2

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Ground truth:

- Prepared benchmark sources frequently use modifiers whose post-body suffix
  writes meaningful state, especially OpenZeppelin-style `nonReentrant` shapes:
  `_status = _ENTERED; _; _status = _NOT_ENTERED;`.
- The final observable post-state after a successful unit call is determined by
  statements after the modifier placeholder, not by setup writes before `_`.
  Mining pre-placeholder writes as ordinary final-state candidates would create
  likely false source-R2 guesses such as `post == _ENTERED`.
- Fuzz remains only a cheap refutation pass. These source-R2 candidates are
  still hypotheses; ESBMC certification is the proving step.

Code shape:

- `scripts/solidity_path_put.py` now indexes `ModifierDefinition` nodes in the
  same contract/base scope used for target function discovery.
- After walking the target function body, source-R2 walks each target modifier's
  top-level suffix after `PlaceholderStatement` and mines assignments from
  that suffix.
- If a modifier has parameters, invocation actuals are bound through the same
  local-alias mechanism used by one-level internal helper inlining.
- Alias/local/storage alias snapshots are restored after each modifier walk, so
  modifier parameters and locals do not pollute the function body or following
  modifiers.
- Modifier references accept both `Identifier` references and direct
  `referencedDeclaration` fields, covering minor solc AST shape variation.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 183/183 tests.
- `git diff --check -- scripts/solidity_path_put.py scripts/test_solidity_path_put.py notes/VeriPUT_handoff_memory.md`
  passed.
- Dataset mtime remained `2026-08-05 01:39:14.979712680 +0800`.
- Results mtime remained `2026-08-05 08:10:46.032908697 +0800`.

## 2026-08-06 conditional return source-R2 leaves

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Ground truth:

- Existing `P19_ReturnShapes.*` weak PUT artefacts are stale but diagnostic:
  they are fuzzed pure-function paths with no state dependencies and no oracle,
  because the assertion ladder reported no state-variable candidate.
- The expected strong oracle is a return-value assertion. For path-specific
  regions, candidates such as `return == 10`, `return == 20`, or boolean
  `return == true/false` are cheap hypotheses; fuzz may refute the wrong side
  and ESBMC must certify any survivor.

Code shape:

- `source_assignment_r2_specs()` now unwraps single-component
  `TupleExpression` return nodes, which matches solc's parenthesized nested
  conditional AST shape.
- Numeric `Conditional` return expressions enumerate candidate terms from both
  branches, recursively, rather than failing the whole return expression.
- Nontrivial bool return expressions with boolean AST type, such as
  short-circuit `&&` and comparisons over helper calls, propose both literal
  endpoints `false` and `true`.
- This does not make the proposer path-aware and does not count either literal
  as proof. It only makes the candidate set expressive enough for the existing
  Forge refuter and ESBMC certification pass.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 184/184 tests.
- Read-only real-AST smoke over `notes/coverage/poc/P19_ReturnShapes.solast`
  now reports source-R2 candidates:
  `tern_lit -> return == 10/20`,
  `sc_lit -> return == 0/1`,
  `tern_call -> return == 3/4/1/2`,
  `tern_nested -> return == 1/2/3`, and
  `cond_call -> return == 0/1`.

## 2026-08-06 POC-local PUT inventory roots

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- `notes/coverage/scripts/poc_ground_truth.py` defaulted to the old
  `notes/coverage/put_roundtrip/_wd` root only.
- The official `poc_one.py` workflow writes current POC-local PUTs under
  `notes/coverage/poc_units/<poc-id>/put_<cell>/...`.
- As a result, the cheap ground-truth inventory could report stale weak
  `put_roundtrip` rows while missing newer official POC-local strong rows.

Code shape:

- The inventory now uses default PUT roots:
  `notes/coverage/put_roundtrip/_wd` plus every existing
  `notes/coverage/poc_units/*/put_*` directory.
- Passing `--put-root <path>` remains a single-root override for focused
  debugging or tests.
- The JSON output records both the legacy-compatible `inputs.put_root` when
  exactly one root is used and `inputs.put_roots` for the full list.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/poc_ground_truth.py scripts/test_poc_ground_truth.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_poc_ground_truth.py`
  passed.
- Read-only default inventory smoke now reports 13 PUT roots, 249 `put.json`
  rows, and 197 strong-shape PUT rows. This is still an artefact inventory, not
  a new ESBMC/Forge measurement.

## 2026-08-06 POC-local certification inventory roots

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- The read-only ground-truth inventory still undercounted current official POC
  progress after adding POC-local PUT roots.
- Default certification scanning included global `notes/coverage/certify/*.jsonl`
  and nested `poc_units/*/*/certify_gate.jsonl`, but missed the common official
  one-level layout `notes/coverage/poc_units/<poc-id>/certify_gate.jsonl`.
- This made POC-local PUTs appear as `no-certification-row`, e.g.
  `FarmingPool.setDistributor`, even though its local certification row existed.

Code shape:

- `notes/coverage/scripts/poc_ground_truth.py` now includes
  `poc_units/*/certify_gate.jsonl` in default cert discovery.
- PUT rows and certification summaries preserve `path_function` for audit.
- Inventory grouping remains by derived `(contract, unit)`: if a cert row lacks
  `contract`, `contract_from_cert_row()` derives the harness contract from
  `path_function` such as `sol:@C@FarmingPool@F@setDistributor#5926`. Full
  `path_function` is not used as the primary grouping key because older cert
  rows may not have it, and exact strings can split otherwise equivalent
  function-level artefacts.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/poc_ground_truth.py scripts/test_poc_ground_truth.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_poc_ground_truth.py`
  passed.
- Read-only default inventory smoke now reports 178 unit rows, 376 cert rows,
  249 PUT rows, 197 strong-shape PUT rows, and unit statuses:
  `ready-strong=61`, `partial-strong-put=5`, `no-strong-put=22`,
  `no-certified-paths=81`, `certified-no-put=6`,
  `no-certification-row=3`.
- Example sanity checks: `FarmingPool.setDistributor` is now `ready-strong`
  with 5 certified paths and 24 strong-shape PUTs among 25 PUT artefacts;
  `St1inch.setFeeReceiver` is now `partial-strong-put` with 5 certified paths
  and 4 strong-shape PUTs.

## 2026-08-06 Rendered fuzz width in PUT inventory

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No solc, Forge, fuzzing, ESBMC, POC attempt, or benchmark certification run
  was started for this change.

Finding:

- The inventory's old `strong_shape` gate used `wide_region`: any certified
  coordinate with `hi > lo`.
- That overcounted state-only width, because wide `state.*` bounds are
  deliberately not rendered as fuzz inputs when entry state is not havoced.
- It also undercounted implicit full-domain calldata fuzzing. Example:
  `St1inch.transfer` enc=3 has region `msg.value == 0`, but the emitted replay
  omitted two calldata args, so Stage 4 lifted `arg0,arg1` over their full
  domains and emitted a real parameterized test with an exit-kind oracle.

Code shape:

- `scripts/solidity_path_put.py` now records `stats.rendered_width` and
  `stats.wide_fuzz_coords` in future `put.json` files.
- `notes/coverage/scripts/poc_ground_truth.py` now uses rendered fuzz width for
  `strong_shape`: prefer `stats.wide_fuzz_coords`; otherwise infer from
  `stats.lifted` and `region`; for older schema rows that have `fuzz_params`
  but no `lifted`, fall back to legacy region-width inference.
- Full `path_function` remains audit metadata, not the grouping key.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py notes/coverage/scripts/poc_ground_truth.py scripts/test_poc_ground_truth.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 184/184 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_poc_ground_truth.py`
  passed.
- Read-only default inventory smoke under rendered-width semantics reports
  178 unit rows, 376 cert rows, 249 PUT rows, 157 strong-shape PUT rows, and
  unit statuses: `ready-strong=41`, `partial-strong-put=23`,
  `no-strong-put=24`, `no-certified-paths=81`, `certified-no-put=6`,
  `no-certification-row=3`.
- Sanity checks: `St1inch.transfer` is now correctly `ready-strong` with both
  certified paths covered by strong PUTs; `St1inch.setDefaultFarm` remains
  `partial-strong-put` because its missing enc=15 row is point-only under the
  current definition and current `build_put()` refuses it as not parameterized.

## 2026-08-06 Fixed bytes calldata as PUT fuzz coordinates

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- ESBMC's Solidity frontend had already repaired the internal `bytesN`
  calldata model: fixed bytes parameters keep a recoverable nondet payload and
  get a function-entry assume pinning `.length == N`.
- The external PUT emitter still only lifted `bool`, `address`, and `uintN`
  parameters. A certified region over `bytes4 key` or an omitted replay arg of
  type `bytes4` therefore could not become a real fuzz coordinate.
- The right boundary is semantic, not syntactic: fixed bytes may name absolute
  equality endpoints (`post == key`) and same-width mapping keys, but must not
  be used as arithmetic deltas (`post - pre == key`).

Code shape:

- `scripts/solidity_path_put.py` now recognizes `bytes1` ... `bytes32` via
  `fixed_bytes_width()`.
- A lifted fixed-bytes parameter is rendered in the PUT signature as a
  same-width unsigned integer (`bytes4` -> `uint32`) so the existing numeric
  `bound()` logic applies. The unit call casts it back at the ABI boundary:
  `c.take(bytes4(key_))`.
- Omitted fixed-bytes calldata is synthesized as full-domain fuzz input, and
  low-level ABI signatures can now spell omitted `bytesN` arguments.
- R2 endpoint classification treats fixed bytes as width-filtered identity
  endpoints. This asks `post == key_` only for same-width readable candidates,
  and keeps fixed bytes out of the delta-coordinate table.
- Source-assignment R2 mining now classifies fixed bytes as identity-shaped, so
  direct setters or mapping writes involving `bytesN` parameters can request
  the strongest equality rows.
- Follow-up: fixed-bytes return-value assertions now cast through the matching
  unsigned width before the final `uint256` cast. Example: `bytes4` returns
  render as `uint256(uint32(_put_ret))`; `bytes32` keeps `uint256(_put_ret)`.
  This avoids generating Solidity that fails to compile on narrower fixed
  bytes return oracles.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 189/189 tests after the return-cast follow-up.
- Added coverage for:
  - explicit `bytes4` region -> `uint32` PUT parameter -> `bytes4(...)` call;
  - omitted `bytes4` replay calldata -> full-domain fuzz input;
  - `mapping(bytes32 => ...)` slot proposal from a same-typed parameter;
  - fixed-bytes R2 identity endpoint width filtering;
  - fixed-bytes return assertions using same-width integer casts.

## 2026-08-06 Address payable PUT call boundary

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- `address payable` parameters were already treated as fuzzable address-width
  coordinates, which is right for `bound()` and the PUT function signature.
- The target high-level call still received the raw `address` fuzz variable.
  Solidity can reject passing an ordinary `address` expression to an
  `address payable` parameter, so these PUTs may fail at compile time despite
  having a valid certified region.

Code shape:

- `scripts/solidity_path_put.py` now routes lifted call arguments through
  `call_arg_expr()`.
- Ordinary address parameters still call with `arg`.
- `address payable` parameters keep the PUT signature as `address arg`, keep
  address-domain `bound()` lines, and call the unit as `payable(arg)`.
- Low-level ABI signatures remain `address`, which is Solidity ABI-correct.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 190/190 tests.
- Added coverage for omitted high-level `address payable` calldata repaired as
  full-domain address fuzz input and cast to `payable(arg0)` at the unit call.

## 2026-08-06 Observable msg.sender absolute R2 endpoint

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- Source/typed R2 can produce high-value rows such as
  `owner: post == msg.sender`.
- The renderer only put `msg.sender` into the absolute endpoint spelling table
  when `establish_env_sender()` added a fuzz parameter. That dropped scalar
  equality rows for ordinary observed pranks, and also for width-one sender
  regions where the prank was rewritten to a constant but no fuzz parameter was
  added.
- This is separate from mapping slot keys. `bal[msg.sender]` must still be
  refused unless the PUT itself established the sender, because otherwise the
  oracle can read a slot the unit never touched.

Code shape:

- `scripts/solidity_path_put.py` now has
  `observable_sender_expr_for_abs_r2()`.
- For absolute scalar R2 endpoints only, the sender expression is:
  - the established fuzz/constant sender when `establish_env_sender()` ran;
  - `address(this)` when no prank governs the call;
  - a canonical decimal literal when the governing `vm.prank(...)` argument is
    readable as a literal address.
- Complex non-literal prank arguments remain fail-closed for this endpoint.
- `key_expr_of["msg.sender"]` is unchanged and remains gated by
  `env_sender_expr is not None`, preserving the mapping-key refusal boundary.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 191/191 tests.
- `git diff --check` passed.

## 2026-08-06 Helper/modifier actuals for source mapping slots

Scope:

- This is another source-resolved slot priority improvement for the external
  VeriPUT generator. It is not POC-specific and does not special-case any
  dataset contract.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `unit_mapping_slot_accesses` now carries a small source alias environment
  across helper-function calls and modifier invocations. Formal parameters in
  the callee are substituted with the caller actuals before naming mapping
  slots.
- The traversal key is now `(callable id, alias fingerprint)` rather than just
  callable id, so the same helper can contribute different slots for different
  callers or different actual arguments.
- Local declaration aliases are preserved in source order and passed through
  helper calls, while assignments invalidate stale aliases. This lets cases
  such as `address sender = msg.sender; touchOne(sender)` still resolve to
  `bal[msg.sender]`.
- Callable edges are only taken from real call/modifier invocation nodes. Bare
  child identifiers that reference a function/modifier no longer enqueue a
  duplicate no-argument visit that would leak unresolved formals like `who` or
  `auth` into the slot set.

Why it matters:

- Earlier source slot priority worked well for direct accesses in the target
  function, but missed or weakened many real Solidity patterns where a public
  unit delegates storage reads/writes through small helpers or modifier guards.
  This change strengthens R1/R2 candidates before any expensive ESBMC proof
  attempt by asking for semantic slots like `bal[to]`, `bal[state.owner]`, or
  `bal[msg.sender]` instead of falling back to broad guessed mapping keys.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 204/204 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 `block.coinbase` Stage-2 address domain

Scope:

- This is a small external VeriPUT stage-2 generalisation fix following the
  modeled environment cheatcode work.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Finding:

- The PUT emitter can now establish and fuzz `block.coinbase` with
  `vm.coinbase(address)`, but stage 2 still gave unknown environment
  coordinates the default `uint256` search domain.
- That was wrong for `block.coinbase`: it is address-like, so the geometric
  bracket and certified region should live in `[0, 2^160-1]`, matching
  `msg.sender` and `tx.origin`.

Code change:

- `_coord_range()` now treats `block.coinbase` as an address-domain
  environment coordinate.
- Added a pure Python regression locking `msg.sender`, `tx.origin`, and
  `block.coinbase` to 160-bit ranges while keeping numeric modeled environment
  coordinates such as `block.basefee` and `tx.gasprice` at 256-bit ranges.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Disagreed modeled environment region promotion

Scope:

- This is an external VeriPUT stage-2 driver improvement. It connects the
  generalized PUT emitter environment-setter table to the automatic
  `--env-coord-disagreed` region policy.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- Extracted `derive_env_coord_disagreed(paths, env_names, pins)` from the
  inline `main()` loop so the policy is directly testable without running
  ESBMC.
- `--env-coord-disagreed` now clearly uses `ESTABLISHABLE_ENV_COORDS` imported
  from the PUT emitter instead of a stale help-text notion that only mentioned
  `msg.sender` and `msg.value`.
- The policy promotes only PUT-establishable environment quantities on which
  witnessed paths disagree. Pinned quantities stay pinned, path-agreed
  quantities stay candidates for `--pin-env`, and unestablishable quantities
  such as `tx.origin` / `block.gaslimit` remain refused rather than guessed.
- Added a pure Python regression covering modeled positive cases
  `block.basefee`, `block.prevrandao`, `tx.gasprice`, and `block.coinbase`,
  plus agreed and unestablishable negative controls.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 211/211 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Modeled environment cheatcode PUT establishment

Scope:

- This is an external VeriPUT generator improvement. It generalizes the
  previous `block.timestamp` / `block.number` / `block.chainid` handling to the
  rest of the Foundry environment setters that ESBMC's Solidity frontend
  already models.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Rationale:

- Read-only ESBMC inspection showed modeled cheatcodes for `vm.warp`,
  `vm.roll`, `vm.chainId`, `vm.fee`, `vm.prevrandao`, `vm.txGasPrice`, and
  `vm.coinbase`.
- The PUT generator may therefore establish/fuzz these coordinates because the
  emitted test and ESBMC agree on their semantics before the target call.
- This deliberately does NOT open `tx.origin`, `block.difficulty`, or
  `block.gaslimit`: there is no equivalent reliable PUT-side setter in the
  currently used model, and ESBMC explicitly leaves `vm.difficulty` unmodeled
  after Paris/prevrandao semantics.

Code change:

- Added shared metadata tables for numeric modeled environment setters:
  `block.timestamp`, `block.number`, `block.chainid`, `block.basefee`,
  `block.prevrandao`, and `tx.gasprice`.
- Added address environment support for `block.coinbase` via `vm.coinbase`.
  Wide regions become an `address p_block_coinbase` fuzz parameter; holes are
  excluded with `vm.assume(uint256(uint160(...)) != hole)`.
- Source-R2 mining now recognizes assignments and deltas involving
  `block.basefee`, `block.prevrandao`, `tx.gasprice`, and `block.coinbase`
  when those coordinates are rendered by the PUT.
- Source-resolved mapping slots accept modeled numeric environment keys for
  unsigned integer mapping levels and accept `block.coinbase` for address
  mapping levels. Incompatible key types remain refused before fallback guesses.
- Observed replay preambles using literal modeled cheatcodes can name those
  environment values in R2 endpoints and mapping-slot keys without adding a new
  fuzz parameter.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/solidity_path_generalise.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 211/211 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 struct-local member source slot aliases

Scope:

- This is a source-resolved mapping slot improvement in the external VeriPUT
  generator. It is not POC-specific and does not alter ESBMC internals.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `_expr_coord_name` now resolves `MemberAccess` bases recursively instead of
  only accepting a direct identifier. A local alias such as
  `Config storage c = cfg; bal[c.owner]` therefore names the same source slot
  key as `bal[cfg.owner]`: `state.cfg.owner`.
- Direct state-member keys (`state.cfg.owner`) and environment members
  (`msg.sender`, `block.timestamp`, etc.) continue to use the same spelling as
  before; they are just routed through the common recursive path.

Why it matters:

- Solidity units often alias a storage struct or config object into a local
  variable before indexing mappings with one of its members. Without recursive
  member resolution, the source slot pass sees `c.owner`, cannot render it as a
  layout-backed entry-state key, and falls back to weaker guessed slots.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 207/207 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 assignment-updated source slot aliases

Scope:

- This is a source-resolved mapping slot improvement in the external VeriPUT
  generator. It is not POC-specific and does not alter ESBMC internals.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `unit_mapping_slot_accesses` now tracks simple straight-line assignments to
  local identifiers when the operator is `=` and the right-hand side can be
  resolved to a safe source coordinate. This covers common code such as
  `address who; who = msg.sender; bal[who]`.
- Alias values are stored as resolved coordinate strings, not raw AST nodes.
  That freezes the value at the assignment/initializer/call boundary, so a
  later reassignment of an intermediate local cannot silently retarget an older
  alias.
- Compound assignments and unsupported right-hand sides still invalidate or
  decline the alias rather than guessing.
- Helper/modifier formal substitution now uses the same resolved-coordinate
  snapshot, preserving the previous call-edge support while avoiding by-reference
  local alias behaviour.

Why it matters:

- Real Solidity code often declares a local key and assigns it after a guard or
  branch before indexing a mapping. Before this change, those accesses either
  leaked a raw local name such as `who` or fell back to broader guessed slots,
  weakening R1/R2 candidates.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 207/207 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 block env source mapping-slot keys

Scope:

- This extends the numeric-environment source slot work from `msg.value` to
  `block.timestamp` and `block.number` in the external VeriPUT generator.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `source_access_slot_vars` now accepts `block.timestamp` and `block.number`
  as source mapping keys for unsigned-integer mapping key types. Address-keyed
  or otherwise incompatible stores remain refused.
- `build_put` adds block environment keys to `key_expr_of` only when
  `coord_ident` already has a concrete expression. That expression can come
  from an established certified block range/point or from an observable literal
  `vm.warp` / `vm.roll` preamble in the emitted replay.
- This is not a Python-only guess: ESBMC's `resolve_coord` already accepts
  `msg.*`, `tx.*`, and `block.*` environment names and `resolve_slot_key`
  routes non-literal mapping keys through that same resolver.

Why it matters:

- Real contracts sometimes bucket or rate-limit state by block timestamp or
  block number. If source-resolved slots such as `byTime[block.timestamp]`
  are dropped, the ladder can miss the only useful frame/update oracle for
  that path even though the emitted PUT can establish or observe the exact
  block environment it runs under.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 207/207 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 `msg.value` source mapping-slot keys

Scope:

- This is a source-resolved mapping slot and PUT emitter improvement in the
  external VeriPUT generator. It is not POC-specific and does not alter ESBMC
  internals.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `source_access_slot_vars` now accepts `msg.value` as a safe source mapping
  key for unsigned-integer mapping key types. This lets source accesses such as
  `paid[msg.value]` enter the first ladder/source-slot candidate set instead of
  falling back to guessed slots.
- `build_put` now adds `key_expr_of["msg.value"]` only when the emitter already
  has a concrete expression in `coord_ident`. That expression can be a bounded
  fuzz variable for a certified low-level value-gate region, or the observed
  literal `0` for an ordinary no-value call. The emitter still does not guess
  a value.
- `block.timestamp` and `block.number` are intentionally not opened as mapping
  keys in this patch. The Python source walker can name them, but ESBMC's
  current mapping-key resolver text still advertises only literals,
  parameters, `msg.sender`, `msg.value`, and entry-state variables for slots.
  Opening block keys should be done together with an internal resolver update
  and regression, not as a Python-only candidate expansion.

Why it matters:

- Payable/value-gated Solidity units often key accounting by `msg.value`.
  Before this change, even when the path's value is established or observed,
  the generated PUT could not read/write the corresponding mapping slot, so
  useful frame and exact-update oracles were lost.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 206/206 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 FunctionCallOptions-wrapped helper source slots

Scope:

- This is a source-resolved mapping slot coverage improvement in the external
  VeriPUT generator. It is not POC-specific and does not alter ESBMC internals.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `unit_mapping_slot_accesses` now unwraps solc `FunctionCallOptions` nodes
  when resolving helper-function callees. This covers source shapes such as
  `helper{gas: ...}(arg)` and `this.helper{value: ...}(arg)`, where the outer
  `FunctionCall` carries the real arguments but its `expression` is not the
  callee identifier directly.
- The actual substitution still uses the outer call's `arguments`, so helper
  formals continue to resolve to caller-side semantic keys such as `to`.
- The change is deliberately limited to callable discovery; it does not treat
  call options themselves as region coordinates or proof evidence.

Why it matters:

- Without this unwrap, helper-internal mapping accesses behind call options
  were invisible to the source slot priority pass. Those paths would fall back
  to broader guessed mapping slots or lose mapping R1/R2 candidates entirely.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 205/205 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 State-keyed mapping ladder candidates

Scope:

- This is the follow-through to the state-variable mapping key renderer above.
  The previous change made `bal[state.owner]` renderable and source-R2-minable;
  this change makes the first assertion ladder ask those slots directly.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `propose_slot_vars` now accepts optional `state_types` and `layout` facts.
  For each mapping key level it appends safe `state.<field>` candidates after
  `msg.sender` and matching parameters.
- The production stage-2b assertion candidate path passes the AST-derived
  state types and solc storage layout into `propose_slot_vars`, so dependency
  selected mappings can produce candidates like `balances[state.owner]` in the
  first ladder pass.
- Ordering is intentional: `msg.sender` remains first for address keys,
  declared parameters remain before state keys, and the existing prefix budget
  semantics are preserved. This keeps caller/argument slots from being starved
  while adding common entry-state-key slots.
- The same conservative key policy is reused: only layout-backed, safely
  encodable state keys are proposed. bytesN state keys and unslotted variables
  are not guessed.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 198/198 tests.
- `git diff --check` passed.

## 2026-08-06 Observable msg.value numeric R2 endpoint

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- Existing POC artifacts contain many certified rows naming `msg.value`, such
  as `post == msg.value` and `post - pre in [msg.value, msg.value]`, but the
  PUT renderer only made `msg.value` nameable when it was actively fuzzed into
  a low-level `{value: p_msg_value}` call.
- For a concrete emitted call, `msg.value` is still observable: absence of a
  `{value:}` option is EVM value zero, and a literal `{value: N}` is a readable
  one-point value. Rendering that one point is weaker than fuzzing the value
  coordinate, but stronger than dropping the oracle.

Code shape:

- `scripts/solidity_path_put.py` now has
  `observable_value_expr_for_r2()`, returning a decimal literal only when the
  emitted call's value is readable through the existing `observed_env()` logic.
- If `establish_env_value()` did not create a fuzz parameter, the renderer
  inserts that observed literal into both `coord_ident` and `coord_ident_abs`.
  This makes `msg.value` usable for numeric delta endpoints and absolute
  endpoints.
- Complex value expressions remain fail-closed because `_lit_int()` returns
  `None`; the emitter still refuses rather than guessing.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 192/192 tests.
- `git diff --check` passed.

## 2026-08-06 Observable block env numeric R2 endpoint

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- Existing generated Foundry tests often contain literal `vm.warp(...)`
  preambles, especially on farming/time-gated paths.
- The renderer already made `block.timestamp` / `block.number` nameable when
  the PUT established them from a certified region or pin, but not when the
  emitted replay itself already had a literal `vm.warp` / `vm.roll`.
- Those literal cheatcodes are one-point observations, like concrete
  `msg.value`: weaker than fuzzing a coordinate, but still strong enough to
  render certified rows that name the emitted execution's block environment.

Code shape:

- `scripts/solidity_path_put.py` now has `_WARP_RE`, `_ROLL_RE`, and
  `observable_block_expr_for_r2()`.
- The helper reads the last literal `vm.warp(<n>)` or `vm.roll(<n>)` before the
  target call statement and returns `<n>`.
- If the PUT did not establish `block.timestamp` / `block.number` as a
  region/pin coordinate, those observed literals are inserted into
  `coord_ident` and `coord_ident_abs`.
- No default block value is invented when there is no cheatcode, and complex
  cheatcode arguments remain fail-closed.
- The msg.value test coverage was extended to show the same observed literal
  unlocks structured expressions such as `post == (pre + msg.value)`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 193/193 tests.
- `git diff --check` passed.

## 2026-08-06 Observable env coordinates feed R2 proposals

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- The previous renderer-side fixes made observed `msg.sender`, `msg.value`,
  `block.timestamp`, and `block.number` nameable when an already-certified R2
  row reached `build_put()`.
- The R2 proposer still only marked env coordinates as rendered when they were
  explicit region coordinates. Source-R2 mining therefore missed strong
  candidates such as `owner = msg.sender` unless the region already mentioned
  `msg.sender`, even though the emitted replay had a concrete prank and the
  renderer could spell it.

Code shape:

- `scripts/solidity_path_put.py` now has `rendered_env_coords_for_r2()`.
- R2 proposal receives env coordinates when either:
  - the coordinate is in the certified region, or
  - the current emitted replay makes it observable by the helpers added in the
    renderer-side fixes.
- Ordinary high-level calls expose observed `msg.sender` and `msg.value == 0`.
- `block.timestamp` / `block.number` are exposed only when a literal
  `vm.warp` / `vm.roll` governs the target call, or when they are explicit
  region coordinates. No default block value is guessed.
- This only changes which R2 candidates are asked. Fuzz remains refutation
  only, and ESBMC remains the proof gate for any candidate that survives.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 194/194 tests.
- `git diff --check` passed.

## 2026-08-06 Structured R2 mixed literal endpoints

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- A direct renderer probe showed that `post in [0, (pre + msg.value)]` failed
  unless `r2_terms` redundantly contained an entry for literal `0`.
- The structured high endpoint was already certified/nameable through
  `r2_terms`; the low endpoint is a decimal literal and should be self-spellable.
- This was visible in stale cached `put.json` rows as mixed intervals around
  value transfers, but the fix is generic renderer behavior rather than a
  POC-specific rewrite.

Code shape:

- `rung_assertions()` now lets structured interval endpoints fall back only for
  decimal integer literals.
- Non-literal names or expressions still require a certified structured term,
  so the renderer remains fail-closed for uncertified symbolic endpoints.
- The new unit test covers the accepted literal+structured interval and a
  refused non-literal endpoint.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 195/195 tests.
- `git diff --check` passed.

## 2026-08-06 Block env pins are established, not unchecked

Scope and constraint:

- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. Validation was Python-only.

Finding:

- Old roundtrip cache rows still showed `block.timestamp` / `block.number`
  pins under `env_unchecked`, with a stale diagnostic that the driver could
  compare only `msg.sender` and `msg.value`.
- Current code already establishes timestamp/number with Foundry's
  `vm.warp` / `vm.roll`; the missing piece was a regression test for the pin
  path and an updated comment that no longer groups those two with unsupported
  `tx.*` / other `block.*` quantities.

Code shape:

- Added a unit test showing pinned `block.timestamp` and `block.number` emit
  `vm.warp(<pin>)` / `vm.roll(<pin>)`.
- The same test checks that these pins do not add fuzz parameters and do not
  remain in `env_unchecked`.
- Updated the environment-establishment comment so future debugging does not
  reason from the obsolete "all block env is unsettable" premise.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 196/196 tests.
- `git diff --check` passed.

## 2026-08-06 Read-only benchmark and recipe resync

Scope and constraint:

- `/home/samson/workspace/VeriPUT/Datasets` was inspected read-only only.
- No `/home/samson/workspace/VeriPUT/Datasets` contract was modified.
- No `/home/samson/workspace/VeriPUT/Results` file was modified.
- No ESBMC POC attempt was consumed. No solc, Forge, fuzz, or benchmark
  certification run was launched.

Current recipe state:

- `notes/coverage/scripts/poc_one.py` is now a three-stage official POC entry:
  stage 1 pathcov, stage 2 certification, stage 3 PUT/Foundry.
- The POC attempt ladder matches the current user budget:
  attempt 1 = 60s/8 GiB, attempt 2 = 120s/8 GiB, attempt 3 = 600s/10 GiB.
- `veriput_recipe.py` currently names `STRONG_RECIPE_VERSION =
  veriput-strong/7`.
- The strong certification recipe enables one job, explicit probes/refinement,
  level0 + perturb, witness probes, probe ladder with budget, skip-bracket,
  disagreed env coords, agreed-state pins, state struct fields, and slot coords.
- The strong PUT recipe enables auto-unwind 1, typed/source R2 depth 1,
  R2 term/candidate budgets, and the one-sided Forge R2 prefilter. Forge
  prefilter evidence still only refutes; every survivor still goes to ESBMC.

Read-only Peer contract080 census:

- The Peer dataset has 182 upgraded `contracts_080/*.sol` files on disk, not
  183. This matches the user note that one peer contract is not upgraded and
  should be ignored.
- Approximate source-feature counts over those 182 files:
  - `msg.sender`: 119
  - mapping: 103
  - nested mapping: 72
  - payable/value-moving shape: 60 payable functions, 52 send/transfer uses
  - `block.timestamp` / `now`: 48
  - `msg.value`: 37
  - dynamic arrays: 57
  - modifiers: 69
  - inheritance: 71
  - SafeMath-style calls: 44
  - low-level `.call`: 7
  - `tx.origin`: 2

Interpretation:

- The real peer workload is strongly caller/value/mapping dominated, so recent
  fixes around `msg.sender`, `msg.value`, block time/number, nested mappings,
  struct mapping members, SafeMath-style source-R2, and helper/modifier mining
  are aligned with the expected benchmark, not POC overfitting.
- Dynamic arrays are common but remain a separate storage-addressing problem:
  solc reports dynamic arrays as non-`inplace`, while the current PUT R1/R2
  renderer reads scalar storage words and mapping slots. Treating dynamic array
  storage as an ordinary slot would be unsound; this should not be a quick
  renderer patch.
- Structured R2 arithmetic should not be casually changed to unchecked or
  wraparound semantics. For checked Solidity paths, path feasibility usually
  supplies definedness; for `unchecked`/wrap-return cases the right fix is
  same-query definedness or an explicitly modelled unchecked candidate class.

## 2026-08-06 State-variable mapping keys

Constraint and scope:

- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained read-only; their mtimes
  stayed unchanged.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed for this change.
- Motivation came from read-only peer contract080 shapes such as
  `balances[owner]`, `balances[treasury]`, `balances[teamAddress]`, and
  `allowed[owner][spender]`. This is a benchmark-common mapping pattern, not a
  POC-specific patch.

Code change:

- Source-R2 mining now names direct state-variable mapping keys as
  `state.<field>` when the key type is safe and the field has a readable solc
  storage-layout slot. Example: `balances[owner] += amount` proposes an R2
  candidate for `balances[state.owner]`.
- PUT rendering now reads safe state-variable keys from the entry snapshot and
  uses that expression in `keccak256(abi.encode(...))`, so pins/oracles/R2 terms
  over `bal[state.owner]` hash the same key value the contract will read.
- The accepted key types are intentionally conservative: `address`,
  contract/interface values rendered as addresses, unsigned integers, and bool.
  Signed ints, bytesN, enums, and other identities remain refused rather than
  guessed, because ABI spelling differences can otherwise create fake-green
  tests over the wrong mapping word.
- The production path reads state variable types from the solc AST and passes
  them into `build_put`. If the AST is absent/unreadable, behavior degrades to
  the old renderer instead of guessing.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 197/197 tests.
- `git diff --check` passed.

## 2026-08-06 Source-resolved mapping slot priority

Scope:

- This closes the next gap after state-keyed mapping rendering/proposal:
  `solidity_path_put.py` now uses solc-resolved source mapping accesses before
  falling back to guessed same-type cross-products.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `solidity_ast_dependencies.unit_mapping_slot_accesses` now preserves state
  keys as `state.<field>` by reading solc `referencedDeclaration`. This avoids
  confusing a bare source name with a parameter/local of the same spelling.
- Added `source_access_slot_vars` in `solidity_path_put.py`. It converts
  source-resolved accesses such as `bal[state.owner]` and
  `allow[state.owner][spender]` into assertion ladder variables only when every
  key is renderable by the PUT: `msg.sender`, same-typed unit parameters, or
  safe layout-backed entry-state variables.
- Stage-2b candidate selection order is now:
  certified-region mapping slots, source-resolved mapping slots, then fallback
  cross-product guesses. Once source access provides a concrete key chain for a
  mapping/member, that mapping/member's fallback cross-product is suppressed
  and the reason is printed.
- This should strengthen R1/R2 on real contracts where the source names a
  precise slot (`balances[owner]`, `_allowances[owner][spender]`) but a
  cross-product would otherwise spend budget on caller/parameter guesses first.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 199/199 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Local alias mapping keys in source slot priority

Scope:

- This extends source-resolved mapping slot priority to local key aliases such
  as `address sender = msg.sender; balances[sender]` and
  `address who = owner; balances[who]`.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `unit_mapping_slot_accesses` now tracks simple local aliases declared by a
  single-variable `VariableDeclarationStatement` with an initializer. Alias
  resolution reuses the same safe expression resolver as direct keys, so it can
  produce `msg.sender`, `state.owner`, literal, and constant-derived keys.
- The scan is Block-order aware: aliases become visible only after their
  declaration statement and are restored when leaving a block.
- Assignment to the local identifier invalidates the alias instead of treating
  later reads as the stale initializer. The walker does not try to interpret
  arbitrary reassignment expressions; skipped aliases fall back to the existing
  candidate policy.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 203/203 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Constant mapping keys in source slot priority

Scope:

- This extends source-resolved mapping slot priority from raw literals to
  constant identifiers used as mapping keys.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `unit_mapping_slot_accesses` now records state constant declarations and,
  when an index key is an identifier referring to a constant, tries to fold its
  literal `value` before treating it as a state variable. Safe examples:
  `m[K]` where `K = 9`, `flags[ON]` where `ON = true`, and
  `owners[A]` where `A = address(2)`.
- Complex constants such as binary expressions are not interpreted; the source
  access is omitted rather than guessed, leaving fallback candidate generation
  to the existing policy.
- bytesN constants still pass through the same conservative source slot filter
  as raw bytesN literals: they are not rendered as uint/address keys.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 202/202 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Literal mapping keys in source slot priority

Scope:

- This extends source-resolved mapping slot priority to source literal keys.
  It does not broaden PUT rendering for ambiguous key encodings.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `unit_mapping_slot_accesses` now preserves simple literal mapping keys:
  unitless numeric literals, bool literals lowered to `0`/`1`, hex-string
  literals, and `address(<literal>)` type conversions.
- `source_access_slot_vars` accepts those literal keys only when the mapping
  key type is unambiguous for the existing slot renderer: unsigned integers,
  address, and bool. This lets first-ladder candidates include source slots
  such as `count[7]`, `flagged[1]`, and `owners[1]`.
- bytesN literal keys remain refused in this path. A hex-looking bytesN key
  must not be encoded as a uint256/address literal because that can hash a
  different storage word and create fake-green assertions.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 201/201 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Struct-member state keys for source slots

Scope:

- This is a narrow follow-up to source-resolved mapping slot priority.
  `unit_mapping_slot_accesses` can now name keys such as `state.cfg.owner`;
  this change makes production PUT rendering know the type of `cfg.owner`.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `contract_state_types` now expands top-level state struct members using the
  solc AST `StructDefinition` referenced by each state variable's `typeName`.
  Example output includes both `cfg: struct C.Config` and
  `cfg.owner: address`.
- Because `build_put` already registers every `(state_types ∩ layout)` entry
  into `key_expr_of`, a source access like `balances[cfg.owner]` can now be
  rendered as `balances[state.cfg.owner]` when solc storage layout also exposes
  `cfg.owner`.
- The existing conservative key filter still applies. Struct members with
  unsafe key types such as bytesN/signed/enums are not opened merely because
  their parent struct was expanded.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 200/200 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Safe type-conversion source slot keys

Scope:

- This is an external VeriPUT generator improvement for source-resolved
  mapping slots. It is not POC-specific and does not change ESBMC internals.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Code change:

- `_expr_coord_name` now unwraps only source-level type conversions that are
  safe for storage-slot key naming: `uint256(...)`, `uint(...)`,
  `address(...)`, `address payable(...)`, `payable(...)`, and `bool(...)`.
- This lets source slot priority cover common contract patterns such as
  `paid[uint256(msg.value)]`, `height[uint256(block.number)]`, and
  `owner[payable(msg.sender)]` before falling back to weaker generic mapping
  candidates.
- Narrowing conversions such as `uint32(block.number)` are deliberately not
  unwrapped. The generated PUT cannot soundly name that as `block.number`
  because the source expression hashes the truncated key, not the full
  environment coordinate.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 208/208 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 `block.chainid` PUT establishment

Scope:

- This is an external VeriPUT generator improvement for a benchmark-common
  environment coordinate. It is not POC-specific and does not alter ESBMC
  internals.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Rationale:

- Read-only benchmark grep showed many uses of `block.chainid` in EIP-712 /
  domain-separator code.
- ESBMC already pretty-prints `block_chainid` as `block.chainid` in path
  reports, and the Solidity frontend already models Foundry `vm.chainId(x)` as
  a deterministic assignment to `block_chainid`.
- Therefore `block.chainid` has the same external PUT status as
  `block.number`: it can be established by a Foundry cheatcode before the
  target call. Coordinates without this end-to-end support, such as
  `tx.origin`, remain refused rather than guessed.

Code change:

- `ESTABLISHABLE_ENV_COORDS` now includes `block.chainid`.
- `build_put` establishes singleton pins/regions with `vm.chainId(k)` and
  wide certified regions with a bounded fuzz parameter
  `p_block_chainid`, preserving punched holes with `vm.assume`.
- Source-R2 mining may now propose numeric endpoints and deltas involving
  `block.chainid` when the PUT can render that coordinate.
- Source-resolved mapping-slot priority accepts `block.chainid` as a numeric
  environment key for unsigned-integer mapping keys, and still refuses
  incompatible key types such as address.
- Observed replay preambles containing a literal `vm.chainId(...)` can feed R2
  endpoints and mapping-slot keys without adding a new fuzz parameter.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/solidity_path_generalise.py scripts/test_solidity_path_put.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 209/209 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Latest handoff pointers

The newest env-related sections were inserted earlier in this file because the
verification footer text repeats across entries. Use these headings with `rg`
after compaction:

- `2026-08-06 Modeled environment cheatcode PUT establishment`: PUT emitter
  support for `block.basefee`, `block.prevrandao`, `tx.gasprice`, and
  `block.coinbase`, building on timestamp/number/chainid.
- `2026-08-06 Disagreed modeled environment region promotion`: stage-2
  `--env-coord-disagreed` now promotes only PUT-establishable environment
  disagreements using the emitter's shared metadata.
- `2026-08-06 block.coinbase Stage-2 address domain`: stage-2 now treats
  `block.coinbase` as a 160-bit address coordinate rather than defaulting to a
  256-bit numeric search domain.

## 2026-08-06 nonzero literal return equality rendering

Scope:

- This is an external VeriPUT PUT-emitter oracle-strength fix.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Finding:

- Post-state rungs already rendered direct decimal equality such as
  `post == 7`.
- Return rungs special-cased only `return == 0`. A certified ladder row
  `return == 7` was dropped unless the optional structured-R2 term table also
  contained a literal entry for `7`.
- That weakens getter and pure/view PUTs: the solver has proved a concrete
  nonzero return equality over the region, but the generated test could lose
  the return oracle.

Code change:

- `return_rung_assertions()` now renders decimal `return == N` directly for
  non-bool scalar/address/fixed-bytes return types, preserving the existing
  cast path for each declared return kind.
- Added a regression where `return == 7` produces an `assertEq` and no
  `oracle_skipped` entry without relying on `r2_terms`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 212/212 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 literal return inequality rendering

Scope:

- This is an external VeriPUT PUT-emitter oracle-strength follow-up to literal
  return equality rendering.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Finding:

- Non-bool returns only rendered the built-in `return != 0` inequality.
  A certified row such as `return != 7` was dropped even though it is a valid
  return-value oracle.
- Bool returns rendered equality but not numeric inequalities. `return != 0`
  is exactly `assertTrue(ret)` and `return != 1` is `assertFalse(ret)`;
  other numeric bool inequalities remain unsafe to spell.

Code change:

- Non-bool scalar/address/fixed-bytes return rows `return != N` now render as
  `assertTrue(cast(ret) != N, ...)` using the declared return type's existing
  cast path.
- Bool return rows `return != 0` and `return != 1` now render as
  `assertTrue` / `assertFalse`; `return != 2` and other out-of-domain bool
  literals remain refused.
- Added regressions for both direct rendering and full PUT emission.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 214/214 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 bool literal return equality rendering

Scope:

- This is an external VeriPUT PUT-emitter oracle-strength follow-up to nonzero
  literal return equality rendering.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Finding:

- Bool returns already rendered `return == true` and `return == false`, and
  could render `return == 0/1` only when the structured-R2 term table included
  a literal entry.
- A direct ladder/source-R2 row `return == 1` on a bool return could therefore
  be dropped even though it is exactly the same assertion as `return == true`.

Code change:

- `return_rung_assertions()` now treats bare bool-return spellings
  `return == 0` and `return == 1` as `assertFalse` / `assertTrue`.
- Other numeric bool literals, e.g. `return == 2`, remain refused rather than
  guessed.
- Added a regression that reaches full PUT emission with a bool `return == 1`
  row and records no `oracle_skipped` entry.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 213/213 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 return antichain pruning

Scope:

- This is an external VeriPUT PUT-emitter oracle-strength accounting fix.
- No ESBMC/Forge/fuzz POC or benchmark attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Finding:

- After literal return equality/inequality rendering was added, a pure/view
  path can keep several return rows that are the same oracle in weaker forms,
  for example `return == 20`, `return in [20, 20]`, and `return != 0`.
- Leaving all of them in the PUT inflates assertion counts without increasing
  mutation-detection strength. This is the same measurement problem the
  existing post-state antichain solved for `post > pre` versus `post >= pre`.

Code change:

- `antichain()` now recognizes return literal equalities and singleton literal
  intervals as exact values.
- On the same return variable only, an exact value drops:
  - the matching singleton interval, e.g. `return in [20, 20]`;
  - a weaker literal inequality when the exact value differs, e.g.
    `return == 20` entails `return != 0`.
- Bool spellings `return == true/false` are mapped to `1/0` for this pruning.
- REFUTED return rows still imply nothing, and tuple members (`return.0`,
  `return.1`, etc.) do not cross-imply each other.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 216/216 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 benchmark sampling preflight

Scope:

- This is a read-only / external-cache preflight for sampling the real
  benchmark populations after the POC-level emitter fixes.
- No ESBMC/Forge/fuzz certification or PUT roundtrip attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Operational finding:

- Prepared benchmark subjects under
  `/home/samson/workspace/VeriPUT/Results/{Peer182,BugFix124,Stress243}/subjects`
  currently have no `flat.sol.solast` in-place. The benchmark planner therefore
  blocks unit enumeration until compact ASTs exist.
- The safe path is to preheat compact ASTs into an external cache, e.g.
  `/tmp/veriput_ast_cache_sample_<stamp>`, never under VeriPUT `Datasets/` or
  `Results/`. This runs solc only, not ESBMC, Forge, or fuzzing.
- A dry-run peer sample plan with `--subject-limit 2 --unit-limit 2` correctly
  reported `next_action=preheat-ast`, `ast_preheat_jobs=2`, `unit_jobs=0`.
- Running that AST preheat for the two selected peer subjects wrote only under
  `/tmp/veriput_ast_cache_sample_20260806_193711` and succeeded:
  - `peer_ccsolbmc__AIRBets`: 18 units.
  - `peer_ccsolbmc__Address`: 28 units, plus 10 skipped public state getters.
- Replanning against that cache produced `next_action=run-unit-campaign`,
  `unit_jobs=4`, first attempt `timeout_s=60`, `memlimit_gb=8`.

Sampling caveat:

- The default unit scheduler is order-driven after priority sorting. For peer it
  selected AIRBets `initialize2`, `name`, `symbol`, `decimals` first. That is a
  poor signal for PUT strength because several are getter-like return units.
- Do not spend certification budget on the scheduler's first rows blindly. Pick
  semantically useful units manually or add a scheduler priority that prefers
  hinted/writing/nontrivial units.

Good first benchmark samples to inspect before running:

- `peer182 / peer_soltg__simple_if / Csi1.simple_if(uint256)`:
  source is tiny; expected path split is `a < 5 -> return 0` and
  `a >= 5 -> return 1`. Good for literal return region/oracle strength.
- `peer182 / peer_soltg__return_1 / Cr1.add(uint,uint)`:
  expected paths are `y == 0 -> return x`, `y == 1 -> return x+1`,
  `y == 2 -> return x+2`, otherwise `return x+y`. Good for source-R2 return
  terms and return antichain.
- `peer182 / peer_syntest__MetaCoin / MetaCoin.sendCoin(address,uint)`:
  expected false path when `balances[msg.sender] < amount`, true path after
  loop-normalizing small `amount` and clipping amounts above 9000. Good for
  mapping/sender region quality, but harder than the SolTG micro programs.
- `bugfix124 / rc_time_manipulation__ether_lotto__SolGPT__ether_lotto_1round /
  EtherLotto.play()`:
  expected requires `msg.value == 10`; then timestamp/difficulty-derived branch
  either pays out and resets `pot` or just accumulates. Good env-region sample,
  with `block.difficulty` likely unsupported by the current safe setter policy.
- `stress203 / compound-finance__comet__AssetListFactory /
  AssetListFactory.createAssetList(...)`:
  small stress subject but uses dynamic array-of-struct input and creates a new
  contract; useful for checking how quickly the pipeline hits frontend/model
  limits on realistic flattened code.

Next recommended action:

- Preheat ASTs for a small curated set of 3-5 subjects into a fresh `/tmp`
  cache.
- Run dry-run `certify_all.py` for the chosen units and inspect the exact
  strong recipe argv.
- Only then spend 60s/8GiB certification attempts, one unit at a time, writing
  journals and generated PUT artifacts under `/tmp/veriput_sample_*`.

## 2026-08-06 benchmark unit scheduling priority

Scope:

- This is an external VeriPUT benchmark-pipeline scheduling fix prompted by the
  sampling preflight above.
- No ESBMC/Forge/fuzz certification or PUT roundtrip attempt was consumed.
- `/home/samson/workspace/VeriPUT/Datasets` and
  `/home/samson/workspace/VeriPUT/Results` remained unchanged.

Finding:

- After AST preheat, the unit scheduler's default order selected
  AIRBets `initialize2`, `name`, `symbol`, `decimals` for the first peer sample.
  Several of those are zero-argument getter-like units, so the first real
  certification attempts would measure weak PUT surfaces instead of useful
  state-changing or parameterized paths.

Code change:

- `veriput_subjects.py` now preserves per-unit AST metadata in the unit
  manifest: owner contract, visibility, state mutability, parameter count,
  return count, and implementation flag.
- `unit_schedule.py` still keeps target hints first, but otherwise prioritizes:
  1. state-changing functions;
  2. pure/view functions with parameters or return values;
  3. zero-argument pure/view functions.
- Old manifests without `unit_info` remain accepted and get the prior
  `enumerated` scheduling reason.

Measured effect:

- Replanning the same two-subject peer cache now selects AIRBets
  `initialize2`, `transfer`, `approve`, `transferFrom`, `setMaxTxPercent`,
  `openTrading`, `receive`, `manualswap` as the first 8 jobs, all
  `state-changing`, instead of `name/symbol/decimals`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_veriput_subjects.py`
  passed: 20 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/veriput_subjects.py notes/coverage/scripts/unit_schedule.py scripts/test_veriput_subjects.py scripts/test_unit_schedule.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 Stage-4 R2 emitted-case context fix

Scope:

- This is a Stage-4 PUT emitter fix and one real benchmark smoke sample.
- It does not modify `/home/samson/workspace/VeriPUT/Datasets` or
  `/home/samson/workspace/VeriPUT/Results`; all generated artifacts went under
  `/tmp/veriput_bench_sample_simple_if_20260806_194351`.
- The benchmark ESBMC budget used here was one 60s/8GiB Stage-4 roundtrip for
  `peer182__peer_soltg__simple_if.simple_if` after the earlier Stage-2
  certification sample.

Bug:

- `put_all.py` reached Stage 4 with three certified `simple_if` regions but
  emitted zero PUTs because `scripts/solidity_path_put.py` crashed in R2 setup:
  `NameError: name 'body' is not defined`.
- Root cause: `rendered_env_coords_for_r2(body, call_i, region)` was called from
  `main()`, but `body` and `call_i` were locals of `build_put()`.
- The same area also parsed parameter arity via a repeated emitted-body slice and
  `find_unit_call(...) or 0`, which could parse the wrong line if the call was
  absent.

Code change:

- Added `emitted_case_body_and_call(emitted, case, unit)` and
  `rendered_env_coords_for_emitted_case(...)`.
- `main()` now recovers `case_body/case_call_i` immediately after selecting the
  emitted case, uses that for AST arity, and passes the emitted-case helper into
  R2 rendered-coordinate discovery.
- If the unit call is missing, env R2 coords are empty rather than guessed.

Regression:

- Added `test_R2_env_coords_are_recovered_from_the_emitted_case`, which asserts
  that ordinary replay exposes `msg.sender` and `msg.value` through the emitted
  case and that a missing call yields no guessed env coords.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 217 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `git diff --check` passed.

Benchmark smoke result:

- Ground truth for `peer182 / peer_soltg__simple_if / Csi1.simple_if(uint256)`:
  `a < 5` increments local `a` and returns `0`; `a >= 5` asserts `a >= 5` and
  returns `1`.
- Stage 2 had certified 3/3 witnessed regions:
  - enc=2: `msg.value > 0`, value-gate/rollback path.
  - enc=6: `msg.value == 0`, `a in [0,4]`, expected return `0`.
  - enc=7: `msg.value == 0`, `a >= 5`, expected return `1`.
- After the fix, Stage 4 emitted all 3 PUTs and all passed the Foundry gates:
  `B = 3 of 3 emitted PUT(s)`.
- Output root:
  `/tmp/veriput_bench_sample_simple_if_20260806_194351/put-roundtrip-after-r2ctx`.

Quality finding for next optimization:

- R2 is now productive but noisy. On `simple_if`, enc=6 emitted 130 return
  assertions and enc=7 emitted 53. Many are equivalent under point environment
  coordinates, e.g. `return == msg.value`, `return == (0 + 0)`, and
  `return == 0` all survive together when `msg.value == 0`.
- Correctness is not harmed, but generated PUTs are bloated. The next likely
  improvement is an R2/return antichain pass that treats point-width rendered
  coordinates and simple constant-foldable terms as implied by exact literal
  return equality.

## 2026-08-06 checked-arithmetic certification fix

Scope:

- This is an ESBMC certification semantics fix plus the matching external
  VeriPUT driver/report plumbing.
- No dataset or VeriPUT Results files were modified. All benchmark sampling
  artifacts were written under `/tmp/veriput_peer_checked*` and
  `/tmp/certify_all/checked*-results`.

Ground truth sample:

- Benchmark unit:
  `peer182 / peer_soltg__return_1 / Cr1.add(uint256 x, uint256 y)`.
- Source behavior:
  - `y == 0`: returns `x`; no arithmetic restriction on `x`.
  - `y == 1`: returns `++x`; normal exit requires `x <= UINT_MAX - 1`.
  - `y == 2`: returns `x + 2`; normal exit requires
    `x <= UINT_MAX - 2`.
  - `y >= 3`: returns `x + y`; normal exit requires the cross-coordinate
    relation `x + y <= UINT_MAX`, which is not representable as one product
    interval without splitting or extra relational predicates.

Bug:

- Strong Stage 2 passed `--overflow-check`, `--div-by-zero-check`, and
  `--path-cov-arith-resolve`, but `--path-cov-certify` still reported wide
  arithmetic regions as certified.
- Root cause inside ESBMC: certification RESULT aggregation only counted its own
  `#exitN` path assertions. A failed Solidity checked-arithmetic assertion under
  `assume(box)` made the overall ESBMC run fail, but did not become a
  certification refutation with a shrinkable witness.
- A second bug hid the available witness: arithmetic claim names are not
  `<unit>:path:<enc>`, so the CE harvester could not infer the Solidity function
  scope and dropped `x/y` as non-unit inputs.

Code changes:

- `notes/coverage/scripts/veriput_recipe.py` now includes
  `--overflow-check`, `--div-by-zero-check`, and `--path-cov-arith-resolve` in
  the shared strong recipe.
- `scripts/solidity_path_generalise.py` disables simple-decision structural
  certification when enumeration saw checked arithmetic, so ESBMC certification
  has to measure those boxes.
- ESBMC records failed `overflow` / `division-by-zero` claims during
  `--path-cov-certify` as `path_cov_certify_safety_refutations` and reports
  `RESULT: UNSAFE`, which the driver treats as refuted-equivalent.
- `cov-report.json` now carries `certify_safety_refutations` outside the path
  `claims` array, preserving the path coverage denominator while publishing the
  safety CE payload for shrink.
- The CE harvester now scopes certification safety claims through the
  certification nonvacuity key, so parameter inputs like `x/y` survive in the
  report.
- The driver reads `certify_safety_refutations` before ordinary path claims and
  matches either plain `condition` unit names or exact `path_function` ids.
- Driver policy: `RESULT: UNSAFE` does not use Definition-5 punch suggestions.
  Arithmetic unsafe sets are usually intervals, so side shrink is the correct
  first response.

Measured effect:

- Before this fix on `return_1.add`: Stage 2 certified 5/5 witnessed paths, but
  Stage 4 Foundry rejected the `y==1`, `y==2`, and `y>=3` PUTs because their
  regions admitted overflow inputs.
- With recipe flags but before ESBMC safety refutations: still 5/5 certified,
  because structural certification and later certification ignored arithmetic
  failures.
- After ESBMC `UNSAFE` plus payload/scoping fixes, 60s/8GiB sample result:
  `4 certified / 1 not / 5 witnessed` in about 15s.
  - enc=2: ABI value gate, `msg.value > 0`.
  - enc=6: `msg.value == 0`, `y == 0`, `x` full.
  - enc=14: `msg.value == 0`, `y == 1`, `x <= UINT_MAX - 1`.
  - enc=30: `msg.value == 0`, `y == 2`, `x <= UINT_MAX - 2`.
  - enc=31: not certified; the needed relation is `x + y <= UINT_MAX`, and the
    current product-region shrink removes one `x` boundary value per round until
    the shrink budget is exhausted.
- Best sample output:
  `/tmp/veriput_peer_checked8_20260806_201942/checked8-results.jsonl`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/veriput_recipe.py`
  passed.
- `cmake --build build -j2 --target esbmc` passed.
- `git diff --check` passed.

Next work:

- Add a relational R1/R2 or split strategy for arithmetic regions like
  `x + y <= UINT_MAX`. The current product interval can certify constant-y
  arithmetic guards but cannot express the `y>=3` branch strongly.

## 2026-08-06 product-region fallback for repeated safety cuts

Problem found after the checked-arithmetic certification fix:

- `peer182 / peer_soltg__return_1 / Cr1.add(uint256 x, uint256 y)` enc=31
  reaches `return x + y` under `y >= 3`.
- The true normal-exit domain is relational: `x + y <= UINT_MAX`.
- Stage 2 regions are product boxes. They cannot spell that relation, so ESBMC
  kept producing `RESULT: UNSAFE` witnesses at the current `x` upper boundary.
  The old loop responded soundly but poorly: cut `x` by one value, query again,
  cut one more value, and finally report `shrink round budget exhausted`.
- This is not a solver timeout and not an insertion bug anymore. It is a region
  language limitation exposed by cheap refutation.

Code changes:

- Added `tiny_safety_cut_retreat()` in `scripts/solidity_path_generalise.py`.
  It pins one coordinate to the path witness only when all of these hold:
  repeated `RESULT: UNSAFE` refutations, same coordinate, one-value cuts,
  threshold reached, and at least one other non-environment coordinate remains
  wide.
- Added driver flag `--safety-retreat-after-tiny-cuts` (default 2; 0 disables).
  The value is written into `run-config.json`.
- `certify()` now returns `"UNSAFE"` as the refutation cause through the existing
  sixth tuple slot, so the shrink loop can distinguish safety refutations from
  ordinary path assertion refutations.
- `notes/coverage/scripts/certify_all.py` now exposes, forwards and records the
  new flag.
- Bumped strong recipe to `veriput-strong/8` and included
  `--safety-retreat-after-tiny-cuts 2`.
- Updated `scripts/test_unit_schedule.py` to read the recipe version constant
  instead of hard-coding `/7`.

Guardrails:

- This does not prove a relation. It deliberately returns a partial
  generalization, e.g. pin `x` and keep `y` wide.
- It does not fire for single-coordinate boundary checks such as `x + 2`, where
  two one-value cuts are the right answer.
- It ignores width left only in environment coordinates (`msg.*`, `tx.*`,
  `block.*`), because that would look parameterized without giving the target
  function a meaningful input/state dimension.

Measured benchmark sample:

- Command family: `certify_all.py` on prepared subject
  `peer182__peer_soltg__return_1`, unit `add`, 60s driver timeout,
  60s per-ESBMC timeout, 8GiB memlimit, output under
  `/tmp/veriput_peer_return1_tinyretreat_20260806_204549`.
- Result: `5 certified / 0 not / 5 witnessed` in 23s.
- Important region:
  - enc=31 certified as `msg.value == 0`, `x == 0`,
    `y in [3, UINT_MAX]`.
  - `generalise-result.json` records `retreated: {"x": "0"}` for enc=31.
  - Driver log shows one ordinary one-value cut, then
    `[retreat enc=31] PINNED x==0 ... after repeated one-value safety cuts`.
- The row was labelled `veriput-strong/7` because the recipe bump happened after
  this sample; the command did pass and record
  `--safety-retreat-after-tiny-cuts 2`. Future scheduled runs use
  `veriput-strong/8`.

Verification:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_guards.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py scripts/test_unit_schedule.py notes/coverage/scripts/certify_all.py notes/coverage/scripts/veriput_recipe.py`
  passed.
- `git diff --check` passed.

Next work:

- Start broader benchmark sampling under recipe `veriput-strong/8`, first with
  a few `peer182` units, then `bugfix124`, then `stress243`.
- Still needed for maximal strength: a real relational region/R2 strategy for
  arithmetic guards. The current fallback is intentionally weaker but useful:
  it trades one dimension for a certified wide region on the remaining semantic
  dimension(s).

## 2026-08-06 benchmark sampling status after strong/8

Sampling is still read-only with respect to
`/home/samson/workspace/VeriPUT/Datasets` and
`/home/samson/workspace/VeriPUT/Results`; all AST caches and outputs went under
`/tmp`.

Peer sample:

- Subject:
  `peer182 / peer_syntest__MetaCoin / MetaCoin.sendCoin(address receiver,uint amount)`.
- Ground truth:
  `sendCoin` has an insufficient-balance return-false path and a sufficient
  return-true path. The loop adjusts `amount` for large values and then raises
  small amounts to at least 5; the function does not write storage.
- Run:
  `/tmp/veriput_sample_peer_metacoin_20260806_205249`, recipe
  `veriput-strong/8`, 60s timeout, 8GiB memlimit.
- Result:
  `CERTIFIED`, `2 certified / 0 not / 2 witnessed`, 6 free coordinates, 22s.

BugFix sample:

- Subject:
  `bugfix124 / rc_time_manipulation__ether_lotto__SolGPT__ether_lotto_1round /
  EtherLotto.play()`.
- Ground truth:
  `msg.value != 10` reverts; at `msg.value == 10`, hash-derived randomness
  splits into winner and non-winner paths. Winner path transfers fee and
  `pot - fee`, then resets `pot`; non-winner path keeps the incremented pot.
- Run:
  `/tmp/veriput_sample_bugfix_etherlotto_20260806_205324`, recipe
  `veriput-strong/8`, 60s timeout, 8GiB memlimit.
- Result:
  `KILLED`, `0 certified / 0 not / 3 witnessed`.
- Diagnosis:
  Level0 found the three witnesses quickly, but refinement spent the budget on
  438 probes and still reported `UNSEPARATED=[12,13]`. Those two normal paths
  share the visible product coordinates and appear to split on the
  hash/randomness expression, which is not currently represented as a useful
  coordinate. This is a region/coordinate strategy problem, not a frontend
  insertion crash.

Stress sample:

- Subject:
  `stress243 / compound-finance__comet__AssetListFactory /
  AssetListFactory.createAssetList(CometConfiguration.AssetConfig[] memory
  assetConfigs)`.
- Ground truth:
  `createAssetList` constructs `AssetList`; the constructor stores
  `numAssets = uint8(assetConfigs.length)` and repeatedly calls
  `getPackedAssetInternal`. That helper uses inline assembly `mload` to load an
  `AssetConfig` element, then reads namespaced struct fields such as
  `assetConfig.asset` and `assetConfig.priceFeed`.
- First run:
  `/tmp/veriput_sample_stress_assetlist_20260806_205501` failed immediately
  with `cannot find struct member reference` after unsupported Yul `mload`
  over-approximation.
- ESBMC frontend fix:
  `find_decl_ref()` now falls back to a global AST-id lookup after scoped
  contract/base/library/interface lookup fails. This covers ordinary contracts
  used as namespaces for shared struct declarations, e.g.
  `CometConfiguration.AssetConfig`, while keeping scoped lookup priority.
- Post-fix run:
  `/tmp/veriput_sample_stress_assetlist_reflookup_20260806_210039`, recipe
  `veriput-strong/8`, 60s timeout, 8GiB memlimit.
- Post-fix result:
  `KILLED`, `0 certified / 0 not / witnessed UNKNOWN`, no coordinates emitted.
  The previous conversion error is gone; this sample is now in the heavy
  initial generation/conversion/symex bucket rather than the immediate
  field-lookup failure bucket.

Checks after the ESBMC frontend fix:

- `cmake --build build -j2 --target esbmc` passed.
- `cppcheck --enable=style,warning ... src/solidity-frontend/solidity_convert_util.cpp`
  passed with no output.
- `git diff --check` passed.

Current decision:

- It is reasonable to start small, stratified benchmark sampling now:
  a batch from `peer`, then `bugfix124`, then `stress203/243`, all first-pass
  60s/8GiB.
- It is not yet a good point for an expensive full benchmark run. The Peer
  sample is healthy, BugFix exposed a hash/randomness coordinate gap, and Stress
  exposed that some large inline-assembly/dynamic-array subjects can burn the
  whole first pass before coordinates are available.

## 2026-08-06 strong/9 static uncontrolled split filter

Problem:

- `EtherLotto.play()` showed a common waste pattern for hash/time/randomness
  contracts. Enumeration and Level0 were cheap, but the driver spent the rest
  of the first 60s attempt refining/certifying two sibling paths split by
  `random == 0`, where `random` is derived from ESBMC's hash/nondet model and is
  not a generated-test-settable coordinate.
- Old result:
  `/tmp/veriput_sample_bugfix_etherlotto_20260806_205324`, recipe
  `veriput-strong/8`, 60s/8GiB:
  `KILLED`, `0 certified / 0 not / 3 witnessed`; Level0 had decided all 3
  paths in 3.1s, then refinement reported `UNSEPARATED=[12,13]`.

Change:

- Added driver flag `--static-uncontrolled-inseparable`.
- It is refutation-only. It never proves a PUT. It only marks sibling paths as
  `NOT_CERTIFIED` before region search when:
  - the pair's decision context contains a known uncontrolled ESBMC source:
    `__esbmc_hash_result`, `NONDET(`, or `extcall.`;
  - the differing source decision does not read any current free coordinate.
- This keeps ordinary input/state guards such as `amount > 9000` in the normal
  region search.
- `notes/coverage/scripts/certify_all.py` forwards and records the flag.
- Bumped the shared recipe to `veriput-strong/9` and enabled the flag there.

Measured result:

- Run:
  `/tmp/veriput_sample_bugfix_etherlotto_static_unctrl_20260806_211020`,
  recipe `veriput-strong/9`, 60s/8GiB.
- Result:
  `CERTIFIED`, `1 certified / 2 not / 3 witnessed`, 23s.
- The two not-certified paths are enc=12/13 with reason:
  `STATICALLY INSEPARABLE ... decision#3 random == 0`.
- The certified path enc=2 captures the `msg.value != 10` ABI/value gate:
  `msg.value in [11, UINT_MAX]` plus wide block/sender/bank coordinates and
  pinned constants/state.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_guards.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/certify_all.py notes/coverage/scripts/veriput_recipe.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 stress ESBMC frontend crash triage

Context:

- The stratified benchmark sample showed peer/bugfix positives, but stress
  units were blocked before VeriPUT region search:
  `OwnableAuthentication.transferOwnership` and
  `IdentityRegistryStorage.addIdentityToStorage` aborted during Solidity
  conversion.
- These were ESBMC Solidity frontend/modeling blockers, not weak PUT-region
  candidates.

Fixes now in the working tree:

- `src/util/c_typecast.cpp`: pointer casts now return early for identical
  pointer types and for pointer-to/from-void before following the pointed
  subtype. This avoids resolving incomplete Solidity contract/interface symbols
  when the cast does not need their body.
- `src/solidity-frontend/solidity_convert_expr.cpp`: `LValueToRValue` no longer
  calls the C typecaster. Solidity's cast changes value category, not type; the
  previous no-op typecast forced premature `namespacet::follow` on forward
  interface symbols such as `tag-IAuthorizer`.
- `src/solidity-frontend/solidity_convert_decl.cpp`: inherited private
  OpenZeppelin upgradeable storage gaps named `__gap` get an internal
  component-name suffix based on their AST id. ESBMC flattens inherited storage
  into one struct, so repeated private `__gap` fields otherwise made
  `struct_union_data::get_component_number` abort with
  `Name "__gap" matches more than one member`.

Measured confirmation:

- Built `build/src/esbmc/esbmc` successfully after the changes.
- `stress243__balancer__balancer-v3-monorepo__OwnableAuthentication /
  transferOwnership`, direct ESBMC 60s/8GiB:
  no `namespacet::follow` abort; complete coverage report written in about 21s,
  `Complete Paths: 3`, `Reached: 3`, `Path Coverage: 100%`.
- `stress243__ERC-3643__ERC-3643__IdentityRegistryStorage /
  addIdentityToStorage`, direct ESBMC 60s/8GiB:
  no `namespacet::follow` abort and no `__gap` duplicate-member abort. It
  reached normal BMC and timed out at 60s with a partial journal:
  `Claims Decided: 92 of 116`, `F 5`, `cov-ce-journal.json` written. No
  `cov-report.json` because the run was terminated before completion.

Checks:

- `/home/samson/.local/bin/clang-format -i` on the three changed C++ files.
- `git diff --check` passed.
- `cppcheck --enable=style,warning` on changed Solidity frontend files passed.
- `cmake --build build --target esbmc -j2` passed.

Current status / next call:

- Code completion for the current crash class is high enough to start
  benchmark sampling.
- Recommended next phase: small stratified sampling, serial, 60s/8GiB first.
  Peer and bugfix can be sampled broadly now. Stress can be sampled, but treat
  60s partials as timeout/cost data rather than PUT failures; escalate only
  selected stress units to 120s or 600s after inspecting their ground-truth
  paths and witness journal.
- Still do not modify `/home/samson/workspace/VeriPUT/Datasets`; use cached ASTs
  and `/tmp` workdirs.

## 2026-08-06 benchmark sampling gate and public getter relation fix

Small benchmark sample after the recursive-helper preflight:

- `peer182__peer_ccsolbmc__AIRBets / AIRBets.transfer`:
  `NO-WITNESS-UNDECIDED` immediately.  The new preflight refuses because the
  target call closure reaches direct recursive SafeMath wrappers
  (`SafeMath.div/2`, `SafeMath.sub/2`).  This is the intended cheap outcome;
  it avoids spending the old 60s budget on a no-witness run.
- `peer182__peer_solar__PermissionGroups / transferAdmin`:
  first 60s/8GiB sample was `KILLED`, but witness discovery was already cheap:
  level-0 found four paths in about 0.9s and refine rounds were still single
  digit seconds.  The timeout happened later in certification/shrink.
- Ground truth for `PermissionGroups.transferAdmin`:
  constructor sets `admin = msg.sender`; `onlyAdmin` checks
  `msg.sender == admin`; the unit then requires
  `newAdmin != address(0)` and writes `pendingAdmin = newAdmin`.
  Expected useful regions are the non-admin revert, admin+zero revert, and
  admin+nonzero normal path, with the nonpayable `msg.value != 0` ABI gate
  excluded by the global pin.
- Root cause of the `PermissionGroups` timeout:
  relation-retreat only recognised getter names like
  `return_value$_owner$1 -> state._owner`.  Public state getters without a
  leading underscore, e.g. `return_value$admin$1`, were not mapped to
  `state.admin`.  The path relation therefore remained an unhandled
  cross-coordinate relation instead of pinning the entry-state side.
- `bugfix124__rc_time_manipulation__ether_lotto__SolGPT__ether_lotto_1round /
  EtherLotto.play`: certified `1 / 3` in 23s.  The value path
  `msg.value >= 11` is certified; paths depending on `random == 0` remain
  statically inseparable, which matches the expected hash/random limitation.
- `stress243__balancer__balancer-v3-monorepo__OwnableAuthentication /
  transferOwnership`: `NO-WITNESS-UNKNOWN` after ESBMC aborts in
  `namespacet::follow(const typet&)` during conversion.  This is an ESBMC
  Solidity frontend/modeling crash, not a region strategy result.

Implemented fix:

- `_decision_term()` now maps any source-level getter-shaped term
  `return_value$<identifier>$N` to `state.<identifier>` when that coordinate
  exists in the CE or pin set.  The existing `__msgSender` special case still
  runs first, so caller modeling is unchanged.
- Tests now cover both the direct mapping
  `return_value$admin$1 -> state.admin` and the PermissionGroups-shaped
  relation-retreat case where `admin == msg.sender` / `admin != msg.sender`
  should pin the entry-state `state.admin` side.

Current gate decision:

- Code completion for the current known region bug is high enough to run the
  next sample, but not a full benchmark sweep.
- Recommended next run: a small stratified sample, serial, first-pass
  60s/8GiB only, covering peer/bugfix/stress and at least:
  ownership/admin guards, payable/value gates, hash/random paths, and one
  known ESBMC-crash-shaped subject.
- Do not rerun `PermissionGroups.transferAdmin` as a first attempt; its 60s
  budget has already been used.  If using it as the confirmation subject after
  this fix, count it as the second attempt and use 120s/8GiB.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/certify_all.py`
  passed.
- `git diff --check` passed.

## 2026-08-06 stratified benchmark sample and bare-state relation fix

First stratified sample after `veriput-strong/12`:

- Inputs and outputs were kept under `/tmp`:
  - target manifest: `/tmp/veriput_targets_strat7_20260806.json`
  - unit manifest: `/tmp/veriput_unit_manifest_strat7_20260806.json`
  - first sample results: `/tmp/veriput_bench_strat_20260806/results.jsonl`
  - final PermissionGroups confirmation:
    `/tmp/veriput_bench_strat_confirm_20260806/results.jsonl`
- No Dataset or Results contract source was modified.
- The 7-subject unit manifest generated compact ASTs only in
  `/tmp/veriput_bench_ast_cache_20260806`; this starts solc but not ESBMC.
  It found 57 target-contract public/external units.

Ground-truth sample choices:

- `PermissionGroups.transferAdmin`: constructor initializes `admin`, modifier
  checks `msg.sender == admin`, body checks `newAdmin != 0`.
- `EtherBank.withdraw`: nonpayable value gate, then `_amount >= min_withdraw`
  and `balances[msg.sender] >= _amount`.
- `Randomness.reveal`: state/block/hash gated; expected to expose
  bytes/hash/random modeling limitations rather than product-region strength.
- `ClaimTopicsRegistry.addClaimTopic`: Ownable guard plus array-length and
  duplicate-topic loop; expected to test ERC-3643 owner/array behavior.

Measured sample results before the bare-state fix:

- `EtherBank.withdraw`, 60s/8GiB:
  `CERTIFIED`, `2 certified / 1 not / 3 witnessed`, about 18s.  Certified
  regions match the source: `_amount < min_withdraw` and
  `_amount >= min_withdraw` with zero balance; the nonpayable ABI value-gate
  path is excluded by `msg.value == 0`.
- `Randomness.reveal`, 60s/8GiB:
  `NOT-CERTIFIED`, `0 / 2`, about 8s.  `_seed` is aggregate/bytes-like, only
  `_seed.length` becomes a coordinate; certification prints no verdict for both
  paths.  This is a hash/bytes/block modeling limitation, not a search timeout.
- `ClaimTopicsRegistry.addClaimTopic`, 60s/8GiB:
  `NO-WITNESS-UNKNOWN`, about 1s.  ESBMC exits `-6` before producing
  `cov-report.json`; this joins the current stress/ERC-3643 frontend/modeling
  crash bucket rather than the region-strategy bucket.
- `PermissionGroups.transferAdmin`, second allowed attempt 120s/8GiB:
  `NOT-CERTIFIED`, `0 / 4`, about 72s.  It no longer timed out, but the
  region search failed to capture the admin/sender relation, then shrink spent
  its budget shaving single values from `msg.sender` and `state.admin`.

Root cause of the remaining PermissionGroups miss:

- The previous fix recognised `return_value$admin$1 -> state.admin`.
- Real `PermissionGroups` branch decisions are source-shaped:
  `msg.sender == admin`, not getter-shaped.  `_decision_term()` did not resolve
  bare source-level state names such as `admin`, so
  `structural_decision_regions_with_retreat()` never saw
  `msg.sender == state.admin`.

Implemented fix:

- `_decision_term()` now maps a bare identifier `x` to `state.x` only when
  `state.x` is present in the current CE or pin set.  This keeps local
  parameters such as `newAdmin` resolved by the earlier direct `term in ce`
  branch, and avoids guessing that an arbitrary identifier is state.
- Tests now cover:
  - direct `admin -> state.admin` CE and pin resolution;
  - getter-shaped `return_value$admin$1 -> state.admin`;
  - PermissionGroups-shaped branch claims
    `msg.sender == admin` / `msg.sender != admin`.

Measured confirmation after the fix:

- `PermissionGroups.transferAdmin`, third and final allowed attempt,
  600s/10GiB max:
  `CERTIFIED`, `3 certified / 1 not / 4 witnessed`, about 51s.
- The driver printed:
  - relation-retreated seed regions for enc=6, enc=14, enc=15;
  - skipped level0, witness-pool probes, per-path ladders, bracket and refine;
  - certified all three body paths through ESBMC certification.
- Certified path shapes:
  - enc=6: `state.admin == 4294967295`,
    `msg.sender != 4294967295`, `newAdmin` full address range;
  - enc=14: `state.admin == 4294967295`,
    `msg.sender == 4294967295`, `newAdmin == 0`;
  - enc=15: `state.admin == 4294967295`,
    `msg.sender == 4294967295`, `newAdmin != 0`.
- enc=2 remains the nonpayable ABI value-gate path excluded by the
  `msg.value == 0` pin and should not be read as a region-search failure.
- Speed attribution: the search stage is now cheap/skipped for this class.
  The remaining about 51s is dominated by three ESBMC certification queries
  under checked arithmetic, not by ladder/refine/shrink.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/certify_all.py`
  passed.
- `git diff --check` passed.

Next:

- Run a few more first-attempt 60s/8GiB samples before any full sweep:
  `BasicProvenance.TransferResponsibility`,
  `DepositLog.setApprovedLogger`, and one ERC-3643 storage/mapping unit such as
  `IdentityRegistryStorage.addIdentityToStorage`.
- Keep `ClaimTopicsRegistry.addClaimTopic` and
  `OwnableAuthentication.transferOwnership` in an ESBMC-crash bucket until the
  Solidity frontend/modeling abort is debugged separately.

Additional first-attempt samples after the bare-state fix:

- Results root: `/tmp/veriput_bench_strat_more_20260806`.
- `peer182__peer_ccsolbmc__BasicProvenance / TransferResponsibility`,
  60s/8GiB:
  `CERTIFIED`, `2 certified / 1 not / 3 witnessed`, about 8s.
  The certified regions cover the body paths over `msg.sender` and
  `newCounterparty`; the nonpayable ABI value-gate path is excluded by
  `msg.value == 0`.
- `bugfix124__acfix_fixlink_DepositLog / setApprovedLogger`, 60s/8GiB:
  `CERTIFIED`, `2 certified / 1 not / 3 witnessed`, about 12s.
  The mapping slot `state.approvedLoggers[_logger]` is proposed from the
  source slot access, stays in the free coordinate set, and both body paths
  certify.  This is a useful positive sample for owner guard + mapping slot.
- `stress243__ERC-3643__ERC-3643__IdentityRegistryStorage /
  addIdentityToStorage`, 60s/8GiB:
  `NO-WITNESS-UNKNOWN`, about 1s.  ESBMC exits `-6` with
  `namespacet::follow(const typet&)` assertion failure during conversion, same
  stress/ERC-3643 crash class as `ClaimTopicsRegistry.addClaimTopic` and
  `OwnableAuthentication.transferOwnership`.

Updated sampling conclusion:

- peer182 and bugfix124 now have multiple quick positive first-attempt samples
  under `veriput-strong/12`.
- The current large visible blocker for stress203/243 is not PUT region search
  but ESBMC Solidity frontend/modeling crashes on some ERC-3643/Balancer
  subjects before `cov-report.json` exists.
- Do not spend more PUT tuning attempts on those stress units until the ESBMC
  `namespacet::follow` crash is isolated or statically preflighted into a
  separate bucket.

## 2026-08-06 relation-retreated structural seeds for owner/sender guards

Benchmark sample that exposed the next bottleneck:

- Subject:
  `stress243__ERC-3643__ERC-3643__AgentRole / AgentRole.transferOwnership`.
- Source shape:
  `onlyOwner` checks `owner() == _msgSender()`, then
  `require(newOwner != address(0))`, then writes `_owner = newOwner`.
- Pre-fix strong/12 sample:
  `/tmp/veriput_sample_v12_20260806_220907/.../transferOwnership` reported
  `0 certified / 5 not / 5 witnessed` in about 31s.
- Failure diagnosis:
  `msg.sender` was promoted to a free coordinate and `state._owner` was also
  free.  The path condition is relational (`msg.sender == state._owner` for
  owner paths, `!=` for non-owner paths).  A product box cannot express this
  diagonal relation, so shrink kept receiving witnesses where
  `extcall.return_value$__msgSender$2`, `msg.sender`, and `state._owner`
  diverged from the path CE.

Implemented change:

- `scripts/solidity_path_generalise.py` now has a relation-retreat helper for
  simple complete-path decisions.
- Default `structural_decision_region()` behaviour is unchanged:
  coordinate-to-coordinate constraints still return `None`.
- The new retreat path is opt-in inside the driver:
  when a complete path contains a simple `==` / `!=` relation between a source
  getter state coordinate and another rendered coordinate, it pins the
  `state.*` side to that path's CE value and converts the relation into an
  ordinary product slice.
- These relation-retreated seeds are NOT structural certificates.  They skip
  ladder/refine only when every non-pin-excluded path has such a seed, then
  each seeded path still goes through `--path-cov-certify`.
- This keeps the earlier EtherLotto lesson intact: global checked-arithmetic in
  the enumeration no longer forbids using the seed, but it still forbids
  accepting it without ESBMC certification.

Measured confirmation:

- First post-change run found a Python `None` normalization bug before
  certification; fixed without counting it as evidence.
- Second run:
  `/tmp/veriput_relretreat_transfer_20260806_2311`,
  60s/8GiB, certified `4 / 5` in about 23s.
- Third and final allowed sample rerun:
  `/tmp/veriput_relretreat_transfer_20260806_2326`,
  60s/8GiB, certified `4 / 5` in 4.3s by skipping level0/probe/refine.
- Certified regions:
  - enc=12: `state._owner == 2147483647`,
    `msg.sender != 2147483647`, `newOwner == 0`;
  - enc=13: `state._owner == 2147483647`,
    `msg.sender != 2147483647`, `newOwner != 0`;
  - enc=14: `state._owner == 4294967295`,
    `msg.sender == 4294967295`, `newOwner == 0`;
  - enc=15: `state._owner == 4294967295`,
    `msg.sender == 4294967295`, `newOwner != 0`.
- enc=2 remains not-certified by design because the global `msg.value == 0`
  body slice excludes the nonpayable ABI value-gate reject path.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check` passed.

Next:

- Do a small stratified benchmark sample again.  Expected immediate gain is on
  Ownable/AccessControl-style guards where both caller and entry owner/admin
  vary.
- `AIRBets.transfer` is still a separate witness-discovery bottleneck
  (`KILLED`, no witnessed path in the earlier 60s sample); relation-retreat
  does not address that class.

Next:

- Start small stratified benchmark sampling under `veriput-strong/10`.
- Watch the `static_uncontrolled_inseparable` count separately from true
  failures: it is a speed/attribution improvement, not a generalized-test
  success.

## 2026-08-06 strong/10 double-negated decision parsing

Follow-up after `veriput-strong/9`:

- ESBMC sometimes prints source decisions under nested negation, e.g.
  `!(!(msg.value == TICKET_AMOUNT))` for a failing `require` branch.
- The old parser stripped only the outer `!`, then parsed the left term as
  `!(msg.value`, so the simple structural decision recogniser missed this
  otherwise product-shaped value gate.

Change retained:

- `_unwrap_not()` now strips balanced outer negations recursively and tracks
  parity.
- `structural_decision_region()` can now read the above shape as
  `msg.value != TICKET_AMOUNT`, producing a product interval with a punched
  value.
- The shared recipe version is now `veriput-strong/10`.
- The final not-certified suffix no longer calls hash/randomness split cases an
  "external-call fixture" case; it says "uncontrolled decision source" unless
  the reason specifically names `extcall.*` / external-call behavior.

Measured confirmation:

- Run:
  `/tmp/veriput_sample_bugfix_etherlotto_v10_confirm_20260806_212128`,
  recipe `veriput-strong/10`, 60s/8GiB.
- Result:
  `CERTIFIED`, `1 certified / 2 not / 3 witnessed`, 23s.
- Same useful outcome as strong/9:
  enc=2 is certified for `msg.value in [11, UINT_MAX]`; enc=12/13 are
  statically not-certified due `decision#3 random == 0`.

Rejected experiment:

- I tried using structural decision regions under global checked-arithmetic as
  "pre-certify" candidates: skip level0/refine, but still send the region to
  ESBMC certification.
- On `EtherLotto.play()` this reduced wall time to about 11s but degraded the
  result to `0 certified / 3 not / 3 witnessed`; enc=2's widened structural
  region got `UNKNOWN` in certification while the concrete witness check passed.
- That code was removed. Do not reintroduce this shortcut without first saving
  the certification log and understanding why the region query produces no
  result. Speed without preserving certified count is not acceptable for the
  benchmark objective.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_guards.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py scripts/test_solidity_path_generalise.py notes/coverage/scripts/certify_all.py notes/coverage/scripts/veriput_recipe.py`
  passed.
- `git diff --check` passed.

## 2026-08-07 benchmark sampling and certification bottleneck

Current branch:

- `feat/veriput-fuzz-first`, pushed to `E-SOL/feat/veriput-fuzz-first`.
- Latest pushed commit at this checkpoint:
  `3cf34626c0 [solidity] Preserve VeriPUT salvage sidecars`.

What is now working:

- Partial enumeration salvage is now robust enough for benchmark accounting.
  If `cov-report.json` / `generalise-result.json` is missing because the outer
  wrapper kills the run, the driver can still record witnessed paths from
  `cov-ce-journal.json`.
- `certify_all.py` records `enumeration_salvage` from either
  `generalise-result.json` or the sidecar `enumeration-salvage.json`.
- Coordinate parsing no longer treats prose lines such as mapping dependency
  policy or `STATE PINNED` as fake coordinates.

Small benchmark confirmation:

- Bugfix124 sample:
  `/tmp/veriput_bench_salvage_bugfix_20260806_235923`
  produced `4 / 4` runner-ok and `4 / 4` `CERTIFIED`.
- Stress243 first sample:
  `/tmp/veriput_bench_salvage_stress_20260806_235923`
  showed that ClaimTopicsRegistry `addClaimTopic` and `removeClaimTopic`
  certify one salvaged path each under the 60s/8GiB first attempt.
- Peer sample:
  `/tmp/veriput_bench_salvage_peer_20260806_235924`
  still has separate shallow issues (`AIRBets.initialize2` `NO-PATH`,
  `AIRBets.transfer` `NO-WITNESS-UNDECIDED`).

Balanced stress sample:

- Directory:
  `/tmp/veriput_stress_balanced_20260807_002507`.
- Six attempt-1 jobs, 60s/8GiB policy via a 70s wrapper:
  2 `CERTIFIED`, 3 `KILLED`, 1 `NO-COORDINATE`.
- Certified:
  `OwnableAuthentication.transferOwnership` certified `2 / 3` witnessed paths;
  `IdentityRegistryStorage.storedIdentity` certified `1 / 2`.
- KILLED but useful:
  `IdentityRegistryStorage.addIdentityToStorage`,
  `IdentityRegistryStorage.bindIdentityRegistry`, and
  `OwnableAuthentication.forceTransferOwnership` all had witnessed paths and
  partial journals.  Their failure mode is not basic instrumentation failure.
- `OwnableAuthentication.getActionId` is `NO-COORDINATE` because the natural
  coordinates include unsupported selector/string-ish state, so it should be
  separated from tool-timeout failures.

Attempt-2 check on two KILLED stress rows:

- Schedule:
  `/tmp/veriput_stress_balanced_20260807_002507/next-unit-schedule-a2-light2.json`.
- Results:
  `/tmp/veriput_stress_balanced_20260807_002507/certify-results-a2-light.jsonl`.
- Policy:
  120s ESBMC budget, 130s unit timeout, 8GiB, `jobs=1`.
- `IdentityRegistryStorage.bindIdentityRegistry`:
  still `KILLED`, `0 certified / 0 not / 2 witnessed`.
  It salvaged `2` paths from a partial journal (`77 / 214` claims decided).
  Level-0 took only `1.6s`; coordinates were `_identityRegistry` and
  `msg.sender`.
- `OwnableAuthentication.forceTransferOwnership`:
  still `KILLED`, `0 certified / 0 not / 1 witnessed`.
  It salvaged `1` path from a partial journal (`1 / 303` claims decided).
  Level-0 took only `2.4s`; coordinate was `newOwner`.
  It reached a full-address refine region before timing out.

Current interpretation:

- The main stress bottleneck has moved from "cannot discover a witness" to
  "witness exists, cheap refutation/probing works, but the final
  refine/certification phase times out or does not emit enough structured
  intermediate status".
- This means fuzz/cheap CE is useful as a first filter and region tester, but
  only as refutation.  Certified PUTs still require ESBMC.
- The next tool-side improvement should distinguish at least:
  enumeration timeout, refine timeout, and certification timeout after a
  concrete/region candidate exists.  Without that split, a full benchmark run
  will undercount progress and overstate hard failures.

Recommended next step:

- Start only stratified small benchmark runs, not full stress-wide execution:
  peer `contract080`, several bugfix124 units, and a capped stress243 slice.
- Before a broad run, improve timeout accounting / partial status emission for
  the post-enumeration stages.  Otherwise the full run will spend many attempts
  rediscovering already-known witnessed paths and still label them `KILLED`.

## 2026-08-07 stage progress sidecar

Change retained after the stress attempt-2 diagnosis:

- `solidity_path_generalise.py` now writes
  `generalise-progress.json` in the unit workdir.
- The file is deliberately observational only.  It does not certify anything,
  does not promote a `KILLED` row, and does not change any ESBMC argument.
- It is written before every expensive ESBMC call that can be killed by the
  outer wrapper:
  - `outer-round-started` with `round_kind` of `level-0`,
    `geometric-bracket`, or `linear-refine`;
  - `certify-query-started` with the path id and box sent to certification.
- It is also written at `started`, `enumerated`, `coordinates-selected`,
  `no-witness`, `no-generalizable-coordinate`, and `complete`.
- The writer is atomic (`.tmp` then `os.replace`) and keeps only the last 40
  history events so result rows do not balloon during long shrink loops.
- `certify_all.py` now copies this sidecar into each JSONL row as
  `generalise_progress`, using the same mtime guard as the existing salvage
  readers.

Why this matters:

- The two sampled 120s/8GiB stress rows died after useful work had already been
  done.  Before this sidecar, the machine-readable row could say
  `enumeration_salvage` existed but could not reliably say whether the live
  budget was consumed in refine or certification.
- The next stratified benchmark run should therefore produce KILLED rows that
  identify their last stage, e.g. `outer-round-started` with
  `round_kind=linear-refine` or `certify-query-started`.
- This gives the scheduler enough evidence to decide whether a unit deserves a
  longer certification attempt, a cheaper fuzz/refutation pass, or a coordinate
  strategy change.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py notes/coverage/scripts/certify_all.py scripts/test_solidity_path_generalise.py scripts/test_certify_all_partial_journal.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `git diff --check -- scripts/solidity_path_generalise.py notes/coverage/scripts/certify_all.py scripts/test_solidity_path_generalise.py scripts/test_certify_all_partial_journal.py`
  passed.

Validation on one benchmark unit:

- Schedule:
  `/tmp/veriput_progress_validate_20260807_01/schedule.json`.
- Unit:
  `stress243__balancer__balancer-v3-monorepo__OwnableAuthentication.forceTransferOwnership`.
- Policy:
  `jobs=1`, 60s per ESBMC invocation, 70s driver timeout, 8GiB.
- Result:
  `/tmp/veriput_progress_validate_20260807_01/certify-results.jsonl`.
- Bucket stayed `KILLED`, as expected:
  `0 certified / 0 not / 1 witnessed`.
- The JSONL row carried both:
  - `enumeration_salvage`: `1 / 303` claims decided, `1` path, `8`
    witnesses;
  - `generalise_progress`: last stage `certify-query-started`, `enc=31`.
- The history tail showed that level-0 and linear-refine had both finished
  before the wrapper kill, and the live budget was consumed during the
  single-point/witness certification path rather than during witness discovery.
- The validation also exposed a harmless but confusing stale-key issue in the
  progress sidecar: the top-level event inherited keys from the prior event
  (e.g. `round_kind=linear-refine` while `stage=certify-query-started`).
  This was fixed immediately.  The sidecar top level is now only
  `{schema, history} + latest_event`; the full sequence remains in `history`.
  A unit test now checks that stale top-level keys are not retained.

Post-validation checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py notes/coverage/scripts/certify_all.py scripts/test_solidity_path_generalise.py scripts/test_certify_all_partial_journal.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `git diff --check -- scripts/solidity_path_generalise.py notes/coverage/scripts/certify_all.py scripts/test_solidity_path_generalise.py scripts/test_certify_all_partial_journal.py notes/VeriPUT_handoff_memory.md`
  passed.

## 2026-08-07 stratified benchmark sample

Read-only planning:

- Pipeline output:
  `/tmp/veriput_stratified_20260807_03`.
- Generated from the benchmark pipeline controller with:
  peer182 + bugfix124 + stress243, stress scope `stateful`, AST cache
  `/tmp/veriput_bench_ast_cache_20260806`.
- The full schedulable set under the existing cache was 76 unit jobs:
  15 bugfix124, 38 peer182, 23 stress243.
- The full denominator is still not ready:
  the unit manifest gate reports 473 missing AST rows and 37 prepared stress
  errors.  This is why the current action remains stratified sampling, not
  full benchmark execution.

Balanced attempt-1 sample:

- Schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced7.json`.
- Results:
  `/tmp/veriput_stratified_20260807_03/certify-results-balanced7.jsonl`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_03/unit-run-balanced7.jsonl`.
- Policy:
  attempt 1, `jobs=1`, 60s per ESBMC invocation, 70s driver timeout, 75s
  runner timeout, 8GiB.
- Units:
  - bugfix124 `DepositLog.approvedToLog`;
  - bugfix124 `EtherLotto.play`;
  - peer182 `AIRBets.transfer`;
  - peer182 `EtherBank.deposit`;
  - stress243 `ClaimTopicsRegistry.addClaimTopic`;
  - stress243 `IdentityRegistryStorage.bindIdentityRegistry`;
  - stress243 `IdentityRegistryStorage.storedIdentity`.

Results:

- Runner status: 7 / 7 `ok`.
- Buckets: 5 `CERTIFIED`, 1 `KILLED`, 1 `NO-WITNESS-UNDECIDED`.
- Certified units:
  - `DepositLog.approvedToLog`: 1 certified / 1 not / 2 witnessed, 2.8s.
  - `EtherLotto.play`: 1 / 2 / 3, 22.8s.
  - `EtherBank.deposit`: 1 / 0 / 1, 2.0s.
  - `ClaimTopicsRegistry.addClaimTopic`: 1 / 0 / 1, 65.7s.
  - `IdentityRegistryStorage.storedIdentity`: 1 / 1 / 2, 8.1s.
- Non-certified buckets:
  - `AIRBets.transfer`: `NO-WITNESS-UNDECIDED`, immediate.  This is still a
    peer witness-discovery/front-end modelling problem, not a region strategy
    result.
  - `IdentityRegistryStorage.bindIdentityRegistry`: `KILLED`, 1 witnessed,
    level-0 and both linear-refine rounds finished.  `generalise_progress`
    ended at `certify-query-started`, `enc=255`, so the 70s attempt was
    consumed in certification / witness-floor checking, not in enumeration or
    region refinement.

Certification summary:

- `certify_result_summary.py` over the balanced7 JSONL reports:
  - certified path rate: 0.5;
  - verdict path rate: 0.9;
  - certified region shapes: 3 wide, 2 point;
  - not-certified reason buckets: 2 empty-region, 2
    method-unsupported:static-uncontrolled.
- Interpretation: this sample is not broadly blocked by insertion failure.
  Most rows get verdicts; the main remaining losses are witness discovery for
  some peer contracts, method-level unsupported/static-uncontrolled splits, and
  certification timeout after a candidate region exists on stress.

Follow-up fix from this sample:

- In a certification-timeout row, `cov-ce-journal.json` may have been
  overwritten by the last certification query, so `partial_witness_journal`
  can describe `path:255#nonvacuous` instead of the original enumeration
  journal.
- `enumeration_salvage` remains the correct enumeration evidence because it is
  a sidecar written before certification.
- `certify_all.py` now annotates `partial_witness_journal` with:
  - `source_stage`;
  - `source_context`, either `path-enumeration-or-probe` or
    `certification-query`.
- This keeps the extra journal evidence useful while preventing a scheduler
  from treating a certification-query journal as complete-path enumeration
  progress.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/certify_all.py scripts/test_certify_all_partial_journal.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_all.py scripts/test_certify_all_partial_journal.py`
  passed.

## 2026-08-07 progress-aware summaries and campaign planning

Change retained:

- `certify_result_summary.py` now aggregates the `generalise_progress` field:
  - `progress_rows`: all latest rows by last progress bucket;
  - `noncert_progress_rows`: non-`CERTIFIED` rows by last progress bucket;
  - `no_verdict_progress_paths`: witnessed-but-no-verdict path gaps, weighted
    by path count, by last progress bucket.
- The progress bucket names are stable:
  - `certification:certify-query-started`;
  - `certification:certify-query-finished`;
  - `outer-round-started:<round_kind>`;
  - `outer-round-finished:<round_kind>`;
  - ordinary stages such as `started`, `complete`, `no-witness`.
- Non-certified samples now carry `progress_bucket` and `progress_stage`.
- `unit_campaign_plan.py` now reads the same progress signal when judging weak
  certification quality.  If a row has witnessed no-verdict paths and the last
  progress bucket is a certification query, its weak reason is now
  `certification-stage no verdict` instead of the generic
  `certified path rate below threshold`.
- Historical rows without progress keep the old behavior and use
  `<missing-progress>`.

Validated on the balanced7 sample:

- New summary path:
  `/tmp/veriput_stratified_20260807_03/certify-summary-balanced7-progress.json`.
- Summary fields:
  - `bucket_rows`: 5 `CERTIFIED`, 1 `KILLED`, 1 `NO-WITNESS-UNDECIDED`;
  - `progress_rows`: 5 `complete`, 1 `started`, 1
    `certification:certify-query-started`;
  - `noncert_progress_rows`: 1 `started`, 1
    `certification:certify-query-started`;
  - `no_verdict_progress_paths`: 1 `certification:certify-query-started`.
- The KILLED sample is directly visible as:
  `IdentityRegistryStorage.bindIdentityRegistry`,
  `progress_bucket=certification:certify-query-started`.

Balanced7 campaign re-plan:

- Plan path:
  `/tmp/veriput_stratified_20260807_03/unit-campaign-balanced7-progress.json`.
- Next attempt schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced7-a2.json`.
- With certification quality enabled, the 7-row sample splits as:
  - `completed_ok`: 2 strong units;
  - `pending_by_attempt`: 5 for attempt 2.
- Weak reasons:
  - `certification-stage no verdict`: 1;
  - `certified path rate below threshold`: 3;
  - `no certified regions`: 1.
- The attempt-2 schedule correctly uses the second budget:
  `run_timeout_s=120`, `timeout_s=130`, `memlimit_gib=8`, `jobs=1`.

Interpretation:

- We can now decide the next experiment mechanically instead of by reading logs:
  - `certification-stage no verdict` rows are candidates for attempt 2/3 or a
    cheaper certification-specific refutation/fuzz pass.
  - `started` / `no certified regions` rows are witness-discovery/modeling
    candidates.
  - `certified path rate below threshold` rows already produce tests but are
    not strong enough for the >=70% target; they need stronger R1/R2, not just
    more time.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_certify_result_summary.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_result_summary.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_certify_result_summary.py scripts/test_unit_campaign_plan.py`
  passed.

## 2026-08-07 Peer string constructor and CE-journal salvage fixes

Problem found while diagnosing Peer `AIRBets.approve`:

- Pre-fix 600s Peer smoke had `AIRBets.approve` and `Arcadia_Token.approve`
  as `NO-PATH` / all bounded-holds.
- `--show-vcc --claim 679` for `AIRBets.approve:path:15` showed false
  constructor assumptions before the transaction:
  `_str_assign(&this->_name, "AIRBets")` and `_str_assign(&this->_symbol,
  "AIRBets")` were cut by the default path-coverage unwind bound.
- A focused `--unwind=16` run immediately made `approve:path:15` feasible,
  confirming this was ESBMC string-model unwinding, not a PUT region failure.

Code changes:

- `src/c2goto/library/solidity/solidity_string.c`:
  `_str_assign` now keeps the old 64-byte capped deep-copy semantics but
  expresses the copy as explicit guarded steps instead of two loops.  This
  avoids constructor string assignments depending on the global path-coverage
  unwind bound and prevents vacuous unit path claims.
- `src/esbmc/bmc.cpp`:
  path-coverage CE payloads are now published to `path_ce` /
  `cov-ce-journal.json` immediately after the first witness is harvested,
  before extra `--all-witnesses` blocking-clause enumeration.  If later witness
  enumeration OOMs or times out, the concrete member already found by ESBMC is
  still salvageable by `solidity_path_generalise.py`.
- `src/esbmc/bmc.cpp`:
  probe blocker kept/dropped counters now count only actual probe claims.  The
  replayable-nondet filtering still applies to path claims, but no longer
  pollutes `--path-cov-probe` metrics for units with no probe goals.

Focused validation:

- `cmake --build build --target esbmc -j2` passed.
- `AIRBets.approve`, default unwind, 120s / 8GiB:
  `/tmp/veriput_airbets_strassign_fix_120_1786061324`.
  It changed from all bounded-holds to 1 witnessed path (`enc=15`, depth 3).
- After first-witness journal salvage, a 60s / 8GiB run:
  `/tmp/veriput_airbets_journal_salvage_1786061606`
  salvaged 1 path from partial `cov-ce-journal.json` (`3/35` claims decided).
  The salvaged payload included `spender=1`, `amount=0`, `msg.value=0`,
  environment pins, and entry storage.  Region refinement then expanded the
  body path to:
  - `amount = [0, uint256.max]`
  - `spender = [1, address.max]`
  plus body-slice pins.
- This run reached `certify-query-started` before the 60s budget ended, so it
  demonstrates payload/region recovery but not final certification for this
  unit.

Regression checks:

- String-related Solidity checks passed:
  `contract_var_1`, `contract_var_2`, `esol_clone_string_pass`, `mapping_4`,
  `tod_race_ctor_args_pass`.
- Path/journal/probe checks passed:
  `solidity_path_cov_ce_journal_survives_death`,
  `solidity_path_cov_nondet_string_no_prefill_loop`,
  `solidity_path_cov_partial_report_on_oom`,
  `solidity_path_cov_partial_report_on_signal`,
  `solidity_path_cov_probe_fire`,
  `solidity_path_cov_probe_silent`,
  `solidity_path_cov_verdict_survives_mid_witness_death`.
- `git diff --check -- src/c2goto/library/solidity/solidity_string.c src/esbmc/bmc.cpp`
  passed.
- `cppcheck` over changed Solidity frontend files was a no-op because this
  patch touches no `src/solidity-frontend/*.cpp` or `*.h` files.

Current benchmark intuition before a new broad rerun:

- Existing 9-case smoke at
  `/tmp/veriput_current_smoke_20260807_065046`:
  4/9 units certified (44.4%).
- Stage4 over certified regions produced 13/13 reference-valid tests:
  9/13 strict PUT/B tests (69.2%) and 4/13 concrete replay fallbacks (30.8%).
- The new fixes are expected mainly to improve Peer-style token contracts that
  have string name/symbol constructors and were previously vacuous before any
  region logic ran.

## 2026-08-07 ESBMC array-decay and Solidity string OM fix

Problem:

- Fresh BugFix hinted runs for `Owned.owned` crashed before producing
  `cov-report.json`.
- Representative old log:
  `/tmp/veriput_fresh_wave_plan_20260807_061251/certify-work-t600_r600_m8/bugfix-cert-hinted/bugfix124__acfix_026_CVE_2019_15080/owned/driver.log`.
- ESBMC aborted in `goto_symext::argument_assignments` with:
  `function call: argument "c:string.c@4751@F@memset@s" type mismatch: got array, expected pointer`.
- Root cause:
  symex-time parameter binding allowed number/pointer casts, but did not apply
  C array-to-pointer decay when the formal was a pointer and the actual was an
  array.  The C typecast layer already knew how to lower this to `&arr[0]`.

Code changes:

- `src/goto-symex/symex_function.cpp`
  - Include `util/c_typecast.h`.
  - In parameter binding, handle the narrow case
    `formal pointer && actual array` with `c_implicit_typecast`.
  - Other incompatible argument shapes still abort as before.
- `regression/cstd/function_array_decay_to_void_ptr/`
  - New regression for passing a stack array to a `void *` parameter.
- `regression/esbmc-solidity/solidity_path_cov_nondet_string_no_prefill_loop/`
  - New regression requiring string-argument path claims to reach the solver
    without the old pre-dispatch `__memset_impl` loop blocker.
- `src/c2goto/library/solidity/solidity_string.c`
  - Removed the pre-dispatch `memset(_ESBMC_rand_str, 0, 33)` from
    `nondet_string`.
  - The model now fills the first `len` bytes with non-NUL nondet chars and
    writes `_ESBMC_rand_str[len] = '\0'`.
  - This preserves bounded `strlen` termination for the current call and avoids
    an avoidable `__memset_impl` loop that can be truncated before path
    coverage claims reach the solver.

Validation:

- Build:
  `cmake --build build --target esbmc -j2` passed after both patches.
- Formatting/check:
  `git diff --check -- src/goto-symex/symex_function.cpp src/c2goto/library/solidity/solidity_string.c regression/cstd/function_array_decay_to_void_ptr/main.c regression/cstd/function_array_decay_to_void_ptr/test.desc`
  passed.
- Regression:
  `cd build && ctest -R function_array_decay_to_void_ptr --output-on-failure`
  passed.
- Solidity regression:
  `cd build && ctest -R solidity_path_cov_nondet_string_no_prefill_loop --output-on-failure`
  passed.
- Direct ESBMC regression:
  `build/src/esbmc/esbmc regression/cstd/function_array_decay_to_void_ptr/main.c --unwind 2 --memlimit 8g --result-only`
  produced `VERIFICATION SUCCESSFUL`.
- Target BugFix re-check with final binary:
  `/tmp/veriput_memset_fix_check_20260807c/acfix026-cert.jsonl`.
  Result changed from crash/unknown to a normal bounded no-witness result:
  `NO-PATH`, with `cov-report.json` present.
- New target workdir:
  `/tmp/veriput_memset_fix_check_20260807c/work/acfix026-cert/bugfix124__acfix_026_CVE_2019_15080/owned`.
- New driver outcome:
  3 path claims for `Owned.owned`, all `bounded-holds`; no
  `ESBMC produced no cov-report.json`, no type mismatch, and no
  path-coverage 0-claim internal defect.

Interpretation:

- This does not make `Owned.owned` produce a PUT.  It removes an ESBMC/OM
  blocker that made the result unusable.
- The remaining `NO-PATH` is a bounded result under
  `--solidity-max-tx 1` / default path-coverage unwind.  It should be handled
  by scheduling/region strategy rather than by treating it as a crash.
- The next separate bottleneck is Peer recursive-helper/no-witness preflight,
  especially `SafeMath.div/2` and `SafeMath.sub/2` in AIRBets/Arcadia transfer
  runs.

## 2026-08-07 wave600 quick benchmark smoke

Purpose:

- User set the final per-case ESBMC timeout to 600s.
- Need a fast intuition for current success rate:
  - unit-level end-to-end: scheduled unit reaches certified regions and then
    produces reference-valid tests;
  - region-level generator: certified region becomes a reference-valid test;
  - split generated tests into strict PUT versus concrete replay fallback.
- Datasets were not modified.  All outputs are under `/tmp`.

Inputs and budgets:

- Root:
  `/tmp/veriput_wave600_20260807`.
- Schedules:
  - `/tmp/veriput_wave600_20260807/peer-schedule.json`
  - `/tmp/veriput_wave600_20260807/bugfix-schedule.json`
  - `/tmp/veriput_wave600_20260807/stress-schedule.json`
- Stage 2 budget embedded in every job:
  `--timeout 600 --run-timeout 600 --memlimit-gib 8`.
- Runner used `--timeout 700 --memlimit-gb 8` only to leave wrapper cleanup
  time around the 600s ESBMC budget.
- Jobs were serial (`--jobs 1`).

Sampling caveat:

- This was a speed smoke, not a statistically clean corpus estimate.
- The schedules were priority ordered, so Peer and BugFix each sampled several
  units from the same first subject:
  - Peer: `peer182__peer_ccsolbmc__AIRBets`.
  - BugFix: `bugfix124__acfix_002_Templedao`.
- Stress sampled mostly `ERC-3643` role contracts.
- A better next smoke should use `--selection-strategy round-robin-subject`
  for breadth.

Stage 2 results:

- Total units: 12.
- Buckets:
  - `CERTIFIED`: 4.
  - `NO-WITNESS-UNDECIDED`: 3.
  - `NO-WITNESS-UNKNOWN`: 4.
  - `NO-PATH`: 1.
- Certified unit-level rate in this smoke: `4 / 12 = 33.3%`.
- Certified regions: 16.
- Witnessed paths: 20.
- Not-certified paths: 4, all from the Stress certified units.

Per-suite detail:

- Peer:
  - Cert file: `/tmp/veriput_wave600_20260807/peer-cert.jsonl`.
  - 0 / 4 certified units.
  - `AIRBets.transfer`, `transferFrom`, `setMaxTxPercent`:
    `NO-WITNESS-UNDECIDED`.
  - `AIRBets.approve`: `NO-PATH`.
- BugFix:
  - Cert file: `/tmp/veriput_wave600_20260807/bugfix-cert.jsonl`.
  - 0 / 4 certified units.
  - `StaxLPStaking.setRewardDistributor`, `addReward`, `stake`,
    `stakeFor`: all `NO-WITNESS-UNKNOWN`.
- Stress:
  - Cert file: `/tmp/veriput_wave600_20260807/stress-cert.jsonl`.
  - 4 / 4 certified units.
  - `AgentRole.addAgent`, `AgentRole.removeAgent`,
    `AgentRole.transferOwnership`, `AgentRoleUpgradeable.addAgent`:
    each 4 certified regions / 1 not-certified path / 5 witnessed paths.

Stage 4 results on the 16 Stress certified regions:

- Command:
  `python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_wave600_20260807/stress-cert.jsonl --out-root /tmp/veriput_wave600_20260807/stress-put --scope focus --max-tx 1 --timeout 600 --memlimit-gib 8 --strong-recipe`
- Summary:
  `/tmp/veriput_wave600_20260807/stress-put/put-summary.json`.
- Reference-valid generated tests:
  `16 / 16` certified regions.
- Split:
  - 12 strict PUT (`B = 12 / 16 = 75%`).
  - 4 concrete replay fallback.
- Forge:
  - PUTs: 12 green / 12 total.
  - Concrete replays: 18 green / 18 total.
- Valid generated-test split:
  `12 PUT / 4 concrete`, all reference-valid on the unmodified contract.

Interpretation:

- Once Stage 2 produces certified regions, the current Stage 4 emitter is
  strong on this smoke: 100% reference-valid, 75% strict PUT.
- The current bottleneck is still earlier:
  - Peer has recursive/helper or undecided witness-entry issues.
  - BugFix has NO-WITNESS-UNKNOWN and separately observed `memset` array /
    pointer mismatch crashes on hinted `Owned.owned` targets.
- Therefore the next high-leverage work is Stage2 entry/instrumentation and
  ESBMC internal modeling, not overfitting the PUT emitter to Stress.
- Fuzz remains refutation-only: the Stage4 R2 Foundry prefilter dropped
  candidate assertions by concrete failures, but every survivor counted as PUT
  still went through ESBMC proof before being emitted.

## 2026-08-07 current 9-case breadth smoke after ESBMC string fix

Purpose:

- User requested a faster current intuition for benchmark success under the
  final 600s per-case budget:
  - reference-valid generated tests on the reference contract;
  - split between concrete replay and strict PUT;
  - quick diagnosis of where unsuccessful units stop.
- Outputs only under `/tmp`; no Dataset or Results contract was modified.

Inputs:

- Root:
  `/tmp/veriput_current_smoke_20260807_065046`.
- Reused existing compact AST cache/manifest:
  `/tmp/veriput_sample_v10_20260806_212550`.
- Schedules:
  - Peer:
    `/tmp/veriput_current_smoke_20260807_065046/peer182-schedule.json`.
  - BugFix:
    `/tmp/veriput_current_smoke_20260807_065046/bugfix124-schedule.json`.
  - Stress:
    `/tmp/veriput_current_smoke_20260807_065046/stress203-schedule.json`.
- Each schedule selected 3 units with `round-robin-subject`, using
  `--timeout 600 --run-timeout 600 --memlimit-gib 8`; runner was serial with
  `--timeout 700 --memlimit-gb 8 --jobs 1`.

Stage 2 result:

- Total units: 9.
- Buckets:
  - `CERTIFIED`: 4.
  - `NO-WITNESS-UNDECIDED`: 3.
  - `NO-PATH`: 1.
  - `NOT-CERTIFIED`: 1.
- Unit-level certified rate in this breadth smoke: `4 / 9 = 44.4%`.
- Certified regions: 13.
- Not-certified paths: 5.
- Known witnessed paths: 18.

Per-suite:

- Peer: 0 / 3 certified.
  - `AIRBets.transfer` and `Arcadia_Token.transfer` were refused before ESBMC
    by the recursive-helper preflight (`SafeMath.div/2`, `SafeMath.sub/2`).
  - `Animalia.transfer` ran about 116s and still ended
    `NO-WITNESS-UNDECIDED`.
- BugFix: 1 / 3 certified.
  - `DepositLog.approvedToLog`: `CERTIFIED`, 1 certified region, 1
    not-certified path, about 3s.
  - `DnGmxBatchingManager.executeBatchDeposit`: `NO-PATH`, about 443s.
  - `MStableYieldSource.supplyTokenTo`: `NOT-CERTIFIED`, 1 not-certified path,
    about 445s.
- Stress: 3 / 3 certified.
  - `AgentRole.addAgent`, `AgentRole.removeAgent`,
    `AgentRoleUpgradeable.addAgent`.
  - Each yielded 4 certified regions and 1 not-certified path; wall time was
    about 54-78s per unit.

Stage 4 result on the 13 certified regions:

- Command:
  `python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_current_smoke_20260807_065046/combined-cert.jsonl --out-root /tmp/veriput_current_smoke_20260807_065046/put-all --scope focus --max-tx 1 --timeout 600 --memlimit-gib 8 --strong-recipe`.
- Summary:
  `/tmp/veriput_current_smoke_20260807_065046/put-all/put-summary.json`.
- Reference-valid generated tests: `13 / 13`.
- Split:
  - strict PUT/B: `9 / 13 = 69.2%`;
  - concrete replay fallback: `4 / 13 = 30.8%`.
- Forge gate:
  - PUTs: 9 green / 9 total.
  - Concrete replays: 10 green / 10 total.

Interpretation:

- Compared with the earlier biased wave600 smoke, unit-level success improved
  from 4/12 to 4/9 mainly because BugFix now has one fast certified unit; Peer
  remains 0/3.
- The Stage4 emitter remains strong once Stage2 certifies a region: all 13
  certified regions generated reference-valid tests, and 9 of them were strict
  PUT/B.
- Current bottlenecks are still Stage2:
  - Peer recursive-helper preflight is over-conservative for flattened
    SafeMath wrappers;
  - BugFix has expensive no-path/not-certified cases around entry/path region
    selection rather than Stage4 emission;
  - Stress ERC-3643 remains the easy green subset.
- Stage4 logs show some ladder repair via named-loop auto-unwind and
  `--partial-loops` for upgradeable stress paths; that is separate from the
  fixed `nondet_string` prefill blocker.

## 2026-08-07 static-obstacle-aware unit scheduling

Problem:

- The breadth smoke exposed that Peer scarce attempts were being spent on
  units the Stage2 driver would refuse before ESBMC:
  `AIRBets.transfer` and `Arcadia_Token.transfer` both hit the recursive-helper
  preflight for `SafeMath.div/2` and `SafeMath.sub/2`.
- Inspecting the prepared Peer sources and compact ASTs showed this is not a
  benign overload-resolution false positive.  The flattened source has
  two-argument wrappers like `return sub(a, b);//...`, and the AST's
  `referencedDeclaration` points back to the same two-argument function.
  Allowing enumeration would likely spend the 600s budget expanding the
  recursive wrapper.

Code change:

- `notes/coverage/scripts/unit_schedule.py`
  - Imports the existing `direct_recursive_helpers_in_unit_closure` AST
    preflight from `scripts/solidity_path_generalise.py`.
  - While expanding a unit manifest, each job gets `static_obstacles`.
  - Jobs whose target call closure reaches a direct self-recursive helper are
    still scheduled, but get priority `4` with reason `static-obstacle`.
  - The schedule summary now records `static_obstacle_jobs` and
    `static_obstacles_by_tag`.
- `scripts/test_unit_schedule.py`
  - Added a compact AST fixture where `transfer` reaches `SafeMath.sub/2`,
    while `approve` is normal.
  - The test asserts `approve` is scheduled before `transfer`, and that the
    recursive-helper label is retained for audit.

Validation:

- Python:
  `python3 -m py_compile notes/coverage/scripts/unit_schedule.py scripts/test_unit_schedule.py`
  passed.
- Unit schedule tests:
  `python3 scripts/test_unit_schedule.py` passed.
- Formatting/check:
  `git diff --check -- notes/coverage/scripts/unit_schedule.py scripts/test_unit_schedule.py`
  passed.
- Real Peer schedule smoke:
  `python3 notes/coverage/scripts/unit_schedule.py /tmp/veriput_sample_v10_20260806_212550/unit-manifest-peer182.json --selection-strategy round-robin-subject --limit 8 --timeout 600 --run-timeout 600 --memlimit-gib 8 --cert-out /tmp/veriput_static_obstacle_check/peer-cert.jsonl --workdir /tmp/veriput_static_obstacle_check/work --out /tmp/veriput_static_obstacle_check/peer-schedule.json`
  changed the first 8 jobs to avoid the known recursive-helper transfer units:
  `AIRBets.approve`, `Address.approve`, `Animalia.transfer`, ...
- Full Peer schedule over that 3-subject cached manifest:
  - total jobs: 70;
  - static-obstacle jobs: 12;
  - priority distribution: 23 state-changing, 35 interface/zero-interface,
    12 static-obstacle.
- One verification run from the new queue head:
  `python3 notes/coverage/scripts/unit_schedule_run.py /tmp/veriput_static_obstacle_check/peer-schedule.json --journal /tmp/veriput_static_obstacle_check/peer-run.jsonl --limit 1 --timeout 700 --memlimit-gb 8 --jobs 1`.
  Result: `AIRBets.approve` no longer stopped at recursive-helper preflight;
  it completed in about 1.1s as `NO-PATH`.

Interpretation:

- This is a speed/sampling improvement, not a proof-strength improvement by
  itself.  It prevents scarce early campaign batches and smoke tests from
  spending their first slots on units known to be structurally refused before
  ESBMC.
- The affected units remain in the schedule and denominator; they are not
  silently dropped.
- Peer still has a separate Stage2 entry/path issue after the recursive-helper
  units are avoided.  `AIRBets.approve` is now measurable but came back
  `NO-PATH`, so the next Peer work is path-entry/region diagnosis rather than
  disabling the recursive-helper guard.

## 2026-08-07 relation-establish PUT alignment

Problem fixed:

- Stage 2 could now certify a wide region by establishing an entry relation,
  e.g. `state._owner := msg.sender`, instead of pinning `state._owner` to one
  concrete value.
- The first implementation propagated that relation into the generated Foundry
  PUT, but not into `--path-cov-assert`.
- Result: the assertion ladder proved post-state rungs over constructor entry
  state while the emitted PUT replayed a relation-established entry state.  On
  `ClaimTopicsRegistry.renounceOwnership` enc=7 this produced a RED PUT:
  ladder said `_owner: post == pre HOLDS`; the PUT stored
  `state._owner := msg.sender`, so the real post-state was zero and the fuzzed
  nonzero sender refuted the assertion.

Code changes in this slice:

- `src/goto-programs/goto_coverage.cpp`
  - `--path-cov-assert` parses an optional `establish` array with the same
    `{"target":"state.<x>","source":"<coord>"}` shape as certification.
  - The assert ladder inserts relation-backed entry `ASSIGN` instructions
    before region assumptions and before pre/post snapshots.
  - It refuses unresolvable, non-state-target, non-expressible, or type-mismatched
    relations rather than silently proving a different entry slice.
- `scripts/solidity_path_put.py`
  - The base ladder spec now includes `establish` when present; R2 specs inherit
    it because they derive from the base spec.
- `notes/coverage/scripts/certify_all.py`
  - Stage 2 rows record machine-readable `certified_details` from
    `generalise-result.json`.
- `notes/coverage/scripts/put_all.py`
  - Stage 4 reads `certified_details[*].established` and passes it to PUT
    emission.
  - Strong recipe v15+ refuses rows whose machine-readable `certified_details`
    entry is missing, because relation establishment is not recoverable from
    the prose region string.
- `scripts/solidity_path_generalise.py`
  - Relation-aware structural regions can keep the source coordinate wide and
    drop the state target from the product box.
  - Certified-region overlap checks account for relation equalities.
  - `--pin-agreed-state` skips state coordinates that a complete-path equality
    can establish from a rendered non-state coordinate.
- `notes/coverage/scripts/veriput_recipe.py`
  - Strong recipe version bumped to `veriput-strong/15-relation-establish`.

Validation:

- Python:
  - `python3 -m py_compile scripts/solidity_path_generalise.py scripts/solidity_path_put.py notes/coverage/scripts/certify_all.py notes/coverage/scripts/put_all.py notes/coverage/scripts/veriput_recipe.py scripts/test_solidity_path_generalise.py scripts/test_solidity_path_put.py`
    passed.
  - `python3 scripts/test_solidity_path_generalise.py` passed.
  - `python3 scripts/test_solidity_path_put.py` passed, 233 tests.
- C++:
  - `clang-format -i src/goto-programs/goto_coverage.cpp`.
  - `cmake --build build --target esbmc -j2` passed.
  - Existing Solidity model array-bounds warnings still appear while generating
    `sol64.goto`; no new compile error.
- Whitespace:
  - `git diff --check` passed.

Targeted relation smoke:

- Stage-2 cert input:
  `/tmp/veriput_relation_smoke3_20260807_051708/certify-renounce.jsonl`.
- Stage-4 rerun after this fix:
  `/tmp/veriput_relation_put4_20260807_052300/put_all.log`.
- `ClaimTopicsRegistry.renounceOwnership`:
  - Stage 2: 3 witnessed paths, 2 certified, 1 not-certified slice-excluded.
  - enc=6 remains rollback/exit-kind PUT, fuzzes `msg.sender`, GREEN.
  - enc=7 now carries `established=[{"target":"state._owner","source":"msg.sender"}]`.
  - The fixed ladder changed from `_owner: post == pre HOLDS` to
    `_owner: post <= pre HOLDS`; Forge gate is GREEN.
  - B changed from 1/2 in the stale stage4 run to 2/2 with the fixed binary.

Peer source_080 micro-sample:

- Stage-2 output root:
  `/tmp/veriput_peer_sample_cert_20260807_052523`.
- Stage-4 output root:
  `/tmp/veriput_peer_sample_put_20260807_052658`.
- Settings:
  `--timeout 600 --run-timeout 600 --memlimit-gib 8`, strong recipe v15,
  `--scope focus --max-tx 1`, no writes to `Datasets` or `Results`.
- Sampled units:
  - `peer_ccsolbmc__MayoOcho.transfer`: CERTIFIED in 83s,
    5 witnessed paths, 4 certified, 1 not-certified.  Stage 4 emitted 4 PUTs;
    all 4 carry fuzz parameters and oracles; all 4 are Forge-green B rows.
    enc=63 is the strongest: 3 fuzz params and 17 oracle asserts, including
    R2/source-assignment-shaped state assertions.
  - `peer_syntest__Straight_Fire_Finance.transfer`: NO-PATH in 0s.
  - `peer_ccsolbmc__PORCUPINE.renounceOwnership`: NO-PATH in 1s.

Current small-wave interpretation:

- Over certified regions actually passed to Stage 4 in this slice:
  - relation smoke: 2/2 valid PUTs, 0 concrete replay tests.
  - peer source_080 sample: 4/4 valid PUTs, 0 concrete replay tests.
  - combined Stage-4 measured certified-region rows: 6/6 valid PUTs, all PUT,
    no concrete replay-only successes.
- Over the three peer units sampled, only one unit produced witnessed paths; do
  not quote this as a corpus success rate.  It is a sanity sample showing that
  when Stage 2 certifies a benchmark transfer unit, Stage 4 can now emit strong
  fuzzed PUTs rather than only point/concrete replay tests.

Next likely bottlenecks:

- Units with NO-PATH need stage-1/coverage-entry diagnosis, not PUT synthesis.
- Transfer-style paths still often become rollback/exit-kind PUTs; strong
  semantic post-state oracles appear on normal exits such as MayoOcho enc=63.
- More benchmark sampling should stratify by dataset difficulty:
  peer/source_080 first, then BugFix124, then Stress243.  Keep outputs under
  `/tmp`; do not modify `/home/samson/workspace/VeriPUT/Datasets` or the
  shared Results contracts.

## 2026-08-07 Stage-4 machine-readable B summary

Speed/accounting change:

- `notes/coverage/scripts/put_all.py` now writes `<out-root>/put-summary.json`
  at the end of every Stage-4 run.
- The summary contains:
  - Stage-2 path accounting for the selected rows;
  - Stage-4 emission counters (`puts_emitted`, fuzz/oracle/both);
  - cell labels and mixed-cell flag;
  - deliverable-B totals;
  - per-certified-region gate values, forge status, widths, assert counts,
    stale/refused flags, and emitted file path.
- This removes the need to parse `put_all.log` by hand when sampling
  benchmarks.  The source of truth for quick success-rate reporting is now
  `put-summary.json`.

Validation:

- `python3 -m py_compile notes/coverage/scripts/put_all.py scripts/test_put_all_accounting.py`
  passed.
- `python3 scripts/test_put_all_accounting.py` passed.
- `git diff --check -- notes/coverage/scripts/put_all.py scripts/test_put_all_accounting.py`
  passed.
- Read-only real replay of the MayoOcho Stage-4 sample:
  `python3 notes/coverage/scripts/put_all.py ... --forge-only` wrote
  `/tmp/veriput_peer_sample_put_20260807_052658/put-summary.json`.
- That JSON reports:
  - `stage2.witnessed = 5`;
  - `stage2.certified = 4`;
  - `emission.puts_emitted = 4`;
  - `deliverable_b.b = 4`;
  - `deliverable_b.certified_region_rows = 4`;
  - `deliverable_b.forge_seen.put.Success = 4`;
  - `deliverable_b.forge_seen.concrete.Success = 0`.

NO-PATH interpretation from the peer source_080 micro-sample:

- `peer_syntest__Straight_Fire_Finance.transfer` and
  `peer_ccsolbmc__PORCUPINE.renounceOwnership` both finished in under 1s with
  `NO-PATH`.
- Their JSONL rows say enumeration found the focus unit but witnessed zero
  paths; all 3 claims were `bounded-holds`.
- Treat these as Stage-1/focus-cell no-witness outcomes, not Stage-4 PUT
  failures and not evidence that the PUT emitter failed.  Re-running the same
  focus/max-tx=1 cell with 600s is not useful unless the campaign deliberately
  changes the cell, e.g. max-tx/unwind/scope.

Commit:

- `4f2223fb8a [solidity] Summarize VeriPUT PUT gates`, pushed to
  `E-SOL/feat/veriput-fuzz-first`.

## 2026-08-07 Stage-4 normal-exit arithmetic retreat

User policy update:

- Final per-case timeout should be understood as 600s maximum.
- For speed, keep sampling small and stratified; use `/tmp` outputs only.
- Fuzz remains refutation-only. It can cheaply expose a bad region, bad
  insertion, or bad oracle, but never upgrades a survivor to proved. ESBMC is
  still the proof authority.

Current small-wave PUT picture:

- After the latest pushed path-decision guard work, the sampled Stage-4
  benchmark/peer rows were already:
  - small-wave rows: 7 / 7 Forge-valid PUTs;
  - `BasicToken.transfer`: 1 / 1 Forge-valid PUT;
  - `MetaCoin.sendCoin`: 2 / 2 Forge-valid PUTs;
  - store-state setter sample: 1 / 1 Forge-valid PUT.
- The problematic peer `return_1.add` sample was initially only 2 / 5:
  the failing generated PUTs were all normal-return arithmetic paths where
  Solidity checked arithmetic could revert inside the supposedly normal region.
- After the arithmetic retreat patch below, `return_1.add` became 5 / 5:
  `/tmp/veriput_put_peer_pair_retreat2_20260807`.
- Aggregate sampled intuition after the patch is 16 / 16 Forge-visible PUTs,
  all with PUT shape and 0 concrete replay-only rows.  This is not a full
  benchmark success rate; it is a small, biased smoke sample used to decide
  whether broader sampling is worth spending.

Root cause and fix:

- Stage 2 had certified product boxes for normal-exit paths, but the product
  box could include values where the selected source return expression reverts
  under checked arithmetic:
  - `return ++x` needs `x <= UINT256_MAX - 1`;
  - `return x + 2` needs `x <= UINT256_MAX - 2`;
  - `return x + y` cannot be represented exactly as a rectangular product
    region, so the current conservative retreat keeps the wide `y` fuzz
    coordinate and pins/narrows `x` to a product-safe slice.
- `scripts/solidity_path_put.py` now applies
  `normal_exit_region_retreat()` before Stage-4 ladder/R2 certification.
  Therefore the generated Foundry PUT and the ESBMC ladder/R2 proof use the
  same narrowed region.
- Source-prioritized R2 extraction now recognizes prefix `++` / `--` return
  expressions, so `return ++x` can ask the semantic candidate
  `return == (x + 1)`.

Validation:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed: 232 / 232 tests.
- Targeted peer pair command:
  `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_peer_pair_certify-results.jsonl --strong-recipe --timeout 600 --memlimit-gib 8 --forge-timeout 300 --out-root /tmp/veriput_put_peer_pair_retreat2_20260807`.
- Result:
  5 / 5 Forge-valid PUTs, all fuzz+oracle, concrete replay 0 / 0.

Next recommended action:

- Commit and push this patch, then run a slightly broader stratified sample
  across peer / bugfix / stress with the 600s maximum policy.  Do not run a full
  benchmark sweep yet; use the next sample to estimate whether current PUT
  validity and PUT-vs-replay ratios are stable outside the easy peer slice.

## 2026-08-07 balanced campaign sampling and new bottlenecks

Campaign-speed code change:

- `notes/coverage/scripts/unit_campaign_plan.py` now accepts
  `--selection-strategy` and `--limit` for the selected next-attempt set.
- `--selection-strategy round-robin-benchmark --limit N` is meant for quick
  small-wave sampling: it avoids spending the first N jobs on one benchmark or
  one subject cluster.
- The planner rewrites each selected job's `ordinal` after ordering.  This is
  necessary because `unit_schedule_run.py` sorts by `(priority, ordinal)`;
  without ordinal normalization the runner would undo the planner's balanced
  order.
- Validation:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
    passed.
  - `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
    passed.
  - `git diff --check -- notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
    passed.

Balanced6 attempt-1 sample:

- New output root:
  `/tmp/veriput_stratified_20260807_04`.
- Rebuilt a base schedule from the cached manifest, writing only under `/tmp`:
  `/tmp/veriput_stratified_20260807_04/unit-schedule-base.json`.
- Planned pending attempt-1 jobs with:
  `--selection-strategy round-robin-benchmark --limit 6`.
- Next schedule:
  `/tmp/veriput_stratified_20260807_04/next-unit-schedule-balanced6-a1.json`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_04/unit-run-balanced6-a1.jsonl`.
- Certification JSONL:
  `/tmp/veriput_stratified_20260807_04/certify-results-balanced6-a1.jsonl`.
- Policy:
  attempt 1, serial, outer runner timeout 75s, certify timeout 70s,
  ESBMC run-timeout 60s, 8GiB.
- Job balance:
  2 peer / 2 bugfix / 2 stress.

Balanced6 certification result:

- Runner status:
  6 / 6 `ok`.
- Certification buckets:
  - `CERTIFIED`: 1 unit (`ClaimTopicsRegistry.renounceOwnership`);
  - `NOT-CERTIFIED`: 2 units (`DepositLog.logRedemptionRequested`,
    `DepositLog.logGotRedemptionSignature`);
  - `KILLED`: 1 unit (`ClaimTopicsRegistry.init`, 60s);
  - `NO-PATH`: 1 unit (`AIRBets.initialize2`);
  - `NO-WITNESS-UNDECIDED`: 1 unit (`AIRBets.transferFrom`, recursive
    SafeMath helper preflight; no ESBMC process started for the witness).
- Certification summary:
  `/tmp/veriput_stratified_20260807_04/certify-summary-balanced6-a1.json`.
- Raw certified unit rate:
  1 / 6.
- Raw certified path rate:
  2 / 7.
- Slice-adjusted and retry-adjusted certified path rate:
  2 / 4 = 0.5.

Balanced6 Stage-4 PUT result:

- Command:
  `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_stratified_20260807_04/certify-results-balanced6-a1.jsonl --strong-recipe --timeout 600 --memlimit-gib 8 --forge-timeout 300 --out-root /tmp/veriput_put_balanced6_a1_20260807_04`.
- Certified regions:
  2, both from `ClaimTopicsRegistry.renounceOwnership`.
- PUT result:
  - enc=6: B, fuzz=1 (`msg.sender`), oracle asserts=1, Forge green.
  - enc=7: refused as not parameterized.
- Stage-4 B:
  1 / 2 certified region rows.
- Forge-visible PUTs:
  1 green / 1 total.
- Concrete replay-only:
  0 / 0.

New bottlenecks exposed by this sample:

- `DepositLog.logRedemptionRequested` and
  `DepositLog.logGotRedemptionSignature` are not Stage-4 insertion failures.
  Their body paths use dynamic bytes length coordinates such as
  `_digest.length == 32` and `_r.length == 32`; certification returns UNKNOWN
  for the single-point body path without naming timeout or unsupported
  coordinate.  This is a bytes/dynamic-aggregate modeling or certification
  explainability issue.
- `ClaimTopicsRegistry.renounceOwnership` enc=7 is not safe to generalize in
  Stage 4.  The current Stage-2 certificate proves only the point
  `msg.sender == 0` and `state._owner == 0`.  Turning it into a useful PUT
  would require a new Stage-2 relation region such as
  `state._owner == msg.sender`, with the test rendering it as fuzzed sender
  plus `vm.store(owner, sender)`.  Stage 4 must not invent that proof.
- The branch report for the owner guard is currently lowered as
  `return_value$_owner$1 == return_value$__msgSender$2`, so the existing
  structural relation-retreat machinery does not see the source-level
  `state._owner == msg.sender` relation.  A useful next optimization is to
  recover these owner/sender relations before or during region generation.

Dataset safety:

- `/home/samson/workspace/VeriPUT/Datasets` was not modified.
- `/home/samson/workspace/VeriPUT/Results` was read as prepared-subject input
  only; all schedules, cert outputs, PUT outputs, and summaries were written
  under `/tmp`.

## 2026-08-07 small Stage-4 benchmark wave

Goal:

- Give a quick current success-rate picture for benchmark-derived certified
  regions, using the final per-case ESBMC timeout setting of 600s.
- Measure concrete replay / PUT validity on reference contracts rather than
  relying only on Stage-2 certification counts.

Input:

- Combined certified-row file:
  `/tmp/veriput_put_smallwave_20260807_certified6.jsonl`.
- Sources:
  - `/tmp/veriput_bench_salvage_bugfix_20260806_235923/certify-results.jsonl`
  - `/tmp/veriput_bench_salvage_stress_20260806_235923/certify-results.jsonl`
- Rows: 6 certified units/records, 7 certified regions:
  - BugFix DepositLog: `approvedToLog`, `setApprovedLogger` x2,
    `logCreated`
  - BugFix EtherLotto: `play`
  - Stress ClaimTopicsRegistry: `addClaimTopic`, `removeClaimTopic`

Command:

- `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_put_smallwave_20260807_certified6.jsonl --strong-recipe --timeout 600 --memlimit-gib 8 --forge-timeout 300 --out-root /tmp/veriput_put_smallwave_20260807_out_fix1`

Stage-2 accounting over these rows:

- Witnessed paths: 12.
- Certified paths/regions: 7.
- Not-certified paths: 5.
- Of not-certified paths: 3 concrete-fallback, 2 method-unsupported.
- No witnessed path lacked a verdict.

Stage-4 result:

- PUT emitted: 3 / 7 certified regions.
- Reference-valid B rows: 3 / 7 certified regions.
- Every emitted PUT was also reference-valid: 3 / 3.
- Every emitted PUT carried both fuzz parameters and an oracle: 3 / 3.
- Emitted/green PUTs:
  - `DepositLog.approvedToLog` enc 3: fuzz=1, asserts=2.
  - `DepositLog.setApprovedLogger` enc 6: fuzz=3, asserts=1
    rollback/exit-kind oracle.
  - `DepositLog.setApprovedLogger` enc 7: fuzz=2, asserts=1.
- Refused regions:
  - `EtherLotto.play` enc 2: no concrete call to `play` found in the emitted
    replay case, so there was nothing to lift.
  - `DepositLog.logCreated` enc 6: not parameterized; the only wide coordinate
    is omitted by the emitter, so the result would be the concrete replay
    wearing bound syntax.
  - `ClaimTopicsRegistry.addClaimTopic` enc 31 and `removeClaimTopic` enc 15:
    assertion ladder returns `UNDECIDED-TRUNCATED`; loops named include
    the `onlyOwner` wrapper loop and `__memset_impl`.

Reporting correction after commit `ecfecd4548`:

- `put_all.py --forge-only` was rerun over the same output.  The correct
  deliverable denominator is certified region rows, not emitted PUTs:
  `B = 3 / 7 certified region rows`.
- Forge-visible emitted PUT tests were `3 green / 3 total`.
- Forge-visible concrete replay tests were `0 green / 0 total` in this Stage-4
  project.  The Stage-2 `3 concrete-fallback` paths are accounting candidates,
  not measured reference-valid concrete-only output tests in this run.
- `put_all.py` now prints these PUT/concrete replay Forge-visible counts
  explicitly and changed the misleading summary text from
  `B = ... emitted PUT(s)` to `B = ... certified region row(s)`.

## 2026-08-07 Stage-4 partial-loop fallback

Problem:

- The small Stage-4 benchmark wave refused both stress
  `ClaimTopicsRegistry` certified regions because `--path-cov-assert`
  returned `UNDECIDED-TRUNCATED`.
- The existing `--auto-unwind 1` retry widened the named loops with
  `--unwindset ...:8`, but both regions still truncated on the `onlyOwner`
  wrapper loop and ESBMC's `__memset_impl`.
- ESBMC's own refusal text names three repairs: raise unwind, use unwindset,
  or pass `--partial-loops`.  The driver implemented only unwindset.

Code change:

- `scripts/solidity_path_put.py` now accepts `--auto-partial-loops`.
- If the ladder is still `UNDECIDED-TRUNCATED` after named-loop
  `--auto-unwind` attempts, the driver retries the assertion ladder once with
  `--partial-loops`.
- The retry is adopted only if it produces ladder rows or a recognized RESULT
  token, using the same `attempt_is_usable` guard as unwindset retries.
- The adopted flag is recorded as ladder-only provenance:
  `LADDER WIDENED: --partial-loops`, and in `put.json`
  `unwind_applied_to_ladder_only`.
- `notes/coverage/scripts/veriput_recipe.py` advanced the shared recipe to
  `veriput-strong/13` and enables this Stage-4 fallback.
- `put_all.py --strong-recipe` passes the new flag through.

Real benchmark measurement:

- Command:
  `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_put_smallwave_20260807_certified6.jsonl --only ClaimTopicsRegistry --strong-recipe --timeout 600 --memlimit-gib 8 --forge-timeout 300 --out-root /tmp/veriput_put_claimtopics_partial_20260807`
- Output root:
  `/tmp/veriput_put_claimtopics_partial_20260807`.
- Selected rows: 2 certified stress regions.
- Result:
  - `ClaimTopicsRegistry.addClaimTopic` enc 31: emitted PUT, fuzz=1,
    asserts=1, Forge green, B.
  - `ClaimTopicsRegistry.removeClaimTopic` enc 15: emitted PUT, fuzz=1,
    asserts=1, Forge green, B.
- For each region, `--unwindset` at 8 still returned
  `blocker=truncated rows=0`; the `--partial-loops` retry returned
  `blocker=None rows=6`.
- The R2 pass still saw truncation and therefore produced no R2 row; the final
  PUT strength came from the base ladder oracle over `_owner`, plus the fuzzed
  `_claimTopic` region.

Updated small-wave success picture:

- Previous same-wave B rows were 3 DepositLog PUTs.
- Adding the two stress PUTs gives 5 B rows over the same 7 certified region
  rows: `5 / 7 = 71.4%`.
- Forge-visible generated PUT tests in the combined measurement: 5 green / 5
  total.
- Forge-visible concrete replay tests remain 0 / 0 in these Stage-4 projects.
- Remaining non-B rows in the 7-row wave:
  - `EtherLotto.play` enc 2: emitter cannot find a concrete `play` call to
    lift.
  - `DepositLog.logCreated` enc 6: region is not parameterized and would only
    emit a concrete replay wearing bound syntax.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py notes/coverage/scripts/put_all.py notes/coverage/scripts/veriput_recipe.py scripts/test_put_all_accounting.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_put_all_accounting.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed, 217 / 217 tests.
- `git diff --check -- scripts/solidity_path_put.py scripts/test_solidity_path_put.py notes/coverage/scripts/put_all.py notes/coverage/scripts/veriput_recipe.py scripts/test_put_all_accounting.py`
  passed.

## 2026-08-07 high-level call-options lift fix

Problem:

- The small Stage-4 wave still refused `EtherLotto.play` enc 2 with:
  no concrete call to `play` found in the emitted replay case.
- Existing artifacts showed this was not an ESBMC emission failure.  The
  emitted `test_cov_1` contained:
  `try c0.play{value: UINT256_MAX}() {} catch {}`.
- The PUT driver's member-call parser recognized `c0.f(...)` and low-level
  `abi.encodeWithSignature(...)`, but not high-level Solidity call options
  between the function name and argument list: `c0.f{value: v}(...)`.

Code change:

- `scripts/solidity_path_put.py` now uses `member_call_re()` for high-level
  member calls.
- The parser accepts optional call options `{...}` before the argument list.
- `find_unit_call`, `call_arg_span`, `rewrite_call_args`, and
  `target_instance_for_call` now share that shape, so `{value: ...}` is
  preserved and only the calldata arguments are rewritten.
- This is generic for payable/value high-level calls, not an EtherLotto
  special case.

Real benchmark measurement:

- Command:
  `PYTHONDONTWRITEBYTECODE=1 python3 notes/coverage/scripts/put_all.py --cert /tmp/veriput_put_smallwave_20260807_certified6.jsonl --only ether_lotto_1round.play --strong-recipe --timeout 600 --memlimit-gib 8 --forge-timeout 300 --out-root /tmp/veriput_put_etherlotto_callopts_20260807`
- Output root:
  `/tmp/veriput_put_etherlotto_callopts_20260807`.
- Result:
  - `EtherLotto.play` enc 2 emitted PUT,
    `EtherLottoCovTest_0_EtherLotto_play_put2.t.sol`.
  - fuzz parameters: 3 (`msg.sender`, `block.timestamp`, `block.number`).
  - oracle assertions: 1 exit-kind oracle for rollback revert.
  - Forge green, B = 1 / 1 for the selected row.
- The generated test keeps `msg.value` as a single observed in-region value,
  not a fuzz coordinate, because payable value is not a call argument that can
  be `bound()` in the signature.  The test says this explicitly.
- The self-check disabled one red concrete replay (`test_cov_2`); the emitted
  PUT stayed enabled and green.

Updated small-wave success picture after this and partial-loop fallback:

- DepositLog B rows from the earlier wave: 3.
- ClaimTopicsRegistry B rows after partial-loop fallback: 2.
- EtherLotto B rows after call-options parsing: 1.
- Combined over the same 7 certified region rows:
  `6 / 7 = 85.7%` deliverable B.
- Forge-visible generated PUT tests in the combined measurement: 6 green / 6
  total.
- Remaining non-B row:
  `DepositLog.logCreated` enc 6, refused because it is not parameterized and
  would only emit a concrete replay wearing bound syntax.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed, 218 / 218 tests.

Important code fix found by this wave:

- Before the fix, Forge could not compile the generated DepositLog PUTs because
  the emitter wrote `vm.prevrandao(0)`.  The installed forge-std has both
  `prevrandao(bytes32)` and `prevrandao(uint256)`, so integer literals are
  ambiguous under solc 0.8.29.
- `scripts/solidity_path_put.py` now renders `block.prevrandao` cheatcode
  arguments as `uint256(...)`, for both singleton pins and fuzz parameters.
- `scripts/test_solidity_path_put.py` was updated to expect
  `vm.prevrandao(uint256(...))`.

Probe-timeout campaign fix:

- `certify_all.py` now parses the ESBMC line
  `--path-cov-probe: unit ... added N exit-latched claim(s) for B branch
  arm(s) at E physical exit(s)` when the same driver output ends in a timeout.
  The row receives `driver_diagnostic.tag =
  path-coverage-probe-claim-explosion` plus the observed dimensions.
- `unit_campaign_plan.py` treats this as a retryable weak result named
  `probe enumeration claim explosion`.
- The retry strategy is `cheap-probe-enumeration`: keep probe mode but rewrite
  the next attempt to `--probe-witnesses 1` and remove `--probe-ladder` /
  `--probe-ladder-budget`.  This preserves cheap refutation/path diversity
  while avoiding the first-stage 300+ claim multi-witness queue that killed the
  BugFix `setGoverned` / `executeBatchDeposit` attempts.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_certify_all_partial_journal.py notes/coverage/scripts/certify_all.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed, 217 / 217 tests.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_all_partial_journal.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- scripts/solidity_path_put.py scripts/test_solidity_path_put.py scripts/test_certify_all_partial_journal.py notes/coverage/scripts/certify_all.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.

## 2026-08-07 round-robin benchmark sampling and unit-level gate

Problem found:

- Priority-only unit sampling can spend an early smoke run on one benchmark
  family before touching the others.  That is bad for VeriPUT triage because
  the current failures are benchmark-family specific:
  - `bugfix124` often reaches real certification work but can timeout on
    heavier units;
  - `peer182` exposes no-witness and preflight closure/modeling issues;
  - `stress243` exposes path-coverage/frontend modeling defects.
- The summary gate previously looked mainly at witnessed-path certification
  quality.  A sample with only one certified unit and many units with no
  usable witness could still look `ready` if the one witnessed path was
  certified after retry/slice adjustment.  That is too weak for the >=70%
  generalized PUT target.

Code changes:

- `unit_schedule.py` now supports
  `--selection-strategy round-robin-benchmark`.
  The default remains `priority`, so existing schedules are unchanged unless
  the new strategy is requested.
- `benchmark_pipeline_plan.py` exposes the same choice as
  `--unit-selection-strategy`.
- `certify_result_summary.py` now reports unit-level certification accounting:
  - `certified_units`;
  - `certified_unit_denominator`;
  - `certified_unit_rate`.
- The summary gate now degrades when the unit-level rate is below the same
  threshold.  Path-level retry/slice adjusted rates are still reported, but
  they no longer hide broad no-witness/no-claim coverage failures.

Validation:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_schedule.py notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_certify_result_summary.py scripts/test_unit_schedule.py scripts/test_benchmark_pipeline_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_result_summary.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_schedule.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_benchmark_pipeline_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_schedule.py notes/coverage/scripts/benchmark_pipeline_plan.py scripts/test_certify_result_summary.py scripts/test_unit_schedule.py scripts/test_benchmark_pipeline_plan.py`
  passed.

Benchmark sample:

- Base output directory:
  `/tmp/veriput_bench_sample_20260807_022340`.
- Round-robin plan:
  `/tmp/veriput_bench_sample_20260807_022340/rr_plan1`.
- The full unit schedule used
  `--unit-selection-strategy round-robin-benchmark --unit-limit 12`.
  The base schedule was balanced: 4 `bugfix124`, 4 `peer182`, 4
  `stress243`.
- With the earlier min3 journals/certification rows supplied, the campaign
  selected 9 fresh attempt-1 jobs, balanced 3 / 3 / 3 across the three
  benchmark families.
- Attempt policy:
  `jobs=1`, 60s ESBMC budget, 75s wrapper timeout, 8GiB memory.
- Result JSONL:
  `/tmp/veriput_bench_sample_20260807_022340/rr_plan1/certify-results-a1.jsonl`.
- Runner journal:
  `/tmp/veriput_bench_sample_20260807_022340/rr_plan1/unit-run-a1.jsonl`.
- Unit-rate summary:
  `/tmp/veriput_bench_sample_20260807_022340/rr_plan1/certify-summary-a1-unitrate.json`.

Observed 9-job result:

- `CERTIFIED`: 1
- `KILLED`: 2
- `NO-PATH`: 1
- `NO-WITNESS-UNDECIDED`: 2
- `NO-WITNESS-UNKNOWN`: 3
- `certified_units = 1`
- `certified_unit_denominator = 9`
- `certified_unit_rate = 0.1111111111111111`
- `retry_adjusted_certified_path_rate = 1.0`
- Gate after the unit-rate fix:
  `degraded`, blocker `certified unit rate is below threshold`.

Per-family diagnosis from this sample:

- `bugfix124`:
  - `DepositLog.approvedToLog` certified one body-slice region:
    `_caller` free, `approvedLoggers[_caller] == 0`, environment pins stable.
  - `DnGmxBatchingManager.executeBatchDeposit` and
    `FlashGovernanceArbiter.setGoverned` both hit the 60s attempt-1 budget at
    `started` with no witness.  These are retryable/heavy, but should be
    inspected before spending 600s attempts.
- `peer182`:
  - `AIRBets.initialize2` was `NO-PATH` / bounded-holds no-witness.
  - `AIRBets.transfer` and `AIRBets.transferFrom` were
    `NO-WITNESS-UNDECIDED` with a preflight refusal around direct
    self-recursive helper wrappers in `SafeMath.div/2` and `SafeMath.sub/2`.
    This is a closure/modeling issue, not a proof of unreachable behavior.
- `stress243`:
  - `BalancerContractRegistry.deregisterBalancerContract`,
    `deprecateBalancerContract`, and
    `addOrUpdateBalancerContractAlias` all produced
    `driver_diagnostic=path-coverage-no-claims-reached-solver`.
  - This repeats the earlier `registerBalancerContract` failure and points to
    a path-coverage/front-end lowering issue where the instrumented claims are
    not reaching the solver on this contract family.

Current decision:

- Do not start full benchmark-wide PUT certification yet.  The scheduling
  machinery can now do disciplined small balanced samples, but the current
  true unit-level success rate is far below the 70% target.
- Next high-value fixes are:
  1. diagnose why `stress243` registry path claims do not reach the solver;
  2. diagnose the `peer182` AIRBets SafeMath/preflight refusal;
  3. only then rerun small balanced benchmark samples before considering a
     full benchmark pass.
- No files under `/home/samson/workspace/VeriPUT/Datasets` or
  `/home/samson/workspace/VeriPUT/Results` were modified; all benchmark
  outputs above are under `/tmp`.

## 2026-08-07 stress registry named-obstacle / probe fix

Benchmark symptom:

- In the round-robin sample, four `stress243` units on
  `BalancerContractRegistry` hit
  `driver_diagnostic=path-coverage-no-claims-reached-solver`.
- Example command, already recorded in the unit workdir, was the focused
  path-coverage enumeration for `deregisterBalancerContract` with
  `--path-cov-probe --all-witnesses --max-witnesses 8`, 60s, 8GiB.
- ESBMC printed:
  - 15 complete paths instrumented for the focused unit;
  - all 15 paths named as a `NAMED OBSTACLE`, cause (a), i.e. a
    branch-free assume / frontend-lowered decision that removes executions from
    the model;
  - 180 exit-latched probe claims;
  - `0 of 15 instrumented path claim(s) reached the solver`;
  - then aborted with `INTERNAL DEFECT — NOT ONE ... reached the solver`.

Root cause:

- `audit_entry_liveness()` grouped `all_claims`, the complete-path universe,
  and treated a focused unit with zero complete-path solver verdicts as a hard
  entry-liveness defect.
- In probe mode, branch-arm probe claims are solved through
  `path_probe_claims/path_probe_outcome`; complete-path claims may legitimately
  have no direct solver verdict and are later reported through the complete-path
  ledger.
- When every complete path of the unit is already in
  `named_obstacle_paths`, the absence of a complete-path verdict is not evidence
  that the harness never entered the unit.  It is a structural model/chain
  obstacle that should be reported as `u_reason=named-obstacle`, not hidden
  behind an abort.

ESBMC change:

- `goto_coveraget::audit_entry_liveness()` now counts how many complete paths
  of each unit are in `named_obstacle_paths`.
- A unit whose instrumented complete-path universe is entirely named-obstacle
  is not added to the hard `dead` list merely because zero complete-path
  verdicts reached the solver.
- True focused units with non-obstacle claims and zero verdicts still abort as
  before.  This is important: the change does not weaken the entry-liveness
  guard for ordinary vacuous runs.

External driver / scheduler change:

- `solidity_path_generalise.py` now gives the all-`named-obstacle` empty
  enumeration case its own text:
  structural model/chain mismatch, not solver-budget advice and not bounded
  no-path.
- `unit_campaign_plan.py` now classifies `empty_witness_verdict=REFUSED` rows
  containing `named-obstacle` as non-retryable reason
  `named obstacle no witness`.
- The SafeMath/direct-recursion preflight bucket is now limited to the
  `direct self-recursive function/helper` reason, so named obstacles no longer
  get misreported as witness preflight refusal.

Validation:

- Built ESBMC:
  `cmake --build build --target esbmc -j2` passed.
  The build printed existing Solidity model array-bound warnings in
  `solidity_address.c`; no new build error.
- Reconfigured CTest:
  `cmake -S . -B build -DESBMC_REGRESS_TIMEOUT=90` exited 0 and regenerated the
  test list.  During configure, Bitwuzla's external sub-build printed cadical
  header failures, but CMake still selected the existing Bitwuzla 0.8.2 tree
  and wrote build files.
- New regression:
  `regression/esbmc-solidity/solidity_path_cov_probe_named_obstacle_reports`.
  It is a payable function with `__ESBMC_assume(false)` under
  `--path-cov-probe`; before this fix this shape trips the 0-of-N liveness
  abort, after the fix it reports 2 `named-obstacle` U reasons and exits
  `VERIFICATION SUCCESSFUL`.
- CTest command passed:
  `ctest --test-dir build -R "solidity_path_cov_(probe_named_obstacle_reports|explicit_assume_obstacle|residual_unit_call_obstacle|focus_function_keeps_callee_decisions|probe_fire)" --output-on-failure`.
- Python checks passed:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_path_generalise.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_solidity_path_generalise.py scripts/test_unit_campaign_plan.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  - `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  - `git diff --check -- ...` on all touched files.
- Direct stress recheck, one ESBMC enumeration only:
  `/tmp/veriput_stress_claim_recheck_20260807`.
  The same `deregisterBalancerContract` command that previously exited `-6`
  now exits `0`, writes `cov-report.json`, reports 15 paths total, 0 witnessed,
  and `u_reasons {'named-obstacle': 15}`.
- Direct driver recheck:
  `/tmp/veriput_stress_driver_recheck_20260807_b`.
  `solidity_path_generalise.py` exits `1` before certification, with:
  no witnessed path, 15/15 `named-obstacle`, structural mismatch.  This is the
  desired outcome: not certifiable, not retryable as a timeout, and not an
  internal crash.

Impact on benchmark strategy:

- This does not make the `stress243` registry units generate PUTs.  It removes
  a misleading internal-defect bucket and exposes the real blocker: the focused
  units are named-obstacle units under the current Solidity path model.
- Full benchmark is still premature.  The next technical target is the peer
  `AIRBets` SafeMath/direct-recursive-helper refusal and then the heavy
  `bugfix124` timeout units.

## 2026-08-07 peer AIRBets SafeMath preflight diagnosis

Observed sample rows:

- `peer182__peer_ccsolbmc__AIRBets.transfer`
- `peer182__peer_ccsolbmc__AIRBets.transferFrom`
- Both rows are `NO-WITNESS-UNDECIDED`, no ESBMC process started.
- Driver text:
  target call closure reaches direct self-recursive function/helper wrappers
  `SafeMath.div/2`, `SafeMath.sub/2`.

Source diagnosis:

- The benchmark flat source under
  `/home/samson/workspace/VeriPUT/Results/Peer182/subjects/peer_ccsolbmc__AIRBets/flat.sol`
  literally contains:
  - `function sub(uint256 a, uint256 b) ... { return sub(a, b); }`
  - `function div(uint256 a, uint256 b) ... { return div(a, b); }`
- The usual OpenZeppelin third argument is present only as a trailing comment:
  `//SafeMath: subtraction overflow")` / `//SafeMath: division by zero")`.
- The solc AST confirms the two-argument wrappers are function ids 70 and 144,
  with a single `Return` statement.  The three-argument overloads are separate
  ids 96 and 170.
- Therefore this is not a false positive from overload resolution in the
  preflight.  In the current source text, the wrappers are genuinely direct
  self-recursive under Solidity overload/arity rules.

Consequence:

- Running ESBMC on these units would spend budget expanding/handling a helper
  with no source-level base case.  It is unlikely to improve PUT yield and is
  exactly the waste the preflight was designed to avoid.
- Do not classify this as a region strategy failure.
- Do not spend attempt-2/attempt-3 on these AIRBets units unless explicitly
  testing the `--allow-recursive-helper-enumeration` escape hatch.
- The correct scheduler behavior is the one now in
  `unit_campaign_plan.py`: non-retryable reason `witness preflight refused`.

Open question:

- If a later benchmark target contains the same comment-truncated SafeMath
  source but is otherwise important, a possible future experiment is an
  explicit source-repair/preprocess mode that rewrites only this canonical
  malformed wrapper back to the three-argument overload.  That would be a
  benchmark-normalization choice, not a verifier proof step, and should be kept
  opt-in because the current Solidity source really is recursive.

## 2026-08-07 retry schedules use attempt-specific result JSONL

Discovered before broad benchmark sampling:

- `unit_campaign_plan.py` could generate an attempt-2/3 retry schedule whose
  per-job `certify_argv` still wrote to the previous attempt's `--out` JSONL.
- `certify_all.py` treats an existing row for the same unit in that JSONL as
  already recorded.  Therefore a retry schedule could be correct in timeout,
  memory, and retry strategy, but still skip the unit instead of spending the
  intended ESBMC attempt.
- This is a campaign plumbing issue, not a region-quality result.  It can make
  a strategy look ineffective because the retry did not actually run.

Code change:

- Added `_attempt_out_path` in `unit_campaign_plan.py`.
- For attempt `N > 1`, `foo-a1.jsonl` becomes `foo-aN.jsonl`; if no terminal
  `-a<digits>` suffix exists, `foo.jsonl` becomes `foo-aN.jsonl`.
- The rewritten path is synchronized across:
  - job `certify_argv`;
  - job `dry_run_argv`;
  - `certification_budget.out`;
  - top-level schedule `cert_out`;
  - schedule summary `certify_out`.

Validation:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py notes/VeriPUT_handoff_memory.md`
  passed before this note append.

Go/no-go update:

- After this fix is committed, it is reasonable to start a small stratified
  benchmark sample.  Do not start full-corpus execution yet.
- The first benchmark run should be measurement-oriented: sample across
  `peer182`/`bugfix124`/`stress243`, inspect weak buckets and retry behavior,
  then decide whether another generator/region optimization is needed before
  scaling.

## 2026-08-07 min3 benchmark sample and non-retryable pathcov defects

Read/write discipline:

- No Dataset or Results contract files were modified.
- New benchmark artifacts are under:
  `/tmp/veriput_bench_sample_20260807_022340`.
- AST preheat wrote compact ASTs only under:
  `/tmp/veriput_bench_sample_20260807_022340/ast-cache`.

AST preheat:

- Initial read-only pipeline with a fresh cache found 508 missing compact AST
  rows, so unit scheduling was blocked.
- Ran the planner-suggested AST preheat batch only, not ESBMC certification:
  `/tmp/veriput_bench_sample_20260807_022340/next-ast-preheat-schedule.json`.
- Budget: `jobs=1`, 90s outer timeout, 8GiB.
- Result: 32 / 32 preheat jobs `ok`.
- Coverage of preheated subjects:
  - 11 peer182 subjects, 198 units;
  - 11 bugfix124 subjects, 224 units;
  - 10 stress243 subjects, 99 units.

Benchmark schedule sampling:

- Per-benchmark schedules were generated to avoid bugfix priority-0 units
  crowding out peer/stress observations.
- The 3-job smoke schedule is:
  `/tmp/veriput_bench_sample_20260807_022340/min3_a1/unit-schedule-min3-a1.json`.
- Jobs:
  - bugfix124 `DepositLog.setApprovedLogger`;
  - peer182 `AIRBets.approve`;
  - stress243 `BalancerContractRegistry.registerBalancerContract`.
- Attempt-1 budget: 60s ESBMC run timeout, 75s runner timeout, 8GiB, `jobs=1`.
- Result JSONL:
  `/tmp/veriput_bench_sample_20260807_022340/min3_a1/certify-results-a1.jsonl`.

Attempt-1 results:

- `DepositLog.setApprovedLogger`: `CERTIFIED`, 2 certified / 1 not / 3
  witnessed, 15s.  The one not-certified path is the expected
  `EXCLUDED FROM THE SLICE by the pins` msg.value gate.
- `AIRBets.approve`: `NO-PATH`, zero witnesses.  Progress reached
  `no-witness`; all 4 claims were decided as `bounded-holds`.
- `BalancerContractRegistry.registerBalancerContract`:
  `NO-WITNESS-UNKNOWN`, zero certified regions, 4s.  This is NOT a normal
  region failure: driver log reports:
  `INTERNAL DEFECT — NOT ONE of the 19 instrumented path claim(s) reached the solver`
  and says the harness never entered any unit.  The same log names an obstacle:
  all 19 paths were excluded because the model removed executions through an
  assume/require-style construct.  This should be tracked as a path coverage /
  frontend-modeling defect, not retried as stronger R1/R2 search.

Attempt-2 peer-only retry:

- Generated from campaign strategy for bounded-holds no-witness:
  `/tmp/veriput_bench_sample_20260807_022340/min3_a1/next-unit-schedule-a2-peer-only.json`.
- Budget: 120s ESBMC run timeout, 135s runner timeout, 8GiB, `jobs=1`.
- Strategy: same focus scope, `--max-tx 2`, `--refine-rounds 2`.
- Result:
  `/tmp/veriput_bench_sample_20260807_022340/min3_a1/certify-results-a2.jsonl`.
- `AIRBets.approve` remained `NO-PATH`; progress again reached `no-witness`
  with all 4 claims `bounded-holds`.  Therefore the automatic
  max-tx-2 retry did not help this ERC20-style approve case.  Further spending
  should first analyze initializer/state-precondition modeling, not blindly
  jump to 600s.

Code change from the sample:

- `certify_all.py` now records a structured `driver_diagnostic` when driver
  output contains the path-coverage internal defect where no instrumented path
  claim reaches the solver.
- `unit_campaign_plan.py` treats that diagnostic as non-retryable:
  `path coverage no claims reached solver`.
- To protect already-produced rows that predate the diagnostic field,
  `unit_campaign_plan.py` also treats
  `bucket=NO-WITNESS-UNKNOWN`, `generalise_progress.stage=started`, nonzero
  non-timeout `exit`, and no partial witness journal as non-retryable:
  `driver stopped before enumeration`.
- Replanning the real min3 sample after this fix gives:
  - completed strong: 1 (`DepositLog.setApprovedLogger`);
  - non-retryable: 1 (`BalancerContractRegistry.registerBalancerContract`);
  - pending retry: 1 (`AIRBets.approve`, attempt 3 only).
- Replan output:
  `/tmp/veriput_bench_sample_20260807_022340/min3_a1/unit-campaign-after-nonretryable-fix.json`.

Validation:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/certify_all.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_all.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.

Current conclusion:

- It is still reasonable to continue small stratified benchmark sampling.
- Do not run full-corpus evaluation yet.
- Do not automatically spend 600s on bounded-holds no-witness rows until a
  small set is inspected for missing initializer/state preconditions; `AIRBets`
  shows that max-tx-2 alone is not enough.
- Treat path-coverage "no claims reached solver" rows as tool/modeling
  blockers, not PUT-region synthesis failures.

## 2026-08-07 read/write slot split benchmark smoke sample

Branch:

- `feat/veriput-fuzz-first`, pushed to `E-SOL/feat/veriput-fuzz-first`.
- Latest code commit before this sample:
  `5e6bf23d58 [solidity] Split VeriPUT read and write slot uses`.

Static candidate selection:

- Base schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule.json`.
- 76 scheduled jobs, 39 with mapping slot accesses.
- 9 jobs have `all` slot accesses strictly wider than `read` slot accesses,
  so they exercise the region/oracle split.
- Representative affected units:
  - `DepositLog.setApprovedLogger`: write-only `approvedLoggers[_logger]`.
  - `AIRBets.approve`: write-only `_allowances[owner][spender]`.
  - `AIRBets.transferFrom`: write-only allowance slots plus read balance/fee
    slots.
  - `IdentityRegistryStorage.addIdentityToStorage` and
    `modifyStoredInvestorCountry`: read
    `_identities[_userAddress].identityContract`, write
    `_identities[_userAddress].investorCountry`.

Smoke schedule:

- Schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-readwrite-sample-a1.json`.
- Results:
  `/tmp/veriput_stratified_20260807_03/certify-results-readwrite-sample-a1.jsonl`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_03/unit-run-readwrite-sample-a1.jsonl`.
- Workdir:
  `/tmp/veriput_stratified_20260807_03/certify-work-readwrite-sample-a1_t70_r60_m8`.
- Policy:
  `jobs=1`, runner timeout 90s, certification timeout 70s, ESBMC run timeout
  60s, memory limit 8GiB.
- No benchmark/dataset contract file was modified; all outputs are under
  `/tmp`.

Smoke result:

- Runner: 3 / 3 jobs exited `ok`.
- `bugfix124 acfix_fixlink_DepositLog DepositLog.setApprovedLogger`:
  `CERTIFIED`, 2 certified / 1 not / 3 witnessed, 13.2s.
  The not-certified path is `EXCLUDED FROM THE SLICE by the pins` on
  `msg.value`, so this is a body-slice success.
- `peer182 peer_ccsolbmc__AIRBets AIRBets.approve`:
  `NO-PATH`, 0 witnessed, 1.0s.
  Generalise progress says all 4 claims were `bounded-holds` under current
  bound/scope. This is a witness-generation / path-depth issue, not a proof of
  unreachable behavior.
- `stress243 ERC-3643__ERC-3643__IdentityRegistryStorage
  IdentityRegistryStorage.modifyStoredInvestorCountry`:
  `KILLED`, 3 witnessed, 0 certified / 0 not, 70.0s.
  Partial journal: 67 / 116 claims decided, 3 paths, 24 witnesses. Failure
  bucket is refinement-stage no verdict.  An initial planner version marked it
  for level0-only retry; a later real attempt showed that was too weak, so the
  current planner uses one refinement round before certification.

Current interpretation:

- The read/write slot split is behaving as intended: write-only slots are still
  available to PUT/R2 oracles, but no longer inflate the entry region.
- We are ready for targeted benchmark sampling, not full corpus普测 yet.
- Before broad runs, fix or tune two buckets:
  - no-witness/bounded-holds units such as `AIRBets.approve`, likely by using a
    deeper cheap witness strategy before spending certification budget;
  - refinement-stage timeouts such as
    `IdentityRegistryStorage.modifyStoredInvestorCountry`, likely by reducing
    refinement rather than skipping it entirely.

## 2026-08-07 bounded-holds no-witness retry strategy

Code change:

- `notes/coverage/scripts/unit_campaign_plan.py` now separates
  `bounded-holds no witness` from the generic `no certified regions` bucket
  when a certification row has `generalise_progress.stage == "no-witness"` and
  the reason text names `bounded-holds`.
- Such jobs remain retryable, but their retry schedule now gets
  `retry_strategy=deepen-witness-search` and `--max-tx 2`.
- The strategy deliberately does NOT set `--scope whole`.
- Existing retry policies are unchanged:
  - `certification-stage no verdict` -> `--refine-rounds 1`;
  - `refinement-stage no verdict` -> `--refine-rounds 1`;
  - ordinary weak/missing certification -> default `--refine-rounds 2`.

Why not `--scope whole`:

- I tested `AIRBets.approve` attempt 2 once with the initially tempting
  `--scope whole --max-tx 2` strategy:
  - schedule:
    `/tmp/veriput_stratified_20260807_03/next-unit-schedule-airbets-approve-a2-nowitness.json`;
  - results:
    `/tmp/veriput_stratified_20260807_03/certify-results-airbets-approve-a2-nowitness.jsonl`;
  - journal:
    `/tmp/veriput_stratified_20260807_03/unit-run-airbets-approve-a2-nowitness.jsonl`;
  - workdir:
    `/tmp/veriput_stratified_20260807_03/certify-work-airbets-approve-a2_nowitness_t130_r120_m8`.
- It exited quickly as `NO-WITNESS-UNKNOWN`, not because of solver budget, but
  because whole-scope path coverage pulled unrelated heavy units into the probe
  universe:
  `transferFrom` needed 20026 probe claims and exceeded
  `--path-cov-max-goals 10000`.
- That makes whole-scope a bad default retry for no-witness rows.  It may still
  be a manual diagnostic arm for selected units, but it should not be the
  campaign planner's automatic second attempt.

Current planned retry for the earlier 3-job smoke sample:

- Replanned with:
  `/tmp/veriput_stratified_20260807_03/unit-campaign-readwrite-sample-a2-single-refine.json`.
- Next schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-readwrite-sample-a2-single-refine.json`.
- `AIRBets.approve`:
  `reason=bounded-holds no witness`,
  `retry_strategy=deepen-witness-search`,
  `--max-tx 2`, no explicit `--scope`, `--refine-rounds 2`.
- `IdentityRegistryStorage.modifyStoredInvestorCountry`:
  `reason=refinement-stage no verdict`,
  `retry_strategy=single-refine-certification-first`,
  `--refine-rounds 1`.

Refine0 counterexample:

- I tested `IdentityRegistryStorage.modifyStoredInvestorCountry` attempt 2 once
  with `--refine-rounds 0`:
  - schedule:
    `/tmp/veriput_stratified_20260807_03/next-unit-schedule-identity-modifyCountry-a2-refine0.json`;
  - results:
    `/tmp/veriput_stratified_20260807_03/certify-results-identity-modifyCountry-a2-refine0.jsonl`;
  - journal:
    `/tmp/veriput_stratified_20260807_03/unit-run-identity-modifyCountry-a2-refine0.jsonl`;
  - workdir:
    `/tmp/veriput_stratified_20260807_03/certify-work-identity-modifyCountry-a2_refine0_t130_r120_m8`.
- It avoided the timeout but produced `NOT-CERTIFIED`, 0 certified / 5 not /
  5 witnessed, 96s.
- The useful finding: with the strong recipe's `--skip-bracket`, refine0 leaves
  no full product region for the body paths.  Level0 only identified point
  projections, while enc 12/14/26/55 all ended with
  `no fully bounded region was measured`.
- Therefore refine0 is a diagnostic arm, not a good automatic second attempt.
  The current planner uses exactly one linear refine round for refinement-stage
  timeout retries.

Validation:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.

## 2026-08-07: Region Slot Coordinates Now Use Read-Only Source Accesses

Problem found on the balanced6b `IdentityRegistryStorage.addIdentityToStorage`
sample:

- The target writes `_identities[_userAddress].investorCountry = _country`.
- The path guard reads `_identities[_userAddress].identityContract`.
- The generalise stage previously collapsed both to the base access
  `_identities[_userAddress]`, then proposed the queryable scalar field
  `state._identities[_userAddress].investorCountry` as a path-region
  coordinate.
- That slot is a post-state oracle target, not an entry-state path splitter.
  It had no CE value, was skipped by level0, and made the 0-refine retry report
  `no fully bounded region was measured`.

Code change:

- `unit_mapping_slot_accesses(..., access_mode="read")` now ignores plain
  assignment LHS slots for region-coordinate discovery.  Default
  `access_mode="all"` is unchanged for PUT/oracle use.
- Struct mapping member tails are preserved:
  `m[k].identityContract` and `m[k].investorCountry` are no longer collapsed to
  the same base `m[k]`.
- `solidity_path_generalise.py` uses read-only slot accesses for
  `--slot-coords` and derives region slot dependencies from those read slots,
  so write-only mappings do not re-enter through fallback cross-products.
- `solidity_path_put.py` accepts the more precise tailed access names, so PUT
  oracle discovery still works for exact readable/written struct fields.

Static validation on the cached T-REX AST:

- Read-mode access:
  `[('_identities.identityContract', ('_userAddress',))]`.
- Default all-mode access:
  `[('_identities.identityContract', ('_userAddress',)),
  ('_identities.investorCountry', ('_userAddress',))]`.
- Region slot proposal after the fix:
  `[]`.
- Oracle slot proposal in default mode:
  `['state._identities[_userAddress].investorCountry']`.
- Source-R2 with PUT storage-layout metadata still proposes:
  `_identities[_userAddress].investorCountry: post == _country`.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/solidity_ast_dependencies.py scripts/solidity_path_generalise.py scripts/solidity_path_put.py scripts/test_solidity_path_generalise.py scripts/test_solidity_path_put.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_generalise.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_solidity_path_put.py`
  passed.

## 2026-08-07 balanced6b benchmark sample and refine retry

New stratified sample:

- Schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced6b.json`.
- Result JSONL:
  `/tmp/veriput_stratified_20260807_03/certify-results-balanced6b.jsonl`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_03/unit-run-balanced6b.jsonl`.
- Workdir:
  `/tmp/veriput_stratified_20260807_03/certify-work-balanced6b_a1_t70_r60_m8`.
- Policy:
  attempt 1, `jobs=1`, 60s ESBMC budget, 70s driver timeout, 75s runner
  timeout, 8GiB.
- Dataset and Results contracts were not modified.

Sample units:

- bugfix124 `DepositLog.setApprovedLogger`.
- bugfix124 `DepositLog.logCreated`.
- peer182 `BasicProvenance.Complete`.
- peer182 `EtherBank.withdraw`.
- stress243 `ClaimTopicsRegistry.removeClaimTopic`.
- stress243 `IdentityRegistryStorage.addIdentityToStorage`.

Attempt-1 results:

- Runner status: 6 / 6 `ok`.
- Buckets: 5 `CERTIFIED`, 1 `KILLED`.
- Certified:
  - `DepositLog.setApprovedLogger`: 2 certified / 1 not / 3 witnessed,
    14s.
  - `DepositLog.logCreated`: 1 / 1 / 2, 2s.
  - `BasicProvenance.Complete`: 2 / 1 / 3, 5s.
  - `EtherBank.withdraw`: 2 / 1 / 3, 20s.
  - `ClaimTopicsRegistry.removeClaimTopic`: 1 / 0 / 1, 67s.
- Killed:
  - `IdentityRegistryStorage.addIdentityToStorage`: 0 certified / 0 not /
    3 witnessed, level-0 had decided all 3 in 3.1s, but the run timed out
    immediately after entering `linear-refine`.

Summary:

- `certify-summary-balanced6b.json` reports:
  - raw certified path rate `8 / 15 = 0.533`;
  - slice-adjusted / retry-adjusted certified path rate `8 / 11 = 0.727`;
  - 4 `slice-excluded-by-pins` paths;
  - 3 no-verdict paths, all at
    `outer-round-started:linear-refine`.
- The overall gate is `ready` under the retry-adjusted rate, but the campaign
  still correctly schedules the one KILLED unit for attempt 2.

Code change:

- `unit_campaign_plan.py` now distinguishes `refinement-stage no verdict`
  from the generic `partial witness journal only` when witnessed paths exist
  and their no-verdict gap is at an outer/refine progress stage.
- Such attempt-2 retry jobs get:
  - `certification_quality.retry_strategy =
    single-refine-certification-first`;
  - `certification_quality.retry_refine_rounds = 1`;
  - `--refine-rounds 1`.
- This is still only a scheduling strategy.  Regions count only if ESBMC later
  certifies them.

Attempt-2 validation:

- Isolated schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced6b-a2-refinefirst-run.json`.
- Result JSONL:
  `/tmp/veriput_stratified_20260807_03/certify-results-balanced6b-a2-refinefirst.jsonl`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_03/unit-run-balanced6b-a2-refinefirst.jsonl`.
- Workdir:
  `/tmp/veriput_stratified_20260807_03/certify-work-balanced6b-a2_refinefirst_t130_r120_m8`.
- Policy:
  attempt 2, `jobs=1`, 120s ESBMC budget, 130s driver timeout, 135s runner
  timeout, 8GiB, `--refine-rounds 0` from the earlier experimental strategy.
- Result:
  `NOT-CERTIFIED`, 0 certified / 5 not / 5 witnessed, 101s.
- Interpretation:
  the level0-only strategy succeeded at converting the prior timeout into a
  verdict, but produced no fully bounded region for the four body paths.  The
  result is useful diagnostically but not a strong PUT, and this is why the
  automatic strategy was changed to one refine round instead of zero.

Follow-up planner fix:

- After a strategy attempt fails, a later default retry must not inherit
  `--refine-rounds 0` from the prior schedule.
- `unit_campaign_plan.py` now restores recipe default `--refine-rounds 2` for
  retry jobs that do not match a stage-specific strategy.
- Replanning the attempt-2 result into:
  `/tmp/veriput_stratified_20260807_03/unit-campaign-balanced6b-a2-refinefirst-defaultrestore.json`
  yields one attempt-3 job with `--run-timeout 600`, `--memlimit-gib 10`, and
  `--refine-rounds 2`.
- Do not run this attempt-3 casually: it is the third and final budget level
  for this benchmark unit.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.

## 2026-08-07 retry-adjusted quality and non-retryable failures

Further diagnosis on the same attempt-2 sample:

- `EtherLotto.play` has source-level randomness:
  `uint random = uint(keccak256(abi.encodePacked(block.timestamp, block.difficulty))) % 2;`
  followed by `if (random == 0)`.
- The two not-certified paths for this unit are therefore correctly classified
  as static uncontrolled / hash-nondet inseparable.  More ESBMC time will not
  let a generated PUT force one side of that uncontrolled decision with the
  current coordinate system.
- `AIRBets.transfer` did not start ESBMC at all.  It was refused by the narrow
  recursive-helper preflight:
  `SafeMath.div/2` and `SafeMath.sub/2` are flattened direct self-recursive
  wrappers in the target call closure.

Code change:

- `certify_result_summary.py` now also reports retry-adjusted accounting:
  - `retry_eligible_witnessed_paths`;
  - `method_unsupported_paths`;
  - `retry_adjusted_certified_path_rate`.
- The summary gate uses the retry-adjusted rate.  Raw and slice-adjusted rates
  remain present, so reporting can still show the true number of witnessed
  paths and method limitations.
- `unit_campaign_plan.py` now uses the same retry-adjusted denominator for
  certification quality.  Static uncontrolled / external-call inseparable
  siblings no longer trigger another attempt.
- `unit_campaign_plan.py` also has an explicit `non_retryable` bucket for
  rows that should not consume more ESBMC time under the same recipe:
  - `NO-COORDINATE` / `no generalisable coordinate`;
  - witness preflight refusal, including the direct self-recursive helper
    guard.

Validation on the real attempt-2 sample:

- Retry-adjusted summary:
  `/tmp/veriput_stratified_20260807_03/certify-summary-balanced7-a2-remaining4-retryadjusted.json`.
- Raw certified path rate:
  `3 / 7 = 0.4286`.
- Slice-adjusted certified path rate:
  `3 / 5 = 0.6`.
- Retry-adjusted certified path rate:
  `3 / 3 = 1.0`.
- Method-unsupported paths:
  2, both from `EtherLotto.play`.
- Final campaign plan:
  `/tmp/veriput_stratified_20260807_03/unit-campaign-balanced7-a2-remaining4-nonretryable.json`.
- Final split for the 4 attempt-2 jobs:
  - `completed_ok = 3`;
  - `non_retryable = 1`;
  - `pending_by_attempt = {}`.
- The sole non-retryable reason is:
  `witness preflight refused` for `AIRBets.transfer`.
- This prevents the planner from scheduling any 600s attempt-3 job for this
  sample.  That is a campaign-speed fix, not a proof-strength claim.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_certify_result_summary.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_result_summary.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_certify_result_summary.py scripts/test_unit_campaign_plan.py`
  passed.

## 2026-08-07 certification-first retry planning

Attempt-2 diagnostic run:

- Reused the balanced7 sample's machine-readable diagnosis and filtered the
  single `certification-stage no verdict` job:
  `stress243__ERC-3643__ERC-3643__IdentityRegistryStorage__bindIdentityRegistry`.
- Filtered schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced7-a2-certstage.json`.
- Result JSONL:
  `/tmp/veriput_stratified_20260807_03/certify-results-balanced7-a2-certstage.jsonl`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_03/unit-run-balanced7-a2-certstage.jsonl`.
- Workdir:
  `/tmp/veriput_stratified_20260807_03/certify-work-balanced7-a2_certstage_t130_r120_m8`.
- Policy:
  attempt 2, `jobs=1`, 120s ESBMC budget, 130s unit timeout, 135s runner
  timeout, 8GiB.

Observed result:

- Runner status was `ok`, but the certification row was still `KILLED`.
- The row had `0 certified / 0 not / 1 witnessed`.
- The progress history showed:
  - `started`;
  - `enumerated`;
  - `coordinates-selected`;
  - `outer-round-started` / `outer-round-finished` for level-0;
  - one `linear-refine` round finished;
  - the second `linear-refine` round started and timed out before
    certification.
- This differs from attempt 1, where the same unit reached
  `certify-query-started`.  With the salvaged single-path set and the same
  `--refine-rounds 2`, attempt 2 spent the longer budget in the second refine
  round instead of reaching certification.

Code change:

- `unit_campaign_plan.py` now applies a stage-specific retry strategy only when
  a pending job's `certification_quality.reason` is exactly
  `certification-stage no verdict`.
- For those jobs, the generated retry schedule rewrites both `certify_argv`
  and `dry_run_argv` to use `--refine-rounds 1`.
- The job metadata records:
  - `certification_quality.retry_strategy = certification-first`;
  - `certification_quality.retry_refine_rounds = 1`;
  - a short `retry_reason`.
- Other weak jobs keep the normal recipe defaults.  This is not contract-name
  special casing: it is triggered by the observed progress stage.

Why this is sound for VeriPUT:

- The change only changes how retry budget is spent.  It does not promote a
  witness, region, or fuzz counterexample to a proof.
- Regions still count only after the usual ESBMC certification query succeeds.
- Wider or less-refined retry candidates may fail certification; they will
  remain non-certified if so.  If they verify, they are stronger PUT regions.

Validation without consuming a new ESBMC attempt:

- Replanned balanced7 into:
  `/tmp/veriput_stratified_20260807_03/unit-campaign-balanced7-certfirst.json`.
- New next schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced7-a2-certfirst.json`.
- Summary stayed:
  `completed_ok=2`, `pending_by_attempt={"2": 5}`.
- Weak reasons stayed:
  1 `certification-stage no verdict`, 3 `certified path rate below threshold`,
  and 1 `no certified regions`.
- Only `IdentityRegistryStorage.bindIdentityRegistry` carried
  `retry_strategy=certification-first` and `--refine-rounds 1`.
- The other four attempt-2 jobs kept the normal `--refine-rounds 2`.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/unit_campaign_plan.py scripts/test_unit_campaign_plan.py`
  passed.

## 2026-08-07 slice-adjusted certification quality

Attempt-2 benchmark sample:

- Took the balanced7 attempt-2 retry schedule and filtered out the already-run
  `IdentityRegistryStorage.bindIdentityRegistry` job to avoid spending a
  duplicate second attempt on the same unit.
- Filtered schedule:
  `/tmp/veriput_stratified_20260807_03/next-unit-schedule-balanced7-a2-remaining4.json`.
- Result JSONL:
  `/tmp/veriput_stratified_20260807_03/certify-results-balanced7-a2-remaining4.jsonl`.
- Runner journal:
  `/tmp/veriput_stratified_20260807_03/unit-run-balanced7-a2-remaining4.jsonl`.
- Workdir:
  `/tmp/veriput_stratified_20260807_03/certify-work-balanced7-a2_remaining4_t130_r120_m8`.
- Policy:
  attempt 2, `jobs=1`, 120s ESBMC budget, 130s unit timeout, 135s runner
  timeout, 8GiB.

Raw results:

- Runner status: 4 / 4 `ok`.
- `DepositLog.approvedToLog`:
  `CERTIFIED`, 1 certified / 1 not / 2 witnessed, 3.6s.
- `EtherLotto.play`:
  `CERTIFIED`, 1 certified / 2 not / 3 witnessed, 32s.
- `AIRBets.transfer`:
  `NO-WITNESS-UNDECIDED`, witnessed unknown, 0.1s.
- `IdentityRegistryStorage.storedIdentity`:
  `CERTIFIED`, 1 certified / 1 not / 2 witnessed, 11.6s.

Important diagnosis:

- The not-certified rows for `DepositLog.approvedToLog` and
  `IdentityRegistryStorage.storedIdentity` were both:
  `EXCLUDED FROM THE SLICE by the pins`.
- In both cases the excluded path is the nonpayable ABI value-gate path where
  the path's counterexample has `msg.value != 0`, while the body-first recipe
  intentionally pins `msg.value == 0`.
- The result text already says this is not a certification failure.  Counting
  it against the >=70% certification-quality threshold caused needless
  attempt-3 scheduling.

Code change:

- `certify_result_summary.py` now reports both:
  - raw `certified_path_rate` over all witnessed paths;
  - `slice_adjusted_certified_path_rate`, whose denominator excludes
    `EXCLUDED FROM THE SLICE by the pins` paths.
- The summary also reports:
  - `eligible_witnessed_paths`;
  - `slice_excluded_paths`;
  - not-certified reason bucket `slice-excluded-by-pins`.
- The summary gate uses the slice-adjusted rate when present, while still
  exposing the raw rate for accounting.
- `unit_campaign_plan.py` uses the same slice-adjusted denominator for retry
  quality, so body-slice-ready units do not get requeued just because their ABI
  value-gate path was intentionally outside the slice.

Validation on the attempt-2 sample:

- Slice-adjusted summary:
  `/tmp/veriput_stratified_20260807_03/certify-summary-balanced7-a2-remaining4-sliceadjusted.json`.
- Overall raw certified path rate:
  `3 / 7 = 0.4286`.
- Slice-adjusted certified path rate:
  `3 / 5 = 0.6`.
- Reason buckets:
  2 `slice-excluded-by-pins`, 2 `method-unsupported:static-uncontrolled`.
- Replan path:
  `/tmp/veriput_stratified_20260807_03/unit-campaign-balanced7-a2-remaining4-sliceadjusted.json`.
- Before this fix, the 4-job attempt-2 sample planned 4 attempt-3 jobs.
- After this fix, it plans only 2 attempt-3 jobs:
  - `EtherLotto.play`, still weak because two paths are statically
    inseparable on an ESBMC random/uncontrolled decision;
  - `AIRBets.transfer`, still `no certified regions` because it has no
    witnessed path.
- `DepositLog.approvedToLog` and `IdentityRegistryStorage.storedIdentity` now
  count as completed body-slice units instead of 600s retry candidates.

Checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_certify_result_summary.py scripts/test_unit_campaign_plan.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_certify_result_summary.py`
  passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_unit_campaign_plan.py`
  passed.
- `git diff --check -- notes/coverage/scripts/certify_result_summary.py notes/coverage/scripts/unit_campaign_plan.py scripts/test_certify_result_summary.py scripts/test_unit_campaign_plan.py`
  passed.
