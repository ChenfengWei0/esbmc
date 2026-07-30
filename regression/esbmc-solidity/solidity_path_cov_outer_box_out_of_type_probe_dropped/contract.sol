// A PROBE VALUE THE COORDINATE'S TYPE CANNOT HOLD IS DROPPED, AND SAID SO.
//
// `to` is an `address` -- 160 bits. The spec asks for a probe at 2^200. A bound
// is built as `constant_int2tc(<coordinate's type>, v)`, so that value WRAPS and
// the probe asks about a completely different number than the one written down.
//
// This is not hypothetical and it is not cosmetic. MEASURED on this very
// contract: the driver's geometric ladder lays one probe per power of two up to
// 2^255 whatever the type is, so on `address` most of the ladder was
// out-of-type. The wrapped probes came back with verdicts, and the bracket duly
// reported
//
//     to lower in [578960446186580977117854925043439539266349923328...968, 1)
//
// -- a HOLDING lower bound of 2^255 on a type whose largest value is 2^160-1.
// The driver's next span was therefore inverted and the whole generalisation
// loop died with `timeout: the monitored command dumped core`. No address
// coordinate could complete the loop at all.
//
// DROPPED, NOT CLAMPED. Clamping invents a probe nobody asked for; dropping
// removes one that could not have meant anything, and the type maximum is
// already seeded as the free outer bound, so no information is lost. The count
// is reported because a silently shorter ladder is a silently coarser
// measurement -- the desc pins both how many were dropped and how many remain.
//
// The TYPE RANGE line is pinned separately, and it is the half that fixes the
// cause rather than the symptom: the tool has always computed this number and
// kept it to itself, so the driver -- which is the component that chooses the
// ladder -- had no way to bound it. With the range published, the geometric
// ladder is laid over the type instead of over 2^256, and the address fixture
// now runs end to end and certifies both of its paths.
//
// The last desc line pins that the out-of-type probe produced NO claim at all,
// as a line-anchored negative lookahead: the harness has no negative-pattern
// syntax, so an absence has to be written as a regex over the whole output.
// Without it, a future change could stop counting the value while still
// emitting its probe.
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
