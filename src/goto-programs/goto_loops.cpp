#include "irep2/irep2_utils.h"
#include <goto-programs/goto_loops.h>
#include <util/expr_util.h>

bool check_var_name(const expr2tc &expr)
{
  if (!is_symbol2t(expr))
    return false;

  symbol2t s = to_symbol2t(expr);
  std::string identifier = s.thename.as_string();

  std::size_t found = identifier.find("__ESBMC_");
  if (found != std::string::npos)
    return false;

  found = identifier.find("return_value___");
  if (found != std::string::npos)
    return false;

  found = identifier.find("pthread_lib");
  if (found != std::string::npos)
    return false;

  // Don't add variables that we created for k-induction
  found = identifier.find("$");
  if (found != std::string::npos)
    return false;

  if (identifier == "__func__")
    return false;

  if (identifier == "__PRETTY_FUNCTION__")
    return false;

  if (identifier == "__LINE__")
    return false;

  return true;
}

void goto_loopst::find_function_loops()
{
  for (goto_programt::instructionst::iterator it =
         goto_function.body.instructions.begin();
       it != goto_function.body.instructions.end();
       it++)
  {
    // We found a loop, let's record its instructions
    if (it->is_backwards_goto())
    {
      assert(it->targets.size() == 1);
      goto_programt::instructionst::iterator &loop_head = *it->targets.begin();
      goto_programt::instructionst::iterator &loop_exit = it;

      // This means something like:
      // A: if(g) goto A;
      // Convert it into: assume(!g);
      if (loop_head->location_number == loop_exit->location_number)
      {
        simplify(loop_head->guard);
        it->make_assumption(not2tc(loop_head->guard));
        continue;
      }
      create_function_loop(loop_head, loop_exit);
    }
  }
}

void goto_loopst::create_function_loop(
  goto_programt::instructionst::iterator loop_head,
  goto_programt::instructionst::iterator loop_exit)
{
  goto_programt::instructionst::iterator it = loop_head;

  function_loops.push_front(loopst());
  function_loopst::iterator it1 = function_loops.begin();

  // Set original iterators
  it1->set_original_loop_head(loop_head);
  it1->set_original_loop_exit(loop_exit);

  // Push the current function name to the list of functions
  std::vector<irep_idt> function_names;
  function_names.push_back(function_name);

  // Copy the loop body
  std::size_t size = 0;
  while (it != loop_exit)
  {
    // This should be done only when we're running k-induction
    // Maybe a flag on the class?
    get_modified_variables(it, it1, function_names);
    ++it;

    // Count the number of instruction
    ++size;
  }

  // Include loop_exit
  it1->set_size(size + 1);
}

void goto_loopst::collect_addressof_targets(loopst &loop, const expr2tc &expr)
{
  if (is_nil_expr(expr))
    return;

  if (is_address_of2t(expr))
  {
    // Walk to the base symbol, peeling member/index/typecast/bitcast
    // layers. Anything underneath address_of that resolves to a named
    // storage location is what the callee may write through.
    expr2tc target = to_address_of2t(expr).ptr_obj;
    while (true)
    {
      if (is_member2t(target))
        target = to_member2t(target).source_value;
      else if (is_index2t(target))
        target = to_index2t(target).source_value;
      else if (is_typecast2t(target))
        target = to_typecast2t(target).from;
      else if (is_bitcast2t(target))
        target = to_bitcast2t(target).from;
      else
        break;
    }
    if (is_symbol2t(target) && check_var_name(target))
      loop.add_modified_var_to_loop(target);
    return;
  }

  // Recurse into sub-expressions — an actual may be a composite like
  // `(cond ? &obj1 : &obj2)` or `&s.field`.
  expr->foreach_operand([this, &loop](const expr2tc &sub) {
    collect_addressof_targets(loop, sub);
  });
}

static bool expr_contains_dereference(const expr2tc &e)
{
  if (is_nil_expr(e))
    return false;
  if (is_dereference2t(e))
    return true;
  bool found = false;
  e->foreach_operand([&found](const expr2tc &sub) {
    if (!found && expr_contains_dereference(sub))
      found = true;
  });
  return found;
}

