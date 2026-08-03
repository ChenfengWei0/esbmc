// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES candidate 3 of the farming/deposit coords-gate: the SUCCESS BIT of
/// a low-level `call` made from an assembly block, branched on afterwards.
///
/// WHERE IT COMES FROM, verbatim, SafeERC20.safeTransferFrom, which is the
/// third statement of FarmingPool.deposit:
///     assembly ("memory-safe") {
///         ...
///         success := call(gas(), token, 0, data, 0x64, 0x0, 0x20)
///         if success {
///             switch returndatasize()
///             case 0 { success := gt(extcodesize(token), 0) }
///             default { success := and(gt(returndatasize(), 31), eq(mload(0), 1)) }
///         }
///     }
///     if (!success) revert SafeTransferFromFailed();
///
/// The driver's own report says `extcall_returns` is NOT harvested, and gives
/// three reasons; this file is reason (a) -- "assigned inside an approximated
/// assembly block: the value IS harvested and resolved, then dropped because
/// the classification has buckets only for parameters and environment values
/// and a call's return is a local."
///
/// WHY P21_ExternalCall DOES NOT COVER THIS. There the call is `s.ping()`,
/// which returns nothing, and the branch is on `armed` -- a plain bool state
/// variable the classifier CAN see. P21 isolates the re-entry model. This file
/// isolates the returned value being the branch condition.
///
/// EXPECTED, `probe`: two complete paths. The question this file answers is
/// whether their counterexamples agree on `token`, `amount` and `msg.sender`.
///   * they AGREE and the path is referred to the coordinate gate -> the
///     success bit is a quantity outside the classification and is a live
///     candidate for deposit's enc 26/27/246/247;
///   * they DIFFER on `token` -> the success bit is a FUNCTION of a payload
///     coordinate here, the shape is not reproduced, and this file is the
///     wrong isolation rather than a result.
///
/// NEGATIVE CONTROL, `ctrl`: identical body with the external call removed and
/// the branch put on the parameter. It must certify; if it does not, the run
/// measured the harness.
contract B2_ExtcallSuccess {
    uint256 public tag;

    function probe(address token, uint256 amount) external {
        bool ok;
        assembly ("memory-safe") {
            ok := call(gas(), token, 0, 0, 0, 0, 0)
        }
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    function ctrl(address token, uint256 amount) external {
        bool ok = (uint160(token) & 1) == 1;
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }
}
