# D30 — st1inch's zero has a source-level mechanism: a 30-deep chain of 256-bit `mul`/`div` in the CONSTRUCTOR, on every query

**Written 2026-08-01, BEFORE the solver sweep's result was read.** The prediction
at the bottom is therefore a prediction. Source read out of
`notes/coverage/extracts/st1inch_VotingPowerCalculator.sol` (flat 4170-4451).

## What the source actually is

`VotingPowerCalculator` is fixed-point **exponentiation by squaring**, unrolled by
hand:

```solidity
uint256 private immutable _EXP_TABLE_0;   // ... through _EXP_TABLE_29

constructor(uint256 expBase_, uint256 origin_) {
    _EXP_TABLE_0  = expBase_;
    _EXP_TABLE_1  = (_EXP_TABLE_0  * _EXP_TABLE_0)  / _ONE_E18;
    _EXP_TABLE_2  = (_EXP_TABLE_1  * _EXP_TABLE_1)  / _ONE_E18;
    ...
    _EXP_TABLE_29 = (_EXP_TABLE_28 * _EXP_TABLE_28) / _ONE_E18;
}

function _votingPowerAt(uint256 balance, uint256 timestamp) internal view … {
    uint256 t = timestamp - ORIGIN;
    votingPower = balance;
    if (t & 0x01       != 0) votingPower = (votingPower * _EXP_TABLE_0)  / _ONE_E18;
    if (t & 0x02       != 0) votingPower = (votingPower * _EXP_TABLE_1)  / _ONE_E18;
    ...
    if (t & 0x20000000 != 0) votingPower = (votingPower * _EXP_TABLE_29) / _ONE_E18;
}
```

`_balanceAt` is the same shape again, dividing BY `_EXP_TABLE_k` instead of by the
constant — division by a symbolic 256-bit value.

## Three consequences, and each explains a number already on record

### 1. `_votingPowerAt` alone has ~2^30 complete paths

Thirty **independent, non-nested** `if`s. That is 1 073 741 824 complete paths in
one callee, against a per-unit budget of 10 000.

⇒ This is exactly why D29 found `_votingPowerAt#7638` withdrawn from **seven of
the twelve** degraded units, with the tool's own words: "fully expanded it
enumerates more paths than the per-unit budget (10000)". The withdrawal policy is
not misfiring here — it is doing precisely the job the design gives it, on the
purest instance of the path-explosion it exists for.

### 2. The solver difficulty is nonlinear 256-bit arithmetic, chained 30 deep

Each `_EXP_TABLE_k` is a nonlinear function of `expBase_`: thirty chained 256-bit
multiplications and thirty 256-bit divisions. Bit-blasted, one 256x256 multiply
is on the order of 256² partial-product gates plus an adder tree, and a 256-bit
division is worse. Thirty of each, composed, with the output of one feeding the
next.

⇒ **This is what `out of memory` means.** D14 measured z3's own
`reason_unknown()` as `out of memory`, IDENTICAL at 4 g and 16 g with per-solve
time scaling ~4x — the extra 12 GiB was consumed. That is not a weak solver; it
is a formula whose bit-blasted size is very large.

### 3. AND IT IS IN THE CONSTRUCTOR, so it is on EVERY query

This is the part that explains the observation that previously had no
explanation: `setFeeReceiver` is an owner check and one assignment, and D14
measured it at symex 0.095 s, **1526 assignments**, 10 VCCs, of which **8 return
no verdict**.

Its own body cannot be the cause. The harness runs the CONSTRUCTOR before any
unit, and the constructor is the 30-deep chain. So every path claim of every
unit — `setFeeReceiver` included — carries the whole table's definition in its
formula.

⇒ **st1inch's `F = 0` is not about any unit's own body.** It is a
constructor-borne formula that every query inherits.

## PREDICTION, written before reading the solver sweep

`solver_arms.py` is running `{auto, z3, z3+node-flattener, cvc5, bitwuzla}` on
`st1inch setFeeReceiver`, each gated on reproducing `F = 2` on
`aqua safeBalances`.

**If this note is right, the answer is READING 2: every backend that passes the
control returns `F = 0` on st1inch.** Bit-blasting a 30-deep chain of 256-bit
`mul`/`div` is expensive for every bit-vector solver; none of them has a
short-cut, and the encoder was already exonerated on aqua (`encoder_arms.py`:
arm B reproduced `F = 2` with FASTER solve times than the default backend).

**FALSIFIER, and it is not a small one**: if any backend returns `F > 0` on
st1inch, this explanation is wrong or at least incomplete — the difficulty would
then be something a different decision procedure can route around, and the right
next step would be the router (`INVOCATION_DECISIONS` row 7), not the contract.

## What would follow if the prediction holds

Not "raise the memory limit" — D14 already measured that it does not help. The
candidates worth naming, none of them attempted here:

* **pin the constructor arguments.** `expBase_` and `origin_` are deployment
  constants on chain. With them concrete, the thirty table entries fold to
  constants at simplification and the whole nonlinear chain disappears from every
  query. This is a HARNESS question (what does the deployed contract look like),
  not a solver question, and it is the same shape as S10's `msg.value` pin: a
  fact about the deployment, not a slice.
* **report st1inch as a stated limit**, the way the Escrows' `ImmutablesLib` is
  now reported — a contract whose constructor alone exceeds what bit-blasting can
  decide is a real and nameable applicability boundary.

⚠ The first is a candidate, not a plan: whether the frontend folds an immutable
initialised from a concrete constructor argument has NOT been checked, and
`state_mutability`/`unsettable_coords` in the stage-2 driver already treat
`immutable` specially for a different reason. Nothing here licenses assuming it.
