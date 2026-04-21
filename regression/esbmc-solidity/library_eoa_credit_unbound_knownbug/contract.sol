// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG: In unbound mode, `addr.balance` on an OPAQUE address
// (an `address payable` parameter that is not a known contract
// instance) short-circuits to a fresh `nondet_uint()` before
// reaching `get_builtin_property_expr`, so the EOA balance map
// credited by transfer/send/call is invisible.
//
// Library transfer path: `_ESBMC_eoa_credit(addr, val)` DOES fire
// and updates `sol_eoa_balance_array`, but unbound-mode reads skip
// the map entirely.  Consequence: the round-trip "credit then read"
// that works under `--bound` is broken under `--unbound`, even
// though the model contains the data.
//
// Fix direction: route unbound-mode `addr.balance` reads through
// `_ESBMC_eoa_balance_of` unconditionally — the helper's
// `_ESBMC_eoa_get_or_init` already over-approximates first-sight
// addresses with a nondet initial value, which is the same
// soundness stance as the current short-circuit.  Once the
// routing is uniform, the credit is observable and the assertion
// below holds, flipping to VERIFICATION SUCCESSFUL.
contract C {
    function credit(address payable eoa) public {
        eoa.transfer(10);
    }

    function test(address payable eoa) public {
        require(eoa != address(0));
        require(eoa != address(this));
        // Pin the EOA's starting balance.  Under unbound mode this
        // pinning constraint is on a fresh nondet; the later read
        // creates ANOTHER fresh nondet so the pin is unrelated.
        require(eoa.balance == 100);
        credit(eoa);
        // Correct: eoa.balance reflects the +10 credit now.
        // Current bug (unbound only): later read is fresh nondet,
        // could be anything, so `== 110` can legitimately fail.
        assert(eoa.balance == 110);
    }
}
