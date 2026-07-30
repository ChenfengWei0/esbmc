// LEVEL 0: a candidate list holding ONE value asks an EQUALITY, in one batch,
// with both directions decidable.
//
// `send` has two complete paths. The require holds -> `to != BANNED`, whose
// domain is the address type minus one point. The require fails -> `to ==
// BANNED`, whose domain is EXACTLY that point. The second is the level-0 case:
// its real constraint is an equality, and the geometric ladder answers
// equalities by bisecting, which on 2^160 is about 160 rounds. Measured on
// state.FACTORY, that is exactly what it did: 2923...595 -> 429496731 ->
// 214748363 -> 107374179 -> 53687087 -> 26843535 -> 13421759.
//
// WHAT THIS FILE PINS -- the PROBE MECHANICS -- and why each desc line is there:
//
// 1. NO NEW QUERY IS NEEDED. The outer-box batch already emits one probe per
//    DIRECTION, so a `values` list of one element v asks `to <= v` AND
//    `to >= v`, whose conjunction is `to == v`. The desc pins both claim names
//    (#ub_to_255 and #lb_to_255) so that a change dropping one direction --
//    leaving an inequality where an equality was measured -- goes red here.
//
// 2. BOTH DIRECTIONS ARE DECIDABLE, which is what makes it a measurement rather
//    than a hope. enc=2 passes both probes; enc=3, whose domain is everything
//    else, is refuted on both. Pinning only the passing side would accept a
//    gate that files anything into the hole.
//
// 3. THE EXCLUDED POINT IS INTERIOR, on purpose. The obvious fixture excludes
//    address(0), where the exclusion sits on the type boundary and degenerates
//    into a tightened bound -- [1, 2^160-1] -- which a closed interval expresses
//    exactly. Here enc=3's true domain is [0,254] union [256, 2^160-1], which a
//    closed interval cannot hold at all.
//
// HISTORY, kept because the desc line changed and a reader will want to know
// why. This file used to pin `to in [256, 2^160-1]`, i.e. the whole of [0, 254]
// discarded, and its comment presented that as the measured argument FOR
// punched intervals. Definition 5 is now implemented, so the same run yields
// `[0, 2^160-1] \ {255}` and the argument has become a feature. The loss it used
// to demonstrate is still measured, and still as a PAIR, in
// solidity_path_cov_punched_ce_independent and ..._hi -- which show that the
// old answer depended on which counterexample the solver returned and the new
// one does not.
//
// OVERLAP, stated rather than left to be discovered: ..._hi runs the IDENTICAL
// outer.json to this directory. It is not an independent measurement of the
// region; it exists so the counterexample-independence pair is discoverable by
// name. This directory is the one that pins the probe mechanics above.
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
