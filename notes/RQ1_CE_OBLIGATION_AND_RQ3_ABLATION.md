# RQ1 CE Obligations and RQ3 Ablation

## Purpose

This document freezes the agreed experimental interpretation of RQ1 and RQ3.
It is the continuity record for future work on concrete replay preservation and
PUT generalization.

## Frozen RQ1 Population

RQ1 is counted at the instrumented path / counterexample obligation grain.
One obligation is identified within one target contract by:

```text
(target, path_function, unit, enc, piece)
```

The frozen canonical-current population is:

```text
generalized CE obligations:      1263
not-generalized CE obligations:   545
total CE obligations:            1808
```

The partition invariant is:

```text
1263 + 545 = 1808
```

The total must not change during further PUT work. An obligation may move only
from `not-generalized` to `generalized`.

The source-of-truth ledger is:

```text
notes/coverage/rq1_ce_obligations.frozen.json
```

Its SHA-256 when frozen was:

```text
215dacecc8ab50fe9784aab00835152626d6946eba2f3e3cee8b88c8203bcde1
```

The reporting command is:

```bash
python3 notes/coverage/scripts/rq1_final_test_inventory.py \
  --result-root /home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT \
  --report notes/coverage/rq1_final_test_inventory.current.json
```

The command fails if the physical obligation population differs from the frozen
ledger. Changing the population requires an explicit `--freeze-ledger` and must
not happen implicitly during ordinary generation, adoption, or auditing.

## What The Counts Mean

`generalized CE obligations` are unique CE identities backed by an existing
parameterized Solidity test and a strict-valid result row.

`not-generalized CE obligations` are unique CE identities backed by:

- an existing zero-parameter Solidity test;
- an assertion about the target execution result;
- audited Forge execution evidence;
- no current parameterized PUT for the same CE identity.

The 545 concrete tests all contain an audited execution-result oracle. The
observed oracle kinds include call status, normal completion, return value,
revert, post-state, event log, and direct storage-slot post-state.

The following are deliberately excluded from the three headline counts:

- PUT basis replay rows;
- retry or duplicate result rows;
- manifest entry counts;
- same-path candidates without confirmed classification;
- historical-best artifacts outside the current physical canonical inventory.

The earlier `1289 PUT + 554 concrete = 1843` report was not a consistent CE
grain. It mixed result rows and exact concrete identities. Direct inspection of
the Solidity test files found duplicate rows and stale `kind` labels. The
physical, identity-deduplicated count is 1263 + 545 = 1808.

## Current Case-Level RQ1 State

After restoring the strongest retained artifacts for ScalingPriceFeed and
WstETHPriceFeed, canonical-current case counts are:

```text
target contracts:       509
valid cases:            509
no-valid cases:           0
valid-no-PUT cases:      15
PUT-no-R1/R2 cases:     155
PUT-with-R1/R2 cases:   339
```

These are target-case counts. They must never be added to or substituted for
the 1808 test-level CE obligations.

## The Required PUT Strength Property

The purpose of retaining the concrete witness behavior is not merely to record
provenance that a PUT originated from a CE.

The critical soundness requirement is:

> A generalized PUT must not lose the observable failure or behavior that the
> original concrete replay exposed.

A concrete replay can reproduce an error while a purportedly generalized PUT
fails to detect it. In that situation the PUT is behaviorally weaker, even if
it shares the same path identity and passes its own fuzz assertions.

For every generalized obligation, the intended test project therefore needs
both:

1. A parameterized PUT that checks the generalized property.
2. A fixed witness-anchor test that uses the original CE inputs and asserts the
   original observable behavior.

The anchor assertion must concern the target execution result, such as:

- exact or typed return value;
- expected revert or call status;
- relevant post-state value;
- emitted event data;
- normal completion only when normal completion is itself the authenticated
  observable property.

The anchor does not create another CE obligation and does not increase 1808.
It is a strength-preservation check attached to the same generalized
obligation.

The required acceptance condition is conceptually:

```text
parameterized PUT passes
AND
original fixed CE witness still reproduces its authenticated observable result
```

