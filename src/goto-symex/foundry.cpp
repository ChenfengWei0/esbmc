#include <goto-symex/foundry.h>
#include <goto-symex/slice.h>
#include <ac_config.h>
#include <util/prefix.h>
#include <util/mp_arith.h>
#include <util/message/format.h>
#include <util/std_code.h>
#include <util/std_expr.h>
#include <irep2/irep2_expr.h>
#include <fstream>
#include <set>
#include <unordered_set>
#include <algorithm>
#include <cctype>

// ---------------------------------------------------------------------------
// Symbol-name parsing
// ---------------------------------------------------------------------------

bool foundry_generator::parse_param_symbol(
  const std::string &raw,
  std::string &contract,
  std::string &method,
  std::string &param)
{
  // Strip SSA level/renaming suffixes: keep the base identifier only.
  std::string name = raw;
  size_t cut = name.find_first_of("?!&#");
  if (cut != std::string::npos)
    name.resize(cut);

  // Solidity function parameters are named
  // `sol:@C@<contract>@F@<method>@<param>`.
  const std::string pfx = "sol:@C@";
  if (!has_prefix(name, pfx))
    return false;
  std::string rest = name.substr(pfx.size());

  size_t f = rest.find("@F@");
  if (f == std::string::npos)
    return false;
  contract = rest.substr(0, f);
  std::string after = rest.substr(f + 3);

  size_t a = after.find('@');
  if (a == std::string::npos)
    return false;
  method = after.substr(0, a);
  param = after.substr(a + 1);

  // A genuine parameter is a single trailing identifier (no further scope
  // separators). Locals/temporaries carry extra `@`, contract fields do not
  // reach here (they are not `@F@`-scoped).
  if (
    param.empty() || param.find('@') != std::string::npos || contract.empty() ||
    method.empty())
    return false;
  return true;
}

// ---------------------------------------------------------------------------
// Value formatting
// ---------------------------------------------------------------------------

namespace
{
// The effective Solidity type string of a lowered parameter *type*. A fixed
// bytesN lowers to the `BytesStatic` struct (its `#sol_type` is the tag
// "BytesStatic"); when the source width survives as `#sol_bytesn_size`
// (set in get_elementary_type_name, solidity_convert_type.cpp) it is recovered
// as "BYTES<N>" so the value formatter can render the exact-width literal. All
// other types pass their `#sol_type` through unchanged. The width irep is often
// stripped from the type by type2tc migration before the generator runs; for
// that reason the authoritative source is the width stamped on the code_typet
// argument itself (see arg_sol_type / get_function_params), not this type.
std::string effective_sol_type(const typet &t)
{
  const typet &ty = t.is_pointer() ? t.subtype() : t;
  // A fixed bytesN carries its width as `#sol_bytesn_size`. Key off that
  // directly — the `#sol_type` tag ("BytesStatic") is not always present on the
  // lowered type across solc AST shapes, but the width is authoritative when
  // set.
  std::string sz = ty.get("#sol_bytesn_size").as_string();
  if (!sz.empty())
  {
    unsigned n = static_cast<unsigned>(std::stoul(sz));
    if (n >= 1 && n <= 32)
      return "BYTES" + std::to_string(n);
  }
  return ty.get("#sol_type").as_string();
}

// A `#sol_type` like "BYTES32" names a fixed-size bytesN (N in 1..32).
// "BytesDynamic"/"BytesStatic" (mixed case) are NOT fixed bytesN and must not
// match. Returns N, or 0 if `sol_type` is not a fixed bytesN.
unsigned parse_fixed_bytes_width(const std::string &sol_type)
{
  if (!has_prefix(sol_type, "BYTES") || sol_type.size() <= 5)
    return 0;
  if (!std::isdigit(static_cast<unsigned char>(sol_type[5])))
    return 0;
  unsigned n = static_cast<unsigned>(std::stoul(sol_type.substr(5)));
  return (n >= 1 && n <= 32) ? n : 0;
}

// Render a `bytesN` value recovered from the model as a Solidity literal
// `bytesN(0x..)`. bytesN is modeled as a BytesStatic struct
// { unsigned char data[32]; size_t length; } with `data` big-endian
// (data[0] = most-significant byte; see bytes_static_from_uint), which is
// exactly how solc lays out a bytesN literal, so emitting data[0..N-1] as hex
// round-trips to the same 32-byte value. Returns "" if the struct/array shape
// is not a fully-concrete constant.
std::string format_fixed_bytes(unsigned n, const expr2tc &value)
{
  if (!is_constant_struct2t(value))
    return "";
  const constant_struct2t &st = to_constant_struct2t(value);
  if (st.datatype_members.empty())
    return "";
  const expr2tc &data = st.datatype_members[0]; // the `data[32]` array

  static const char *hexd = "0123456789abcdef";
  std::string hex;
  auto append_byte = [&hex](const expr2tc &e) -> bool {
    if (!is_constant_int2t(e))
      return false;
    unsigned b =
      static_cast<unsigned>(to_constant_int2t(e).value.to_uint64() & 0xffu);
    hex.push_back(hexd[(b >> 4) & 0xf]);
    hex.push_back(hexd[b & 0xf]);
    return true;
  };

  if (is_constant_array2t(data))
  {
    const constant_array2t &arr = to_constant_array2t(data);
    for (unsigned i = 0; i < n; ++i)
    {
      if (
        i >= arr.datatype_members.size() ||
        !append_byte(arr.datatype_members[i]))
        return "";
    }
  }
  else if (is_constant_array_of2t(data))
  {
    // Whole array collapsed to one repeated element (e.g. all-zero).
    const expr2tc &init = to_constant_array_of2t(data).initializer;
    for (unsigned i = 0; i < n; ++i)
      if (!append_byte(init))
        return "";
  }
  else
    return "";

  return "bytes" + std::to_string(n) + "(0x" + hex + ")";
}

// Effective sol-type string of a code_typet argument. Prefers the fixed-bytes
// width stamped directly on the argument (`#sol_bytesn_size`, set in
// get_function_params) — this is the authoritative source-declared width and
// survives the type migration that strips it from the argument *type*. Falls
// back to the argument type's own tags. `arg` is a code_typet::argumentt
// (an exprt subtype), so both the direct irep and its type are queryable.
std::string arg_sol_type(const exprt &arg)
{
  const std::string bn = arg.get("#sol_bytesn_size").as_string();
  if (!bn.empty() && bn.find_first_not_of("0123456789") == std::string::npos)
  {
    unsigned n = static_cast<unsigned>(std::stoul(bn));
    if (n >= 1 && n <= 32)
      return "BYTES" + std::to_string(n);
  }
  return effective_sol_type(arg.type());
}
} // namespace

