// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "../src/Vault.sol";
contract VaultNativeTest is Test {
    Vault v;
    function setUp() public { v = new Vault(); }
    function test_small_withdraw() public { v.withdraw(50); }   // amount>1000 FALSE only
    function test_positive_adjust() public { v.adjust(5); }     // d<0 FALSE only
}
