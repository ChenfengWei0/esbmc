#pragma once

#include <nlohmann/json.hpp>
#include <string>
#include <utility>
#include <vector>

enum class TodHarnessMode
{
  Balance,
  Race,
};

/// Generate a TOD (Transaction Order Dependence) harness as compilable
/// Solidity source.
///
/// Race mode emits one harness with `function test(C c1, C c2, ...)`
/// parameters; ESBMC's --contract-param-fresh option ensures c1 and c2
/// get independent storage at symbolic entry.
///
/// Balance mode keeps the two-copy rename approach (V_C1 / V_C2) so that
/// ETH transfers on the distinct singleton dispatchers do not alias.
std::string generate_tod_harness(
  const std::string &sol_source,
  const nlohmann::json &ast,
  const std::string &contract,
  const std::string &func_a,
  const std::string &func_b,
  TodHarnessMode mode);

/// Multi-pair variant of generate_tod_harness().
std::string generate_tod_harness_multi(
  const std::string &sol_source,
  const nlohmann::json &ast,
  const std::string &contract,
  const std::vector<std::pair<std::string, std::string>> &pairs,
  TodHarnessMode mode);