std::string foundry_generator::format_sol_value(
  const std::string &sol_type,
  const expr2tc &value)
{
  if (sol_type == "BOOL")
  {
    if (is_constant_bool2t(value))
      return to_constant_bool2t(value).value ? "true" : "false";
    if (is_constant_int2t(value))
      return (to_constant_int2t(value).value != 0) ? "true" : "false";
    return "";
  }

  // Fixed-size bytesN (bytes1..bytes32): recovered as a BytesStatic struct.
  // The width N MUST come from the declared source type (`#sol_type` =
  // "BYTES<N>"); we never infer it from the recovered struct's `.length`, which
  // is a free nondet value on any path that does not constrain the bytesN and
  // could otherwise yield a wrong-width literal for the declared parameter. If
  // the caller cannot supply the declared width, the value degrades to "" (the
  // call is then UNSUPPORTED) rather than risk a wrong-width/wrong-value test.
  if (unsigned n = parse_fixed_bytes_width(sol_type))
    return format_fixed_bytes(n, value);

  if (!is_constant_int2t(value))
    return "";
  const BigInt &n = to_constant_int2t(value).value;

  // Unsigned / signed integers: a decimal literal is always valid and exact.
  if (has_prefix(sol_type, "UINT") || has_prefix(sol_type, "INT"))
    return integer2string(n);

  // Address: go through uint160 so we never emit a 40-hex-digit literal
  // (which Solidity rejects unless EIP-55 checksummed).
  if (sol_type == "ADDRESS" || sol_type == "ADDRESS_PAYABLE")
    return "address(uint160(" + integer2string(n) + "))";

  // bytesN / string / aggregates: not faithfully renderable yet -> caller
  // marks the call unsupported rather than emit a wrong literal.
  return "";
}

