// PUNCHED INTERVAL (Definition 5): the certified region stops depending on
// WHICH counterexample the solver happened to return.
//
// `send` has two complete paths. The require holds -> `to != BANNED`, whose
// domain is the address type minus one INTERIOR point. The require fails ->
// `to == BANNED`, whose domain is EXACTLY that point. The second is the level-0
// case: its real constraint is an EQUALITY, and the geometric ladder answers
// equalities by bisecting, which on 2^160 is about 160 rounds. Measured on
// state.FACTORY, that is exactly what it did: 2923...595 -> 429496731 ->
// 214748363 -> 107374179 -> 53687087 -> 26843535 -> 13421759.
//
// THE MEASUREMENT THIS DIRECTORY EXISTS FOR IS A PAIR. The sibling directory
// solidity_path_cov_punched_ce_independent_hi is this fixture with ONE number
// changed: enc=3's supplied counterexample. Before holes existed, the
// subtraction could only keep the SIDE of the excluded point holding this
// path's own counterexample, so that one number decided the answer:
//
//     sibling CE = 0          ->  `to in [0, 254]`          (255 values)
//     sibling CE = 2^160-1    ->  `to in [256, 2^160-1]`    (~1.46e48)
//
// Both correct, both respecting "may only narrow", differing by ~5.7e45 --
// decided by a value nobody chose. Both directories now pin the SAME region,
// `[0, 2^160-1] \ {255}`, so a change that reintroduces the side cut turns
// exactly one of them red whichever counterexample it happens to favour.
// VERIFIED BY FAULT INJECTION: disabling the hole candidate in the subtraction
// restores the two different regions above, one in each directory, while the
// certification query is unaffected.
//
// WHAT ELSE THE DESC PINS, and why each line is there:
//
// 1. NO NEW QUERY IS NEEDED to ask the equality. The outer-box batch already
//    emits one probe per DIRECTION, so a `values` list of one element v asks
//    `to <= v` AND `to >= v`, whose conjunction is `to == v`. The desc pins
//    both claim names (#ub_to_255 and #lb_to_255) so that a change dropping one
//    direction -- leaving an inequality where an equality was measured -- goes
//    red here.
//
// 2. BOTH DIRECTIONS ARE DECIDABLE, which is what makes it a measurement rather
//    than a hope. enc=2 passes both probes; enc=3, whose domain is everything
//    else, is refuted on both. Pinning only the passing side would accept a
//    gate that files anything into the hole.
//
// 3. THE EXCLUDED POINT IS INTERIOR, on purpose. The obvious fixture excludes
//    address(0), where the hole sits on the type boundary and degenerates into
//    a tightened bound -- [1, 2^160-1] -- which a closed interval expresses
//    exactly. That fixture can show neither the loss nor its recovery.
//
// 4. THE HOLE COUNT IS PINNED SEPARATELY from the region text: a path that
//    happens to need no hole prints no `\ {...}`, so "the subtraction punched
//    rather than took a side" is not readable off the region alone.
//
// 5. enc=2's OWN region is pinned unchanged, with its degenerate-sibling
//    warning. Its sibling spans the whole type, so there is nothing to punch --
//    the hole must not fire where no single value separates the two.
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
