// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// When a library body performs an external call (here `t.ping()`
// via a typed contract-to-contract call from `Lib.nudge`), real
// Solidity semantics require the callee to observe
// `msg.sender == address(A)` — the enclosing CONTRACT that invoked
// the library, not the library itself and not the original caller
// of A.  ESBMC cannot pin A statically because any contract could
// call the same library, so the high-level-call wrapper emits a
// nondet uint160 for msg.sender at library call sites.  This is an
// OVER-approximation [APPROX: OVER] documented in
// src/solidity-frontend/solidity_convert_contract.cpp
// (`get_high_level_call_wrapper`, is_library branch).
//
// Regression behavior: the assertion
// `t.lastSender() == address(a)` is refutable because the nondet
// sender can legally be any address — including ones other than A.
// Without this pin, a future regression to "use Lib's dummy
// `this->$address`" (garbage) would silently flip the verdict.
contract Target {
    address public lastSender;
    function ping() public { lastSender = msg.sender; }
}

library Lib {
    function nudge(Target t) internal {
        t.ping();
    }
}

contract A {
    Target public target;
    function setTarget(Target t) public { target = t; }
    function poke() public { Lib.nudge(target); }
}

contract Harness {
    function test() public {
        Target t = new Target();
        A a = new A();
        a.setTarget(t);
        a.poke();
        // Sound-overapprox gate: the nondet sender can legally equal
        // OR differ from address(a); the equality cannot be proved.
        assert(t.lastSender() == address(a));
    }
}
