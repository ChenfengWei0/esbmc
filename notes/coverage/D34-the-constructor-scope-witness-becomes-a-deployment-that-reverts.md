# D34 — the constructor-scope witness is rendered as the DEPLOYMENT argument, and forge proves the test RED

**Measured 2026-08-01**, forge 1.7.1 / solc 0.8.34, on the generated fixture pair
from D33. This is the question D33 left open, answered, and the answer is the one
the pipeline must never produce.

## The pair, one line apart

`notes/coverage/poc/D34_CtorCallsUnit/` — identical contracts except for
`setFeeReceiver(feeReceiver_);` in the constructor.

**`off` — constructor does not call the unit.** One test contract, valid
deployment, four cases, full provenance (an asserted normal exit, two
revert-tolerant, one ABI value-gate case that asserts the call MUST fail):

```
Ran 4 tests for test/D32.cov.t.sol:D32CovTest
[PASS] test_cov_0  [PASS] test_cov_1  [PASS] test_cov_2  [PASS] test_cov_3
Suite result: ok. 4 passed; 0 failed
```

**`on` — constructor calls the unit.** TWO test contracts, and the second one's
`setUp` is:

```solidity
c0 = new D32(0, address(uint160(0)));
```

while the constructor runs `setFeeReceiver(address(0))` and that does
`if (feeReceiver_ == address(0)) revert ZeroAddress();`

```
Ran 1 test for …:D32CovTest_0
[PASS] test_cov_0
Ran 1 test for …:D32CovTest_1
[FAIL: ZeroAddress()] setUp() (gas: 0)
Suite result: FAILED. 0 passed; 1 failed
```

## What happened

The **constructor-scope** instantiation of the unit's assert was witnessed with
`feeReceiver_ == address(0)` — the zero-address revert path, taken *inside the
constructor*. The emitter used that counterexample value as the **deployment
argument**. The deployment therefore reverts.

⇒ **A generated test that is RED on the unmodified contract, with nothing marking
it.** That is the one outcome `EXECUTION_PLAN.md` names as the bottom line
("不许砍：4.4 自检闸、R0 — 这三样是「绝不产错测试」的底线").

⇒ **And it is worse than one red test.** A reverting `setUp` means every test
function in that contract never runs. `off` emits 4 cases covering 4 paths and
all 4 execute; `on` emits 3 cases across 2 contracts, of which **one** executes.
The coverage the report claims is not the coverage the artefact delivers, and the
loss is silent.

## What this does and does NOT explain

**Explains**: the duplicate claim key, the disagreeing verdicts (D32), the 2.00
VCC/path ratio unique to `setFeeReceiver` in st1inch (D33), and now a concrete
red test.

**Does NOT explain st1inch's `out of memory`.** On this 20-line fixture BOTH
instantiations solve in milliseconds. The double identity is why one key gets two
different answers; it is not why one of st1inch's answers exhausts 8 GiB. That
remains open, and conflating the two would repeat exactly the mistake D30 was
withdrawn for.

## ⛔ THE FRAMING BELOW WAS WRONG AND IS CORRECTED HERE (user, 2026-08-01)

Everything from "Candidate fixes" down was written as a two-option DESIGN choice
to be settled by measuring the baseline's scope. **It is not a design choice, and
the baseline does not get to settle it.**

**It is fixed by our own Problem Definition.** A unit `u` is an externally
callable ENTRY POINT; a path is the complete decision sequence of ONE CALL from
entry to return or revert; the path domain is the set of inputs A TEST CAN SUPPLY
that walk it. The constructor-scope execution is not an external call — it has no
calldata, no ABI value gate, and no originating account. **It is not a member of
`X_u` at all.** So option (2) — "count it, and key/refuse around it" —
contradicts a definition this project has already written down. It is not an
option.

**And it breaks the soundness proposition, which is why this is not accounting.**
The proposition is that every input the emitted test admits lies in `D_π` and the
test passes on the contract. If the constructor-scope execution counts as
covering `π`, the values it took enter our understanding of `D_π` — and no
transaction on chain can reproduce them, so the rendered test is RED. The note
below already observed "it renders no legal test" and then filed it as a
bookkeeping problem. **It is a soundness problem.**

