// AN INVERTED SPAN IS A RECORDABLE REFUSAL, NOT A CORE DUMP.
//
// `lo=100, hi=11` contains no probe value at all. The tool has always caught it;
// what it used to do was `abort()`, and unattended that is the difference
// between a datum and a lost run -- the driver sees SIGABRT, has no message to
// classify, and the round is reported as "the round measured nothing" with no
// cause. It now exits 1 with a message naming the coordinate and both bounds.
//
// It is reachable from an ordinary driver bug rather than only from a typo, and
// that is why it earns a fixture. MEASURED on a live run: the geometric bracket
// lays probes up to 2^255, which does NOT fit a 160-bit `address` coordinate;
// the out-of-type values wrap, the bracket comes back with a lower bound of
// 2^255 and an upper bound of 2^160-1, and the driver's next span is inverted.
// The whole loop then died with `timeout: the monitored command dumped core`.
//
// (That wrapping is a SEPARATE defect and is not fixed here -- pinning it in
// this fixture would make one test fail for two unrelated reasons. What is
// pinned here is only that the malformed span is refused legibly.)
//
// The desc also pins that NEITHER verdict line is printed, so a caller reading
// SUCCESSFUL/FAILED as whole lines sees its explicit third state rather than a
// missing green. The negative is a line-anchored lookahead because the harness
// has no negative-pattern syntax at all.
pragma solidity ^0.8.0;

contract Gate3 {
    uint256 public sink;

    function send(uint256 to) external payable returns (uint256) {
        require(to != 255);
        sink = 1;
        return 1;
    }
}
