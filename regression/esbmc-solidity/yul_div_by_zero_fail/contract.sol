// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

contract H {
    // Semantic-deviation test: Yul `div(_, 0) == 0` (no panic), which
    // diverges from Solidity 0.8+ where `100 / 0` panics with
    // Panic(0x12). T2.4's lowering encodes the Yul rule as
    // `if (b == 0) return 0; else return bvudiv(a, b)`, so ESBMC's
    // built-in --div-by-zero-check (which fires on the bvudiv op)
    // never sees a zero divisor — the if-branch routes around it.
    //
    // Consequence: to surface a Yul div-by-zero bug, the programmer
    // must write a post-condition that the Yul model violates. Here
    // `assert(r == 100)` would only hold under panic-on-zero
    // semantics; the Yul model returns 0, so the assertion fails
    // and ESBMC reports the assertion violation. Pairs with
    // yul_div_zero_pass (asserts r == 0).
    function check() public pure {
        uint256 r;
        assembly {
            r := div(100, 0)
        }
        assert(r == 100);
    }
}
