#include <goto-programs/goto_coverage.h>
#include <goto-programs/k_path_spanning.h>
#include <irep2/irep2_utils.h>
#include <util/i2string.h>
#include <util/std_types.h>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cassert>
#include <cstdio>
#include <deque>
#include <fstream>
#include <functional>
#include <map>
#include <vector>

// Defined later in this TU; used by solidity_path_coverage() for --contract
// scoping. Extracts the contract from a Solidity mangled id "sol:@C@<C>@F@...".
static std::string contract_of(const std::string &mangled_id);

size_t goto_coveraget::total_assert = 0;
size_t goto_coveraget::total_assert_ins = 0;
std::set<std::pair<std::string, std::string>> goto_coveraget::total_cond;
size_t goto_coveraget::total_branch = 0;
size_t goto_coveraget::total_func_branch = 0;
size_t goto_coveraget::total_kpath = 0;
size_t goto_coveraget::total_kpath_spanning = 0;
std::set<std::pair<std::string, std::string>>
  goto_coveraget::k_path_spanning_redundant;
std::set<std::pair<std::string, std::string>> goto_coveraget::all_claims;
std::set<std::pair<std::string, std::string>> goto_coveraget::covered_set;
std::map<std::string, char> goto_coveraget::claim_outcome;
std::mutex goto_coveraget::claim_outcome_mutex;
std::set<std::pair<std::string, std::string>> goto_coveraget::revert_paths;
std::set<std::pair<std::string, std::string>>
  goto_coveraget::rollback_revert_paths;
std::set<std::pair<std::string, std::string>>
  goto_coveraget::undetermined_exit_paths;
std::map<std::string, goto_coveraget::path_ce_t> goto_coveraget::path_ce;
std::string goto_coveraget::covered_set_outpath;
std::atomic<bool> goto_coveraget::branch_cov_active{false};
std::atomic<size_t> goto_coveraget::total_branch_atomic{0};
std::atomic<bool> goto_coveraget::covered_set_mode{false};
std::atomic<size_t> goto_coveraget::live_reached{0};
std::atomic<size_t> goto_coveraget::covered_run{0};

void goto_coveraget::write_covered_set_atomic()
{
  if (covered_set_outpath.empty())
    return;
  nlohmann::json out;
  out["version"] = 1;
  out["covered"] = nlohmann::json::array();
  for (const auto &[cond, loc] : covered_set)
    out["covered"].push_back({{"cond", cond}, {"loc", loc}});
  const std::string tmp = covered_set_outpath + ".tmp";
  {
    std::ofstream f(tmp);
    if (!f)
    {
      log_warning("coverage-covered-set: cannot write {}", tmp);
      return;
    }
    f << out.dump(2) << "\n";
  }
  // Atomic publish: a kill between the two writes leaves the previous
  // valid file intact (never a truncated/corrupt covered-set).
  if (std::rename(tmp.c_str(), covered_set_outpath.c_str()) != 0)
    log_warning(
      "coverage-covered-set: atomic rename to {} failed", covered_set_outpath);
}

std::string goto_coveraget::get_filename_from_path(std::string path)
{
  if (path.find_last_of('/') != std::string::npos)
    return path.substr(path.find_last_of('/') + 1);

  return path;
}

/*
  replace the old_condition of all assertions
  to the new condition(guard)
*/
void goto_coveraget::replace_all_asserts_to_guard(
  const expr2tc &guard,
  bool is_instrumentation)
{
  std::unordered_set<std::string> location_pool = {};
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  Forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      Forall_goto_program_instructions (it, goto_program)
      {
        std::string cur_filename =
          get_filename_from_path(it->location.file().as_string());
        if (location_pool.count(cur_filename) == 0)
          continue;

        if (it->is_assert())
          replace_assert_to_guard(guard, it, is_instrumentation);
      }
    }
}

/*
  replace the old_condition of a specific assertion
  to the new condition(guard)
*/
void goto_coveraget::replace_assert_to_guard(
  const expr2tc &guard,
  goto_programt::instructiont::targett &it,
  bool is_instrumentation)
{
  const expr2tc old_guard = it->guard;
  it->guard = guard;
  if (is_instrumentation)
    it->location.property("instrumented assertion");
  else
    it->location.property("replaced assertion");
  it->location.comment(from_expr(ns, "", old_guard));
  it->location.user_provided(true);
}

/*
  convert assert(cond) to assume(cond)
  preserving the original condition as a path constraint
*/
void goto_coveraget::replace_assert_to_assume(
  goto_programt::instructiont::targett &it)
{
  const expr2tc guard = it->guard;
  it->make_assumption(guard);
  it->location.property("replaced assertion");
  it->location.user_provided(true);
}

/*
  convert all assertions to assumptions
*/
void goto_coveraget::replace_all_asserts_to_assume()
{
  std::unordered_set<std::string> location_pool = {};
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  Forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      Forall_goto_program_instructions (it, goto_program)
      {
        std::string cur_filename =
          get_filename_from_path(it->location.file().as_string());
        if (location_pool.count(cur_filename) == 0)
          continue;

        if (it->is_assert())
          replace_assert_to_assume(it);
      }
    }
}

/*
Algo:
- convert all assertions to false and enable multi-property
*/
void goto_coveraget::assertion_coverage()
{
  replace_all_asserts_to_guard(gen_false_expr(), true);
  total_assert = get_total_instrument();
  total_assert_ins = get_total_assert_instance();
  all_claims = get_total_cond_assert();
}

/*
Branch coverage applies to any control structure that can alter the flow of execution, including:
- if-else
- switch-case
- Loops (for, while, do-while)
- try-catch-finally (not in c)
- Early exits (return, break, continue)
The goal of branch coverage is to ensure that all possible execution paths in the program are tested.

The CBMC extends it to the entry of the function. So we will do the same.


Algo:
  1. convert assertions to true
  2. add false assertion add the beginning of the function and the branch()
*/
void goto_coveraget::branch_function_coverage()
{
  log_progress("Adding false assertions...");
  total_func_branch = 0;

  std::unordered_set<std::string> location_pool = {};
  // cmdline.arg[0]
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  std::unordered_set<int> catch_tgt_list;
  Forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      bool flg = true;

      Forall_goto_program_instructions (it, goto_program)
      {
        std::string cur_filename =
          get_filename_from_path(it->location.file().as_string());
        // skip if it's not the verifying files
        // probably a library
        if (location_pool.count(cur_filename) == 0)
          continue;

        if (flg)
        {
          // add a false assert in the beginning
          // to check if the function is entered.
          insert_assert(
            goto_program,
            it,
            gen_false_expr(),
            "function entry: " + id2string(f_it->first));
          flg = false;
        }

        if (it->location.property().as_string() == "skipped")
          // this stands for the auxiliary condition/branch we added.
          continue;

        // convert assertions to true (or assume)
        if (
          it->is_assert() &&
          it->location.property().as_string() != "replaced assertion" &&
          it->location.property().as_string() != "instrumented assertion")
        {
          if (cov_assume_asserts)
            replace_assert_to_assume(it);
          else
            replace_assert_to_guard(gen_true_expr(), it, false);
        }

        // e.g. IF !(a > 1) THEN GOTO 3
        else if (it->is_goto() && !is_true(it->guard))
        {
          if (it->is_target())
            target_num = it->target_number;
          // assert(!(a > 1));
          // assert(a > 1);
          insert_assert(goto_program, it, it->guard);
          insert_assert(goto_program, it, gen_not_expr(it->guard));
        }
      }

      flg = true;
    }

  // fix for branch coverage with kind/incr
  // It seems in kind/incr, the goto_functions used during the BMC is simplified and incomplete
  total_func_branch = get_total_instrument();
  all_claims = get_total_cond_assert();

  // avoid Assertion `call_stack.back().goto_state_map.size() == 0' failed
  goto_functions.update();
}

// Walk an ASSIGN rhs / RETURN operand for short-circuit operators that
// the frontend did NOT lower to control flow. goto_sideeffects.cpp:160
// rewrites `||`/`&&` into an if-then-else GOTO chain ONLY when an
// operand has a side effect; with side-effect-free operands the `or`/
// `and` stays a flat boolean expression in one instruction and carries
// no GOTO guard, so the `it->is_goto()` arm below never sees it
// (ESBMC: "No branch detected" where solc instruments the operator as
// a 2-arm decision). For each such operator, emit the same 2-arm
// decision the GOTO arm produces, keyed on the short-circuit operand
// (side_1 — the operand that decides whether the rest is evaluated),
// recursing both sides to reach nested operators.
// Max folded short-circuit/ternary operands treated as decisions at ONE site.
// Phase 1 (runtime snapshots into tr/cnt) and Phase 2 (offline enumeration of
// the 2^K combinations) MUST apply this identically: if Phase 1 snapshots K
// operands that Phase 2 does not enumerate, the emitted path carries a depth
// that is short by K, so `cnt != depth` holds on EVERY real execution and the
// path becomes permanently uncoverable — and is then reported as PASSED, i.e.
// a false proof of unreachability. 2^12 = 4096 combinations per site is already
// far beyond any real Solidity expression; sites above it are left out of the
// decision set entirely (and reported) rather than half-instrumented.
static constexpr size_t SC_DECISION_MAX = 12;

static void collect_short_circuit_decisions(
  const expr2tc &e,
  const std::function<void(const expr2tc &)> &emit)
{
  if (is_nil_expr(e))
    return;
  if (is_or2t(e))
    emit(to_or2t(e).side_1);
  else if (is_and2t(e))
    emit(to_and2t(e).side_1);
  else if (is_if2t(e))
    // Solidity ternary `cond ? a : b`: lowered to a flat if2t SELECT
    // when both arms are side-effect-free.  solc-coverage instruments
    // the ternary's `cond` as a 2-arm decision; mirror that by emitting
    // the cond expression as a probe keyed on the same location.
    emit(to_if2t(e).cond);
  for (size_t i = 0; i < e->get_num_sub_exprs(); ++i)
  {
    const expr2tc *sub = e->get_sub_expr(i);
    if (sub != nullptr)
      collect_short_circuit_decisions(*sub, emit);
  }
}

