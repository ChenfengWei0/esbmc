// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG: Library-scope `.call{value:v}(data)` to a tracked
// contract with a payable receive should return `true` on the
// success path — real Solidity only returns `false` when the callee
// reverts, runs out of gas, or otherwise fails, none of which apply
// to our abstract receive().  Our model emits `nondet_bool()` on
// every return path (conservative over-approximation of EVM
// revert reasons), so the solver is free to pick `false` even when
// the receive semantically succeeds.
//
// Fix direction: return `true` on the tracked-target success path,
// keep nondet only on the EOA fallthrough.  Once done, the test
// flips to VERIFICATION SUCCESSFUL.
contract Sink {
    uint256 public credited;
    receive() external payable { credited += msg.value; }
}

library Call {
    function pay(address payable to, uint256 v) internal returns (bool) {
        (bool ok, ) = to.call{value: v}("");
        return ok;
    }
}

contract A {
    function dispatch(Sink s) public returns (bool) {
        return Call.pay(payable(address(s)), 5);
    }
}

contract Harness {
    function test() public {
        Sink s = new Sink();
        A a = new A();
        require(address(a).balance == 100);
        bool ok = a.dispatch(s);
        // Correct: ok is true (receive succeeds, no revert reason).
        // Current bug: ok is nondet, can be false.
        assert(ok);
    }
}
