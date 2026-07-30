// Reproducer #2 -- the St1inch shape, which reproducer #1 did NOT have.
//
// #1 kept everything in one function and the model was CORRECT there
// (`d1 := a - ORIGIN` stayed symbolic). The three things #1 was missing, and
// which St1inch has, are all here:
//
//   * the immutable is assigned in a BASE constructor, from an argument the
//     derived constructor passes as block.timestamp;
//   * the clamp-then-subtract lives in an INTERNAL function taking `timestamp`
//     as a PARAMETER and reassigning it;
//   * the two calls happen inside the DERIVED constructor, one second apart,
//     and their results feed a self-check that reverts.
//
// MEASURED on the real benchmark: both calls produced `t == 0` and returned
// their `balance` argument unchanged, so the self-check reverted
// unconditionally and nothing after the constructor existed
// (`Generated 0 VCC(s)`, every path U, the entry-liveness audit hard-failing).
//
// If this file reproduces that, the defect is in this shape and not in the
// 5000-line flat. If it does NOT, the shape is not sufficient and the cause is
// something else in St1inch -- which is equally worth knowing, and is why the
// two calls' results are stored rather than only compared.
pragma solidity ^0.8.0;

contract Base {
    uint256 public immutable ORIGIN;

    constructor(uint256 origin_) {
        ORIGIN = origin_;
    }

    function _at(uint256 balance, uint256 timestamp)
        internal
        view
        returns (uint256 r)
    {
        timestamp = timestamp < ORIGIN ? ORIGIN : timestamp;
        unchecked {
            uint256 t = timestamp - ORIGIN;
            r = balance;
            if (t & 0x01 != 0) {
                r = r + 1;
            }
            if (t & 0x02 != 0) {
                r = r + 2;
            }
            if (t & 0x04 != 0) {
                r = r + 4;
            }
        }
        return r;
    }
}

contract D is Base {
    uint256 public v1;
    uint256 public v2;

    error Bad();

    constructor() Base(block.timestamp) {
        v1 = _at(1000, block.timestamp + 63072000);
        v2 = _at(1000, block.timestamp + 63072001);
        // On a correct model the two differ (their `t` differ by 1 in the low
        // bit), so this is NOT taken and the contract stays alive.
        if (v1 == v2) revert Bad();
    }

    function ping(uint256 x) public {
        if (x > 5) {
            v1 = x;
        } else {
            v2 = x;
        }
    }
}
