// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// DISCRIMINATOR FOR `--focus-function a,b` — three units, three DIFFERENT
/// path counts.
///
/// WHY THREE AND WHY DIFFERENT. The first attempt at this used `Tiny2`, which
/// has exactly two public functions, and the result was worthless in the one
/// direction that mattered: focusing BOTH of them and IGNORING the flag
/// entirely produce the identical output (8 paths), because "both units" and
/// "the whole contract" are the same set. A discriminator whose two outcomes
/// coincide is not a discriminator.
///
/// Here `one`, `two` and `three` have strictly increasing decision counts, so
/// every subset has its own path total and the pair `one,two` is
/// distinguishable from the whole contract by the NUMBER alone, independently
/// of whether the `narrowed INSTRUMENTATION` line is printed.
///
/// WHAT IS PINNED (each a must-flip, measured with the numbers this file
/// actually produces rather than predicted here — the point is the RELATION):
///
///   one            -> N1 paths, "narrowed ... to 1 unit(s); 2 other"
///   two            -> N2 paths, N2 > N1
///   three          -> N3 paths, N3 > N2
///   one,two        -> N1 + N2 paths, "narrowed ... to 2 unit(s); 1 other"
///   (no focus)     -> N1 + N2 + N3 paths, no narrowing line
///
/// The load-bearing check is `one,two` == N1 + N2 AND strictly less than the
/// unfocused total. If it equals the unfocused total, the second name was not
/// parsed and the run silently fell back to the whole contract — which is
/// exactly the failure the help text promises cannot happen, and which would be
/// invisible on any two-function contract.
contract F01_MultiFocus {
    uint256 public x;

    function one(uint256 a) external {
        if (a > 10) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function two(uint256 a) external {
        if (a > 10) {
            if (a > 20) {
                x = 3;
            } else {
                x = 4;
            }
        } else {
            x = 5;
        }
    }

    function three(uint256 a) external {
        require(a > 0);
        if (a > 10) {
            if (a > 20) {
                if (a > 30) {
                    x = 6;
                } else {
                    x = 7;
                }
            } else {
                x = 8;
            }
        } else {
            x = 9;
        }
    }
}
