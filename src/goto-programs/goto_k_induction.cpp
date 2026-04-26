#include <goto-programs/goto_k_induction.h>
#include <goto-programs/remove_no_op.h>
#include <util/c_types.h>
#include <util/expr_util.h>
#include <util/i2string.h>
#include <util/std_expr.h>
#include <set>

static void
collect_symbol_names(const expr2tc &expr, std::set<irep_idt> &names)
{
  if (!expr)
    return;
  if (is_symbol2t(expr))
  {
    names.insert(to_symbol2t(expr).get_symbol_name());
    return;
  }
  for (size_t i = 0; i < expr->get_num_sub_exprs(); ++i)
  {
    const expr2tc *sub = expr->get_sub_expr(i);
    if (sub && *sub)
      collect_symbol_names(*sub, names);
  }
}

void goto_k_induction(goto_functionst &goto_functions)
{
  Forall_goto_functions (it, goto_functions)
    if (it->second.body_available)
      goto_k_inductiont(it->first, goto_functions, it->second);

  goto_functions.update();
}

void goto_termination(goto_functionst &goto_functions)
{
  Forall_goto_functions (it, goto_functions)
    if (it->second.body_available)
      goto_k_inductiont(it->first, goto_functions, it->second);
  goto_functions.update();

  auto function = goto_functions.function_map.find("__ESBMC_main");

  // Search for __ESBMC_main
  auto it = function->second.body.instructions.begin();
  while (it != function->second.body.instructions.end())
  {
    if (it->is_function_call())
    {
      auto const &call = to_code_function_call2t(it->code);
      if (to_symbol2t(call.function).thename.as_string() == "c:@F@main")
        break;
    }
    it++;
  }
  assert(it != function->second.body.instructions.end());

  // Create assert(0) as termination marker.
  // This assertion fails when reached, allowing reachability analysis
  // to detect program termination vs. infinite execution
  goto_programt dest;
  goto_programt::targett t = dest.add_instruction(ASSERT);
  // Always false - assertion always fails when reached
  t->guard = gen_false_expr();
  t->inductive_step_instruction = true;
  t->inductive_assertion = false;
  t->location.comment("termination");

  // And add it one instruction after the call to main
  it++;
  function->second.body.insert_swap(it, dest);
}

// Iteration-count cap above which we still apply the k-induction
// transform.  For very long counted loops (say, 1000+ iterations), BMC
// fully unrolling produces an SMT formula proportional in size to the
// trip count, which dominates solver time; the havoc-step-once
// over-approximation is preferable there.  64 is a conservative
// breakpoint: most array literals and library helper sizes in practice
// (Solidity array literals, fixed-N copies) are <= 32, and even doubling
// that headroom keeps the SMT footprint manageable.
static constexpr int kCountedLoopThreshold = 64;

