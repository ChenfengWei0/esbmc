// A PUNCHED INTERVAL HAS A SECOND WAY OF BEING EMPTY, and `lo <= hi` cannot
// see it.
//
//     box `to in [255, 255] \ {255}`
//
// The interval is well-formed by every test that existed before holes did --
// lo is not greater than hi, the coordinate is bounded once, the type is
// whitelisted -- and it still admits no input at all. An unsatisfiable entry
// assumption means nothing executes, every exit assert holds for want of an
// execution, and the run would print VERIFICATION SUCCESSFUL next to a box
// containing no inputs. That is a FALSE certificate, not a weak one.
//
// This is the third route to a vacuous certificate found in this stage, after
// an inverted interval (`a in [100, 11]`) and a signed wrap (the same decimal
// box refuted on uint256 and "certified" on int256). All three have the same
// shape: the box is empty in the SOLVER while the printed numbers look like a
// region. The pattern is why the check is on the count of values the holes
// actually remove from [lo, hi] rather than on the endpoints.
//
// Refusal, not a warning, and BEFORE the query is emitted: an unsatisfiable
// assumption does not make the question hard, it makes it meaningless, so there
// is nothing to interpret afterwards. The desc pins that no verdict line is
// printed either -- a caller reading SUCCESSFUL/FAILED as whole lines has to
// see its explicit third state rather than a missing green.
//
// Holes OUTSIDE [lo, hi] are deliberately not counted: they remove nothing, and
// counting them would refuse a perfectly good box.
pragma solidity ^0.8.0;

contract Gate2 {
    uint256 public sink;
    address constant BANNED = address(0x00000000000000000000000000000000000000ff);

    function send(address to) external payable returns (uint256) {
        require(to != BANNED);
        sink = 1;
        return 1;
    }
}
