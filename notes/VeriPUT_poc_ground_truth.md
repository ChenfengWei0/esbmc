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
  - Remaining blocker to test once, not guess by rerunning: whether the literal
    slot lets outer refine finish, or whether the next failure is in
    `BalanceLib.load` assembly modeling / path-cov outer-box internals.

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

### `Aqua.rawBalances`

Stage-1 pathcov: 2 witnessed paths in the current result.

- ABI non-payable reject is structural and should assert call failure when
  `msg.value != 0`.
- Normal path with `msg.value == 0` returns the selected slot's amount and
  `tokensCount`. With default state and zero keys, expected return is
  `(0, 0)`.
- Strong PUT expectation: fuzz `maker`, `app`, `token`; keep
  `strategyHash = bytes32(0)` until bytes32 slot-region support is classified;
  assert default return `(0, 0)` when no state setup is applied.

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

This POC is already semantically complete in the current certification result.

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

## Next Static Checks Before Running Another POC

- Keep the Aqua ground truth above in front of the next run. The expected body
  region is an inactive selected Balance slot, not a free `strategyHash`.
- If Aqua still aborts after literal-key slot generation, inspect the saved
  `failed-rounds/*.outer.json` and log first. The next likely causes are
  `BalanceLib.load` assembly approximation or verifier-side outer-box
  instrumentation on mapping-member slots.
- Do not spend a POC retry until the generated outer-box spec visibly contains
  the literal bytes32 mapping key and no `state._balances[...][strategyHash]`
  coordinate.
