# VeriPUT Engineering Memory

Last updated: 2026-08-05

This document is the durable handoff state for VeriPUT. It records facts that
were established by reading the paper, the work order, the implementation, and
the existing run artefacts. It is not an experiment result and must not be used
as one. The user explicitly requested this file, overriding the older work-order
rule against creating new Markdown files.

## 1. Current repository state

- Working branch: `feat/veriput-fuzz-first`
- Snapshot commit: `5efe5b3252` (`[solidity] Strengthen parameterized path test synthesis`)
- Pushed remote branch: `E-SOL/feat/veriput-fuzz-first`
- Snapshot checks:
  - `python3 scripts/test_solidity_path_generalise.py`: passed
  - `python3 scripts/test_solidity_path_put.py`: 109/109 passed
  - Python byte compilation of staged scripts: passed
  - `git clang-format` was applied to the two changed C++ files and then
    reported no further changes
- Not run for the snapshot: C++ rebuild, CTest, Forge, or any POC ESBMC run.
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