If the concrete witness still exposes an error but the PUT cannot observe it,
the obligation must not be reported as successfully generalized.

### R0 encoding and the atomic Foundry execution protocol

Methodologically, the fixed CE assertion is part of the generalized PUT's R0.
The PUT artifact denotes the conjunction:

```text
PUT obligation = parameterized R1/R2 test AND fixed-witness R0 test
```

The current Foundry encoding places these two conjuncts in the same final
Solidity source file and the same test contract, but in two functions:

```solidity
function test_put_<identity>(/* fuzz parameters */) public { /* R1/R2 */ }
function test_ce_anchor_<identity>() public { /* fixed CE and R0 assertion */ }
```

The separate zero-parameter function is an isolation mechanism, not a second
CE obligation and not an optional companion test. Foundry restores the test
state for each test function, so this encoding prevents the fixed CE execution
from mutating the state used by the fuzz execution. It is accepted as the
engineering representation of an R0 embedded in the PUT artifact only when
the execution and reporting pipeline treats both functions as one atomic
acceptance unit.

Consequently, a Forge result obtained only with a selector such as
`--match-test '^test_put_'` is incomplete and must never establish a valid or
generalized PUT. The permitted execution protocols are:

1. run the final test file and verify from Forge's machine-readable result that
   the exact PUT test and the exact CE-anchor test both executed and succeeded;
2. run two exact, source-bound invocations, one selecting the PUT test and one
   selecting the anchor test, and join them through the same source SHA-256,
   PUT identity, test contract, and evidence record.

The paired runner must fail closed when either test is absent, filtered out,
not executed, failed, timed out, or reported only by a process return code
without an exact per-test `Success` record. The parameterized test must also
meet the configured fuzz-run requirement. Both exact test names, commands,
Forge JSON records, and the final source hash must be retained in metadata.

This yields suite-level semantic equivalence to placing both assertions in one
test function while preserving state isolation. It does **not** justify
claiming that the R0 statement text occurs inside the parameterized function
body. The paper and reports must instead describe it as an isolated R0 test
inside the same atomic PUT artifact.

## Recovering Witness Anchors

Witness anchors should first be recovered from retained evidence, without
rerunning path discovery:

- `cov-ce-journal.json`;
- `cov-report.json`;
- `put.json`;
- retained generated concrete tests;
- Forge execution logs and put summaries.

A preliminary join against the 1263 physical generalized obligations found:

```text
1165 obligations with an exact retained CE claim
  98 obligations without an exact claim in that retained inventory
```

The 1165 exact claims currently break down approximately as:

```text
normal-exit observations: 595
fixed return observations: 396
state-delta observations:  174
```

These figures are a recovery-planning inventory, not a replacement for the
frozen 1263/545/1808 headline counts. Each recovered anchor must still be
validated against its actual Solidity source and retained execution evidence.

## Oracle Proof And Execution Gates

Do not conflate the three different checks attached to a generalized output:

The required verifier-mode boundary is:

```text
Path Enumeration:
  instrument path-denial assertions;
  use bounded unwind / incremental reasoning to enumerate paths and obtain CEs.

Certified Input Region:
  instrument the candidate region and same-path/same-boundary properties;
  prove them with --k-induction.

R1/R2 Assertion Synthesis:
  instrument each generalized behavioral assertion under the certified region;
  prove it with --k-induction.
```

A bounded or incremental result is acceptable for Path Enumeration because its
purpose is to discover a path witness. It is not sufficient for either region
certification or an R1/R2 oracle. Those two stages must retain an artifact only
when the k-induction proof succeeds; timeout, unknown, base-case-only success,
or a bounded `HOLDS` result must fail closed.

The implementation must also keep separate verifier argument channels. Path
Enumeration may retain arithmetic safety checks and its unwind/incremental
configuration. Region and R1/R2 proof queries must not inherit
`--overflow-check`, `--div-by-zero-check`, `--unwind`, `--unwindset`,
`--unwindsetname`, or `--partial-loops`; those add unrelated obligations or
reintroduce a finite proof bound. Their proof argument set is instead fixed to
`--k-induction --max-k-step 30` plus solver/model options that do not alter the
property population. The result metadata must record enumeration and proof
arguments separately.

