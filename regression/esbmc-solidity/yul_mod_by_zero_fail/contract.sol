// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

contract H {
    // Same shape as yul_div_by_zero_fail: Yul `mod(_, 0) == 0` deviates
    // from Solidity 0.8+ panic. T2.4's lowering uses
    // `if (b == 0) return 0; else bvurem(a, b)`, so ESBMC's built-in
    // div-by-zero check never fires on the bvurem op. To detect, the
    // programmer must assert a post-condition the Yul model violates.
    function check() public pure {
        uint256 r;
        assembly {
            r := mod(7, 0)
        }
        // Would only hold under Solidity-style panic semantics.
        assert(r == 7);
    }
}
