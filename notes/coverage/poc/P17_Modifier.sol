// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// NOTE ON COMMENT STYLE: this file uses `//`, not `///`. The first version used
// NatSpec and did not compile — solc reads `@C@M@F@set#38` inside a `///` block
// as a documentation tag and refuses the contract:
//
//     Error: Documentation tag @C@M@F@set#38` not valid for contracts.
//
// Worth keeping as a note, because the whole point of this set is that a
// contract which does not run teaches nothing, and this one silently would not
// have.
//
// ISOLATES: a modifier, and the RENAMING it causes.
//
// The frontend rewrites a modified function into a renamed body: `set` with
// `onlyPositive` becomes `set_onlyPositive`, inlined into the unit that keeps
// the SOURCE-LEVEL name `set`. Measured on the existing modifier regression:
// the unit id ends in `@F@set#38` and the counterexample's stack frame is
// `set_onlyOwner`.
//
// EXPECTED: one unit named `set`; the modifier's `require` present as a
// decision inside its paths; `--focus-function set` selects it, and
// `--focus-function set_onlyPositive` does NOT, because that name is not a unit.
//
// WHY IT IS IN THE SET: README.md:843-845 says modifier-renamed functions are
// "prefix-matched" by `--focus-function`. There is no prefix matching. The name
// the documentation tells a user to pass is rejected by the validator, and the
// name that works is the one the documentation does not mention. That is one of
// five places where the docs and the source disagree, and it is the one that
// silently changes which unit an experiment measured.
contract P17_Modifier {
    uint256 public v;

    modifier onlyPositive(uint256 x) {
        require(x > 0);
        _;
    }

    function set(uint256 x) external onlyPositive(x) {
        if (x > 100) {
            v = 1;
        } else {
            v = 2;
        }
    }
}
