// A SPEC DECIMAL THAT DOES NOT FIT THE COORDINATE'S TYPE IS REFUSED.
//
//     `to` is an address (160 bits), and the spec asks for hi = 2^160.
//
// Every bound is built with a constant OF THE COORDINATE'S OWN TYPE, so an
// out-of-range decimal WRAPS: 2^160 becomes 0, the emitted query is about
// `to in [0, 0]`, and it would answer about a box nobody asked for. If that
// answer came back SUCCESSFUL it would be a false certificate -- the same
// failure as the signed-type hole documented in coord_expressible, reached
// through the VALUE rather than through the type.
//
// This check was added with punched intervals because holes are a new place for
// the same mistake to enter, and validating only the new surface while leaving
// lo/hi unvalidated would have been arbitrary: all three are the same kind of
// decimal turned into the same kind of constant. So the check covers lo, hi and
// every hole, and this fixture pins the lo/hi half of it.
//
// It does NOT unlock signed coordinates. coord_expressible still refuses those
// by type, and the note there asks for exactly this validation against the
// signed range as a separate change with its own criteria -- one unsigned range
// check must not be read as having done that work.
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
