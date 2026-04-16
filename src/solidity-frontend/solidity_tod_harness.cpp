#include <solidity-frontend/solidity_tod_harness.h>
#include <util/message.h>
#include <map>
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

/// Emit a Solidity parameter list string like "uint256 a, address b".
static std::string emit_params(const nlohmann::json &func_def)
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
    out += st + " " + name;
  }
  return out;
}

/// Emit just the argument names for forwarding: "a, b".
static std::string emit_args(const nlohmann::json &func_def)
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
    out += name;
  }
  return out;
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
  std::vector<std::string> mapping_key_types; // for (nested) mappings
};

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

// ---------------------------------------------------------------------------
// Main generator
// ---------------------------------------------------------------------------

std::string generate_tod_harness(
  const std::string &sol_source,
  const nlohmann::json &ast,
  const std::string &contract,
  const std::string &func_a,
  const std::string &func_b)
{
  // 1. Find the contract
  const nlohmann::json *cdef = find_contract(ast, contract);
  if (!cdef)
  {
    log_error("TOD harness: contract '{}' not found in AST", contract);
    return {};
  }

  // 2. Find functions A and B
  const nlohmann::json *fa = find_function(*cdef, func_a);
  const nlohmann::json *fb = find_function(*cdef, func_b);
  if (!fa)
  {
    log_error("TOD harness: function '{}' not found in contract '{}'", func_a, contract);
    return {};
  }
  if (!fb)
  {
    log_error("TOD harness: function '{}' not found in contract '{}'", func_b, contract);
    return {};
  }

  // 3. Extract contract source text using AST src field
  std::string contract_src = extract_src(sol_source, cdef->value("src", ""));
  if (contract_src.empty())
  {
    log_error("TOD harness: cannot extract contract source text");
    return {};
  }

  // 4. Create two renamed copies
  std::string c1_name = contract + "_C1";
  std::string c2_name = contract + "_C2";
  std::string copy1 = rename_contract(contract_src, contract, c1_name);
  std::string copy2 = rename_contract(contract_src, contract, c2_name);

  // 5. Collect metadata
  auto state_vars = collect_public_state_vars(*cdef);
  const nlohmann::json *ctor = find_constructor(*cdef);

  // Constructor params
  std::string ctor_params_decl;
  std::string ctor_args;
  if (ctor)
  {
    ctor_params_decl = emit_prefixed_params(*ctor, "ctor");
    ctor_args = emit_prefixed_args(*ctor, "ctor");
  }

  // Function params (prefixed to avoid name collisions)
  std::string fa_params = emit_prefixed_params(*fa, "a");
  std::string fb_params = emit_prefixed_params(*fb, "b");
  std::string fa_args = emit_prefixed_args(*fa, "a");
  std::string fb_args = emit_prefixed_args(*fb, "b");

  // Collect params by type for mapping key comparison.
  // For each mapping key type, gather all matching params from both functions.
  std::map<std::string, std::vector<std::string>> params_by_type;
  for (const auto &sv : state_vars)
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

  // 6. Build the harness
  std::ostringstream out;
  out << "// Auto-generated TOD (Transaction Order Dependence) harness\n";
  out << "// Contract: " << contract << "\n";
  out << "// Functions: " << func_a << ", " << func_b << "\n";
  out << "//\n";
  out << "// Verify with:\n";
  out << "//   solc --ast-compact-json <this-file>.sol > <this-file>.solast\n";
  out << "//   esbmc <this-file>.solast --sol <this-file>.sol "
      << "--contract TOD_" << func_a << "_" << func_b
      << " --bound --no-standard-checks --unwind 2 "
      << "--no-unwinding-assertions\n";
  out << "//\n";
  out << "// If VERIFICATION FAILED, a TOD vulnerability exists:\n";
  out << "//   the counterexample shows inputs where swapping the\n";
  out << "//   execution order of " << func_a << " and " << func_b
      << " produces different state.\n\n";
  out << "// SPDX-License-Identifier: MIT\n";
  out << "pragma solidity >=0.8.0;\n\n";

  // Copy 1
  out << "// ===== Copy 1 =====\n";
  out << copy1 << "\n\n";

  // Copy 2
  out << "// ===== Copy 2 =====\n";
  out << copy2 << "\n\n";

  // Harness contract
  std::string harness_name = "TOD_" + func_a + "_" + func_b;
  out << "// ===== TOD Harness =====\n";
  out << "contract " << harness_name << " {\n";

  // test function
  out << "    function test(\n";

  // Combine all params
  std::vector<std::string> all_params;
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
  out << "    ) public {\n";

  // Deploy two copies
  out << "        " << c1_name << " c1 = new " << c1_name << "(";
  out << ctor_args << ");\n";
  out << "        " << c2_name << " c2 = new " << c2_name << "(";
  out << ctor_args << ");\n\n";

  // Order 1: A → B
  out << "        // Order 1: " << func_a << " then " << func_b << "\n";
  out << "        c1." << func_a << "(" << fa_args << ");\n";
  out << "        c1." << func_b << "(" << fb_args << ");\n\n";

  // Order 2: B → A
  out << "        // Order 2: " << func_b << " then " << func_a << "\n";
  out << "        c2." << func_b << "(" << fb_args << ");\n";
  out << "        c2." << func_a << "(" << fa_args << ");\n\n";

  // Assertions on state variables
  out << "        // State comparison — if any assert fails, TOD exists\n";
  for (const auto &sv : state_vars)
  {
    if (sv.is_mapping && !sv.mapping_key_types.empty())
    {
      // For each combination of keys matching the mapping's key types,
      // generate an assertion.
      // For a single-level mapping(K => V): compare at each param of type K.
      // For nested mapping(K1 => mapping(K2 => V)): Cartesian product.
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

      // Generate Cartesian product of key sets
      // For simplicity, handle 1-level and 2-level mappings explicitly.
      if (key_sets.size() == 1)
      {
        for (const auto &k : key_sets[0])
        {
          out << "        assert(c1." << sv.name << "(" << k << ")"
              << " == c2." << sv.name << "(" << k << "));\n";
        }
      }
      else if (key_sets.size() == 2)
      {
        for (const auto &k1 : key_sets[0])
          for (const auto &k2 : key_sets[1])
          {
            out << "        assert(c1." << sv.name << "(" << k1
                << ", " << k2 << ")"
                << " == c2." << sv.name << "(" << k1 << ", " << k2
                << "));\n";
          }
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

  out << "    }\n";
  out << "}\n";

  return out.str();
}
