// Return-shape cross-product for the exit census.
//
// Built by crossing two syntactic dimensions rather than by listing the shapes
// someone thought of, because the shapes someone thinks of are exactly what a
// regression suite already covers.
//
// Dimension 1, the return declaration:
//   (a) none        (b) unnamed        (c) named        (d) mixed named/unnamed
// Dimension 2, how the unit terminates:
//   (i) explicit `return expr;`   (ii) bare `return;`   (iii) falls off the end
//   (iv) returns on one branch, falls off on another
//   (v) a revert path beside a fall-off path
//   (vi) returns from inside a loop
//
// One cell the grid asks for DOES NOT EXIST: (c)(ii), a named return with a
// bare `return;`, is rejected by solc ("Return arguments required"). Crossing
// dimensions produces cells the language forbids, and letting the compiler rule
// them out is cheaper than arguing about whether the tool handles them. The
// legal neighbour (a)(ii) stands in its place.
//
// Every unit branches, so each contributes at least two paths and the exit
// census has something to be right or wrong about.
//
// What this pins: an exit is `normal` only on POSITIVE evidence. A RETURN exit
// can only ever have one witness -- symex ends the frame at RETURN and never
// reaches END_FUNCTION, so `saw_epilogue` is false there by construction and
// the frontend's source-return marker is the whole case. `e_named_fall` is the
// shape that made aqua's `Aqua.ship` report 62 undetermined exits.
pragma solidity ^0.8.0;

contract Ret {
    uint256 public s;

    // (a)(iii) -- no return values at all, falls off the end.
    function a_void_fall(uint256 x) external {
        if (x > 0) {
            s = 1;
        } else {
            s = 2;
        }
    }

    // (b)(i) -- unnamed return, explicit return on every path.
    function b_unnamed_explicit(uint256 x) external returns (uint256) {
        if (x > 0) {
            return 1;
        }
        return 2;
    }

    // (c)(i) -- named return, but still an explicit return on every path.
    function c_named_explicit(uint256 x) external returns (uint256 r) {
        if (x > 0) {
            return 1;
        }
        return 2;
    }

    // (a)(ii) -- void unit with a bare `return;` on one path.
    function d_void_bare_return(uint256 x) external {
        s = 1;
        if (x > 0) {
            return;
        }
        s = 2;
    }

    // (c)(iii) -- named return, no `return` statement anywhere. This is the
    // `Aqua.ship` shape.
    function e_named_fall(uint256 x) external returns (uint256 r) {
        if (x > 0) {
            r = 1;
        } else {
            r = 2;
        }
    }

    // (c)(iv) -- returns on one branch, falls off the end on the other.
    function f_named_mixed_exit(uint256 x) external returns (uint256 r) {
        if (x > 0) {
            return 1;
        }
        r = 2;
    }

    // (d)(iii) -- mixed named/unnamed tuple, falls off the end.
    function g_tuple_fall(uint256 x) external returns (uint256 r, bool) {
        if (x > 0) {
            r = 1;
        } else {
            r = 2;
        }
    }

    // (c)(v) -- a revert path beside a fall-off path. This is the pair that
    // keeps the marker honest: marking the synthesised return must NOT turn the
    // reverting path normal, because classify_exit tests rollback first.
    function h_named_fall_with_revert(uint256 x) external returns (uint256 r) {
        require(x > 0, "zero");
        r = 1;
    }

    // (c)(vi) -- returns from inside a loop, falls off the end otherwise.
    function i_return_in_loop(uint256 x) external returns (uint256 r) {
        for (uint256 k = 0; k < 2; k++) {
            if (k == x) {
                return k;
            }
        }
        r = 9;
    }
}