// Walk a (possibly composite) lvalue/rvalue expression down to the symbol
// that is being indirected through.  Peels typecast/bitcast/index/member/
// dereference/pointer-arithmetic layers.  Used by the param-derivation
// pass to identify the storage root of a deref-target or the source root
// of an assignment.  Returns nil if no plain symbol root exists (e.g. for
// constants, address_of of a struct literal, or unsupported shapes).
static expr2tc resolve_storage_base(const expr2tc &e)
{
  expr2tc cur = e;
  while (cur)
  {
    if (is_dereference2t(cur))
      cur = to_dereference2t(cur).value;
    else if (is_index2t(cur))
      cur = to_index2t(cur).source_value;
    else if (is_member2t(cur))
      cur = to_member2t(cur).source_value;
    else if (is_typecast2t(cur))
      cur = to_typecast2t(cur).from;
    else if (is_bitcast2t(cur))
      cur = to_bitcast2t(cur).from;
    else if (is_add2t(cur))
    {
      // Pointer arithmetic — keep the side that is symbol-rooted.
      const add2t &a = to_add2t(cur);
      bool s1 = is_symbol2t(a.side_1) || is_typecast2t(a.side_1) ||
                is_bitcast2t(a.side_1) || is_index2t(a.side_1) ||
                is_member2t(a.side_1) || is_dereference2t(a.side_1) ||
                is_add2t(a.side_1) || is_sub2t(a.side_1);
      bool s2 = is_symbol2t(a.side_2) || is_typecast2t(a.side_2) ||
                is_bitcast2t(a.side_2) || is_index2t(a.side_2) ||
                is_member2t(a.side_2) || is_dereference2t(a.side_2) ||
                is_add2t(a.side_2) || is_sub2t(a.side_2);
      if (s1 && !s2)
        cur = a.side_1;
      else if (s2 && !s1)
        cur = a.side_2;
      else if (s1)
        cur = a.side_1;
      else
        break;
    }
    else if (is_sub2t(cur))
      cur = to_sub2t(cur).side_1;
    else if (is_address_of2t(cur))
      cur = to_address_of2t(cur).ptr_obj;
    else
      break;
  }
  return cur;
}

bool goto_loopst::callee_writes_through_pointer(
  const irep_idt &callee,
  std::unordered_set<irep_idt, irep_id_hash> &visited)
{
  // Recursion guard.
  if (!visited.insert(callee).second)
    return false;

  auto it = goto_functions.function_map.find(callee);
  if (it == goto_functions.function_map.end() || !it->second.body_available)
    return false;

  // Build the running set of pointer symbols whose storage is reachable
  // from a parameter of this function.  Only writes through such pointers
  // can affect caller-visible memory (and therefore propagate up to the
  // k-induction modified-vars set).  Writes through locally-allocated
  // pointers — e.g. `block` in `_ESBMC_alloc_array`'s `block = calloc(...)`
  // followed by `block[0] = count` — must be excluded; otherwise the
  // address-of-arg propagation incorrectly havocs caller-side aux arrays
  // that are only READ through the call.
  std::unordered_set<irep_idt, irep_id_hash> param_derived;
  for (const auto &arg : it->second.type.arguments())
  {
    irep_idt id = arg.get_identifier();
    if (id != irep_idt())
      param_derived.insert(id);
  }

  for (const auto &insn : it->second.body.instructions)
  {
    if (insn.is_assign() && is_code_assign2t(insn.code))
    {
      const code_assign2t &a = to_code_assign2t(insn.code);

      // Strip outer typecast/bitcast on the lhs to find the lhs root.
      expr2tc lhs_root = a.target;
      while (lhs_root)
      {
        if (is_typecast2t(lhs_root))
          lhs_root = to_typecast2t(lhs_root).from;
        else if (is_bitcast2t(lhs_root))
          lhs_root = to_bitcast2t(lhs_root).from;
        else
          break;
      }

      // Case 1: deref-target (array/pointer write).  Caller-affecting
      // iff the indirected pointer is param-derived.
      if (expr_contains_dereference(a.target))
      {
        expr2tc base = resolve_storage_base(a.target);
        if (
          base && is_symbol2t(base) &&
          param_derived.count(to_symbol2t(base).thename))
          return true;
        // Otherwise: writes through a local allocation; doesn't propagate.
        continue;
      }

      // Case 2: plain symbol assignment.  Update param_derived membership
      // of the lhs based on whether the rhs's storage root is param-
      // derived.
      if (is_symbol2t(lhs_root))
      {
        irep_idt lhs_name = to_symbol2t(lhs_root).thename;
        expr2tc rhs_root = resolve_storage_base(a.source);
        bool rhs_pd = false;
        if (rhs_root && is_symbol2t(rhs_root))
        {
          if (param_derived.count(to_symbol2t(rhs_root).thename))
            rhs_pd = true;
        }
        if (rhs_pd)
          param_derived.insert(lhs_name);
        else
          param_derived.erase(lhs_name);
      }
    }
    else if (insn.is_function_call() && is_code_function_call2t(insn.code))
    {
      const code_function_call2t &nested =
        to_code_function_call2t(insn.code);

      // The result of a function call is a fresh value (heap allocation,
      // computed result, etc.) — clear any prior param-derived status.
      if (!is_nil_expr(nested.ret) && is_symbol2t(nested.ret))
        param_derived.erase(to_symbol2t(nested.ret).thename);

      // For callees we recurse into, ask whether THEIR body writes through
      // their own parameters — if it does, that means they may write
      // through the actuals we passed.  But it only matters if at least one
      // of those actuals is a param-derived pointer of this function;
      // otherwise the inner call's deref-writes touch only OUR locally-
      // allocated storage, which doesn't escape.
      if (is_dereference2t(nested.function) || !is_symbol2t(nested.function))
        continue;
      if (!callee_writes_through_pointer(
            to_symbol2t(nested.function).thename, visited))
        continue;
      bool any_actual_param_derived = false;
      for (const expr2tc &actual : nested.operands)
      {
        expr2tc base = resolve_storage_base(actual);
        if (
          base && is_symbol2t(base) &&
          param_derived.count(to_symbol2t(base).thename))
        {
          any_actual_param_derived = true;
          break;
        }
      }
      if (any_actual_param_derived)
        return true;
    }
  }
  return false;
}