std::string foundry_generator::default_sol_literal(const std::string &sol_type)
{
  if (sol_type == "BOOL")
    return "false";
  if (has_prefix(sol_type, "UINT") || has_prefix(sol_type, "INT"))
    return "0";
  if (sol_type == "ADDRESS" || sol_type == "ADDRESS_PAYABLE")
    return "address(0)";
  // Fixed-size bytesN not exercised on the path: the zero value is a valid,
  // faithful default (a sliced/unread bytesN cannot change branch reachability).
  if (unsigned n = parse_fixed_bytes_width(sol_type))
    return "bytes" + std::to_string(n) + "(0x" + std::string(2 * n, '0') + ")";
  return "";
}

const std::vector<std::pair<std::string, std::string>> &
foundry_generator::get_method_params(
  const namespacet &ns,
  const std::string &contract,
  const std::string &method) const
{
  std::string key = contract + "@" + method;
  auto it = method_params.find(key);
  if (it != method_params.end())
    return it->second;

  std::vector<std::pair<std::string, std::string>> params;
  const std::string fn_prefix = "sol:@C@" + contract + "@F@" + method;
  const symbolt *fn = nullptr;
  ns.get_context().foreach_operand([&](const symbolt &s) {
    if (fn || !s.type.is_code())
      return;
    const std::string id = s.id.as_string();
    if (!has_prefix(id, fn_prefix))
      return;
    // The function symbol is `<prefix>#<node-id>`; a parameter/local is
    // `<prefix>@<name>`. Reject the latter.
    if (id.size() > fn_prefix.size() && id[fn_prefix.size()] != '#')
      return;
    fn = &s;
  });

  if (fn)
  {
    const code_typet &ct = to_code_type(fn->type);
    for (const auto &arg : ct.arguments())
    {
      // The first argument of every contract method is the `this`
      // self-pointer synthesised by the frontend; it is not a source-level
      // parameter and must not be emitted.
      std::string pname = arg.get_base_name().as_string();
      if (pname.empty() || pname == "this")
        continue;
      // Source type: prefer the width stamped on the argument (arg_sol_type,
      // which also handles bytesN via `#sol_bytesn_size`), then the parameter
      // symbol (`sol:@C@<C>@F@<method>@<param>`) if the argument carries none.
      std::string stype = arg_sol_type(arg);
      if (stype.empty())
      {
        std::string pid = fn_prefix + "@" + pname;
        if (const symbolt *ps = ns.lookup(irep_idt(pid)))
          stype = ps->type.get("#sol_type").as_string();
      }
      params.emplace_back(pname, stype);
    }
  }

  return method_params.emplace(key, std::move(params)).first->second;
}

// Recurse a dispatcher body, recording every direct call to a contract method
// `sol:@C@<C>@F@<base>#<id>` (prefix `cpfx`) as base -> {ids}. Aux/modifier
// helpers the dispatcher never calls directly are naturally excluded.
static void collect_dispatch_calls(
  const exprt &e,
  const std::string &cpfx,
  std::map<std::string, std::vector<std::string>> &calls)
{
  // The frontend emits calls as `side_effect_expr_function_call` (id
  // "sideeffect", statement "function_call"); goto-lowered bodies use
  // `code_function_call`. Handle both; the callee is the function operand.
  const exprt *callee = nullptr;
  if (e.is_code() && to_code(e).get_statement() == "function_call")
    callee = &to_code_function_call(to_code(e)).function();
  else if (
    e.id() == "sideeffect" && e.get("statement") == "function_call" &&
    !e.operands().empty())
    callee = &e.op0();

  if (callee && callee->id() == "symbol")
  {
    const std::string id = callee->get("identifier").as_string();
    if (has_prefix(id, cpfx))
    {
      std::string rest = id.substr(cpfx.size());
      size_t h = rest.find('#');
      std::string base = h == std::string::npos ? rest : rest.substr(0, h);
      if (
        !base.empty() && base[0] != '_' && base[0] != '$' &&
        base.find('@') == std::string::npos)
      {
        auto &v = calls[base];
        if (std::find(v.begin(), v.end(), id) == v.end())
          v.push_back(id);
      }
    }
  }
  forall_operands (op, e)
    collect_dispatch_calls(*op, cpfx, calls);
}