bool goto_coveraget::edge_reaches_error_revert(
  goto_programt::const_targett it,
  goto_programt::const_targett end) const
{
  // Bounded straight-line walk. Stop at anything that changes control flow or
  // merges another edge in; only an unbroken run of straight-line instructions
  // that reaches an error call proves THIS edge reverts.
  for (size_t steps = 0; it != end && steps < 256; ++it, ++steps)
  {
    // A downstream join (some other edge targets this instruction) means we can
    // no longer attribute a later terminator to this edge alone.
    if (steps > 0 && it->is_target())
      return false;
    // Control-flow / terminating instructions break the straight-line run.
    if (
      it->is_goto() || it->is_return() || it->is_end_function() ||
      it->is_throw() || it->is_catch())
      return false;
    // A lowered `revert CustomError(...)` is a call to a `#sol_error` function.
    if (it->is_function_call() && is_code_function_call2t(it->code))
    {
      const expr2tc &fn = to_code_function_call2t(it->code).function;
      if (is_symbol2t(fn))
      {
        const symbolt *s = ns.lookup(to_symbol2t(fn).thename);
        if (s && !s->type.get("#sol_error").as_string().empty())
          return true;
      }
      // A non-error call is straight-line; keep walking.
    }
    // ASSIGN / DECL / DEAD / SKIP / LOCATION / OTHER / ATOMIC / ASSUME are
    // straight-line: keep walking.
  }
  return false;
}

void goto_coveraget::branch_coverage()
{
  log_progress("Adding false assertions...");
  total_branch = 0;
  // all_claims is the no-skip static universe, rebuilt every call
  // (Item 2c). covered_set/outpath start clean unless a path is given.
  all_claims.clear();
  covered_set.clear();
  covered_set_outpath.clear();

  // Cross-run covered-set (Item 2): load the persisted edge keys. A
  // missing/unreadable/empty file is treated as "nothing covered yet"
  // (first run). The path is still recorded so the run-end report
  // (bmc.cpp) merge-writes the accumulated set back.
  if (!covered_set_path.empty())
  {
    covered_set_outpath = covered_set_path;
    std::ifstream in(covered_set_path);
    if (in)
    {
      try
      {
        nlohmann::json j;
        in >> j;
        for (const auto &e : j.value("covered", nlohmann::json::array()))
          covered_set.emplace(
            e.at("cond").get<std::string>(), e.at("loc").get<std::string>());
      }
      catch (const std::exception &ex)
      {
        log_warning(
          "coverage-covered-set: ignoring unparseable {} ({})",
          covered_set_path,
          ex.what());
        covered_set.clear();
      }
    }
  }

  std::unordered_set<std::string> location_pool = {};
  // cmdline.arg[0]
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  std::unordered_set<int> catch_tgt_list;
  Forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      Forall_goto_program_instructions (it, goto_program)
      {
        std::string cur_filename =
          get_filename_from_path(it->location.file().as_string());
        // skip if it's not the verifying files
        // probably a library
        if (location_pool.count(cur_filename) == 0)
          continue;

        if (it->location.property().as_string() == "skipped")
          // this stands for the auxiliary condition/branch we added.
          continue;

        // Emit one 2-arm decision (assert(cond) + assert(!cond)) for
        // `cond` at the current instruction, under the exact scoping,
        // edge-key, static-universe and cross-run covered-set rules the
        // GOTO-guard path uses, so a folded short-circuit operator and
        // a control-flow guard are counted identically.
        // `cond_reverts`/`neg_reverts`: the covered edge behind the
        // assert(cond) / assert(!cond) probe reverts (require failure / revert
        // CustomError). Stamp `sol_revert_edge` on that probe so the Foundry
        // generator emits vm.expectRevert(). Only set for GOTO decisions.
        auto emit_decision = [&](
                               const expr2tc &cond,
                               bool cond_reverts = false,
                               bool neg_reverts = false) {
          // Per-contract scoping (--contract C, Solidity): only instrument
          // decisions lexically declared inside contract C. The frontend
          // stamps each statement location with "sol_decl_contract" (its
          // declaring ContractDefinition, invariant across inheritance
          // merge-by-copy). Skipping the assert pair for C-foreign
          // decisions auto-scopes BOTH the denominator
          // (get_total_cond_assert counts instrumented asserts only) and
          // the numerator (reached_claims can only hit instrumented
          // asserts), so the percentage stays correct by construction.
          if (
            !scope_contract.empty() &&
            it->location.get("sol_decl_contract").as_string() != scope_contract)
            return;

          // Item 5-d: dependency exclusion. Drop the decision BEFORE the
          // all_claims.insert below, so an excluded contract's decisions
          // leave BOTH the denominator (static universe) and the
          // numerator (no assert => reached_claims can never hit it) —
          // exactly the "OZ in no denominator, no numerator" property.
          // Default mode never reaches here for foreign code (scope
          // filter above already skipped it), so this is a no-op there.
          if (
            !exclude_contracts.empty() &&
            exclude_contracts.count(
              it->location.get("sol_decl_contract").as_string()))
            return;

          // Edge keys (guard_str, location.as_string()) — exactly the
          // identity get_total_cond_assert() and the numerator
          // (bmc.cpp claim_sig) use, so universe / denominator /
          // numerator stay key-aligned. as_string() excludes custom
          // irep fields, so inheritance/modifier copies fold to one.
          const expr2tc neg = gen_not_expr(cond);
          const std::string loc = it->location.as_string();
          const std::pair<std::string, std::string> k_g(
            from_expr(ns, "", cond), loc);
          const std::pair<std::string, std::string> k_ng(
            from_expr(ns, "", neg), loc);

          // Static universe (Item 2c): every in-scope edge counts in
          // the denominator regardless of the covered-set skip below,
          // so skipping can never inflate coverage.
          all_claims.insert(k_g);
          all_claims.insert(k_ng);

          // Item 2b: an edge already witnessed P_SATISFIABLE in a prior
          // run (covered_set) is not re-instrumented — fewer SMT
          // obligations on re-runs. Sound: an instrumented assert is a
          // property obligation, not a path constraint, so omitting it
          // removes one observation only and perturbs no other branch;
          // the cross-run cover is monotone-∃ (a real witness stays
          // valid). Only true P_SATISFIABLE is ever written back.
          if (!covered_set.count(k_g))
          {
            insert_assert(goto_program, it, cond);
            if (cond_reverts)
              std::prev(it)->location.set("sol_revert_edge", true);
          }
          if (!covered_set.count(k_ng))
          {
            insert_assert(goto_program, it, neg);
            if (neg_reverts)
              std::prev(it)->location.set("sol_revert_edge", true);
          }
        };

        // convert assertions to true (or assume)
        if (
          it->is_assert() &&
          it->location.property().as_string() != "replaced assertion" &&
          it->location.property().as_string() != "instrumented assertion")
        {
          if (cov_assume_asserts)
            replace_assert_to_assume(it);
          else
            replace_assert_to_guard(gen_true_expr(), it, false);
        }

        // e.g. IF !(a > 1) THEN GOTO 3
        else if (it->is_goto() && !is_true(it->guard))
        {
          if (it->is_target())
            target_num = it->target_number;
          // Revert fidelity: classify which edge reverts BEFORE instrumenting
          // (target/fall-through still point at the original successors). A
          // probe assert(P) fails when P is false, so assert(it->guard) covers
          // the FALL-THROUGH edge and assert(!it->guard) the GOTO-taken edge.
          const bool taken_reverts = edge_reaches_error_revert(
            it->get_target(), goto_program.instructions.end());
          const bool fall_reverts = edge_reaches_error_revert(
            std::next(it), goto_program.instructions.end());
          // Only tag when exactly one edge reverts (both/neither -> no tag).
          emit_decision(
            it->guard,
            /*cond_reverts=*/fall_reverts && !taken_reverts,
            /*neg_reverts=*/taken_reverts && !fall_reverts);
        }

        // Pure short-circuit ||/&& folded into an ASSIGN rhs / RETURN
        // operand (no GOTO — see collect_short_circuit_decisions above).
        // solc instruments every such operator as a 2-arm decision;
        // without this ESBMC reports "No branch detected" for e.g.
        // `return a == 0 || b == 1;`.
        else if (it->is_assign())
          collect_short_circuit_decisions(
            to_code_assign2t(it->code).source, emit_decision);

        else if (it->is_return())
          collect_short_circuit_decisions(
            to_code_return2t(it->code).operand, emit_decision);
      }
    }

  // Denominator = the no-skip static universe built in the loop above:
  // every in-scope decision edge keyed by (condition,
  // location.as_string()) in a std::set, so inheritance/modifier
  // physical copies fold to one source identity and override/sibling
  // decisions stay distinct (different source line). This decouples the
  // denominator from what was actually instrumented (Item 2c): when the
  // covered-set skip omits an assert, all_claims is unaffected, so
  // coverage % can never be spuriously inflated. The numerator
  // (reached_claims, matched against all_claims) uses the same key.
  // When no covered-set is given this is identical to the previous
  // get_total_cond_assert() result (same keys, same dedup), so the
  // no-path path is behaviour-preserving. Other coverage modes
  // (assertion/k-path/branch-function) keep get_total_cond_assert() /
  // get_total_instrument() by design.
  total_branch = static_cast<size_t>(all_claims.size());
  // Signal-safe snapshot for the timeout/term handlers ("data even on
  // UNKNOWN"). Set here, at instrumentation time, before any solve can
  // be killed. covered_set_outpath is set during option parsing (well
  // before this), so covered_set_mode is final here.
  total_branch_atomic.store(total_branch, std::memory_order_relaxed);
  covered_set_mode.store(
    !covered_set_outpath.empty(), std::memory_order_relaxed);
  live_reached.store(0, std::memory_order_relaxed);
  covered_run.store(0, std::memory_order_relaxed);
  branch_cov_active.store(true, std::memory_order_relaxed);

  // avoid Assertion `call_stack.back().goto_state_map.size() == 0' failed
  goto_functions.update();
}

