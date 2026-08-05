# VeriPUT POC Ground Truth

Date: 2026-08-06. This file is the handoff point for POC-level expected
semantics before spending ESBMC retry budget. Fuzz may refute these candidates
or generated tests, but it may not be counted as proof.

## Retry Discipline

- Attempt 1: 60s ESBMC timeout, 8GiB memlimit.
- Attempt 2: 120s ESBMC timeout, 8GiB memlimit.
- Attempt 3: 600s ESBMC timeout, 10GiB memlimit.
- Do not rerun a POC just to inspect behavior. First read source, pathcov
  claims, existing `certify_gate.jsonl`, and generated tests.

## Common Expectations

- Non-payable ABI gate: for every non-payable unit, `msg.value != 0` is a
  structural revert path. The generated PUT should use low-level `.call{value:
  v}` and assert `ok == false`; ESBMC region certification is not needed.
- Constants and immutables such as `state._DOCKED` and `state._MAX_BALANCE`
  are semantic pins, not query-settable coordinates.
- A generated PUT must assert an observable oracle: revert, return value, event,
  or state delta. A fuzzed `try/catch` with no assertion is only a replay
  witness and should not count as a strong PUT.
- External-call success/failure is not a normal fuzz coordinate unless the test
  fixture installs a deterministic mock/stub. Without that fixture, sibling
  paths that differ only in external-call success are statically inseparable.

## Aqua

Source: `notes/coverage/poc_units/aqua_Aqua__Aqua__safeBalances/inputs/aqua__Aqua.flat.sol`

Relevant code:

- `Balance` has `uint248 amount` and `uint8 tokensCount`.
- `_balances[maker][app][strategyHash][token]` is a four-level mapping to
  `Balance`.
- `_DOCKED == 0xff`.
- `rawBalances(...)` returns `_balances[maker][app][strategyHash][token].load()`.
- `safeBalances(...)` loads token0 then token1 and requires
  `tokensCount > 0 && tokensCount != _DOCKED` for each loaded slot.
- `push(...)` loads one slot, requires the same active-token predicate, stores
  `prevBalance + amount`, then calls `IERC20(token).safeTransferFrom(...)`.

### `Aqua.push`

Stage-1 pathcov: 6 instrumented paths, 2 witnessed paths.

- Path `2`: ABI non-payable reject.
  - Witness: `msg.value = uint256.max`.
  - Expected region: `msg.value != 0`.
  - Expected PUT oracle: low-level call with value must fail.
- Path `6`: body reaches the active-token guard and reverts on inactive slot.
  - Witness: `msg.value = 0`, `maker = app = token = 0`,
    `strategyHash = bytes32(0)`, default balance slot.
  - Expected source slot:
    `state._balances[maker][app][strategyHash][token].tokensCount`.
  - Expected region: `msg.value == 0` and selected slot is inactive, minimally
    `tokensCount == 0`; a second semantic region may cover
    `tokensCount == _DOCKED`.
  - Expected PUT oracle: `expectRevert` or low-level `ok == false`. This path
    should not be emitted as assertion-free `try/catch`.
  - Static diagnosis: the source slot key `strategyHash` is a `bytes32`
    aggregate, so it must not be emitted as `[strategyHash]` in an ESBMC slot
    coordinate. The driver now fixes it to the witnessed mapping-key literal
    `0x2000000000000000000000000000000000000000000000000000000000000000`
    when the CE slice is `bytes32(0)`.
  - Current static check, without consuming a POC ESBMC retry: replaying the
    existing Stage-1 report and real solc AST through the current Python
    coordinate logic proposes exactly
    `state._balances[maker][app][0x2000...0000][token].amount` and
    `.tokensCount`, with no `[strategyHash]` aggregate coordinate and no
    guessed cross-product slots such as `[maker][maker]`.
  - Current static type check: the proposed leaves carry the source ranges
    `.amount in [0, 2^248-1]` and `.tokensCount in [0, 255]`.
  - The old `certify_gate.jsonl` and `/tmp/.../outer.json` for `push` are stale:
    they still contain `[strategyHash]`, guessed cross-product slots, and
    `uint256` leaf ranges. Treat their SIGABRT as evidence about that old query
    shape, not about the current region strategy. No official retry remains for
    `push` unless the budget policy is explicitly reopened.

