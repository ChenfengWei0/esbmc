// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: a path that REVERTS INSIDE AN INLINED CALLEE.
///
/// P12 inlines a public callee but the callee only returns; P13 has four exit
/// kinds but all of them are in the unit's own body. Neither covers the
/// combination, and the combination is where the exit census can be wrong in
/// the direction that produces a RED test.
///
/// The census decides a path's exit kind. If it reads the exit of the UNIT's
/// body rather than of the inlined callee that actually aborted, a path that
/// reverts is recorded as exiting normally — and the emitted test then asserts
/// the call SUCCEEDS. That is red on the unmodified contract, and it is the
/// exact shape already measured once on a real benchmark, where the census
/// confirmed a normal exit and forge reported a revert.
///
/// Three exits are crossed against callee/caller position:
///   guard fails in the CALLEE     -> `require` rollback, custom error
///   guard fails in the CALLER     -> plain revert with a string
///   nothing fails                 -> normal return
///
/// EXPECTED: three distinct exit kinds, each attributed to the path that
/// actually took it, and `vm.expectRevert` with the CALLEE's selector on the
/// callee-revert path.
///
/// A contract that reverts only in the caller cannot distinguish a census that
/// reads the right frame from one that always reads the unit's own — which is
/// why this file exists separately from P13.
contract P24_RevertInCallee {
    error CalleeSaysNo(uint256 got);

    uint256 public tag;

    function check(uint256 y) internal pure returns (uint256) {
        if (y > 900) {
            revert CalleeSaysNo(y);
        }
        require(y != 13);
        return y * 2;
    }

    function run(uint256 x) external {
        if (x == 0) {
            revert("caller-zero");
        }
        tag = check(x);
    }
}
