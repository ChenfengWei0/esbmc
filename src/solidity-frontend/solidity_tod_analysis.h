#pragma once

#include <nlohmann/json.hpp>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace solidity_tod
{

/// Read/write footprint of a single function over the contract's
/// (public/private) state variables, identified by AST id.
struct RWSet
{
  std::set<int> reads;
  std::set<int> writes;
};

/// Pair of externally callable function names with overlapping footprints.
/// `shared` lists the AST ids that triggered the candidacy (informational).
struct Pair
{
  std::string func_a;
  std::string func_b;
  std::set<int> shared;
};

/// Compute R/W sets for every FunctionDefinition (and modifier body) in
/// the given ContractDefinition node, then propagate footprints transitively
/// over the intra-contract internal call graph.  External calls and
/// `this.f()` self-external calls are conservatively ignored — they are out
/// of scope for the Tier-2 algorithm.  Returns a map keyed by function name.
std::map<std::string, RWSet> compute_rw_sets(
  const nlohmann::json &contract_def);

/// Find pairs of public/external functions whose footprints satisfy
///   W(f1) ∩ (R(f2) ∪ W(f2))  ∪  W(f2) ∩ (R(f1) ∪ W(f1))  ≠ ∅
/// Skips view/pure functions, the constructor, fallback and receive.
/// Pairs are returned sorted with `func_a < func_b` lexicographically.
std::vector<Pair> find_tod_candidates(
  const nlohmann::json &contract_def);

} // namespace solidity_tod
