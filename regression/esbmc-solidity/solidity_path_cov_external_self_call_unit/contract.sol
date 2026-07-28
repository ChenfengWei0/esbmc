// An `external` function can ONLY be called internally through `this.f(...)`,
// which the frontend lowers to a plain direct call. That makes it the narrow
// case where a "this function is called from user code, so treat it as
// internal" rule would fire on a function that is a MAIN external entry — and
// silently delete its whole path set. A directly reachable attack surface
// would then go untested while coverage still read 100%.
//
// The invariant pinned here: `f` has its OWN complete path set regardless of
// the `this.f(a)` call site in `g`. Being expanded into a caller and being a
// unit are independent properties, and `f` has both.
//
//   f -- 1 decision + ABI non-payable gate = 3 paths (2 normal, 1 revert)
//   g -- f expanded (its decision joins g's identity) + gate = 3 paths
//   total 6 across 2 units, revert 2.
//
// This also checks that `external` (not just `public`) carries the
// external-entry marker the unit test keys on: if it did not, `f` would not be
// a unit at all and the total would drop to 3 across 1 unit.
//
// Third property, and the reason `Reached : 6` is pinned rather than just the
// path count: both units RETURN A VALUE. A RETURN terminates the frame, so an
// identity assert placed at END_FUNCTION sits downstream of the frame exit and
// can never execute — every body path of every value-returning unit was
// reported U, with only the ABI value-reject path (which reaches END_FUNCTION
// by a plain GOTO, bypassing the RETURN) still coverable. Measured here before
// the fix: `Reached : 2` of 6. The asserts are now emitted AT each RETURN, so a
// regression shows up as coverage collapsing to 2/6 — and since getters and
// view functions all return values, that failure mode is invisible in any
// contract that returns nothing.
pragma solidity ^0.8.0;

contract T {
    function f(uint256 a) external pure returns (uint256) {
        if (a > 3) {
            return 1;
        }
        return 0;
    }

    function g(uint256 a) public view returns (uint256) {
        return this.f(a);
    }
}
