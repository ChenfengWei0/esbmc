#ifndef GOTO_PROGRAMS_GOTO_LOOPS_H_
#define GOTO_PROGRAMS_GOTO_LOOPS_H_

#include <goto-programs/goto_functions.h>
#include <goto-programs/loopst.h>
#include <unordered_set>
#include <util/std_types.h>

class goto_loopst
{
protected:
  irep_idt function_name;
  goto_functionst &goto_functions;
  goto_functiont &goto_function;

  typedef std::list<loopst> function_loopst;
  function_loopst function_loops;

  void create_function_loop(
    goto_programt::instructionst::iterator loop_head,
    goto_programt::instructionst::iterator loop_exit);

  void get_modified_variables(
    goto_programt::instructionst::iterator instruction,
    function_loopst::iterator loop,
    std::vector<irep_idt> &function_name);

  void add_modified_var(loopst &loop, const expr2tc &expr);
  void add_unmodified_var(loopst &loop, const expr2tc &expr);

  void add_loop_var(loopst &loop, const expr2tc &expr, bool is_modified);

  /// Walk an expression (typically a FUNCTION_CALL argument) for
  /// `address_of` sub-expressions; for each, resolve to the base
  /// storage symbol and add it to the loop's modified-vars set.
  /// Used to propagate caller-side writes through pointer-passed
  /// arguments into the k-induction havoc preamble.
  void collect_addressof_targets(loopst &loop, const expr2tc &expr);

  /// Scan the named callee (and transitively called functions) for
  /// any ASSIGN whose lhs mentions a dereference — a proxy for "this
  /// function writes through some pointer parameter". Gates whether
  /// `collect_addressof_targets` is applied to the call's actuals.
  bool callee_writes_through_pointer(
    const irep_idt &callee,
    std::unordered_set<irep_idt, irep_id_hash> &visited);

public:
  goto_loopst(
    const irep_idt &_function_name,
    goto_functionst &_goto_functions,
    goto_functiont &_goto_function)
    : function_name(_function_name),
      goto_functions(_goto_functions),
      goto_function(_goto_function)
  {
    find_function_loops();
  }

  void find_function_loops();
  void dump() const;

  const function_loopst &get_loops() const
  {
    return function_loops;
  }
};

#endif /* GOTO_PROGRAMS_GOTO_LOOPS_H_ */