### `Aqua.safeBalances`

Stage-1 pathcov: 4 instrumented paths, 2 witnessed paths.

- Path `2`: ABI non-payable reject.
  - Witness: `msg.value = uint256.max`.
  - Expected region: `msg.value != 0`.
  - Expected PUT oracle: low-level call with value must fail.
- Path `6`: first token's active-token guard reverts on inactive slot.
  - Witness: `msg.value = 0`, `maker = app = token0 = token1 = 0`,
    `strategyHash = bytes32(0)`, default balance slots.
  - Expected source slot:
    `state._balances[maker][app][strategyHash][token0].tokensCount`.
  - Useful secondary slots:
    `state._balances[maker][app][strategyHash][token0].amount`,
    `state._balances[maker][app][strategyHash][token1].tokensCount`, and
    `state._balances[maker][app][strategyHash][token1].amount`.
  - Expected region: `msg.value == 0` and token0 selected slot inactive,
    minimally `tokensCount == 0`; `token1` is irrelevant when token0 guard
    fails.
  - Expected PUT oracle: `expectRevert` or low-level `ok == false`.
  - Static diagnosis: same `bytes32` mapping-key issue as `push`. Offline
    checks against the real Stage-1 report now propose literal-key slots for
    both `token0` and `token1`; the next ESBMC run should verify whether this
    removes the outer-refine abort.
  - Current branch check: Stage 2 attempt 1 under 60s/8GiB completed with
    `2 witnessed / 2 certified / 0 not certified`. The certified body-path
    region contains the expected input slice (`msg.value == 0`, wide
    `maker/app/token0/token1`) and the four literal-key balance leaf slots
    fixed at zero. Stage 3 attempt 1 then emitted the value-gate PUT, but the
    body-path PUT was only a `try/catch` replay. The expected strong PUT for
    body path `enc=6` is therefore still an exit-kind oracle:
    catch the call and assert `ok == false`.
  - Current branch PUT check: Stage 3 attempt 2 under 120s/8GiB produced
    `B = 2 of 2`. Path `enc=6` now fuzzes `maker/app/token0/token1`,
    establishes the four zero literal-key slots with `vm.store` plus readback
    checks, calls `safeBalances(...)` inside `try/catch`, and asserts the call
    reverted.
  - Focused assertion-spec replay after the internal resolver fix no longer
    hard-refuses the literal bytes32 key. It emits R1 rows for all four
    literal-key `_balances` leaf slots: `post == pre` holds on the rollback
    path, while change and strict-order candidates are refuted. This confirms
    the slot-coordinate blocker is fixed; the generated gate-cell PUT should
    still rely on the revert exit-kind oracle because post-state is not
    chain-observable after rollback.

### `Aqua.rawBalances`

Stage-1 pathcov: 2 witnessed paths in the current result.

- Path `2`: ABI non-payable reject.
  - Witness: `msg.value = uint256.max`, all call arguments zero.
  - Expected region: `msg.value != 0`. Under the current strong recipe this
    should certify with `msg.value in [1, uint256.max]`, rather than being
    excluded by the old auto `msg.value == 0` pin.
  - Expected PUT oracle: low-level call with value must fail.
- Path `3`: normal getter.
  - Witness: `msg.value = 0`, `maker = app = token = 0`,
    `strategyHash = bytes32(0)`, default selected slot.
  - Expected region: `msg.value == 0`, wide `maker/app/token`, and, once slot
    coordinates are enabled for this unit, the selected literal-key
    `_balances[maker][app][0x2000...0000][token].{amount,tokensCount}` values.
  - Expected PUT oracle: destructure the two return members and assert
    `return.0 == 0` and `return.1 == 0` over the certified region. Do not assert
    the Stage-1 CE string `(0)` directly; tuple returns must come from
    `--path-cov-assert` member rows.