void goto_k_inductiont::goto_k_induction()
{
  // Determine whether this function's loops can be safely fully unrolled
  // by BMC instead of being havoc-step-once'd by k-induction.  The transform
  // is sound but a coarse over-approximation; for "pure local writers" —
  // functions whose deref-writes only go through pointers locally allocated
  // in the function (not through caller-passed pointers) — BMC unrolling
  // is strictly more precise.  Library helpers like `_ESBMC_arrcpy` (whose
  // loop writes to a `dst` returned from `_ESBMC_alloc_array`) fall in this
  // bucket; functions like `__memset_impl` (writing through the parameter
  // `sp`) do not, and must keep the k-induction transform — otherwise BMC's
  // bounded unwind silently truncates writes the caller relies on.
  bool function_is_pure_local_writer = false;
  {
    std::unordered_set<irep_idt, irep_id_hash> visited;
    function_is_pure_local_writer =
      !callee_writes_through_pointer(function_name, visited);
  }

  // Full unwind the program
  for (auto &function_loop : function_loops)
  {
    if (function_loop.get_modified_loop_vars().empty())
      continue;

    if (
      config.options.get_bool_option("add-symex-value-sets") &&
      function_loop.contains_only_pointers())
      continue;

    // Skip counted-for-loops in pure-local-writer functions when the
    // loop's bound is a function parameter (or other loop-invariant
    // symbol).  BMC unrolling captures these trip counts exactly with the
    // `--unwind k` cap supplied by k-induction, so the havoc-step-once
    // transform would only weaken precision.  This is what unblocks
    // postcondition-after-helper-loop patterns (e.g. `t = [1,2,3]`
    // lowering to a `_ESBMC_arrcpy` call whose internal copy loop was
    // being k-inductized and havocing the freshly-allocated `dst`).
    //
    // We deliberately keep loops with literal-constant bounds k-inductized
    // even in pure-local-writer functions: empirically, library helpers
    // like `bytes_static_from_hex` (loop bounded by the compile-time
    // constant `_ESBMC_BYTES_STATIC_MAX = 32`) close inductive proofs
    // through havoc-step-once that BMC unrolling at unwind=k for k<32
    // cannot reproduce.
    if (
      function_is_pure_local_writer &&
      is_counted_for_loop_with_symbolic_bound(function_loop))
      continue;

    // Start the loop conversion
    convert_finite_loop(function_loop);
  }
}

