// SPDX-License-Identifier: MIT
pragma solidity 0.8.23;

/// STEP 0 of the setFeeReceiver ladder — the function, verbatim, and nothing else.
///
/// The real `St1inch.setFeeReceiver` (flat 4567-4571) is:
///
///     function setFeeReceiver(address feeReceiver_) public onlyOwner {
///         if (feeReceiver_ == address(0)) revert ZeroAddress();
///         feeReceiver = feeReceiver_;
///         emit FeeReceiverSet(feeReceiver_);
///     }
///
/// On the real contract this unit enumerates 5 complete paths and witnesses NONE
/// of them: `F 0, U 5` — `bounded-holds 2`, `solver-unknown 3`, the three with
/// z3's own reason `out of memory` at ~10 s each. Measured, D32.
///
/// SAY IT PRECISELY, because the imprecise version has already misled me once:
/// the paths ARE enumerated. What is missing is the WITNESSES. "Cannot get a
/// path" and "cannot get a counterexample for a path" are different failures
/// with different causes.
///
/// This file is the bottom of a ladder whose top is the real contract. It carries
/// the function and the three things it syntactically needs — an owner, the
/// modifier, the error and the event — and NOTHING else. If it does not solve
/// instantly, the function itself is the problem and every rung above is
/// irrelevant. If it does, the cause is something the real contract adds, and the
/// ladder says which rung adds it.
///
/// PREDICTION, written before the run: F = every path, sub-second. Anything else
/// and the ladder is not needed because the answer is here.
contract D36 {
    error ZeroAddress();
    event FeeReceiverSet(address receiver);

    address public owner;
    address public feeReceiver;

    modifier onlyOwner() {
        require(msg.sender == owner);
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    function setFeeReceiver(address feeReceiver_) public onlyOwner {
        if (feeReceiver_ == address(0)) revert ZeroAddress();
        feeReceiver = feeReceiver_;
        emit FeeReceiverSet(feeReceiver_);
    }
}
