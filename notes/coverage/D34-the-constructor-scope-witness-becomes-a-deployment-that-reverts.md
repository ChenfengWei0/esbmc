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
coverage at all, and that is a methodology question, not only an implementation
one. Neither has been attempted here.

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
