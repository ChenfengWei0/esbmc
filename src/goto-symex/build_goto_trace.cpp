#include <cstdlib>
#include <cassert>
#include <goto-symex/build_goto_trace.h>
#include <util/message.h>
#include <irep2/irep2_utils.h>
#include <goto-symex/witnesses.h>

expr2tc build_lhs(smt_convt &smt_conv, const expr2tc &lhs)
{
  if (is_nil_expr(lhs))
    return lhs;

  expr2tc new_lhs = lhs;
  switch (new_lhs->expr_id)
  {
  case expr2t::index_id:
  {
    // An array subscription
    index2t index = to_index2t(new_lhs);

    // Build new source value, it might be an index, in case of
    // multidimensional arrays
    expr2tc new_source_value = build_lhs(smt_conv, index.source_value);
    expr2tc new_value = smt_conv.get(index.index);
    new_lhs = index2tc(new_lhs->type, new_source_value, new_value);
    break;
  }

  case expr2t::typecast_id:
    new_lhs = to_typecast2t(new_lhs).from;
    break;

  case expr2t::bitcast_id:
    new_lhs = to_bitcast2t(new_lhs).from;
    break;

  default:
    break;
  }

  renaming::renaming_levelt::get_original_name(new_lhs, symbol2t::level0);
  return new_lhs;
}

bool build_goto_trace_lazy_assignment_values = false;

expr2tc build_rhs(smt_convt &smt_conv, const expr2tc &rhs);

static expr2tc rebase_member_chain(const expr2tc &lhs, const expr2tc &ssa_lhs)
{
  // member(member(...(X)...)) -> the same chain over ssa_lhs; nil if the
  // chain has anything but members above its base.
  if (is_member2t(lhs))
  {
    const member2t &m = to_member2t(lhs);
    expr2tc inner = rebase_member_chain(m.source_value, ssa_lhs);
    if (is_nil_expr(inner))
      return expr2tc();
    return member2tc(m.type, inner, m.member);
  }
  if (is_dereference2t(lhs) || is_symbol2t(lhs))
    return ssa_lhs;
  return expr2tc();
}

expr2tc materialise_trace_step_value(smt_convt &smt_conv, goto_trace_stept &step)
{
  static const bool lazy_debug = std::getenv("ESBMC_TRACE_LAZY_DEBUG");
  if (!is_nil_expr(step.value) || !step.is_assignment())
    return step.value;
  try
  {
    if (
      is_member2t(step.lhs) && is_with2t(step.rhs) && !is_nil_expr(step.ssa_lhs) &&
      is_struct_type(step.ssa_lhs->type))
    {
      expr2tc target = rebase_member_chain(step.lhs, step.ssa_lhs);
      // Only scalar members are fetched through the rebased chain: the
      // solver hands a struct-typed member back as an unevaluated member
      // expression over the SSA symbol (measured on Product: the value of
      // `tmp$5#4._version` came back as `_ESBMC_Object_Product#&0#2._version`),
      // which the harvest would treat as a non-constant and drop. Those go
      // through the eager build_rhs() path below.
      if (
        !is_nil_expr(target) && target->type == step.lhs->type &&
        (is_bv_type(target) || is_bool_type(target)))
      {
        expr2tc v = smt_conv.get(target);
        if (lazy_debug)
          log_status(
            "[lazy-trace] member get lhs={} ssa={} target={} ttype={} -> {} ({})",
            from_expr(step.lhs),
            from_expr(step.ssa_lhs),
            from_expr(target),
            get_type_id(target->type),
            is_nil_expr(v) ? std::string("<nil>") : from_expr(v).substr(0, 60),
            is_nil_expr(v) ? std::string("-") : get_expr_id(v));
        renaming::renaming_levelt::get_original_name(v, symbol2t::level0);
        step.value = v;
        return step.value;
      }
      if (lazy_debug)
        log_status(
          "[lazy-trace] member rebase FAILED lhs={} lhs_id={} target_nil={}",
          from_expr(step.lhs),
          get_expr_id(step.lhs),
          is_nil_expr(target));
    }
    // ---- NO ELEMENT-WISE MODEL OF A LARGE ARRAY ----
    //
    // smt_convt::get on an array asks the solver for every element (and
    // every element of every nested array). MEASURED on PuttyV2.balanceOf
    // (full-20260822-v33): a nondet-sourced nested array assignment sent
    // get_array into bitwuzla get_value per element until the process hit
    // std::bad_alloc at --memlimit 5g, 97 s after the last claim was
    // decided -- in eager mode too. The harvest renders no array of that
    // size (inputs are scalars, `.length`s and small literals), so such a
    // value is left nil and the step is dropped exactly as a nil value
    // always was.
    {
      size_t elems = 1;
      bool too_big = false;
      const type2tc *t = &step.lhs->type;
      while (is_array_type(*t))
      {
        const array_type2t &at = to_array_type(*t);
        if (at.size_is_infinite || !is_constant_int2t(at.array_size))
        {
          too_big = true;
          break;
        }
        elems *= to_constant_int2t(at.array_size).value.to_uint64();
        if (elems > 256)
        {
          too_big = true;
          break;
        }
        t = &at.subtype;
      }
      if (too_big)
      {
        if (lazy_debug)
          log_status(
            "[lazy-trace] array too large to materialise, dropped: {}",
            from_expr(step.lhs));
        return step.value;
      }
    }
    step.value = build_rhs(smt_conv, step.rhs);
    if (lazy_debug)
      log_status(
        "[lazy-trace] rhs get lhs={} ({}) -> {}",
        from_expr(step.lhs),
        get_expr_id(step.lhs),
        is_nil_expr(step.value) ? std::string("<nil>")
                                : from_expr(step.value).substr(0, 60));
    if (
      is_nil_expr(step.value) && !is_nil_expr(step.ssa_lhs) &&
      (is_unsignedbv_type(step.ssa_lhs) || is_signedbv_type(step.ssa_lhs)))
      step.value = smt_conv.get(step.ssa_lhs);
  }
  catch (const type2t::symbolic_type_excp &)
  {
  }
  catch (const array_type2t::dyn_sized_array_excp &)
  {
  }
  return step.value;
}

