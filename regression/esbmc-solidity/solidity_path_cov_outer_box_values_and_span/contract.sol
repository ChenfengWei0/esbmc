// THE PROBE SET IS THE UNION of the driver's explicit values and the uniform
// ladder over [lo, hi] -- and that union is what lets a punched cut fire in
// production rather than only in a hand-written spec.
//
// A ladder measures a bound only to ITS OWN RESOLUTION. So a sibling whose real
// projection is a single point comes back as an INTERVAL, and the hole cut --
// which requires the sibling to be an exactly-known point -- silently never
// applies. Measured end to end on this very shape before the union existed:
//
//   [level0]  enc=2 single-point on: to==255      <- resolved EXACTLY
//   [refine]  regions={2: {'to': (230, 256)}, ...} <- and then thrown away
//   result:   enc=3 got the side cut `[257, 2^256-1]`, losing [0, 254];
//             enc=2 was NOT CERTIFIED at all.
//
// Level 0 had already computed the exact value at zero query cost (proposition
// 9: the candidate is the sibling's own counterexample), and the round then
// replaced it with a coarser measurement. Carrying both costs two probes.
//
// THE SPAN HERE CANNOT PRODUCE 255 BY ITSELF. With lo=0, hi=1000 and probes=2
// the uniform subdivision lands on 0, 333, 666, 1000 -- 255 is not among them,
// by construction. So `to in [255, 255]` in the desc below can only come from
// the explicit `values` list, and a change that goes back to
// values-OR-span (rather than values-AND-span) turns this red whichever branch
// it keeps.
//
// The consequence is pinned too: with enc=2 resolved to a point, enc=3's region
// becomes the punched interval `[0, 2^256-1] \ {255}` instead of one side of
// 255. That is the whole input space minus one value, for a path whose side cut
// would have been either 255 values or the rest of the type depending on which
// counterexample the solver happened to return.
//
// uint256 rather than address on purpose: the geometric bracket lays probes up
// to 2^255, which does not fit a 160-bit address coordinate. That is a real,
// separate defect (the values wrap and the bracket comes back inverted) and it
// is NOT what this fixture is about -- pinning it here would make one test fail
// for two unrelated reasons.
pragma solidity ^0.8.0;

contract Gate3 {
    uint256 public sink;

    function send(uint256 to) external payable returns (uint256) {
        require(to != 255);
        sink = 1;
        return 1;
    }
}