// Post-simplification depth of an expression tree, capped early once the
// caller's threshold is exceeded. Used to gate emission of the structural
// witness (issue #4325).
static size_t expr_depth(const expr2tc &e, size_t cap)
{
  if (is_nil_expr(e))
    return 0;
  size_t n = e->get_num_sub_exprs();
  if (n == 0)
    return 1;
  size_t d = 0;
  for (size_t i = 0; i < n; ++i)
  {
    const expr2tc *sub = e->get_sub_expr(i);
    if (sub == nullptr)
      continue;
    d = std::max(d, expr_depth(*sub, cap));
    if (d > cap)
      return d + 1;
  }
  return 1 + d;
}

/*
k-path coverage (Phase 1 — see GitHub issue #4325).

For each branching `IF g GOTO L`, emit one coverage goal per combination of
the last (n-1) prior branch directions × the two outcomes of the current
branch. Each goal is `assert(!witness)` where `witness = d_1 ∧ … ∧ d_k`
with each d_i either a prior branch guard or its negation; multi_property
marks a goal as reached when the assertion is falsifiable, i.e. when the
corresponding path is feasible. This mirrors the existing branch_coverage
inversion convention.

Bounded by the textual order of branches within a function (cheap and
deterministic). Joins make this an *over-approximation* of true path
coverage — some witnesses may be infeasible and stay uncovered, which is
correct under the spanning-set scoring proposed in #4325.

Goal count per branch: 2^min(prefix_size+1, n). Capped per function by
`k_path_max_goals`; on overflow the instrumentation aborts with an
actionable error rather than silently truncating (decision locked in #4325).
*/
void goto_coveraget::k_path_coverage()
{
  log_progress("Adding k-path coverage assertions (n={})...", k_path_n);
  total_kpath = 0;
  total_kpath_spanning = 0;
  k_path_spanning_redundant.clear();
  k_path_spanning_sett spanning;

  // Defense-in-depth: parseoptions rejects N==0 and N>30 at the CLI, but
  // re-check here in case the method is invoked via another code path.
  // 30 keeps `1 << pdepth` well below the size_t shift limit and below
  // any reasonable goal cap.
  static constexpr size_t K_PATH_N_MAX = 30;
  if (k_path_n == 0 || k_path_n > K_PATH_N_MAX)
  {
    log_error(
      "--k-path-coverage requires 1 <= N <= {} (got {})",
      K_PATH_N_MAX,
      k_path_n);
    abort();
  }

  std::unordered_set<std::string> location_pool = {};
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  Forall_goto_functions (f_it, goto_functions)
  {
    if (!f_it->second.body_available || f_it->first == "__ESBMC_main")
      continue;

    goto_programt &goto_program = f_it->second.body;
    if (filter(f_it->first, goto_program))
      continue;

    // Sliding window of the last (n-1) prior branch guards in textual order.
    // Reset per function: each function is its own k-path scope (#4325).
    std::deque<expr2tc> prefix;
    size_t function_goals = 0;

    Forall_goto_program_instructions (it, goto_program)
    {
      std::string cur_filename =
        get_filename_from_path(it->location.file().as_string());
      if (location_pool.count(cur_filename) == 0)
        continue;

      if (it->location.property().as_string() == "skipped")
        continue;

      // Mirror branch_coverage: neutralise existing assertions so they don't
      // confuse multi_property_check.
      if (
        it->is_assert() &&
        it->location.property().as_string() != "replaced assertion" &&
        it->location.property().as_string() != "instrumented assertion")
      {
        if (cov_assume_asserts)
          replace_assert_to_assume(it);
        else
          replace_assert_to_guard(gen_true_expr(), it, false);
        continue;
      }

      // Conditional forward branch. Backward unconditional gotos (loop
      // back-edges) carry guard=true and are skipped here; iteration
      // semantics are picked up later by ESBMC's --unwind unrolling.
      if (it->is_goto() && !is_true(it->guard))
      {
        if (it->is_target())
          target_num = it->target_number;

        const expr2tc current_guard = it->guard;
        // pdepth is bounded by k_path_n - 1 <= K_PATH_N_MAX - 1 = 29
        // (enforced above), so the shift below cannot overflow. Assert as
        // a tripwire — silent overflow would be unsound.
        const size_t pdepth = std::min(prefix.size(), k_path_n - 1);
        assert(pdepth < 30 && "pdepth bounded by parseoptions cap");
        const size_t pcombos = size_t(1) << pdepth;
        const size_t branch_goals = 2 * pcombos;

        if (
          branch_goals > k_path_max_goals ||
          function_goals > k_path_max_goals - branch_goals)
        {
          log_error(
            "k-path coverage: per-function goal count would exceed "
            "--k-path-max-goals={} in '{}'. Lower --k-path-coverage=N "
            "(currently {}) or raise --k-path-max-goals.",
            k_path_max_goals,
            id2string(f_it->first),
            k_path_n);
          abort();
        }

        // The deque is trimmed to ≤ (n-1) entries at the bottom of every
        // branch iteration, so its current contents are exactly the active
        // prefix.
        std::vector<expr2tc> active(prefix.begin(), prefix.end());

        for (size_t mask = 0; mask < pcombos; ++mask)
        {
          // Build the prefix witness for this direction mask, while
          // tracking (stored-guard, polarity) pairs so we can drop mask
          // combinations that are unsat by construction.
          //
          // ESBMC's `simplify` recognises 2-term `p ∧ ¬p` but does not
          // fold chained forms like `p ∧ q ∧ ¬p` to FALSE — it would
          // instrument a tautological `assert(¬(p ∧ q ∧ ¬p))` that can
          // never be falsified, permanently inflating the denominator.
          // Catching this at construction time is sound (we only drop
          // witnesses we can prove unsat by syntactic structure) and
          // preserves the single-term behaviour of the simplifier.
          //
          // Phase-1 limitation: this only catches *syntactic* same-atom
          // contradictions (same stored guard with opposing polarities).
          // Semantically contradictory pairs across different stored
          // expressions — e.g. `(x == 1) ∧ (x == 2)` from successive
          // switch-case branches — require comparison-domain reasoning
          // and are out of scope for this PR.
          expr2tc pwit;
          std::vector<std::pair<expr2tc, bool>> atoms;
          atoms.reserve(pdepth);
          bool contradictory = false;
          for (size_t i = 0; i < pdepth; ++i)
          {
            const bool pol = (mask & (size_t(1) << i)) != 0;
            for (const auto &[h, p] : atoms)
            {
              if (h == active[i] && p != pol)
              {
                contradictory = true;
                break;
              }
            }
            if (contradictory)
              break;
            atoms.emplace_back(active[i], pol);
            expr2tc d = pol ? active[i] : gen_not_expr(active[i]);
            pwit = is_nil_expr(pwit) ? d : gen_and_expr(pwit, d);
          }
          if (contradictory)
            continue;

          // Emit one goal per current direction. Skip the direction if
          // it would contradict an atom already in the prefix.
          const expr2tc current_neg = gen_not_expr(current_guard);
          for (size_t cd = 0; cd < 2; ++cd)
          {
            const bool cdir_pol = (cd == 0);
            bool cdir_conflict = false;
            for (const auto &[h, p] : atoms)
            {
              if (h == current_guard && p != cdir_pol)
              {
                cdir_conflict = true;
                break;
              }
            }
            if (cdir_conflict)
              continue;

            const expr2tc &cdir = cdir_pol ? current_guard : current_neg;
            expr2tc full = is_nil_expr(pwit) ? cdir : gen_and_expr(pwit, cdir);
            simplify(full);

            if (is_false(full))
              continue;
            if (is_true(full))
              continue;

            if (expr_depth(full, k_path_witness_depth) > k_path_witness_depth)
            {
              // Phase 1: drop witnesses past the depth cap. The hashed
              // ghost-flag fallback for deep prefixes is Phase 2 (#4325).
              continue;
            }

            expr2tc neg_full = gen_not_expr(full);
            simplify(neg_full);

            std::string idf = from_expr(ns, "", full);
            insert_assert(goto_program, it, neg_full, idf);

            // Record the goal's full atom multiset (prefix + current
            // direction) so the spanning-set analysis can drop subsumed
            // emissions from the coverage denominator.
            std::vector<std::pair<expr2tc, bool>> goal_atoms = atoms;
            goal_atoms.emplace_back(current_guard, cdir_pol);
            spanning.add_goal(
              std::move(goal_atoms), idf, it->location.as_string());

            ++function_goals;
          }
        }

        prefix.push_back(current_guard);
        if (prefix.size() > k_path_n - 1)
          prefix.pop_front();
      }
    }
  }

  total_kpath = get_total_instrument();
  all_claims = get_total_cond_assert();

  // Soundness invariant: each insert_assert call above paired with
  // exactly one spanning.add_goal call, so the number of goals tracked
  // in the spanning analysis must equal the number of instrumented
  // assertions counted in the goto programs. A divergence means the
  // emission path diverged from the spanning bookkeeping (e.g. a future
  // edit added an insert_assert without the matching add_goal, or vice
  // versa) and the spanning-set denominator would be silently wrong.
  // ESBMC is a verifier — we abort rather than report an unsound
  // coverage percentage.
  if (spanning.total() != static_cast<size_t>(total_kpath))
  {
    log_error(
      "k-path coverage: internal invariant violated — spanning.total()={} "
      "but get_total_instrument()={}. Each instrumented assertion must "
      "have a matching spanning.add_goal entry. Aborting rather than "
      "report an unsound coverage percentage.",
      spanning.total(),
      total_kpath);
    abort();
  }

  // Compute the spanning-set after every goal has been collected. The
  // resulting size is the Phase-2 denominator; redundant_claims feeds the
  // JSON `feasibility` field.
  //
  // Secondary invariant: the simplifier never collapses two semantically
  // distinct witnesses to the same idf string, so spanning_size_ is
  // bounded above by all_claims.size() + |redundant|, which is what
  // allows the bmc.cpp coverage cap to make sense. Any future change
  // that reuses an idf across distinct witnesses or alters from_expr()
  // formatting must preserve this 1:1 mapping or the percentage will
  // silently deflate.
  spanning.finalize();
  total_kpath_spanning = spanning.spanning_size();
  for (const auto &claim : all_claims)
    if (spanning.is_redundant(claim.first, claim.second))
      k_path_spanning_redundant.insert(claim);

  goto_functions.update();
}

