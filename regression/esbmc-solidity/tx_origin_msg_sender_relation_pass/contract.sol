// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// T2.3 — empirical test for tx.origin == msg.sender at the top-level
// call frame. Real EVM: when a contract is called directly by an EOA,
// tx.origin equals msg.sender. ESBMC's _sol_per_tx_reseed reseeds
// msg_sender and tx_origin to fresh nondet independently, so the
// solver can pick distinct values and the assertion fails.
// Predicted FAILED today; locks in the gap as a real KNOWNBUG.
contract H {
    function check() external view {
        assert(tx.origin == msg.sender);
    }
}
