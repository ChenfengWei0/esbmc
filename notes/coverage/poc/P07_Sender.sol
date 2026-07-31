// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: an ENVIRONMENT coordinate that is not a function parameter.
///
/// `msg.sender` is not an argument the test can pass; it is established with
/// `vm.prank`. So this path's region has a coordinate the renderer must express
/// in a completely different way from `bound(x, lo, hi)`, and `owner` is
/// storage written by the constructor, which is a third kind again.
///
/// EXPECTED: `onlyOwner`'s taken path certifies with `msg.sender` PINNED to the
/// owner value (an equality-constrained coordinate — the honest outcome, not a
/// failure), and the emitted test carries `vm.prank(owner)`.
///
/// WHY IT IS HERE: an equality-constrained ADDRESS coordinate was previously
/// recorded as degenerating into ~160 rounds of bisection during shrinking. On
/// the real contract that instance turned out to be `immutable` and therefore
/// pinned rather than generalised. This is the case where it is NOT immutable,
/// so if that degeneration is real, this is where it shows up — in one second,
/// on ten lines.
contract P07_Sender {
    address public owner;
    uint256 public hits;

    constructor() {
        owner = msg.sender;
    }

    function onlyOwner() external {
        require(msg.sender == owner);
        hits += 1;
    }
}
