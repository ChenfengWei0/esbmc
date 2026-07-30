# Every local declared with an initialiser inside an INHERITED function was zeroed

**Fixed** in `src/solidity-frontend/solidity_convert_decl.cpp`, one condition:

```cpp
-  bool set_init = has_init && !is_inherited && !is_storage_ref_alias;
+  bool set_init = has_init && !(is_inherited && is_state_var) && !is_storage_ref_alias;
```

Regression: `regression/esbmc-solidity/inherited_local_initializer/`.

**Not a path-coverage defect.** It reproduced with no coverage flag at all
(`esbmc contract.sol --contract D`), and it was the sole reason the st1inch
benchmark produced nothing at all.

## Root cause

`merge_inheritance_ast()`'s `add_inherit_label()` stamps `is_inherited` on
**every** sub-node that carries an `id`. That includes locals inside an
inherited function body, not just state variables.

`get_var_decl()` serves both state-variable declarations **and**
`rule variable-declaration-statement` — see its own header comment. It
suppressed the initialiser for anything marked inherited. The justification
written next to that line covers state variables only: their initial value is
replayed later by `move_inheritance_to_ctor()` as `D.x = B.x`. **A local has no
such step to be deferred to**, so its value was not moved — it was lost, and
replaced by `gen_zero`.

The derived contract's copy is the one the dispatcher calls, so the correct base
copy never executes. Measured on the reduced case: `sol:@C@B4@F@f#17` carries
`ASSIGN y = x + 1` while `sol:@C@D4@F@f#17` carried `ASSIGN y = 0`.

## Reproducers, in `notes/repro/`

| file | shape | before the fix |
|---|---|---|
| `immutable_clamp_1_ok.sol` | everything in one function, immutable set in the contract's OWN constructor | correct — stays symbolic |
| `immutable_clamp_2_param.sol` | immutable set in a BASE constructor; internal fn reassigns its `timestamp` PARAMETER | wrong — `t == 0` |
| `immutable_clamp_3_local.sol` | same as 2 but clamps into a fresh LOCAL | wrong — `ts == 0` |
| `inherited_local_init_4_plain.sol` | **no** immutable, **no** ternary, **no** `block.timestamp` — just `uint256 y = x + 1;` in a base function | wrong — `y == 0` |

Reproducer 4 is the one that names the bug. Everything about immutables,
ternaries and clamps in 1–3 was incidental: the trigger is nothing more than
*a local declaration with an initialiser, inside a function that is inherited*.
2 and 3 fail identically, so reassigning a parameter is not the trigger either.

## What the model computed (reproducer 3, before the fix)

Complete SSA, 149 steps; every step below is verbatim:

```
/* 93  */ origin_            == block_timestamp                  <- base ctor arg, correct
/* 96  */ tmp$1#3            == (tmp$1#1 WITH [ORIGIN := origin_])
/* 97  */ _ESBMC_aux_Base3#1 == tmp$1#3                          <- aux has it
/* 98  */ _ESBMC_Object_D3#1 == (#0 WITH [ORIGIN := _ESBMC_aux_Base3#1.ORIGIN])
/* 127 */ timestamp#1        == block_timestamp + 63072000       <- argument binding, correct
/* 133 */ ts                 == 0                                <- WRONG
/* 134 */ t                  == 0
/* 135 */ r                  == 1000                             <- returns `balance` unchanged
/* 139 */ timestamp#1        == block_timestamp + 63072001       <- second call, correct
/* 145 */ ts                 == 0                                <- WRONG again
/* 149 */ (assume)0                                              <- the contract's own check reverts
```

Step 133 is `uint256 ts = timestamp < ORIGIN ? ORIGIN : timestamp;`. Its two
arms are `block_timestamp` and `block_timestamp + 63072000`; neither can be 0,
and both operands are symbolic at the point of use (steps 98 and 127). The value
was not selected from an arm — the declaration simply never got one.

The goto program was CORRECT, which is what kept sending the search downstream.
From `--goto-functions-only` on the real benchmark:

```
ASSIGN timestamp = timestamp < this->ORIGIN ? this->ORIGIN : timestamp;
DECL   unsigned _ExtInt(256) t;
ASSIGN t = timestamp - this->ORIGIN;
IF !((t & 1) != 0) THEN GOTO 1
```

