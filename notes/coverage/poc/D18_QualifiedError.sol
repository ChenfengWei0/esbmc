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

library L18 {
    error LibSaysNo(uint256 got);
}

contract D18_ErrHolder {
    error HolderSaysNo(uint256 got);
}

// ---------------- constructor row: the cell predicted to be LIVE ----------

contract D18_CtorPlain {
    error LocalSaysNo(uint256 got);

    uint256 public v;

    constructor(uint256 x) {
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

contract D18_CtorLibQual {
    uint256 public v;

    constructor(uint256 x) {
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

contract D18_CtorContractQual {
    uint256 public v;

    constructor(uint256 x) {
        if (x == 0) {
            revert D18_ErrHolder.HolderSaysNo(x);
        }
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
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
