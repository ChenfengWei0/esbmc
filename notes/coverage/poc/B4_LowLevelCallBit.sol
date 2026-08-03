// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ISOLATES EXACTLY ONE THING: the SHAPE the external call's success bit arrives
// in. Nothing else in this file differs between the two units.
//
// ---- WHY A SECOND EXTCALL PoC EXISTS AT ALL --------------------------------
//
// B2_ExtcallSuccess.sol already isolates one shape and its drop site is
// MEASURED: `harness_nondets_dropped` goes 23 -> 24 on exactly the two paths
// that execute the assignment, so the value reaches the counterexample harvest
// and is then classified into the drop bucket. That is shape (a) of the three
// the cov-report's own `extcall_returns_unavailable_reason` distinguishes:
//
//   (a) bound to a named local, or assigned inside an approximated assembly
//       block -- the value IS harvested and resolved, then dropped because the
//       classification has buckets only for parameters and environment values.
//   (b) low-level `(bool ok, ) = a.call(...)` -- get_nondet_symbol returns nil,
//       so the step is SKIPPED BEFORE the classification ever runs.
//   (c) used inline, `if (c.f())` -- no named local is assigned at all.
//
// AND THE CORPUS USES (b), NOT (a). Measured on farming::deposit: its six
// non-gate witnessed paths are three pairs, and every pair differs on exactly
// one decision -- `!success` vs `!(!success)` in SafeERC20's safeTransferFrom,
// which is `(bool success, ) = address(token).call(data)`. So a repair built
// only at the (a) site would have moved B2 from 0 to 2 certified, moved the
// corpus by nothing, and looked like a success. This file exists so (b) has a
// twelve-line reproduction of its own before any code is written.
//
// ---- WRITTEN BEFORE THE RUN. THE PREDICTIONS ARE THE POINT. ----------------
//
// The two shapes make DIFFERENT predictions about ESBMC's own counter, which is
// what makes this a discriminator rather than a demonstration:
//
//   shape (a), already measured on B2: the step reaches the classification and
//       falls off its end into `++ce.dropped_internal`, so the counter is
//       STRICTLY HIGHER on the paths that execute the assignment (23 -> 24).
//
//   shape (b), predicted here: bmc.cpp skips the step with a bare `continue`
//       BEFORE any bucket is chosen, and that `continue` does NOT increment
//       anything. So `harness_nondets_dropped` must be EQUAL on the ok-true and
//       ok-false paths of probeLowLevel, and the `ce step:` diagnostic line for
//       that symbol must print nondet='-'.
//
//   ⛔ IF THE COUNTER GOES UP BY ONE ON probeLowLevel TOO, then (b) is really
//      (a), the report's reason text is wrong, and the single repair at the
//      classification site would in fact cover the corpus. That is a result,
//      not a failure, and it must be reported rather than explained away.
//
// ---- THE DISCRIMINATOR MUST BE SEEN TO FIRE FIRST --------------------------
//
// ctrlBool is IDENTICAL in body to probeLowLevel and differs only in where the
// bool comes from: it is a declared PARAMETER. The driver is already known to
// resolve it -- commit 0601b4b5ae measured `ok in [1,1]` and `ok in [0,0]` as
// certified regions on the B2 twin of this unit, i.e. 0 certified -> 2
// certified purely from the bool being visible.
//
// So ctrlBool MUST run FIRST, and it MUST show `ok` inside `inputs` on both
// arms. If it does not, the diagnostic channel is off or spelled differently
// and probeLowLevel's silence would mean NOTHING -- the run is inconclusive,
// not evidence for (b).
//
// ---- WHAT THIS FILE DELIBERATELY DOES NOT TEST -----------------------------
//
// Shape (c) (`if (target.call(""))` inline, no named local). It needs no bucket
// because there is no trace step at all, and fixing it is a different change at
// a different layer. One PoC, one thing.

contract B4_LowLevelCallBit {
    uint256 public tag;

    // THE DISCRIMINATOR. The bool is a PARAMETER, so it is in `inputs` by the
    // rule the harvest already implements, and the region over it is already
    // known to certify.
    function ctrlBool(uint256 amount, bool ok) external {
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    // THE QUESTION. Same body, same branch, same writes. The ONLY difference is
    // that `ok` is the first component of a low-level call's tuple return
    // rather than a parameter -- shape (b), the one the corpus uses.
    function probeLowLevel(address target, uint256 amount) external {
        (bool ok, ) = target.call("");
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }
}
