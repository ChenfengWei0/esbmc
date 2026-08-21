#ifndef CPROVER_GOTO_SYMEX_BUILD_GOTO_TRACE_H
#define CPROVER_GOTO_SYMEX_BUILD_GOTO_TRACE_H

#include <goto-symex/goto_symex_state.h>
#include <goto-symex/goto_trace.h>
#include <goto-symex/symex_target_equation.h>

// When true, build_goto_trace records each assignment's lhs/rhs/ssa_lhs but
// does NOT ask the solver for the assigned value (goto_trace_stept::value stays
// nil). The caller materialises the values it needs with
// materialise_trace_step_value. Set only by consumers that harvest a few named
// symbols from a trace and never print it (the Solidity path-coverage
// counterexample journal): MEASURED on acfix_fixlink_Product, building the
// trace asked for the model of every one of 3332 assignments per witness, ~35
// of a 47 s enumeration run, for a harvest that reads a handful of them.
extern bool build_goto_trace_lazy_assignment_values;

// Fetch the value of a lazily-built assignment step from the solver. A member
// write (`this->x = v`, lhs a member chain, rhs a `with` over the object)
// fetches ONLY that member of the SSA lhs object; everything else fetches the
// SSA rhs. Returns the value (also stored in step.value).
class smt_convt;
expr2tc materialise_trace_step_value(smt_convt &smt_conv, goto_trace_stept &step);

void build_goto_trace(
  const symex_target_equationt &target,
  smt_convt &smt_conv,
  goto_tracet &goto_trace,
  const bool &is_compact_trace);

void build_successful_goto_trace(
  const symex_target_equationt &target,
  const namespacet &ns,
  goto_tracet &goto_trace);

expr2tc build_lhs(smt_convt &smt_conv, const expr2tc &lhs);
expr2tc build_rhs(smt_convt &smt_conv, const expr2tc &rhs);

#endif
