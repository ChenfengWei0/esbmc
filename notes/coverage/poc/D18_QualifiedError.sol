// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// A QUALIFIED CUSTOM-ERROR REVERT VANISHES IN CONSTRUCTOR SCOPE.
//
// THE MECHANISM, read out of the source rather than guessed.
//
// `solidity_convert_expr.cpp:688-695` turns a MemberAccess call into
// `code_skipt()` when the referenced declaration is an `EventDefinition` OR an
// `ErrorDefinition`. The unqualified spelling `revert Err(x)` never reaches that
// line -- it goes to `expr.cpp:2982-3008` and calls the `#sol_error` function
// whose body is `__ESBMC_assume(false)`, and THAT is what makes the path go
// away.
//
// Path coverage does not leave this to chance in general: `--solidity-path-
// coverage` sets `solidity-path-coverage-enabled` (esbmc_parseoptions.cpp:955),
// which is the third disjunct of the single assignment to
// `uses_revert_observation` (solidity_convert.cpp:225-231), so the flag is TRUE
// on every run of this pipeline. With it true, `solidity_convert_stmt.cpp:
// 1203-1210` builds a rollback block and NEVER CONVERTS `errorCall` at all --
// both spellings become the same thing and the drop is unreachable.
//
// EXCEPT THAT THE ROLLBACK BUILDER CAN DECLINE, AND THEN STMT FALLS THROUGH TO
// LINE 1213, which is `get_expr(stmt["errorCall"])` -- the drop. It declines in
// exactly the scope where a constructor sits: `solidity_convert_modifier.cpp:206`
// sets `current_function_revert_observable = !is_event_or_err && !is_ctor`, and a
// constructor is not an external entry so it has no `_sol_save_this` snapshot
// either, so both terms of the bail-out at :952-955 hold.
//
// SO THE DEFECT IS SCOPED, AND THE SCOPE IS THE WHOLE FINDING. That is why this
// file crosses TWO dimensions rather than one:
//
//     WHERE the revert is  :  constructor / external function
//     HOW the error is named:  unqualified / library-qualified / contract-qualified
//
// A file that tested only the function row -- which is what the first version of
// this file did -- would have reported "the two spellings agree" and been read as
// evidence that something downstream compensates. It would have been a true
// observation of the cell that cannot fail.
//
// WHY IT PRODUCES A RED TEST RATHER THAN A MISSING PATH. Nothing downstream sees
// it. The exit census recognises three revert artifacts -- a `#sol_error` call,
// a revert mark, a rollback restore (`goto_coverage.cpp:5769-5782`) -- and a
// dropped qualified error produces NONE of them. But a constructor does get an
// epilogue, so `saw_epilogue` is set, so the exit lands in neither
// `rollback_exits` nor `undetermined_exits` and is classified NORMAL. It is then
// filed in `normal_exit_paths` and the emitter renders a case asserting the
// DEPLOYMENT SUCCEEDS. On chain that deployment reverts.
//
// The undetermined census cannot catch it either: that census counts MISSING
// evidence, and this path has affirmative evidence that happens to be wrong.
//
// ---- EXPECTED, WRITTEN BEFORE RUNNING ----
//
// Run each contract separately:
//   esbmc D18_QualifiedError.solast --sol D18_QualifiedError.sol \
//         --contract <name> --solidity-path-coverage --cov-report-json \
//         --solidity-max-tx 1
//
//   contract              predicted exits   predicted kinds
//   D18_CtorPlain         3                 1 revert, 2 normal
//   D18_CtorLibQual       4                 0 revert, 4 normal   <- the defect
//   D18_CtorContractQual  4                 0 revert, 4 normal   <- other route
//   D18_FnPlain           3                 1 revert, 2 normal   } must be
//   D18_FnLibQual         3                 1 revert, 2 normal   } IDENTICAL
//
// TWO SYMPTOMS IN ONE PAIR, and the second matters more. The count is too high
// by one because the `x == 0` arm has no terminator, so the DFS runs on into the
// second `if` and forks again -- that is what the second `if` is FOR. And the
// exit kind is wrong, which is the half that ships a red test. A reader checks
// the count; the count being wrong too is luck, not design.
//
// THE FUNCTION ROW IS THE NEGATIVE CONTROL AND IT MUST SHOW NO DIFFERENCE.
// If `D18_FnLibQual` also diverges from `D18_FnPlain`, then the operative factor
// is not the constructor scope and the reading above is wrong -- the drop would
// be unconditional and the rollback analysis a red herring.
//
// `D18_CtorContractQual` is separate from `D18_CtorLibQual` because a contract
// qualifier reaches `expr.cpp:693` through a DIFFERENT ExpressionT
// (TypeMemberCall via `solidity_grammar.cpp:956-957`, versus LibraryMemberCall
// via :942-945). "Qualified" and "library" are two properties and only one of
// them is under test.
//
// ---- MEASURED 2026-08-01. THE PREDICTION IS REFUTED, WITH A LIVE CONTROL ----
//
//   contract                 paths   F   U   `v == 0` arm
//   D18_CtorNoRevert (ctrl)    3     3   0   WITNESSED        <- control fires
//   D18_CtorPlain              3     2   1   bounded-holds
//   D18_CtorLibQual            3     2   1   bounded-holds
//   D18_CtorContractQual       3     2   1   bounded-holds
//   D18_FnPlain                4     4   0   (function row)
//   D18_FnLibQual              4     4   0   IDENTICAL to FnPlain
//
// The qualified revert PRUNES exactly like the unqualified one, in constructor
// scope as well as function scope. Library-qualified and contract-qualified are
// indistinguishable from plain.
//
// The control is the whole reason that sentence is allowed. `D18_CtorNoRevert`
// has no revert and `v = x`, so `v == 0` is reachable by deploying with x = 0 --
// and it IS witnessed, both arms, 3/3 F. So "not witnessed" in the three revert
// rows is a result and not a blind spot.
//
// WHAT IS NOT CLAIMED: that the source reading is wrong. The drop at
// expr.cpp:688-695 is real and so is stmt.cpp's fall-through at :1213. What is
// refuted is the BEHAVIOURAL CONSEQUENCE -- something between them prevents it.
// Named but unchecked candidates: `get_func_modifier`'s re-scoping of a
// constructor body, and whatever lowering path coverage applies to constructors.
// Anyone acting on the source reading has to re-establish that first.
//
// TWO EARLIER VERSIONS OF THIS FILE WERE WRONG, kept here rather than quietly
// replaced, because each was wrong in a way that would have produced a confident
// answer:
//   v1 put every function in `external` scope -- the one cell the reading itself
//      predicts CANNOT fail. It would have reported "the spellings agree".
//   v2 gave the constructor contracts no public function at all, so all three
//      reported `paths_total 0`. A constructor is not a unit and cannot be
//      enumerated directly; it has to be observed THROUGH a unit that reads what
//      it wrote.
//
// ---- A SEPARATE DEFECT FELL OUT OF THE SAME RUNS ----
//
// Every run, INCLUDING the control, reports `final_state` `v: '0'` -- even on the
// path whose own decision proves `v != 0` (the `v == 0/taken` arm sets tag = 2).
// That is the D09_ValueGate shape on the STATE channel: the payload is harvested
// from the declaration-time write rather than the last write before the unit is
// entered. D09's fix was for `env`. Tracked separately; `final_state` is what the
// R1/R2 ladder is built from, so a wrong value there is wrong CONTENT.

