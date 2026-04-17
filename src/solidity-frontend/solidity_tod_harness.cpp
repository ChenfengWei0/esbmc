#include <solidity-frontend/solidity_tod_harness.h>
#include <solidity-frontend/solidity_tod_analysis.h>
#include <util/message.h>
#include <map>
#include <set>
#include <sstream>
#include <regex>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Parse a Solidity AST "src" field ("offset:length:fileIndex") and
/// return the substring of the original source.
static std::string extract_src(
  const std::string &source,
  const std::string &src_field)
{
  auto colon1 = src_field.find(':');
  auto colon2 = src_field.find(':', colon1 + 1);
  if (colon1 == std::string::npos || colon2 == std::string::npos)
    return {};
  size_t offset = std::stoull(src_field.substr(0, colon1));
  size_t length = std::stoull(src_field.substr(colon1 + 1, colon2 - colon1 - 1));
  if (offset + length > source.size())
    return {};
  return source.substr(offset, length);
}

/// Find a ContractDefinition node by name in the top-level AST.
static const nlohmann::json *find_contract(
  const nlohmann::json &ast,
  const std::string &name)
{
  if (!ast.contains("nodes"))
    return nullptr;
  for (const auto &node : ast["nodes"])
  {
    if (
      node.value("nodeType", "") == "ContractDefinition" &&
      node.value("name", "") == name)
      return &node;
  }
  return nullptr;
}

/// Find a FunctionDefinition inside a ContractDefinition by name.
static const nlohmann::json *find_function(
  const nlohmann::json &contract,
  const std::string &name)
{
  if (!contract.contains("nodes"))
    return nullptr;
  for (const auto &node : contract["nodes"])
  {
    if (
      node.value("nodeType", "") == "FunctionDefinition" &&
      node.value("name", "") == name)
      return &node;
  }
  return nullptr;
}

/// Map a Solidity typeString (from typeDescriptions) to a Solidity source
/// type name.  Handles common types; falls back to the raw typeString for
/// anything exotic.
static std::string soltype_to_source(const std::string &ts)
{
  // "uint256" → "uint256", "address" → "address", etc.
  // "contract Foo" → "Foo"
  if (ts.substr(0, 9) == "contract ")
    return ts.substr(9);
  // "struct Foo.Bar" → keep as-is (rare in public interfaces)
  // "enum Foo.Bar" → keep as-is
  // mapping(...) — not representable as a function param; skip
  if (ts.substr(0, 7) == "mapping")
    return {};
  // "bytes32", "bool", "string", "int256", etc. — pass through
  return ts;
}

/// Prefix parameter names to avoid collisions between funcA and funcB.
/// Returns e.g. "uint256 a_a, address a_b".
static std::string emit_prefixed_params(
  const nlohmann::json &func_def,
  const std::string &prefix)
{
  std::string out;
  if (
    !func_def.contains("parameters") ||
    !func_def["parameters"].contains("parameters"))
    return out;
  bool first = true;
  for (const auto &p : func_def["parameters"]["parameters"])
  {
    std::string ts =
      p.value("typeDescriptions", nlohmann::json{}).value("typeString", "");
    std::string name = p.value("name", "");
    std::string st = soltype_to_source(ts);
    if (st.empty())
      continue;
    if (!first)
      out += ", ";
    first = false;
    out += st + " " + prefix + "_" + name;
  }
  return out;
}

/// Emit prefixed argument names: "a_x, a_y".
static std::string emit_prefixed_args(
  const nlohmann::json &func_def,
  const std::string &prefix)
{
  std::string out;
  if (
    !func_def.contains("parameters") ||
    !func_def["parameters"].contains("parameters"))
    return out;
  bool first = true;
  for (const auto &p : func_def["parameters"]["parameters"])
  {
    std::string name = p.value("name", "");
    if (name.empty())
      continue;
    if (!first)
      out += ", ";
    first = false;
    out += prefix + "_" + name;
  }
  return out;
}

