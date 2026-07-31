// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: mapping-keyed state, which is what forces the SOLVER CHOICE.
///
/// Every earlier PoC uses scalar storage, so the frontend picks the fast
/// backend. A nested mapping makes it auto-select CVC5 with a stated reason:
/// "Bitwuzla aborts on the CONST_ARRAY-initialised infinite mapping array". On
/// aqua that choice is where a whole-contract run's memory went, and the same
/// contract shape is why st1inch's queries do not return inside 120 s.
///
/// EXPECTED: the auto-selection warning appears, and the per-claim solve time
/// is measurably higher than the scalar PoCs' — which are around 0.1 s.
///
/// THE NUMBER THIS BUYS: a per-claim cost for a mapping, on a contract with
/// three decisions, where nothing else can be blamed. Corpus profiling gives
/// 0.246 s/claim on aqua and >120 s on st1inch, a gap of three orders of
/// magnitude with no isolated cause. This is the first rung of a ladder that
/// can find it: scalar, one mapping, nested mapping, mapping plus 256-bit
/// arithmetic. Without that ladder "st1inch is slow" is not actionable.
contract P16_Mapping {
    mapping(address => mapping(uint256 => uint256)) public bal;

    function put(uint256 k, uint256 v) external {
        require(v > 0);
        bal[msg.sender][k] = v;
    }

    function take(uint256 k, uint256 v) external {
        require(v > 0);
        require(bal[msg.sender][k] >= v);
        if (v > 100) {
            bal[msg.sender][k] -= v;
        } else {
            bal[msg.sender][k] = 0;
        }
    }
}
