// Stage-2 CERTIFICATION QUERY: a SIGNED coordinate is refused, because the same
// box that is correctly refuted on uint256 certifies VACUOUSLY on int256.
//
// This contract is `_certify_box_inside` with ONE TOKEN changed -- `uint256 a`
// became `int256 a` -- and cert.json bounds `a` by the full unsigned range,
// which is what the driver falls back to when no bracket was measured. The pair
// was run before the refusal existed:
//
//     uint256 a, a in [0, 2^256-1]  ->  VERIFICATION FAILED
//     int256  a, the SAME box       ->  VERIFICATION SUCCESSFUL
//
// FAILED is the correct answer for both: the box is the whole type, so it holds
// inputs that walk the other path, and the query is supposed to say so. The
// second line is a FALSE CERTIFICATE -- the third route to one found in this
// pipeline, after a driver substring-matching a verdict phrase and an inverted
// interval certifying for want of an execution.
//
// The mechanism: a bound becomes a constant of the coordinate's OWN type and the
// comparison is signedness-aware, so on a signed 256-bit type the decimal
// 2^256-1 is all ones, i.e. -1 under bvsle. `a >= 0 && a <= -1` is
// unsatisfiable, nothing executes, and every exit assert holds.
//
// WHAT MAKES THIS WORTH A FIXTURE RATHER THAN A COMMENT is which defence it got
// past. The empty-box guard added a few hours earlier compares the spec's
// DECIMAL lo and hi, and 0 <= 2^256-1 decimally, so it never fires. The box is
// empty in the SOLVER and non-empty in the SPEC. Two readings of "empty", and
// the guard was written knowing only one of them.
//
// Signed types are now refused outright. That is the fail-closed direction and
// costs nothing measured: every coordinate any real contract has produced so far
// is uint256, address or bytesN. Supporting signed is NOT a whitelist entry --
// it is bound validation against the coordinate's own signed range -- and this
// test exists partly to make widening the type test back the thing that breaks.
pragma solidity ^0.8.0;

contract Box {
    function f(int256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