const std::map<std::string, std::vector<std::string>> &
foundry_generator::dispatcher_callable(
  const namespacet &ns,
  const std::string &contract) const
{
  auto it = dispatcher_methods.find(contract);
  if (it != dispatcher_methods.end())
    return it->second;

  std::map<std::string, std::vector<std::string>> calls;
  const std::string disp_id =
    "sol:@C@" + contract + "@_ESBMC_Nondet_Extcall_" + contract + "#";
  if (const symbolt *disp = ns.lookup(irep_idt(disp_id)))
    collect_dispatch_calls(disp->value, "sol:@C@" + contract + "@F@", calls);

  return dispatcher_methods.emplace(contract, std::move(calls)).first->second;
}

// Declared parameters (source order, `#sol_type`) of an exact method symbol id,
// skipping the synthesised `this` self-pointer. Used to resolve overloads,
// where the base name alone is ambiguous.
static std::vector<std::pair<std::string, std::string>>
params_of_method_id(const namespacet &ns, const std::string &id)
{
  std::vector<std::pair<std::string, std::string>> params;
  const symbolt *fn = ns.lookup(irep_idt(id));
  if (!fn || !fn->type.is_code())
    return params;
  for (const auto &arg : to_code_type(fn->type).arguments())
  {
    std::string pname = arg.get_base_name().as_string();
    if (pname.empty() || pname == "this")
      continue;
    // Prefer the width stamped on the argument (arg_sol_type), then the
    // parameter symbol's type when the argument carries no source tag.
    std::string st = arg_sol_type(arg);
    if (st.empty())
    {
      const irep_idt &pid = arg.get_identifier();
      if (const symbolt *ps = !pid.empty() ? ns.lookup(pid) : nullptr)
        st = effective_sol_type(ps->type);
    }
    params.emplace_back(pname, st);
  }
  return params;
}

// Wrap an integer literal in an explicit typed cast so Solidity's overload
// resolution selects the intended overload; bool/address literals are already
// unambiguous and pass through.
static std::string
cast_for_overload(const std::string &sol_type, const std::string &lit)
{
  std::string ty;
  if (has_prefix(sol_type, "UINT"))
    ty = "uint" + sol_type.substr(4);
  else if (has_prefix(sol_type, "INT"))
    ty = "int" + sol_type.substr(3);
  else
    return lit;
  if (ty == "uint")
    ty = "uint256";
  if (ty == "int")
    ty = "int256";
  return ty + "(" + lit + ")";
}

// ---------------------------------------------------------------------------
// Reconstruction
// ---------------------------------------------------------------------------

// If `raw` is a dispatcher's FIRST nondet guard
// (`sol:@C@<C>@_ESBMC_Nondet_Extcall_<C>#::$tmp::return_value$_nondet_bool$1`),
// return <C>; else "". Each such guard marks the start of one transaction: the
// harness re-enters the dispatcher loop and re-evaluates guard #1.
static std::string dispatcher_tx_contract(const std::string &raw)
{
  const std::string mark = "_ESBMC_Nondet_Extcall_";
  size_t p = raw.find(mark);
  if (p == std::string::npos || p < 8 || !has_prefix(raw, "sol:@C@"))
    return "";
  if (raw[p - 1] != '@')
    return "";
  const std::string guard = "return_value$_nondet_bool$";
  size_t gp = raw.find(guard);
  if (gp == std::string::npos)
    return "";
  size_t d = gp + guard.size();
  std::string num;
  while (d < raw.size() && isdigit(static_cast<unsigned char>(raw[d])))
    num += raw[d++];
  if (num != "1")
    return "";
  return raw.substr(7, p - 1 - 7);
}

// Base method name a step is executing, from its source-location function. The
// Solidity frontend stores either the source name ("probe") or the mangled id
// (`sol:@C@<C>@F@<m>#..`); both reduce to the source method name.
static std::string
step_location_method(const symex_target_equationt::SSA_stept &step)
{
  std::string fn = step.source.pc->location.function().as_string();
  if (fn.empty())
    return "";
  if (has_prefix(fn, "sol:@C@"))
  {
    size_t f = fn.find("@F@");
    if (f == std::string::npos)
      return "";
    std::string after = fn.substr(f + 3);
    size_t cut = after.find_first_of("#@");
    return cut == std::string::npos ? after : after.substr(0, cut);
  }
  if (has_prefix(fn, "c::"))
    fn = fn.substr(3);
  return fn;
}

