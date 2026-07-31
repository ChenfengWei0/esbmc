// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: `msg.value`, and the SYNTHESISED non-payable gate.
///
/// Two units, deliberately paired:
///   `pay` is payable and branches on `msg.value`;
///   `free` is non-payable, so the frontend synthesises a `msg_value == 0` gate
///   for it — a decision that exists in path coverage and NOT in branch
///   coverage, whose location is COPIED from the unit's first body instruction.
///
/// EXPECTED: `pay`'s two paths certify with a `msg.value` interval and render
/// as `vm.deal` + a value-carrying call; `free`'s synthesised gate is reported
/// as `synthetic_abi_gate` and is EXCLUDED from any decision projection.
///
/// WHY IT MATTERS BEYOND RENDERING: because the gate's location is copied from
/// a real line, counting it would credit the method with whatever real decision
/// sits on that line. The projection must drop it on the producer's own flag,
/// never by matching its condition text. This contract is where that can be
/// checked against a hand-known answer instead of a benchmark's.
contract P08_Value {
    uint256 public got;

    function pay(uint256 x) external payable {
        require(x > 0);
        if (msg.value >= 1 ether) {
            got = 1;
        } else {
            got = 2;
        }
    }

    function free(uint256 x) external {
        require(x > 0);
        got = 3;
    }
}
