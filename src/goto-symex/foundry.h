#ifndef CPROVER_GOTO_SYMEX_FOUNDRY_H
#define CPROVER_GOTO_SYMEX_FOUNDRY_H

#include <goto-symex/symex_target_equation.h>
#include <solvers/smt/smt_conv.h>
#include <util/namespace.h>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <utility>
#include <vector>

/// Generates Foundry (`*.t.sol`) test-cases for Solidity contracts.
///
/// A Solidity coverage/counterexample run drives contract methods through the
/// multi-transaction dispatcher with nondet parameters. Since the fix that
/// makes scalar harness parameters flow as `nondet$symex` symbols
/// (solidity_convert_call.cpp assign_param_nondet), each param value is
/// recoverable from the SMT model AND its SSA assignment `original_lhs` names
/// the owning method+parameter (`sol:@C@<C>@F@<method>@<param>`). This
/// generator reconstructs the transaction sequence from those assignments and
/// emits a compilable Foundry test that replays it, mirroring the
/// ctest/pytest generators used for C/Python.
class foundry_generator
{
private:
  /// One reconstructed argument: its Solidity source type (from the
  /// `#sol_type` irep on the parameter symbol) and the formatted literal.
  struct sol_arg
  {
    std::string param;    // parameter name
    std::string sol_type; // e.g. "UINT256", "ADDRESS", "BOOL"
    std::string literal;  // Solidity literal text, or empty if unformattable
    expr2tc value;        // raw recovered value (nil if not recovered)
  };

  /// One reconstructed external call `c.method(args)`.
  struct sol_call
  {
    std::string contract;
    std::string method;
    std::vector<sol_arg> args;
    bool supported = true; // false if any arg type could not be formatted
    bool reverts = false;  // covered edge reverts -> wrap in vm.expectRevert()
    // ③A0 environment pinning: the msg.value the solver picked for this call's
    // transaction (recovered from the per-tx `_sol_per_tx_reseed`). Emitted as
    // `{value: N}` ONLY when `payable` — sending value to a non-payable method
    // reverts. Nil / non-payable -> no value pin.
    expr2tc msg_value;
    bool payable = false;
    // ③A0 block.timestamp pinning: the timestamp the solver picked, emitted as
    // `vm.warp(N)` ONLY when `warp` (the covered path actually reads
    // block.timestamp) — otherwise a nondet timestamp is noise that can spuriously
    // revert unrelated arithmetic. Nil / !warp -> no warp.
    expr2tc block_timestamp;
    bool warp = false;
  };

  /// One counterexample -> one test function: a sequence of calls.
  using test_case = std::vector<sol_call>;

  std::vector<test_case> test_cases;
  std::string source_file;
  mutable std::mutex data_mutex;

  /// Contracts Solidity forbids `new` on (abstract / interface / library),
  /// detected from the `#sol_no_new` flag stamped on their constructor symbol.
  /// The generator degrades their instantiation to UNSUPPORTED so the emitted
  /// test always compiles. Populated during reconstruct() (needs the namespace).
  mutable std::set<std::string> non_instantiable;

  /// Contracts that are Solidity libraries (no `this` self-pointer on their
  /// functions). A library is called statically (`Lib.fn(args)`) and never
  /// instantiated, so it is kept out of the construction plan. Populated
  /// during reconstruct() (needs the namespace).
  mutable std::set<std::string> libraries;

  /// Cache of a method's declared parameters in source order:
  /// key "<contract>@<method>" -> [(param_name, sol_type)].
  mutable std::
    map<std::string, std::vector<std::pair<std::string, std::string>>>
      method_params;

  /// Look up a method's declared parameters (source order + `#sol_type`) from
  /// the symbol table; cached. Empty vector if the method is not found.
  const std::vector<std::pair<std::string, std::string>> &get_method_params(
    const namespacet &ns,
    const std::string &contract,
    const std::string &method) const;

  /// Cache of a contract's dispatcher-callable methods: base-name -> list of
  /// full method-ids (`sol:@C@<C>@F@<m>#<id>`); >1 id means the base name is
  /// overloaded. Keyed by contract name.
  mutable std::map<std::string, std::map<std::string, std::vector<std::string>>>
    dispatcher_methods;

  /// The methods a contract's `_ESBMC_Nondet_Extcall_<C>` dispatcher can
  /// invoke as a transaction entry, extracted from the dispatcher body. This
  /// is the ground truth of externally-callable methods: modifier/aux helpers
  /// (e.g. a `bump_onlyOwner` the dispatcher never calls directly) are absent,
  /// and overloads appear as multiple ids under one base name. Cached.
  const std::map<std::string, std::vector<std::string>> &
  dispatcher_callable(const namespacet &ns, const std::string &contract) const;

  /// Parse a Solidity parameter symbol name
  /// `sol:@C@<contract>@F@<method>@<param>` into its parts. Returns false if
  /// the name is not a Solidity function parameter.
  static bool parse_param_symbol(
    const std::string &name,
    std::string &contract,
    std::string &method,
    std::string &param);

  /// Model value of a focused-function parameter (contract,method,param), read
  /// from the solver for `--function` isolated-function runs (where parameters
  /// are free nondet inputs, not `param = nondet` assignments). Null expr when
  /// the parameter was sliced away (irrelevant to the covered branch).
  static expr2tc recover_focus_param(
    const symex_target_equationt &target,
    smt_convt &smt_conv,
    const std::string &contract,
    const std::string &method,
    const std::string &param);

  /// Format an SMT constant as a Solidity literal for the given `#sol_type`.
  /// Returns empty for a type we cannot faithfully render (caller then marks
  /// the call unsupported rather than emit a wrong test).
  static std::string
  format_sol_value(const std::string &sol_type, const expr2tc &value);

  /// Default literal for a supported type (used for a declared parameter that
  /// was not exercised on the path, e.g. a short-circuited operand). Returns
  /// empty for an unsupported type.
  static std::string default_sol_literal(const std::string &sol_type);

  /// Reconstruct the ordered call sequence of one counterexample from its SSA.
  test_case reconstruct(
    const symex_target_equationt &target,
    smt_convt &smt_conv,
    const namespacet &ns) const;

  /// Deduplication fingerprint for a reconstructed test case.
  static std::string fingerprint(const test_case &tc);

  /// Contract under test for a case: the first non-constructor call's contract
  /// (fallback: the first call's contract). Files split one per primary.
  static std::string primary_contract(const test_case &tc);

  /// Write one `.t.sol` file for the given contract-under-test. Test cases are
  /// grouped by construction signature: each distinct construction becomes its
  /// own `contract <primary>CovTest[_n] is Test` with a `setUp()` that deploys
  /// the instance, and the cases sharing it become its `test_cov_*` functions.
  /// Returns the number of calls wrapped in revert-tolerant try/catch.
  size_t write_foundry_file(
    const std::string &path,
    const std::string &primary,
    const std::vector<test_case> &cases) const;

public:
  foundry_generator() = default;

  /// Clear collected data (start of a coverage run).
  void clear();

  /// Collect one counterexample (called per reached goal in coverage mode).
  void collect(
    const symex_target_equationt &target,
    smt_convt &smt_conv,
    const namespacet &ns);

  /// Emit the accumulated test suite.
  void generate() const;

  /// Single-shot emission for non-coverage mode.
  void generate_single(
    const symex_target_equationt &target,
    smt_convt &smt_conv,
    const namespacet &ns);

  /// Whether any test case has been collected.
  bool has_tests() const;
};

#endif