bool goto_k_inductiont::is_counted_for_loop(
  const loopst &loop,
  int threshold) const
{
  goto_programt::targett loop_head = loop.get_original_loop_head();
  goto_programt::targett loop_exit = loop.get_original_loop_exit();

  // Bail on the unusual loop_head==assert shape; ordinary for-loops
  // always have a forward GOTO (the exit-on-condition) at the head.
  if (!loop_head->is_goto())
    return false;
  if (loop_head->is_backwards_goto())
    return false;
  if (is_nil_expr(loop_head->guard))
    return false;

  // The exit condition appears as `IF !(stay) GOTO past_loop` after lowering.
  if (!is_not2t(loop_head->guard))
    return false;

  const expr2tc &stay_cond = to_not2t(loop_head->guard).value;

  bool inclusive = false;
  expr2tc var_expr;
  expr2tc bound_expr;
  if (is_lessthan2t(stay_cond))
  {
    var_expr = to_lessthan2t(stay_cond).side_1;
    bound_expr = to_lessthan2t(stay_cond).side_2;
  }
  else if (is_lessthanequal2t(stay_cond))
  {
    inclusive = true;
    var_expr = to_lessthanequal2t(stay_cond).side_1;
    bound_expr = to_lessthanequal2t(stay_cond).side_2;
  }
  else
  {
    return false;
  }

  if (!is_symbol2t(var_expr))
    return false;
  const irep_idt &var_name = to_symbol2t(var_expr).thename;

  // The bound side must be either a literal non-negative constant int, or
  // a non-empty expression in symbols that are all loop-invariant
  // (i.e., none appear in modified_loop_vars and none equal var_name).
  bool bound_is_const = is_constant_int2t(bound_expr);
  long bound_val_literal = 0;
  if (bound_is_const)
  {
    const BigInt &bv = to_constant_int2t(bound_expr).value;
    if (bv.is_negative())
      return false;
    bound_val_literal = bv.to_int64();
  }
  else
  {
    std::set<irep_idt> bound_syms;
    collect_symbol_names(bound_expr, bound_syms);
    if (bound_syms.empty())
      return false;
    if (bound_syms.count(var_name))
      return false;
    for (const auto &mv : loop.get_modified_loop_vars())
    {
      if (!is_symbol2t(mv))
        continue;
      if (bound_syms.count(to_symbol2t(mv).thename))
        return false;
    }
  }

  // The instruction immediately before the backwards GOTO must be the
  // canonical increment `var = var + step`, with step a positive int
  // constant.  We check this first so we can also exclude this iterator
  // from the "no other writes to var" scan below.
  if (loop_exit == goto_function.body.instructions.begin())
    return false;
  goto_programt::targett incr_it = loop_exit;
  --incr_it;
  if (!incr_it->is_assign() || !is_code_assign2t(incr_it->code))
    return false;
  const auto &incr_assign = to_code_assign2t(incr_it->code);
  if (
    !is_symbol2t(incr_assign.target) ||
    to_symbol2t(incr_assign.target).thename != var_name)
    return false;
  if (!is_add2t(incr_assign.source))
    return false;
  const add2t &add = to_add2t(incr_assign.source);
  long step_val = 0;
  if (
    is_symbol2t(add.side_1) &&
    to_symbol2t(add.side_1).thename == var_name &&
    is_constant_int2t(add.side_2))
  {
    step_val = to_constant_int2t(add.side_2).value.to_int64();
  }
  else if (
    is_symbol2t(add.side_2) &&
    to_symbol2t(add.side_2).thename == var_name &&
    is_constant_int2t(add.side_1))
  {
    step_val = to_constant_int2t(add.side_1).value.to_int64();
  }
  else
  {
    return false;
  }
  if (step_val <= 0)
    return false;

  // The instruction immediately before the loop head must be the init
  // `var = k0` with k0 a non-negative int constant.
  if (loop_head == goto_function.body.instructions.begin())
    return false;
  goto_programt::targett init_it = loop_head;
  --init_it;
  if (!init_it->is_assign() || !is_code_assign2t(init_it->code))
    return false;
  const auto &init_assign = to_code_assign2t(init_it->code);
  if (
    !is_symbol2t(init_assign.target) ||
    to_symbol2t(init_assign.target).thename != var_name)
    return false;
  expr2tc init_rhs = init_assign.source;
  simplify(init_rhs);
  if (!is_constant_int2t(init_rhs))
    return false;
  const BigInt &init_bv = to_constant_int2t(init_rhs).value;
  if (init_bv.is_negative())
    return false;
  long init_val = init_bv.to_int64();

  // No other ASSIGN to var is allowed inside the loop body (loop_head
  // exclusive, loop_exit exclusive — except the increment we matched).
  goto_programt::targett body_it = loop_head;
  ++body_it;
  for (; body_it != loop_exit; ++body_it)
  {
    if (body_it == incr_it)
      continue;
    if (!body_it->is_assign() || !is_code_assign2t(body_it->code))
      continue;
    const auto &a = to_code_assign2t(body_it->code);
    if (
      is_symbol2t(a.target) &&
      to_symbol2t(a.target).thename == var_name)
      return false;
  }

  if (bound_is_const)
  {
    long span = bound_val_literal - init_val;
    if (inclusive)
      span += 1;
    if (span <= 0)
      return false;
    long iter_count = (span + step_val - 1) / step_val;
    return iter_count > 0 && iter_count <= threshold;
  }

  // Symbolic loop-invariant bound: defer trip count to BMC unrolling.
  return true;
}

