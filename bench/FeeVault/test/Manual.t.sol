// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// manual: true
//
// T1. HAND-WRITTEN, and it must never be counted in a conversion rate (R7).
// Its only job is to make the deliverable concrete: this is the shape the
// pipeline has to produce by itself in T3.
//
// Target path of `withdraw`: the amount is accepted, a discount APPLIES, and the
// fee stays UNDER the cap. That is the one path on which the paper's M2 fault is
// visible.

import "forge-std/Test.sol";
import "../src/FeeVault.sol";

contract ManualTest is Test {
    FeeVault v;
    address alice = address(0xA11CE);

    function setUp() public {
        v = new FeeVault(); // the test contract deploys, so it is `owner`
    }

    function test_discountLowersFee(uint256 amount, uint16 disc) public {
        // The region. Both coordinates have WIDTH > 1, which is what separates a
        // PUT from a concrete replay test.
        amount = bound(amount, 1 ether, 3 ether);
        disc = uint16(bound(uint256(disc), 1, 249));

        // Why these two bounds pin the path:
        //   disc >= 1        forces `discountBps[alice] > 0`, so the first
        //                    conditional is ENTERED.
        //   amount <= 3e18   with rate = 250 - disc <= 249, the fee is at most
        //                    3e18 * 249 / 10000 = 7.47e16 < 1e17 = maxFee, so
        //                    the second conditional is NOT entered.
        v.setDiscount(alice, disc);

        vm.deal(alice, amount);
        vm.startPrank(alice);
        v.deposit{value: amount}();
        uint256 net = v.withdraw(amount);
        vm.stopPrank();

        uint256 rate = uint256(v.feeBps()) - uint256(disc);
        uint256 fee = amount * rate / 10000;

        // Oracle over the RETURN VALUE.
        assertEq(net, amount - fee, "net must be the amount less the discounted fee");
        // Oracle over POST-STATE.
        assertEq(v.deposits(alice), 0, "the whole deposit must be withdrawn");
        // The path claim itself, as an assertion: the fee stayed under the cap,
        // i.e. the second conditional was not entered on ANY admitted input.
        assertLt(fee, v.maxFee(), "the region must keep the fee under the cap");
    }
}