/*
Solidity complete-path coverage (entry->exit path coverage for test gen).

For each eligible (user-source) function:
  Phase 1: one integer path-number accumulator `tr`. At function entry
           `tr = 1`; before every decision `tr = tr*2 + guard_value`. A single
           scalar records the whole decision sequence in order and survives
           loop unrolling (symex re-runs the update each iteration), so it
           handles loops without per-occurrence ghost symbols. The guard VALUE
           (not the direction) is accumulated; the path condition supplies the
           direction, so no CFG edge-splitting is needed.
  Phase 2: bounded DFS of complete entry->exit decision sequences. Each path's
           number enc mirrors tr (start 1; enc*2+1 for the guard-true/taken
           successor, enc*2+0 for guard-false/fallthrough). At END_FUNCTION
           emit `assert(tr != enc)`, falsified exactly on that path (enc is
           unique, so all path asserts can sit before the single END_FUNCTION).
           enc goes into the claim comment for a unique claim_sig
           (bmc.cpp:2000 is otherwise unsound). Loops: a back-edge is followed
           at most path_cov_unwind times per path, so paths are enumerated up
           to that many iterations, aligned with the symex --unwind bound;
           `assert(tr != enc)` fires per distinct iteration count.

Revert exits (require/revert in public/external functions) lower to a real
branch that END_FUNCTION captures, so they are enumerated as distinct paths.
*/
void goto_coveraget::solidity_path_coverage()
{
  log_progress("Adding Solidity complete-path coverage assertions...");
  if (cov_context == nullptr)
  {
    log_error(
      "--solidity-path-coverage: no context available to create ghost "
      "snapshot symbols (dispatch must set cov_context). Aborting rather "
      "than silently producing no coverage.");
    abort();
  }

  std::unordered_set<std::string> location_pool = {};
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  // Cross-run covered-set: paths already witnessed (CE obtained) in an earlier
  // round are NOT re-instrumented this round, so each escalation round
  // instruments a strictly smaller set and spends its solver budget only on
  // paths still lacking a CE. Sound: an instrumented assert is a property
  // obligation, not a path constraint, so omitting one removes an observation
  // and perturbs no other path; the cross-run cover is monotone (a real
  // witness stays valid). The denominator (all_claims) is built below WITHOUT
  // the skip, so skipping can never inflate the reported coverage.
  all_claims.clear();
  covered_set.clear();
  covered_set_outpath.clear();
  revert_paths.clear();
  rollback_revert_paths.clear();
  undetermined_exit_paths.clear();
  {
    std::lock_guard lock(claim_outcome_mutex);
    claim_outcome.clear();
    path_ce.clear();
  }
  if (!covered_set_path.empty())
  {
    covered_set_outpath = covered_set_path;
    std::ifstream in(covered_set_path);
    if (in)
    {
      try
      {
        nlohmann::json j;
        in >> j;
        for (const auto &e : j.value("covered", nlohmann::json::array()))
          covered_set.emplace(
            e.at("cond").get<std::string>(), e.at("loc").get<std::string>());
      }
      catch (const std::exception &ex)
      {
        log_warning(
          "coverage-covered-set: ignoring unparseable {} ({})",
          covered_set_path,
          ex.what());
        covered_set.clear();
      }
    }
  }

  size_t ghost_counter = 0;
  // Folded short-circuit sites left out of the decision set for exceeding
  // SC_DECISION_MAX operands; reported so the incompleteness is visible.
  size_t sc_sites_over_cap = 0;
  size_t total_paths = 0;
  size_t dropped_paths = 0;
  size_t skipped_paths = 0;

  Forall_goto_functions (f_it, goto_functions)
  {
    if (!f_it->second.body_available || f_it->first == "__ESBMC_main")
      continue;
    goto_programt &goto_program = f_it->second.body;
    if (filter(f_it->first, goto_program))
      continue;

    // Only instrument functions living in the user source. c2goto library
    // models and the synthetic _ESBMC_Main dispatcher harness carry
    // non-user locations; enumerating their complete paths is both wrong
    // (not the unit under test) and explodes (thousands of paths). A
    // function is in scope iff at least one of its instructions is in
    // location_pool.
    bool in_user_src = false;
    forall_goto_program_instructions (uit, goto_program)
      if (location_pool.count(
            get_filename_from_path(uit->location.file().as_string())))
      {
        in_user_src = true;
        break;
      }
    if (!in_user_src)
      continue;

    // --contract scoping (codex #4): only enumerate functions declared in the
    // target contract; sibling contracts / their helpers are out of the unit
    // under test. Empty scope_contract => no scoping (whole-unit).
    if (
      !scope_contract.empty() &&
      contract_of(f_it->first.as_string()) != scope_contract)
      continue;

    // Skip the lowered custom-error functions (`error E();` becomes a
    // `#sol_error` function whose whole body is ASSUME(false)). They are the
    // lowering of a `revert E()` STATEMENT, not a unit under test: counting
    // their single degenerate path would inflate the denominator with a goal
    // that is uncoverable by construction (its assert sits downstream of the
    // ASSUME(false)) and so permanently reports as undecided.
    {
      const symbolt *fsym = ns.lookup(f_it->first);
      if (fsym && !fsym->type.get("#sol_error").as_string().empty())
        continue;
    }

    // Loops are handled by the tr accumulator (survives unrolling) plus a
    // bounded DFS (each back-edge followed at most path_cov_unwind times).

    // Phase 1: one integer path-number accumulator `tr` per function.
    // tr starts at 1 (a leading sentinel bit so different-length prefixes
    // stay distinct) and each decision does `tr = tr*2 + guard_value`. A
    // single scalar records the whole decision sequence in order and — unlike
    // one bool per static decision — survives loop unrolling, because symex
    // re-runs the update on every iteration (this is the Slice-2 enabler).
    const type2tc utype = get_uint_type(64);
    symbolt sym;
    sym.type = unsignedbv_typet(64);
    sym.name = "__ESBMC_path_tr$" + i2string(ghost_counter++);
    sym.id = "path_cov::" + id2string(sym.name);
    sym.lvalue = true;
    sym.static_lifetime = false;
    sym.is_extern = false;
    symbolt *psym;
    cov_context->move(sym, psym);
    expr2tc tr = symbol2tc(migrate_type(psym->type), psym->id);
    irep_idt tr_id = psym->id;

    // Companion decision-COUNT ghost `cnt` (starts 0, +1 per decision). The
    // exit assert checks tr==enc AND cnt==depth, so a feasible path with more
    // than 64 decisions — whose 64-bit tr WRAPS — can never spuriously match a
    // shorter emitted path's enc (its cnt = true length differs). Without this
    // a wrapped tr could fire another path's assert => a WRONG test (codex #1).
    symbolt csym;
    csym.type = unsignedbv_typet(64);
    csym.name = "__ESBMC_path_cnt$" + i2string(ghost_counter++);
    csym.id = "path_cov::" + id2string(csym.name);
    csym.lvalue = true;
    csym.static_lifetime = false;
    csym.is_extern = false;
    symbolt *pcsym;
    cov_context->move(csym, pcsym);
    expr2tc cnt = symbol2tc(migrate_type(pcsym->type), pcsym->id);
    irep_idt cnt_id = pcsym->id;

    // Snapshot one decision: insert `tr = tr*2 + (uint64)val; cnt = cnt+1`
    // before `it` (leaving `it` unchanged), both marked "skipped" so they are
    // not coverage claims. For K decisions at the same site, call in order —
    // the first snapshotted becomes the higher-order bit, matching the DFS.
    auto snapshot = [&](goto_programt::targett &sit, const expr2tc &val) {
      goto_programt::instructiont a;
      a.type = ASSIGN;
      a.code = code_assign2tc(
        tr,
        add2tc(
          utype,
          mul2tc(utype, tr, constant_int2tc(utype, BigInt(2))),
          typecast2tc(utype, val)));
      a.location = sit->location;
      a.location.property("skipped");
      a.function = sit->location.get_function();
      goto_program.insert_swap(sit++, a);
      --sit;
      goto_programt::instructiont b;
      b.type = ASSIGN;
      b.code = code_assign2tc(
        cnt, add2tc(utype, cnt, constant_int2tc(utype, BigInt(1))));
      b.location = sit->location;
      b.location.property("skipped");
      b.function = sit->location.get_function();
      goto_program.insert_swap(sit++, b);
      --sit;
    };

    // At each decision: snapshot its value into tr. Conditional GOTOs (guard)
    // AND folded short-circuit &&/|| / ternary operands in ASSIGN/RETURN — the
    // latter carry no GOTO, so branch_coverage collects them via
    // collect_short_circuit_decisions; we mirror that (codex #2), snapshotting
    // each in collect order (matched by the DFS fan-out).
    Forall_goto_program_instructions (it, goto_program)
    {
      if (it->is_goto() && !is_true(it->guard))
        snapshot(it, it->guard);
      else if (
        (it->is_assign() || it->is_return()) &&
        it->location.property().as_string() != "skipped")
      {
        const expr2tc &src = it->is_assign()
                               ? to_code_assign2t(it->code).source
                               : to_code_return2t(it->code).operand;
        std::vector<expr2tc> ops;
        collect_short_circuit_decisions(
          src, [&](const expr2tc &e) { ops.push_back(e); });
        // Same cap as the Phase-2 fan-out. Snapshotting operands the DFS will
        // not enumerate would desynchronise cnt from every emitted depth and
        // silently make the whole site's paths uncoverable.
        if (ops.size() > SC_DECISION_MAX)
        {
          ++sc_sites_over_cap;
          continue;
        }
        for (const expr2tc &op : ops)
          snapshot(it, op);
      }
    }

    // DECL tr and initialise `tr = 1` at function entry (in that order),
    // both before the original first instruction.
    {
      auto entry = goto_program.instructions.begin();
      locationt eloc = entry->location;
      irep_idt efn = entry->location.get_function();
      goto_programt::instructiont dcl;
      dcl.type = DECL;
      dcl.code = code_decl2tc(utype, tr_id);
      dcl.location = eloc;
      dcl.location.property("skipped");
      dcl.function = efn;
      goto_program.insert_swap(entry++, dcl); // DECL before entry
      --entry;                                // entry back at original
      goto_programt::instructiont ini;
      ini.type = ASSIGN;
      ini.code = code_assign2tc(tr, constant_int2tc(utype, BigInt(1)));
      ini.location = eloc;
      ini.location.property("skipped");
      ini.function = efn;
      goto_program.insert_swap(entry++, ini); // ASSIGN after DECL, before orig
      --entry;
      goto_programt::instructiont cdcl;
      cdcl.type = DECL;
      cdcl.code = code_decl2tc(utype, cnt_id);
      cdcl.location = eloc;
      cdcl.location.property("skipped");
      cdcl.function = efn;
      goto_program.insert_swap(entry++, cdcl);
      --entry;
      goto_programt::instructiont cini;
      cini.type = ASSIGN;
      cini.code = code_assign2tc(cnt, constant_int2tc(utype, BigInt(0)));
      cini.location = eloc;
      cini.location.property("skipped");
      cini.function = efn;
      goto_program.insert_swap(entry++, cini);
      --entry;
    }

    goto_program.compute_target_numbers();

    // Phase 2: bounded DFS over complete entry->exit decision sequences.
    // Each path's number enc mirrors the runtime tr (start 1; at a decision
    // enc*2+1 for the guard-true/taken successor, enc*2+0 for
    // guard-false/fallthrough), so `assert(tr != enc)` at the exit is
    // falsified exactly on that path. Loops: a back-edge (goto whose target
    // is earlier) is followed at most path_cov_unwind times per path, so
    // paths are enumerated up to that many iterations — matching the symex
    // --unwind bound. State per path: (pc, enc, back-edge-follow count).
    // State per path: (pc, enc, per-loop back-edge counts, decision depth).
    // Each loop is keyed by its head (the back-edge's target target_number),
    // so nested loops get INDEPENDENT budgets (a single shared counter would
    // make outer+inner share path_cov_unwind and miss valid nested paths;
    // symex unwinds each loop independently). codex #3.
    // 5th field: has this partial path already walked over a rollback restore
    // (i.e. it is a require/revert("msg") reverting path)?
    using becntt = std::map<unsigned, unsigned>;
    // 6th field: has this partial path walked through the function epilogue?
    std::vector<std::
                  tuple<goto_programt::targett, uint64_t, becntt, uint64_t, bool, bool>>
      stack;
    stack.push_back(
      {goto_program.instructions.begin(),
       (uint64_t)1,
       becntt{},
       (uint64_t)0,
       false,
       false});

    // Deferred exit asserts (insert after the walk so we don't mutate the
    // program mid-DFS). Each entry: (insertion pc, tr!=enc||cnt!=depth guard,
    // claim comment, is_revert). An is_revert path exits through a custom-error
    // `#sol_error` revert; its assert is placed right BEFORE that call (upstream
    // of the callee's ASSUME(false), which would otherwise make an
    // END_FUNCTION-placed assert vacuous -> path never covered) and gets
    // stamped `sol_revert_edge` so the Foundry generator renders
    // vm.expectRevert() (R0). Normal paths exit at END_FUNCTION, is_revert=false.
    std::vector<
      std::tuple<goto_programt::targett, expr2tc, std::string, bool>>
      to_insert;
    bool capped = false;
    // Indices into `to_insert` whose path exits via a rollback revert; resolved
    // to claim keys after the walk (the key needs the insertion location).
    std::set<size_t> rollback_exits;
    // Indices whose exit shape is ambiguous between a bare require-revert and a
    // plain early return (see the END_FUNCTION arm below).
    std::set<size_t> undetermined_exits;
    // Hard cap on DFS work so a pathological CFG can never exhaust memory.
    size_t pushes = 0;
    const size_t push_cap = 50 * path_cov_max_goals + 100000;

    // Emit one deferred exit assert for a complete path reaching `loc` with
    // path number `penc` and decision depth `pdepth`. Returns false (and sets
    // capped) when the per-function goal cap is hit, so the caller stops.
    auto emit_exit = [&](
                       goto_programt::targett loc,
                       uint64_t penc,
                       uint64_t pdepth,
                       bool is_revert) -> bool {
      if (to_insert.size() >= path_cov_max_goals)
      {
        capped = true;
        ++dropped_paths;
        return false;
      }
      // assert(tr != enc || cnt != depth): falsified only on the exact path
      // (same decision sequence AND same length), so a wrapped tr from a longer
      // path cannot fire this shorter path's assert.
      expr2tc g = or2tc(
        notequal2tc(tr, constant_int2tc(utype, BigInt(penc))),
        notequal2tc(cnt, constant_int2tc(utype, BigInt(pdepth))));
      std::string comment =
        id2string(f_it->first) + ":path:" + std::to_string(penc);
      to_insert.emplace_back(loc, g, comment, is_revert);
      return true;
    };

    // True iff `i` is the lowered call of a custom-error `revert E()` — a
    // FUNCTION_CALL to a `#sol_error` function whose body is ASSUME(false).
    // Reaching such a call means the path reverts unconditionally, so its
    // identity assert must be placed right BEFORE the call (upstream of the
    // callee's ASSUME(false)); an END_FUNCTION-placed assert would be
    // downstream and vacuous. Checking the call instruction itself (rather than
    // the incoming edge) catches EVERY revert shape — guarded `if(c) revert
    // E()`, straight-line `revert E()` as the whole body, and reverts reached
    // via an intervening unconditional GOTO — because the DFS always walks onto
    // the call instruction. require()/revert("msg") lower to a state-restoring
    // rollback with NO #sol_error call, so they are excluded and correctly fall
    // through to END_FUNCTION (try/catch). (codex: an earlier per-edge check
    // missed unguarded / goto-reached reverts; this instruction check fixes it.)
    // True iff `i` is the state-restoring assignment of a rollback revert:
    // `require(cond)` / `require(cond,"msg")` / `revert("msg")` in a function
    // with an entry snapshot lower to `*this = _sol_save_this` followed by a
    // jump to END_FUNCTION. Keying on the frontend's canonical snapshot symbol
    // name is exact — no other assignment sources it. A path that walks over
    // this instruction reverts, even though it reaches END_FUNCTION like a
    // normal exit.
    auto is_rollback_restore = [&](goto_programt::const_targett i) -> bool {
      if (!i->is_assign() || !is_code_assign2t(i->code))
        return false;
      const expr2tc &src = to_code_assign2t(i->code).source;
      return is_symbol2t(src) &&
             to_symbol2t(src).thename.as_string().find("_sol_save_this") !=
               std::string::npos;
    };

    // True iff `i` is the frontend's explicit revert marker
    // `_ESBMC_sol_mark_revert()`. Under the revert-observation gate (which
    // --solidity-path-coverage turns on) EVERY require/revert failure edge
    // carries this call, including the shapes that emit no state restore at
    // all. It is the ONLY positive evidence separating a reverting exit from a
    // plain early `return`: both otherwise lower to the identical
    // `IF <guard> THEN GOTO <END_FUNCTION>`.
    auto is_revert_mark = [&](goto_programt::const_targett i) -> bool {
      if (!i->is_function_call() || !is_code_function_call2t(i->code))
        return false;
      const expr2tc &fn = to_code_function_call2t(i->code).function;
      return is_symbol2t(fn) &&
             to_symbol2t(fn).thename.as_string().find(
               "_ESBMC_sol_mark_revert") != std::string::npos;
    };

    // True iff `i` is the function EPILOGUE's restore of the enclosing-contract
    // context (`_ESBMC_enclosing_contract_address = _saved_encl_addr`). Every
    // ordinary exit of a Solidity public function walks through it; a
    // `require`-failure edge that precedes any state write is compiled as a
    // BARE jump straight to END_FUNCTION and skips it. That makes "did this
    // path pass the epilogue?" the only positive evidence available to tell an
    // ordinary exit from such a bare revert edge.
    auto is_epilogue_restore = [&](goto_programt::const_targett i) -> bool {
      if (!i->is_assign() || !is_code_assign2t(i->code))
        return false;
      const expr2tc &src = to_code_assign2t(i->code).source;
      return is_symbol2t(src) &&
             to_symbol2t(src).thename.as_string().find("_saved_encl_addr") !=
               std::string::npos;
    };

    // Does this function HAVE an epilogue at all? Without one the marker above
    // carries no information, so every path would look "bypassed" and be
    // reported undetermined. Only apply the test where it is meaningful.
    bool has_epilogue = false;
    forall_goto_program_instructions (eit, goto_program)
      if (is_epilogue_restore(eit))
      {
        has_epilogue = true;
        break;
      }

    auto is_error_call = [&](goto_programt::const_targett i) -> bool {
      if (!i->is_function_call() || !is_code_function_call2t(i->code))
        return false;
      const expr2tc &fn = to_code_function_call2t(i->code).function;
      if (!is_symbol2t(fn))
        return false;
      const symbolt *s = ns.lookup(to_symbol2t(fn).thename);
      return s && !s->type.get("#sol_error").as_string().empty();
    };

    while (!stack.empty())
    {
      auto [pc, enc, becnt, depth, rolled_back, saw_epilogue] = stack.back();
      stack.pop_back();

      while (true)
      {
        if (pc == goto_program.instructions.end() || pc->is_end_function())
        {
          if (pc != goto_program.instructions.end())
          {
            if (!emit_exit(pc, enc, depth, false))
              break;
            const size_t idx = to_insert.size() - 1;
            if (rolled_back)
              // Positive evidence of a rollback revert.
              rollback_exits.insert(idx);
            else if (!has_epilogue || !saw_epilogue)
              // No positive evidence of a normal exit. Either the path reached
              // END_FUNCTION while SKIPPING the epilogue, or the function has
              // no epilogue at all (library / free function — exactly the
              // scopes the revert-observation gate does NOT mark, so a revert
              // there carries no marker either). Both a `require` failing
              // before any state write and a plain early `return` compile to
              // this same shape, with nothing on the edge to separate them.
              // Report undetermined rather than guess: calling it "normal"
              // would claim a reverted transaction succeeded — measured on
              // a library whose function reverts, that is exactly what the
              // previous "no epilogue => normal" default did.
              undetermined_exits.insert(idx);
          }
          break;
        }
        if (is_rollback_restore(pc) || is_revert_mark(pc))
          rolled_back = true;
        if (is_epilogue_restore(pc))
          saw_epilogue = true;
        // Custom-error revert exit: the DFS reached the `#sol_error` call
        // (guarded, straight-line, or via an unconditional GOTO). Emit the
        // identity assert HERE (upstream of the callee's ASSUME(false), so it is
        // reachable) and stop; flag it for vm.expectRevert() (R0).
        if (is_error_call(pc))
        {
          if (!emit_exit(pc, enc, depth, true))
            break;
          break;
        }
        if (pc->is_goto())
        {
          const bool back = pc->is_backwards_goto();
          if (is_true(pc->guard))
          {
            // Unconditional goto. A backward one is one iteration of the loop
            // whose head is the goto's target; bound that loop independently.
            if (back)
            {
              const unsigned key = pc->get_target()->target_number;
              if (becnt[key] >= path_cov_unwind)
                break; // this loop's bound reached: path truncated
              ++becnt[key];
            }
            pc = pc->get_target();
            continue;
          }
          // Conditional. Keep enc within 64 bits (leading sentinel + one bit
          // per decision on the path); drop over-long paths rather than alias.
          if (enc >= (uint64_t(1) << 62))
          {
            ++dropped_paths;
            break;
          }
          // guard-true/taken successor -> target. If that edge is a loop
          // back-edge, its own loop is bounded by path_cov_unwind.
          bool take = true;
          becntt becnt_taken = becnt;
          if (back)
          {
            const unsigned key = pc->get_target()->target_number;
            if (becnt_taken[key] >= path_cov_unwind)
              take = false;
            else
              ++becnt_taken[key];
          }
          // Push the guard-true/taken successor; the guard-false/fall-through
          // continues in-place. A reverting successor (custom-error revert) is
          // detected at the top of the loop when the DFS reaches the
          // `#sol_error` call instruction, so no per-edge revert test is needed.
          if (take)
          {
            if (++pushes > push_cap)
            {
              capped = true;
              ++dropped_paths;
              break;
            }
            stack.push_back(
              {pc->get_target(),
               enc * 2 + 1,
               becnt_taken,
               depth + 1,
               rolled_back,
               saw_epilogue});
          }
          // guard-false/fallthrough successor -> next (never a back-edge).
          enc = enc * 2 + 0;
          ++depth;
          pc = std::next(pc);
          continue;
        }
        // Folded short-circuit/ternary operands (no control-flow branch):
        // each was snapshotted into tr in Phase 1. Fan the DFS out over the
        // 2^K operand-value combinations, appending K bits to enc/depth (in
        // collect order, matching tr) so each combination is a distinct path.
        if (
          (pc->is_assign() || pc->is_return()) &&
          pc->location.property().as_string() != "skipped")
        {
          const expr2tc &src = pc->is_assign()
                                 ? to_code_assign2t(pc->code).source
                                 : to_code_return2t(pc->code).operand;
          size_t K = 0;
          collect_short_circuit_decisions(src, [&](const expr2tc &) { ++K; });
          // Cap MUST match Phase 1's (see SC_DECISION_MAX): a site Phase 1
          // skipped contributes nothing to tr/cnt, so the DFS must not add bits
          // for it either — and vice versa.
          if (K > 0 && K <= SC_DECISION_MAX)
          {
            bool overflowed = false;
            for (uint64_t mask = 0; mask < (uint64_t(1) << K); ++mask)
            {
              uint64_t e = enc, d = depth;
              for (size_t j = 0; j < K; ++j)
              {
                if (e >= (uint64_t(1) << 62))
                {
                  overflowed = true;
                  break;
                }
                e = e * 2 + ((mask >> j) & 1);
                ++d;
              }
              if (overflowed)
              {
                ++dropped_paths;
                break;
              }
              if (++pushes > push_cap)
              {
                capped = true;
                ++dropped_paths;
                break;
              }
              stack.push_back(
                {std::next(pc), e, becnt, d, rolled_back, saw_epilogue});
            }
            break; // this path forked into the 2^K continuations
          }
        }
        pc = std::next(pc); // straight-line
      }
      if (capped)
        break;
    }

    size_t ins_idx = 0;
    for (auto &[pc, g, comment, is_revert] : to_insert)
    {
      const size_t this_idx = ins_idx++;
      // Claim key == the (comment, location) pair get_total_cond_assert() and
      // bmc.cpp's claim_sig use, so universe / covered-set / numerator stay
      // key-aligned. insert_assert copies pc->location onto the new assert,
      // so reading it here (pre-insert) gives the same string.
      const std::string loc = pc->location.as_string();
      const std::pair<std::string, std::string> key(comment, loc);
      // Static universe FIRST: every enumerated path counts in the
      // denominator whether or not it is instrumented this round.
      all_claims.insert(key);
      // exit_kind for the report: this path leaves via a detected
      // custom-error revert rather than the normal END_FUNCTION exit.
      if (is_revert)
        revert_paths.insert(key);
      // ...or via a require/revert("msg") rollback, which reaches END_FUNCTION
      // but still reverts the transaction.
      if (rollback_exits.count(this_idx))
        rollback_revert_paths.insert(key);
      if (undetermined_exits.count(this_idx))
        undetermined_exit_paths.insert(key);
      if (covered_set.count(key))
      {
        ++skipped_paths; // already witnessed in an earlier round
        continue;
      }
      insert_assert(goto_program, pc, g, comment);
      // Stamp the just-inserted assert (now at std::prev(pc)) so the Foundry
      // generator emits vm.expectRevert() for this detected revert path (R0).
      //
      // ONLY for `is_revert`, whose assert sits at the `#sol_error` call — an
      // instruction reachable on that path ALONE. A rollback revert's assert
      // sits at the shared END_FUNCTION, where every path's assert is stacked:
      // the generator marks a transaction as reverting when ANY reached assert
      // step carries the flag, so stamping there makes a NON-reverting path's
      // counterexample pick the flag up and emit `vm.expectRevert()` before a
      // call that does not revert — a test that fails when run. Measured: all 3
      // of D's tests (two of them normal paths) got the wrapper.
      // The JSON already carries `exit_kind: "revert"` for these, so a
      // generator can emit the oracle from there without this bleed.
      if (is_revert)
        std::prev(pc)->location.set("sol_revert_edge", true);
      ++total_paths;
    }

  }

  // all_claims is the no-skip static universe built in the loop above (one
  // entry per enumerated complete path), NOT get_total_cond_assert() — the
  // latter counts instrumented asserts only, so a covered-set skip would
  // shrink the denominator and spuriously inflate coverage.
  if (dropped_paths > 0)
    log_warning(
      "--solidity-path-coverage: per-function path/length cap ({}) hit; {} "
      "path(s) dropped (coverage is complete only up to the cap for those "
      "functions)",
      path_cov_max_goals,
      dropped_paths);
  log_status(
    "--solidity-path-coverage: instrumented {} complete path(s) "
    "(loop bound = {} iterations)",
    total_paths,
    path_cov_unwind);
  if (sc_sites_over_cap > 0)
    log_warning(
      "--solidity-path-coverage: {} folded short-circuit/ternary site(s) have "
      "more than {} operands and were NOT treated as decisions; the paths "
      "through them are merged rather than enumerated (they stay coverable, "
      "but the decision set is incomplete at those sites)",
      sc_sites_over_cap,
      SC_DECISION_MAX);
  if (skipped_paths > 0)
    log_status(
      "--solidity-path-coverage: {} path(s) already witnessed in a previous "
      "round were not re-instrumented (covered-set {}); denominator remains "
      "the full {} path(s)",
      skipped_paths,
      covered_set_path,
      all_claims.size());

  goto_functions.update();
}