bool goto_k_inductiont::is_counted_for_loop_with_symbolic_bound(
  const loopst &loop) const
{
  // Same canonical-counted-for-loop pattern as `is_counted_for_loop`,
  // but additionally requires the bound expression to be symbolic
  // (i.e., not a literal `constant_int`).  Used to keep literal-bounded
  // library helpers (e.g. `bytes_static_from_hex`'s loop bounded by
  // _ESBMC_BYTES_STATIC_MAX = 32) k-inductized while skipping symbolic-
  // bounded ones (e.g. `_ESBMC_arrcpy`'s loop bounded by `from_size`).
  goto_programt::targett loop_head = loop.get_original_loop_head();
  goto_programt::targett loop_exit = loop.get_original_loop_exit();

  if (!loop_head->is_goto())
    return false;
  if (loop_head->is_backwards_goto())
    return false;
  if (is_nil_expr(loop_head->guard))
    return false;
  if (!is_not2t(loop_head->guard))
    return false;

  const expr2tc &stay_cond = to_not2t(loop_head->guard).value;

  expr2tc var_expr;
  expr2tc bound_expr;
  if (is_lessthan2t(stay_cond))
  {
    var_expr = to_lessthan2t(stay_cond).side_1;
    bound_expr = to_lessthan2t(stay_cond).side_2;
  }
  else if (is_lessthanequal2t(stay_cond))
  {
    var_expr = to_lessthanequal2t(stay_cond).side_1;
    bound_expr = to_lessthanequal2t(stay_cond).side_2;
  }
  else
  {
    return false;
  }

  if (!is_symbol2t(var_expr))
    return false;
  const irep_idt &var_name = to_symbol2t(var_expr).thename;

  // Reject literal-constant bounds.
  if (is_constant_int2t(bound_expr))
    return false;

  // Bound must be a non-empty expression of loop-invariant symbols.
  std::set<irep_idt> bound_syms;
  collect_symbol_names(bound_expr, bound_syms);
  if (bound_syms.empty())
    return false;
  if (bound_syms.count(var_name))
    return false;
  for (const auto &mv : loop.get_modified_loop_vars())
  {
    if (!is_symbol2t(mv))
      continue;
    if (bound_syms.count(to_symbol2t(mv).thename))
      return false;
  }

  // Increment immediately before backwards GOTO, of the form var = var + step.
  if (loop_exit == goto_function.body.instructions.begin())
    return false;
  goto_programt::targett incr_it = loop_exit;
  --incr_it;
  if (!incr_it->is_assign() || !is_code_assign2t(incr_it->code))
    return false;
  const auto &incr_assign = to_code_assign2t(incr_it->code);
  if (
    !is_symbol2t(incr_assign.target) ||
    to_symbol2t(incr_assign.target).thename != var_name)
    return false;
  if (!is_add2t(incr_assign.source))
    return false;
  const add2t &add = to_add2t(incr_assign.source);
  bool incr_ok = false;
  if (
    is_symbol2t(add.side_1) &&
    to_symbol2t(add.side_1).thename == var_name &&
    is_constant_int2t(add.side_2) &&
    to_constant_int2t(add.side_2).value.to_int64() > 0)
    incr_ok = true;
  else if (
    is_symbol2t(add.side_2) &&
    to_symbol2t(add.side_2).thename == var_name &&
    is_constant_int2t(add.side_1) &&
    to_constant_int2t(add.side_1).value.to_int64() > 0)
    incr_ok = true;
  if (!incr_ok)
    return false;

  // Init `var = k0` with k0 a non-negative constant int, immediately
  // before the loop head.
  if (loop_head == goto_function.body.instructions.begin())
    return false;
  goto_programt::targett init_it = loop_head;
  --init_it;
  if (!init_it->is_assign() || !is_code_assign2t(init_it->code))
    return false;
  const auto &init_assign = to_code_assign2t(init_it->code);
  if (
    !is_symbol2t(init_assign.target) ||
    to_symbol2t(init_assign.target).thename != var_name)
    return false;
  expr2tc init_rhs = init_assign.source;
  simplify(init_rhs);
  if (!is_constant_int2t(init_rhs))
    return false;
  if (to_constant_int2t(init_rhs).value.is_negative())
    return false;

  // No other ASSIGN to var inside the body.
  goto_programt::targett body_it = loop_head;
  ++body_it;
  for (; body_it != loop_exit; ++body_it)
  {
    if (body_it == incr_it)
      continue;
    if (!body_it->is_assign() || !is_code_assign2t(body_it->code))
      continue;
    const auto &a = to_code_assign2t(body_it->code);
    if (
      is_symbol2t(a.target) &&
      to_symbol2t(a.target).thename == var_name)
      return false;
  }

  return true;
}

