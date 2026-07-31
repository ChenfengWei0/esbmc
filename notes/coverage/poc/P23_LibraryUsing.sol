// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ISOLATES: a `library` with `using ... for`, and a LOOP inside it.
//
// Two facts make this the shape most worth having and least represented:
//
//   * Libraries are where the corpus loses decisions it can never recover: a
//     UNIT is a public/external function, library functions are `internal`, so
//     a compilation unit that is library-only enumerates ZERO units while
//     branch coverage instruments it directly. That is a SCOPE difference
//     between the two metrics, and it was measured as a whole benchmark
//     reporting N/A.
//   * The worst measured defect in this project came from a LIBRARY LOOP:
//     with `--no-simplify`, a loop that had previously been folded away was
//     actually entered, was truncated at the unwind bound, and the forced
//     `no-unwinding-assertions` ASSUMED AWAY exactly the executions that
//     witnessed the path. F went 2 to 0 with exit 0, a normal report and no
//     specific warning — the tool stating a path does not hold when it does.
//
// `Math.clampSum` is that shape, deliberately: a library function with a loop
// whose trip count comes from the caller, reached through `using for`.
//
// EXPECTED: `total` enumerates paths that include the LIBRARY's decisions,
// inlined into the caller; the library's own functions are NOT units.
//
// THE MUST-FLIP THIS ENABLES, in one second instead of on a real benchmark:
// run it with and without `--no-simplify` and compare F. If F drops, the
// interaction reproduces here and can be studied on ten lines.
library P23_Math {
    function clampSum(uint256[3] memory xs, uint256 cap)
        internal
        pure
        returns (uint256)
    {
        uint256 s = 0;
        for (uint256 i = 0; i < 3; i++) {
            s += xs[i];
            if (s > cap) {
                return cap;
            }
        }
        return s;
    }
}

contract P23_LibraryUsing {
    using P23_Math for uint256[3];

    uint256 public out;

    function total(uint256 a, uint256 b, uint256 cap) external {
        require(cap > 0);
        uint256[3] memory xs = [a, b, uint256(1)];
        out = xs.clampSum(cap);
    }
}
