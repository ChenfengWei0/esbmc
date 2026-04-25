#ifndef GOTO_PROGRAMS_GOTO_K_INDUCTION_H_
#define GOTO_PROGRAMS_GOTO_K_INDUCTION_H_

#include <goto-programs/goto_functions.h>
#include <goto-programs/goto_loops.h>
#include <util/guard.h>
#include <irep2/irep2_expr.h>
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

  bool get_entry_cond_rec(
    const goto_programt::targett &loop_head,
    const goto_programt::targett &after_exit,
    guardst &guards);

  void
  make_nondet_assign(goto_programt::targett &loop_head, const loopst &loop);

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
