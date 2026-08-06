# VeriPUT Engineering Memory

Last updated: 2026-08-06

This document is the durable handoff state for VeriPUT. It records facts that
were established by reading the paper, the work order, the implementation, and
the existing run artefacts. It is not an experiment result and must not be used
as one. The user explicitly requested this file, overriding the older work-order
rule against creating new Markdown files.

## 1. Current repository state

- Working branch: `feat/veriput-fuzz-first`
- Original takeover snapshot: `5efe5b3252`
  (`[solidity] Strengthen parameterized path test synthesis`)
- Latest pushed commit when the st1inch attempt-3 replay repair began:
  `bb9443d52f` (`[solidity] Recognize all corpus benches in PUT sweep`)
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
