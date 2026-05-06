// SPDX-License-Identifier: MIT
// KNOWNBUG: solver-auto-hint Pattern B over-fires for unbound k-induction.
//
// Pattern B in src/esbmc/esbmc_parseoptions.cpp:1216-1218 forces --cvc5
// whenever ALL of `--k-induction` + ≥2 contracts + value-call (.transfer/
// .send/.call{value:}) hold. The rationale (lines 846-865) is that the
// k-induction-amplified `this->$address` 256-bit-equality dispatcher
// chain balloons under Bitwuzla's BV-quantifier engine. But that chain
// is only materialized under `--bound` or `--reentry-check`; without
// either, ESBMC nondeterministically models external calls and there is
// no chain — so Pattern B over-fires and forces CVC5 when there is no
// CVC5 advantage.
//
// This canary triggers Pattern B (2 contracts + .transfer + --k-induction)
// WITHOUT --bound. Pre-narrowing the auto-heuristic forces --cvc5; post-
// narrowing the default fallback (bitwuzla → cvc5 → boolector → z3) picks
// Bitwuzla. The KNOWNBUG oracle includes a `^Solving with solver Bitwuzla`
// regex line that only matches post-fix, so the test silently
// reclassify-flag-holds at HEAD and flips to CORE once the narrowing
// lands.
//
// The assertion `total == before_total - amount` is a true invariant of
// the body (decrement-and-assert), so VERIFICATION SUCCESSFUL is the
// correct verdict regardless of solver.
pragma solidity >=0.8.0;
contract Vault {
    uint256 public total;

    function deposit(uint256 amount) public payable {
        total += amount;
    }

    function withdraw(address payable recipient, uint256 amount) public {
        require(total >= amount);
        uint256 before_total = total;
        total -= amount;
        recipient.transfer(amount);
        assert(total == before_total - amount);
    }
}

contract Sink {
    receive() external payable {}
}
