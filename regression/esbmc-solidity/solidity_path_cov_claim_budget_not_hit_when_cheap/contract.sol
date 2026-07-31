// THE MUST-NOT-FIRE HALF: same budget, cheap queries, no token.
//
// A budget that always fires is worth nothing -- it would turn every U into
// `claim-budget-exceeded` and the token would stop distinguishing anything.
// This project has already shipped a gate whose answer was true on every input,
// so the negative direction is pinned in its own test rather than assumed.
//
// THE PAIR VARIES THE WORKLOAD, NOT THE FLAG, and that is the point. Its
// partner (solidity_path_cov_claim_budget_abandons_and_continues) runs at the
// SAME `--path-cov-claim-timeout 1` on a contract with a 256-bit
// multiplication obligation and gets the token; this one runs at the same 1 s
// on four cheap paths and gets `claim-budget-exceeded 0` with all four
// witnessed. So the token tracks what the query COST, not whether the flag was
// passed -- which is the property that would silently break if the budget were
// ever applied to the wrong clock, or if the "did it exceed?" test keyed on the
// flag rather than on the elapsed time.
//
// `Path Status: F 4, I 0, U 0` is asserted so that "no token" cannot be
// explained away as "this run decided nothing": every claim was asked, answered
// and witnessed, well inside one second.
//
// The `Claim Budget:` line is pinned with its zero for the same reason the
// U-reason breakdown prints every slot: a count that is only shown when it is
// non-zero cannot be distinguished from a count that stopped being computed.
pragma solidity ^0.8.0;

contract D {
    uint256 public x;

    function g(uint256 a) public {
        require(a != 0);
        if (a > 100) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