void goto_coveraget::insert_assert(
  goto_programt &goto_program,
  goto_programt::targett &it,
  const expr2tc &guard)
{
  insert_assert(goto_program, it, guard, from_expr(ns, "", guard));
}

/*
  convert
    1: DECL x   <--- it
    ASSIGN X 1
  to
    1: ASSERT(guard);
    DECL x      <--- it
    ASSIGN X 1  
*/
void goto_coveraget::insert_assert(
  goto_programt &goto_program,
  goto_programt::targett &it,
  const expr2tc &guard,
  const std::string &idf)
{
  goto_programt::instructiont instruction;
  instruction.make_assertion(guard);
  instruction.location = it->location;
  instruction.function = it->function;
  instruction.location.property("instrumented assertion");
  instruction.location.comment(idf);
  instruction.location.user_provided(true);
  goto_program.insert_swap(it++, instruction);
  it--;
}

int goto_coveraget::get_total_instrument() const
{
  int total_instrument = 0;
  forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      const goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      forall_goto_program_instructions (it, goto_program)
      {
        if (
          it->is_assert() &&
          it->location.property().as_string() == "instrumented assertion" &&
          it->location.user_provided() == true)
        {
          total_instrument++;
        }
      }
    }
  return total_instrument;
}

// Count the total assertion instances in goto level via goto-unwind api
// run the algorithm on the copy of the original goto program
int goto_coveraget::get_total_assert_instance() const
{
  // 1. execute goto unwind
  bounded_loop_unroller unwind_loops;
  unwind_loops.run(goto_functions);
  // 2. calculate the number of assertion instance
  return get_total_instrument();
}

