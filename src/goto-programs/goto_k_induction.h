#ifndef GOTO_PROGRAMS_GOTO_K_INDUCTION_H_
#define GOTO_PROGRAMS_GOTO_K_INDUCTION_H_

#include <goto-programs/goto_functions.h>
#include <goto-programs/goto_loops.h>
#include <util/guard.h>
#include <irep2/irep2_expr.h>
#include <optional>
#include <set>
#include <vector>

void goto_k_induction(goto_functionst &goto_functions);

void goto_termination(goto_functionst &goto_functions);

class goto_k_inductiont : public goto_loopst
{
public:
  goto_k_inductiont(
    const irep_idt &_function_name,
    goto_functionst &_goto_functions,
    goto_functiont &_goto_function)
    : goto_loopst(_function_name, _goto_functions, _goto_function)
  {
    if (function_loops.size())
      goto_k_induction();
  }

protected:
  typedef std::unordered_map<unsigned, bool> marked_branchst;
  marked_branchst marked_branch;

  typedef std::unordered_map<unsigned, guardt> guardst;
  guardst guards;

  void goto_k_induction();

  void convert_finite_loop(loopst &loop);

  /// Return true iff `loop` is a canonical counted-for-loop whose trip
  /// count BMC unrolling can capture precisely, so the k-induction
  /// havoc-step-once transform would only weaken precision.  Pattern:
  ///
  ///   ASSIGN var = k0                  (k0 a non-negative int constant)
  /// loop_head: IF !(var < N) GOTO past  (or var <=, with N a constant
  ///                                      or a loop-invariant symbol
  ///                                      expression — i.e., not in
  ///                                      modified_loop_vars)
  ///   ... body that does not reassign var ...
  ///   ASSIGN var = var + step          (immediately before the
  ///                                      backwards GOTO; step is a
  ///                                      positive int constant)
  /// loop_exit: GOTO loop_head
  ///
  /// For literal bounds the iteration count must be in (0, threshold];
  /// for symbolic loop-invariant bounds we always say yes (BMC will
  /// unroll up to --unwind, which dominates havoc-step-once for
  /// deterministic per-element bodies).  Returning false leaves the
  /// existing k-induction transform intact.
  bool is_counted_for_loop(const loopst &loop, int threshold) const;

  /// Variant of `is_counted_for_loop` that requires a symbolic
  /// loop-invariant bound (rejects literal-constant bounds even when
  /// they are under the threshold).  Used by the pure-local-writer
  /// counted-loop skip in `goto_k_induction()`: literal-bounded loops
  /// in pure-local-writer library helpers are kept k-inductized because
  /// some of them (e.g. `bytes_static_from_hex`'s
  /// `i < _ESBMC_BYTES_STATIC_MAX`) rely on the havoc-step-once over-
  /// approximation to close inductive proofs at unwind values smaller
  /// than the literal bound.
  bool is_counted_for_loop_with_symbolic_bound(const loopst &loop) const;

  bool get_entry_cond_rec(
    const goto_programt::targett &loop_head,
    const goto_programt::targett &after_exit,
    guardst &guards);

  void
  make_nondet_assign(goto_programt::targett &loop_head, const loopst &loop);

  /// For a struct-typed lhs that the per-field havoc emit is about to
  /// nondet, return the precise set of immediate field names that the
  /// loop body actually writes to lhs — or std::nullopt to signal
  /// "fall back to all-non-pointer-fields havoc" (current behavior).
  ///
  /// Returns nullopt under any of:
  ///  - whole-struct write `lhs = ...` somewhere in the body;
  ///  - any deref-write `*p = ...` in the body (could alias lhs.field);
  ///  - any FUNCTION_CALL whose callee writes through a pointer
  ///    parameter AND whose actuals reference `lhs` (could write any
  ///    field via the param);
  ///  - function-pointer call (no callee identity).
  ///
  /// If the scan finds zero direct member-writes to lhs and no
  /// indirect-write triggers fired, returns an empty set; the caller
  /// must treat empty as "fallback to all-fields" (preserves current
  /// behavior whenever our local scan is too narrow to confirm a
  /// specific field subset).
  std::optional<std::set<irep_idt>>
  collect_modified_struct_fields(const loopst &loop, const expr2tc &lhs_sym);

  /// Auto-infer entry-dominator invariants from loop-body assertions.
  ///
  /// Walks instructions in source order from \p scan_begin (exclusive of the
  /// loop-condition GOTO) up to \p scan_end and collects each ASSERT P whose
  /// guard variables have not been the target of any prior ASSIGN in that
  /// prefix.  These are candidate inductive hypotheses: "P held when the loop
  /// body was entered."
  ///
  /// Sound because the inductive-step ASSUME we emit is tagged
  /// inductive_step_instruction=true (skipped in base/forward phases).  If P
  /// is not a real invariant the base case still witnesses the violation.
  ///
  /// FUNCTION_CALL is followed (recursing into the callee body) up to
  /// kInvariantInferenceMaxDepth so that the harness pattern
  /// `_ESBMC_Main_<C>` → `_ESBMC_Nondet_Extcall_<C>` → user method can be
  /// reached.  Recursion breaks on a recursive callee or a backwards GOTO
  /// (nested loop).
  void infer_entry_invariants(
    goto_programt::const_targett scan_begin,
    goto_programt::const_targett scan_end,
    std::vector<expr2tc> &assumptions) const;

  void infer_entry_invariants_rec(
    goto_programt::const_targett scan_begin,
    goto_programt::const_targett scan_end,
    std::set<irep_idt> &modified_so_far,
    std::vector<expr2tc> &assumptions,
    std::set<irep_idt> &visited_funcs,
    int depth) const;

  void remove_unrelated_loop_cond(guardst &guards, loopst &loop);

  void assume_loop_entry_cond_before_loop(
    goto_programt::targett &loop_head,
    goto_programt::targett &loop_exit,
    const guardst &guard);

  void adjust_loop_head_and_exit(
    goto_programt::targett &loop_head,
    goto_programt::targett &loop_exit);

  void
  assume_cond(const expr2tc &cond, goto_programt &dest, const locationt &loc);
};

#endif /* GOTO_PROGRAMS_GOTO_K_INDUCTION_H_ */