1. The fixed witness anchor is an R0 replay assertion. It copies the
   authenticated observable result of the original CE and does not require a
   generalized verifier proof.
2. Generalized R1/R2 assertions are submitted as behavioral assertions under
   the certified input part. Only a completed k-induction proof may emit a
   generalized test oracle. A plain or bounded `HOLDS` result is insufficient.
3. The constructed Foundry PUT is then executed with fuzzed parameters. Forge
   is a dynamic counterexample and construction-consistency gate; it is not a
   replacement for the verifier proof.

The paper's soundness claim requires a generalized behavioral oracle to have a
proof that does not depend on a finite loop-unwind bound. K-induction is the
intended mechanism when ordinary BMC cannot provide that proof. Forge fuzzing
cannot replace this obligation because it can find counterexamples but cannot
establish the assertion for every admitted input.

The retained RQ1 implementation evidence currently exposes a methodology gap:
`assert/run.log` commands use `--path-cov-assert` with a bounded Solidity
harness (`--solidity-max-tx 1` and an automatic loop unwind, commonly 4) and do
not contain `--k-induction`. The logs explicitly label such successful results
as bounded. A bounded `HOLDS` result is therefore insufficient for the paper's
generalized-oracle claim. The implementation must export the certified-region
premise and behavioral assertion to a proof query that can establish a
bound-independent result, and must accept R1/R2 only after that proof succeeds.
The subsequent Forge fuzz run remains a separate construction and dynamic
counterexample gate.

## K-Induction Harness Shape

The proof harness must not deploy or call a second instance of the target. In
particular, it must not generate either of these shapes:

```solidity
Target target = new Target(...);
Result result = target.targetFunction(x);
```

or a separate `Harness` contract that calls a deployed `Target`. Both shapes
introduce another address, cross-contract dispatch, and different
`msg.sender`, storage, revert, return, and event semantics.

Instead, the proof query operates on the original target instance and original
target function. The implementation creates a temporary instrumented source or
GOTO program with this logical shape:

```text
original Target constructor
original targetFunction, selected by --focus-function
  entry: assume certified-region coordinates and authenticated fixture state
  entry: capture pre-state used by R1/R2
  body:  execute the original function body on the current Target instance
  exits: assert same path and same boundary
  exits: assert the selected R1/R2 properties
```

The proof command retains `--solidity-max-tx 1`, adds `--k-induction
--max-k-step 30`, and does not add a wrapper transaction. Thus the verifier
constructs the original contract once and dispatches exactly one focused target
call. Loops inside that call are the loops handled by k-induction.

If source-level insertion is required, it modifies a temporary copy of the
target function rather than adding a callable proof wrapper. Prefer GOTO-level
insertion because it leaves Solidity call semantics unchanged. Constructor and
fixture inputs remain those authenticated for the PUT; setup state that cannot
be represented without an extra transaction must be encoded by the existing
establishment model and may not be silently replaced by a wrapper call.

### Direct Foundry-contract experiment

An alternative was measured by flattening an existing Foundry PUT and running
ESBMC on the test contract with `--bound --contract <test> --focus-function
<test_put> --k-induction --max-k-step 30 --solidity-max-tx 1`.

The complete `forge-std` flatten was 21,881 lines / 775,695 bytes. ESBMC spent
the full 120-second cap in Solidity conversion and never entered k-induction
(peak RSS about 765 MiB). A minimized equivalent test contract converted and
reported an inductive solution at k=2 in 0.62 seconds / 157 MiB, but the result
was vacuous: the frontend printed `UNMODELED cheatcode vm.deal -> path pruned`
and the same for `vm.prank`. The target call and its assertion were therefore
not explored.

Direct Foundry-contract proof is not currently an acceptable shortcut. It can
be reconsidered only after every used cheatcode has a faithful verifier model,
the proof records target-call non-vacuity, and unused `forge-std` code is
removed without changing test semantics. Until then, original-target/GOTO
instrumentation is the sound primary route.

### Internal versus external k-induction

There are two possible k-induction proof surfaces. They use the same induction
engine but verify different transition systems:

- **Internal k-induction** focuses the original target function. Certified
  region assumptions, authenticated fixture state, path-boundary checks, and
  R1/R2 assertions are inserted in a temporary source/GOTO program. This is the
  primary proof authority because it preserves the target call semantics and
  does not depend on Foundry cheatcode modeling.
- **External k-induction** focuses the generated Foundry test function. The
  target call is nested inside that one test transaction. This verifies the
  delivered test more directly, but is sound only when every reached cheatcode
  has a faithful operational model and the target call plus its assertions are
  proven non-vacuous. An unmodeled cheatcode must fail the proof; pruning the
  path is not a successful proof.

The external route does not require all Foundry cheatcodes. It requires the
ones actually used by the frozen PUT population. A source inventory of the
1263 unique PUT obligations found:

```text
environment/sender-only cheatcodes:                   265
raw vm.load/vm.store, without mock/expectRevert:       791
vm.etch/vm.mockCall/vm.clearMockedCalls:               175
vm.expectRevert, without external mocks:                32
total:                                                1263
```

The categories above are mutually exclusive and assign external-mock tests
first, then expect-revert tests, then raw-storage tests. Individual call counts
include `vm.prank` in 1206 PUTs, `vm.load` in 909, `vm.store` in 838,
`vm.deal` in 216, `vm.etch` in 170, and `vm.mockCall` in 153. Therefore a small
environment/sender model can soundly cover 265 PUTs first. Full external proof
coverage also requires correct raw EVM storage-slot semantics and external-call
mock semantics; treating these operations as no-ops or assumptions is
unsound.

The implementation order is consequently:

1. make internal k-induction the mandatory region/R1/R2 proof authority;
2. add a hard non-vacuity/error gate for external Foundry verification;
3. model the environment/sender subset and externally cross-check its 265 PUTs;
4. extend external verification to raw storage, then external mocks and
   expect-revert, with positive and negative regressions for each operation.

### K-induction diagnostic prior

The existing valid PUT corpus is the positive diagnostic corpus for the new
k-induction pass. Each retained PUT has already passed the bounded ESBMC
candidate check and an independent Forge fuzz gate. This is not a substitute
for an unbounded proof, but it is strong engineering evidence when diagnosing
the verifier:

- `UNKNOWN`, timeout, out-of-memory, or failure to close the inductive step is
  recorded as **verifier inconclusive** and does not invalidate the PUT;
- a k-induction counterexample invalidates or narrows a PUT only after the
  concrete trace is translated and reproduced against the same Foundry test;
- when the base cases agree with the existing oracle and only the inductive
  step fails to converge, optimization work targets ESBMC's induction
  transformation, slicing, invariant generation, and solver query rather than
  weakening the test by default.

This policy lets the 1263 existing PUTs act as a regression benchmark for
improving k-induction while keeping proof status distinct from test validity.

### Internal k-induction checkpoint

The bounded baseline was committed as `b33ced62f3` before changing proof
strategy.  Region certification and R1/R2 assertion queries now remove
`--unwind*`, `--partial-loops`, `--incremental-bmc`, `--overflow-check`, and
`--div-by-zero-check`, then run with:

```text
--k-induction --max-k-step 30 --solidity-max-tx 1
```

Path enumeration and CE refutation remain bounded/incremental; this change is
only for proving a candidate region and its synthesized oracle.  Two isolated
checks using the new binary closed at `k = 2`: BaseEscalationManager's
`isDisputeAllowed` R2 return oracle (peak RSS 186,904 KiB) and its certified
region query (peak RSS 183,980 KiB).  These are feasibility samples, not a
claim that the whole corpus has been re-proved.

Each newly generated PUT file also contains an independent zero-parameter
`test_ce_anchor_<hash>()`.  Its body is copied from the authenticated
certified-basis replay and retains the fixed target call and concrete
return/state/revert assertion.  The fuzz PUT function is not modified and the
two tests run with separate Foundry setup state.  The PUT Forge gate requires
both functions to pass.  An isolated BaseEscalationManager integration check
passed the 256-run fuzz PUT and the fixed CE anchor separately.