**The comparability argument below has its direction backwards, and is
withdrawn.** It reasoned: if the baseline counts deployment-time executions in
its reach, refusing them costs us against the baseline. The correct reading is
the opposite — that would mean the BASELINE is crediting a coverage with no
emittable test, i.e. measuring something the artefact cannot match. The response
is not to loosen our definition to a looser denominator; it is to REPORT the
difference as a fact (both denominators, the reason, the size). That is the
discipline this project already applies when it lists every path that got no test
together with its reason.

⇒ The baseline-scope measurement recorded below **stays**, but it is re-filed:
it is an EVALUATION scope datum, not the basis of a decision.

⇒ **The decision is A: a constructor-scope execution does not witness a unit
path.** The cost — a path reachable only from constructor scope stays U forever —
is honest, because such a path genuinely has no test, and "no test, with the
reason" is already the reporting format.

⇒ **One thing below survives independently of all of this**: the claim must be
keyed by `(signature, instantiation)`. That reason has nothing to do with whether
the execution counts. The same function body executed twice in one run yields TWO
independent verdicts, and sharing one key merges them into one. That is an
accounting defect under A and under B alike.

<details>
<summary>The superseded framing, kept because its measurements are still valid</summary>

## Candidate fixes — CANDIDATES, none checked against the instrumentation site

1. **Do not let a constructor-scope execution witness a UNIT path.** A unit's
   path set is about calls the ABI can make; the constructor is not one. The
   frontend already distinguishes constructor scope
   (`current_function_revert_observable = !is_ctor`, the `_sol_save_this`
   snapshot), so the notion exists.
2. **Key the claim by (claim_sig, instantiation)** so the two do not collide, and
   have the emitter REFUSE any case whose provenance is constructor scope — the
   same shape as the `named-obstacle` refusal already built in `a6ea07f2e9`,
   where marking without excluding was the half-fix.

Which is right depends on whether the constructor-scope execution should count as
coverage at all. That is a methodology question, and it had ONE testable input
that had not been taken.

### The comparability objection to (1), tested and NOT supported

The objection runs: our gate is compared against branch coverage, branch coverage
counts a decision as reached when it EXECUTES regardless of who called it, so
refusing constructor-scope witnesses would make us score below the baseline on
the same denominator for a reason that is not about the method.

Measured, on the same fixture pair, with the baseline's own flag set
(`--branch-coverage-claims --coverage-whole-unit --k-induction
--unlimited-k-steps --no-assertions`):

| cell | Branches | Reached | covered decision lines |
|---|---|---|---|
| constructor does NOT call the unit | 4 | **4** | the `require` and the `if` |
| constructor CALLS the unit | 4 | **4** | the `require` and the `if` |

**Identical.** The constructor call adds nothing to the baseline's numerator,
because the dispatcher call alone already reaches both decisions.

And there is a structural reason to expect that generally, not just here: for a
PUBLIC unit the dispatcher supplies a NONDET argument, so any decision in the
body that depends on the argument is dispatcher-reachable by construction. The
one decision that depends on state — `require(msg.sender == owner)` — is
*constant-true* in constructor scope (owner was just set to msg.sender) and has
BOTH arms available under the dispatcher. The dispatcher covers more, not less.

⇒ **On this evidence option (1) costs nothing in comparability**, and it removes
the red test rather than annotating it. That is a recommendation, not a ruling:
one fixture plus a structural argument.

⚠ **The residual, named rather than waved away**: a decision reachable ONLY from
constructor scope would have to depend on storage that exists only mid-
construction. Nothing in this fixture has one, and nothing here searched the
corpus for one. If such a decision exists, option (1) loses it and the baseline
keeps it.

</details>

## Reproduction

```
python3 notes/coverage/scripts/gen_exp_chain_poc.py 3 D.sol --ctor-calls-unit
solc --ast-compact-json D.sol > D.sol.solast
esbmc D.sol.solast --sol D.sol --solidity-path-coverage --contract D32 \
      --focus-function setFeeReceiver --solidity-max-tx 1 --cov-report-json \
      --memlimit 4g --z3 --tuple-node-flattener --generate-foundry-testcase
# put D.sol and D32.cov.t.sol in a forge project's test/ and run `forge test`
```

Drop `--ctor-calls-unit` for the green control.