void goto_k_inductiont::convert_finite_loop(loopst &loop)
{
  // Get current loop head and loop exit
  goto_programt::targett loop_head = loop.get_original_loop_head();
  goto_programt::targett loop_exit = loop.get_original_loop_exit();

  guardst guards;
  get_entry_cond_rec(loop_head, loop_exit, guards);

  // Remove loop conditions not related to the written variables
  remove_unrelated_loop_cond(guards, loop);

  // Assume the loop entry condition before go into the loop
  assume_loop_entry_cond_before_loop(loop_head, loop_exit, guards);

  // Create the nondet assignments on the beginning of the loop
  make_nondet_assign(loop_head, loop);

  // Check if the loop exit needs to be updated
  // We must point to the assume that was inserted in the previous
  // transformation
  adjust_loop_head_and_exit(loop_head, loop_exit);
}

bool goto_k_inductiont::get_entry_cond_rec(
  const goto_programt::targett &loop_head,
  const goto_programt::targett &loop_exit,
  guardst &guards)
{
  // Let's walk the loop and collect the constraints to enter the
  // loop. This might be messy because of side-effects

  // entry and exit numbers
  auto const &entry_number = loop_head->location_number;
  auto const &exit_number = loop_exit->location_number;

  // We jumped outside the loop, don't collect this constraint
  if (entry_number > exit_number)
    return true;

  goto_programt::targett tmp_head = loop_head;
  for (; tmp_head != loop_exit; tmp_head++)
  {
    auto it = marked_branch.find(tmp_head->location_number);
    if (it != marked_branch.end())
      return it->second;

    /* TODO: disable this for now, it will be used for termination evaluation
     * in the future.

    // Return, assume(0) and assert(0) stop the execution, so ignore these
    // branches too
    if(tmp_head->is_return())
      return true;

    if(tmp_head->is_assume() || tmp_head->is_assert())
      if(is_false(tmp_head->guard))
        return true;
    */

    if (tmp_head->is_goto() && !tmp_head->is_backwards_goto())
    {
      expr2tc g = tmp_head->guard;
      simplify(g);

      // If the guard is false, we can skip it right away
      if (is_false(g))
        continue;

      // We need to walk the branches and collect constraints that force
      // the path inside the loop and reach the end of the loop body

      // Get the branch number for caching
      auto const branch_number = tmp_head->location_number;

      // Walk the true branch
      bool true_branch = true;
      guardst true_branch_guard;
      if (!is_false(g))
      {
        true_branch_guard[branch_number].add(g);
        true_branch = get_entry_cond_rec(
          tmp_head->targets.front(), loop_exit, true_branch_guard);
      }

      // Walk the false branch
      bool false_branch = true;
      guardst false_branch_guard;
      if (!is_true(g))
      {
        goto_programt::targett new_tmp_head = tmp_head;
        make_not(g);
        false_branch_guard[branch_number].add(g);
        false_branch =
          get_entry_cond_rec(++new_tmp_head, loop_exit, false_branch_guard);
      }

      // If we evaluated both sides of the branch, mark it so we don't
      // have to do it again.
      marked_branch[branch_number] = (false_branch ^ true_branch);

      // If both side reach the end of the loop or if both side don't reach it
      // we can ignore them
      if (!(false_branch ^ true_branch))
        return false_branch && true_branch;

      // At least only one of the branches reach the end of the loop, so
      // collect the guards
      if (!true_branch)
      {
        guards.insert(true_branch_guard.begin(), true_branch_guard.end());
        return false;
      }

      if (!false_branch)
      {
        guards.insert(false_branch_guard.begin(), false_branch_guard.end());
        return false;
      }
    }
  }

  return false;
}

