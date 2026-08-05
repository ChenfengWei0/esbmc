// A LADDER LAID PER PATH, WHICH ONE SHARED `coords` LIST CANNOT EXPRESS.
//
// The outer-box spec used to carry a single value list per coordinate, judged
// for every path in the batch. That is the wrong shape for the one thing a
// driver holding several witnesses per path knows: a rung is worth laying for a
// path only OUTSIDE that path's own known domain, and a value may be dropped
// from a SHARED list only when it is uninformative for EVERY path at once.
//
// MEASURED on notes/coverage/poc/P14_Ladder.sol `bump` with eight witnesses per
// path: enc=7's known members bracket [16, 20] and enc=6's bracket
// [2^256-4, 2^256-1]. The intersection is EMPTY -- and that is the general case
// rather than bad luck, because two paths of one unit are separated precisely by
// the coordinate the ladder is measuring. So the shared list could drop nothing
// at all, and the whole saving was unreachable from the driver.
//
// WHAT THIS FIXTURE PINS, and each item has a way of being wrong:
//
//   1. THE OVERRIDE IS CONSULTED. enc=2 is given ["777", "888"] and the claims
//      `send:path:2#ub_to_777` / `#ub_to_888` exist. Without the emitter
//      reading the spec's per-path list, neither name is ever built -- the
//      written-is-not-wired case, where the driver writes overrides nobody
//      reads and the round looks exactly the same from outside.
//
//   2. IT REPLACES, IT DOES NOT MERGE. The shared list is ["100"] and enc=3 is
//      NOT overridden, so:
//          enc=2  2 values x 2 directions = 4
//          enc=3  1 value  x 2 directions = 2
//      i.e. `emitted 6 ladder probe(s)`. A merge would put the shared `100`
//      back on enc=2 as well and emit 8, so the count alone separates the two
//      behaviours -- which matters because a knob whose effect another rule
//      quietly undoes still reports as applied.
//
//   3. THE UNOVERRIDDEN PATH KEEPS THE SHARED LADDER.
//      `send:path:3#ub_to_100` must still be there: the per-path form is an
//      addition to the spec, not a replacement of it, and a spec written before
//      it existed must behave bit-identically.
//
//   4. THE COUNT IS PRINTED EITHER WAY. The tool reports how many (path,
//      coordinate) pairs took a per-path ladder AND how many the spec carried.
//      "the spec carried none" and "the spec carried some and the emitter
//      ignored them" have the same silence, and only one of them is a defect.
//
// The bound in the desc (`to in [0, 777]` for enc=2) is a consequence rather
// than a separate claim: 777 and 888 are the only upper probes enc=2 was given,
// both hold, and the tighter one is the box.
//
// uint256 rather than address on purpose, for the same reason as the sibling
// fixture solidity_path_cov_outer_box_values_and_span: an address coordinate is
// 160-bit and out-of-type probe values wrap, which is a real but SEPARATE
// defect. A fixture that can fail for two unrelated reasons cannot tell you
// which one fired.
pragma solidity ^0.8.0;

contract Gate3 {
    uint256 public sink;

    function send(uint256 to) external payable returns (uint256) {
        require(to != 255);
        sink = 1;
        return 1;
    }
}