foundry_generator::test_case foundry_generator::reconstruct(
  const symex_target_equationt &target,
  smt_convt &smt_conv,
  const namespacet &ns) const
{
  // Resolve a recovered (contract, method, args) into a compilable call by
  // matching the method's DECLARED parameters in source order: a recovered
  // value fills its slot, an un-recovered slot (parameter sliced away because
  // it is irrelevant to the covered branch, or a short-circuited operand) takes
  // a type default — sound, since a sliced argument cannot change which branch
  // is reached — and an unrenderable type flags the call unsupported. This is
  // always arity-correct: never a wrong-arity call.
  auto build_call = [&](
                      const std::string &contract,
                      const std::string &method,
                      const std::map<std::string, sol_arg> &recovered) {
    sol_call out;
    out.contract = contract;
    out.method = method;

    // Resolve the exact method signature. For a dispatcher-callable method we
    // use its exact id (disambiguating overloads by which recovered parameter
    // names the candidate declares); a constructor or otherwise-unlisted method
    // falls back to base-name lookup.
    const auto &callable = dispatcher_callable(ns, contract);
    auto cit = callable.find(method);
    std::vector<std::pair<std::string, std::string>> decls;
    bool overloaded = false;

    if (cit != callable.end() && !cit->second.empty())
    {
      const auto &ids = cit->second;
      if (ids.size() == 1)
        decls = params_of_method_id(ns, ids.front());
      else
      {
        // Overloaded: pick the single candidate declaring every recovered
        // parameter name. No unique match (incl. no recovered args) -> we
        // cannot know which overload ran, so mark unsupported rather than guess.
        overloaded = true;
        std::string chosen;
        for (const auto &id : ids)
        {
          auto p = params_of_method_id(ns, id);
          bool ok = true;
          for (const auto &kv : recovered)
            if (std::none_of(p.begin(), p.end(), [&](const auto &d) {
                  return d.first == kv.first;
                }))
            {
              ok = false;
              break;
            }
          if (ok && !recovered.empty())
          {
            if (!chosen.empty())
            {
              chosen.clear(); // ambiguous
              break;
            }
            chosen = id;
            decls = std::move(p);
          }
        }
        if (chosen.empty())
        {
          for (const auto &kv : recovered)
            out.args.push_back(kv.second);
          out.supported = false;
          return out;
        }
      }
    }
    else
    {
      decls = get_method_params(ns, contract, method);
      if (decls.empty() && !recovered.empty())
      {
        // Unknown signature: keep recovered args but flag unsupported so we
        // never emit a possibly-wrong call shape.
        for (const auto &kv : recovered)
          out.args.push_back(kv.second);
        out.supported = false;
        return out;
      }
    }

    for (const auto &decl : decls)
    {
      sol_arg a;
      a.param = decl.first;
      a.sol_type = decl.second;
      auto it = recovered.find(decl.first);
      // Re-format any recovered value against the DECLARED type (decl.second),
      // which carries the authoritative source width — critical for bytesN,
      // whose width must be the declared width, not one inferred from the
      // recovered struct's `.length`. The recovery-site literal was computed
      // from the (possibly width-degraded) parameter symbol type, so only use
      // it as a fallback when re-formatting yields nothing.
      if (it != recovered.end() && it->second.value)
        a.literal = format_sol_value(decl.second, it->second.value);
      if (a.literal.empty() && it != recovered.end())
        a.literal = it->second.literal;
      if (a.literal.empty())
        a.literal = default_sol_literal(decl.second);
      if (a.literal.empty())
        out.supported = false;
      else if (overloaded)
        a.literal = cast_for_overload(decl.second, a.literal);
      out.args.push_back(a);
    }
    return out;
  };

  // Constructor arguments (assigned before the dispatcher loop) and one segment
  // per transaction. The harness constructs every contract up front, then loops
  // dispatcher invocations, each running at most one public method chosen by
  // its first nondet guard. Split at those guards: before the first is
  // construction; each later segment is one transaction whose method is the
  // first contract-method body that runs in it.
  std::map<std::string, std::map<std::string, sol_arg>> ctor_args;
  struct segment
  {
    std::string contract, method;
    std::map<std::string, sol_arg> args;
    bool reverts = false;
  };
  std::vector<segment> segs;

  for (auto const &step : target.SSA_steps)
  {
    if (!smt_conv.l_get(step.guard_ast).is_true())
      continue;

    // Revert fidelity: the covered branch-coverage claim is the one kept assert
    // whose guard is true on this path. When goto_coverage marked its edge as a
    // revert (a `require` failure / `revert CustomError(...)`), the transaction
    // active at this step reverts, so its call must be wrapped in
    // vm.expectRevert() to remain a passing Foundry test.
    if (
      step.is_assert() && !segs.empty() &&
      step.source.pc->location.get_bool("sol_revert_edge"))
      segs.back().reverts = true;

    const bool assign_sym =
      step.is_assignment() && is_symbol2t(step.original_lhs);
    const std::string lhs_id =
      assign_sym ? to_symbol2t(step.original_lhs).thename.as_string()
                 : std::string();

    // Transaction boundary: a fresh dispatcher invocation.
    if (assign_sym)
    {
      std::string txc = dispatcher_tx_contract(lhs_id);
      if (!txc.empty())
      {
        segs.push_back(segment{txc, "", {}});
        continue;
      }
    }

    // A recovered nondet parameter value.
    expr2tc nondet =
      assign_sym ? symex_slicet::get_nondet_symbol(step.rhs) : expr2tc();
    std::string c, m, p;
    if (nondet && is_symbol2t(nondet) && parse_param_symbol(lhs_id, c, m, p))
    {
      sol_arg a;
      a.param = p;
      if (const symbolt *ps = ns.lookup(irep_idt(lhs_id)))
        a.sol_type = effective_sol_type(ps->type);
      a.value = smt_conv.get(nondet);
      a.literal = format_sol_value(a.sol_type, a.value);

      if (segs.empty())
      {
        if (m == c) // constructor argument (method == contract name)
          ctor_args[c][p] = a;
      }
      else if (c == segs.back().contract)
      {
        segs.back().args[p] = a;
        // Only accept `m` as the transaction's method if the dispatcher can
        // actually call it. A recovered parameter may belong to a modifier/aux
        // helper (`<method>_<modifier>`) the dispatcher never enters directly;
        // its value still belongs to the real method, but its name must not be
        // emitted as the call. The real entry is set from source-location below.
        if (segs.back().method.empty() && dispatcher_callable(ns, c).count(m))
          segs.back().method = m;
      }
      continue;
    }

    // Otherwise fix the transaction's method from the first dispatcher-callable
    // body that executes in it (catches parameterless / sliced-argument calls,
    // and skips modifier/aux helpers that are not external entries).
    if (!segs.empty() && segs.back().method.empty())
    {
      const std::string base = step_location_method(step);
      if (dispatcher_callable(ns, segs.back().contract).count(base))
        segs.back().method = base;
    }
  }

  test_case calls;
  for (const auto &kv : ctor_args)
    calls.push_back(build_call(kv.first, kv.first, kv.second));
  for (const auto &s : segs)
    if (!s.method.empty()) // dispatcher chose no method on this path
    {
      sol_call c = build_call(s.contract, s.method, s.args);
      c.reverts = s.reverts;
      calls.push_back(std::move(c));
    }

  // Synthesise a defaulted constructor for any contract that was called but
  // whose parameterized constructor was not reconstructed, so `new C(...)`
  // still compiles (parameterless constructors need no call: `new C()`).
  std::set<std::string> used, has_ctor;
  for (const auto &c : calls)
  {
    used.insert(c.contract);
    if (c.method == c.contract)
      has_ctor.insert(c.contract);
  }
  for (const auto &cn : used)
    if (!has_ctor.count(cn) && !get_method_params(ns, cn, cn).empty())
      calls.push_back(build_call(cn, cn, {}));

  // Constructors precede the transactions that use their instance.
  std::stable_partition(calls.begin(), calls.end(), [](const sol_call &c) {
    return c.method == c.contract;
  });
  return calls;
}