std::set<std::pair<std::string, std::string>>
goto_coveraget::get_total_cond_assert() const
{
  std::set<std::pair<std::string, std::string>> total_cond_assert = {};
  forall_goto_functions (f_it, goto_functions)
  {
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      const goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      forall_goto_program_instructions (it, goto_program)
      {
        if (
          it->is_assert() &&
          it->location.property().as_string() == "instrumented assertion" &&
          it->location.user_provided() == true)
        {
          std::pair<std::string, std::string> claim_pair = std::make_pair(
            it->location.comment().as_string(), it->location.as_string());
          total_cond_assert.insert(claim_pair);
        }
      }
    }
  }
  return total_cond_assert;
}

/*
  Condition Coverage: fault injection
  1. find condition statements, this includes the converted for_loop/while
  2. insert assertion instances before that statement.
  e.g.
    if (a >1)
  =>
    assert(!(a>1))
    assert(a>1)
    if(a>1)
  then run multi-property
*/
void goto_coveraget::condition_coverage()
{
  // we need to skip the conditions within the built-in library
  // while keeping the file manually included by user
  // this filter, however, is unsound.. E.g. if the src filename is the same as the builtin library name
  total_cond = {{}};

  std::unordered_set<std::string> location_pool = {};
  // cmdline.arg[0]
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  Forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;

      Forall_goto_program_instructions (it, goto_program)
      {
        std::string cur_filename =
          get_filename_from_path(it->location.file().as_string());
        if (location_pool.count(cur_filename) == 0)
          continue;

        if (it->location.property().as_string() == "skipped")
          // this stands for the auxiliary condition/branch we added.
          continue;

        /* 
          Places that could contains condition
          1. GOTO:          if (x == 1);
          2. ASSIGN:        int x = y && z;
          3. ASSERT
          4. ASSUME
          5. FUNCTION_CALL  test((signed int)(x != y));
          6. RETURN         return x && y;
          7. Other          1?2?3:4
          The issue is that, the side-effects have been removed 
          thus the condition might have been split or modified.

          For assert, assume and goto, we know it contains GUARD
          For others, we need to convert the code back to expr and
          check there operands.
        */

        // Skip ASSUME instructions: __VERIFIER_assume / __ESBMC_assume
        // express path constraints, not program logic, so their guards
        // must not contribute to condition-coverage claims (issue #4291).
        if (it->is_assume())
          continue;

        // e.g. assert(a == 1);
        if (
          it->is_assert() &&
          it->location.property().as_string() != "replaced assertion")
        {
          if (!is_nil_expr(it->guard))
          {
            expr2tc guard = handle_single_guard(it->guard, true);
            gen_cond_cov_assert(guard, expr2tc(), goto_program, it);
            // after adding the instrumentation, we neutralize the original assert
            if (cov_assume_asserts)
              replace_assert_to_assume(it);
            else
              replace_assert_to_guard(gen_true_expr(), it, false);
          }
        }

        // e.g. IF !(a > 1) THEN GOTO 3
        else if (it->is_goto() && !is_true(it->guard))
        {
          // e.g.
          //    GOTO 2;
          //    2: IF(...);
          if (it->is_target())
            target_num = it->target_number;

          // preprocessing: if(true) ==> if(true == true)
          expr2tc guard = handle_single_guard(it->guard, true);
          gen_cond_cov_assert(guard, expr2tc(), goto_program, it);
        }

        // e.g. bool x = (a>b);
        else if (it->is_assign())
        {
          const expr2tc &rhs = to_code_assign2t(it->code).source;
          if (!is_nil_expr(rhs))
            handle_operands_guard(rhs, goto_program, it);
        }

        // a>b;
        else if (it->is_other())
        {
          if (is_code_expression2t(it->code))
          {
            const expr2tc &other = to_code_expression2t(it->code).operand;
            if (!is_nil_expr(other))
              handle_operands_guard(other, goto_program, it);
          }
        }

        // e.g. RETURN a>b;
        else if (it->is_return())
        {
          const expr2tc &ret = to_code_return2t(it->code).operand;
          if (!is_nil_expr(ret))
            handle_operands_guard(ret, goto_program, it);
        }

        // e.g. func(a>b);
        else if (it->is_function_call())
        {
          for (const expr2tc &op : to_code_function_call2t(it->code).operands)
            if (!is_nil_expr(op))
              handle_operands_guard(op, goto_program, it);
        }

        // reset target number
        target_num = -1;
      }
    }

  total_cond = get_total_cond_assert();
  all_claims = total_cond;

  // recalculate line number/ target number
  goto_functions.update();
}

