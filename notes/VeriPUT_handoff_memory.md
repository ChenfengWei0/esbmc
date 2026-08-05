# VeriPUT Engineering Memory

Last updated: 2026-08-05

This document is the durable handoff state for VeriPUT. It records facts that
were established by reading the paper, the work order, the implementation, and
the existing run artefacts. It is not an experiment result and must not be used
as one. The user explicitly requested this file, overriding the older work-order
rule against creating new Markdown files.

## 1. Current repository state

- Working branch: `feat/veriput-fuzz-first`
- Original takeover snapshot: `5efe5b3252`
  (`[solidity] Strengthen parameterized path test synthesis`)
- Current static pipeline commit: `0298564375`
  (`[solidity] Harden VeriPUT single-POC pipeline`)
- Pushed remote branch: `E-SOL/feat/veriput-fuzz-first`
- Snapshot checks:
  - `python3 scripts/test_solidity_path_generalise.py`: passed
  - `python3 scripts/test_solidity_path_put.py`: 110/110 passed
  - Python byte compilation of staged scripts: passed
  - `git clang-format` was applied to the two changed C++ files and then
    reported no further changes
- Not run after the static batch: C++ rebuild, CTest, Forge, or any POC ESBMC run.
  Other experiments are using the machine, and a real POC run is a scarce
  measurement, not a compile check.
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
- Each POC may be rerun through ESBMC at most once, not twice.
- Do not run a POC merely to answer a question already answered by logs, source,
  unit tests, a small synthetic regression, or GOTO inspection.
- The eventual global generalisation target is at least 70%. This is a delivery
  threshold, not permission to abandon the remaining paths without attribution.

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

The Python unit tests exercise many of these mechanisms, including state pins,
mapping members, sender/value gates, R2 proposal, rollback, antichains, guarded
assertions, and width provenance. This is implementation coverage, not proof
that the official POC entry point enables the mechanisms.

## 5. Current gaps from the frozen method

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