/// Collect public state variable names and types for assertion generation.
struct StateVar
{
  std::string name;
  std::string type_string;
  bool is_mapping;
  int id; // AST id, used to detect references from function bodies
  std::vector<std::string> mapping_key_types; // for (nested) mappings
};

/// Recursively walk an AST node and collect every `referencedDeclaration`
/// integer.  This captures any identifier whose binding points to another
/// declaration (state vars, locals, functions, modifiers, ...).
static void collect_referenced_decls(
  const nlohmann::json &node,
  std::set<int> &ids)
{
  if (node.is_object())
  {
    auto it = node.find("referencedDeclaration");
    if (it != node.end() && it->is_number_integer())
      ids.insert(it->get<int>());
    for (auto kv = node.begin(); kv != node.end(); ++kv)
      collect_referenced_decls(kv.value(), ids);
  }
  else if (node.is_array())
  {
    for (const auto &child : node)
      collect_referenced_decls(child, ids);
  }
}

/// Walk a Mapping typeName node to collect all key types.
static void collect_mapping_keys(
  const nlohmann::json &type_name,
  std::vector<std::string> &keys)
{
  if (type_name.value("nodeType", "") != "Mapping")
    return;
  if (type_name.contains("keyType"))
    keys.push_back(
      type_name["keyType"]
        .value("typeDescriptions", nlohmann::json{})
        .value("typeString", ""));
  if (type_name.contains("valueType"))
    collect_mapping_keys(type_name["valueType"], keys);
}

static std::vector<StateVar> collect_public_state_vars(
  const nlohmann::json &contract)
{
  std::vector<StateVar> vars;
  if (!contract.contains("nodes"))
    return vars;
  for (const auto &node : contract["nodes"])
  {
    if (node.value("nodeType", "") != "VariableDeclaration")
      continue;
    if (!node.value("stateVariable", false))
      continue;
    if (node.value("visibility", "") != "public")
      continue;

    StateVar sv;
    sv.name = node.value("name", "");
    sv.type_string =
      node.value("typeDescriptions", nlohmann::json{}).value("typeString", "");
    sv.is_mapping = false;
    sv.id = node.value("id", -1);

    if (
      node.contains("typeName") &&
      node["typeName"].value("nodeType", "") == "Mapping")
    {
      sv.is_mapping = true;
      collect_mapping_keys(node["typeName"], sv.mapping_key_types);
    }

    if (!sv.name.empty())
      vars.push_back(sv);
  }
  return vars;
}

/// Collect parameters by type from a function (prefixed names).
static std::vector<std::string> collect_params_by_type(
  const nlohmann::json &func_def,
  const std::string &prefix,
  const std::string &type_string)
{
  std::vector<std::string> keys;
  if (
    !func_def.contains("parameters") ||
    !func_def["parameters"].contains("parameters"))
    return keys;
  for (const auto &p : func_def["parameters"]["parameters"])
  {
    std::string ts =
      p.value("typeDescriptions", nlohmann::json{}).value("typeString", "");
    std::string name = p.value("name", "");
    if (ts == type_string && !name.empty())
      keys.push_back(prefix + "_" + name);
  }
  return keys;
}

/// Rename all occurrences of `old_name` as a whole word in `text`.
static std::string rename_contract(
  const std::string &text,
  const std::string &old_name,
  const std::string &new_name)
{
  // Use word-boundary regex to avoid false matches in substrings.
  std::regex re("\\b" + old_name + "\\b");
  return std::regex_replace(text, re, new_name);
}

