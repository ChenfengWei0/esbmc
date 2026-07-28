// The ABI non-payable gate must apply to the EXTERNAL entry only.
//
// On-chain, calling `g` with a nonzero value is admitted (g is payable) and the
// internal call `f()` is a plain jump that does NOT run f's non-payable value
// check. So there is no execution in which this transaction reverts because of
// f.
//
// goto has one body per function, so while `f` served both entry kinds a
// body-level gate INVENTED that execution: measured, the model admitted a path
// through a value-carrying call to `g` entering `f`, taking the value-reject
// edge and classifying f's path as a revert. That is reachable-and-wrong -- a
// replay-breaking test, not merely a missing path.
//
// Expansion fixes it at the root rather than by weakening the gate: `g` holds
// its own gate-free COPY of f's body, while f's own body -- now reachable only
// through the dispatcher, which IS the external entry -- keeps the gate. Both
// entry kinds are right at the same time.
//
//   f -- no decisions in the body, + gate            = 2 paths (1 normal, 1 revert)
//   g -- payable (no gate), f expanded (no decisions) = 1 path  (normal)
//   total 3 across 2 units, revert 1.
//
// If the gate ever leaks into the inlined copy again, g gains a second path and
// the revert count goes to 2.
pragma solidity ^0.8.0;

contract Q {
    uint256 public x;

    function f() public {
        x = 1;
    }

    function g() public payable {
        f();
        x = 2;
    }
}
