// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: how a TUPLE return lowers, and whether the path-coverage
/// return-value ghost can reach it.
///
/// WHY THIS EXISTS. The return-value ghost was built for a SCALAR `RETURN`
/// operand and verified on P19_ReturnShapes.tern_lit (10 vs 20). The corpus
/// unit it was built to unblock is `Aqua.rawBalances`, and solc says that one
/// is `-> (uint248, uint8)` -- a TUPLE. "Returns two scalars" and "returns a
/// tuple of two scalars" were conflated once already; the second is a shape the
/// scalar ghost cannot hold, so the question is settled here on ten lines
/// rather than on a 2000-line flat that needs a pinned compiler.
///
/// THE THREE CELLS, one unit each, so a partial answer is visible as a partial
/// answer instead of being read as a whole one:
///
///   one_scalar   the shape already known to work -- the CONTROL. If this ever
///                stops reporting a value, the failure is in the ghost and not
///                in tuples.
///   two_scalars  the shape Aqua.rawBalances has: `returns (uint256, uint256)`
///                with a two-value `return`.
///   mixed_width  the shape Aqua.rawBalances has EXACTLY: unequal widths, which
///                is what would break a ghost typed from the first member.
///
/// EXPECTED, per unit: two paths (the ABI value gate's revert arm plus the
/// body), and the two normal-exit paths of each must disagree on the returned
/// values -- that is what makes a reported value checkable at all. A tuple unit
/// reporting NO value is a real answer too (the honest "unknown"), and it is
/// the one that says the ghost needs a per-member extension.
contract P27_TupleReturn {
    function one_scalar(uint256 x) external pure returns (uint256) {
        if (x > 100) {
            return 11;
        }
        return 22;
    }

    function two_scalars(uint256 x) external pure returns (uint256, uint256) {
        if (x > 100) {
            return (11, 12);
        }
        return (22, 23);
    }

    function mixed_width(uint256 x) external pure returns (uint248, uint8) {
        if (x > 100) {
            return (11, 1);
        }
        return (22, 2);
    }
}
