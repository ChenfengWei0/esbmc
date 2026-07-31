// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: a region over TWO independent coordinates — Definition 6, the
/// region as a product of per-coordinate sets.
///
/// The guards on `a` and `b` are independent, so the feasible set of the taken
/// path is exactly `[11, 2^256-1] x [0, 4]`, a product. Everything the method
/// says about regions assumes this shape; one coordinate can never demonstrate
/// it.
///
/// EXPECTED: both coordinates generalise to non-trivial intervals, and the
/// emitted test bounds BOTH.
///
/// THE FAILURE TO WATCH FOR is the interesting one: a region that pins one
/// coordinate to its counterexample value and only widens the other. That still
/// certifies — every point in it does walk the path — so it passes every check
/// except "is it big". The measurement is the fraction of coordinates that come
/// back as single points, which is the only honest indicator of how much the
/// generalisation is actually doing.
contract P06_Product {
    uint256 public tag;

    function both(uint256 a, uint256 b) external {
        if (a > 10 && b < 5) {
            tag = 1;
        } else {
            tag = 2;
        }
    }
}
