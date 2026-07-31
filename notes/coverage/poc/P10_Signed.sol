// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: a SIGNED coordinate.
///
/// `coord_expressible` refuses `signedbv` today, so this is a contract the
/// method is expected to REFUSE rather than mis-handle. It is in the set for
/// exactly that reason: a refusal that is loud and specific is a result, and a
/// refusal that silently degrades to an unsigned interval is a defect that
/// would put wrong values in a test.
///
/// EXPECTED: paths enumerate and are witnessed (enumeration does not care about
/// signedness), and stage 2 REFUSES the region with a message naming the
/// coordinate and the reason. Not a crash, not a silent unsigned box.
///
/// The driver was made signed-ready ahead of the tool (five regexes that only
/// matched `\d+` would have made a published `TYPE RANGE [-578..., 578...]`
/// parse as empty and the ladder fall back to `[0, 2^256-1]` — a wrong range
/// with every log line saying the round ran). This contract is what proves the
/// tool side when it lands, and proves the refusal is honest until then.
contract P10_Signed {
    int256 public acc;

    function shift(int256 d) external {
        if (d < 0) {
            acc -= 1;
        } else if (d > 1000) {
            acc += 2;
        } else {
            acc += 1;
        }
    }
}
