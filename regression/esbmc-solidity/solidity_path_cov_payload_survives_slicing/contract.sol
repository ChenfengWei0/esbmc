// Asking for the JSON report must actually produce a usable counterexample
// payload — and must do so WITHOUT switching slicing off.
//
// A path claim's guard mentions nothing but the ghost path accumulators, so the
// symex slicer — which keeps only what the claim depends on — removes every
// contract-state write and every environment read. The report then came back
// with empty `inputs`/`env`/`final_state`: an interface whose entire purpose is
// those values, silently delivering none of them.
//
// The fix is NOT --no-slice: that also keeps every c2goto crypto/ABI table and
// the whole address allocator in the formula (measured on this contract: 172
// assignments reach the solver instead of 87). Instead `--cov-report-json`
// registers exactly the symbols the harvest reads — the contract instance
// object, contract-scope mapping/array stores, and msg./tx./block. — in
// ESBMC's existing no_slice_names exemption, and says so on stdout rather than
// changing a flag behind the user's back.
//
// This test pins that message AND that real slicing still ran. The removal
// count is matched as "at least two digits" rather than the exact 94: the exact
// number moves whenever the c2goto library or the frontend grows, which would
// produce confusing red runs for changes that have nothing to do with this
// feature. Two digits is still decisive for what the test is FOR — reverting to
// --no-slice drops the count to 9, a single digit, and the exemption line above
// disappears at the same time.
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
