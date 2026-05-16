// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// A defines a modifier `gate` whose body contains a branch; A also has
// its own branchy internal `bumpInternal`. B inherits A and applies
// `gate` to setB. We verify with --contract B.
//
// Per-contract semantics A (lexical declarer, NOT reachability): the
// `gate` branch is textually declared inside contract A, so it is
// attributed to A and EXCLUDED from B's own count even though B applies
// the modifier. B's setB has no own decision => "No branch detected".
// This is the named, accepted completeness trade-off (a base/other-
// contract-defined modifier applied by B is not counted under
// --contract B; there is no opt-in whole-unit escape hatch). KNOWNBUG
// pins this gap honestly; it flips only if that trade-off is revisited.
contract A {
    uint256 public a;
    modifier gate(uint256 z) {
        if (z > 100) {
            _;
        }
    }
    function bumpInternal(uint256 p) internal {
        if (p > 3) {
            a = p;
        }
    }
}

contract B is A {
    uint256 public b;
    function setB(uint256 v) public gate(v) {
        b = v;
    }
}