// ---------------------------------------------------------------------------
// Collection
// ---------------------------------------------------------------------------

void foundry_generator::clear()
{
  std::lock_guard<std::mutex> lock(data_mutex);
  test_cases.clear();
  source_file.clear();
}

void foundry_generator::collect(
  const symex_target_equationt &target,
  smt_convt &smt_conv,
  const namespacet &ns)
{
  test_case tc = reconstruct(target, smt_conv, ns);
  if (tc.empty())
    return;

  std::lock_guard<std::mutex> lock(data_mutex);
  if (source_file.empty())
    source_file = config.options.get_option("input-file");
  test_cases.push_back(std::move(tc));
}

bool foundry_generator::has_tests() const
{
  std::lock_guard<std::mutex> lock(data_mutex);
  return !test_cases.empty();
}

std::string foundry_generator::fingerprint(const test_case &tc)
{
  std::string fp;
  for (const auto &call : tc)
  {
    if (call.reverts)
      fp += "revert:";
    fp += call.contract;
    fp += '.';
    fp += call.method;
    fp += '(';
    for (const auto &a : call.args)
    {
      fp += a.literal.empty() ? "?" : a.literal;
      fp += ',';
    }
    fp += ");";
  }
  return fp;
}

// ---------------------------------------------------------------------------
// Emission
// ---------------------------------------------------------------------------

