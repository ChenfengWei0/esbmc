#include <goto-symex/foundry.h>
#include <goto-symex/slice.h>
#include <ac_config.h>
#include <util/prefix.h>
#include <util/mp_arith.h>
#include <util/message/format.h>
#include <irep2/irep2_expr.h>
#include <fstream>
#include <set>
#include <unordered_set>
#include <algorithm>

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
      // Source type from the argument's own `#sol_type`; fall back to the
      // parameter symbol (`sol:@C@<C>@F@<method>@<param>`) if absent.
      std::string stype = arg.type().get("#sol_type").as_string();
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

// ---------------------------------------------------------------------------
// Reconstruction
// ---------------------------------------------------------------------------

foundry_generator::test_case foundry_generator::reconstruct(
  const symex_target_equationt &target,
  smt_convt &smt_conv,
  const namespacet &ns) const
{
  test_case calls;
  sol_call current;
  bool have_current = false;

  // Finalize a grouped call: emit its arguments in DECLARED order, one per
  // declared parameter. A parameter recovered on the path uses its concrete
  // value; a parameter not exercised (e.g. a short-circuited operand) falls
  // back to a type default; any parameter of an unrenderable type makes the
  // whole call unsupported. This guarantees an arity-correct, compilable call
  // (or an explicit UNSUPPORTED marker) — never a wrong-arity call.
  auto flush = [&]() {
    if (!have_current || current.args.empty())
    {
      have_current = false;
      current = sol_call();
      return;
    }

    const auto &decls = get_method_params(ns, current.contract, current.method);

    sol_call out;
    out.contract = current.contract;
    out.method = current.method;

    if (decls.empty())
    {
      // Unknown signature (e.g. overload we could not resolve): fall back to
      // the recovered args as-is but flag unsupported so we never emit a
      // possibly-wrong call shape.
      out.args = current.args;
      out.supported = false;
    }
    else
    {
      for (const auto &decl : decls)
      {
        sol_arg a;
        a.param = decl.first;
        a.sol_type = decl.second;
        const sol_arg *rec = nullptr;
        for (const auto &c : current.args)
          if (c.param == decl.first)
          {
            rec = &c;
            break;
          }
        if (rec && !rec->literal.empty())
          a.literal = rec->literal;
        else
          a.literal = default_sol_literal(decl.second);
        if (a.literal.empty())
          out.supported = false;
        out.args.push_back(a);
      }
    }

    calls.push_back(out);
    have_current = false;
    current = sol_call();
  };

  for (auto const &step : target.SSA_steps)
  {
    if (!step.is_assignment())
      continue;
    if (!smt_conv.l_get(step.guard_ast).is_true())
      continue;

    // Only harness-injected nondet parameter assignments are of interest.
    expr2tc nondet = symex_slicet::get_nondet_symbol(step.rhs);
    if (!nondet || !is_symbol2t(nondet))
      continue;
    if (!is_symbol2t(step.original_lhs))
      continue;

    const std::string lhs_id =
      to_symbol2t(step.original_lhs).thename.as_string();
    std::string contract, method, param;
    if (!parse_param_symbol(lhs_id, contract, method, param))
      continue;

    // A new call starts when the method changes or a parameter repeats
    // (arguments of one call are assigned consecutively, in declaration
    // order, by symex_function before the body runs).
    bool repeat = false;
    for (const auto &a : current.args)
      if (a.param == param)
      {
        repeat = true;
        break;
      }
    if (
      have_current &&
      (current.contract != contract || current.method != method || repeat))
      flush();

    if (!have_current)
    {
      current.contract = contract;
      current.method = method;
      have_current = true;
    }

    // Recover the Solidity source type from the parameter symbol.
    std::string sol_type;
    if (const symbolt *psym = ns.lookup(irep_idt(lhs_id)))
      sol_type = psym->type.get("#sol_type").as_string();

    expr2tc val = smt_conv.get(nondet);
    sol_arg arg;
    arg.param = param;
    arg.sol_type = sol_type;
    arg.value = val;
    arg.literal = format_sol_value(sol_type, val);
    current.args.push_back(arg);
  }
  flush();

  // Every contract instance is built with `new C(...)`, so a contract whose
  // constructor declares parameters needs a constructor call (method ==
  // contract). If one was not reconstructed from the path (e.g. the ctor args
  // were not on a guard-true nondet assignment), synthesise it with defaults
  // so the emitted `new C(...)` still compiles; an unrenderable ctor-arg type
  // flags it unsupported (the instance is then dropped, never mis-built).
  std::set<std::string> used, has_ctor;
  for (const auto &c : calls)
  {
    used.insert(c.contract);
    if (c.method == c.contract)
      has_ctor.insert(c.contract);
  }
  for (const auto &cn : used)
  {
    if (has_ctor.count(cn))
      continue;
    const auto &cp = get_method_params(ns, cn, cn);
    if (cp.empty())
      continue; // parameterless constructor: `new C()` is correct
    sol_call ctor;
    ctor.contract = cn;
    ctor.method = cn;
    for (const auto &d : cp)
    {
      sol_arg a;
      a.param = d.first;
      a.sol_type = d.second;
      a.literal = default_sol_literal(d.second);
      if (a.literal.empty())
        ctor.supported = false;
      ctor.args.push_back(a);
    }
    calls.push_back(ctor);
  }
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
  const std::vector<test_case> &cases) const
{
  // Contracts that need importing (sorted for deterministic output).
  std::set<std::string> contracts;
  for (const auto &tc : cases)
    for (const auto &call : tc)
      contracts.insert(call.contract);

  std::string src_base = source_file;
  size_t slash = src_base.find_last_of("/\\");
  if (slash != std::string::npos)
    src_base = src_base.substr(slash + 1);

  std::ofstream f(path);
  f << "// SPDX-License-Identifier: MIT\n";
  f << "// Auto-generated by ESBMC " << ESBMC_VERSION << "\n";
  f << "// Foundry coverage test reconstructed from ESBMC counterexamples.\n";
  f << "pragma solidity >=0.8.0;\n\n";
  f << "import {Test} from \"forge-std/Test.sol\";\n";
  for (const auto &c : contracts)
    f << "import {" << c << "} from \"./" << src_base << "\";\n";
  f << "\ncontract " << file_stem(source_file) << "CovTest is Test {\n";

  auto join_args = [](const sol_call &call) {
    std::string s;
    for (size_t i = 0; i < call.args.size(); ++i)
    {
      if (i)
        s += ", ";
      s += call.args[i].literal;
    }
    return s;
  };

  size_t idx = 0;
  for (const auto &tc : cases)
  {
    f << "  function test_cov_" << idx << "() public {\n";

    // Constructor call (method == contract) carries the `new C(...)` args.
    std::map<std::string, const sol_call *> ctor;
    for (const auto &call : tc)
      if (call.method == call.contract)
        ctor[call.contract] = &call;

    // One instance per distinct contract, shared across the call sequence: a
    // multi-transaction counterexample drives a single `_ESBMC_Object_<C>`
    // across txs, so every call targets the same `new C(...)`. An instance is
    // built only if its constructor args are renderable; otherwise its calls
    // are emitted as UNSUPPORTED comments.
    std::map<std::string, std::string> instance;
    for (const auto &call : tc)
    {
      if (call.method == call.contract || instance.count(call.contract))
        continue;
      auto ci = ctor.find(call.contract);
      if (ci != ctor.end() && !ci->second->supported)
        continue; // constructor args unrenderable -> cannot build instance
      std::string var = "c" + std::to_string(instance.size());
      instance[call.contract] = var;
      f << "    " << call.contract << " " << var << " = new " << call.contract
        << "(" << (ci != ctor.end() ? join_args(*ci->second) : std::string())
        << ");\n";
    }

    for (const auto &call : tc)
    {
      if (call.method == call.contract)
        continue; // constructor, already emitted in the `new C(...)`
      if (!call.supported || !instance.count(call.contract))
      {
        f << "    // UNSUPPORTED: " << call.contract << "." << call.method
          << " has an argument type ESBMC cannot yet render as a literal\n";
        continue;
      }
      f << "    " << instance[call.contract] << "." << call.method << "("
        << join_args(call) << ");\n";
    }
    f << "  }\n";
    ++idx;
  }
  f << "}\n";
}

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

  std::string path = file_stem(source_file) + ".cov.t.sol";
  write_foundry_file(path, unique);
  log_status(
    "Generated Foundry coverage test with {} case(s): {}", unique.size(), path);
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

  std::string path = file_stem(source_file) + ".cov.t.sol";
  write_foundry_file(path, {tc});
  log_status("Generated Foundry test: {}", path);
}
