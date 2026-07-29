# Focused-mode runnability of complete-path enumeration

## ⚠ THIS IS NOT A PER-UNIT PATH DISTRIBUTION, AND THE RENAME IS THE RESULT

The plan gated this table behind an enumeration-equality check
(`progress.md §4533`): *"`--focus-function` is whether it affects enumeration,
judged by each unit's path count and content-addressed key set being equal under
both configurations. The distribution table is not valid before this."*
If the gate does not pass, the product is renamed to **focused-mode
runnability** and kept **out of `evaluation_skeleton.md` table 5**. That is not a
worse measurement of the same quantity — it is a different quantity.

**The gate passes on the toy fixture and FAILS on real input.** Measured
2026-07-29, esbmc `bea5dfe87b`, same flat, same 22 `--coverage-exclude-contract`
flags (taken from the locked collector's own recorded command, verified
byte-identical across FarmingPool's entries), only `--focus-function` differing:

| focus target | instrumented total | expanded internal calls | NOT expanded (depth bound) | `exit`'s own paths |
|---|---|---|---|---|
| `claim` | 9536 | 168 | 38 | 8929 |
| **`exit`** | **1004** | **176** | **42** | **397** |
| `decimals` | 9536 | 168 | 38 | 8929 |
| `deposit` | 9536 | 168 | 38 | 8929 |

**The same unit has two different path counts depending on which unit you focus:
`FarmingPool.exit` is 397 paths under its own focus and 8929 under `claim`'s.**

### The mechanism, seen directly and then confirmed by intervention

Not inferred from "A would cause B and I see B" (section 4's diagnosis rule).
The instrumentation lines name it themselves: internal-call expansion is rooted
at the REACHABLE ENTRY SET, and `--focus-function` changes that set. Focusing on
a deeper entry (`exit` calls `withdraw` and `claim`) expands more call sites,
which pushes more of them past the call-depth bound — and the tool's own warning
says what happens to those: *"paths through them are MERGED rather than
enumerated"*.

Intervention, raising the bound from 4 to 6:

| focus | instrumented 4 → 6 | NOT expanded 4 → 6 |
|---|---|---|
| `claim` | 9536 → 10375 | 38 → 30 |
| `exit` | **1004 → 8215** | 42 → 34 |

The gap closes from 9.5x to 1.26x in the direction the depth-bound mechanism
predicts. That is the confirmation; without it this would still be a reading.

### What this invalidates, precisely

1. **This table is focused-mode runnability.** Each row's path count is that
   unit's count *in that unit's own focused enumeration*. Rows are not
   comparable to each other as a distribution over one enumeration, and this
   file does not feed table 5.
2. **The regression `solidity_path_cov_focus_function_same_enumeration` pins a
   true fact with a reason that is too wide.** Its header argues enumeration
   "should not" depend on focus *because enumeration is a static DFS over the
   goto program at instrumentation time*. That argument is wrong whenever the
   call-depth bound binds. The fixture cannot exhibit it: its call graph is
   shallower than the bound, so the property it pins is a property of that
   contract. Section 7 item 6 (the regression suite covers the shapes we thought
   of) and item 22 (a mechanically wrong explanation is worse than none).
3. **`scripts/solidity_path_generalise.py`'s docstring is too wide** where it
   says `focus` "does NOT change the enumeration -- that was verified by
   comparing the content-addressed path key sets of both configurations". The
   verification was on a shallow contract.
4. **Open, and NOT measured here**: the locked collector unions covered sets
   across one run per method. If per-method runs enumerate different path sets,
   that union is over paths drawn from different enumerations. Whether that is
   merely a bookkeeping question or touches the partition proposition has not
   been measured and must not be asserted either way.

---

## The measurement

Measured by `t2_runnability.py` on esbmc `bea5dfe87b`, 2026-07-29. memlimit 20g,
units in lexicographic order, 2 timed-out units end a project (breadth first).
A unit that did not finish stays in the denominator: "did not finish" is one of
the measured values, not a gap in the data.

**Rows marked `no` are not units and not failures either.** `--function` mode on
a library entry either hits `main symbol '<name>' is ambiguous` -> CONVERSION
ERROR (the name exists on several contracts in the flat), or runs fine and
instruments zero paths because the tool says so in as many words:
*"N in-scope function(s) are internal/private and are therefore not units; they
have no path set of their own and appear inside the paths of the units that call
them"*. Both are the unit definition working, not a measurement gap — but they
mean **the locked branch-coverage entry list and the path-coverage unit list
have different denominators**, and the reason is the definition of a unit.

`unit paths` is THIS unit's own complete paths, grouped out of `cov-report.json`
on `path_function`.
`ctr` is the contract-wide instrumented count and is CONTEXT ONLY -- it is
identical for every unit of a contract because `--focus-function` does not
change enumeration (T2.0 gate, measured), so it carries no distribution
information. The first version of this table reported `ctr` in the `unit paths`
column and produced six identical 2846s that looked like data.

`cap(s)` is the timeout this particular run was given: 540s whenever the slice
had room, the remaining slice time otherwise. A row that did not finish says so
against the cap it actually got.

**Two whole projects contribute no measurement, for two DIFFERENT reasons, and
neither of them is "we ran out of time".**

* **`limit_order_protocol`: zero units.** Every one of its 14 collector entries
  is a `MakerTraitsLib` function, and every run reports *"1 in-scope function(s)
  are internal/private and are therefore not units"*, instrumenting 0 paths in
  0.3 s. A pure library has no public entry, so under the unit definition
  (a public/external function) the project has nothing to enumerate. That is the
  definition working, and it is a structural fact about the corpus rather than a
  gap: the branch-coverage dataset counts these entries and the path-coverage
  one cannot.
* **`st1inch`: `TOOL-FAILURE` on all 22 units.** Every run finishes in ~15 s and
  ESBMC itself refuses the result:
  *"INTERNAL DEFECT — NOT ONE of the 243 instrumented path claim(s) reached the
  solver. The harness never entered any unit, so this run establishes nothing
  whatsoever; every path would otherwise be reported 'U', which reads exactly
  like an honest solver timeout. This is a tool failure, not a result."*
  ⚠ **The first version of this collector recorded those 22 rows as
  `completed=yes`**, because its completion test was "the instrumented line is
  present and we did not time out" — and that line is present. Twenty-two rows
  of dashes were filed as successful measurements of units with no paths. The
  tool had already declined to make that mistake and the collector made it
  anyway; the rows now read `TOOL-FAILURE` and the script checks the guard.
  ⚠ NOT diagnosed here: WHY the harness never enters. The locked dataset's own
  notes record a different st1inch problem (the constructor's `_votingPowerAt`
  invariant narrowing the state space until downstream bodies are proven
  unreachable), which is a candidate and not a conclusion.

Also visible on st1inch, and worth reading even though the run establishes
nothing: the **degradation** mechanism fires there and reports itself precisely —
12 units had 12 call points withdrawn to fit the per-unit budget of 10000, with
each withdrawn call point named by source line, and the message distinguishes
itself from truncation ("this one costs assertion strength at named places and
keeps the enumeration complete, the goal cap instead drops paths").

DEVIATION FROM THE PLAN'S 600s PER-UNIT CAP, STATED RATHER THAN ABSORBED: the
agent's foreground command window is 590s and esbmc must never be detached, so a
unit given 600s can only ever be CUT OFF by the window -- producing no row at
all -- instead of being recorded as a timeout. 540s leaves room to record the
result. The only units this misreports are those that would have finished
between 540s and 600s; they appear here as not finishing.

| bench | contract | function | unit paths | F | I | U | wall(s) | cap(s) | completed | ctr |
|---|---|---|---|---|---|---|---|---|---|---|
| `aqua_Aqua` | `Aqua` | `dock` | 63 | 2 | 0 | 61 | 9.4 | 540 | yes | 2846 |
| `aqua_Aqua` | `Aqua` | `pull` | 17 | 5 | 0 | 12 | 9.1 | 540 | yes | 2846 |
| `aqua_Aqua` | `Aqua` | `push` | 19 | 2 | 0 | 17 | 2.9 | 540 | yes | 2846 |
| `aqua_Aqua` | `Aqua` | `rawBalances` | 3 | 2 | 0 | 1 | 2.1 | 540 | yes | 2846 |
| `aqua_Aqua` | `Aqua` | `safeBalances` | 11 | 2 | 0 | 9 | 2.6 | 540 | yes | 2846 |
| `aqua_Aqua` | `Aqua` | `ship` | 2733 | 2 | 0 | 2731 | 254.3 | 540 | yes | 2846 |
| `aqua_Aqua` | `BalanceLib` | `load` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `aqua_Aqua` | `BalanceLib` | `store` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `BaseEscrow` | `rescueFunds` | 5 | 5 | 0 | 0 | 10.4 | 540 | yes | 13 |
| `cross_chain_swap_EscrowDst` | `EscrowDst` | `cancel` | 12 | 12 | 0 | 0 | 121.9 | 540 | yes | 30 |
| `cross_chain_swap_EscrowDst` | `EscrowDst` | `publicWithdraw` | - | - | - | - | 547.1 | 540 | TIMEOUT | 30 |
| `cross_chain_swap_EscrowDst` | `EscrowDst` | `withdraw` | - | - | - | - | 550.1 | 540 | TIMEOUT | 30 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `hash` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `hashMem` | 0 | 0 | 0 | 0 | 1.3 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `integratorFeeAmount` | 0 | 0 | 0 | 0 | 1.3 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `integratorFeeAmountCd` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `integratorFeeRecipient` | 0 | 0 | 0 | 0 | 1.8 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `integratorFeeRecipientCd` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `protocolFeeAmount` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `cross_chain_swap_EscrowDst` | `ImmutablesLib` | `protocolFeeAmountCd` | 0 | 0 | 0 | 0 | 1.2 | 540 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `BaseEscrow` | `rescueFunds` | 5 | 5 | 0 | 0 | 9.4 | 539 | yes | 13 |
| `cross_chain_swap_EscrowSrc` | `EscrowSrc` | `cancel` | 4 | 4 | 0 | 0 | 5.8 | 530 | yes | 31 |
| `cross_chain_swap_EscrowSrc` | `EscrowSrc` | `publicCancel` | 4 | 4 | 0 | 0 | 20.7 | 524 | yes | 31 |
| `cross_chain_swap_EscrowSrc` | `EscrowSrc` | `publicWithdraw` | 5 | 5 | 0 | 0 | 39.0 | 504 | yes | 31 |
| `cross_chain_swap_EscrowSrc` | `EscrowSrc` | `withdraw` | 5 | 5 | 0 | 0 | 11.0 | 465 | yes | 31 |
| `cross_chain_swap_EscrowSrc` | `EscrowSrc` | `withdrawTo` | 5 | 5 | 0 | 0 | 11.6 | 454 | yes | 31 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `hash` | 0 | 0 | 0 | 0 | 1.2 | 442 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `hashMem` | 0 | 0 | 0 | 0 | 1.2 | 441 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `integratorFeeAmount` | 0 | 0 | 0 | 0 | 1.2 | 440 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `integratorFeeAmountCd` | 0 | 0 | 0 | 0 | 1.2 | 438 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `integratorFeeRecipient` | 0 | 0 | 0 | 0 | 1.2 | 437 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `integratorFeeRecipientCd` | 0 | 0 | 0 | 0 | 1.2 | 436 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `protocolFeeAmount` | 0 | 0 | 0 | 0 | 1.2 | 435 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `protocolFeeAmountCd` | 0 | 0 | 0 | 0 | 1.2 | 434 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `protocolFeeRecipient` | 0 | 0 | 0 | 0 | 1.2 | 432 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ImmutablesLib` | `protocolFeeRecipientCd` | 0 | 0 | 0 | 0 | 1.2 | 431 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `ProxyHashLib` | `computeProxyBytecodeHash` | 0 | 0 | 0 | 0 | 1.2 | 430 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `TimelocksLib` | `get` | - | - | - | - | 1.0 | 429 | no | - |
| `cross_chain_swap_EscrowSrc` | `TimelocksLib` | `rescueStart` | 0 | 0 | 0 | 0 | 1.2 | 428 | yes | 0 |
| `cross_chain_swap_EscrowSrc` | `TimelocksLib` | `setDeployedAt` | 0 | 0 | 0 | 0 | 1.2 | 427 | yes | 0 |
| `farming` | `Distributor` | `distributor` | 2 | 2 | 0 | 0 | 7.4 | 425 | yes | 17 |
| `farming` | `Distributor` | `setDistributor` | 5 | 5 | 0 | 0 | 9.0 | 418 | yes | 17 |
| `farming` | `FarmAccounting` | `claim` | - | - | - | - | 5.0 | 409 | no | - |
| `farming` | `FarmAccounting` | `farmedSinceCheckpointScaled` | 0 | 0 | 0 | 0 | 5.2 | 404 | yes | 0 |
| `farming` | `FarmAccounting` | `startFarming` | - | - | - | - | 5.0 | 399 | no | - |
| `farming` | `FarmAccounting` | `stopFarming` | - | - | - | - | 5.0 | 394 | no | - |
| `farming` | `FarmingLib` | `claim` | - | - | - | - | 5.6 | 389 | no | - |
| `farming` | `FarmingLib` | `farmed` | - | - | - | - | 5.0 | 383 | no | - |
| `farming` | `FarmingLib` | `getData` | 0 | 0 | 0 | 0 | 5.2 | 378 | yes | 0 |
| `farming` | `FarmingLib` | `makeInfo` | 0 | 0 | 0 | 0 | 5.2 | 373 | yes | 0 |
| `farming` | `FarmingLib` | `startFarming` | - | - | - | - | 5.1 | 368 | no | - |
| `farming` | `FarmingLib` | `stopFarming` | - | - | - | - | 4.9 | 363 | no | - |
| `farming` | `FarmingLib` | `updateBalances` | - | - | - | - | 5.6 | 358 | no | - |
| `farming` | `FarmingPool` | `claim` | 19 | 10 | 0 | 9 | 15.4 | 352 | yes | 9536 |
| `farming` | `FarmingPool` | `decimals` | 2 | 2 | 0 | 0 | 8.8 | 337 | yes | 9536 |
| `farming` | `FarmingPool` | `deposit` | 154 | 7 | 0 | 147 | 57.2 | 328 | yes | 9536 |
| `farming` | `FarmingPool` | `exit` | 397 | 37 | 0 | 360 | 170.3 | 271 | yes | 1004 |
| `farming` | `FarmingPool` | `farmInfo` | 2 | 2 | 0 | 0 | 7.3 | 100 | yes | 9536 |
| `farming` | `FarmingPool` | `farmed` | 4 | 4 | 0 | 0 | 9.5 | 93 | yes | 9536 |
| `farming` | `FarmingPool` | `rescueFunds` | - | - | - | - | 85.3 | 84 | TIMEOUT | 9536 |
| `farming` | `FarmingPool` | `startFarming` | 50 | 26 | 0 | 24 | 31.0 | 539 | yes | 9536 |
| `farming` | `FarmingPool` | `stopFarming` | 20 | 5 | 0 | 15 | 13.5 | 508 | yes | 9536 |
| `farming` | `FarmingPool` | `withdraw` | 103 | 7 | 0 | 96 | 38.3 | 495 | yes | 9536 |
| `farming` | `UserAccounting` | `eraseFarmed` | 0 | 0 | 0 | 0 | 6.4 | 457 | yes | 0 |
| `farming` | `UserAccounting` | `farmed` | - | - | - | - | 5.0 | 450 | no | - |
| `farming` | `UserAccounting` | `farmedPerToken` | 0 | 0 | 0 | 0 | 5.3 | 445 | yes | 0 |
| `farming` | `UserAccounting` | `updateBalances` | - | - | - | - | 4.9 | 440 | no | - |
| `farming` | `UserAccounting` | `updateFarmedPerToken` | 0 | 0 | 0 | 0 | 5.2 | 435 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `allowMultipleFills` | 0 | 0 | 0 | 0 | 0.3 | 430 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `allowPartialFills` | 0 | 0 | 0 | 0 | 0.3 | 430 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `getExpirationTime` | 0 | 0 | 0 | 0 | 0.3 | 429 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `hasExtension` | 0 | 0 | 0 | 0 | 0.3 | 429 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `isAllowedSender` | 0 | 0 | 0 | 0 | 0.3 | 429 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `isExpired` | 0 | 0 | 0 | 0 | 0.3 | 428 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `needCheckEpochManager` | 0 | 0 | 0 | 0 | 0.3 | 428 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `needPostInteractionCall` | 0 | 0 | 0 | 0 | 0.3 | 428 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `needPreInteractionCall` | 0 | 0 | 0 | 0 | 0.3 | 427 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `nonceOrEpoch` | 0 | 0 | 0 | 0 | 0.3 | 427 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `series` | 0 | 0 | 0 | 0 | 0.3 | 427 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `unwrapWeth` | 0 | 0 | 0 | 0 | 0.3 | 427 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `useBitInvalidator` | 0 | 0 | 0 | 0 | 0.3 | 426 | yes | 0 |
| `limit_order_protocol` | `MakerTraitsLib` | `usePermit2` | 0 | 0 | 0 | 0 | 0.3 | 426 | yes | 0 |
| `st1inch_St1inch` | `St1inch` | `approve` | - | - | - | - | 15.4 | 426 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `deposit` | - | - | - | - | 14.4 | 410 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `depositFor` | - | - | - | - | 15.5 | 396 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `depositForWithPermit` | - | - | - | - | 14.4 | 380 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `depositWithPermit` | - | - | - | - | 15.4 | 366 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `earlyWithdraw` | - | - | - | - | 14.7 | 351 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `earlyWithdrawLoss` | - | - | - | - | 14.6 | 336 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `earlyWithdrawTo` | - | - | - | - | 15.3 | 321 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `rescueFunds` | - | - | - | - | 14.8 | 306 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `setDefaultFarm` | - | - | - | - | 15.4 | 291 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `setEmergencyExit` | - | - | - | - | 14.4 | 276 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `setFeeReceiver` | - | - | - | - | 15.6 | 261 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `setMaxLossRatio` | - | - | - | - | 14.6 | 246 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `setMinLockPeriodRatio` | - | - | - | - | 15.4 | 231 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `transfer` | - | - | - | - | 14.8 | 216 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `transferFrom` | - | - | - | - | 14.2 | 201 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `votingPower` | - | - | - | - | 15.4 | 187 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `votingPowerAt` | - | - | - | - | 14.4 | 171 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `votingPowerOf` | - | - | - | - | 15.3 | 157 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `votingPowerOfAt` | - | - | - | - | 14.3 | 141 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `withdraw` | - | - | - | - | 15.6 | 127 | TOOL-FAILURE | 243 |
| `st1inch_St1inch` | `St1inch` | `withdrawTo` | - | - | - | - | 14.5 | 112 | TOOL-FAILURE | 243 |