static std::string file_stem(const std::string &path)
{
  size_t slash = path.find_last_of("/\\");
  std::string base = slash == std::string::npos ? path : path.substr(slash + 1);
  size_t dot = base.find('.');
  return dot == std::string::npos ? base : base.substr(0, dot);
}

void foundry_generator::write_foundry_file(
  const std::string &path,
  const std::string &primary,
  const std::vector<test_case> &cases) const
{
  auto join_args = [](const sol_call &call) {
    std::string s;
    for (size_t i = 0; i < call.args.size(); ++i)
      s += (i ? ", " : "") + call.args[i].literal;
    return s;
  };

  // A construction plan: one instance per distinct contract (sorted for a
  // stable var mapping shared across the group), built via its reconstructed
  // constructor call (or `new C()` when parameterless). A single dispatcher
  // drives one `_ESBMC_Object_<C>` across all txs, so one instance suffices.
  struct inst
  {
    std::string contract, var, ctor_args;
    bool buildable;
  };
  auto plan_of = [&](const test_case &tc) {
    std::map<std::string, const sol_call *> ctor;
    std::set<std::string> used;
    for (const auto &c : tc)
    {
      used.insert(c.contract);
      if (c.method == c.contract)
        ctor[c.contract] = &c;
    }
    std::vector<inst> plan;
    for (const auto &cn : used)
    {
      inst ib;
      ib.contract = cn;
      ib.var = "c" + std::to_string(plan.size());
      auto it = ctor.find(cn);
      ib.buildable = it == ctor.end() || it->second->supported;
      ib.ctor_args = it == ctor.end() ? std::string() : join_args(*it->second);
      plan.push_back(ib);
    }
    return plan;
  };
  auto sig_of = [&](const std::vector<inst> &plan) {
    std::string s;
    for (const auto &ib : plan)
      s += ib.contract + "(" + (ib.buildable ? ib.ctor_args : "!") + ");";
    return s;
  };

  // Group cases by construction signature, preserving first-seen order.
  std::vector<std::string> group_order;
  std::map<std::string, std::vector<const test_case *>> groups;
  for (const auto &tc : cases)
  {
    std::string s = sig_of(plan_of(tc));
    if (!groups.count(s))
      group_order.push_back(s);
    groups[s].push_back(&tc);
  }

  std::set<std::string> imports;
  for (const auto &tc : cases)
    for (const auto &call : tc)
      imports.insert(call.contract);

  // Import from the Solidity source (`--sol`), not the `.solast` AST input:
  // forge compiles `.sol`, and the two share a directory.
  std::string src_base = config.options.get_option("sol");
  if (src_base.empty())
    src_base = source_file;
  size_t slash = src_base.find_last_of("/\\");
  if (slash != std::string::npos)
    src_base = src_base.substr(slash + 1);

  std::ofstream f(path);
  f << "// SPDX-License-Identifier: MIT\n";
  f << "// Auto-generated by ESBMC " << ESBMC_VERSION << "\n";
  f << "// Foundry coverage test reconstructed from ESBMC counterexamples.\n";
  f << "pragma solidity >=0.8.0;\n\n";
  f << "import {Test} from \"forge-std/Test.sol\";\n";
  for (const auto &c : imports)
    f << "import {" << c << "} from \"./" << src_base << "\";\n";

  const bool multi = group_order.size() > 1;
  size_t gidx = 0, fn = 0;
  for (const auto &s : group_order)
  {
    const auto &grp = groups[s];
    const auto plan = plan_of(*grp.front());
    std::map<std::string, std::string> var;
    std::set<std::string> built;
    for (const auto &ib : plan)
    {
      var[ib.contract] = ib.var;
      if (ib.buildable)
        built.insert(ib.contract);
    }

    f << "\ncontract " << primary << "CovTest";
    if (multi)
      f << "_" << gidx;
    f << " is Test {\n";

    // State-variable instances, deployed once in setUp() — Foundry re-runs
    // setUp() before every test_cov_*, giving each a fresh construction.
    for (const auto &ib : plan)
      if (ib.buildable)
        f << "  " << ib.contract << " " << ib.var << ";\n";
    f << "  function setUp() public {\n";
    for (const auto &ib : plan)
      if (ib.buildable)
        f << "    " << ib.var << " = new " << ib.contract << "(" << ib.ctor_args
          << ");\n";
      else
        f << "    // UNSUPPORTED: constructor of " << ib.contract
          << " has an argument type ESBMC cannot yet render as a literal\n";
    f << "  }\n";

    for (const auto *tcp : grp)
    {
      f << "  function test_cov_" << fn++ << "() public {\n";
      for (const auto &call : *tcp)
      {
        if (call.method == call.contract)
          continue; // constructor -> setUp()
        if (!call.supported || !built.count(call.contract))
          f << "    // UNSUPPORTED: " << call.contract << "." << call.method
            << " has an argument type ESBMC cannot yet render as a literal\n";
        else
        {
          // The covered edge reverts (require failure / revert CustomError):
          // expect the revert so the assertion-free replay stays a PASS in
          // forge instead of aborting on the top-level revert. Bare
          // vm.expectRevert() matches any revert (faithful without pinning the
          // exact selector).
          if (call.reverts)
            f << "    vm.expectRevert();\n";
          f << "    " << var[call.contract] << "." << call.method << "("
            << join_args(call) << ");\n";
        }
      }
      f << "  }\n";
    }
    f << "}\n";
    ++gidx;
  }
}

