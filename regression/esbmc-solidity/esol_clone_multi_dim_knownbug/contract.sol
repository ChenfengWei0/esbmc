// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
// KNOWNBUG: multi-dim fixed array `uint256[M][N]` post-clone read
// still fails AFTER Phase 2 + Phase 1.  Investigation log:
//
// What Phase 1+2 does correctly (confirmed in the goto dump):
//   - ctor emits `this->grid = alloc_array(3, 8)` (outer) followed by
//     `this->grid[i] = alloc_array(2, 32)` for i=0..2 (inner rows).
//   - base round-trip `base.setAt(0,0,a); base.get(0,0)==a` PASSES.
//   - clone helper emits `*c = *base` then `c->grid = alloc_array(3,
//     8)` (fresh outer) then `c->grid[i] = arrcpy(base->grid[i], 2,
//     32)` for each i.  All three arrcpy calls fire.
//
// What still fails:
//   `assert(clone.get(0,0) == a)` — the read resolves to something
//   that solver cannot pin to `a` even though the symbolic arrcpy
//   should have copied base's inner rows byte-for-byte.
//
// Raw-C/C++ repros of the EXACT same shape PASS under both bitwuzla
// and CVC5.  Three variants live under repro_raw/:
//   - `raw_u256_c.c` (C99, MALLOC + ctor(base) + direct struct ASSIGN)
//   - `raw_u256_cpp.cpp` (C++, cpp_new + ctor(base) + operator=)
//   - `raw_u256_cpp_sol_pattern.cpp` (C++, cpp_new + ctor(&tmp) +
//     direct struct ASSIGN `*new_ptr = tmp`, i.e. byte-identical to
//     the Solidity frontend's emission of `C base = new C()` — still
//     PASSES).
//
// Disproven hypotheses (2026-04-20 session, tracked here so the same
// dead-ends are not retried):
//   - **NOT** operator= vs direct struct ASSIGN: raw-C uses direct
//     ASSIGN too and passes.
//   - **NOT** the cpp_new + temp-object + struct-copy pattern itself:
//     `raw_u256_cpp_sol_pattern.cpp` reproduces the pattern exactly
//     (including the stack `tmp` + `ctor(&tmp)` + `*new_ptr = tmp`
//     sequence, with no explicit `ctor(base)` on the heap pointer)
//     and still PASSES.
//   - **NOT** the `_ExtInt(96)`/`_ExtInt(160)`/`_ExtInt(192)` anon-pad
//     layout of the Solidity contract struct: the raw-C++ sol-pattern
//     repro mirrors it and passes.
//
// Remaining suspects (unverified, would need symex value-set tracing):
//   - Interaction between __ESBMC_main's global `_ESBMC_Object_C`
//     (allocated once via the same cpp_new + ctor(&tmp) + struct-copy
//     pattern) and check()'s later `new C()`, through shared type-tag
//     or shared ctor symbol identity.
//   - Something in how the contract ctor's `_sol_init_()` /
//     `this->_ESBMC_bind_cname = C` writes interact with symex's
//     struct-copy byte-tracking when the struct later flows through
//     `*new_ptr = tmp`.
//
// Compare side-by-side:
//   esbmc contract.sol --contract H --bound --unwind 3 --no-unwinding-assertions --no-standard-checks --cvc5
//     → FAILED (clone.get(0,0) != a)
//   esbmc repro_raw/raw_u256_cpp_sol_pattern.cpp --unwind 3 --no-unwinding-assertions --no-standard-checks --force-malloc-success --bitwuzla
//     → SUCCESSFUL
//
// Scope: tracked until root cause pinned. Next step would be to run
// `--show-value-set` on the Solidity test around the `base->grid[0]`
// read inside clone_c's arrcpy arg, and compare the renamed SSA symbol
// against the value-set entry that symex resolves it to.
function __ESOL_deep_copy(C src) pure returns (C) { return src; }

contract C {
    uint256[2][3] public grid;
    function setAt(uint256 i, uint256 j, uint256 v) public { grid[i][j] = v; }
    function get(uint256 i, uint256 j) public view returns (uint256) { return grid[i][j]; }
}

contract H {
    function check(uint256 a) public {
        require(a != 0);
        C base = new C();
        base.setAt(0, 0, a);
        C clone = __ESOL_deep_copy(base);
        // Deep-copied clone should see a at (0,0).  Currently fails
        // because clone's grid outer is fresh but inner rows alias
        // base's uninitialised inner pointers — which read nondet.
        assert(clone.get(0, 0) == a);
    }
}