library L18 {
    error LibSaysNo(uint256 got);
}

contract D18_ErrHolder {
    error HolderSaysNo(uint256 got);
}

// ---------------- constructor row: the cell predicted to be LIVE ----------

// A CONSTRUCTOR IS NOT A UNIT, so its paths cannot be enumerated directly --
// MEASURED: the first version of this row gave all three contracts NO public
// function, and all three reported `paths_total 0`. The constructor has to be
// observed THROUGH a unit that reads what it wrote.
//
// `v` is written ONLY after the revert. If the qualified revert still prunes,
// `x == 0` is dead, so `v` is 1 or 2 at every entry and `probe`'s `v == 0` arm
// is UNREACHABLE. If the revert was dropped, deployment continues with x == 0,
// `v` keeps its default 0, and that arm becomes REACHABLE.
//
// So the discriminator is one bit, on one arm, and it needs no path arithmetic:
//   Plain    -> `v == 0` NOT witnessed
//   LibQual  -> `v == 0` witnessed  == the defect

// POSITIVE CONTROL FOR THE WHOLE CONSTRUCTOR ROW. No revert at all, and `v = x`,
// so `v == 0` is reachable by deploying with x == 0. If THIS contract also fails
// to witness the `v == 0` arm, the discriminator cannot fire and NO row of this
// row means anything -- the same trap the first D20 run fell into.
contract D18_CtorNoRevert {
    uint256 public v;
    uint256 public tag;

    constructor(uint256 x) {
        v = x;
    }

    function probe() external {
        if (v == 0) {
            tag = 1;
        } else {
            tag = 2;
        }
    }
}

contract D18_CtorPlain {
    error LocalSaysNo(uint256 got);

    uint256 public v;
    uint256 public tag;

    constructor(uint256 x) {
        if (x == 0) {
            revert LocalSaysNo(x);
        }
        v = x > 10 ? 1 : 2;
    }

    function probe() external {
        if (v == 0) {
            tag = 1;
        } else {
            tag = 2;
        }
    }
}

contract D18_CtorLibQual {
    uint256 public v;
    uint256 public tag;

    constructor(uint256 x) {
        if (x == 0) {
            revert L18.LibSaysNo(x);
        }
        v = x > 10 ? 1 : 2;
    }

    function probe() external {
        if (v == 0) {
            tag = 1;
        } else {
            tag = 2;
        }
    }
}

contract D18_CtorContractQual {
    uint256 public v;
    uint256 public tag;

    constructor(uint256 x) {
        if (x == 0) {
            revert D18_ErrHolder.HolderSaysNo(x);
        }
        v = x > 10 ? 1 : 2;
    }

    function probe() external {
        if (v == 0) {
            tag = 1;
        } else {
            tag = 2;
        }
    }
}

// ---------------- function row: the negative control, predicted DEAD ------

contract D18_FnPlain {
    error LocalSaysNo(uint256 got);

    uint256 public v;

    function run(uint256 x) external {
        if (x == 0) {
            revert LocalSaysNo(x);
        }
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }
}

contract D18_FnLibQual {
    uint256 public v;

    function run(uint256 x) external {
        if (x == 0) {
            revert L18.LibSaysNo(x);
        }
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }
}