// One file per contract-under-test; write_foundry_file then splits each file's
// cases into per-construction test contracts.
void foundry_generator::generate() const
{
  std::lock_guard<std::mutex> lock(data_mutex);
  if (test_cases.empty())
  {
    log_warning("No Foundry test cases collected. No *.t.sol generated.");
    return;
  }

  // Deduplicate structurally-identical reconstructions.
  std::unordered_set<std::string> seen;
  std::vector<test_case> unique;
  for (const auto &tc : test_cases)
    if (seen.insert(fingerprint(tc)).second)
      unique.push_back(tc);

  // Group by contract-under-test, preserving first-seen order.
  std::vector<std::string> order;
  std::map<std::string, std::vector<test_case>> by_primary;
  for (const auto &tc : unique)
  {
    std::string p = primary_contract(tc);
    if (p.empty())
      p = file_stem(source_file);
    if (!by_primary.count(p))
      order.push_back(p);
    by_primary[p].push_back(tc);
  }

  for (const auto &p : order)
  {
    const auto &cs = by_primary[p];
    std::string path = p + ".cov.t.sol";
    write_foundry_file(path, p, cs);
    log_status(
      "Generated Foundry coverage test with {} case(s): {}", cs.size(), path);
    // A reverting covered edge (require failure / revert CustomError) is
    // wrapped in vm.expectRevert() so the assertion-free replay stays a PASS
    // in forge; report the count so the wrapping is visible/testable.
    size_t revert_cases = 0;
    for (const auto &tc : cs)
      for (const auto &call : tc)
        if (call.reverts)
          ++revert_cases;
    if (revert_cases)
      log_status(
        "Foundry: {} call(s) wrapped in vm.expectRevert (reverting branch)",
        revert_cases);
  }
}

void foundry_generator::generate_single(
  const symex_target_equationt &target,
  smt_convt &smt_conv,
  const namespacet &ns)
{
  if (source_file.empty())
    source_file = config.options.get_option("input-file");

  test_case tc = reconstruct(target, smt_conv, ns);
  if (tc.empty())
  {
    log_warning(
      "No reconstructable transaction found. No Foundry test generated.");
    return;
  }

  std::string p = primary_contract(tc);
  if (p.empty())
    p = file_stem(source_file);
  std::string path = p + ".cov.t.sol";
  write_foundry_file(path, p, {tc});
  log_status("Generated Foundry test: {}", path);
}

std::string foundry_generator::primary_contract(const test_case &tc)
{
  for (const auto &c : tc)
    if (c.method != c.contract)
      return c.contract;
  return tc.empty() ? std::string() : tc.front().contract;
}
