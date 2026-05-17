#include <goto-programs/goto_functions.h>
#include <goto-programs/goto_convert_class.h>
#include <goto-programs/loop_unroll.h>
#include <langapi/language_util.h>
#include <unordered_set>

class goto_coveraget
{
public:
  explicit goto_coveraget(const namespacet &ns, goto_functionst &goto_functions)
    : ns(ns), goto_functions(goto_functions)
  {
    target_num = -1;
  };
  explicit goto_coveraget(
    const namespacet &ns,
    goto_functionst &goto_functions,
    const std::string filename)
    : ns(ns), goto_functions(goto_functions), filename(filename)
  {
    target_num = -1;
  };
  void assertion_coverage();
  void branch_coverage();
  void branch_function_coverage();

  // k-path coverage: at each branch, emit goals for every combination of the
  // last (n-1) branch directions and the current direction (Williams et al.,
  // EDCC 2005). A goal is `assert(!witness)`; SAT means the path is reachable
  // and the goal is marked covered by multi_property_check, mirroring the
  // branch_coverage convention. The structural AND-chain preserves SSA merge
  // friendliness up to depth_cap; past depth_cap the witness is too deep to
  // hand to the solver and is dropped (a Phase-2 ghost-flag fallback is
  // tracked in #4325). Goal count is bounded by max_goals per function.
  void k_path_coverage();

  void insert_assert(
    goto_programt &goto_program,
    goto_programt::targett &it,
    const expr2tc &guard);

  // customize comment
  void insert_assert(
    goto_programt &goto_program,
    goto_programt::targett &it,
    const expr2tc &guard,
    const std::string &idf);

  // replace every assertion to a specific guard
  void replace_all_asserts_to_guard(
    const expr2tc &guard,
    bool is_instrumentation = false);
  // replace an assertion to a specific guard
  void replace_assert_to_guard(
    const expr2tc &guard,
    goto_programt::instructiont::targett &it,
    bool is_instrumentation);

  // convert assert(cond) to assume(cond) to preserve path constraints
  void replace_assert_to_assume(goto_programt::instructiont::targett &it);
  void replace_all_asserts_to_assume();

  // convert assert(cond) to assert(!cond)
  void negating_asserts(const std::string &tgt_fname);

  // condition cov
  void condition_coverage();
  expr2tc gen_not_eq_expr(const expr2tc &lhs, const expr2tc &rhs);
  expr2tc gen_and_expr(const expr2tc &lhs, const expr2tc &rhs);
  expr2tc gen_not_expr(const expr2tc &expr);
  int get_total_instrument() const;
  int get_total_assert_instance() const;
  std::set<std::pair<std::string, std::string>> get_total_cond_assert() const;
  std::string get_filename_from_path(std::string path);
  void set_target(const std::string &_tgt);
  bool is_target_func(const irep_idt &f, const std::string &tgt_name) const;
  bool
  filter(const irep_idt &func_name, const goto_programt &goto_program) const;

  // total numbers of instrumentation
  static size_t total_assert;
  static size_t total_assert_ins;
  static std::set<std::pair<std::string, std::string>> total_cond;
  static size_t total_branch;
  static size_t total_func_branch;
  static size_t total_kpath;
  // |spanning_set| under Marré-Bertolino subsumption (issue #4335 PR1).
  // Equals total_kpath when no goal is subsumed by another. Used as the
  // denominator of the k-path coverage percentage to drop the lower
  // bound contribution of redundant subsumed goals.
  static size_t total_kpath_spanning;
  // (msg, loc) pairs whose every emission is non-maximal. JSON report
  // marks these as "spanning-set-redundant" and they are excluded from
  // the spanning-set denominator.
  static std::set<std::pair<std::string, std::string>>
    k_path_spanning_redundant;
  // all instrumented claims (condition, location) for JSON report.
  // For branch coverage this is the *static universe* (every in-scope
  // decision edge, built with NO covered-set skip applied) so the
  // denominator never shrinks when edges are skipped (Item 2c).
  static std::set<std::pair<std::string, std::string>> all_claims;