That dump is of the BASE copy. The derived copy — the one that runs — is where
the `ASSIGN` was missing.

## Why it killed a whole benchmark

`st1inch__St1inch.flat.sol`: `VotingPowerCalculator` (base) sets
`ORIGIN = block.timestamp`; the inherited `_votingPowerAt` clamps against it and
branches on 30 bits of `t`; and the **St1inch constructor calls it twice as a
self-check**:

```solidity
if (_votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD)     * _VOTING_POWER_DIVIDER <  1e18) revert ExpBaseTooBig();
if (_votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD + 1) * _VOTING_POWER_DIVIDER >  1e18) revert ExpBaseTooSmall();
```

With `t == 0` all thirty `if`s are dead, both calls return `1e18` unchanged, the
second check is `1e18 * 20 > 1e18` — unconditionally true — the constructor
reverts, and `assume(false)` removes every execution after it, so
`_ESBMC_Main_St1inch()` never ran:

```
Generated 0 VCC(s), 0 remaining after simplification            (1167 assignments)
--solidity-path-coverage: 0 of 243 instrumented path claim(s) reached the solver across 39 unit(s)
ERROR: INTERNAL DEFECT — NOT ONE of the 243 instrumented path claim(s) reached the solver.
```

The entry-liveness audit behaved correctly: it refused to report 243 paths as
"U", which would have been indistinguishable from an honest solver timeout.

**After the fix**, the same command reports `Generated 45 VCC(s), 45 remaining
after simplification (4321 assignments)` over 275 paths in 39 units — a solver
budget problem, which is a real measurement, instead of an empty run.

## It also inflated the branch-coverage BASELINE on this benchmark

`t == 0` made every `if (t & bit)` guard constant-false. A branch-coverage probe
`assert(t & bit != 0)` is then refuted, which MARKS THE FALL-THROUGH ARM
COVERED. Measured on one focused run: 32 covered edges in `_votingPowerAt` and
32 in `_balanceAt` — **64 of that run's 94** — all credited to a value the model
invented.

**Consequence for the comparison:** the locked baseline in
`notes/coverage/data/esbmc_*.json` was collected with the buggy frontend, so it
is not a valid opposite side any more. Both sides have to be re-collected on the
fixed build before any alignment claim is made. This is a re-baseline forced by
a tool fix, not a口径 drift, and it has to be stated wherever the st1inch figure
appears.

## Ruled out, each by measurement, so it is not re-derived

| hypothesis | refuted by |
|---|---|
| loop/unwind budget | `_votingPowerAt` contains no loop at all; and `--unwind 4/8/32` all die |
| string-library loop truncation | the only truncated loops are `nondet_string` / `_str_assign`; still dies at unwind 32 |
| path-coverage inlining corrupts the program | 21 vs 134 expanded calls -> identical 1167 symex assignments |
| the tx driver shape | `--solidity-max-tx 0` and `1` -> identical |
| slicing | `--no-slice` -> identical |
| `--contract` / `--focus-function` / `--function` | all four variants -> identical |
| the goto program was mangled | `__ESBMC_main`'s call list is byte-identical to a plain run; the constructor body diffs by 0 lines |
| the base ctor's immutables are not copied back | all 30 `this->_EXP_TABLE_k = _ESBMC_aux_VotingPowerCalculator._EXP_TABLE_k` are present |
| k-induction's inductive step (havoc'd state) is what reaches the methods | `--base-case` ALONE at `--unwind 8` reaches 94 edges including `_deposit` |
| unwinding assertions are what is violated | `--no-unwinding-assertions` -> identical |
| our path-coverage pass causes it | reproduces with NO coverage flag |

The constant `1167` symex assignments across every one of those variants was the
fingerprint: symex stopped at the same place every time, before any of them
could matter. What finally located it was reducing until the shape had nothing
left in it (reproducer 4) — not reasoning about the shape it started in.

## Still open

Why `--unwind 4` and `--unwind 8` differ for branch coverage on the real
benchmark (38 vs 94 covered edges) is NOT explained by the above, and no claim
is made about it here. It has to be re-measured on the fixed build regardless.