void goto_k_inductiont::make_nondet_assign(
  goto_programt::targett &loop_head,
  const loopst &loop)
{
  // Track the original loop head
  auto const original_loop_head = loop_head;

  // Check if the loop_head is an assertion, and track it
  const bool is_assert = loop_head->is_assert();

  // If it's an assertion, adjust loop_head to insert assignments before it
  if ((is_assert) && loop_head != goto_function.body.instructions.begin())
  {
    --loop_head;
    // We add instructions before a GOTO instruction
    // So we ensure we have one here
    assert(loop_head->is_goto());
  }

  // Auto-infer entry-dominator invariants from loop-body assertions.
  // Must be done BEFORE building dest / insert_swap so the body iterators
  // we walk reflect the unmodified program.  scan_begin = first instruction
  // after the loop-condition GOTO; scan_end = the original loop exit.
  std::vector<expr2tc> inferred_assumptions;
  {
    auto scan_begin = loop_head;
    ++scan_begin;
    auto scan_end = loop.get_original_loop_exit();
    infer_entry_invariants(scan_begin, scan_end, inferred_assumptions);
  }

  // Get the list of variables modified inside the loop
  auto const &loop_vars = loop.get_modified_loop_vars();

  // Filter inferred assumptions: keep only those whose guard references at
  // least one variable that is also in the modified_loop_vars set.  Without
  // this gate, an assertion deep in the call graph may reference variables
  // (like `this->x`) that resolve at a different memory location at the
  // caller scope — injecting them as ASSUME at the outer loop constrains
  // unrelated memory and breaks inductive proofs that previously closed.
  // The intuition: an ASSUME is useful only if it constrains a havoc'd
  // value (otherwise it is either a tautology or an unsound contradiction
  // about non-havoc'd state).
  {
    std::set<irep_idt> loop_var_names;
    for (const auto &v : loop_vars)
      collect_symbol_names(v, loop_var_names);

    std::vector<expr2tc> filtered;
    for (const auto &inv : inferred_assumptions)
    {
      std::set<irep_idt> guard_syms;
      collect_symbol_names(inv, guard_syms);
      for (const auto &s : guard_syms)
      {
        if (loop_var_names.count(s))
        {
          filtered.push_back(inv);
          break;
        }
      }
    }
    inferred_assumptions.swap(filtered);
  }

  const bool use_value_sets =
    config.options.get_bool_option("add-symex-value-sets");
  goto_programt dest;
  for (auto const &lhs : loop_vars)
  {
    // do not assign nondeterministic value to pointers if we assume
    // objects extracted from the value set analysis
    if (use_value_sets && is_pointer_type(lhs))
      continue;

    // For struct-typed lhs (e.g. Solidity's `_ESBMC_Object_<C>` contract
    // instance), nondet'ing the whole struct also clobbers any pointer-
    // typed fields (object-identity pointers like the backing buffer of
    // `uint[3] x`). After such havoc, body writes through those fields
    // dereference nondet pointers and every deref-validity claim fails
    // — yielding a spurious UNKNOWN at the inductive step. Under value-
    // set analysis, peel off one struct level and emit per-field nondets,
    // skipping pointer-typed fields so their pre-loop identity is
    // preserved (sound: they are never reassigned by the body of a
    // dispatcher loop; otherwise the field is reachable as a separate
    // entry in modified_loop_vars and havoc'd anyway via that entry).
    if (use_value_sets && is_struct_type(lhs->type))
    {
      const struct_type2t &st = to_struct_type(lhs->type);
      for (size_t i = 0; i < st.members.size(); ++i)
      {
        if (is_pointer_type(st.members[i]))
          continue;
        expr2tc field = member2tc(st.members[i], lhs, st.member_names[i]);
        expr2tc rhs = gen_nondet(st.members[i]);
        goto_programt::targett t = dest.add_instruction(ASSIGN);
        t->inductive_step_instruction = true;
        t->code = code_assign2tc(field, rhs);
        t->location = loop_head->location;
      }
      continue;
    }

    // Generate a nondeterministic value for the loop variable
    expr2tc rhs = gen_nondet(lhs->type);

    // Create an assignment instruction for the nondeterministic value
    goto_programt::targett t = dest.add_instruction(ASSIGN);
    t->inductive_step_instruction = true;
    t->code = code_assign2tc(lhs, rhs);
    // Keep the same location as the loop head
    t->location = loop_head->location;
  }

  // Inject the auto-inferred inductive hypotheses (ASSUME P) after havoc.
  // Tagged inductive_step_instruction=true so the base/forward phases skip
  // them (see execution_state.cpp:210-224); only the inductive step sees
  // them, which is exactly what classical k-induction requires.
  for (const auto &inv : inferred_assumptions)
  {
    goto_programt::targett t = dest.add_instruction(ASSUME);
    t->inductive_step_instruction = true;
    t->guard = inv;
    t->location = loop_head->location;
  }

  // Insert the generated assignments before the loop head in the program
  goto_function.body.insert_swap(loop_head, dest);

  // Get original head again
  // Since we are using insert_swap to keep the targets, the
  // original loop head as shifted to after the assume cond
  if (is_assert)
  {
    // Restore the original loop head if it was an assertion
    loop_head = original_loop_head;
    assert(loop_head->is_assert());
  }
  else
  {
    // Move past the inserted instructions during the inductive step
    while ((++loop_head)->inductive_step_instruction)
      ;
  }
}

