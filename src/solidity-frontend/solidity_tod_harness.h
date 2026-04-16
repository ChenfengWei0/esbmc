#pragma once

#include <nlohmann/json.hpp>
#include <string>

/// Generate a TOD (Transaction Order Dependence) harness as compilable
/// Solidity source.  The harness creates two renamed copies of the
/// target contract (V_C1, V_C2), deploys them with identical constructor
/// args, calls funcA/funcB in both orderings, and asserts that all
/// public state variables are equal afterward.
///
/// \param sol_source  Full text of the original .sol file
/// \param ast         Parsed JSON AST (from solc --ast-compact-json)
/// \param contract    Name of the target contract
/// \param func_a      Name of the first function
/// \param func_b      Name of the second function
/// \return Compilable Solidity source for the harness, or empty on error
std::string generate_tod_harness(
  const std::string &sol_source,
  const nlohmann::json &ast,
  const std::string &contract,
  const std::string &func_a,
  const std::string &func_b);
