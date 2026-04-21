// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG: Library transfers don't debit the caller contract's
// $balance.  Real Solidity: `Lib.sendAll(...)` inside A.f() runs in
// A's context, so A.$balance -= _val should fire alongside
// recipient.$balance += _val.  Our model skips the debit because
// library bodies have no access to A's $balance slot (the enclosing
// contract is run-time data).  Once the proper fix is in place
// (threading A's `this` through library invocations), the assertion
// below holds (A.$balance must equal before - 10) and the test flips
// to VERIFICATION SUCCESSFUL, at which point testing_tool screams
// "reclassify as CORE".
contract Target {
    receive() external payable {}
}

library Pay {
    function send(address payable to, uint256 val) internal {
        to.transfer(val);
    }
}

contract A {
    uint256 public snap_before;
    uint256 public snap_after;

    function run(Target t) public {
        snap_before = address(this).balance;
        Pay.send(payable(address(t)), 10);
        snap_after = address(this).balance;
    }
}

contract Harness {
    function test() public {
        Target t = new Target();
        A a = new A();
        // Pin A's starting $balance so we can reason about the delta.
        require(address(a).balance == 100);
        a.run(t);
        // Correct behaviour: A.$balance decreased by exactly 10.
        // Current (bug): the debit is skipped in library scope, so
        // snap_after == snap_before == 100.
        assert(a.snap_before() - a.snap_after() == 10);
    }
}