// Recursion bound for following FUNCTION_CALL chains.  Solidity's harness
// is _ESBMC_Main_<C> -> _ESBMC_Nondet_Extcall_<C> -> user method = depth 2.
// Allow some headroom for inheritance/internal trampolines.
static constexpr int kInvariantInferenceMaxDepth = 6;

void goto_k_inductiont::infer_entry_invariants_rec(
  goto_programt::const_targett scan_begin,
  goto_programt::const_targett scan_end,
  std::set<irep_idt> &modified_so_far,
  std::vector<expr2tc> &assumptions,
  std::set<irep_idt> &visited_funcs,
  int depth) const
{
  if (depth > kInvariantInferenceMaxDepth)
    return;

  for (auto it = scan_begin; it != scan_end; ++it)
  {
    if (it->is_assert())
    {
      // Skip any assertion already tagged inductive_step_instruction (it was
      // emitted by an earlier transformer pass; not a user-source ASSERT).
      if (it->inductive_step_instruction)
        continue;
      if (is_nil_expr(it->guard))
        continue;

      std::set<irep_idt> guard_syms;
      collect_symbol_names(it->guard, guard_syms);
      if (guard_syms.empty())
        continue;

      bool clean = true;
      for (const auto &sym : guard_syms)
      {
        if (modified_so_far.count(sym))
        {
          clean = false;
          break;
        }
      }

      if (clean)
        assumptions.push_back(it->guard);
      // else: a write to one of P's variables precedes this ASSERT in source
      // order, so P is a postcondition rather than an entry-dominator.
    }
    else if (it->is_assign() && is_code_assign2t(it->code))
    {
      const auto &assign = to_code_assign2t(it->code);
      collect_symbol_names(assign.target, modified_so_far);
    }
    else if (it->is_function_call() && is_code_function_call2t(it->code))
    {
      const auto &call = to_code_function_call2t(it->code);

      // Treat the return-value lhs (if any) as modified.
      if (!is_nil_expr(call.ret))
        collect_symbol_names(call.ret, modified_so_far);

      // Recurse into the callee body when resolvable.
      if (is_symbol2t(call.function))
      {
        const irep_idt callee_name = to_symbol2t(call.function).thename;
        if (visited_funcs.count(callee_name))
        {
          // Recursive call; conservatively bail.
          break;
        }
        auto fit = goto_functions.function_map.find(callee_name);
        if (
          fit != goto_functions.function_map.end() &&
          fit->second.body_available)
        {
          visited_funcs.insert(callee_name);
          const auto &body = fit->second.body.instructions;
          infer_entry_invariants_rec(
            body.begin(),
            body.end(),
            modified_so_far,
            assumptions,
            visited_funcs,
            depth + 1);
          visited_funcs.erase(callee_name);
          continue;
        }
      }
      // Unknown / opaque callee: conservative bail to avoid missing writes.
      break;
    }
    else if (it->is_backwards_goto())
    {
      // Nested loop back-edge; stop here so we don't mistake a property
      // written after the inner loop for an entry-dominant invariant.
      break;
    }
    // Other instruction kinds (ASSUME, DECL, forward GOTO, RETURN, OTHER):
    // skipped without affecting the modified set.  Linear order may visit
    // both arms of an IF; this gives a sound over-approximation of writes
    // (potentially blocking some asserts from becoming candidates) but
    // never injects an unsound ASSUME.
  }
}

