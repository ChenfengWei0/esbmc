// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

/// Scalar-only: both decisions are renderable (uint256 / int256), so the
/// ESBMC-generated test can reach every branch ESBMC reports.
contract Vault {
    uint256 public big;
    int256  public neg;
    function withdraw(uint256 amount) external { if (amount > 1000) big = 1; else big = 2; }
    function adjust(int256 d)         external { if (d < 0)         neg = d; else neg = 7; }
}
