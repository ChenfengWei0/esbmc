// THE PER-PATH EMIT SEQUENCE — R0's third rung, and the one that needs a
// fixture with a control rather than a count.
//
// R0 is "the generated test must reproduce the path's OBSERVABLE behaviour":
// same exit kind, same revert reason, and the same events in the same order.
// The first two rungs are single values and a wrong one is visibly wrong. The
// third is a SEQUENCE, and its two failure modes both look like success:
//
//   (1) THE ALWAYS-EMPTY CHANNEL. A recorder that never fires reports "no
//       events" on every path, which is indistinguishable from a contract that
//       emits nothing — and every contract in a regression suite that does not
//       specifically emit IS such a contract. This pass has already shipped one
//       recorder consumed by nothing, so the mode is not hypothetical.
//   (2) THE ORDER-BLIND RECORDER. A set, or a per-unit constant pasted onto
//       every path, gets the NAMES right and the ORDER wrong. Since almost
//       every fixture emits its events in only one order, nothing separates it
//       from a correct one.
//
// This fixture is built to fail loudly in both modes, and every one of its
// three units is here for that reason:
//
//   * `ab` and `ba` are THE SAME FUNCTION but for the order of two emits. A
//     recorder that reports a set, or that reports the unit's events rather
//     than the path's, gives these two IDENTICAL sequences. The desc pins the
//     two orders separately, so agreement is red.
//   * `none` is the negative control for mode (1). It emits nothing, so the
//     count of non-empty sequences must be strictly less than the count of
//     witnessed paths. A recorder stamping a constant onto every path fails
//     here; so does a desc that only ever checks "some events were found".
//   * every unit is non-payable, so each also carries the SYNTHESISED ABI
//     value gate path (depth 1). That path reverts before reaching the emits,
//     so its sequence must be EMPTY even in `ab` and `ba`. This is the
//     per-path-ness check: a per-unit constant would put Alpha and Beta on it.
//
// AND WHAT THE EMPTY ARRAY MEANS, which is the reason the summary line counts
// `published for N of N` separately from `M of them non-empty`. In the report,
// an F claim ALWAYS carries `events`; an empty array therefore says "recorded,
// and this path emits nothing recordable", while a MISSING field says "nobody
// recorded". A generator can only assert the absence of events on the strength
// of the first. Folding the two counts into one is precisely how mode (1)
// passes for a working rung.
//
// ⚠ RECORDABLE IS NOT THE SAME AS EMITTED. The qualified spelling
// `emit L.E(x)` (an event declared in a library or another contract) becomes a
// code_skipt() in the front end and reaches the goto program carrying nothing,
// so it is invisible to this rung. No unit here uses that spelling, so this
// fixture says nothing about it, and an empty array must never be turned into
// an assertion that NO event fires.
pragma solidity ^0.8.0;

contract EV {
    event Alpha(uint256 got);
    event Beta(uint256 got);

    uint256 public v;

    function ab(uint256 x) external {
        emit Alpha(x);
        emit Beta(x);
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }

    function ba(uint256 x) external {
        emit Beta(x);
        emit Alpha(x);
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }

    function none(uint256 x) external {
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }
}