- Current attempt status:
  - Stage 2 attempt 1 under 60s/8GiB was spent fresh and certified both
    witnessed paths. Path `2` has `msg.value in [1, uint256.max]`; path `3`
    has `msg.value == 0`, wide `maker/app/token`, and the two selected
    literal-key `_balances` leaf slots.
  - Stage 3 attempt 1 under 60s/8GiB was spent and produced only `B = 1 of 2`:
    the ABI nonpayable path emitted a strong value-gate PUT, but the normal
    getter emitted a fuzzed replay with zero oracle assertions.
  - The normal getter did not reach a semantic solver problem. Its assertion
    ladder was refused before solving because Stage 2 gave
    `.tokensCount` the default `uint256` upper bound instead of the declared
    `uint8` upper bound `255`.
  - Script fix: mapping struct leaf types are now carried from the AST into
    static slot type ranges, so `_balances...amount` uses `uint248` and
    `_balances...tokensCount` uses `uint8`. The PUT driver also now parses
    `REFUSING THE LADDER on region coordinate ...` as a real refusal,
    preventing another oracle-free emission if ESBMC rejects a spec.
  - Stage 2 attempt 2 under 120s/8GiB validated the fix: `.tokensCount` is now
    `[0, 255]` and `.amount` is `[0, 2^248-1]` in the certified region.
  - Stage 3 attempt 2 under 120s/8GiB produced `B = 2 of 2`. Path `3` fuzzes
    `maker/app/token` and asserts the tuple returns `return.0 == 0` and
    `return.1 == 0` along with state non-change rungs; path `2` fuzzes
    `msg.value/maker/app/token` and asserts the non-payable value-call revert.
- Next run discipline: rawBalances should not be run again unless a later
  regression needs confirmation. Its 60s and 120s opportunities have already
  been spent; only the 600s/10GiB tier remains by the current budget ladder.

### `Aqua.dock`

Current result certifies the loop body slice with `msg.value == 0` and an
`app` interval, while excluding the ABI value path because `msg.value` was
pinned to zero.

Ground truth:

- `dock(app, strategyHash, tokens)` iterates over `tokens`.
- For each token it requires
  `_balances[msg.sender][app][strategyHash][tokens[i]].tokensCount ==
  tokens.length`, then stores `_DOCKED`.
- A strong gate PUT needs a concrete array length and slot setup. Without a
  fixture that writes the selected `_balances` slots, the default state should
  revert for any non-empty `tokens`; the empty array path should not execute
  the guard.

## Farming

Source: `notes/coverage/poc_units/farming__FarmingPool__deposit/inputs/farming__FarmingPool.flat.sol`

Semantic constructor pins observed in reports:

- `_owner == 1`
- `_distributor == 0`
- `_totalSupply == 0`
- `_farm.farmInfo.finished == 0`
- `_MAX_BALANCE == 100000000000000000000000000000000`

### `Distributor.distributor`

Stage-1 pathcov: 2 witnessed paths.

- Path `2`: ABI non-payable reject.
  - Expected region: `msg.value != 0`.
  - Expected PUT oracle: low-level call with value must fail.
- Path `3`: normal getter.
  - Expected region: `msg.value == 0` and constructor pins above.
  - Expected PUT oracle: return value equals `_distributor`, currently `0`.

Current attempt status:

- Stage 3 attempt 3 under 600s/10GiB produced `B = 1 of 2`.
- Path `2` is now a strong value-gate PUT: it fuzzes `msg.value` over
  `[1, uint256.max]` and asserts the low-level non-payable call fails.
- Path `3` remains refused as not parameterized. It has the expected return
  oracle (`return == 0`), but no coordinate rendered by the Foundry test has
  width greater than one: `msg.value` is pinned to zero and the state slice is
  constructor-pinned.
- No official retry remains for this POC under the current three-attempt
  budget.

### `Distributor.setDistributor`

Source behavior:

