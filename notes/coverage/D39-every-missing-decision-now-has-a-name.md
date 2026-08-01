# D39 — farming's `4/12` is 5 killed + 3 constructor-scope, and the constructor number is the one the ruling predicted

**Measured 2026-08-01** with `notes/coverage/scripts/gap_lines.py`.

`branch_gate.py` compares COUNTS — correctly, because METHODOLOGY §4 records the
baseline's reach as a count and the locked JSON carries no per-decision identity.
That is useless for *fixing* a gap: `4 / 12 contracts/FarmingPool.sol` does not
say which eight. Our side does have the identity (every F claim publishes a
`decisions` array with a flat line), so the missing set is computable on our side
alone, and each miss can be attributed to the **AST node that encloses it**.

## farming's gap is fully attributed — 0 unexplained

| flat | decision | enclosing definition | cause |
|---|---|---|---|
| 4135 | `if (stakingToken_ == rewardsToken_) revert …` | `FarmingPool.constructor` | **constructor-scope** |
| 4136 | `if (address(stakingToken_) == address(0)) …` | `FarmingPool.constructor` | **constructor-scope** |
| 4137 | `if (address(rewardsToken_) == address(0)) …` | `FarmingPool.constructor` | **constructor-scope** |
| 4196 | `if (token == IERC20(address(0)))` | `FarmingPool.rescueFunds` | killed unit |
| 4199 | `if (token == STAKING_TOKEN)` | `FarmingPool.rescueFunds` | killed unit |
| 4200 | `if (STAKING_TOKEN.balanceOf(…) < …) revert …` | `FarmingPool.rescueFunds` | killed unit |
| 4201 | `} else if (token == REWARDS_TOKEN)` | `FarmingPool.rescueFunds` | killed unit |
| 4202 | `if (REWARDS_TOKEN.balanceOf(…) < …) revert …` | `FarmingPool.rescueFunds` | killed unit |

**5 killed + 3 constructor-scope = 8.** The "farming `4/12` is unattributed" item
is closed, and neither half is a reach failure of the method.

`FarmingPool.rescueFunds` is one of D38's three killed units. Its five decisions
are missing because the run did not finish, not because the paths are hard.

## ⇒ The 3 constructor-scope decisions are the quantity the A/B ruling predicted

The ruling on constructor-scope coverage (task #36, answer **A**) settled it from
the Problem Definition rather than from measurement: a unit is an externally
callable **entry point**, a path is one call from entry to return, so a
constructor-scope execution is not in any unit's path set. It also said what to
do about the baseline:

> 该做的是把这个差异当成一个可报告的事实——两边分母不同、原因是什么、差多少。

**This is the first measurement of "差多少" on a real benchmark: 3 of farming's
26 canonical decisions.** The baseline reaches 26/26 including these three; our
method cannot reach them and must not, because no emitted test can execute them —
they run once, at deployment, with no calldata and no ABI value gate. Reporting
them as a named denominator difference is what the ruling asked for, and it is
now a number rather than a prediction.

## The attribution is STRUCTURAL, not textual

`stakingToken_` has a trailing underscore, so it *looks* like a constructor
parameter. That inference is exactly the kind this project has been burned by, so
the tool reads solc's own `FunctionDefinition.kind == "constructor"` off the AST,
reusing `ast_decisions`' byte→line map and file blocks so it cannot disagree with
the canonical set it is joined against.

## What the same tool says about the other benchmarks — NOT yet attributed

Run and recorded, but the causes below are **candidates, not findings**:

| bench | missing | what they are |
|---|---|---|
| `aqua_Aqua` | 1 | `require(tokensCount1 > 0 && … != _DOCKED)` in `Aqua.safeBalances`. That unit RAN (F 2, U 9), so this is a genuine reach gap — the decision sits on paths that stayed U. |
| `cross_chain_swap_EscrowDst` | 12 | 2 in modifiers (`onlyValidSecret`, `onlyAccessTokenHolder`), 2 in `EscrowDst._withdraw` (an **internal** callee of `withdraw`, which DID report F 5/5), 8 in `ImmutablesLib`. |
| `cross_chain_swap_EscrowSrc` | 10 | 2 in `BaseEscrow` (`onlyValidSecret` modifier, `_ethTransfer`), 8 in `ImmutablesLib`. |

Three things worth noting without yet claiming them:

* **`onlyValidSecret` is missing on BOTH Escrows** and its guard is
  `_keccakBytes32(secret) != hashlock` — a crypto-inversion guard, which
  METHODOLOGY §10 already names as a reach gap. But the baseline's P2 reaches
  16/16 on EscrowSrc, so the baseline got it and we did not; that asymmetry is
  not yet explained.
* **`EscrowDst._withdraw`'s two decisions are missing although its caller
  `withdraw` witnessed 5 of 5 paths.** An internal callee's decisions are
  supposed to be part of its caller's path identity. EscrowDst also shows
  depth-bound truncation on 4 of 4 runs with **8 sites past the bound** — a
  candidate, and testable.
* **`ImmutablesLib`'s 8 split 4 `internal` / 4 `external`.** Task #33 attributed
  the whole 0/8 to the `--function` ban, which is a clean explanation for the 4
  external ones. The 4 **internal** ones should be reachable through their
  callers, so that half of the attribution is now in doubt and needs re-checking.

None of these is claimed here. The point of this note is that the corpus's
missing decisions are now **enumerable and individually named**, which is the
precondition for attributing any of them.

## Files

`notes/coverage/scripts/gap_lines.py`.
