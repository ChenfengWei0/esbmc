// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: checked vs `unchecked` arithmetic, and division by zero.
///
/// Two claims are on the record and both are checkable here in a second:
///
///   1. `unchecked { }` and a normal block produce a BYTE-IDENTICAL model.
///      `#sol_unchecked` is read only by checks that default OFF, and all of
///      them end in a single-successor ASSERT, so the path set, every `enc` and
///      every stable id are the same either way.
///   2. With the division check off, ESBMC models `a / 0` as `type(uintN).max`
///      — a value that exists in neither real Solidity (`Panic(0x12)`) nor bare
///      EVM (`0`). That all-ones value would otherwise flow into an R2
///      assertion, producing a test that asserts something no chain can do.
///
/// EXPECTED: `plus` and `plusU` enumerate identically; `div` witnesses a path
/// whose counterexample has `d == 0`, and stage 2 must EXCLUDE that value from
/// the region — which is why `--div-by-zero-check` at certification time is a
/// requirement rather than a preference.
///
/// This is the cheapest place to prove claim 2, because the wrong value is
/// visible in the counterexample rather than inferred from a table.
contract P18_Unchecked {
    uint256 public r;

    function plus(uint256 a, uint256 b) external {
        r = a + b;
    }

    function plusU(uint256 a, uint256 b) external {
        unchecked {
            r = a + b;
        }
    }

    function div(uint256 a, uint256 d) external {
        if (a > 10) {
            r = a / d;
        } else {
            r = 0;
        }
    }
}
