// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: inheritance, `virtual`/`override`, and a base-declared unit.
///
/// Every other contract in this set is a single flat contract. Real benchmarks
/// are not: the one benchmark whose runs all died was blocked for a while by a
/// frontend defect that zeroed initialised locals INSIDE INHERITED FUNCTION
/// BODIES, which is a shape nothing here reproduces.
///
/// Three distinct things are crossed:
///   * `baseOnly` is declared in the base and never overridden — a unit whose
///     body lives in another contract;
///   * `hook` is `virtual` in the base and overridden in the derived contract —
///     which body does the unit enumerate?
///   * `useHook` calls `hook()` internally, so the OVERRIDE is what gets
///     physically inlined into its paths.
///
/// EXPECTED: units are enumerated for the DERIVED contract including the
/// inherited `baseOnly`; `useHook`'s paths contain the DERIVED `hook`'s
/// decision, not the base's.
///
/// WHAT WOULD BE A DEFECT AND WOULD NOT LOOK LIKE ONE: `useHook` inlining the
/// BASE body. The run reports a perfectly ordinary path count and every test is
/// green on the base's behaviour — while the contract under test is the derived
/// one. Virtual dispatch is also explicitly excluded from the canonical
/// decision set, so the baseline metric will not disagree and cannot flag it.
///
/// `--focus-function baseOnly` with `--contract Derived` also asks a scoping
/// question that has already caused a silent empty run on a real contract: a
/// function declared in a base and filtered out by the contract scope.
contract P22_Base {
    uint256 public v;

    function baseOnly(uint256 x) external {
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }

    function hook(uint256 x) internal virtual returns (uint256) {
        return x + 1;
    }
}

contract P22_Inherit is P22_Base {
    function hook(uint256 x) internal pure override returns (uint256) {
        if (x > 100) {
            return 7;
        }
        return 8;
    }

    function useHook(uint256 x) external {
        v = hook(x);
    }
}