void goto_loopst::get_modified_variables(
  goto_programt::instructionst::iterator instruction,
  function_loopst::iterator loop,
  std::vector<irep_idt> &function_names)
{
  if (instruction->is_assign())
  {
    const code_assign2t &assign = to_code_assign2t(instruction->code);
    add_loop_var(*loop, assign.target, true);
  }
  else if (instruction->is_function_call())
  {
    // Functions are a bit tricky
    code_function_call2t &function_call =
      to_code_function_call2t(instruction->code);

    // Don't do function pointers
    if (is_dereference2t(function_call.function))
      return;

    // First, add its return
    add_loop_var(*loop, function_call.ret, true);

    // Extend the modified-variable set to cover caller-side storage that
    // may be written through pointer-typed arguments. Without this, the
    // k-induction havoc preamble omits variables like `obj` in the common
    // pattern `dispatch(&obj)` / `obj.method(...)` where the callee writes
    // through the formal pointer parameter. The plain syntactic recursion
    // below only registers the callee-local pointer (e.g. `p`), never the
    // caller's object. Gate the extension on the callee actually
    // containing an indirect write (an ASSIGN whose lhs mentions a
    // dereference); otherwise a purely read-only helper would over-
    // approximate unnecessarily and kill provability of k-induction
    // invariants that don't involve the pointed-to object.
    if (is_symbol2t(function_call.function))
    {
      std::unordered_set<irep_idt, irep_id_hash> visited;
      if (callee_writes_through_pointer(
            to_symbol2t(function_call.function).thename, visited))
      {
        for (const expr2tc &arg : function_call.operands)
          collect_addressof_targets(*loop, arg);
      }
    }

    // The run over the function body and get the modified variables there
    irep_idt &identifier = to_symbol2t(function_call.function).thename;

    // This means recursion, do nothing
    if (
      std::find(function_names.begin(), function_names.end(), identifier) !=
      function_names.end())
      return;

    // We didn't entered this function yet, so add it to the list
    function_names.push_back(identifier);

    // find code in function map
    goto_functionst::function_mapt::iterator it =
      goto_functions.function_map.find(identifier);

    if (it == goto_functions.function_map.end())
    {
      log_error("failed to find `{}' in function_map", id2string(identifier));
      abort();
    }

    // Avoid iterating over functions that don't have a body
    if (!it->second.body_available)
      return;

    for (goto_programt::instructionst::iterator head =
           it->second.body.instructions.begin();
         head != it->second.body.instructions.end();
         ++head)
    {
      get_modified_variables(head, loop, function_names);
    }
  }
  else if (
    instruction->is_goto() || instruction->is_assert() ||
    instruction->is_assume())
  {
    add_loop_var(*loop, instruction->guard, false);
  }
  else if (instruction->is_end_function())
  {
    function_names.pop_back();
  }
}

void goto_loopst::add_loop_var(
  loopst &loop,
  const expr2tc &expr,
  bool is_modified)
{
  if (is_nil_expr(expr))
    return;

  expr->foreach_operand([this, &loop, &is_modified](const expr2tc &e) {
    add_loop_var(loop, e, is_modified);
  });

  if (is_symbol2t(expr) && check_var_name(expr))
  {
    if (is_modified)
      loop.add_modified_var_to_loop(expr);
    else
      loop.add_unmodified_var_to_loop(expr);
  }
}

void goto_loopst::dump() const
{
  for (auto &function_loop : function_loops)
    function_loop.dump();
}
