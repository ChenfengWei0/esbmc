#include <solidity-frontend/solidity_tod_analysis.h>

#include <algorithm>

namespace solidity_tod
{

namespace
{

/// Snapshot of the contract's structural decls we need for the walk.
struct ContractIndex
{
  // state variable id -> name (only used for diagnostics)
  std::map<int, std::string> state_var_ids;
  // internal function id -> name (intra-contract callgraph nodes)
  std::map<int, std::string> internal_fn_ids;
  // modifier id -> name (treated as inline-callable, included in callgraph)
  std::map<int, std::string> modifier_ids;
  // function id -> body json pointer (FunctionDefinition or ModifierDefinition)
  std::map<int, const nlohmann::json *> body_by_id;
  // function id -> name for any callable inside the contract (used both for
  // walking and for callgraph lookup)
  std::map<int, std::string> all_callable_names;
};

static ContractIndex index_contract(const nlohmann::json &contract_def)
{
  ContractIndex idx;
  if (!contract_def.contains("nodes"))
    return idx;
  for (const auto &node : contract_def["nodes"])
  {
    const std::string nt = node.value("nodeType", "");
    int id = node.value("id", -1);
    if (id < 0)
      continue;

    if (nt == "VariableDeclaration" && node.value("stateVariable", false))
      idx.state_var_ids[id] = node.value("name", "");
    else if (nt == "FunctionDefinition")
    {
      idx.internal_fn_ids[id] = node.value("name", "");
      idx.all_callable_names[id] = node.value("name", "");
      idx.body_by_id[id] = &node;
    }
    else if (nt == "ModifierDefinition")
    {
      idx.modifier_ids[id] = node.value("name", "");
      idx.all_callable_names[id] = node.value("name", "");
      idx.body_by_id[id] = &node;
    }
  }
  return idx;
}

/// Walker state passed by reference through the recursion.
struct Walker
{
  const ContractIndex &idx;
  RWSet rw;
  // callees discovered in this function body (as AST ids of callable defs).
  std::set<int> callees;
};

static void walk(Walker &w, const nlohmann::json &node, bool is_write_target);

static void walk_children(
  Walker &w,
  const nlohmann::json &node,
  bool is_write_target)
{
  if (node.is_object())
  {
    for (auto it = node.begin(); it != node.end(); ++it)
      walk(w, it.value(), is_write_target);
  }
  else if (node.is_array())
  {
    for (const auto &child : node)
      walk(w, child, is_write_target);
  }
}

static void walk(Walker &w, const nlohmann::json &node, bool is_write_target)
{
  if (!node.is_object())
  {
    if (node.is_array())
      for (const auto &c : node)
        walk(w, c, is_write_target);
    return;
  }

  const std::string nt = node.value("nodeType", "");

  if (nt == "Identifier")
  {
    if (node.contains("referencedDeclaration"))
    {
      int ref = node["referencedDeclaration"].is_number_integer()
                  ? node["referencedDeclaration"].get<int>()
                  : -1;
      if (ref >= 0)
      {
        if (w.idx.state_var_ids.count(ref))
        {
          if (is_write_target)
            w.rw.writes.insert(ref);
          else
            w.rw.reads.insert(ref);
        }
        // Note: callees are recorded only via FunctionCall to avoid
        // counting function-typed assignments etc.
      }
    }
    return; // Identifier has no children to recurse into.
  }

  if (nt == "Assignment")
  {
    const std::string op = node.value("operator", "=");
    bool compound = (op != "=");
    if (node.contains("leftHandSide"))
      walk(w, node["leftHandSide"], true);
    if (compound && node.contains("leftHandSide"))
      // x += e  reads x as well — re-visit LHS as a read.
      walk(w, node["leftHandSide"], false);
    if (node.contains("rightHandSide"))
      walk(w, node["rightHandSide"], false);
    return;
  }

  if (nt == "UnaryOperation")
  {
    const std::string op = node.value("operator", "");
    bool writes_arg = (op == "++" || op == "--" || op == "delete");
    if (node.contains("subExpression"))
    {
      walk(w, node["subExpression"], writes_arg || is_write_target);
      if ((op == "++" || op == "--") && node.contains("subExpression"))
        walk(w, node["subExpression"], false); // read-then-write
    }
    return;
  }

  if (nt == "IndexAccess")
  {
    if (node.contains("baseExpression"))
      walk(w, node["baseExpression"], is_write_target);
    if (node.contains("indexExpression"))
      walk(w, node["indexExpression"], false);
    return;
  }

  if (nt == "MemberAccess")
  {
    if (node.contains("expression"))
      walk(w, node["expression"], is_write_target);
    return;
  }

  if (nt == "FunctionCall")
  {
    // Direct internal call?  expression is Identifier whose
    // referencedDeclaration is one of our same-contract callables.
    if (node.contains("expression"))
    {
      const auto &expr = node["expression"];
      if (
        expr.is_object() && expr.value("nodeType", "") == "Identifier" &&
        expr.contains("referencedDeclaration"))
      {
        int ref = expr["referencedDeclaration"].is_number_integer()
                    ? expr["referencedDeclaration"].get<int>()
                    : -1;
        if (ref >= 0 && w.idx.all_callable_names.count(ref))
          w.callees.insert(ref);
      }
      walk(w, expr, false);
    }
    if (node.contains("arguments"))
      walk(w, node["arguments"], false);
    return;
  }

  if (nt == "ModifierInvocation")
  {
    if (node.contains("modifierName"))
    {
      const auto &mn = node["modifierName"];
      if (mn.is_object() && mn.contains("referencedDeclaration"))
      {
        int ref = mn["referencedDeclaration"].is_number_integer()
                    ? mn["referencedDeclaration"].get<int>()
                    : -1;
        if (ref >= 0 && w.idx.all_callable_names.count(ref))
          w.callees.insert(ref);
      }
    }
    if (node.contains("arguments"))
      walk(w, node["arguments"], false);
    return;
  }

  // Default: recurse into all children with the current write_target flag
  // disabled (most contexts are read contexts).
  walk_children(w, node, false);
}

/// Compute the per-callable footprint (R/W + outgoing call edges).
struct LocalInfo
{
  RWSet rw;
  std::set<int> callees;
};

static std::map<int, LocalInfo> compute_local(
  const ContractIndex &idx)
{
  std::map<int, LocalInfo> out;
  for (const auto &kv : idx.body_by_id)
  {
    int id = kv.first;
    const nlohmann::json &fn = *kv.second;
    Walker w{idx, {}, {}};
    if (fn.contains("body"))
      walk(w, fn["body"], false);
    // Modifier invocations attached to a FunctionDefinition live on the
    // FunctionDefinition itself, not in its body.
    if (fn.contains("modifiers"))
      for (const auto &mi : fn["modifiers"])
        walk(w, mi, false);
    out[id] = {w.rw, w.callees};
  }
  return out;
}

/// Iterate to a fixed point: footprint(f) = local(f) ∪ ⋃ footprint(callee).
static std::map<int, RWSet> close_callgraph(
  const std::map<int, LocalInfo> &local)
{
  std::map<int, RWSet> closed;
  for (const auto &kv : local)
    closed[kv.first] = kv.second.rw;

  bool changed = true;
  while (changed)
  {
    changed = false;
    for (const auto &kv : local)
    {
      int id = kv.first;
      RWSet &dst = closed[id];
      for (int callee : kv.second.callees)
      {
        auto it = closed.find(callee);
        if (it == closed.end())
          continue;
        for (int r : it->second.reads)
          if (dst.reads.insert(r).second)
            changed = true;
        for (int wid : it->second.writes)
          if (dst.writes.insert(wid).second)
            changed = true;
      }
    }
  }
  return closed;
}

static bool is_externally_callable(const nlohmann::json &fn)
{
  const std::string vis = fn.value("visibility", "");
  return vis == "public" || vis == "external";
}

static bool is_orderable(const nlohmann::json &fn)
{
  // Reject things that can't appear in a TOD swap: ctors, fallback,
  // receive, view/pure (no writes).
  const std::string kind = fn.value("kind", "function");
  if (kind != "function")
    return false;
  const std::string mut = fn.value("stateMutability", "nonpayable");
  if (mut == "view" || mut == "pure")
    return false;
  return true;
}

} // namespace

std::map<std::string, RWSet> compute_rw_sets(
  const nlohmann::json &contract_def)
{
  ContractIndex idx = index_contract(contract_def);
  auto local = compute_local(idx);
  auto closed = close_callgraph(local);

  std::map<std::string, RWSet> out;
  for (const auto &kv : closed)
  {
    auto name_it = idx.all_callable_names.find(kv.first);
    if (name_it != idx.all_callable_names.end() && !name_it->second.empty())
      out[name_it->second] = kv.second;
  }
  return out;
}

std::vector<Pair> find_tod_candidates(const nlohmann::json &contract_def)
{
  ContractIndex idx = index_contract(contract_def);
  auto local = compute_local(idx);
  auto closed = close_callgraph(local);

  // Collect orderable, externally callable function names + ids.
  std::vector<std::pair<std::string, int>> orderable;
  for (const auto &kv : idx.body_by_id)
  {
    const nlohmann::json &fn = *kv.second;
    if (fn.value("nodeType", "") != "FunctionDefinition")
      continue;
    if (!is_externally_callable(fn) || !is_orderable(fn))
      continue;
    const std::string name = fn.value("name", "");
    if (name.empty())
      continue;
    // Also require that the function actually has a non-empty footprint
    // (touches state in some way) — pure thunks with no writes would never
    // satisfy the candidacy check and would just clutter output.
    auto rw_it = closed.find(kv.first);
    if (rw_it == closed.end())
      continue;
    if (rw_it->second.writes.empty() && rw_it->second.reads.empty())
      continue;
    orderable.emplace_back(name, kv.first);
  }
  std::sort(orderable.begin(), orderable.end());

  std::vector<Pair> pairs;
  for (size_t i = 0; i < orderable.size(); ++i)
  {
    const RWSet &a = closed[orderable[i].second];
    for (size_t j = i + 1; j < orderable.size(); ++j)
    {
      const RWSet &b = closed[orderable[j].second];

      std::set<int> shared;
      // W(a) ∩ (R(b) ∪ W(b))
      for (int wid : a.writes)
      {
        if (b.reads.count(wid) || b.writes.count(wid))
          shared.insert(wid);
      }
      // W(b) ∩ (R(a) ∪ W(a))
      for (int wid : b.writes)
      {
        if (a.reads.count(wid) || a.writes.count(wid))
          shared.insert(wid);
      }
      if (shared.empty())
        continue;

      // Skip self-pairs (already excluded by j>i, but defensive).
      if (orderable[i].first == orderable[j].first)
        continue;

      pairs.push_back(
        {orderable[i].first, orderable[j].first, std::move(shared)});
    }
  }
  return pairs;
}

} // namespace solidity_tod