/// Inject a synthetic `function __tod_bal() public view returns (uint) {
/// return address(this).balance; }` immediately before the contract's
/// closing `}`.
///
/// Needed because the TOD harness uses `new`-allocated dynamic instances
/// (c1 / c2), but ESBMC's Two-Copy Rename routes method calls through
/// the renamed contract's *singleton* (_ESBMC_Object_V_C1) — state
/// reads on the dynamic pointer (`c1->$balance`) therefore see a
/// never-updated nondet value, while the singleton tracks the real
/// balance.  By asserting equality through a call (`c1.__tod_bal()`)
/// we go through the dispatch; inside the callee `address(this).balance`
/// resolves against `this = &singleton` (see the AddressMemberCall fix
/// in solidity_convert_expr.cpp) and returns the up-to-date value.
static std::string inject_tod_bal_getter(const std::string &contract_src)
{
  auto last_brace = contract_src.rfind('}');
  if (last_brace == std::string::npos)
    return contract_src;
  // Inject TWO synthetic getters:
  //   __tod_bal()  -> singleton's $balance (via address(this).balance)
  //   __tod_addr() -> singleton's $address (via address(this))
  // Both go through the contract-call dispatch so the result reflects
  // the singleton state, not the dynamic-struct field accessed
  // directly via `c1->$balance` / `address(c1)` from outside.  The
  // address getter is needed for the harness's isolation guards:
  // `address(c1)` in the harness reads c1's dynamic $address (one
  // nondet), while the transfer dispatcher matches on the singleton's
  // $address (a different nondet).  Routing the require() comparison
  // through __tod_addr() makes both sides see the SAME (singleton)
  // address.
  std::string injected =
    "\n    function __tod_bal() public view returns (uint) { "
    "return address(this).balance; }\n"
    "    function __tod_addr() public view returns (address) { "
    "return address(this); }\n";
  return contract_src.substr(0, last_brace) + injected +
         contract_src.substr(last_brace);
}

/// Extract constructor parameters from a ContractDefinition.
static const nlohmann::json *find_constructor(const nlohmann::json &contract)
{
  if (!contract.contains("nodes"))
    return nullptr;
  for (const auto &node : contract["nodes"])
  {
    if (
      node.value("nodeType", "") == "FunctionDefinition" &&
      node.value("kind", "") == "constructor")
      return &node;
  }
  return nullptr;
}