/*
  algo:
  if(b==0 && c > 90)
  => assert(b==0)
  => assert(!(b==0));
  => assert(!(b==0 && c>90))
  => assert(!(b==0 && !(c>90)))

  if(b==0 || c > 90)
  => assert(b==0)
  => assert((b==0));
  => assert(!(!b==0 && c>90))
  => assert(!(!(b==0) && !(c>90)))
*/
/// Recurse into all sub-expressions of @p ptr, calling
/// gen_cond_cov_assert on each.
void goto_coveraget::gen_cond_cov_assert(
  const expr2tc &ptr,
  const expr2tc &pre_cond,
  goto_programt &goto_program,
  goto_programt::instructiont::targett &it)
{
  if (is_nil_expr(ptr))
    return;
  const std::size_t n = ptr->get_num_sub_exprs();
  if (n == 0)
    return; // atom

  auto recurse_all = [&]() {
    for (std::size_t i = 0; i < n; ++i)
      gen_cond_cov_assert(*ptr->get_sub_expr(i), pre_cond, goto_program, it);
  };

  if (n == 1)
  {
    // (a!=0)++, !a, -a, (_Bool)(int)a
    recurse_all();
  }
  else if (n == 2)
  {
    if (is_comparison_expr(ptr))
    {
      recurse_all();
      add_cond_cov_assert(ptr, pre_cond, goto_program, it);
    }
    else if (is_and2t(ptr))
    {
      const expr2tc &lhs = *ptr->get_sub_expr(0);
      const expr2tc &rhs = *ptr->get_sub_expr(1);
      gen_cond_cov_assert(lhs, pre_cond, goto_program, it);

      // update pre-condition: pre_cond && lhs
      expr2tc new_pre =
        is_nil_expr(pre_cond) ? lhs : gen_and_expr(pre_cond, lhs);
      gen_cond_cov_assert(rhs, new_pre, goto_program, it);
    }
    else if (is_or2t(ptr))
    {
      const expr2tc &lhs = *ptr->get_sub_expr(0);
      const expr2tc &rhs = *ptr->get_sub_expr(1);
      gen_cond_cov_assert(lhs, pre_cond, goto_program, it);

      // update pre-condition: !(pre_cond && lhs)
      expr2tc new_pre =
        is_nil_expr(pre_cond) ? lhs : gen_and_expr(pre_cond, lhs);
      new_pre = gen_not_expr(new_pre);
      gen_cond_cov_assert(rhs, new_pre, goto_program, it);
    }
    else
    {
      // a+=b; a>>(b!=0);
      recurse_all();
    }
  }
  else if (n == 3)
  {
    // ternary if
    const expr2tc &cond = *ptr->get_sub_expr(0);
    const expr2tc &t_val = *ptr->get_sub_expr(1);
    const expr2tc &f_val = *ptr->get_sub_expr(2);

    gen_cond_cov_assert(cond, pre_cond, goto_program, it);

    // update pre-condition: pre_cond && cond
    expr2tc pre_cond_1 =
      is_nil_expr(pre_cond) ? cond : gen_and_expr(pre_cond, cond);
    gen_cond_cov_assert(t_val, pre_cond_1, goto_program, it);

    // update pre-condition: pre_cond && !cond
    expr2tc not_cond = gen_not_expr(cond);
    expr2tc pre_cond_2 =
      is_nil_expr(pre_cond) ? not_cond : gen_and_expr(pre_cond, not_cond);
    gen_cond_cov_assert(f_val, pre_cond_2, goto_program, it);
  }
  else
  {
    log_error("unexpected operand size");
    abort();
  }
}

void goto_coveraget::add_cond_cov_assert(
  const expr2tc &expr,
  const expr2tc &pre_cond,
  goto_programt &goto_program,
  goto_programt::instructiont::targett &it)
{
  expr2tc cond = is_nil_expr(pre_cond) ? expr : gen_and_expr(pre_cond, expr);

  // e.g. assert(!(a==1));  // a==1
  // the idf is used as the claim_msg
  // note that it's different from the actual guard.
  std::string idf = from_expr(ns, "", expr);
  expr2tc guard = gen_not_expr(cond);
  insert_assert(goto_program, it, guard, idf);

  // reversal
  expr2tc not_expr = gen_not_expr(expr);
  cond = is_nil_expr(pre_cond) ? not_expr : gen_and_expr(pre_cond, not_expr);
  idf = from_expr(ns, "", not_expr);
  guard = gen_not_expr(cond);
  insert_assert(goto_program, it, guard, idf);
}

expr2tc goto_coveraget::gen_not_eq_expr(const expr2tc &lhs, const expr2tc &rhs)
{
  expr2tc _lhs = (lhs->type == rhs->type) ? lhs : typecast2tc(rhs->type, lhs);
  return notequal2tc(_lhs, rhs);
}

expr2tc goto_coveraget::gen_and_expr(const expr2tc &lhs, const expr2tc &rhs)
{
  type2tc bt = get_bool_type();
  expr2tc _lhs = is_bool_type(lhs->type) ? lhs : typecast2tc(bt, lhs);
  expr2tc _rhs = is_bool_type(rhs->type) ? rhs : typecast2tc(bt, rhs);
  return and2tc(_lhs, _rhs);
}

expr2tc goto_coveraget::gen_not_expr(const expr2tc &guard)
{
  if (is_not2t(guard))
    return to_not2t(guard).value;
  return not2tc(guard);
}

/*
  This function convert single guard to a non_equal_to_false expression
  e.g. if(true) ==> if(true!=false)
  rule:
  1. No-op: Do nothing. This means it's a symbol or constant
  2. Binary OP: for boolean expreession, e.g. a>b, a==b, do nothing
  3. Binary OP: for and/or expresson, add on both side, if possible. Do not add if it's already a binary boolean expression in 2. 
    e.g. if(x==1 && a++) => if(x==1 && a++ !=0)
  4. Others: for any other expresison, including unary, binary and teranry, traverse its op with handle_single_guard recursivly. convert it to not equal in the top level only.
    e.g. if((bool)a+b+c) => if((bool)(a+b+c)!=0)
    typecast <--- add not equal here
    - +
      - a
      - + 
        - b
        - c
  e.g. if(a) => if(a!=0); if(true) => if(true != 0); if(a?b:c:d) => if((a?b:c:d)!=0)
  if(a==b) => if(a==b); if(a&&b) => if(a != 0 && b!=0 )
*/
/// Recursively maps each operand of @p expr through handle_single_guard
/// (with the supplied @p sub_top_level), in place. Foreach_operand detaches
/// the irep_container before mutating, so this is safe even when @p expr
/// shares storage with its caller.
static void replace_operands(
  expr2tc &expr,
  bool sub_top_level,
  const std::function<expr2tc(const expr2tc &, bool)> &recurse)
{
  expr->Foreach_operand([&](expr2tc &op) { op = recurse(op, sub_top_level); });
}

