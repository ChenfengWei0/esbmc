# A clamp against a base-constructor immutable evaluates to the constant 0

**Not a path-coverage defect.** It reproduces with no coverage flag at all
(`esbmc contract.sol --contract D3`), and it is the sole reason the st1inch
benchmark produces nothing at all.

Reproducers, smallest first, live in `notes/repro/`:

| file | shape | result |
|---|---|---|
| `immutable_clamp_1_ok.sol` | everything in one function, immutable set in the contract's OWN constructor | **correct** — stays symbolic |
| `immutable_clamp_2_param.sol` | immutable set in a BASE constructor; internal fn reassigns its `timestamp` PARAMETER | **wrong** — `t == 0` |
| `immutable_clamp_3_local.sol` | same as 2 but clamps into a fresh LOCAL | **wrong** — `ts == 0` |

2 and 3 fail identically, so reassigning the parameter is NOT the trigger. The
`1 -> 2` delta that matters is: the immutable is assigned in a **base**
constructor, and the clamp runs in an **internal function called from the
derived constructor**.

## What the model computes

Complete SSA of reproducer 3 (60 lines of Solidity, 149 steps). Every step
named below is verbatim:

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

The source of step 133 is

```solidity
uint256 ts = timestamp < ORIGIN ? ORIGIN : timestamp;
```

Its two arms are `block_timestamp` and `block_timestamp + 63072000`. **Neither
can be 0**, and both operands are symbolic at the point of use (steps 98 and
127 show them). So this is not a legal simplification of either arm — the value
is manufactured, not selected.

The goto program is CORRECT; the defect is downstream of it. From the
`--goto-functions-only` dump of the real benchmark:

```
ASSIGN timestamp = timestamp < this->ORIGIN ? this->ORIGIN : timestamp;
DECL   unsigned _ExtInt(256) t;
ASSIGN t = timestamp - this->ORIGIN;
IF !((t & 1) != 0) THEN GOTO 1
...
```

## Why it kills a whole benchmark

`st1inch__St1inch.flat.sol` has exactly this shape:
`VotingPowerCalculator` (base) sets `ORIGIN = block.timestamp`; `_votingPowerAt`
clamps against it and then branches on 30 bits of `t`; and the **St1inch
constructor calls it twice as a self-check**:

```solidity
if (_votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD)     * _VOTING_POWER_DIVIDER <  1e18) revert ExpBaseTooBig();
if (_votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD + 1) * _VOTING_POWER_DIVIDER >  1e18) revert ExpBaseTooSmall();
```

With `t == 0` all thirty `if`s are dead, both calls return `1e18` unchanged, the
second check is `1e18 * 20 > 1e18` — **unconditionally true** — and the
constructor reverts. `assume(false)` then removes every execution after it, so
`_ESBMC_Main_St1inch()` never runs:

```
Generated 0 VCC(s), 0 remaining after simplification
--solidity-path-coverage: 0 of 243 instrumented path claim(s) reached the solver across 39 unit(s)
ERROR: INTERNAL DEFECT — NOT ONE of the 243 instrumented path claim(s) reached the solver.
```

The entry-liveness audit is behaving correctly here: it is refusing to report
243 paths as "U", which would be indistinguishable from an honest solver
timeout.

## It also inflates the branch-coverage baseline on this benchmark

`t == 0` makes every `if (t & bit)` guard constant-false. A branch-coverage
probe `assert(t & bit != 0)` is then refuted, which MARKS THE FALL-THROUGH ARM
COVERED. Measured on one focused run: 32 covered edges in `_votingPowerAt` and
32 in `_balanceAt` — **64 of that run's 94** — all of them credited to a value
the model invented. Any comparison against the locked st1inch figure has to say
so.

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
| our path-coverage pass causes it | reproduces with NO coverage flag |

The constant `1167` symex assignments across every one of those variants is the
fingerprint: symex stops at the same place every time, before any of them can
matter.

## Still open

Why `--unwind 4` and `--unwind 8` differ for branch coverage on the real
benchmark (38 vs 94 covered edges) is NOT explained by the above, and no claim
is made about it here.