  // Cross-run persisted covered-set (Item 2). covered_set holds the
  // (guard_str, location.as_string()) edge keys loaded from the JSON at
  // branch_coverage() entry; an edge present here is NOT re-instrumented
  // this run (its assert pair is skipped) but still counted in the
  // denominator via all_claims. covered_set_outpath, when non-empty, is
  // where bmc.cpp merge-writes the accumulated set at run end. Static so
  // the run-end report path (bmc.cpp) can read them like total_branch /
  // all_claims.
  static std::set<std::pair<std::string, std::string>> covered_set;
  static std::string covered_set_outpath;
  // Item 2e: serialize covered_set to covered_set_outpath crash-safely
  // (write a .tmp then atomic rename). Called both incrementally as
  // each edge is witnessed P_SATISFIABLE (bmc.cpp) and once at run end,
  // so a mid-run kill still persists every edge proven so far and
  // bounded re-runs accumulate monotonically. No-op if outpath empty.
  static void write_covered_set_atomic();

  std::string target_function = "";
  bool cov_assume_asserts = false;
  // When non-empty (set from --contract), branch_coverage() instruments
  // ONLY decisions whose lexically-declaring Solidity contract equals this
  // name (location's "sol_decl_contract", stamped by the frontend). This
  // makes per-contract branch coverage count C's own source decisions
  // only; inherited/library decisions are attributed to their own
  // declaring contract and excluded. Empty => no scoping (unchanged
  // whole-unit behaviour, e.g. C/C++/no --contract).
  std::string scope_contract = "";

  // Path to the cross-run covered-set JSON (--coverage-covered-set).
  // Empty => disabled (no load, no skip, no write-back; behaviour
  // identical to before Item 2). Read at branch_coverage() entry.
  std::string covered_set_path = "";

  // Item 5-d: contracts whose own decisions are excluded from branch
  // coverage entirely (--coverage-exclude-contract, repeatable). A
  // decision whose "sol_decl_contract" is in this set is dropped BEFORE
  // all_claims.insert, so it counts in NEITHER the denominator NOR the
  // numerator (true dependency exclusion, e.g. OpenZeppelin under
  // --coverage-whole-unit). Empty => no exclusion. In default
  // per-contract mode scope_contract already filters foreign decisions,
  // so this set is a no-op there by construction.
  std::set<std::string> exclude_contracts;

  // k-path coverage knobs (see #4325 "Decided defaults").
  // n  : prefix depth — number of consecutive branches in each witness
  //      (default 4 if --unwind is unset, else --unwind).
  // d  : post-simplification depth cap on the witness expression tree.
  //      Witnesses deeper than d are skipped in Phase 1 (no ghost-flag
  //      fallback yet — see #4325).
  // m  : per-function goal cap. On overflow, instrumentation aborts with
  //      an actionable error rather than silently truncating.
  size_t k_path_n = 4;
  size_t k_path_witness_depth = 8;
  size_t k_path_max_goals = 10000;

protected:
  // turn a OP b OP c into a list a, b, c
  expr2tc handle_single_guard(const expr2tc &expr, bool top_level);
  void handle_operands_guard(
    const expr2tc &expr,
    goto_programt &goto_program,
    goto_programt::instructiont::targett &it);
  void add_cond_cov_assert(
    const expr2tc &top_ptr,
    const expr2tc &pre_cond,
    goto_programt &goto_program,
    goto_programt::instructiont::targett &it);
  void gen_cond_cov_assert(
    const expr2tc &top_ptr,
    const expr2tc &pre_cond,
    goto_programt &goto_program,
    goto_programt::instructiont::targett &it);

  namespacet ns;
  goto_functionst &goto_functions;

  // we need to skip the conditions within the built-in library
  // while keeping the file manually included by user
  // this filter, however, is unsound.. E.g. if the src filename is the same as the builtin library name
  std::string filename;

  int target_num;
};

class goto_coverage_rm : goto_convertt
{
public:
  goto_coverage_rm(
    contextt &_context,
    optionst &_options,
    goto_functionst &goto_functions)
    : goto_convertt(_context, _options), goto_functions(goto_functions)
  {
    options.set_option("goto-instrumented", true);
  }
  void remove_sideeffect();
  goto_functionst &goto_functions;
};