expr2tc build_rhs(smt_convt &smt_conv, const expr2tc &rhs)
{
  if (is_nil_expr(rhs) || is_constant_expr(rhs))
    return rhs;

  auto new_rhs = smt_conv.get(rhs);
  renaming::renaming_levelt::get_original_name(new_rhs, symbol2t::level0);
  return new_rhs;
}

void build_goto_trace(
  const symex_target_equationt &target,
  smt_convt &smt_conv,
  goto_tracet &goto_trace,
  const bool &is_compact_trace)
{
  unsigned step_nr = 0;

  for (auto const &SSA_step : target.SSA_steps)
  {
    if (SSA_step.hidden && is_compact_trace)
      continue;

    if (!smt_conv.l_get(SSA_step.guard_ast).is_true())
      continue;

    goto_trace_stept goto_trace_step;

    goto_trace_step.thread_nr = SSA_step.source.thread_nr;
    goto_trace_step.pc = SSA_step.source.pc;
    goto_trace_step.comment = SSA_step.comment;
    goto_trace_step.original_lhs = SSA_step.original_lhs;
    goto_trace_step.type = SSA_step.type;
    goto_trace_step.step_nr = ++step_nr;
    goto_trace_step.format_string = SSA_step.format_string;

    goto_trace_step.stack_trace = SSA_step.stack_trace;

    if (SSA_step.is_assignment())
    {
      goto_trace_step.lhs = build_lhs(smt_conv, SSA_step.original_lhs);
      goto_trace_step.rhs = SSA_step.rhs;
      goto_trace_step.ssa_lhs = SSA_step.lhs;
      assert(!goto_trace_step.value);
      if (build_goto_trace_lazy_assignment_values)
      {
        goto_trace.steps.push_back(goto_trace_step);
        continue;
      }
      try
      {
        if (is_nil_expr(SSA_step.original_rhs))
          goto_trace_step.value = build_rhs(smt_conv, SSA_step.rhs);
        else
          goto_trace_step.value = build_rhs(smt_conv, SSA_step.original_rhs);
        // Try asking solver if value was not built
        if (
          !goto_trace_step.value &&
          (is_unsignedbv_type(SSA_step.lhs) || is_signedbv_type(SSA_step.lhs)))
          goto_trace_step.value = smt_conv.get(SSA_step.lhs);
      }
      catch (const type2t::symbolic_type_excp &e)
      {
        log_debug(
          "trace",
          "skipping assignment at {} (symbolic type)",
          SSA_step.source.pc->location.as_string());
        continue;
      }
      catch (const array_type2t::dyn_sized_array_excp &e)
      {
        log_debug(
          "trace",
          "skipping assignment at {} (symbolic-size array, e.g. argv)",
          SSA_step.source.pc->location.as_string());
        continue;
      }
    }

    if (SSA_step.is_output())
    {
      for (const auto &arg : SSA_step.converted_output_args)
      {
        if (is_constant_expr(arg))
          goto_trace_step.output_args.push_back(arg);
        else
          goto_trace_step.output_args.push_back(smt_conv.get(arg));
      }
    }

    if (SSA_step.is_assert() || SSA_step.is_assume() || SSA_step.is_branching())
      goto_trace_step.guard = !smt_conv.l_get(SSA_step.cond_ast).is_false();

    goto_trace.steps.push_back(goto_trace_step);
  }
}

void build_successful_goto_trace(
  const symex_target_equationt &target,
  const namespacet &ns,
  goto_tracet &goto_trace)
{
  unsigned step_nr = 0;
  for (const symex_target_equationt::SSA_stept &SSA_step : target.SSA_steps)
  {
    if (
      (SSA_step.is_assert() || SSA_step.is_assume()) &&
      (is_valid_witness_expr(ns, SSA_step.lhs)))
    {
      // When building the correctness witness, we only care about
      // asserts and assumes
      if (!(SSA_step.is_assert() || SSA_step.is_assume()))
        continue;

      goto_trace.steps.emplace_back();
      goto_trace_stept &goto_trace_step = goto_trace.steps.back();
      goto_trace_step.thread_nr = SSA_step.source.thread_nr;
      goto_trace_step.lhs = SSA_step.lhs;
      goto_trace_step.rhs = SSA_step.rhs;
      goto_trace_step.pc = SSA_step.source.pc;
      goto_trace_step.comment = SSA_step.comment;
      goto_trace_step.original_lhs = SSA_step.original_lhs;
      goto_trace_step.type = SSA_step.type;
      goto_trace_step.step_nr = step_nr++;
      goto_trace_step.format_string = SSA_step.format_string;
      goto_trace_step.stack_trace = SSA_step.stack_trace;
    }
  }
}
