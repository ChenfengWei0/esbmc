// LEVEL 0: a candidate list holding ONE value asks an equality, in one batch,
// with both directions decidable -- and the closed interval then loses a whole
// side of the sibling's domain.
//
// `send` has two complete paths. The require holds -> `to != BANNED`, whose
// domain is the address type minus one point. The require fails -> `to ==
// BANNED`, whose domain is EXACTLY that point. The second is the level-0 case:
// its real constraint is an EQUALITY, and the geometric ladder answers
// equalities by bisecting, which on 2^160 is about 160 rounds. Measured on
// state.FACTORY, that is exactly what it did: 2923...595 -> 429496731 ->
// 214748363 -> 107374179 -> 53687087 -> 26843535 -> 13421759.
//
// WHAT THIS FILE PINS, and why each line of the desc is there:
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
//    address(0), where the hole sits on the type boundary and degenerates into
//    a tightened bound -- [1, 2^160-1] -- which a closed interval expresses
//    exactly. That fixture cannot show the loss. Here enc=3's true domain is
//    [0,254] union [256, 2^160-1], a closed interval cannot hold it, and the
//    subtraction keeps only the side containing enc=3's own counterexample.
//    The desc pins `to in [256, ...]`: the whole of [0, 254] is gone.
//
//    That is the measured argument for a punched interval rather than an argued
//    one, and it is why the counterexample value in outer.json matters. Supply
//    0 instead of 2^160-1 -- also a genuine member of enc=3's domain -- and the
//    certified region becomes `to in [0, 254]`, 255 values instead of about
//    1.46e48. Both are correct, both respect "may only narrow", and they differ
//    by roughly 5.7e45 decided by nothing but which member was supplied.
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
