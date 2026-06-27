// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

// CORRECT-form InvMut differential of CEI reentrancy mutant M1, written the
// natural way: the harness receive() routes each reentrant callback by
// `msg.sender == address(p)` / `address(m)` (no workaround flag). One
// sync-wrapper per public/external function; the single differential assertion
// `assert(snap_p == snap_m)` lives only in the boundary wrapper s3_withdraw.
//
// KNOWNBUG: ESBMC reports VERIFICATION SUCCESSFUL (misses M1) because of the
// instance-vs-singleton $address duality. In the reentrant callback ESBMC sets
// msg.sender to the dispatch-SINGLETON `_ESBMC_Object_C_ref.$address`, while
// `address(p)` reads the `new C_ref()` HEAP-INSTANCE `$address` — they differ,
// so both `if` branches are false, neither snapshot is taken, and 0 == 0 holds.
// On a real EVM these are the same address and the assertion would FAIL (detect
// M1). When the duality is fixed this flips to VERIFICATION FAILED -> CORE.
// The flag-routed dual diff_reentrancy_M1_cei_observer_{fail,pass} is the
// working (CORE) detector in the meantime.

contract C_ref {
    address public owner = msg.sender;
    uint256 public rewardRate = 100;
    mapping(address => uint256) public balance;
    mapping(address => uint256) public reward;
    mapping(address => uint256) public rateSnapshot;
    function deposit() external payable { balance[msg.sender] += msg.value; rateSnapshot[msg.sender] = rewardRate; }
    function setRewardRate(uint256 r) external { require(msg.sender == owner); rewardRate = r; }
    function claim() external returns (uint256 a) { a = balance[msg.sender] * rateSnapshot[msg.sender]; reward[msg.sender] += a; }
    function withdraw() external {
        uint256 a = balance[msg.sender];
        require(a > 0);
        balance[msg.sender] = 0;                            // CEI-correct
        (bool ok,) = msg.sender.call{value: a}(""); require(ok);
    }
}

contract C_mut {
    address public owner = msg.sender;
    uint256 public rewardRate = 100;
    mapping(address => uint256) public balance;
    mapping(address => uint256) public reward;
    mapping(address => uint256) public rateSnapshot;
    function deposit() external payable { balance[msg.sender] += msg.value; rateSnapshot[msg.sender] = rewardRate; }
    function setRewardRate(uint256 r) external { require(msg.sender == owner); rewardRate = r; }
    function claim() external returns (uint256 a) { a = balance[msg.sender] * rateSnapshot[msg.sender]; reward[msg.sender] += a; }
    function withdraw() external {
        uint256 a = balance[msg.sender];
        require(a > 0);
        (bool ok,) = msg.sender.call{value: a}(""); require(ok);  // M1: call BEFORE zero
        balance[msg.sender] = 0;
    }
}

// differential verification — one sync-wrapper per public/external function;
// the single differential assertion lives only in the boundary wrapper s3_withdraw.
// Callback routed by the NATURAL `msg.sender == address(p)` (no workaround flag).
contract Harness {
    C_ref p = new C_ref(); C_mut m = new C_mut();
    uint256 snap_p; uint256 snap_m;
    constructor() { require(address(p).balance == address(m).balance); }
    function __ESBMC_reverted() internal returns (bool) {}
    function __ESBMC_assume(bool) internal pure {}

    receive() external payable {
        if (msg.sender == address(p))      snap_p = p.balance(address(this));
        else if (msg.sender == address(m)) snap_m = m.balance(address(this));
    }

    function s0_deposit(uint256 __v) public payable {
        p.deposit{value: __v}();
        m.deposit{value: __v}();
    }
    function s1_setRewardRate(uint256 a0) public {
        p.setRewardRate(a0);
        m.setRewardRate(a0);
    }
    function s2_claim() public {
        p.claim();
        m.claim();
    }
    function s3_withdraw() public {
        p.withdraw();
        m.withdraw();
        assert(snap_p == snap_m);   // single differential assertion at boundary tb = withdraw
    }
}
