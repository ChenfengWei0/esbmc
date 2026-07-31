// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: where a decision that lives INSIDE A RETURN gets instrumented.
///
/// The enumerating DFS fans out at exactly three site kinds and everything else
/// walks straight through:
///
///   1. a conditional GOTO
///   2. a folded short-circuit inside an ASSIGN
///   3. a folded short-circuit inside a RETURN
///
/// Kind 3 exists precisely for `return <cond> ? a : b` and `return a && b`. But
/// which kind a given return shape actually lands on depends on how the
/// frontend lowers it, and that is NOT something to assume: this repository has
/// already been caught out three times by return shapes, which is why its own
/// rule says shapes must be enumerated as a CROSS PRODUCT of syntactic
/// dimensions rather than by picking the ones that came to mind.
///
/// So the dimensions are crossed here, one function per cell:
///
///   ternary / short-circuit  x  literal arms / call arms  x  plain / nested
///
/// EXPECTED, per function, countable by hand:
///   tern_lit    2 paths   one decision, two literal arms
///   sc_lit      2 paths   `&&` folds to one decision site with two operands
///   tern_call   2 x 2 = 4 both arms are internal calls with a branch each,
///                         physically inlined before enumeration
///   tern_nested 3 paths   two decisions, the second only on one arm
///   cond_call   4 paths   two inlined callees inside one comparison
///
/// WHAT WOULD BE A DEFECT: any of these enumerating FEWER paths than the count
/// above. A decision that the DFS walks straight past does not become a `tr`
/// bit, so two genuinely different executions collapse to one `enc` — and the
/// method's central claim, that an `enc` identifies exactly one path, is false
/// for that shape. It would not crash and it would not look wrong; the run
/// would simply report a smaller, tidier path set.
contract P19_ReturnShapes {
    function leaf1(uint256 y) internal pure returns (uint256) {
        if (y > 50) {
            return 1;
        }
        return 2;
    }

    function leaf2(uint256 y) internal pure returns (uint256) {
        if (y > 200) {
            return 3;
        }
        return 4;
    }

    function tern_lit(uint256 x, uint256 y) external pure returns (uint256) {
        return x > y ? 10 : 20;
    }

    function sc_lit(uint256 x, uint256 y) external pure returns (bool) {
        return x > 10 && y < 5;
    }

    function tern_call(uint256 x, uint256 y) external pure returns (uint256) {
        return x > y ? leaf1(x) : leaf2(x);
    }

    function tern_nested(uint256 x, uint256 y) external pure returns (uint256) {
        return x > y ? (x > 100 ? 1 : 2) : 3;
    }

    function cond_call(uint256 x) external pure returns (bool) {
        return leaf1(x) > leaf2(x);
    }
}