void goto_k_inductiont::infer_entry_invariants(
  goto_programt::const_targett scan_begin,
  goto_programt::const_targett scan_end,
  std::vector<expr2tc> &assumptions) const
{
  std::set<irep_idt> modified_so_far;
  std::set<irep_idt> visited;
  infer_entry_invariants_rec(
    scan_begin, scan_end, modified_so_far, assumptions, visited, 0);
}

static bool contains_rec(const expr2tc &expr, const loopst::loop_varst &vars)
{
  bool res = false;
  expr->foreach_operand([&vars, &res](const expr2tc &e) {
    if (!is_nil_expr(e))
      res = contains_rec(e, vars) || res;
    return res;
  });

  if (!is_symbol2t(expr))
    return res;

  return (vars.find(expr) != vars.end()) || res;
}

void goto_k_inductiont::remove_unrelated_loop_cond(
  guardst &guards,
  loopst &loop)
{
  auto const &loop_vars = loop.get_modified_loop_vars();
  if (!loop_vars.size())
  {
    guards.clear();
    return;
  }

  guardst::iterator g = guards.begin();
  while (g != guards.end())
  {
    expr2tc g_expr = g->second.as_expr();

    if (!contains_rec(g_expr, loop_vars))
      g = guards.erase(g);
    else
      ++g;
  }
}

void goto_k_inductiont::assume_loop_entry_cond_before_loop(
  goto_programt::targett &loop_head,
  goto_programt::targett &loop_exit,
  const guardst &guards)
{
  goto_programt::targett tmp_head = loop_head;
  for (; tmp_head != loop_exit; tmp_head++)
  {
    auto const g = guards.find(tmp_head->location_number);
    if (g == guards.end())
      continue;

    expr2tc loop_cond = g->second.as_expr();

    if (is_nil_expr(loop_cond))
      return;

    if (is_true(loop_cond) || is_false(loop_cond))
      return;

    goto_programt dest;
    assume_cond(loop_cond, dest, tmp_head->location);

    goto_function.body.insert_swap(tmp_head, dest);
  }
}

void goto_k_inductiont::adjust_loop_head_and_exit(
  goto_programt::targett &loop_head,
  goto_programt::targett &loop_exit)
{
  loop_exit->targets.clear();
  loop_exit->targets.push_front(loop_head);

  goto_programt::targett _loop_exit = loop_exit;
  ++_loop_exit;

  // Zero means that the instruction was added during
  // the k-induction transformation
  if (_loop_exit->location_number == 0)
  {
    // Clear the target
    loop_head->targets.clear();

    // And set the target to be the newly inserted assume(cond)
    loop_head->targets.push_front(_loop_exit);
  }
}

void goto_k_inductiont::assume_cond(
  const expr2tc &cond,
  goto_programt &dest,
  const locationt &loc)
{
  goto_programt tmp_e;
  goto_programt::targett e = tmp_e.add_instruction(ASSUME);
  e->inductive_step_instruction = true;
  e->guard = cond;
  e->location = loc;
  dest.destructive_append(tmp_e);
}