- `onlyOwner`: revert unless `msg.sender == owner()`.
- Body: revert if `distributor_ == address(0)`, otherwise set `_distributor`.

Stage-1 pathcov: 5 witnessed paths.

- Path `2`: ABI non-payable reject.
  - Expected region: `msg.value != 0`.
  - Expected PUT oracle: low-level call with value must fail.
- Path `12`: non-owner plus zero distributor.
  - Expected region: `msg.value == 0`, `msg.sender != 1`,
    `distributor_ == 0`.
  - Expected oracle: revert. The revert reason may be owner failure first.
- Path `13`: non-owner plus nonzero distributor.
  - Expected region: `msg.value == 0`, `msg.sender != 1`,
    `distributor_ != 0`.
  - Expected oracle: revert due owner failure.
- Path `14`: owner plus zero distributor.
  - Expected region: `msg.value == 0`, `msg.sender == 1`,
    `distributor_ == 0`.
  - Expected oracle: revert due zero distributor.
- Path `15`: owner plus nonzero distributor.
  - Expected region: `msg.value == 0`, `msg.sender == 1`,
    `distributor_ != 0`.
  - Expected oracle: normal exit and post-state `_distributor == distributor_`.

This POC is already the cleanest example of a strong parameterized unit test:
`distributor_`, `msg.sender`, and `msg.value` form the whole input region, and
all five regions are certified.

### `FarmingPool.deposit`

Source behavior:

- `deposit(amount)` is public and non-payable.
- It mints `amount` to `msg.sender`.
- It reverts if `balanceOf(msg.sender) > _MAX_BALANCE`.
- It then calls `STAKING_TOKEN.safeTransferFrom(msg.sender, address(this),
  amount)`.

Stage-1 pathcov: 154 instrumented paths, 7 witnessed paths.

- Path `2`: ABI non-payable reject.
  - Expected region: `msg.value != 0`.
  - Expected PUT oracle: low-level call with value must fail.
- Paths `26/27`, `246/247`, `3622/3623`: sibling pairs with the same
  test-settable payload, differing only in external-call success/failure at
  `safeTransferFrom`.
  - Current classification as statically inseparable is correct for a plain
    generated PUT.
  - A stronger future PUT would need a deterministic ERC20 fixture or mock so
    `STAKING_TOKEN.safeTransferFrom` can be forced to succeed or fail. Only
    then can these paths become parameterized tests with real oracles.
  - Fuzz can cheaply refute a proposed fixture or oracle, but it cannot prove
    either external-call branch.

Current result is therefore not a region-search failure. It is a harness
expressiveness boundary: the region is over controllable inputs, while the path
split is over callee behavior.

Current attempt status:

- Stage 3 attempt 1 under 60s/8GiB timed out in the emit substep before a
  `.cov.t.sol` was produced. No PUT/oracle result was measured by that attempt.
- Stage 3 attempt 2 under 120s/8GiB produced `B = 1 of 1 emitted PUT` for
  path `2`.
- The path `2` PUT fuzzes `msg.sender`, `msg.value`, and `amount`; it asserts
  the low-level non-payable value call fails.
- The six external-call-split paths remain excluded by method attribution, not
  by a failed region search.
- Only the 600s/10GiB tier remains for this POC, and there is no need to spend
  it unless the fixture/model strategy changes.

## Next Static Checks Before Running Another POC

- Keep the Aqua ground truth above in front of the next run. The expected body
  region is an inactive selected Balance slot, not a free `strategyHash`.
- Literal bytes32 mapping keys are now queryable by both region assumptions and
  assertion observables. If Aqua still aborts, inspect the saved
  `failed-rounds/*.outer.json` and log first; the next likely causes are
  `BalanceLib.load` assembly approximation or a different verifier-side
  instrumentation issue, not the `0x2000...0000` key spelling.
- Do not spend a POC retry until the generated outer-box/assert spec visibly
  contains the literal bytes32 mapping key and no
  `state._balances[...][strategyHash]` coordinate.