The first implementation incorrectly accepted a fresh witness merely because
it had the same path identity.  That is not the source CE.  The current gate
is deliberately stricter: the fresh claim's complete scalar input,
environment, and entry-state map must equal the certified detail's CE, and the
ESBMC report and emitted Foundry case must carry the same SHA-256 fingerprint
of the testcase reconstructed from that solver model.  After result-oracle
insertion, the selected Foundry `setUp` and target-call body are hashed again;
the attachment step recomputes both hashes from the final basis source.  The
fixed return oracle, when present, must equal the certified CE return.  A
fixture or repair that changes the caller, call arguments, environment, setup,
or expected result is refused until a dedicated equivalence materializer can
prove that transformation.  This may reduce anchor yield; it cannot silently
substitute another point on the path.

The k-induction integration also exposed a verifier bookkeeping bug: a base
case `P` verdict survived an inconclusive inductive step and was later printed
as `K-INDUCTION HOLDS`/`CERTIFIED`.  Coverage reporting now authorizes those
labels only after the strategy-level forward condition or inductive step
actually closes.  On max-k exhaustion, base-only `P` rows are downgraded to
`UNDECIDED` while concrete `F` witnesses remain refutations.  Dedicated
regressions cover a positive proof at `k = 2` and forced inductive-step
exhaustion for both region certification and R1/R2.

The existing bounded-proof-plus-Forge corpus is a strong empirical oracle for
the k-induction work: its tests have already passed both the old ESBMC query and
independent Foundry fuzz execution.  Therefore, when the new k-induction run is
inconclusive on one of these established tests, the default diagnosis is a
weak inductive step or missing invariant, not an immediate assertion defect.
This is prioritization evidence, not permission to label an inconclusive run
proved; only an actual forward-condition or inductive-step closure may publish
`HOLDS`/`CERTIFIED`.

## RQ3 No-Certification Ablation

RQ3 `VeriExploit/No_Cer_Reg` is a concrete-only ablation with a 600-second
budget. It spends its budget on path/CE discovery and concrete replay
generation rather than PUT certification and generalization.

Its completed result is:

```text
valid concrete tests: 2140
PUT tests:               0
invalid tests:           0
PUT leaks:               0
```

Dataset totals are:

```text
bugfix124: 322
peer182:   988
real203:   830
total:    2140
```

The difference between RQ3's 2140 concrete tests and RQ1's frozen 1808 CE
obligations is expected and meaningful. RQ3 gives path discovery up to 600
seconds and does not spend time on PUT work. RQ1 divides its budget among path
discovery, certification, PUT generation, oracle synthesis, and Forge gates.
The Stage-1/Stage-2 folding and stopping decisions therefore need not discover
the same path population.

RQ3 must remain an independent ablation result. Its 2140 tests must not be
silently merged into RQ1's 1808 obligations.

RQ3 can still be used as engineering evidence:

- reuse its CE, path, instrumentation, and logs to avoid rediscovering a CE;
- prioritize RQ3 paths whose discovery time fits the default RQ1 budget;
- identify cases where the 600-second budget, rather than PUT logic, explains
  the additional concrete tests;
- test improvements to PUT generalization on an already available CE.

Earlier exact-identity comparison found that RQ3 overlaps 142 of the then
not-generalized RQ1 concrete artifacts across 75 targets. Of these, 31 came
from RQ3 cases with total wall time no greater than 120 seconds, while 40 had
Stage-2 time no greater than 120 seconds. These are useful prioritization
signals, but an RQ3 concrete test is not automatically a valid RQ1 PUT.

## Next Work

1. Keep the 1808 obligation ledger frozen.
2. Build a witness-anchor audit for the 1263 generalized obligations.
3. Recover anchors from retained CE/log evidence before considering reruns.
4. Reject any generalized classification whose PUT loses the source concrete
   witness's observable behavior.
5. Resume conversion of the 545 not-generalized obligations only after the
   strength-preservation gate is enforced.
6. Use RQ3 evidence as a prioritized CE source, while keeping its 600-second
   ablation population separate from RQ1 reporting.
