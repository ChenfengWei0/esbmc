// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.5.0;

// KNOWNBUG: mulmod(MAX, MAX, MAX) crashes ESBMC with SIGFPE.
// The 512-bit intermediate product (2^256-1)^2 overflows ESBMC's
// constant evaluator during simplification.
// True result: (MAX * MAX) % MAX = 0
contract MulmodOverflowMax {
    function test() public pure {
        uint256 a = 0;
        a -= 1; // a = MAX = 2^256 - 1
        uint256 result = mulmod(a, a, a);
        assert(result == 0);
    }
}