/// Walk `node` recursively and return true if any subtree contains a
/// revert-inducing construct: an explicit `require(...)` / `revert(...)` /
/// `assert(...)` call, a `revert SomeError(...)` statement, or an explicit
/// throw.  Used to decide whether to wrap a call-site in `try/catch` in the
/// TOD harness — non-reverting callees get a raw call to avoid the nondet
/// "catch arm" over-approximating real EVM behavior.
///
/// Limitation: this is a conservative SYNTACTIC check.  It does NOT detect
/// reverts arising from overflow, failed external calls, or callees of
/// callees.  For the callee with overflow-checked arithmetic in 0.8+, a raw
/// call is still "safe enough" here because the overflow path also emits
/// `assume(false)` which prunes just that path — the harness test()
/// continues along the non-overflow path as long as at least one ordering
/// keeps running.  Reverts inside *callees-of-callees* are the known soft
/// spot and are still handled by the try/catch path whenever the top-level
/// function has any syntactic revert construct.
static bool body_may_revert_explicitly(const nlohmann::json &node)
{
  if (node.is_object())
  {
    const std::string nt = node.value("nodeType", "");
    if (nt == "FunctionCall" && node.contains("expression"))
    {
      const auto &expr = node["expression"];
      if (expr.is_object() && expr.value("nodeType", "") == "Identifier")
      {
        const std::string name = expr.value("name", "");
        if (name == "require" || name == "revert" || name == "assert")
          return true;
      }
    }
    if (nt == "RevertStatement" || nt == "Throw")
      return true;
    for (auto it = node.begin(); it != node.end(); ++it)
    {
      if (body_may_revert_explicitly(it.value()))
        return true;
    }
  }
  else if (node.is_array())
  {
    for (const auto &el : node)
      if (body_may_revert_explicitly(el))
        return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Per-pair harness contract emission (used by both single and multi)
// ---------------------------------------------------------------------------

/// Emit a single `contract TOD_<a>_<b> { function test(...) { ... } }` block
/// to `out`, using the V_C1/V_C2 deployment names supplied by the caller.
/// Returns false on lookup error (function missing); the caller can decide
/// whether to abort or continue with the remaining pairs.
///
/// `rw_by_name` is the call-graph-closed R/W footprint computed once per
/// contract by the caller.  Targeted assertions use it so that vars touched
/// only via internal helpers still get asserted.
static bool emit_harness_contract(
  std::ostringstream &out,
  const nlohmann::json &cdef,
  const std::vector<StateVar> &all_state_vars,
  const std::map<std::string, solidity_tod::RWSet> &rw_by_name,
  const nlohmann::json *ctor,
  const std::string &c1_name,
  const std::string &c2_name,
  const std::string &func_a,
  const std::string &func_b)
{
  const nlohmann::json *fa = find_function(cdef, func_a);
  const nlohmann::json *fb = find_function(cdef, func_b);
  if (!fa || !fb)
  {
    log_error(
      "TOD harness: function '{}' or '{}' not found",
      func_a,
      func_b);
    return false;
  }

  // Phase 3: keep only state vars referenced by BOTH functions, using the
  // call-graph-closed footprint so writes hidden in internal helpers count.
  std::set<int> footprint_a, footprint_b;
  auto it_a = rw_by_name.find(func_a);
  if (it_a != rw_by_name.end())
  {
    footprint_a.insert(it_a->second.reads.begin(), it_a->second.reads.end());
    footprint_a.insert(it_a->second.writes.begin(), it_a->second.writes.end());
  }
  auto it_b = rw_by_name.find(func_b);
  if (it_b != rw_by_name.end())
  {
    footprint_b.insert(it_b->second.reads.begin(), it_b->second.reads.end());
    footprint_b.insert(it_b->second.writes.begin(), it_b->second.writes.end());
  }
  std::set<int> shared_ids;
  for (int id : footprint_a)
    if (footprint_b.count(id))
      shared_ids.insert(id);

  std::vector<StateVar> shared_vars;
  std::vector<std::string> skipped_vars;
  for (const auto &sv : all_state_vars)
  {
    if (sv.id >= 0 && shared_ids.count(sv.id))
      shared_vars.push_back(sv);
    else
      skipped_vars.push_back(sv.name);
  }
  // Phase 4: TOD-Balance.  If the closed footprint puts the virtual
  // __balance token in the shared set, both functions touch ETH balance —
  // emit `assert(address(c1).balance == address(c2).balance)`.
  bool emit_balance_assert =
    shared_ids.count(solidity_tod::kBalanceId) != 0;

  // Constructor params
  std::string ctor_params_decl;
  std::string ctor_args;
  if (ctor)
  {
    ctor_params_decl = emit_prefixed_params(*ctor, "ctor");
    ctor_args = emit_prefixed_args(*ctor, "ctor");
  }

  // Function params (prefixed to avoid name collisions).
  std::string fa_params = emit_prefixed_params(*fa, "a");
  std::string fb_params = emit_prefixed_params(*fb, "b");
  std::string fa_args = emit_prefixed_args(*fa, "a");
  std::string fb_args = emit_prefixed_args(*fb, "b");

  // For each mapping key type, gather all matching params from both funcs.
  std::map<std::string, std::vector<std::string>> params_by_type;
  for (const auto &sv : shared_vars)
  {
    if (!sv.is_mapping)
      continue;
    for (const auto &kt : sv.mapping_key_types)
    {
      if (params_by_type.count(kt))
        continue;
      auto ka = collect_params_by_type(*fa, "a", kt);
      auto kb = collect_params_by_type(*fb, "b", kt);
      std::vector<std::string> all;
      all.insert(all.end(), ka.begin(), ka.end());
      all.insert(all.end(), kb.begin(), kb.end());
      params_by_type[kt] = all;
    }
  }

  // Per-pair audit comment block.
  out << "// ----- " << func_a << " vs " << func_b << " -----\n";
  out << "// Targeted state variables (referenced by BOTH functions):";
  if (shared_vars.empty())
    out << " <none>\n";
  else
  {
    out << "\n";
    for (const auto &sv : shared_vars)
      out << "//   - " << sv.name << "\n";
  }
  if (!skipped_vars.empty())
  {
    out << "// Skipped (touched by at most one function):\n";
    for (const auto &n : skipped_vars)
      out << "//   - " << n << "\n";
  }
  if (emit_balance_assert)
    out << "// Plus: address(this).balance (TOD-Balance check)\n";

  std::string harness_name = "TOD_" + func_a + "_" + func_b;
  // For TOD-Balance with a payable constructor, fund both copies with the
  // same nondet initial balance so transfers in the function bodies don't
  // get pruned by the `balance < value -> assume(false)` revert model.
  // Requires the test() function itself to be payable so msg.value can
  // cover both `new C{value: __initBal}()` calls.
  bool ctor_payable =
    ctor && ctor->value("stateMutability", "") == "payable";
  bool fund_copies = emit_balance_assert && ctor_payable;

  out << "contract " << harness_name << " {\n";
  out << "    function test(\n";

  std::vector<std::string> all_params;
  if (fund_copies)
    all_params.push_back("uint __initBal");
  if (!ctor_params_decl.empty())
    all_params.push_back(ctor_params_decl);
  if (!fa_params.empty())
    all_params.push_back(fa_params);
  if (!fb_params.empty())
    all_params.push_back(fb_params);

  for (size_t i = 0; i < all_params.size(); ++i)
  {
    out << "        " << all_params[i];
    if (i + 1 < all_params.size())
      out << ",";
    out << "\n";
  }
  out << "    ) public" << (fund_copies ? " payable {\n" : " {\n");

  if (fund_copies)
  {
    out << "        " << c1_name << " c1 = new " << c1_name
        << "{value: __initBal}(" << ctor_args << ");\n";
    out << "        " << c2_name << " c2 = new " << c2_name
        << "{value: __initBal}(" << ctor_args << ");\n\n";
  }
  else
  {
    out << "        " << c1_name << " c1 = new " << c1_name << "("
        << ctor_args << ");\n";
    out << "        " << c2_name << " c2 = new " << c2_name << "("
        << ctor_args << ");\n\n";
  }

  // For TOD-Balance, exclude harness-internal addresses from any
  // address-typed parameter so the two copies stay isolated under
  // ETH transfers.  Without this guard, e.g. a_to == address(c1)
  // would route c2's payment back into c1, breaking the symmetry the
  // harness relies on.
  if (emit_balance_assert)
  {
    auto collect_addr_params = [](const nlohmann::json &fdef,
                                  const std::string &prefix,
                                  std::vector<std::string> &out_list) {
      if (!fdef.contains("parameters") ||
          !fdef["parameters"].contains("parameters"))
        return;
      for (const auto &p : fdef["parameters"]["parameters"])
      {
        const std::string ts = p.value("typeDescriptions", nlohmann::json{})
                                 .value("typeString", "");
        const std::string name = p.value("name", "");
        if (
          (ts == "address" || ts == "address payable") && !name.empty())
          out_list.push_back(prefix + "_" + name);
      }
    };
    std::vector<std::string> addr_params;
    collect_addr_params(*fa, "a", addr_params);
    collect_addr_params(*fb, "b", addr_params);
    if (ctor)
      collect_addr_params(*ctor, "ctor", addr_params);
    if (!addr_params.empty())
    {
      // Cache the singleton-side addresses through __tod_addr() once;
      // each subsequent require() against the cached locals is just an
      // address compare.
      out << "        address __c1_addr = c1.__tod_addr();\n";
      out << "        address __c2_addr = c2.__tod_addr();\n";
      for (const auto &p : addr_params)
      {
        out << "        require(" << p << " != __c1_addr && " << p
            << " != __c2_addr, \"isolate copies\");\n";
      }
      out << "\n";
    }
  }

  // Call-site wrapping: for a callee with an explicit `require`/`revert`/
  // `assert`, wrap in `try/catch` so a revert in the first call does NOT
  // abort `test()` and swallow the equality assertions (which would yield
  // a vacuous VERIFICATION SUCCESSFUL on require-guarded TODs).  For a
  // callee with NO syntactic revert, keep the raw call — the catch arm is
  // modelled nondet by ESBMC's `TryStatement` converter and would
  // over-approximate into false TOD reports (the `tod_balance_pass` case,
  // where both calls always succeed but a phantom catch path creates a
  // state mismatch).  Trade-off: catches reverts in explicit-guard bodies
  // only; reverts arising from overflow or nested callees still ride on
  // the raw call's `assume(false)` path pruning, which is unchanged.
  const bool wrap_a = body_may_revert_explicitly(*fa);
  const bool wrap_b = body_may_revert_explicitly(*fb);
  auto emit_call = [&](std::ostringstream &o, const std::string &c,
                       const std::string &fn, const std::string &args,
                       bool wrap) {
    if (wrap)
      o << "        try " << c << "." << fn << "(" << args
        << ") {} catch {}\n";
    else
      o << "        " << c << "." << fn << "(" << args << ");\n";
  };

  out << "        // Order 1: " << func_a << " then " << func_b << "\n";
  emit_call(out, "c1", func_a, fa_args, wrap_a);
  emit_call(out, "c1", func_b, fb_args, wrap_b);
  out << "\n";

  out << "        // Order 2: " << func_b << " then " << func_a << "\n";
  emit_call(out, "c2", func_b, fb_args, wrap_b);
  emit_call(out, "c2", func_a, fa_args, wrap_a);
  out << "\n";

  out << "        // State comparison — if any assert fails, TOD exists\n";
  for (const auto &sv : shared_vars)
  {
    if (sv.is_mapping && !sv.mapping_key_types.empty())
    {
      std::vector<std::vector<std::string>> key_sets;
      bool skip = false;
      for (const auto &kt : sv.mapping_key_types)
      {
        if (!params_by_type.count(kt) || params_by_type[kt].empty())
        {
          skip = true;
          break;
        }
        key_sets.push_back(params_by_type[kt]);
      }
      if (skip)
      {
        out << "        // Skipped: " << sv.name
            << " (no matching key params)\n";
        continue;
      }

      if (key_sets.size() == 1)
      {
        for (const auto &k : key_sets[0])
          out << "        assert(c1." << sv.name << "(" << k << ")"
              << " == c2." << sv.name << "(" << k << "));\n";
      }
      else if (key_sets.size() == 2)
      {
        for (const auto &k1 : key_sets[0])
          for (const auto &k2 : key_sets[1])
            out << "        assert(c1." << sv.name << "(" << k1 << ", "
                << k2 << ") == c2." << sv.name << "(" << k1 << ", "
                << k2 << "));\n";
      }
      else
      {
        out << "        // Skipped: " << sv.name
            << " (deeply nested mapping, " << key_sets.size()
            << " levels)\n";
      }
    }
    else if (!sv.is_mapping)
    {
      out << "        assert(c1." << sv.name << "()"
          << " == c2." << sv.name << "());\n";
    }
  }
  if (emit_balance_assert)
    out << "        assert(c1.__tod_bal() == c2.__tod_bal());\n";

  out << "    }\n";
  out << "}\n\n";

  if (shared_vars.empty() && !emit_balance_assert)
    log_warning(
      "TOD harness: no public state variable nor ETH balance is touched "
      "by both '{}' and '{}'.  The harness contains no equality "
      "assertion; verification will trivially succeed.",
      func_a,
      func_b);
  return true;
}

// ---------------------------------------------------------------------------
// Top-level generators
// ---------------------------------------------------------------------------

std::string generate_tod_harness_multi(
  const std::string &sol_source,
  const nlohmann::json &ast,
  const std::string &contract,
  const std::vector<std::pair<std::string, std::string>> &pairs)
{
  if (pairs.empty())
  {
    log_error("TOD harness: no function pairs supplied");
    return {};
  }

  const nlohmann::json *cdef = find_contract(ast, contract);
  if (!cdef)
  {
    log_error("TOD harness: contract '{}' not found in AST", contract);
    return {};
  }

  std::string contract_src = extract_src(sol_source, cdef->value("src", ""));
  if (contract_src.empty())
  {
    log_error("TOD harness: cannot extract contract source text");
    return {};
  }

  std::string c1_name = contract + "_C1";
  std::string c2_name = contract + "_C2";
  std::string copy1 = rename_contract(contract_src, contract, c1_name);
  std::string copy2 = rename_contract(contract_src, contract, c2_name);

  auto state_vars = collect_public_state_vars(*cdef);
  const nlohmann::json *ctor = find_constructor(*cdef);
  // Compute closed R/W footprints once per contract; emit_harness_contract
  // re-uses this map for every (a, b) pair.
  auto rw_by_name = solidity_tod::compute_rw_sets(*cdef);

  // If ANY pair will emit a balance assertion, inject the __tod_bal()
  // view-getter into both renamed copies.  Cheap enough to always inject
  // when any pair touches balance — no need to inject per-pair.
  bool any_balance = false;
  for (const auto &p : pairs)
  {
    auto it_a = rw_by_name.find(p.first);
    auto it_b = rw_by_name.find(p.second);
    bool a_bal = it_a != rw_by_name.end() &&
                 (it_a->second.reads.count(solidity_tod::kBalanceId) ||
                  it_a->second.writes.count(solidity_tod::kBalanceId));
    bool b_bal = it_b != rw_by_name.end() &&
                 (it_b->second.reads.count(solidity_tod::kBalanceId) ||
                  it_b->second.writes.count(solidity_tod::kBalanceId));
    if (a_bal && b_bal)
    {
      any_balance = true;
      break;
    }
  }
  if (any_balance)
  {
    copy1 = inject_tod_bal_getter(copy1);
    copy2 = inject_tod_bal_getter(copy2);
  }

  std::ostringstream out;
  out << "// Auto-generated TOD (Transaction Order Dependence) harness\n";
  out << "// Contract: " << contract << "\n";
  out << "// Pairs (" << pairs.size() << "):\n";
  for (const auto &p : pairs)
    out << "//   - " << p.first << " vs " << p.second << "\n";
  out << "//\n";
  out << "// Verify each TOD_<a>_<b> contract with:\n";
  out << "//   esbmc <this-file>.sol --contract TOD_<a>_<b> "
      << "--bound --no-standard-checks --unwind 2 "
      << "--no-unwinding-assertions\n";
  out << "// Or let ESBMC drive all pairs via --tod-auto.\n\n";
  out << "// SPDX-License-Identifier: MIT\n";
  out << "pragma solidity >=0.8.0;\n\n";

  out << "// ===== Copy 1 =====\n";
  out << copy1 << "\n\n";
  out << "// ===== Copy 2 =====\n";
  out << copy2 << "\n\n";

  out << "// ===== TOD Harness contracts =====\n";
  for (const auto &p : pairs)
    emit_harness_contract(
      out,
      *cdef,
      state_vars,
      rw_by_name,
      ctor,
      c1_name,
      c2_name,
      p.first,
      p.second);

  return out.str();
}

std::string generate_tod_harness(
  const std::string &sol_source,
  const nlohmann::json &ast,
  const std::string &contract,
  const std::string &func_a,
  const std::string &func_b)
{
  return generate_tod_harness_multi(
    sol_source, ast, contract, {{func_a, func_b}});
}
