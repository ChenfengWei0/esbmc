// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: a slot the unit writes that is keyed by the CALLER, in a unit
/// that has NO address parameter at all.
///
/// WHY THIS EXACT SHAPE. `propose_slot_vars` used to draw mapping keys only
/// from the unit's PARAMETER list. `deposit(uint256)` has no address
/// parameter, so for this contract the proposer produced NOT ONE candidate
/// slot name -- the only slot the unit writes was never mentioned to the
/// ladder, the ladder therefore returned no state rung, and the emitted PUT
/// carried an empty oracle. That is the corpus shape too:
/// `FarmingPool.deposit(uint256 amount)` writes `_balances[msg.sender]` and
/// declares no address parameter either.
///
/// Ten lines, one mapping, one level, one writer, no guards beyond `amt > 0`,
/// no external calls, no library: if the chain
///     propose `bal[msg.sender]` -> ladder decides a rung -> emitter renders
///     the read at the PRANKED address -> forge green
/// does not close here, nothing about the failure can be blamed on contract
/// complexity, on nesting, or on the solver's mapping backend.
///
/// EXPECTED, written before the run so the result cannot be reinterpreted:
///   1. the proposer offers `bal[msg.sender]` (before the change: nothing);
///   2. the ladder decides at least `post > pre` on it, and with --propose-r2
///      the delta rung `post - pre in [amt, amt] with post >= pre`;
///   3. the emitted .t.sol reads the slot at
///      `keccak256(abi.encode(<the expression the prank uses>, uint256(0)))`
///      -- NOT at the test contract's own address;
///   4. `forge test` is GREEN on the unmodified contract.
///
/// NEGATIVE CONTROL, which is the half that can actually fail silently: if the
/// region says nothing about `msg.sender`, the emitter must still REFUSE the
/// key with its existing wording rather than hash whatever the test's own
/// sender happens to be. A green test that established nothing looks exactly
/// like a green test that established everything.
contract N03_SenderKeyedBalance {
    mapping(address => uint256) public bal;

    function deposit(uint256 amt) external {
        require(amt > 0);
        bal[msg.sender] += amt;
    }
}