expr2tc goto_coveraget::handle_single_guard(
  const expr2tc &expr,
  bool top_level /* = true */)
{
  if (is_nil_expr(expr))
    return expr;
  const std::size_t n = expr->get_num_sub_exprs();
  auto recurse = [this](const expr2tc &e, bool tl) {
    return handle_single_guard(e, tl);
  };

  // --- Rule 1: Atomic expressions ---
  // If the expression has no operands (a symbol or constant),
  // then if it's Boolean and we're at the outer guard, wrap it with
  // "!= false".
  if (n == 0)
  {
    if (top_level && is_bool_type(expr->type))
      return gen_not_eq_expr(expr, gen_false_expr());
    return expr;
  }

  // --- Special-case for "not" nodes ---
  // For a "not" operator, process its operand with top_level = true so that
  // even nested atomic expressions (like x in !(!(x))) get wrapped.
  if (is_not2t(expr))
  {
    expr2tc result = expr;
    replace_operands(result, /*sub_top_level=*/true, recurse);
    return result;
  }

  // --- Special-case for typecasts to bool ---
  // If we have (bool)(X) and X is not already a recognized guard
  // (comparison or logical AND/OR), unwrap the typecast and wrap X.
  if (is_typecast2t(expr) && is_bool_type(expr->type))
  {
    expr2tc inner = handle_single_guard(to_typecast2t(expr).from, top_level);
    if (!(is_comparison_expr(inner) || is_and2t(inner) || is_or2t(inner)))
      return gen_not_eq_expr(inner, gen_false_expr());
    return inner;
  }

  // --- Process Binary Operators (exactly 2 operands) ---
  if (n == 2)
  {
    expr2tc result = expr;
    if (is_and2t(expr) || is_or2t(expr))
    {
      // Process each operand as an independent guard (top_level = true).
      replace_operands(result, /*sub_top_level=*/true, recurse);
      return result;
    }
    if (is_comparison_expr(expr))
    {
      replace_operands(result, /*sub_top_level=*/false, recurse);
      return result;
    }
    // Other binary operators (e.g. arithmetic '+').
    replace_operands(result, /*sub_top_level=*/false, recurse);
    if (top_level)
      return gen_not_eq_expr(result, gen_false_expr());
    return result;
  }

  // --- Process Non-Binary Operators (Unary, Ternary, etc.) ---
  expr2tc result = expr;
  replace_operands(result, /*sub_top_level=*/false, recurse);

  // For any other expression producing a Boolean value, if at the outer
  // guard (top_level true) and its kind is not among our no-wrap set, then
  // wrap it with "!= false". This catches cases like member accesses.
  if (
    top_level && is_bool_type(result->type) && !is_and2t(result) &&
    !is_or2t(result) && !is_not2t(result) && !is_comparison_expr(result))
    return gen_not_eq_expr(result, gen_false_expr());
  return result;
}

/*
  add condition instrumentation for OTHER, ASSIGN, FUNCTION_CALL..
  whose operands might contain conditions
  we handle guards for each boolean sub-operand.
*/
void goto_coveraget::handle_operands_guard(
  const expr2tc &expr,
  goto_programt &goto_program,
  goto_programt::instructiont::targett &it)
{
  if (is_nil_expr(expr))
    return;
  const std::size_t n = expr->get_num_sub_exprs();
  if (n == 0)
    return;

  expr2tc pre_cond; // nil

  if (n == 1)
  {
    // e.g. RETURN ++(x&&y);
    handle_operands_guard(*expr->get_sub_expr(0), goto_program, it);
  }
  else if (n == 2)
  {
    expr2tc target = expr;
    if (is_and2t(expr) || is_or2t(expr))
    {
      // we do not need to add a !=false at top level
      // e.g. return x?1:0 != return (x?1:0)!=false
      target->Foreach_operand(
        [this](expr2tc &op) { op = handle_single_guard(op, false); });
    }
    gen_cond_cov_assert(target, pre_cond, goto_program, it);
  }
  else
  {
    // this could only be ternary boolean
    expr2tc rewrapped = handle_single_guard(expr, false);
    gen_cond_cov_assert(rewrapped, pre_cond, goto_program, it);
  }
}

// set the target function from "--function"
void goto_coveraget::set_target(const std::string &_tgt)
{
  target_function = _tgt;
}

// check if it's the target function
bool goto_coveraget::is_target_func(
  const irep_idt &f,
  const std::string &tgt_name) const
{
  const symbolt *sym = ns.lookup(f);
  if (sym == nullptr)
  {
    log_error("Cannot find target function");
    abort();
  }

  exprt symbol = symbol_expr(*ns.lookup(f));
  std::string sym_name = symbol.name().as_string();
  if (sym_name == tgt_name)
    return true;

  // For Solidity: modifier expansion renames functions from "func" to
  // "func_modifierName". Support prefix matching so that --function func
  // matches func_modifierName.
  if (
    config.language.lid == language_idt::SOLIDITY &&
    sym_name.size() > tgt_name.size() &&
    sym_name.substr(0, tgt_name.size()) == tgt_name &&
    sym_name[tgt_name.size()] == '_')
    return true;

  return false;
}

// Parse the --negating-property spec "[contract:]function[:line]".
//   1 token  -> function
//   2 tokens -> last all-digits: function:line ; else contract:function
//   3 tokens -> contract:function:line
// A malformed spec (>3 tokens or empty function) degrades to treating the
// whole string as the function name (backward compatible). `line` stays
// empty when no line is given; it is kept as a string so it can be compared
// directly against the instruction location, with no integer parsing.
static void parse_negate_spec(
  const std::string &spec,
  std::string &contract,
  std::string &fname,
  std::string &line)
{
  contract.clear();
  line.clear();

  auto all_digits = [](const std::string &s) {
    return !s.empty() && s.find_first_not_of("0123456789") == std::string::npos;
  };

  std::vector<std::string> tok;
  size_t start = 0;
  for (size_t pos = spec.find(':'); pos != std::string::npos;
       pos = spec.find(':', start))
  {
    tok.push_back(spec.substr(start, pos - start));
    start = pos + 1;
  }
  tok.push_back(spec.substr(start));

  if (tok.size() == 1)
    fname = tok[0];
  else if (tok.size() == 2)
  {
    if (all_digits(tok[1]))
    {
      fname = tok[0];
      line = tok[1];
    }
    else
    {
      contract = tok[0];
      fname = tok[1];
    }
  }
  else if (tok.size() == 3)
  {
    contract = tok[0];
    fname = tok[1];
    if (all_digits(tok[2]))
      line = tok[2];
  }
  else
  {
    log_warning(
      "--negating-property: malformed spec '{}', treating it as a plain "
      "function name",
      spec);
    fname = spec;
  }

  if (fname.empty())
  {
    log_warning(
      "--negating-property: empty function name in spec '{}', treating it as "
      "a plain function name",
      spec);
    contract.clear();
    line.clear();
    fname = spec;
  }
}

// Extract the contract name from a Solidity mangled id of the form
// "sol:@C@<Contract>@F@...". Returns "" for non-contract / non-Solidity ids.
static std::string contract_of(const std::string &mangled_id)
{
  const std::string c_tag = "@C@";
  const std::string f_tag = "@F@";
  size_t cpos = mangled_id.find(c_tag);
  if (cpos == std::string::npos)
    return "";
  cpos += c_tag.size();
  size_t fpos = mangled_id.find(f_tag, cpos);
  if (fpos == std::string::npos || fpos <= cpos)
    return "";
  return mangled_id.substr(cpos, fpos - cpos);
}

// negate the condition inside the assertion
// The idea is that, if the claim is verified safe, and its negated claim is also verified safe, then we say this claim is unreachable
void goto_coveraget::negating_asserts(const std::string &tgt_spec)
{
  std::string contract, fname, target_line;
  parse_negate_spec(tgt_spec, contract, fname, target_line);

  std::string old = target_function;
  target_function = fname;

  std::unordered_set<std::string> location_pool = {};
  location_pool.insert(get_filename_from_path(filename));
  for (auto const &inc : config.ansi_c.include_files)
    location_pool.insert(get_filename_from_path(inc));

  // First pass: collect candidate asserts in functions matching the
  // function-name filter (and the optional case-sensitive contract filter),
  // restricted to the user source files.
  std::vector<goto_programt::instructiont::targett> candidates;
  Forall_goto_functions (f_it, goto_functions)
    if (f_it->second.body_available && f_it->first != "__ESBMC_main")
    {
      goto_programt &goto_program = f_it->second.body;
      if (filter(f_it->first, goto_program))
        continue;
      if (!contract.empty() && contract_of(f_it->first.as_string()) != contract)
        continue;

      Forall_goto_program_instructions (it, goto_program)
      {
        std::string cur_filename =
          get_filename_from_path(it->location.file().as_string());
        if (location_pool.count(cur_filename) == 0)
          continue;

        if (it->is_assert())
          candidates.push_back(it);
      }
    }

  // Select the asserts to negate. When a line is given, keep only those on
  // that source line; if none match, silently fall back to whole-function
  // negation (all candidates).
  std::vector<goto_programt::instructiont::targett> matched;
  if (!target_line.empty())
  {
    for (auto &it : candidates)
      if (it->location.get_line().as_string() == target_line)
        matched.push_back(it);
    if (matched.empty())
    {
      log_debug(
        "coverage",
        "--negating-property: no assert at line {} in '{}', falling back to "
        "whole-function negation",
        target_line,
        fname);
      matched = candidates;
    }
  }
  else
    matched = candidates;

  for (auto &it : matched)
    replace_assert_to_guard(gen_not_expr(it->guard), it, false);

  target_function = old;
}

// return true if this function is skipped
bool goto_coveraget::filter(
  const irep_idt &func_name,
  const goto_programt &goto_program) const
{
  // "--function" mode
  if (target_function != "" && !is_target_func(func_name, target_function))
    return true;

  // Skip the function that is labelled with "__ESBMC_HIDE"
  // Extended to support Python in addition to Solidity
  if (
    goto_program.hide && (config.language.lid == language_idt::SOLIDITY ||
                          config.language.lid == language_idt::PYTHON))
    return true;
  return false;
}
