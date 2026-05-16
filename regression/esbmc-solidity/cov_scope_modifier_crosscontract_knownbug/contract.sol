// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// A defines a modifier `gate` whose body contains a branch.
// A itself does NOT use `gate`. A also has its own branchy function
// `bump` that is NOT public-exposed via B's transactions.
// B inherits A and uses `gate`. We verify with --contract B.
// Logically only B-reachable branches should be counted.
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
