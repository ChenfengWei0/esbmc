#include <csignal>
#include <memory>
#include <sys/types.h>
#include <algorithm>
#include <thread>
#include <chrono>

#ifndef _WIN32
#  include <unistd.h>
#  include <sched.h>
#else
#  include <windows.h>
#  include <winbase.h>
#  undef ERROR
#  undef small
#endif

#include <fmt/format.h>
#include <regex>
#include <ac_config.h>
#include <esbmc/bmc.h>
#include <esbmc/document_subgoals.h>
#include <fstream>
#include <goto-programs/goto_loops.h>
#include <goto-symex/build_goto_trace.h>
#include <goto-symex/goto_trace.h>
#include <goto-symex/features.h>
#include <goto-symex/xml_goto_trace.h>
#include <langapi/language_util.h>
#include <langapi/languages.h>
#include <langapi/mode.h>
#include <sstream>
#include <util/i2string.h>
#include <irep2/irep2.h>
#include <util/location.h>

#include <util/migrate.h>
#include <util/show_symbol_table.h>
#include <util/time_stopping.h>
#include <util/cache.h>
#include <atomic>
#include <nlohmann/json.hpp>

// ---- DEFINED HERE, and deliberately ----
//
// These five are written and read ONLY by this file: the re-solve happens in
// multi_property_check's job loop and the numbers are printed by
// report_coverage, both below. goto_coverage.cpp neither sets nor reads them.
// Putting the definitions beside their only users follows the precedent
// immediately below (goto_functionst's four statics live here for the same
// reason) and means a reader who finds one finds all of them.
std::set<std::pair<std::string, std::string>>
  goto_coveraget::arith_revert_only_paths;
std::atomic<size_t> goto_coveraget::arith_resolve_queries{0};
std::atomic<size_t> goto_coveraget::arith_resolve_replaced{0};
std::atomic<size_t> goto_coveraget::arith_resolve_ms{0};
std::atomic<size_t> goto_coveraget::arith_conditions_seen{0};
std::atomic<size_t> goto_coveraget::arith_revert_only_suppressed{0};
std::atomic<size_t> goto_coveraget::verdicts_preserved{0};

std::unordered_set<std::string> goto_functionst::reached_claims;
std::unordered_multiset<std::string> goto_functionst::reached_mul_claims;
std::mutex goto_functionst::reached_claims_mutex;
std::mutex goto_functionst::reached_mul_claims_mutex;
std::set<std::string> goto_functionst::truncated_loops;
std::mutex goto_functionst::truncated_loops_mutex;

// ---- HOW OFTEN ONE CLAIM KEY WAS DECIDED, AND WHETHER IT CHANGED ITS MIND ----
//
// File-local for the same reason as the block above: the counting happens in
// multi_property_check's job loop and the printing in report_coverage, both in
// this file, and nothing else reads them.
//
// `Verdicts Preserved` already watches ONE direction -- a decision replaced by
// a non-decision. Its own header says a non-zero value "also means the same
// claim key was solved more than once". The converse is false, and that is the
// hole: MEASURED on notes/coverage/poc/P28_MapMin.sol, --solidity-max-tx 2,
// 4 instrumented paths and 8 VCCs, the key `take:path:15` was solved twice and
// got BOTH verdicts --
//
//     take:path:15   solved 2x   verdicts PASSED then FAILED
//     Verdicts Preserved: 0
//
// -- while the counter that is supposed to notice read zero, because the second
// solve DID return a verdict. A decision was contradicted and nothing recorded
// it.
//
// WHY THAT PARTICULAR CONTRADICTION IS NOT AN ERROR. The two solves are two
// INSTANCES of one path: the transaction body is emitted once per transaction,
// so the same assert instruction is reached once per transaction, and a path
// guarded by state that only an earlier transaction can establish holds in tx 1
// and is refuted in tx 2. The path's verdict is the DISJUNCTION over its
// instances, which is exactly what the existing rule computes (F is final, and
// P upgrades to F). Aborting on it -- the literal reading of "one key, two
// verdicts, therefore fatal" -- would abort every multi-transaction run for
// doing the one thing a multi-transaction harness exists to do.
//
// SO THE FATAL IS THE MULTIPLICITY, NOT THE DISAGREEMENT. One key may be
// decided at most once per transaction. More than that is not the disjunction,
// it is the duplicate instrumentation the write site's own comment calls "a
// SECOND defect ... not fixed here": the same claim emitted at more than one
// site, paying for solves that buy nothing and giving one path several chances
// to be witnessed. That is checkable against a number the run already knows.
namespace
{
std::mutex path_cov_solve_count_mutex;
std::map<std::string, size_t> path_cov_solve_count;
// A DECIDED verdict replaced by a DIFFERENT decided verdict (today only
// P -> F). Legitimate, and counted rather than silent: it is the event
// `Verdicts Preserved` cannot see, and a run in which it never happens must be
// distinguishable from one in which it happened forty times.
std::atomic<size_t> path_cov_verdict_upgrades{0};
// Solves of a key beyond its first. Bounded above by (transactions - 1) per
// key when the instrumentation is sound.
std::atomic<size_t> path_cov_extra_solves{0};
// The largest multiplicity seen, and the key that had it. Printed so the line
// is a measurement rather than a pass/fail.
size_t path_cov_max_solves = 0;
std::string path_cov_max_solves_key;
// How many times one key MAY be decided. Set once, from the transaction bound,
// before the job loop starts. 0 means the check is not armed (not a path
// coverage run).
size_t path_cov_allowed_solves = 0;
// Where the ceiling came from. The refusal message must not say "this run
// explores N transaction(s)" when N is an override -- that sentence would be
// FALSE on exactly the runs the override exists to produce, and a diagnostic
// that misstates the run is worse than none.
std::string path_cov_allowed_solves_origin;
} // namespace

bmct::bmct(
  goto_functionst &funcs,
  optionst &opts,
  contextt &_context,
  foundry_generator *ext_foundry_gen)
  : options(opts),
    context(_context),
    ns(context),
    foundry_gen_ext(ext_foundry_gen)
{
  interleaving_number = 0;
  interleaving_failed = 0;

  ltl_results_seen[ltl_res_bad] = 0;
  ltl_results_seen[ltl_res_failing] = 0;
  ltl_results_seen[ltl_res_succeeding] = 0;
  ltl_results_seen[ltl_res_good] = 0;

  // The next block will initialize the algorithms used for the analysis.
  {
    // Run cache if user has specified the option
    if (
      !options.get_bool_option("no-cache-asserts") &&
      !options.get_bool_option("forward-condition") &&
      !options.get_bool_option("k-induction") &&
      !options.get_bool_option("ltl"))
      // Store the set between runs
      algorithms.emplace_back(
        std::make_unique<assertion_cache>(config.ssa_caching_db));

    if (opts.get_bool_option("no-slice"))
      algorithms.emplace_back(std::make_unique<simple_slice>());
    else
      algorithms.emplace_back(std::make_unique<symex_slicet>(options));

    if (opts.get_bool_option("ssa-features-dump"))
      algorithms.emplace_back(std::make_unique<ssa_features>());
  }

  if (options.get_bool_option("smt-during-symex"))
  {
    runtime_solver = std::unique_ptr<smt_convt>(create_solver("", ns, options));

    symex = std::make_unique<reachability_treet>(
      funcs,
      ns,
      options,
      std::make_shared<runtime_encoded_equationt>(ns, *runtime_solver),
      _context);
  }
  else
  {
    symex = std::make_unique<reachability_treet>(
      funcs,
      ns,
      options,
      std::make_shared<symex_target_equationt>(ns),
      _context);
  }
}


// ---- WITNESS MINIMISATION FOR A REFUTED CERTIFICATION SAFETY CLAIM --------
//
// A checked-arithmetic refutation under --path-cov-certify is answered by the
// driver with a CUT at the witness on the coordinate the refutation points at
// (method §Certification). The solver's model is any violating point, and on a
// threshold-shaped violation (`feeBps - discountBps` reverts for discountBps >
// 250) it is typically an extreme one (65535): the cut then removes a sliver
// and the next witness is the next extreme, so a 4-round shrink budget halves
// the interval at best. MEASURED on motivation_FeeVault enc=119 (freeM):
// 65535 -> 33017 -> 32766 -> 16382, budget exhausted, not certified.
//
// The method says nothing about WHICH violating point the tool returns, and a
// witness closer to x_pi leaves "the region as wide as the refutation allows"
// (the method's own criterion for a cut). So, per bounded coordinate on which
// the model differs from x_pi: (1) if the claim is still violable with the
// coordinate AT x_pi's value, pin it there in the model (the coordinate is not
// what this violation is about, and the driver then sees no difference on it);
// (2) otherwise search the least distance from x_pi at which a violation
// exists -- geometric doubling then bisection, each step one incremental
// re-solve under a pushed constraint, SAT steps kept and UNSAT steps popped --
// and leave the tightest constraint in place so the trace harvested below is
// that point. Sound by construction: every kept constraint is satisfied by a
// genuine violating execution, so the reported witness is one. Bounded by a
// solve budget; on budget exhaustion whatever was tightened stands.
static void path_cov_minimise_certify_witness(
  symex_target_equationt &eq,
  smt_convt &solver,
  const std::string &claim_txt)
{
  static const std::string tag = "path-cov-certify-bound:";
  // Per claim; a 256-bit coordinate needs up to 2*256 re-solves for a full
  // gallop+bisect, so the per-coordinate cap is 2*bits+4 and the claim cap
  // covers two such coordinates (TODO 30 #5).
  const size_t solve_cap = 640;
  size_t solves = 0;
  bool changed = false;
  size_t n_assume = 0, n_tagged = 0, n_ignored = 0, n_shape = 0, n_unnamed = 0,
         n_noval = 0, n_equal = 0;
  // The solver must be in a SAT state before `get` is asked for a model value;
  // a popped UNSAT probe leaves it in an UNSAT state (Bitwuzla aborts on
  // get_value there), so the last verdict is tracked and the model refreshed.
  bool last_sat = true;
  auto parse_big = [](const std::string &s) -> BigInt {
    return (s.size() > 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
             ? BigInt(s.c_str() + 2, 16)
             : string2integer(s);
  };
  // (name, coordinate expression) pairs. Handles first -- the ghost
  // `__ESBMC_certify_coord$N := bs` assignments carry the renamed coordinate
  // whatever the bound folded to -- then the bound ASSUME steps for any
  // coordinate without a handle. `state.*` coordinates go before the rest:
  // for a two-coordinate relation (`amount > deposits`, x_pi = (1,1)) pinning
  // the parameter first leaves the state coordinate as the difference and
  // costs the driver an extra round, where pinning the state coordinate
  // first leaves `amount = 2` and the cut lands on the parameter at once.
  std::vector<std::pair<std::string, expr2tc>> targets;
  std::set<std::string> have;
  for (auto &st : eq.SSA_steps)
  {
    if (!st.is_assignment() || st.ignore || is_nil_expr(st.lhs) ||
        !is_symbol2t(st.lhs))
      continue;
    const std::string ln = to_symbol2t(st.lhs).get_symbol_name();
    if (ln.find("__ESBMC_certify_coord$") == std::string::npos)
      continue;
    std::string cname;
    for (const auto &[hid, coord] :
         goto_coveraget::path_cov_certify_coord_handles)
      if (ln.rfind(hid, 0) == 0)
      {
        cname = coord;
        break;
      }
    if (cname.empty() || have.count(cname) || is_nil_expr(st.rhs))
      continue;
    have.insert(cname);
    targets.emplace_back(cname, st.rhs);
  }
  std::stable_sort(
    targets.begin(), targets.end(), [](const auto &a, const auto &b) {
      const bool sa = a.first.rfind("state.", 0) == 0;
      const bool sb = b.first.rfind("state.", 0) == 0;
      return sa && !sb;
    });
  for (auto &st : eq.SSA_steps)
  {
    if (!st.is_assume() || !st.source.is_set)
      continue;
    ++n_assume;
    const std::string cm = st.source.pc->location.comment().as_string();
    if (cm.rfind(tag, 0) != 0)
      continue;
    ++n_tagged;
    if (st.ignore)
    {
      ++n_ignored;
      continue;
    }
    const std::string name = cm.substr(tag.size());
    if (have.count(name))
      continue;
    // The bound was emitted as `and(ge(bs, lo), le(bs, hi))` but symex and
    // the simplifier rewrite it (a `ge(x, 0)` on an unsigned is folded away,
    // the guard may be an implication), so the coordinate expression is
    // recovered as "the non-constant side of the first comparison found".
    expr2tc bs;
    {
      std::vector<expr2tc> stack{st.cond};
      while (!stack.empty() && is_nil_expr(bs))
      {
        expr2tc e = stack.back();
        stack.pop_back();
        if (is_nil_expr(e))
          continue;
        if (is_and2t(e))
        {
          stack.push_back(to_and2t(e).side_2);
          stack.push_back(to_and2t(e).side_1);
        }
        else if (is_implies2t(e))
          stack.push_back(to_implies2t(e).side_2);
        else if (is_not2t(e))
          stack.push_back(to_not2t(e).value);
        else if (
          is_greaterthanequal2t(e) || is_lessthanequal2t(e) ||
          is_greaterthan2t(e) || is_lessthan2t(e) || is_equality2t(e) ||
          is_notequal2t(e))
        {
          const expr2tc &a = *e->get_sub_expr(0);
          const expr2tc &b = *e->get_sub_expr(1);
          if (is_constant_int2t(b) && !is_constant_int2t(a))
            bs = a;
          else if (is_constant_int2t(a) && !is_constant_int2t(b))
            bs = b;
        }
      }
    }
    if (is_nil_expr(bs))
    {
      ++n_shape;
      continue;
    }
    have.insert(name);
    targets.emplace_back(name, bs);
  }
  for (const auto &[name, bs] : targets)
  {
    const type2tc &bt = bs->type;
    if (!is_unsignedbv_type(bt) && !is_signedbv_type(bt))
      continue;
    const auto xit = goto_coveraget::path_cov_certify_ce.find(name);
    if (xit == goto_coveraget::path_cov_certify_ce.end())
    {
      ++n_unnamed;
      continue;
    }
    BigInt c;
    try
    {
      c = parse_big(xit->second);
    }
    catch (...)
    {
      continue;
    }
    if (!last_sat)
    {
      if (solver.dec_solve() != smt_convt::P_SATISFIABLE)
        break;
      last_sat = true;
    }
    expr2tc wv = solver.get(bs);
    if (is_nil_expr(wv) || !is_constant_int2t(wv))
    {
      ++n_noval;
      continue;
    }
    const BigInt w = to_constant_int2t(wv).value;
    // A coordinate ALREADY at x_pi is pinned there all the same (one cheap
    // re-solve): a later move of another coordinate may change what this one
    // denotes -- MEASURED: `discountBps[msg.sender]` read 1 at sender 0, was
    // skipped as equal, then `msg.sender` moved to x_pi's own value and the
    // payload reported the new sender's slot, unconstrained, as 127.
    auto sat_with = [&](const expr2tc &cons) -> bool {
      if (solves >= solve_cap)
        return false;
      ++solves;
      solver.push_ctx();
      solver.assert_ast(solver.convert_ast(cons));
      const smt_convt::resultt r = solver.dec_solve();
      last_sat = (r == smt_convt::P_SATISFIABLE);
      if (last_sat)
        return true;
      solver.pop_ctx();
      return false;
    };
    auto between = [&](const BigInt &a, const BigInt &b) -> expr2tc {
      const BigInt lo = a < b ? a : b, hi = a < b ? b : a;
      return and2tc(
        greaterthanequal2tc(bs, constant_int2tc(bt, lo)),
        lessthanequal2tc(bs, constant_int2tc(bt, hi)));
    };
    // (1) the violation does not need this coordinate to move at all
    if (sat_with(equality2tc(bs, constant_int2tc(bt, c))))
    {
      changed = true;
      if (w == c)
        ++n_equal;
      else
        log_status(
          "--path-cov-certify: witness for '{}' on coordinate '{}' moved from "
          "{} to x_pi's own {} -- the violation does not depend on it",
          claim_txt,
          name,
          integer2string(w),
          integer2string(c));
      continue;
    }
    if (w == c)
    {
      // equal in the model yet not pinnable together with the constraints
      // already pushed: leave it, the search below would find nothing closer
      ++n_equal;
      continue;
    }
    // (2) least distance from x_pi at which the violation exists
    const bool up = w > c;
    const BigInt dist = up ? w - c : c - w;
    BigInt lo_d = 0, hi_d = dist, k = 1;
    bool bracketed = false;
    const size_t coord_cap = std::min(
      solve_cap, solves + 2 * (size_t)bt->get_width() + 4);
    while (k < dist && solves < coord_cap)
    {
      const BigInt probe = up ? c + k : c - k;
      if (sat_with(between(c, probe)))
      {
        hi_d = k;
        bracketed = true;
        break;
      }
      lo_d = k;
      k = k * 2;
    }
    if (!bracketed)
    {
      // the known witness itself is the bracket's far end
      if (!sat_with(between(c, w)))
        continue;
      hi_d = dist;
    }
    while (hi_d - lo_d > 1 && solves < coord_cap)
    {
      const BigInt mid = (lo_d + hi_d) / 2;
      const BigInt probe = up ? c + mid : c - mid;
      if (sat_with(between(c, probe)))
        hi_d = mid;
      else
        lo_d = mid;
    }
    changed = true;
    log_status(
      "--path-cov-certify: witness for '{}' on coordinate '{}' minimised from "
      "{} toward x_pi={} to {} ({} re-solve(s) so far{})",
      claim_txt,
      name,
      integer2string(w),
      integer2string(c),
      integer2string(up ? c + hi_d : c - hi_d),
      solves,
      solves >= solve_cap ? ", budget exhausted" : "");
  }
  log_status(
    "--path-cov-certify: witness minimisation for '{}': {} assume step(s), {} "
    "box-bound step(s), {} sliced, {} of unexpected shape, {} not in x_pi, {} "
    "without a model value, {} already at x_pi, {} re-solve(s)",
    claim_txt,
    n_assume,
    n_tagged,
    n_ignored,
    n_shape,
    n_unnamed,
    n_noval,
    n_equal,
    solves);
  if (changed || !last_sat)
  {
    // Refresh the model under the constraints left pushed: the last re-solve
    // may have been a popped UNSAT probe, and the harvest below reads `get`.
    if (solver.dec_solve() != smt_convt::P_SATISFIABLE)
      log_warning(
        "--path-cov-certify: witness minimisation for '{}' ended in a "
        "non-SAT state; the harvested witness is whatever the solver holds",
        claim_txt);
  }
}


void bmct::successful_trace(const symex_target_equationt &eq [[maybe_unused]])
{
  if (options.get_bool_option("result-only"))
    return;

  std::string witness_graphml_output =
    options.get_option("witness-output-graphml");
  std::string witness_yaml_output = options.get_option("witness-output-yaml");

  goto_tracet goto_trace;
  // correctness witness, why did goto trace ignore it in the past?
  // build_successful_goto_trace(eq, ns, goto_trace);
  if (witness_graphml_output != "")
    correctness_graphml_goto_trace(options, ns, goto_trace);

  if (witness_yaml_output != "")
    correctness_yaml_goto_trace(options, ns, goto_trace);
}

void bmct::error_trace(smt_convt &smt_conv, const symex_target_equationt &eq)
{
  if (options.get_bool_option("result-only"))
    return;

  log_progress("Building error trace");

  bool is_compact_trace = true;
  if (
    options.get_bool_option("no-slice") &&
    !options.get_bool_option("compact-trace"))
    is_compact_trace = false;

  goto_tracet goto_trace;
  build_goto_trace(eq, smt_conv, goto_trace, is_compact_trace);

  std::string output_file = options.get_option("cex-output");
  if (output_file != "")
  {
    std::ofstream out(output_file);
    show_goto_trace(out, ns, goto_trace);
  }

  std::string witness_graphml_output =
    options.get_option("witness-output-graphml");
  std::string witness_yaml_output = options.get_option("witness-output-yaml");
  if (witness_graphml_output != "")
    violation_graphml_goto_trace(options, ns, goto_trace);

  if (witness_yaml_output != "")
    violation_yaml_goto_trace(options, ns, goto_trace);

  if (options.get_bool_option("generate-testcase"))
  {
    generate_testcase_metadata();
    generate_testcase("testcase.xml", eq, smt_conv);
  }

  if (options.get_bool_option("generate-pytest-testcase"))
  {
    // Generate pytest filename based on source file: test_<module>.py
    std::string input_file = options.get_option("input-file");
    std::string module_name = pytest_generator::extract_module_name(input_file);
    std::string pytest_filename =
      pytest_generator::generate_pytest_filename(module_name);
    pytest_gen.generate_single(pytest_filename, eq, smt_conv, ns);
  }

  if (options.get_bool_option("generate-ctest-testcase"))
  {
    ctest_gen.generate_single(".", eq, smt_conv, ns);
  }

  if (options.get_bool_option("generate-foundry-testcase"))
  {
    foundry_gen.generate_single(eq, smt_conv, ns);
  }

  if (options.get_bool_option("generate-html-report"))
    generate_html_report("1", ns, goto_trace, options);

  if (options.get_bool_option("generate-json-report"))
    generate_json_report("1", ns, goto_trace);

  // esbmc-minimise consumes this structured violation summary to seed
  // its mandatory set and oracle tuple.
  const std::string violation_info_path =
    options.get_option("dump-violation-info");
  if (!violation_info_path.empty())
  {
    if (!dump_violation_info_json(violation_info_path, ns, goto_trace))
      log_warning("Failed to write violation info to {}", violation_info_path);
  }

  std::ostringstream oss;
  log_fail("\n[Counterexample]\n");
  show_goto_trace(oss, ns, goto_trace);
  log_result("{}", oss.str());
}

void bmct::generate_smt_from_equation(
  smt_convt &smt_conv,
  symex_target_equationt &eq) const
{
  std::string logic;

  if (!options.get_bool_option("int-encoding"))
  {
    logic = "bit-vector";
    logic += (!config.ansi_c.use_fixed_for_float) ? "/floating-point " : " ";
    logic += "arithmetic";
  }
  else
    logic = "integer/real arithmetic";

  log_status("Encoding remaining VCC(s) using {}", logic);

  fine_timet encode_start = current_time();
  eq.convert(smt_conv);
  fine_timet encode_stop = current_time();
  log_status(
    "Encoding to solver time: {}s", time2string(encode_stop - encode_start));
}

void bmct::keep_alive_function() const
{
  fine_timet start_time = current_time();
  while (keep_alive_running)
  {
    std::this_thread::sleep_for(std::chrono::seconds(keep_alive_interval));
    if (!keep_alive_running)
      break;

    fine_timet alive_current = current_time();
    // output runtime
    log_status(
      "Solver is still solving... Total Time: {}s",
      time2string(alive_current - start_time));
  }
}

smt_convt::resultt bmct::run_decision_procedure(
  smt_convt &smt_conv,
  symex_target_equationt &eq) const
{
  if (options.get_bool_option("enable-keep-alive"))
  {
    keep_alive_running = true;
    keep_alive_interval =
      atoi(options.get_option("keep-alive-interval").c_str());

    if (keep_alive_interval <= 0)
      keep_alive_interval = 60; // Default interval to 60 seconds

    std::thread([this]() { keep_alive_function(); }).detach();
  }

  generate_smt_from_equation(smt_conv, eq);

  if (
    options.get_bool_option("smt-formula-too") ||
    options.get_bool_option("smt-formula-only"))
  {
    std::string smt_formula = smt_conv.dump_smt();

    // Print the SMT formula to stdout or file
    if (!smt_formula.empty())
    {
      const std::string &output_path = options.get_option("output");

      if (output_path.empty() || output_path == "-")
      {
        // Print to stdout
        fprintf(stdout, "%s", smt_formula.c_str());
      }
      else
      {
        // Print to file
        FILE *file = fopen(output_path.c_str(), "w");
        if (!file)
          log_error("Could not open output file '{}'", output_path);
        else
        {
          fprintf(file, "%s", smt_formula.c_str());
          fclose(file);
          log_status("SMT formula dumped to file: {}", output_path);
        }
      }
    }

    if (options.get_bool_option("smt-formula-only"))
      return smt_convt::P_SMTLIB;
  }

  log_progress("Solving with solver {}", smt_conv.solver_text());

  fine_timet sat_start = current_time();
  smt_convt::resultt dec_result = smt_conv.dec_solve();
  fine_timet sat_stop = current_time();
  keep_alive_running = false;

  // output runtime
  log_status(
    "Runtime decision procedure: {}s", time2string(sat_stop - sat_start));

  return dec_result;
}

void bmct::report_success()
{
  log_success("\nVERIFICATION SUCCESSFUL");
}

void bmct::report_failure()
{
  log_fail("\nVERIFICATION FAILED");
}

void bmct::show_program(const symex_target_equationt &eq)
{
  unsigned int count = 1;
  std::ostringstream oss;
  if (config.options.get_bool_option("ssa-symbol-table"))
    ::show_symbol_table_plain(ns, oss);

  languagest languages(ns, language_idt::C);

  oss << "\nProgram constraints: \n";

  bool sliced = config.options.get_bool_option("ssa-sliced");

  for (auto const &it : eq.SSA_steps)
  {
    if (!(it.is_assert() || it.is_assignment() || it.is_assume()))
      continue;

    if (it.ignore && !sliced)
      continue;

    oss << "// " << it.source.pc->location_number << " ";
    oss << it.source.pc->location.as_string();
    if (!it.comment.empty())
      oss << " (" << it.comment << ")";
    oss << "\n/* " << count << " */ ";

    std::string string_value;
    languages.from_expr(migrate_expr_back(it.cond), string_value);

    if (it.is_assignment())
    {
      oss << string_value << "\n";
    }
    else if (it.is_assert())
    {
      oss << "(assert)" << string_value << "\n";
    }
    else if (it.is_assume())
    {
      oss << "(assume)" << string_value << "\n";
    }
    else if (it.is_renumber())
    {
      oss << "renumber: " << from_expr(ns, "", it.lhs) << "\n";
    }

    if (!migrate_expr_back(it.guard).is_true())
    {
      languages.from_expr(migrate_expr_back(it.guard), string_value);
      oss << std::string(i2string(count).size() + 3, ' ');
      oss << "guard: " << string_value << "\n";
    }

    oss << '\n';
    count++;
  }
  log_status("{}", oss.str());
}

void bmct::report_trace(
  smt_convt::resultt &res,
  const symex_target_equationt &eq)
{
  bool bs = options.get_bool_option("base-case");
  bool fc = options.get_bool_option("forward-condition");
  bool is = options.get_bool_option("inductive-step");
  bool term = options.get_bool_option("termination");
  bool show_cex = options.get_bool_option("show-cex");

  switch (res)
  {
  case smt_convt::P_UNSATISFIABLE:
    if (is && term)
    {
    }
    else if (!bs)
    {
      successful_trace(eq);
    }
    break;

  case smt_convt::P_SATISFIABLE:
    if (!bs && show_cex)
    {
      error_trace(*runtime_solver, eq);
    }
    else if (!is && !fc)
    {
      error_trace(*runtime_solver, eq);
    }
    break;

  default:
    break;
  }
}

/*
  For incremental-bmc and k-induction
  Whenever an error_trace or successful_trace is reported
  we finish reasoning this claims, thereby converting it to SKIP
*/
void bmct::clear_verified_claims_in_ssa(
  symex_target_equationt &local_eq,
  const claim_slicer &claim,
  const bool &is_goto_cov)
{
  for (auto &step : local_eq.SSA_steps)
  {
    if (!step.is_assert())
      continue;

    if (!step.source.is_set)
      continue;

    bool loc_match = (step.source.pc->location.as_string() == claim.claim_loc);
    bool expr_match = false;

    if (is_goto_cov)
      expr_match =
        (step.source.pc->location.comment().as_string() == claim.claim_msg);
    else
      expr_match = (from_expr(ns, "", step.guard) == claim.claim_msg);

    if (loc_match && expr_match)
    {
      step.cond = step.cond = gen_true_expr();
    }
  }
}

void bmct::clear_verified_claims_in_goto(
  const claim_slicer &claim,
  const bool &is_goto_cov)
{
  for (auto &func : symex->goto_functions.function_map)
  {
    for (auto &instr : func.second.body.instructions)
    {
      std::lock_guard lock(instr.clear_claims_mutex);
      if (!instr.is_assert())
        continue;

      bool loc_match = (instr.location.as_string() == claim.claim_loc);
      bool expr_match = false;

      std::string guard_str = from_expr(ns, "", instr.guard);

      if (is_goto_cov)
        expr_match = (instr.location.comment().as_string() == claim.claim_msg);
      else
        expr_match = (guard_str == claim.claim_msg);

      if (loc_match && expr_match)
      {
        instr.make_skip();
      }
    }
  }
}

void bmct::report_multi_property_trace(
  const smt_convt::resultt &res,
  const std::vector<witness_recordt> &witnesses,
  enumeration_stop_reasont stop_reason,
  const std::string &msg)
{
  if (options.get_bool_option("result-only"))
    return;

  switch (res)
  {
  case smt_convt::P_UNSATISFIABLE:
    log_success("Claim '{}' holds up to the current K", msg);
    return;

  case smt_convt::P_SATISFIABLE:
    break;

  default:
    log_fail("Claim '{}' could not be solved", msg);
    return;
  }

  // Single-witness textual output: keep the existing "[Counterexample]" form.
  // This preserves the look of every existing failing test in regression/.
  // Skip this path when --all-witnesses was requested (stop_reason != Disabled)
  // so the structured footer is still emitted at N=1 — otherwise a CapHit
  // with cap=1 would silently look identical to a real "only-one-witness"
  // result.
  if (
    witnesses.size() <= 1 && stop_reason == enumeration_stop_reasont::Disabled)
  {
    std::ostringstream oss;
    log_fail("\n[Counterexample]\n");
    if (!witnesses.empty())
      show_goto_trace(oss, ns, witnesses.front().trace);
    log_result("{}", oss.str());
    return;
  }

  // Multi-witness rendering: structured per-witness blocks, then a footer.
  // Goal: highlight the *inputs* (the part that varies across witnesses) and
  // avoid dumping N copies of nearly-identical traces. The full trace for
  // each witness is still emitted unless --compact-trace is on, but they
  // are clearly separated and labelled.
  std::ostringstream oss;
  // ASCII-only header: en-dash and similar non-ASCII glyphs get
  // mojibake'd on Windows' default cp1252 console, breaking regression
  // matching and reader output. The box-drawing glyphs further down
  // are cosmetic and only appear at N>1; ASCII-fallback there is
  // tracked separately (#4311).
  oss << "\n[Counterexamples - " << witnesses.size() << " witnesses]\n\n";
  for (size_t i = 0; i < witnesses.size(); ++i)
  {
    const witness_recordt &w = witnesses[i];
    oss << "  ┌─ Witness " << (i + 1) << " of " << witnesses.size()
        << " ─────────────────────────────\n";
    oss << "  │  Inputs : ";
    if (w.nondet_inputs.empty())
    {
      oss << "(none)\n";
    }
    else
    {
      for (size_t k = 0; k < w.nondet_inputs.size(); ++k)
      {
        if (k)
          oss << ", ";
        // Use WITNESS presentation to render bit-distinct floats with
        // round-trippable precision; otherwise the default HUMAN flags
        // collapse e.g. several near-MAX floats to the same string.
        oss << "[" << k << "] = "
            << from_expr(
                 ns, "", w.nondet_inputs[k].value_expr, presentationt::WITNESS);
      }
      oss << "\n";
    }
    oss << "  │  Trace  :\n";
    {
      std::ostringstream tr;
      show_goto_trace(tr, ns, w.trace);
      // Indent the trace under the box.
      std::string s = tr.str();
      std::string indented;
      indented.reserve(s.size() + 8);
      indented += "  │    ";
      for (char c : s)
      {
        indented += c;
        if (c == '\n')
          indented += "  │    ";
      }
      oss << indented << "\n";
    }
    oss << "  └──────────────────────────────────────────────\n\n";
  }

  oss << "Summary: " << witnesses.size()
      << " distinct input tuples violate this property (enumeration stopped: ";
  switch (stop_reason)
  {
  case enumeration_stop_reasont::Unsat:
    oss << "UNSAT after " << witnesses.size() << " witnesses";
    break;
  case enumeration_stop_reasont::CapHit:
    oss << "--max-witnesses cap reached";
    break;
  case enumeration_stop_reasont::NoInputs:
    oss << "no enumerable nondet inputs — more witnesses may exist";
    break;
  case enumeration_stop_reasont::Error:
    oss << "solver returned error/unknown — more witnesses may exist";
    break;
  case enumeration_stop_reasont::Disabled:
    oss << "single-witness mode";
    break;
  }
  oss << ")\n";

  log_fail("\n[Counterexample]\n");
  log_result("{}", oss.str());
}

// Prettify C-level expression strings for Solidity coverage reports.
// Strips C casts, maps internal names to Solidity names, etc.
static std::string prettify_solidity_expr(const std::string &expr)
{
  if (config.language.lid != language_idt::SOLIDITY)
    return expr;

  std::string s = expr;

  // Remove C-style casts: (signed int), (unsigned int), (signed long int), etc.
  // Also handles _ExtInt(N) casts like (unsigned _ExtInt(256))
  static const std::regex cast_re(
    R"(\((?:signed|unsigned)\s+(?:_ExtInt\(\d+\)|(?:long\s+)?(?:long\s+)?int)\))");
  s = std::regex_replace(s, cast_re, "");

  // Remove this-> prefix (Solidity state variables)
  static const std::regex this_re(R"(this->)");
  s = std::regex_replace(s, this_re, "");

  // Map internal Solidity global variable names to their Solidity equivalents
  static const std::vector<std::pair<std::regex, std::string>> name_map = {
    {std::regex(R"(\bmsg_sender\b)"), "msg.sender"},
    {std::regex(R"(\bmsg_value\b)"), "msg.value"},
    {std::regex(R"(\bmsg_sig\b)"), "msg.sig"},
    {std::regex(R"(\bmsg_data\b)"), "msg.data"},
    {std::regex(R"(\btx_origin\b)"), "tx.origin"},
    {std::regex(R"(\btx_gasprice\b)"), "tx.gasprice"},
    {std::regex(R"(\bblock_number\b)"), "block.number"},
    {std::regex(R"(\bblock_timestamp\b)"), "block.timestamp"},
    {std::regex(R"(\bblock_coinbase\b)"), "block.coinbase"},
    {std::regex(R"(\bblock_difficulty\b)"), "block.difficulty"},
    {std::regex(R"(\bblock_gaslimit\b)"), "block.gaslimit"},
    {std::regex(R"(\bblock_chainid\b)"), "block.chainid"},
    {std::regex(R"(\bblock_basefee\b)"), "block.basefee"},
    {std::regex(R"(\bblock_blobbasefee\b)"), "block.blobbasefee"},
    {std::regex(R"(\bblock_prevrandao\b)"), "block.prevrandao"},
  };
  for (const auto &[re, repl] : name_map)
    s = std::regex_replace(s, re, repl);

  // Remove redundant parentheses left by cast removal, e.g. ((x)) -> (x)
  // Iteratively reduce until stable (handles nested cases)
  static const std::regex double_paren(R"(\((\([^()]*\))\))");
  std::string prev;
  do
  {
    prev = s;
    s = std::regex_replace(s, double_paren, "$1");
  } while (s != prev);

  // Remove parens in array index: [(...)] -> [...]
  static const std::regex bracket_paren(R"(\[\(([^()]*)\)\])");
  s = std::regex_replace(s, bracket_paren, "[$1]");

  // Prettify Solidity internal symbol IDs: sol:@C@Contract@F@func#N -> func
  // Appears in "function entry: sol:@C@..." messages
  static const std::regex sol_id_re(R"(sol:@C@\w+@F@(\w+)#\d*)");
  s = std::regex_replace(s, sol_id_re, "$1");

  // Clean up extra spaces from removed casts
  static const std::regex multi_space(R"(  +)");
  s = std::regex_replace(s, multi_space, " ");

  // Remove leading/trailing whitespace
  auto start = s.find_first_not_of(' ');
  auto end = s.find_last_not_of(' ');
  if (start != std::string::npos)
    s = s.substr(start, end - start + 1);

  return s;
}

// Solidity complete-path coverage: can THIS run establish that a path is
// UNREACHABLE (status I), as opposed to merely "no witness found here" (U)?
//
// It could only do so if the exploration over-approximated every reachable
// state. Nothing in Solidity coverage mode does:
//
//  * `--solidity-max-tx N` with N > 0 emits N straight-line transactions —
//    bounded by construction.
//  * `--solidity-max-tx 0` asks the frontend for the `while (nondet_bool())
//    dispatch()` driver, which READS as unbounded — but that loop is then
//    destroyed. esbmc_parseoptions.cpp::process_goto_program walks every
//    function whose name contains `_ESBMC_Main` and calls make_skip() on each
//    `is_backwards_goto()`, under a condition (`is_coverage &&
//    !--coverage-multi-tx`) whose `is_coverage` disjunction includes
//    `--solidity-path-coverage`. One guarded transaction is left, so max_tx 0
//    is the SHALLOWEST setting, not an unbounded one.
//
//    Verified three independent ways, not taken from documentation:
//      - the code above;
//      - `--show-loops` on the same contract lists `_ESBMC_Main_S` as a loop
//        WITHOUT the coverage flag and does not list it WITH it (every other
//        loop identical);
//      - behaviour: on `arm(); fire();`, where fire()'s guarded path needs a
//        prior transaction, that path is REACHED at --solidity-max-tx 2 and
//        NOT reached at --solidity-max-tx 0.
//  * the entry state is whatever the constructor left; state variables are
//    never havoc'd, so an UNSAT only says "not reachable from THIS entry
//    state", not "not reachable".
//
// Treating max_tx == 0 as unbounded — which this code used to do — would dress
// a one-transaction budget up as a proof of unreachability, the exact failure
// the tri-state exists to prevent. So every non-refuted path is U, flagged
// `bounded_holds` when it held at this exploration.
//
// Kept as a function rather than deleted so that the day a havoc'd-entry or
// loop-live exploration mode exists, this is the single place to change.
static bool path_cov_can_prove_unreachable()
{
  return false;
}

// Parse location string "file X line Y column Z function F" into components
static nlohmann::json parse_claim_location(const std::string &loc)
{
  nlohmann::json j;
  j["file"] = "";
  j["line"] = 0;
  j["column"] = 0;
  j["function"] = "";

  std::istringstream iss(loc);
  std::string token;
  while (iss >> token)
  {
    if (token == "file")
    {
      std::string val;
      iss >> val;
      j["file"] = val;
    }
    else if (token == "line")
    {
      int val = 0;
      iss >> val;
      j["line"] = val;
    }
    else if (token == "column")
    {
      int val = 0;
      iss >> val;
      j["column"] = val;
    }
    else if (token == "function")
    {
      std::string val;
      iss >> val;
      j["function"] = val;
    }
  }
  return j;
}

void report_coverage(
  const optionst &options,
  std::unordered_set<std::string> &reached_claims,
  const std::unordered_multiset<std::string> &reached_mul_claims,
  pytest_generator &pytest_gen,
  ctest_generator &ctest_gen,
  foundry_generator &foundry_gen,
  const std::string &partial_reason)
{
  // Published to the U-reason classifier before anything reads it, so a claim
  // that never got a verdict is filed `run-died-before-solving` rather than
  // `not-solved-this-run` (which means "the simplifier folded it away", a fact
  // about the claim and not about the run), and so audit_entry_liveness stops
  // treating an un-entered unit as a defect. Both consumers run inside this
  // function, below.
  const bool is_partial = !partial_reason.empty();
  if (is_partial)
    goto_coveraget::path_cov_partial_reason = partial_reason;

  bool is_assert_cov = options.get_bool_option("assertion-coverage") ||
                       options.get_bool_option("assertion-coverage-claims");
  bool is_cond_cov = options.get_bool_option("condition-coverage") ||
                     options.get_bool_option("condition-coverage-claims") ||
                     options.get_bool_option("condition-coverage-rm") ||
                     options.get_bool_option("condition-coverage-claims-rm");
  bool is_branch_cov = options.get_bool_option("branch-coverage") ||
                       options.get_bool_option("branch-coverage-claims");
  bool is_branch_func_cov =
    options.get_bool_option("branch-function-coverage") ||
    options.get_bool_option("branch-function-coverage-claims");
  const bool is_path_probe =
    options.get_bool_option("solidity-path-probe-enabled");
  if (is_path_probe)
    is_branch_func_cov = false;
  // `k-path-coverage` itself stores the CLI integer N; the dedicated
  // boolean enable flag is set by parseoptions when either CLI flag is
  // present. This avoids `get_bool_option("k-path-coverage")` returning 0
  // (false) for valid invocations like `--k-path-coverage` (no value) or
  // `--k-path-coverage=0` (rejected at parse time, but defensive here).
  bool is_k_path_cov = options.get_bool_option("k-path-coverage-enabled");
  // Solidity complete-path coverage: one goal per enumerated entry->exit
  // path, keyed by the "fn:path:enc" comment (see solidity_path_coverage()).
  bool is_path_cov = options.get_bool_option("solidity-path-coverage-enabled");

  // A base-case UNSAT is only bounded evidence.  The per-claim ledger is
  // shared across k-induction phases and deliberately preserves that `P`
  // against a later SAT/UNKNOWN inductive step.  On strategy exhaustion this
  // function is still called, so fail closed here unless FC/IS actually
  // closed: retain concrete `F` witnesses, but turn base-only `P` rows back
  // into undecided before certification or ladder reporting consumes them.
  if (
    is_path_cov && options.get_bool_option("k-induction") &&
    !goto_coveraget::path_cov_k_induction_proved)
  {
    size_t downgraded = 0;
    std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
    for (auto &[claim, outcome] : goto_coveraget::claim_outcome)
    {
      (void)claim;
      if (outcome == 'P')
      {
        outcome = 'U';
        ++downgraded;
      }
    }
    if (downgraded > 0)
      log_warning(
        "k-induction did not close: downgrading {} base-case-only path "
        "verdict(s) to UNDECIDED; none may be reported HOLDS or CERTIFIED",
        downgraded);
  }

  // Truncation disclosure. With --no-unwinding-assertions (which coverage
  // mode turns on automatically whenever --unwind is given, and which the
  // k-induction base/inductive phases set too) a loop that reaches its
  // bound is cut with an ASSUME rather than flagged with an assertion.
  // Every path that needed one more iteration disappears, so the code
  // after the loop can become unreachable and its goals are reported as
  // uncovered -- with no diagnostic at all, next to "VERIFICATION
  // SUCCESSFUL". Say so, and name the loops, so a 0% report is not read as
  // "the harness explored everything and covered nothing".
  {
    std::lock_guard lk(goto_functionst::truncated_loops_mutex);
    if (
      !goto_functionst::truncated_loops.empty() &&
      options.get_bool_option("no-unwinding-assertions"))
    {
      log_warning(
        "Coverage may be UNDER-REPORTED: {} loop(s) hit the unwind bound "
        "while --no-unwinding-assertions was active, so the paths that "
        "needed more iterations were silently assumed away. Goals reachable "
        "only through those paths are counted as uncovered. Raise --unwind, "
        "use --unwindset/--unwindsetname for the specific loop, or switch to "
        "--k-induction / --incremental-bmc. Loops truncated:",
        goto_functionst::truncated_loops.size());
      for (const auto &l : goto_functionst::truncated_loops)
        log_warning("  {}", l);
    }
  }

  if (is_assert_cov)
  {
    const int total = goto_coveraget::total_assert;
    const int tracked_instance = reached_mul_claims.size();
    const int total_instance = goto_coveraget::total_assert_ins;

    log_success("\n[Coverage]\n");
    // The total assertion instances include the assert inside the source file, the unwinding asserts, the claims inserted during the goto-check and so on.
    log_result("Total Asserts: {}", total);
    if (total_instance >= tracked_instance)
      log_result("Total Assertion Instances: {}", total_instance);
    else
      // this could be
      // 1. the loop is too large that we cannot goto-unwind it
      // 2. the loop is somewhat non-deterministic that we cannot run goto-unwind
      log_result("Total Assertion Instances: unknown / non-deterministic");
    log_result("Reached Assertion Instances: {}", tracked_instance);

    // show claims
    if (options.get_bool_option("assertion-coverage-claims"))
    {
      // reached claims:
      for (const auto &claim : reached_mul_claims)
      {
        log_status("  {}", prettify_solidity_expr(claim));
      }
    }

    if (total_instance != 0)
    {
      if (total_instance >= tracked_instance)
        log_result(
          "Assertion Instances Coverage: {}%",
          tracked_instance * 100.0 / total_instance);
      else
        log_result("Assertion Instances Coverage Unknown");
    }
    else
      log_result("Assertion Instances Coverage: 0%");
  }

  else if (is_cond_cov)
  {
    log_success("\n[Coverage]\n");

    // not all the claims are cond-cov instrumentations
    // thus we need to skip the irrelevant claims like unwinding assertions
    // when comparing 'total_cond_assert' and 'reached_claims'
    const std::set<std::pair<std::string, std::string>> &total_cond_assert =
      goto_coveraget::total_cond;
    const size_t total_instance = total_cond_assert.size();
    size_t reached_instance = 0;
    size_t short_circuit_instance = 0;
    size_t sat_instance = 0;
    size_t unsat_instance = 0;

    // show claims
    bool cond_show_claims =
      options.get_bool_option("condition-coverage-claims") ||
      options.get_bool_option("condition-coverage-claims-rm");

    // reached claims:
    auto total_cond_assert_cpy = total_cond_assert;
    for (const auto &claim_pair : total_cond_assert)
    {
      std::string claim_msg = claim_pair.first;
      std::string claim_loc = claim_pair.second;
      std::string claim_sig = claim_msg + "\t" + claim_loc;
      if (reached_claims.count(claim_sig))
      {
        // show sat claims
        if (cond_show_claims)
          log_status("  {} : SATISFIED", prettify_solidity_expr(claim_sig));

        // update counter +=2
        // as we handle ass and !ass at the same time
        reached_instance += 2;

        // update sat counter
        ++sat_instance;

        // prevent double count
        reached_claims.erase(claim_sig);
        total_cond_assert_cpy.erase(claim_pair);

        // reversal: obtain !ass
        if (
          claim_msg[0] == '!' && claim_msg[1] == '(' && claim_msg.back() == ')')
          // e.g. !(a==1)
          claim_msg = claim_msg.substr(2, claim_msg.length() - 3);
        else
          claim_msg = "!(" + claim_msg + ")";
        std::string r_claim_sig = claim_msg + "\t" + claim_loc;

        if (reached_claims.count(r_claim_sig))
        {
          ++sat_instance;
          if (cond_show_claims)
            log_result("  {} : SATISFIED", prettify_solidity_expr(r_claim_sig));
        }
        else
        {
          ++unsat_instance;
          if (cond_show_claims)
            log_result(
              "  {} : UNSATISFIED", prettify_solidity_expr(r_claim_sig));
        }

        // prevent double count
        // e.g if( a ==0 && a == 0)
        // we only count a==0 and !(a==0) once
        reached_claims.erase(r_claim_sig);
        std::pair<std::string, std::string> _pair =
          std::make_pair(claim_msg, claim_loc);
        total_cond_assert_cpy.erase(_pair);
      }
    }

    // the remain unreached instrumentations are regarded as short-circuited
    //! the reached_claims might not be empty (due to unwinding assertions)
    short_circuit_instance = total_cond_assert_cpy.size();

    // show short-circuited:
    if (cond_show_claims && short_circuit_instance > 0)
    {
      log_success("[Short Circuited Conditions]\n");
      for (const auto &claim_pair : total_cond_assert_cpy)
      {
        std::string claim_msg = claim_pair.first;
        std::string claim_loc = claim_pair.second;
        std::string claim_sig = claim_msg + "\t" + claim_loc;
        log_result("  {}", prettify_solidity_expr(claim_sig));
      }
    }

    // show the number
    log_result("Reached Conditions:  {}", reached_instance);
    log_result("Short Circuited Conditions:  {}", short_circuit_instance);
    log_result(
      "Total Conditions:  {}\n", reached_instance + short_circuit_instance);

    log_result("Condition Properties - SATISFIED:  {}", sat_instance);
    log_result("Condition Properties - UNSATISFIED:  {}\n", unsat_instance);

    if (total_instance != 0)
      log_result(
        "Condition Coverage: {}%", sat_instance * 100.0 / total_instance);
    else
      log_result("Condition Coverage: 0%");
  }

  else if (is_branch_cov)
  {
    const size_t total = goto_coveraget::total_branch;
    const bool cov_set_active = !goto_coveraget::covered_set_outpath.empty();

    // Default (no --coverage-covered-set): unchanged — raw reached set
    // (this also included non-universe entries like unwinding
    // assertions, kept as-is to preserve existing pinned numbers).
    // With the cross-run covered-set, the numerator is the universe
    // edges EITHER witnessed this run OR already persisted — and is
    // intersected with the universe, which also drops that over-count.
    size_t tracked_instance = reached_claims.size();
    if (cov_set_active)
    {
      tracked_instance = 0;
      for (const auto &[cond, loc] : goto_coveraget::all_claims)
        if (
          goto_coveraget::covered_set.count({cond, loc}) ||
          reached_claims.count(cond + "\t" + loc))
          ++tracked_instance;
    }
    log_success("\n[Coverage]\n");
    if (total == 0)
    {
      log_result("No branch detected");
    }
    else
    {
      log_result("Branches : {}", total);
      log_result("Reached : {}", tracked_instance);

      // show claims
      if (options.get_bool_option("branch-coverage-claims"))
      {
        // reached claims:
        for (const auto &claim : reached_claims)
          log_status("  {}", prettify_solidity_expr(claim));
      }

      log_result("Branch Coverage: {}%", tracked_instance * 100.0 / total);
    }

    // Re-sync the signal-safe snapshot to the AUTHORITATIVE number:
    // this is the only place reached_claims is erased (short-circuit /
    // non-universe pruning above), and tracked_instance is exactly the
    // mode-correct numerator just printed. Storing it into the active
    // counter makes a subsequent kill (e.g. inductive step after the
    // base-case report) emit the exact post-report coverage, not a
    // pre-report lower bound.
    if (cov_set_active)
      goto_coveraget::covered_run.store(
        tracked_instance, std::memory_order_relaxed);
    else
      goto_coveraget::live_reached.store(
        tracked_instance, std::memory_order_relaxed);

    // Item 2 / 2e final write-back: covered_set is the single live
    // accumulator (loaded input ∪ every edge the Item-2e hook persisted
    // as it was witnessed this run). Fold in any reached universe edge
    // defensively, then one final atomic rewrite. Monotone union —
    // never truncated; only true-P_SATISFIABLE edges are ever added.
    if (cov_set_active)
    {
      for (const auto &[cond, loc] : goto_coveraget::all_claims)
        if (reached_claims.count(cond + "\t" + loc))
          goto_coveraget::covered_set.emplace(cond, loc);
      goto_coveraget::write_covered_set_atomic();
      log_success(
        "coverage covered-set written to {}",
        goto_coveraget::covered_set_outpath);
    }
  }

  else if (is_branch_func_cov)
  {
    //! Might got incorrect total number when using --k-induction
    //! due to that the symex->goto_functions has been simplified
    const size_t total = goto_coveraget::total_func_branch;
    // this also included the non-unwinding-assertions
    // which is not what we want
    const size_t tracked_instance = reached_claims.size();
    log_success("\n[Coverage]\n");
    if (total == 0)
    {
      log_result("No branch detected");
    }
    else
    {
      log_result("Function Entry Points & Branches : {}", total);
      log_result("Reached : {}", tracked_instance);

      // show claims
      if (options.get_bool_option("branch-function-coverage-claims"))
      {
        // reached claims:
        for (const auto &claim : reached_claims)
          log_status("  {}", prettify_solidity_expr(claim));
      }

      log_result("Branch Coverage: {}%", tracked_instance * 100.0 / total);
    }
  }

  else if (is_k_path_cov)
  {
    const size_t total = goto_coveraget::total_kpath;
    const size_t spanning = goto_coveraget::total_kpath_spanning;

    // Phase-2 (issue #4335): both numerator and denominator must restrict
    // to maximal goals under the atom-multiset subsumption order (Marré-
    // Bertolino, IEEE TSE 2003). Filter reached_claims against
    // k_path_spanning_redundant so a reached-but-subsumed goal does not
    // inflate the numerator against the maximal-only denominator.
    const auto &redundant = goto_coveraget::k_path_spanning_redundant;
    auto is_maximal = [&redundant](const std::string &claim_sig) {
      // claim_sig = "msg\tloc"; loc has no tabs, so rfind is robust if a
      // future emission path puts a tab in msg.
      const auto tab = claim_sig.rfind('\t');
      return redundant.count(
               {claim_sig.substr(0, tab), claim_sig.substr(tab + 1)}) == 0;
    };

    const size_t tracked_instance =
      std::count_if(reached_claims.begin(), reached_claims.end(), is_maximal);

    log_success("\n[Coverage]\n");
    log_result("k-Path Witnesses : {}", total);
    log_result("Spanning Set : {}", spanning);
    log_result("Reached : {}", tracked_instance);

    // Listing shows every reached claim regardless of maximality so the
    // user can see which subsumed goals were also reached — this is a
    // diagnostic flag, not a coverage-formula display.
    if (options.get_bool_option("k-path-coverage-claims"))
      for (const auto &claim : reached_claims)
        log_status("  {}", prettify_solidity_expr(claim));

    if (spanning != 0)
      log_result("k-Path Coverage: {}%", tracked_instance * 100.0 / spanning);
    else
      log_result("k-Path Coverage: N/A (no k-path goals)");
  }

  else if (is_path_cov)
  {
    // BEFORE any number is printed: does this run establish anything at all?
    //
    // Measured on a real benchmark, 120166 paths were instrumented and symex
    // produced ZERO verification conditions — the harness never entered a
    // single unit — and every path was then reported "U", which reads exactly
    // like an honest solver timeout. The audit aborts on that, and on the
    // per-unit version of it, so the coverage figures below are never printed
    // for a run that is vacuous rather than merely incomplete. It runs first
    // precisely so those figures do not appear and get quoted.
    //
    // This path is also reached with an empty reached-set when the equation
    // simplifies to zero remaining claims (see run_thread), which is exactly
    // the whole-run version of the same failure.
    goto_coveraget::audit_entry_liveness(options.get_option("focus-function"));

    // CERTIFICATION MODE PRINTS NO [Coverage] BLOCK AT ALL.
    //
    // The claims in this mode are `assume(box); assert(tr == pi)` — a certified
    // box makes them HOLD, so they would be counted as U/bounded-holds and the
    // run would read "Path Coverage: 0%" for a completely successful
    // certification. The verdict of a certification run is VERIFICATION
    // SUCCESSFUL versus FAILED, nothing else.
    //
    // Suppressed rather than annotated, on precedent: the path-distribution line
    // once mixed a per-round count into a structural measurement and printed
    // "1 path(s) total ... 0.33x" for a contract whose real figures are 8 and
    // 2.67x. It was documented in a comment and still got quoted, and a measured
    // result had to be retracted. A number that does not exist cannot be quoted;
    // a number with a disclaimer attached can.
    if (goto_coveraget::path_cov_outer_box_mode)
    {
      // Same reason as certification mode: these claims are ladder probes, and
      // a probe that HOLDS is a measurement, not an uncovered path. Reporting
      // them through the coverage counters would print a number that means
      // nothing and reads like one that does.
      goto_coveraget::report_outer_boxes();
    }
    else if (goto_coveraget::path_cov_certify_mode)
    {
      goto_coveraget::audit_certify_witness(
        options.get_bool_option("cov-report-json"));
      log_status(
        "--path-cov-certify: no [Coverage] block is printed in certification "
        "mode — the claims here are `assume(box); assert(tr == pi)`, so a "
        "CERTIFIED box makes them hold and would be counted as uncovered");
      // THE RESULT LINE, and it replaces the verdict line as this mode's
      // answer. The non-vacuity witness is REFUTED on every run that
      // certifies, so a certified box now prints VERIFICATION FAILED — which
      // is why the tool has to state its own result rather than let a caller
      // infer one. Printed last in this arm so it is the final word before the
      // verdict a reader must NOT use.
      goto_coveraget::report_path_cov_certify();
    }
    else if (goto_coveraget::path_cov_assert_mode)
    {
      // Same reason as the two arms above, and the sharpest case of it: in this
      // mode a claim that HOLDS is the WANTED outcome, so the coverage counters
      // would print "Path Coverage: 0%" for a completely successful ladder.
      // audit_entry_liveness above still runs and is the precondition that
      // stops a never-entered unit from making every candidate hold vacuously.
      goto_coveraget::report_path_cov_assertions();
    }
    else
    {
      // Denominator = the no-skip static universe built by
      // solidity_path_coverage() (one entry per enumerated complete path), so a
      // covered-set skip never shrinks it. Numerator = universe paths EITHER
      // witnessed this run OR already persisted by an earlier round.
      const size_t total = goto_coveraget::all_claims.size();
      // Complete-path coverage keys its cross-run cover on the CONTENT-ADDRESSED
      // stable id, not on `covered_set` (which solidity_path_coverage()
      // deliberately leaves empty — see path_witnessed_earlier). Using
      // covered_set here reported every path carried over from an earlier round
      // as U: a path with a counterexample in hand, filed under "undecided".
      const bool cov_set_active = !goto_coveraget::path_covered_outpath.empty();
      size_t tracked_instance = 0;
      for (const auto &k : goto_coveraget::all_claims)
      {
        // THE SAME DISJUNCTION `Path Status: F` USES, and it has to be, because
        // `Reached` and `F` are two renderings of one set. Adding `v == 'F'`
        // below without adding it here produced a report reading
        // `Path Coverage: 0%` and `Path Status: F 1` in the same block --
        // measured on the injected mid-harvest fault. Two numbers computed from
        // one fact must not be able to disagree; a reader would have to guess
        // which was the defect.
        char vv = 0;
        {
          std::lock_guard lk(goto_coveraget::claim_outcome_mutex);
          auto it_v =
            goto_coveraget::claim_outcome.find(k.first + "\t" + k.second);
          if (it_v != goto_coveraget::claim_outcome.end())
            vv = it_v->second;
        }
        if (
          goto_coveraget::path_witnessed_earlier(k) ||
          reached_claims.count(k.first + "\t" + k.second) || vv == 'F')
          ++tracked_instance;
      }

      log_success("\n[Coverage]\n");
      // ---- THE COMPLETENESS LINE, printed in BOTH directions ----
      //
      // A partial report lands under the same filename a complete one does, and
      // the [Coverage] block below is byte-compatible with a run that genuinely
      // measured these numbers. So the discriminator is stated as its own line,
      // unconditionally: a marker that is only present when something is wrong is
      // indistinguishable, to any consumer that has not been taught about it,
      // from a marker that was forgotten.
      if (is_partial)
        log_result(
          "Report Completeness: PARTIAL — this run did not conclude ({}). {} "
          "of "
          "{} claim(s) had been decided. Every count below is a LOWER BOUND: "
          "the "
          "paths never reached are reported U with reason "
          "'run-died-before-solving', which is NOT the same as "
          "'not-solved-this-run'",
          partial_reason,
          goto_coveraget::live_decided.load(std::memory_order_relaxed),
          goto_coveraget::claims_total_atomic.load(std::memory_order_relaxed));
      else
        log_result("Report Completeness: COMPLETE");
      // Printed on EVERY path-coverage run, including when the budget is off and
      // when it never fired. "The cap was on" and "the cap fired N times" are
      // separate statements, and a run whose numbers were shaped by an abandoned
      // query must not look like one whose solver answered everything.
      log_result(
        "Claim Budget: {} — {} claim(s) abandoned over budget ({})",
        goto_coveraget::claim_budget_seconds == 0
          ? std::string("unlimited")
          : std::to_string(goto_coveraget::claim_budget_seconds) +
              "s per claim",
        goto_coveraget::claim_budget_exceeded.load(std::memory_order_relaxed),
        goto_coveraget::claim_budget_mechanism.empty()
          ? std::string("no enforcement")
          : goto_coveraget::claim_budget_mechanism);
      // ---- WHAT THE ARITHMETIC RE-SOLVE COST, AND WHAT IT BOUGHT ----
      //
      // Printed on EVERY path-coverage run, including when the mechanism is off
      // and when it never fired. "The flag was on" and "it fired N times" are
      // separate statements, and a mechanism whose price is unmeasured is one
      // nobody can decide to keep -- nobody knew, when this was designed, whether
      // it would fire on three claims or three thousand.
      //
      // `conditions seen` is printed even at ZERO and especially then: zero means
      // no arithmetic check was enabled, or none reached these units, and without
      // it a run that re-solved nothing looks exactly like a run that had nothing
      // to re-solve.
      if (options.get_bool_option("path-cov-arith-resolve"))
        log_result(
          // `took the constrained witness` counts SAT re-solves, NOT wraps
          // fixed: a path whose original witness already satisfied every check
          // re-solves to an equally good one and is counted here too. Measured on
          // D10_WrapNotPanic, 3 of 3 -- and only ONE of those three was wrapping.
          // Naming it "replaced by a non-wrapping one" would have let a reader
          // infer three defects fixed from one.
          "Arithmetic Re-solve: {} condition(s) seen, {} claim(s) re-solved in "
          "{}s, {} took the constrained witness (this counts SAT re-solves, "
          "not "
          "wraps fixed -- a path whose witness was already fine is counted "
          "too), "
          "{} path(s) PROVEN reachable only through a checked-arithmetic "
          "revert, "
          "{} Foundry case(s) REFUSED for that reason",
          goto_coveraget::arith_conditions_seen.load(std::memory_order_relaxed),
          goto_coveraget::arith_resolve_queries.load(std::memory_order_relaxed),
          goto_coveraget::arith_resolve_ms.load(std::memory_order_relaxed) /
            1000.0,
          goto_coveraget::arith_resolve_replaced.load(
            std::memory_order_relaxed),
          goto_coveraget::arith_revert_only_paths.size(),
          goto_coveraget::arith_revert_only_suppressed.load(
            std::memory_order_relaxed));
      else
        log_result(
          "Arithmetic Re-solve: OFF (--path-cov-arith-resolve). A witnessed "
          "path whose counterexample wraps or divides by zero is emitted as a "
          "normal-exit test and is RED on the unmodified contract");

      // Printed unconditionally, zero included. A NON-ZERO value means the same
      // claim key reached the solve loop more than once and a decision it had
      // already made would have been thrown away -- so this line is both the
      // guard's effect and the duplicate-instrumentation defect's detector.
      log_result(
        "Verdicts Preserved: {} — a claim already DECIDED whose later solve "
        "returned no verdict kept its decision. Non-zero also means the same "
        "claim key was solved more than once, which is a separate defect",
        goto_coveraget::verdicts_preserved.load(std::memory_order_relaxed));

      // ---- THE OTHER DIRECTION, which the line above cannot see ----
      //
      // `Verdicts Preserved` fires only when the LATER solve returned no verdict.
      // A later solve that returns a DIFFERENT verdict passes it silently, and
      // that is the common case: measured on a 20-line contract at
      // --solidity-max-tx 2, one key was solved twice and got PASSED then FAILED
      // while `Verdicts Preserved` read 0.
      //
      // Printed unconditionally, zeros included, and as three separate numbers
      // because they answer three different questions: how much solving was
      // repeated, how often a decision was superseded, and what the worst key
      // was. A single "duplicates: N" would let "no duplication" and "the
      // counter was never wired" look the same.
      log_result(
        "Claim Multiplicity: {} extra solve(s) beyond the first across all "
        "keys; worst key '{}' decided {} time(s); ceiling {}; {} decided "
        "verdict(s) superseded by a different decided verdict. Repetition is "
        "EXPECTED and is not bounded by this run's tx/unwind: one "
        "instantiation "
        "per transaction, one per re-entry level, and one per caller for a "
        "public unit another unit calls internally. The path's answer is the "
        "disjunction over them. The number to read is SUPERSEDED: non-zero "
        "means "
        "one key's instantiations disagreed, so what this run reports for that "
        "path depended on which was solved first",
        path_cov_extra_solves.load(std::memory_order_relaxed),
        path_cov_max_solves_key.empty()
          ? std::string("(none)")
          : prettify_solidity_expr(path_cov_max_solves_key),
        path_cov_max_solves,
        path_cov_allowed_solves == 0
          ? std::string("not enforced (--path-cov-max-claim-solves unset)")
          : std::to_string(path_cov_allowed_solves),
        path_cov_verdict_upgrades.load(std::memory_order_relaxed));

      if (total == 0)
        log_result("No complete path enumerated");
      else
      {
        log_result("Complete Paths : {}", total);
        log_result("Reached : {}", tracked_instance);
        log_result("Path Coverage: {}%", tracked_instance * 100.0 / total);
        // Exit-shape breakdown on stdout as well as in the JSON. The JSON is a
        // file, and the regression harness matches program OUTPUT only — without
        // this line the whole exit classification (and in particular the refusal
        // to call an undetermined exit "normal") cannot be regression-locked.
        size_t n_rev = 0, n_und = 0;
        for (const auto &k : goto_coveraget::all_claims)
        {
          if (
            goto_coveraget::revert_paths.count(k) ||
            goto_coveraget::rollback_revert_paths.count(k))
            ++n_rev;
          else if (goto_coveraget::undetermined_exit_paths.count(k))
            ++n_und;
        }
        log_result(
          "Path Exits: normal {}, revert {}, undetermined {}",
          total - n_rev - n_und,
          n_rev,
          n_und);

        // Tri-state totals on stdout too, for the same reason as the exit line:
        // the per-path F/I/U verdicts live only in the JSON file, which the
        // regression harness cannot inspect. Without this the central honesty
        // rule — a claim that merely HELD within the bound is U, never I — is
        // not regression-locked. Same rule as the JSON emission below.
        {
          // See path_cov_can_prove_unreachable(): no coverage configuration can
          // establish unreachability, so this is false and every non-refuted
          // path is U. Deliberately NOT keyed on --solidity-max-tx 0, which the
          // back-edge-to-SKIP pass reduces to a single guarded transaction.
          const bool unb = path_cov_can_prove_unreachable();
          size_t nF = 0, nI = 0, nU = 0;
          // Every U carries a reason token. See path_u_reason_token(): a U cell
          // that can silently absorb an implementation defect has already done so
          // three times, and each time the broken case looked exactly like an
          // honest timeout.
          std::map<std::string, size_t> u_reasons;
          for (const auto &t : goto_coveraget::path_u_reason_tokens())
            u_reasons[t] = 0;
          std::vector<std::string> untokened;
          for (const auto &k : goto_coveraget::all_claims)
          {
            const std::string sig = k.first + "\t" + k.second;
            char v = 0;
            {
              std::lock_guard lk(goto_coveraget::claim_outcome_mutex);
              auto it_o = goto_coveraget::claim_outcome.find(sig);
              if (it_o != goto_coveraget::claim_outcome.end())
                v = it_o->second;
            }
            // ---- A VERDICT THAT WAS MADE MUST NOT BE LOST ----
            //
            // `v == 'F'` is in this disjunction because the two records of a
            // refutation are written at DIFFERENT TIMES and a dying run can land
            // between them. `claim_outcome[sig] = 'F'` is written the instant the
            // solver answers SAT; `reached_claims.emplace(sig)` is written only
            // after build_goto_trace, the counterexample harvest and every
            // artifact emitter have run. Everything in that window can throw.
            //
            // MEASURED on notes/coverage/poc/P16_Mapping.sol -- a 30-line nested
            // mapping, 8 paths, `std::bad_alloc` at 4 GB:
            //
            //     ✗ FAILED: 'put:path:7 at'
            //     Path Status: F 0, I 0, U 8
            //     ERROR: INTERNAL DEFECT — 1 path(s) are reported U with NO
            //            reason token: sol:@C@P16_Mapping@F@put#31:path:7
            //
            // The run printed the refutation and then reported zero refutations.
            // The path fell to the `else` below, `path_u_reason_token` has no
            // token for 'F' (correctly -- an F is not a U), and the invariant
            // fired. So the abort was RIGHT and its cause was here: a witnessed
            // path filed as undecided, which on a live run reads exactly like an
            // honest "we could not decide it".
            //
            // The payload may be absent for such a claim -- the harvest is what
            // died -- and that is reported as it is: `status: F` with an empty
            // `inputs` is the true statement (a witness exists, its values did
            // not survive), whereas dropping the F asserts something false about
            // the program. The same disjunction is applied to `witnessed` in the
            // JSON block so the file and the terminal cannot disagree.
            if (
              goto_coveraget::path_witnessed_earlier(k) ||
              reached_claims.count(sig) || v == 'F')
              ++nF;
            else if (v == 'P' && unb)
              ++nI;
            else
            {
              ++nU;
              const std::string tok = goto_coveraget::path_u_reason_token(k);
              if (tok.empty())
                untokened.push_back(k.first);
              else
                ++u_reasons[tok];
            }
          }
          log_result("Path Status: F {}, I {}, U {}", nF, nI, nU);
          // Printed whenever there is a U, with EVERY slot listed including the
          // zeros: a category that stops occurring is noticed, a category that
          // silently disappears from the output is not.
          if (nU > 0)
          {
            std::string breakdown;
            for (const auto &t : goto_coveraget::path_u_reason_tokens())
              breakdown += (breakdown.empty() ? "" : ", ") + t + " " +
                           std::to_string(u_reasons[t]);
            log_result("U Reasons: {}", breakdown);
          }
          if (!untokened.empty())
          {
            std::string names;
            for (const auto &n : untokened)
              names += (names.empty() ? "" : "; ") + n;
            log_error(
              "--solidity-path-coverage: INTERNAL DEFECT — {} path(s) are "
              "reported U with NO reason token: {}. The claim this pass makes "
              "is "
              "that every uncovered path carries a named reason and there is "
              "no "
              "unexplained remainder. An untokened U is that claim being "
              "false, "
              "and it is exactly the shape in which an implementation defect "
              "hides inside an honest-looking 'we do not know'.",
              untokened.size(),
              names);
            abort();
          }
        }
      }

      // Final write-back of the CONTENT-ADDRESSED cover.
      //
      // This call was missing, and the omission killed the whole cross-run
      // mechanism silently. `write_path_covered_set_atomic` existed and was
      // correct; nothing called it, while the code here called the BRANCH
      // metric's writer, gated on `covered_set_outpath` — which
      // solidity_path_coverage() never sets. Measured before the fix: running
      // with `--coverage-covered-set cov.json` produced no file at all, so every
      // round re-instrumented everything and the escalation story (round N spends
      // its budget only on paths still lacking a CE) did not work at all.
      //
      // Same defect shape as the entry-liveness audit before it was wired: a
      // function that is written, tested by eye, and never called. The function
      // itself selects only 'F' claims, so nothing but a real witness is ever
      // persisted.
      if (cov_set_active)
      {
        goto_coveraget::write_path_covered_set_atomic("at run end");
        log_success(
          "coverage covered-set written to {}",
          goto_coveraget::path_covered_outpath);
      }
    } // end of the non-certification reporting block
  }

  // Generate JSON coverage report
  if (options.get_bool_option("cov-report-json"))
  {
    using json = nlohmann::json;

    std::string cov_type = "unknown";
    if (is_branch_cov)
      cov_type = "branch";
    else if (is_branch_func_cov)
      cov_type = "branch-function";
    else if (is_k_path_cov)
      cov_type = "k-path";
    else if (is_path_cov)
      cov_type = is_path_probe ? "solidity-path-probe" : "solidity-path";
    else if (is_cond_cov)
      cov_type = "condition";
    else if (is_assert_cov)
      cov_type = "assertion";

    const auto &all_claims = goto_coveraget::all_claims;
    std::set<std::string> source_files;
    json claims_json = json::array();

    // Bound under which THIS run's verdicts were produced. Recorded on every
    // path entry: a "holds" verdict is only meaningful together with the
    // exploration it was obtained under.
    const std::string max_tx = options.get_option("solidity-max-tx");
    const std::string unwind_s = options.get_option("unwind");
    const bool k_induction_run = options.get_bool_option("k-induction");
    bool loops_truncated = false;
    {
      std::lock_guard lk(goto_functionst::truncated_loops_mutex);
      loops_truncated = !goto_functionst::truncated_loops.empty();
    }
    // See path_cov_can_prove_unreachable(). This used to be
    // `max_tx == "0" && !loops_truncated`, on the belief that max_tx 0 gives an
    // unbounded transaction sequence. It does not: coverage rewrites the
    // dispatcher back-edge to a SKIP, so max_tx 0 explores ONE transaction —
    // fewer than max_tx 2. Every non-refuted path is therefore U.
    const bool unbounded_run = path_cov_can_prove_unreachable();
    // What the transaction driver ACTUALLY explored, stated on every entry so a
    // reader never has to infer it from the flag. `--solidity-max-tx 0` is the
    // trap: it reads as "unbounded" and is in fact the shallowest setting.
    const std::string tx_exploration =
      max_tx == "0"
        ? std::string(
            "one guarded transaction: --solidity-max-tx 0 emits the "
            "`while (nondet) dispatch()` driver, but Solidity coverage "
            "rewrites "
            "every _ESBMC_Main* back-edge to a SKIP, so the loop runs at most "
            "once. This explores FEWER transactions than --solidity-max-tx 2")
        : (max_tx.empty()
             ? std::string(
                 "coverage default: the dispatcher loop back-edge is rewritten "
                 "to a SKIP, leaving one guarded transaction")
             : ("exactly " + max_tx +
                " straight-line transaction(s) (no dispatcher back-edge to "
                "remove)"));

    for (const auto &[claim_msg, claim_loc] : all_claims)
    {
      std::string claim_sig = claim_msg + "\t" + claim_loc;
      bool covered = reached_claims.count(claim_sig) > 0;

      // For assertion coverage, check reached_mul_claims instead
      if (is_assert_cov)
        covered = reached_mul_claims.count(claim_sig) > 0;

      json loc = parse_claim_location(claim_loc);
      std::string file = loc["file"];
      if (!file.empty())
        source_files.insert(file);

      json claim_entry;
      claim_entry["condition"] = prettify_solidity_expr(claim_msg);
      claim_entry["file"] = loc["file"];
      claim_entry["line"] = loc["line"];
      claim_entry["column"] = loc["column"];
      claim_entry["function"] = loc["function"];
      claim_entry["status"] = covered ? "covered" : "uncovered";
      // k-path Phase-2 (#4335): annotate each claim as feasible (a
      // maximal element of the subsumption lattice and thus part of the
      // spanning set) or spanning-set-redundant (subsumed by a stronger
      // emitted goal — covering it adds no information beyond covering
      // its subsumer).
      if (is_k_path_cov)
      {
        const auto &redundant = goto_coveraget::k_path_spanning_redundant;
        claim_entry["feasibility"] = redundant.count({claim_msg, claim_loc}) > 0
                                       ? "spanning-set-redundant"
                                       : "feasible";
      }

      // Solidity complete-path coverage: the CE->generalisation interface.
      // Tri-state per complete path, plus the bound the verdict was reached
      // under and how the path exits.
      if (is_path_cov)
      {
        char v = 0;
        {
          std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
          auto it_o = goto_coveraget::claim_outcome.find(claim_sig);
          if (it_o != goto_coveraget::claim_outcome.end())
            v = it_o->second;
        }
        // Witnessed = refuted this run, or already refuted in an earlier
        // escalation round (cross-run covered-set). Either way a concrete
        // input exists for this path.
        const bool prior =
          goto_coveraget::path_witnessed_earlier({claim_msg, claim_loc});
        // `v == 'F'`: the same disjunction the stdout counters use, for the
        // same reason. The refutation is recorded in `claim_outcome` the moment
        // the solver answers and in `reached_claims` only after the trace and
        // harvest have run, so a run that dies in between has a verdict that
        // `covered` cannot see. Keeping the two readers in step is what stops
        // the file and the terminal from disagreeing about how many paths were
        // witnessed. See the stdout block for the measured P16_Mapping case.
        const bool witnessed = covered || prior || v == 'F';

        // F: feasible, CE in hand.
        // I: PROVEN unreachable. No coverage configuration can establish this
        //    today — see path_cov_can_prove_unreachable() — so this arm is
        //    currently unreachable by construction, deliberately.
        // U: everything else, INCLUDING a claim that "holds" at this
        //    exploration: that means no witness within one-or-N transactions
        //    from the post-constructor entry state, not that the path is
        //    unreachable. It stays U and is flagged `bounded_holds`.
        std::string tri;
        if (witnessed)
          tri = "F";
        else if (v == 'P' && unbounded_run)
          tri = "I";
        else
          tri = "U";
        claim_entry["status"] = tri;
        if (tri == "U")
        {
          // Same single source as the stdout breakdown, so the file and the
          // terminal can never disagree about why a path is uncovered.
          const std::string tok =
            goto_coveraget::path_u_reason_token({claim_msg, claim_loc});
          claim_entry["u_reason"] = tok;
          if (tok == "named-obstacle")
          {
            auto ob =
              goto_coveraget::named_obstacle_paths.find({claim_msg, claim_loc});
            if (ob != goto_coveraget::named_obstacle_paths.end())
              claim_entry["u_reason_detail"] = ob->second;
          }
          else if (tok == "unit-not-entered")
          {
            // WHY the unit was not entered, not just that it wasn't: excluded
            // on purpose by --focus-function is a normal per-method run, while
            // "the harness never entered it" is a defect. A consumer that
            // cannot tell them apart would treat a deliberate narrowing as a
            // broken run, or worse, the reverse.
            const size_t p = claim_msg.rfind(":path:");
            const std::string unit =
              p == std::string::npos ? claim_msg : claim_msg.substr(0, p);
            auto ue = goto_coveraget::units_not_entered.find(unit);
            if (ue != goto_coveraget::units_not_entered.end())
              claim_entry["u_reason_detail"] = ue->second;
          }
        }
        // Keep the proof strategy explicit.  K-induction discharges loop
        // iterations but does not make the fixed transaction horizon
        // unbounded.
        if (v == 'P' && !unbounded_run)
        {
          if (k_induction_run)
            claim_entry["inductively_holds"] = true;
          else
            claim_entry["bounded_holds"] = true;
        }
        if (!witnessed && v == 0)
          // Never handed to the solver this run (sliced away, or skipped
          // because an earlier round already covered a different path).
          claim_entry["not_solved_this_run"] = true;

        claim_entry["bound"]["max_tx"] = max_tx.empty() ? "default" : max_tx;
        claim_entry["bound"]["unwind"] =
          k_induction_run ? "not-applicable"
                          : (unwind_s.empty() ? "default" : unwind_s);
        claim_entry["bound"]["kind"] =
          k_induction_run ? "k-induction"
                          : (unbounded_run ? "unbounded" : "bounded");
        if (k_induction_run)
        {
          const bool proof_closed = goto_coveraget::path_cov_k_induction_proved;
          claim_entry["bound"]["max_k_step"] = options.get_option("max-k-step");
          claim_entry["bound"]["base_case_unwind"] =
            unwind_s.empty() ? "default" : unwind_s;
          claim_entry["bound"]["proof_closed"] = proof_closed;
          claim_entry["bound"]["loop_proof"] =
            proof_closed ? "inductive proof over loop iterations"
                         : "k-induction inconclusive at max-k-step";
          claim_entry["bound"]["transaction_scope"] =
            "bounded by solidity-max-tx";
        }
        claim_entry["bound"]["tx_exploration"] = tx_exploration;
        if (loops_truncated)
          claim_entry["bound"]["loops_truncated"] = true;
        // Both revert shapes exit the transaction, so both are "revert": the
        // custom-error one (ASSUME(false) in a #sol_error callee) and the
        // rollback one (require/revert("msg"), which restores `*this` and then
        // reaches END_FUNCTION). Reporting the latter as "normal" would claim a
        // reverting transaction succeeded.
        // Certification replaces the enumerated claim with query-local
        // `#exitN` and `#nonvacuous` assertions. Exit metadata remains keyed
        // by the undecorated enumerated path, so recover that key before
        // classifying the query witness. In particular, the non-vacuity claim
        // is inserted at this path's own exit and must retain its revert kind.
        std::string metadata_claim_msg = claim_msg;
        const size_t query_suffix = metadata_claim_msg.rfind('#');
        if (query_suffix != std::string::npos)
        {
          const std::string suffix = metadata_claim_msg.substr(query_suffix);
          const bool numbered_exit =
            suffix.rfind("#exit", 0) == 0 && suffix.size() > 5 &&
            std::all_of(suffix.begin() + 5, suffix.end(), [](const char c) {
              return c >= '0' && c <= '9';
            });
          if (suffix == "#nonvacuous" || numbered_exit)
            metadata_claim_msg.resize(query_suffix);
        }
        const std::pair<std::string, std::string> metadata_key{
          metadata_claim_msg, claim_loc};
        const auto contains_path = [&](const auto &paths) {
          return std::any_of(paths.begin(), paths.end(), [&](const auto &key) {
            return key.first == metadata_claim_msg;
          });
        };
        // A certification query moves its assertions to query-specific exit
        // sites. The enumerator's metadata key retains the original exit
        // location, so the path comment, not the moved assertion location, is
        // the stable join key in this mode.
        const bool certification_claim = metadata_claim_msg != claim_msg;
        const bool ck_err =
          certification_claim
            ? contains_path(goto_coveraget::revert_paths)
            : goto_coveraget::revert_paths.count(metadata_key);
        const bool ck_rb =
          certification_claim
            ? contains_path(goto_coveraget::rollback_revert_paths)
            : goto_coveraget::rollback_revert_paths.count(metadata_key);
        const bool ck_un =
          certification_claim
            ? contains_path(goto_coveraget::undetermined_exit_paths)
            : goto_coveraget::undetermined_exit_paths.count(metadata_key);
        claim_entry["exit_kind"] =
          (ck_err || ck_rb) ? "revert" : (ck_un ? "undetermined" : "normal");
        if (ck_rb)
          // Distinguishes the two: here the rollback IS modelled, so
          // final_state is the correctly restored post-state.
          claim_entry["revert_kind"] = "rollback";
        else if (ck_err)
          claim_entry["revert_kind"] = "custom-error";
        else if (ck_un)
          claim_entry["exit_kind_undetermined_reason"] =
            "reaches END_FUNCTION bypassing the function epilogue and carries "
            "no rollback restore: a `require` failing before any state write "
            "and a plain early `return` compile to the same shape, so this is "
            "either a revert or a normal early exit";
        claim_entry["witnessed_in_earlier_round"] = prior && !covered;

        // PROVEN reachable only through a checked-arithmetic revert. Emitted
        // beside `status: F` rather than instead of it: the path IS feasible
        // and a witness exists, it is just a witness the chain reaches through
        // a Panic. A consumer that renders this path as a bare call asserting a
        // normal exit produces a RED test, which is the single outcome this
        // pipeline must never produce -- so the flag is published rather than
        // left to be inferred from the values.
        if (goto_coveraget::arith_revert_only_paths.count(
              {claim_msg, claim_loc}))
        {
          claim_entry["arith_revert_only"] = true;
          claim_entry["arith_revert_only_reason"] =
            "the re-solve of this claim under the enabled arithmetic check "
            "conditions is UNSAT, which PROVES that no input reaches this path "
            "without violating a checked operation. On chain the path is "
            "therefore reached through a Panic revert (0x11 overflow / 0x12 "
            "division by zero), NOT through the normal exit this entry's "
            "exit_kind reports -- exit_kind classifies the MODEL, and the "
            "model has no Panic. Render with vm.expectRevert, or do not render "
            "it at all; a bare call asserting a normal exit is red on the "
            "unmodified contract";
        }

        // path_id == enc(pi): the integer encoding the path's whole decision
        // sequence (tr accumulator). Text before ":path:" is the function.
        const auto pos = claim_msg.rfind(":path:");
        if (pos != std::string::npos)
        {
          claim_entry["path_id"] = claim_msg.substr(pos + 6);
          claim_entry["path_function"] = claim_msg.substr(0, pos);
        }
        // The decision depth, i.e. the `cnt` half of the path's identity. A
        // stage-2 query is `tr == enc && cnt == depth`, so a consumer that only
        // had `path_id` could not build one; it had to be told the depth out of
        // band, which is the opposite of an interface.
        {
          auto dp = goto_coveraget::path_decision_depth.find(metadata_key);
          if (
            certification_claim &&
            dp == goto_coveraget::path_decision_depth.end())
            dp = std::find_if(
              goto_coveraget::path_decision_depth.begin(),
              goto_coveraget::path_decision_depth.end(),
              [&](const auto &entry) {
                return entry.first.first == metadata_claim_msg;
              });
          if (dp != goto_coveraget::path_decision_depth.end())
            claim_entry["path_depth"] = dp->second;

          // ---- THE ORDERED DECISION SEQUENCE, for F claims only ----
          //
          // This is what makes path coverage comparable with branch coverage at
          // all: the two metrics share no denominator until the witnessed paths
          // are projected onto the DECISIONS they walk. `path_id` cannot be
          // projected — `enc` records the arms and nothing about the sites, so
          // only the enumerator knows which instruction each bit came from.
          //
          // RESTRICTED TO F on purpose, and it is not merely a saving. F is the
          // only status the projection can use (an uncovered path witnesses no
          // decision), and F is rare — single digits per unit — while a unit can
          // enumerate 120166 paths. Emitting for every claim would put the
          // report's size in the same order as the path count for no gain.
          if (tri == "F" && pos != std::string::npos)
          {
            const std::string unit = claim_msg.substr(0, pos);
            const uint64_t penc =
              strtoull(claim_msg.substr(pos + 6).c_str(), nullptr, 10);
            auto ti = goto_coveraget::path_decision_table.find(unit);
            auto xi = goto_coveraget::path_decision_index.find(unit);
            if (
              ti != goto_coveraget::path_decision_table.end() &&
              xi != goto_coveraget::path_decision_index.end() &&
              dp != goto_coveraget::path_decision_depth.end() && dp->second > 0)
            {
              const uint64_t pdepth = dp->second;
              json seq = json::array();
              size_t missing = 0;
              for (uint64_t k = 0; k < pdepth; ++k)
              {
                const uint64_t key = penc >> (pdepth - 1 - k);
                auto di = xi->second.find(key);
                if (di == xi->second.end() || di->second >= ti->second.size())
                {
                  // Reported as a hole, never skipped. A shorter array would
                  // read as "this path walks fewer decisions" — a claim about
                  // the path, when the truth is a claim about the recording.
                  json h;
                  h["index"] = k + 1;
                  h["unrecorded_prefix_enc"] = std::to_string(key);
                  seq.push_back(h);
                  ++missing;
                  continue;
                }
                const auto &d = ti->second[di->second];
                const bool arm_taken = (key & 1) != 0;
                json e;
                e["index"] = k + 1;
                json dloc = parse_claim_location(d.loc);
                e["file"] = dloc["file"];
                e["line"] = dloc["line"];
                e["column"] = dloc["column"];
                e["function"] = dloc["function"];
                e["operand"] = d.sub;
                e["arm"] = arm_taken ? "taken" : "fall-through";
                // This arm's claim text, already inverted (assert(P) fails when
                // P is false, so assert(guard) covers the FALL-THROUGH edge).
                // Published rather than left to the consumer: inverting it
                // there is a silent error that still produces a number.
                //
                // DIAGNOSTIC ONLY — do not join on it. Measured: a `require`
                // lowers to a guard one `not` deeper under path coverage than
                // under branch coverage (the revert-observation gate), so the
                // texts differ for the same decision while a plain `if` agrees
                // verbatim. See path_decisiont in goto_coverage.h. Join on
                // file+line, which is the comparison metric's own unit.
                e["branch_claim"] = prettify_solidity_expr(
                  arm_taken ? d.cond_arm_true : d.cond_arm_false);
                if (d.synthetic_abi_gate)
                  // No branch-coverage counterpart AND a location copied from
                  // the unit's first body instruction, so a consumer matching on
                  // location alone would credit a real decision on that line.
                  e["synthetic_abi_gate"] = true;
                seq.push_back(e);
              }
              claim_entry["decisions"] = seq;
              if (missing > 0)
                claim_entry["decisions_unrecorded"] = missing;
            }

            // ---- R0's EVENT RUNG: the emits this path walks, in order ----
            //
            // Read out of the published tables with exactly the same prefix
            // walk as the decisions above, so the two fields cannot disagree
            // about which path they describe. The inner map is keyed by program
            // position, and std::map iterates it in ascending key order, which
            // IS program order — that is the whole reason the recorder keys on
            // position rather than appending.
            //
            // WRITTEN EVEN WHEN EMPTY, and that is the point of the field. R0
            // is "same exit, same revert reason, SAME EVENTS IN THE SAME
            // ORDER"; a generator can only assert the third rung if it can tell
            // "this path emits nothing" apart from "nobody looked". An absent
            // key here means recording was off; `[]` means it was on and the
            // path emitted nothing recordable.
            //
            // ⚠ "nothing recordable" is not "nothing emitted". The qualified
            // spelling `emit L.E(x)` is dropped in the front end and never
            // reaches the goto program, so a consumer must not turn `[]` into
            // an assertion that NO event fires. It may only assert the events
            // that ARE listed, in the order listed.
            {
              auto ei = goto_coveraget::path_event_table.find(unit);
              auto vi = goto_coveraget::path_event_index.find(unit);
              if (
                ei != goto_coveraget::path_event_table.end() &&
                vi != goto_coveraget::path_event_index.end() &&
                dp != goto_coveraget::path_decision_depth.end())
              {
                json evs = json::array();
                for (uint64_t k = dp->second + 1; k-- > 0;)
                {
                  auto pit = vi->second.find(penc >> k);
                  if (pit == vi->second.end())
                    continue;
                  for (const auto &[evpos, evid] : pit->second)
                  {
                    (void)evpos;
                    if (evid < ei->second.size())
                      evs.push_back(ei->second[evid]);
                  }
                }
                claim_entry["events"] = evs;
              }
            }
          }
        }

        // CE payload: the concrete values behind an F. Absent for I/U (there
        // is no counterexample to report).
        std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
        auto it_ce = goto_coveraget::path_ce.find(claim_sig);
        if (it_ce != goto_coveraget::path_ce.end())
        {
          const auto &ce = it_ce->second;
          json ins = json::object();
          for (const auto &[n, v] : ce.inputs)
            ins[prettify_solidity_expr(n)] = v;
          json envj = json::object();
          for (const auto &[n, v] : ce.env)
            envj[prettify_solidity_expr(n)] = v;
          json fin = json::object();
          for (const auto &[n, v] : ce.final_state)
            fin[prettify_solidity_expr(n)] = v;
          claim_entry["inputs"] = ins;
          claim_entry["env"] = envj;
          const std::string testcase_fingerprint =
            foundry_gen.testcase_fingerprint_sha256_for_claim(claim_msg);
          if (!testcase_fingerprint.empty())
            claim_entry["foundry_testcase_fingerprint_sha256"] =
              testcase_fingerprint;
          // What the outside world returned on this path, in call order. Kept
          // separate from `inputs` because a consumer can CHOOSE an input and
          // cannot choose this: a replay has to mock the callee to return these
          // values. An ARRAY, not a map — repeated calls reuse the same symbol
          // name and their order is part of the answer.
          json ext = json::array();
          for (const auto &[n, v] : ce.extcall_returns)
            ext.push_back(
              {{"symbol", prettify_solidity_expr(n)}, {"value", v}});
          claim_entry["extcall_returns"] = ext;
          if (ce.extcall_returns.empty())
            // NOT "there were no external calls": say what is actually known.
            //
            // Below is what stands between the value and this field, measured
            // on a four-unit contract whose units differ ONLY in the syntactic
            // shape the call's result arrives in. There are THREE distinct
            // reasons, not one, and a fix for any single one leaves the field
            // empty in the other shapes -- which looks exactly like no fix at
            // all. ONE of the three is now fixed, so an empty list means less
            // than it used to and the message below says which cases remain.
            //
            //   (a) FIXED. Result bound to a named local (`bool ok = c.f();`)
            //       and the assembly form (`success` assigned inside an
            //       approximated block): the step DOES reach the harvest and
            //       get_nondet_symbol DOES resolve it. It used to be dropped
            //       because the classification had exactly three outcomes --
            //       parameter -> inputs, environment -> env, otherwise ->
            //       dropped_internal -- and a call's return is a LOCAL, so it
            //       landed in the third. A fourth bucket now takes it: see the
            //       `in_fn_scope && !is_param` branch in the classification.
            //       ⚠ That bucket holds nondet LOCALS in general, which is a
            //       superset of call returns and is documented as such there.
            //   (b) low-level `(bool ok, ) = a.call("")`: get_nondet_symbol
            //       returns nil, so the step is skipped BEFORE classification.
            //       (This is the mechanism an earlier version of this comment
            //       named -- correctly, but for this shape only.)
            //   (c) used inline (`if (c.f())`): no named local is ever
            //       assigned, so no step carries the value at all.
            //
            // The _ESBMC_Nondet_Extcall_* symbols that ARE in the trace are the
            // re-entry model's method-choice bits, not the returned value
            // (measured: identical on two paths that disagree about it), so
            // they are deliberately not reported here.
            claim_entry["ce_extraction"]["extcall_returns_unavailable_reason"] =
              "EMPTY here means shape (b) or (c) below, NOT shape (a): a value "
              "bound to a named local of the unit under test is now harvested "
              "into this list, so an empty list no longer covers that case. "
              "Two "
              "shapes remain and this field does NOT say which applies. "
              "⚠ AND A THIRD READING SURVIVES BOTH: the path may simply "
              "perform "
              "no external call. An empty list is UNKNOWN, never proof of "
              "absence. (a) FIXED -- bound to a named local, or assigned "
              "inside "
              "an approximated assembly block: the value is resolved and now "
              "lands in this list rather than being dropped for want of a "
              "bucket. ⚠ The list holds nondet LOCALS, which is how a call's "
              "return arrives but is not the same thing: read an entry as 'a "
              "quantity the harness chose that no test can pass as an "
              "argument', not as 'the callee returned this'. (b) low-level "
              "`(bool ok, ) = a.call(...)`: get_nondet_symbol returns nil, so "
              "the step is skipped before classification. (c) used inline, "
              "`if (c.f())`: no named local is assigned, so no trace step "
              "carries it. The _ESBMC_Nondet_Extcall_* symbols that ARE in the "
              "trace are the re-entry model's method-choice bits, not the "
              "returned value (measured: identical on two paths that disagree "
              "about it), so they are deliberately not reported here. An empty "
              "list means UNKNOWN, not 'this path performs no external call'";
          // Contract state this path STARTED from. Without it a path guarded by
          // state an earlier transaction established cannot be replayed from
          // the inputs alone.
          json entry = json::object();
          for (const auto &[n, v] : ce.entry_storage)
            entry[prettify_solidity_expr(n)] = v;
          claim_entry["entry_storage"] = entry;
          if (!ce.entry_storage_known)
            // Never let an empty entry_storage be read as "the contract started
            // empty": say that the marker was not observed instead.
            claim_entry["ce_extraction"]["entry_storage_unavailable_reason"] =
              "the entry marker for this path's function was not seen in the "
              "trace, so the state it started from was not captured; an empty "
              "entry_storage here means UNKNOWN, not 'no state'";
          // A custom-error revert in a scope the revert-observation gate does
          // NOT cover (constructor / library / free function) is still modelled
          // by ASSUME(false) with no rollback, so the harvested values are the
          // state AT the revert, not the post-state — on-chain the transaction
          // is undone and the post-state is the pre-call state. Publishing
          // those under `final_state` would be a wrong value carrying a
          // disclaimer; publish them under their true meaning instead and leave
          // `final_state` empty, since the post-state is genuinely unavailable.
          if (ce.revert_pre_rollback)
          {
            claim_entry["state_at_revert_point"] = fin;
            claim_entry["final_state"] = json::object();
          }
          else
            claim_entry["final_state"] = fin;
          // ---- THE UNIT'S OWN RETURN VALUE, BESIDE THE POST-STATE ----
          //
          // Never inside `final_state`: a return value is not state, and a
          // consumer that asserted it as state would assert the wrong thing
          // about the wrong object. This is the field the PUT emitter needs to
          // turn `c0.f(args);` into `assertEq(c0.f(args), v);`.
          //
          // `return_value_known` is emitted in BOTH directions. Absent, or an
          // empty string on its own, would read as "this unit returns nothing"
          // -- a claim about the CONTRACT manufactured out of an absence in the
          // harvest. The three ways to be unknown are genuinely different and
          // the reason names all of them.
          claim_entry["return_value_known"] = ce.return_value_known;
          if (ce.return_value_known)
            claim_entry["return_value"] = ce.return_value;
          else
          {
            // ---- SAY WHICH OF THE THREE, WHEN THE DECLARATION SETTLES IT ----
            //
            // The message below used to list three situations and refuse to
            // choose, which is honest and, for the commonest one, needlessly
            // so: whether the unit RETURNS ANYTHING AT ALL is not a property of
            // the trace, it is in the declaration, and the declaration is in
            // the symbol table this loop already has.
            //
            // WHY IT MATTERS RATHER THAN BEING TIDINESS. On farming/deposit the
            // six uncertified paths end at the same verdict and the driver's
            // reason lists the quantities that could separate the witness from
            // the counterexample. Three of them have now been excluded by
            // measurement -- the mapping slots, the shrink budget, and the
            // external-call return -- and `return_value` was still on the list
            // ONLY because this field would not say (a) from (b) or (c). For a
            // unit declared to return nothing there is no return value to be
            // the discriminator, and that has to come out of the tool rather
            // than out of a reader's inspection of the source.
            //
            // ⛔ IT ONLY SPEAKS WHEN IT KNOWS. If the lookup fails the sentence
            // is the old three-way one, unchanged: an unavailable declaration
            // is not evidence of a void return, and guessing here would put a
            // manufactured fact where the caveat used to be.
            const std::string declared = ce.declared_return;
            std::string why =
              "no scalar return value was captured on this path. THREE "
              "different situations produce this";
            if (declared == "none")
              why +=
                ", and the DECLARATION settles it: this unit is declared to "
                "return NOTHING, so it is situation (a) and there is no return "
                "value on this path for any consumer to be missing. (b) and "
                "(c) "
                "below are ruled out for this claim, not merely unlisted";
            else if (declared == "present")
              why +=
                ", and the DECLARATION rules out (a): this unit IS declared to "
                "return something, so the value is missing rather than absent. "
                "It is (b) or (c), and this field does not say which";
            else
              why +=
                " and the field does not say which -- the unit's declaration "
                "could not be looked up here, so not even (a) is settled";
            why +=
              ". (a) the unit returns nothing; (b) it returns an aggregate, "
              "tuple or dynamic type, which the instrumenter does not "
              "materialise because there is no single renderable value; (c) "
              "this path exits without reaching a RETURN at all, which is the "
              "normal shape of a revert. An absent value means UNKNOWN, never "
              "'this unit returns nothing' -- unless the declaration says so "
              "above";
            claim_entry["ce_extraction"]["return_value_unavailable_reason"] =
              why;
            claim_entry["ce_extraction"]["declared_return"] =
              declared.empty() ? "unknown" : declared;
          }
          // State this path WROTE but whose value is not renderable (mapping /
          // dynamic-array stores). Listed so "absent from final_state" is never
          // read as "unchanged".
          if (!ce.state_written_unrendered.empty())
          {
            json un = json::array();
            for (const auto &n : ce.state_written_unrendered)
              un.push_back(prettify_solidity_expr(n));
            claim_entry["state_written_value_unavailable"] = un;
          }
          // Not silent: say how many nondet values were classified as harness
          // plumbing and left out of `inputs`/`env`.
          claim_entry["ce_extraction"]["harness_nondets_dropped"] =
            ce.dropped_internal;
          // How the values were harvested. REQUIRED for the reader to
          // interpret an empty final_state: with `sliced` true the symex
          // slicer kept only what the path claim depends on — and a path
          // claim's guard mentions only the ghost accumulators — so state
          // writes are legitimately absent rather than non-existent. Re-run
          // with --no-slice to obtain the post-state.
          claim_entry["ce_extraction"]["sliced"] = ce.sliced;
          claim_entry["ce_extraction"]["payload_symbols_exempt_from_slicing"] =
            ce.payload_symbols_protected;
          claim_entry["ce_extraction"]["compact_trace"] = ce.compact_trace;
          claim_entry["ce_extraction"]["scoped_to_claim"] = ce.scoped_to_claim;
          if (!ce.scoped_to_claim)
            claim_entry["ce_extraction"]["post_state_may_include_later_tx"] =
              "this path's own assert was not found in the trace, so the whole "
              "trace was scanned; with more than one transaction the reported "
              "post-state may belong to a later transaction";
          // A custom-error revert is modelled by ASSUME(false) in the error
          // callee with NO state rollback, so the harvested post-state is the
          // state at the revert point. On-chain the transaction reverts and
          // every write is undone, i.e. the true post-state is the PRE-state.
          // Say so rather than let a consumer read these values as the result.
          if (ce.revert_pre_rollback)
            claim_entry["ce_extraction"]["post_state_unavailable_reason"] =
              "custom-error revert in a scope without rollback modelling "
              "(constructor / library / free function): the harvested values "
              "are published as `state_at_revert_point`, NOT as final_state. "
              "The real post-state of a reverted transaction is the pre-call "
              "state, which this model does not reconstruct";
          // Only meaningful when the payload symbols were NOT exempted: with
          // the exemption in place an empty final_state means this path writes
          // no state, and saying "the writes were sliced away" would be a
          // wrong explanation of a correct result.
          if (ce.sliced && !ce.payload_symbols_protected && fin.empty())
            claim_entry["ce_extraction"]["final_state_unavailable_reason"] =
              "slicing active and the payload symbols were not exempted: a "
              "path claim depends only on the ghost path accumulators, so "
              "contract state writes are sliced out of the counterexample. "
              "Ask for --cov-report-json (which exempts them) or re-run with "
              "--no-slice";

          // ---- EVERY WITNESS, WHEN THERE IS MORE THAN ONE ----
          //
          // `witness_count` is emitted unconditionally, because "this path has
          // one input tuple that reaches it" and "this path has sixteen and we
          // reported one" are different statements and the report could not
          // previously make either. The `witnesses` ARRAY is emitted only above
          // 1: at count 1 it would duplicate the fields directly above it on
          // every F claim of every run, and the report is already ~1.6 MB on a
          // 2846-path contract.
          //
          // The extra witnesses are the raw material the stage-2 ladder wants
          // -- sibling spans need more than one point in a path's domain, and
          // one counterexample cannot bracket a boundary.
          auto it_all = goto_coveraget::path_ce_all.find(claim_sig);
          const size_t n_wit = it_all == goto_coveraget::path_ce_all.end()
                                 ? 1
                                 : it_all->second.size();
          claim_entry["ce_extraction"]["witness_count"] = n_wit;
          if (n_wit > 1)
          {
            json wits = json::array();
            for (const auto &w : it_all->second)
            {
              json wj;
              json wi = json::object();
              for (const auto &[n, v] : w.inputs)
                wi[prettify_solidity_expr(n)] = v;
              json we = json::object();
              for (const auto &[n, v] : w.env)
                we[prettify_solidity_expr(n)] = v;
              json wf = json::object();
              for (const auto &[n, v] : w.final_state)
                wf[prettify_solidity_expr(n)] = v;
              json wen = json::object();
              for (const auto &[n, v] : w.entry_storage)
                wen[prettify_solidity_expr(n)] = v;
              wj["inputs"] = wi;
              wj["env"] = we;
              wj["entry_storage"] = wen;
              // Same rule as the single-witness block above: a revert without
              // rollback modelling has no post-state, and publishing the
              // values at the revert point under `final_state` would be a
              // wrong value carrying a disclaimer.
              if (w.revert_pre_rollback)
                wj["state_at_revert_point"] = wf;
              else
                wj["final_state"] = wf;
              wits.push_back(wj);
            }
            claim_entry["witnesses"] = wits;
          }
        }
        else if (tri == "F")
        {
          // Witnessed, but not by THIS run: the cross-run covered-set already
          // held it, so the path was not re-instrumented and no model was
          // produced here.
          //
          // The covered set now carries the PAYLOAD alongside the id, so this
          // is a lookup rather than an excuse. The old text said the values
          // "are in the report of the round that witnessed it" — which was a
          // hope, not a fact: that report is a file in a directory this run
          // knows nothing about, and on a run that died before writing one it
          // never existed. Emit the persisted values when they are there, and
          // keep an accurate reason when they are not.
          const auto *prior_ce =
            goto_coveraget::path_payload_earlier({claim_msg, claim_loc});
          if (prior_ce != nullptr)
          {
            json ins = json::object();
            for (const auto &[n, v] : prior_ce->inputs)
              ins[prettify_solidity_expr(n)] = v;
            json envj = json::object();
            for (const auto &[n, v] : prior_ce->env)
              envj[prettify_solidity_expr(n)] = v;
            json fin = json::object();
            for (const auto &[n, v] : prior_ce->final_state)
              fin[prettify_solidity_expr(n)] = v;
            json entry = json::object();
            for (const auto &[n, v] : prior_ce->entry_storage)
              entry[prettify_solidity_expr(n)] = v;
            claim_entry["inputs"] = ins;
            claim_entry["env"] = envj;
            claim_entry["entry_storage"] = entry;
            if (prior_ce->revert_pre_rollback)
            {
              claim_entry["state_at_revert_point"] = fin;
              claim_entry["final_state"] = json::object();
            }
            else
              claim_entry["final_state"] = fin;
            claim_entry["ce_extraction"]["sliced"] = prior_ce->sliced;
            claim_entry["ce_extraction"]["compact_trace"] =
              prior_ce->compact_trace;
            claim_entry["ce_extraction"]["scoped_to_claim"] =
              prior_ce->scoped_to_claim;
            claim_entry["ce_extraction"]["harness_nondets_dropped"] =
              prior_ce->dropped_internal;
            // NAMED, not silent. These values were harvested under a DIFFERENT
            // run's bound and slicing configuration, which is recorded in the
            // payload itself; a consumer that treats them as this run's own
            // output would attribute this run's `bound` block to them.
            claim_entry["ce_extraction"]["payload_source"] =
              "persisted by the earlier round that witnessed this path "
              "(cross-run covered-set), not harvested by this run; the "
              "bound/slicing flags in this block are that round's";
          }
          else
            claim_entry["ce_extraction"]["payload_absent_reason"] =
              "path was already witnessed in an earlier round (covered-set) "
              "and therefore not re-instrumented this run, and the covered-set "
              "file carries NO payload for it. This report cannot produce a "
              "test for this path; re-run without the covered-set, or with a "
              "covered-set written by a build that persists payloads";
        }
      }
      claims_json.push_back(claim_entry);
    }

    json certify_safety_json = json::array();
    if (is_path_cov && goto_coveraget::path_cov_certify_mode)
    {
      std::string certify_path_function;
      std::string certify_unit_name;
      {
        const std::string &nv =
          goto_coveraget::path_cov_certify_nonvacuous_key.first;
        const size_t p = nv.rfind(":path:");
        if (p != std::string::npos)
          certify_path_function = nv.substr(0, p);
        const size_t f = certify_path_function.find("@F@");
        const size_t h = certify_path_function.find('#', f);
        if (f != std::string::npos && h != std::string::npos)
          certify_unit_name = certify_path_function.substr(f + 3, h - f - 3);
      }
      std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
      for (const auto &[claim_msg, claim_loc] :
           goto_coveraget::path_cov_certify_safety_refutations)
      {
        const std::string claim_sig = claim_msg + "\t" + claim_loc;
        auto it_o = goto_coveraget::claim_outcome.find(claim_sig);
        if (it_o == goto_coveraget::claim_outcome.end() || it_o->second != 'F')
          continue;
        json loc = parse_claim_location(claim_loc);
        json entry;
        entry["condition"] = certify_unit_name.empty()
                               ? prettify_solidity_expr(claim_msg)
                               : certify_unit_name + ":safety";
        if (!certify_path_function.empty())
          entry["path_function"] = certify_path_function;
        entry["claim"] = prettify_solidity_expr(claim_msg);
        entry["file"] = loc["file"];
        entry["line"] = loc["line"];
        entry["column"] = loc["column"];
        entry["function"] = loc["function"];
        entry["status"] = "F";
        entry["safety_kind"] = "checked-arithmetic";
        auto it_ce = goto_coveraget::path_ce.find(claim_sig);
        if (it_ce != goto_coveraget::path_ce.end())
        {
          const auto &ce = it_ce->second;
          json ins = json::object();
          for (const auto &[n, v] : ce.inputs)
            ins[prettify_solidity_expr(n)] = v;
          json envj = json::object();
          for (const auto &[n, v] : ce.env)
            envj[prettify_solidity_expr(n)] = v;
          json ent = json::object();
          for (const auto &[n, v] : ce.entry_storage)
            ent[prettify_solidity_expr(n)] = v;
          json ext = json::array();
          for (const auto &[n, v] : ce.extcall_returns)
            ext.push_back(
              {{"symbol", prettify_solidity_expr(n)}, {"value", v}});
          entry["inputs"] = ins;
          entry["env"] = envj;
          entry["entry_storage"] = ent;
          entry["extcall_returns"] = ext;
        }
        certify_safety_json.push_back(entry);
      }
    }

    size_t total = all_claims.size();
    size_t covered_count = 0;
    for (const auto &c : claims_json)
      // Path coverage reports the tri-state in `status`; "F" is its
      // "covered" (a complete path with a counterexample in hand).
      if (c["status"] == "covered" || c["status"] == "F")
        covered_count++;

    // For k-path coverage, restrict the summary to maximal goals so the
    // JSON percentage matches the terminal spanning-set-filtered output.
    // Individual claims keep their `feasibility` annotation so consumers
    // that want raw counts can still derive them from the `claims` array.
    if (is_k_path_cov)
    {
      total = 0;
      covered_count = 0;
      for (const auto &c : claims_json)
        if (c["feasibility"] == "feasible")
        {
          ++total;
          if (c["status"] == "covered")
            ++covered_count;
        }
    }

    json report;
    // ---- PARTIAL, AT THE TOP LEVEL, IN BOTH DIRECTIONS ----
    //
    // A partial report is written to the same `cov-report.json` a complete one
    // is, because that is where every consumer looks and the alternative --
    // writing it somewhere else -- reproduces today's behaviour of keeping
    // nothing. The marker is therefore the ONLY thing separating them, so it is
    // emitted unconditionally: `false` on a complete run is what makes its
    // absence a detectable defect rather than an assumed default. A consumer
    // that reads `partial` with a default of false would otherwise treat a
    // report from a build that forgot to set it as complete.
    //
    // Duplicated under `summary` on purpose. Every existing reader of these
    // reports (report_summary.py, branch_gate.py, fset_cmp.py, gap_attribution
    // .py) opens `summary` and several never look at the top level at all; a
    // marker they cannot see is a marker that does not exist.
    report["partial"] = is_partial;
    if (is_partial)
      report["partial_reason"] = partial_reason;
    report["coverage_type"] = cov_type;
    report["source_files"] = json::array();
    for (const auto &f : source_files)
      report["source_files"].push_back(f);
    report["claims"] = claims_json;
    report["certify_safety_refutations"] = certify_safety_json;
    report["summary"]["partial"] = is_partial;
    if (is_partial)
    {
      report["summary"]["partial_reason"] = partial_reason;
      // The two numbers that say HOW partial. Without them a reader can see
      // that the run was cut short but not whether it was cut short at 1% or
      // 99%, and the difference decides whether the report is worth consuming
      // at all. Taken from the atomics the job loop maintains, so they are the
      // same numbers the signal handler would have printed.
      report["summary"]["claims_decided"] =
        goto_coveraget::live_decided.load(std::memory_order_relaxed);
      report["summary"]["claims_total"] =
        goto_coveraget::claims_total_atomic.load(std::memory_order_relaxed);
    }
    report["summary"]["total"] = total;
    report["summary"]["covered"] = covered_count;
    report["summary"]["uncovered"] = total - covered_count;
    report["summary"]["percentage"] =
      total > 0 ? covered_count * 100.0 / total : 0.0;
    // For branch / branch-function modes: explicit "no_branch" marker when the
    // source has zero branches. Key absent when total > 0 — consumers should
    // read with default false. Not emitted for condition/assertion/k-path
    // modes (their zero-claim case has different semantics).
    if (total == 0 && (is_branch_cov || is_branch_func_cov))
      report["summary"]["no_branch"] = true;

    // Path coverage: tri-state totals + the run's bound, so a consumer can
    // see at a glance how many complete paths have a CE (F), how many are
    // PROVEN unreachable (I, unbounded runs only), and how many are still
    // open (U) — with `bounded_holds` counting the U's that held within this
    // bound and are therefore worth re-checking unbounded.
    if (is_path_cov)
    {
      size_t nF = 0, nI = 0, nU = 0, nBH = 0, nIH = 0, nRevert = 0;
      for (const auto &c : claims_json)
      {
        const std::string s = c["status"];
        if (s == "F")
          ++nF;
        else if (s == "I")
          ++nI;
        else
          ++nU;
        if (c.contains("bounded_holds"))
          ++nBH;
        if (c.contains("inductively_holds"))
          ++nIH;
        if (c.value("exit_kind", "") == "revert")
          ++nRevert;
      }
      report["summary"]["paths_total"] = total;
      report["summary"]["F_feasible_with_ce"] = nF;
      report["summary"]["I_proven_unreachable"] = nI;
      report["summary"]["U_undecided"] = nU;
      report["summary"]["U_of_which_bounded_holds"] = nBH;
      report["summary"]["U_of_which_inductively_holds"] = nIH;

      // ---- HOW MANY WITNESSES THE REPORT ACTUALLY CARRIES ----
      //
      // Counted from `claims_json`, i.e. from what was EMITTED, not from the
      // producer's own map: this pass has already shipped one recorder that ran
      // on every path and was consumed by nothing, and a census taken from the
      // producer would have looked healthy throughout. Printed on stdout too,
      // because the regression harness matches program output only, so a field
      // that exists solely inside the file cannot be regression-locked.
      {
        size_t wtotal = 0, wmulti = 0;
        for (const auto &c : claims_json)
        {
          if (c.value("status", "") != "F")
            continue;
          const size_t n =
            c.contains("ce_extraction")
              ? c["ce_extraction"].value("witness_count", (size_t)1)
              : (size_t)1;
          wtotal += n;
          if (n > 1)
            ++wmulti;
        }
        report["summary"]["witnesses_total"] = wtotal;
        report["summary"]["F_with_multiple_witnesses"] = wmulti;
        log_status(
          "--solidity-path-coverage: CE PAYLOADS published for {} witness(es) "
          "across {} witnessed path(s); {} path(s) carry more than one "
          "(--all-witnesses is {}). Every witness of a path is a further point "
          "in that path's input domain, which is what a sibling span needs and "
          "what one counterexample cannot give",
          wtotal,
          nF,
          wmulti,
          options.get_bool_option("all-witnesses") ? "on" : "off");
      }

      // ---- THE DECISION-SEQUENCE PUBLICATION, COUNTED ON STDOUT ----
      //
      // A mechanism whose only evidence is a field inside a file nobody reads
      // has no evidence at all: this pass has already shipped one recorder that
      // ran on every path and was consumed by nothing. Counting it here, from
      // `claims_json` (i.e. from what was ACTUALLY emitted, not from what the
      // producer believes it emitted), makes "it fired" and "it fired on N
      // things" the same statement.
      //
      // `without` is printed even when it is zero, and it is the number that
      // matters: an F carrying no sequence cannot be projected onto decisions,
      // so it is a silent hole in any comparison built on this field.
      {
        size_t with = 0, without = 0, steps = 0, holes = 0, synth = 0;
        for (const auto &c : claims_json)
        {
          if (c.value("status", "") != "F")
            continue;
          if (!c.contains("decisions"))
          {
            ++without;
            continue;
          }
          ++with;
          for (const auto &d : c["decisions"])
          {
            ++steps;
            if (d.contains("unrecorded_prefix_enc"))
              ++holes;
            if (d.value("synthetic_abi_gate", false))
              ++synth;
          }
        }
        report["summary"]["decision_sequences"]["paths_with"] = with;
        report["summary"]["decision_sequences"]["paths_without"] = without;
        report["summary"]["decision_sequences"]["decision_steps"] = steps;
        report["summary"]["decision_sequences"]["unrecorded_steps"] = holes;
        report["summary"]["decision_sequences"]["synthetic_abi_gate_steps"] =
          synth;
        log_status(
          "--solidity-path-coverage: DECISION SEQUENCES published for {} of {} "
          "witnessed path(s) ({} step(s), {} unrecorded, {} synthesised ABI "
          "value gate). A path with no sequence cannot be projected onto "
          "decisions, and the synthesised gate has no branch-coverage "
          "counterpart, so both are named rather than folded in",
          with,
          nF,
          steps,
          holes,
          synth);
      }

      // ---- THE EVENT SEQUENCES, COUNTED THE SAME WAY AND FOR THE SAME REASON ----
      //
      // Counted from `claims_json`, i.e. from what was emitted, so "the rung
      // exists" and "the rung fired on N paths" are one statement.
      //
      // THE THREE NUMBERS ARE NOT REDUNDANT, and the middle one is the whole
      // point. `with_field` counts F claims carrying the array AT ALL;
      // `with_events` counts those whose array is NON-EMPTY. A contract that
      // emits nothing and a recorder that never fired both give
      // `with_events = 0` — they are told apart only by `without_field`, which
      // is 0 in the first case and nF in the second. Folding the two into one
      // count is exactly how an always-empty channel passes for a working one.
      {
        size_t with_field = 0, with_events = 0, without_field = 0, evsteps = 0;
        for (const auto &c : claims_json)
        {
          if (c.value("status", "") != "F")
            continue;
          if (!c.contains("events"))
          {
            ++without_field;
            continue;
          }
          ++with_field;
          const size_t n = c["events"].size();
          evsteps += n;
          if (n > 0)
            ++with_events;
        }
        report["summary"]["event_sequences"]["paths_with_field"] = with_field;
        report["summary"]["event_sequences"]["paths_without_field"] =
          without_field;
        report["summary"]["event_sequences"]["paths_with_events"] = with_events;
        report["summary"]["event_sequences"]["event_steps"] = evsteps;
        log_status(
          "--solidity-path-coverage: EVENT SEQUENCES published for {} of {} "
          "witnessed path(s), {} of them non-empty ({} emit(s) total). An "
          "EMPTY array is a measurement (\"this path emits nothing "
          "recordable\") and a MISSING field is not, so the two are counted "
          "apart; and `recordable` excludes the qualified spelling `emit "
          "L.E(x)`, which the front end drops",
          with_field,
          nF,
          with_events,
          evsteps);
      }
      // Every U's reason, with all slots present including the zeros — the
      // summary must never let a category vanish by simply not appearing.
      {
        json ur = json::object();
        for (const auto &t : goto_coveraget::path_u_reason_tokens())
          ur[t] = 0;
        for (const auto &c : claims_json)
          if (c.value("status", "") == "U")
          {
            const std::string tok = c.value("u_reason", "");
            if (!tok.empty())
              ur[tok] = ur[tok].get<size_t>() + 1;
          }
        report["summary"]["U_reasons"] = ur;
      }
      report["summary"]["revert_exit_paths"] = nRevert;
      // Its own cell beside F/I/U, never folded into any of them: "this path
      // needs an overflow" is a DECIDED property, and the cost of deciding it
      // travels with it so a reader can tell a run that measured this from a
      // run that did not ask.
      report["summary"]["arith_resolve"]["enabled"] =
        options.get_bool_option("path-cov-arith-resolve");
      report["summary"]["arith_resolve"]["conditions_seen"] =
        goto_coveraget::arith_conditions_seen.load(std::memory_order_relaxed);
      report["summary"]["arith_resolve"]["claims_resolved"] =
        goto_coveraget::arith_resolve_queries.load(std::memory_order_relaxed);
      report["summary"]["arith_resolve"]["seconds"] =
        goto_coveraget::arith_resolve_ms.load(std::memory_order_relaxed) /
        1000.0;
      // SAT re-solves, not wraps fixed. See the stdout line for why the
      // distinction is worth a key name this long.
      report["summary"]["arith_resolve"]["took_constrained_witness"] =
        goto_coveraget::arith_resolve_replaced.load(std::memory_order_relaxed);
      report["summary"]["arith_revert_only_paths"] =
        goto_coveraget::arith_revert_only_paths.size();
      report["summary"]["bound"]["max_tx"] =
        max_tx.empty() ? "default" : max_tx;
      report["summary"]["bound"]["unwind"] =
        k_induction_run ? "not-applicable"
                        : (unwind_s.empty() ? "default" : unwind_s);
      report["summary"]["bound"]["kind"] =
        k_induction_run ? "k-induction"
                        : (unbounded_run ? "unbounded" : "bounded");
      if (k_induction_run)
      {
        const bool proof_closed = goto_coveraget::path_cov_k_induction_proved;
        report["summary"]["bound"]["max_k_step"] =
          options.get_option("max-k-step");
        report["summary"]["bound"]["base_case_unwind"] =
          unwind_s.empty() ? "default" : unwind_s;
        report["summary"]["bound"]["proof_closed"] = proof_closed;
        report["summary"]["bound"]["loop_proof"] =
          proof_closed ? "inductive proof over loop iterations"
                       : "k-induction inconclusive at max-k-step";
        report["summary"]["bound"]["transaction_scope"] =
          "bounded by solidity-max-tx";
      }
      report["summary"]["bound"]["tx_exploration"] = tx_exploration;
      // THE PER-CLAIM BUDGET IS PART OF THE BOUND, not a footnote. A capped
      // run's U counts are not comparable with an uncapped run's: some of its
      // U's mean "we stopped asking", and a reader diffing two reports without
      // this field would read that as "no witness exists". Emitted in both
      // directions (0 = unlimited) for the same reason `partial` is.
      report["summary"]["bound"]["claim_timeout_s"] =
        goto_coveraget::claim_budget_seconds;
      report["summary"]["bound"]["claim_timeout_enforcement"] =
        goto_coveraget::claim_budget_mechanism.empty()
          ? std::string("unlimited: no per-claim budget was applied")
          : goto_coveraget::claim_budget_mechanism;
      report["summary"]["claims_abandoned_over_budget"] =
        goto_coveraget::claim_budget_exceeded.load(std::memory_order_relaxed);
      if (k_induction_run)
        report["summary"]["note"] =
          goto_coveraget::path_cov_k_induction_proved
            ? "loop assertions are discharged by k-induction; transaction "
              "exploration remains bounded by solidity-max-tx, so this does "
              "not claim unbounded multi-transaction reachability"
            : "k-induction did not close by max-k-step; bounded base-case "
              "holds were downgraded to unknown and no inductive proof is "
              "claimed";
      else if (!unbounded_run)
        report["summary"]["note"] =
          "no coverage configuration can establish unreachability, so I is "
          "never emitted and every path that merely held at this exploration "
          "is reported as U with bounded_holds=true. In particular "
          "--solidity-max-tx 0 is NOT an unbounded run: it emits the "
          "`while (nondet) dispatch()` driver, whose back-edge Solidity "
          "coverage then rewrites to a SKIP, leaving one guarded transaction "
          "— strictly fewer than --solidity-max-tx 2. Use a larger "
          "--solidity-max-tx N to explore deeper, not 0";
      // Known modelling limitation, stated rather than left for the reader to
      // rediscover: the entry state of a transaction is whatever the
      // constructor left behind. State variables are NOT havoc'd, so a path
      // guarded by state that only a PRIOR transaction can establish (a
      // balance, a role, an initialised flag) is unreachable at this
      // transaction bound and lands in U — not because the path is dead, but
      // because the run never built the state that unlocks it. Raising
      // --solidity-max-tx explores more of those; it does not remove the
      // limitation.
      report["summary"]["known_limitation_entry_state"] =
        "transaction entry state is the post-constructor state; contract state "
        "is not havoc'd, so paths guarded by state that an earlier transaction "
        "would have to establish are reported U at this tx bound. A U is "
        "therefore not evidence that the path is unreachable";

      if (is_path_probe)
      {
        json goals = json::array();
        size_t fired = 0, observations = 0, unattributed = 0;
        size_t varying_coordinates = 0, outside_variations = 0;
        std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
        for (const auto &[goal_id, goal] : goto_coveraget::path_probe_goals)
        {
          json gj;
          gj["id"] = goal_id;
          gj["unit"] = goal.unit;
          gj["decision_location"] = parse_claim_location(goal.decision_loc);
          gj["condition"] = prettify_solidity_expr(goal.condition);
          gj["arm"] = goal.arm;
          gj["exit_universe_truncated"] = goal.exit_universe_truncated;

          size_t claims = 0, nF = 0, nP = 0, nU = 0, nB = 0;
          for (const auto &[key, meta] : goto_coveraget::path_probe_claims)
          {
            if (meta.goal_id != goal_id)
              continue;
            ++claims;
            const std::string sig = key.first + "\t" + key.second;
            auto oi = goto_coveraget::path_probe_outcome.find(sig);
            const char v =
              oi == goto_coveraget::path_probe_outcome.end() ? 'U' : oi->second;
            if (v == 'F')
              ++nF;
            else if (v == 'P')
              ++nP;
            else if (v == 'B')
              ++nB;
            else
              ++nU;
          }
          gj["claims_total"] = claims;
          gj["claim_status"] = {{"F", nF}, {"P", nP}, {"U", nU}, {"B", nB}};
          gj["status"] = nF > 0 ? "F"
                                : (goal.exit_universe_truncated
                                     ? "U"
                                     : (nP == claims ? "P" : "U"));
          if (nF > 0)
            ++fired;

          std::map<
            std::pair<uint64_t, uint64_t>,
            std::vector<const goto_coveraget::path_ce_t *>>
            by_path;
          auto wi = goto_coveraget::path_probe_observations.find(goal_id);
          if (wi != goto_coveraget::path_probe_observations.end())
            for (const auto &ce : wi->second)
            {
              ++observations;
              if (!ce.observed_path_known)
              {
                ++unattributed;
                continue;
              }
              by_path[{ce.observed_path_id, ce.observed_path_depth}].push_back(
                &ce);
            }

          json paths = json::array();
          for (const auto &[path_key, members] : by_path)
          {
            json pj;
            pj["path_id"] = path_key.first;
            pj["decision_depth"] = path_key.second;
            json witnesses_json = json::array();
            std::map<std::string, std::set<std::string>> coordinate_values;
            std::map<std::string, std::set<std::string>> outside_values;
            for (const auto *ce : members)
            {
              json wj;
              auto pairs_object = [&](const auto &pairs) {
                json obj = json::object();
                for (const auto &[name, value] : pairs)
                  obj[prettify_solidity_expr(name)] = value;
                return obj;
              };
              wj["inputs"] = pairs_object(ce->inputs);
              wj["env"] = pairs_object(ce->env);
              wj["entry_storage"] = pairs_object(ce->entry_storage);
              json ext = json::array();
              for (const auto &[name, value] : ce->extcall_returns)
                ext.push_back(
                  {{"symbol", prettify_solidity_expr(name)}, {"value", value}});
              wj["extcall_returns"] = ext;
              witnesses_json.push_back(std::move(wj));

              for (const auto &[name, value] : ce->inputs)
                coordinate_values[prettify_solidity_expr(name)].insert(value);
              for (const auto &[name, value] : ce->env)
              {
                const std::string pretty = prettify_solidity_expr(name);
                if (pretty == "msg.sender" || pretty == "msg.value")
                  coordinate_values[pretty].insert(value);
                else
                  outside_values["env:" + pretty].insert(value);
              }
              for (const auto &[name, value] : ce->entry_storage)
                outside_values["entry:" + prettify_solidity_expr(name)].insert(
                  value);
              for (const auto &[name, value] : ce->extcall_returns)
                outside_values["nondet-local:" + prettify_solidity_expr(name)]
                  .insert(value);
            }
            pj["witnesses"] = std::move(witnesses_json);
            pj["witness_count"] = members.size();
            pj["varying_coordinates"] = json::array();
            pj["outside_coordinate_variation"] = json::array();
            for (const auto &[name, values] : coordinate_values)
              if (values.size() > 1)
              {
                pj["varying_coordinates"].push_back(name);
                ++varying_coordinates;
              }
            for (const auto &[name, values] : outside_values)
              if (values.size() > 1)
              {
                pj["outside_coordinate_variation"].push_back(name);
                ++outside_variations;
              }
            paths.push_back(std::move(pj));
          }
          gj["paths"] = std::move(paths);
          goals.push_back(std::move(gj));
        }
        report["probe"]["strategy"] = "branch-function-coverage";
        report["probe"]["attribution"] = "exit-latched-observed-tr-cnt";
        report["probe"]["goals"] = std::move(goals);
        report["probe"]["summary"] = {
          {"goals_total", goto_coveraget::path_probe_goals.size()},
          {"goals_fired", fired},
          {"observations_total", observations},
          {"observations_unattributed", unattributed},
          {"varying_path_coordinate_pairs", varying_coordinates},
          {"outside_coordinate_variations", outside_variations},
          {"nondets_kept_for_blocking",
           goto_coveraget::path_probe_nondets_kept.load(
             std::memory_order_relaxed)},
          {"nondets_dropped_from_blocking",
           goto_coveraget::path_probe_nondets_dropped.load(
             std::memory_order_relaxed)},
          {"valid", unattributed == 0}};
        log_status(
          "--path-cov-probe: {} of {} branch-arm goal(s) fired; {} complete "
          "execution observation(s), {} unattributed; {} varying coordinate "
          "pair(s), {} outside-coordinate variation(s); blocker kept {} and "
          "dropped {} nondet value(s)",
          fired,
          goto_coveraget::path_probe_goals.size(),
          observations,
          unattributed,
          varying_coordinates,
          outside_variations,
          goto_coveraget::path_probe_nondets_kept.load(
            std::memory_order_relaxed),
          goto_coveraget::path_probe_nondets_dropped.load(
            std::memory_order_relaxed));
      }
    }

    std::ofstream out("cov-report.json");
    out << report.dump(2) << std::endl;
    log_success("Coverage report written to cov-report.json");

    // Seal the journal. Everything before this point wrote `complete: false`,
    // which is the honest state of a file that is refreshed while the solve is
    // still running. A consumer must be able to tell the journal of a run that
    // finished from the journal of a run that was killed, and the only moment
    // the tool can say so is here — beside the report it did manage to write.
    if (is_path_cov && !goto_coveraget::path_ce_journal_path.empty())
      goto_coveraget::write_path_ce_journal_atomic(
        "at run end", /*complete=*/true);
  }

  // Generate pytest test case from collected data (for coverage mode)
  if (options.get_bool_option("generate-pytest-testcase"))
  {
    std::string input_file = options.get_option("input-file");
    std::string module_name = pytest_generator::extract_module_name(input_file);
    std::string pytest_filename =
      pytest_generator::generate_pytest_filename(module_name);
    pytest_gen.generate(pytest_filename);
  }

  // Generate CTest test cases from collected data (for coverage mode)
  if (options.get_bool_option("generate-ctest-testcase"))
  {
    ctest_gen.generate();
  }

  // Generate Foundry test cases from collected data (for coverage mode)
  if (options.get_bool_option("generate-foundry-testcase"))
  {
    foundry_gen.generate();
  }
}

// Output coverage information whenever an instrumented assertion is found violated.
// It is helpful when the program is too large and ESBMC cannot finish, we can still get some info about the coverage
void bmct::report_coverage_verbose(
  const claim_slicer &claim,
  const std::string &claim_sig,
  const bool &is_assert_cov,
  const bool &is_cond_cov,
  const bool &is_branch_cov,
  const bool &is_branch_func_cov,
  const std::unordered_set<std::string> &reached_claims,
  const std::unordered_multiset<std::string> &reached_mul_claims)
{
  // for condition coverage verbose output
  // total_cond: the combination of assertion's guard and location, which is used to identify each assertion in multi-property checking.

  auto current_pair = std::make_pair(claim.claim_msg, claim.claim_loc);

  if (is_cond_cov)
  {
    auto total_cond = goto_coveraget::total_cond;

    if (total_cond.count(current_pair))
    {
      if (
        options.get_bool_option("condition-coverage-claims") ||
        options.get_bool_option("condition-coverage-claims-rm"))
      {
        // show claims
        log_status("\n  {} : SATISFIED", prettify_solidity_expr(claim_sig));
      }

      // show coverage data
      log_result(
        "Current Condition Coverage: {}%\n",
        reached_claims.size() * 100.0 / total_cond.size());
    }
  }
  else
  {
    if (is_assert_cov)
    {
      const size_t total_instance = goto_coveraget::total_assert_ins;
      const size_t tracked_instance = reached_mul_claims.size();

      if (options.get_bool_option("assertion-coverage-claims"))
      {
        for (const auto &claim : reached_mul_claims)
          log_status("  {}", prettify_solidity_expr(claim));
      }
      if (total_instance != 0)
      {
        if (total_instance >= tracked_instance)
          log_result(
            "Assertion Instances Coverage: {}%",
            tracked_instance * 100.0 / total_instance);
        else
          log_result("Assertion Instances Coverage: 0%");
      }
    }
    else if (is_branch_cov)
    {
      size_t totals = goto_coveraget::total_branch;
      const int tracked_instance = reached_claims.size();
      if (totals == 0)
      {
        log_result("No branch detected");
      }
      else
      {
        // show claims
        if (options.get_bool_option("branch-coverage-claims"))
        {
          // reached claims:
          for (const auto &claim : reached_claims)
            log_status("  {}", prettify_solidity_expr(claim));
        }
        log_result("Branch Coverage: {}%", tracked_instance * 100.0 / totals);
      }
    }
    else if (is_branch_func_cov)
    {
      size_t totals = goto_coveraget::total_func_branch;
      const int tracked_instance = reached_claims.size();
      if (totals == 0)
      {
        log_result("No branch detected");
      }
      else
      {
        // show claims
        if (options.get_bool_option("branch-function-coverage-claims"))
        {
          // reached claims:
          for (const auto &claim : reached_claims)
            log_status("  {}", prettify_solidity_expr(claim));
        }
        log_result(
          "Branch Function Coverage: {}%", tracked_instance * 100.0 / totals);
      }
    }
    else
    {
      log_error("Unsupported coverage metrics");
      abort();
    }
  }
}

void bmct::report_result(smt_convt::resultt &res)
{
  // k-induction prints its own messages
  if (options.get_bool_option("k-induction-parallel"))
    return;
  // Diagnostic pass: per-property results are already printed by
  // multi_property_check; suppress any global verdict from this level.
  if (options.get_bool_option("diagnose-unknown-properties"))
    return;

  bool bs = options.get_bool_option("base-case");
  bool fc = options.get_bool_option("forward-condition");
  bool is = options.get_bool_option("inductive-step");
  bool term = options.get_bool_option("termination");
  bool mul = options.get_bool_option("multi-property");

  switch (res)
  {
  case smt_convt::P_UNSATISFIABLE:
    if (is && term)
    {
      report_failure();
    }
    else if (!bs || mul)
    {
      // Suppress spurious success when a violation was already found in a
      // previous k step (multi-property sequential k-induction).  The final
      // verdict is printed by do_bmc_strategy once the loop terminates.
      // Exception: assertion-coverage mode always reports success after
      // coverage analysis, regardless of any violations found.
      if (
        !options.get_bool_option("kind-violation-found") ||
        options.get_bool_option("assertion-coverage") ||
        options.get_bool_option("assertion-coverage-claims"))
        report_success();
    }
    else
    {
      log_status("No bug has been found in the base case");
    }
    break;

  case smt_convt::P_SATISFIABLE:
    if (!is && !fc)
    {
      report_failure();
    }
    else if (fc)
    {
      log_status("The forward condition is unable to prove the property");
    }
    else if (is)
    {
      log_status("The inductive step is unable to prove the property");
    }
    break;

    // Return failure if we didn't actually check anything, we just emitted the
    // test information to an SMTLIB formatted file. Causes esbmc to quit
    // immediately (with no error reported)
  case smt_convt::P_SMTLIB:
    return;

  default:
    log_error("SMT solver failed");
    break;
  }

  if ((interleaving_number > 0) && options.get_bool_option("all-runs"))
  {
    log_status("Number of generated interleavings: {}", interleaving_number);
    log_status("Number of failed interleavings: {}", interleaving_failed);
  }
}

smt_convt::resultt bmct::start_bmc()
{
  std::shared_ptr<symex_target_equationt> eq;
  smt_convt::resultt res = run(eq);
  if (!options.get_bool_option("multi-property"))
    // multi-property traces are output during the run(eq)
    report_trace(res, *eq);
  report_result(res);
  return res;
}

smt_convt::resultt bmct::run(std::shared_ptr<symex_target_equationt> &eq)
{
  symex->options.set_option("unwind", options.get_option("unwind"));
  symex->setup_for_new_explore();

  if (options.get_bool_option("schedule"))
    return run_thread(eq);

  smt_convt::resultt res;
  do
  {
    if (++interleaving_number > 1)
      log_status("Thread interleavings {}", interleaving_number);

    // Clear the cache between thread interleavings to prevent
    // incorrect caching of assertions with different thread contexts
    if (!options.get_bool_option("no-cache-asserts"))
      config.ssa_caching_db.clear();

    fine_timet bmc_start = current_time();
    res = run_thread(eq);

    if (res == smt_convt::P_SATISFIABLE)
    {
      if (config.options.get_bool_option("smt-model"))
        runtime_solver->print_model();

      if (config.options.get_bool_option("bidirectional"))
        bidirectional_search(*runtime_solver, *eq);
    }

    if (res)
    {
      if (res == smt_convt::P_SATISFIABLE)
        ++interleaving_failed;

      if (!options.get_bool_option("all-runs"))
        return res;
    }
    fine_timet bmc_stop = current_time();

    log_status("BMC program time: {}s", time2string(bmc_stop - bmc_start));

    // Only run for one run
    if (options.get_bool_option("interactive-ileaves"))
      return res;

  } while (symex->setup_next_formula());

  if (options.get_bool_option("ltl"))
  {
    // So, what was the lowest value ltl outcome that we saw?
    if (ltl_results_seen[ltl_res_bad])
      log_result("Final lowest outcome: LTL_BAD");
    else if (ltl_results_seen[ltl_res_failing])
      log_result("Final lowest outcome: LTL_FAILING");
    else if (ltl_results_seen[ltl_res_succeeding])
      log_result("Final lowest outcome: LTL_SUCCEEDING");
    else if (ltl_results_seen[ltl_res_good])
      log_result("Final lowest outcome: LTL_GOOD");
    else
      log_warning("No LTL traces seen, apparently");
  }

  return interleaving_failed > 0 ? smt_convt::P_SATISFIABLE : res;
}

void bmct::bidirectional_search(
  smt_convt &smt_conv,
  const symex_target_equationt &eq)
{
  // We should only analyze the inductive step's cex and we're running
  // in k-induction mode
  if (!(options.get_bool_option("inductive-step") &&
        options.get_bool_option("k-induction")))
    return;

  // We'll walk list of SSA steps and look for inductive assignments
  std::vector<stack_framet> frames;
  unsigned assert_loop_number = 0;
  for (const auto &ssait : eq.SSA_steps)
  {
    if (ssait.is_assert() && smt_conv.l_get(ssait.cond_ast).is_false())
    {
      if (!ssait.loop_number)
        return;

      // Save the location of the failed assertion
      frames = ssait.stack_trace;
      assert_loop_number = ssait.loop_number;

      // We are not interested in instructions before the failed assertion yet
      break;
    }
  }

  for (auto f : frames)
  {
    // Look for the function
    goto_functionst::function_mapt::iterator fit =
      symex->goto_functions.function_map.find(f.function);
    assert(fit != symex->goto_functions.function_map.end());

    // Find function loops
    goto_loopst loops(f.function, symex->goto_functions, fit->second);

    if (!loops.get_loops().size())
      continue;

    auto lit = loops.get_loops().begin(), lie = loops.get_loops().end();
    while (lit != lie)
    {
      auto loop_head = lit->get_original_loop_head();

      // Skip constraints from other loops
      if (loop_head->loop_number == assert_loop_number)
        break;

      ++lit;
    }

    if (lit == lie)
      continue;

    // Get the loop vars
    auto all_loop_vars = lit->get_modified_loop_vars();
    all_loop_vars.insert(
      lit->get_unmodified_loop_vars().begin(),
      lit->get_unmodified_loop_vars().end());

    // Now, walk the SSA and get the last value of each variable before the loop
    std::unordered_map<irep_idt, std::pair<expr2tc, expr2tc>, irep_id_hash>
      var_ssa_list;

    for (const auto &ssait : eq.SSA_steps)
    {
      if (ssait.loop_number == lit->get_original_loop_head()->loop_number)
        break;

      if (ssait.ignore)
        continue;

      if (!ssait.is_assignment())
        continue;

      expr2tc new_lhs = ssait.original_lhs;
      renaming::renaming_levelt::get_original_name(new_lhs, symbol2t::level0);

      if (all_loop_vars.find(new_lhs) == all_loop_vars.end())
        continue;

      var_ssa_list[to_symbol2t(new_lhs).thename] = {
        ssait.original_lhs, ssait.rhs};
    }

    if (!var_ssa_list.size())
      return;

    // Query the solver for the value of each variable
    std::vector<expr2tc> equalities;
    for (auto it : var_ssa_list)
    {
      // We don't support arrays or pointers
      if (is_array_type(it.second.first) || is_pointer_type(it.second.first))
        return;

      auto lhs = build_lhs(smt_conv, it.second.first);
      auto value = build_rhs(smt_conv, it.second.second);

      // Add lhs and rhs to the list of new constraints
      equalities.push_back(equality2tc(lhs, value));
    }

    // Build new assertion
    expr2tc constraints = equalities[0];
    for (std::size_t i = 1; i < equalities.size(); ++i)
      constraints = and2tc(constraints, equalities[i]);

    // and add it to the goto program
    goto_programt::targett loop_exit = lit->get_original_loop_exit();

    goto_programt::instructiont i;
    i.make_assertion(not2tc(constraints));
    i.location = loop_exit->location;
    i.location.user_provided(true);
    i.loop_number = loop_exit->loop_number;
    i.inductive_assertion = true;

    fit->second.body.insert_swap(loop_exit, i);

    // recalculate numbers, etc.
    symex->goto_functions.update();
    return;
  }
}

smt_convt::resultt bmct::run_thread(std::shared_ptr<symex_target_equationt> &eq)
{
  // Clear collected pytest test data at the start of coverage run
  if (options.get_bool_option("generate-pytest-testcase"))
    pytest_gen.clear();

  // Clear collected ctest test data at the start of coverage run
  if (options.get_bool_option("generate-ctest-testcase"))
    ctest_gen.clear();

  fine_timet symex_start = current_time();
  try
  {
    goto_symext::symex_resultt solver_result =
      options.get_bool_option("schedule") ? symex->generate_schedule_formula()
                                          : symex->get_next_formula();

    fine_timet symex_stop = current_time();

    eq =
      std::dynamic_pointer_cast<symex_target_equationt>(solver_result.target);

    log_status(
      "Symex completed in: {}s ({} assignments)",
      time2string(symex_stop - symex_start),
      eq->SSA_steps.size());

    if (options.get_bool_option("double-assign-check"))
      eq->check_for_duplicate_assigns();

    BigInt ignored;
    for (auto &a : algorithms)
    {
      a->run(eq->SSA_steps);
      ignored += a->ignored();
    }

    // Count remaining assertions after all algorithms have run
    BigInt remaining_asserts = 0;
    for (const auto &step : eq->SSA_steps)
    {
      if (step.is_assert() && !step.ignore)
        ++remaining_asserts;
    }

    if (
      options.get_bool_option("program-only") ||
      options.get_bool_option("program-too"))
      show_program(*eq);

    if (options.get_bool_option("program-only"))
      return smt_convt::P_SMTLIB;

    log_status(
      "Generated {} VCC(s), {} remaining after simplification ({} "
      "assignments)",
      solver_result.total_claims,
      remaining_asserts,
      BigInt(eq->SSA_steps.size()) - ignored);

    if (options.get_bool_option("document-subgoals"))
    {
      std::ostringstream oss;
      document_subgoals(*eq, oss);
      log_status("{}", oss.str());
      return smt_convt::P_SMTLIB;
    }

    if (options.get_bool_option("show-vcc"))
    {
      show_vcc(*eq);
      return smt_convt::P_SMTLIB;
    }

    if (solver_result.remaining_claims == 0)
    {
      if (options.get_bool_option("smt-formula-only"))
      {
        log_status(
          "No VCC remaining, no SMT formula will be generated for"
          " this program\n");
        return smt_convt::P_SMTLIB;
      }

      // In coverage mode, still print the coverage summary even when all
      // claims are simplified away (e.g., straight-line code with 0 branches).
      //
      // BUT NOT under --k-induction / --incremental-bmc. Those drivers run
      // several bmct phases and emit the authoritative summary themselves
      // (do_bmc_strategy in esbmc_parseoptions.cpp), from the accumulated
      // goto_functionst::reached_claims. A phase whose equation simplifies
      // to zero claims -- which is the normal outcome of the final inductive
      // step -- would otherwise print a second "[Coverage] ... Reached : 0 ...
      // Branch Coverage: 0%" block from the deliberately EMPTY sets below,
      // immediately before the real one. Two blocks, the first always 0%:
      // any consumer that reads the first match (or greps `head -1`)
      // concludes k-induction covered nothing, when the run in fact reached
      // 9/10 on the same input. multi_property_check's own report_coverage
      // call already carries exactly this guard; this early-return path was
      // missing it.
      if (
        options.get_bool_option("multi-property") &&
        !options.get_bool_option("k-induction") &&
        !options.get_bool_option("incremental-bmc"))
      {
        std::unordered_set<std::string> empty_reached;
        std::unordered_multiset<std::string> empty_mul_reached;
        pytest_generator empty_pytest;
        ctest_generator empty_ctest;
        foundry_generator empty_foundry;
        report_coverage(
          options,
          empty_reached,
          empty_mul_reached,
          empty_pytest,
          empty_ctest,
          empty_foundry);
      }

      return smt_convt::P_UNSATISFIABLE;
    }

    if (options.get_bool_option("ltl"))
    {
      int res = ltl_run_thread(*eq);
      if (res == -1)
        return smt_convt::P_SMTLIB;
      if (res < 0)
        return smt_convt::P_ERROR;
      // Record that we've seen this outcome; later decide what the least
      // outcome was.
      ltl_results_seen[res]++;
      return smt_convt::P_UNSATISFIABLE;
    }

    if (!options.get_bool_option("smt-during-symex"))
    {
      runtime_solver =
        std::unique_ptr<smt_convt>(create_solver("", ns, options));
    }

    if (
      options.get_bool_option("multi-property") &&
      (options.get_bool_option("base-case") ||
       options.get_bool_option("diagnose-unknown-properties") ||
       (options.get_bool_option("inductive-step") &&
        options.get_bool_option("loop-invariant"))))
      return multi_property_check(
        *eq, solver_result.remaining_claims, *runtime_solver);

    return run_decision_procedure(*runtime_solver, *eq);
  }

  catch (std::string &error_str)
  {
    log_error("{}", error_str);
    return smt_convt::P_ERROR;
  }

  catch (const char *error_str)
  {
    log_error("{}", error_str);
    return smt_convt::P_ERROR;
  }

  catch (std::bad_alloc &)
  {
    log_error("Out of memory\n");
    return smt_convt::P_ERROR;
  }
}

int bmct::ltl_run_thread(symex_target_equationt &equation) const
{
  /* LTL checking - first check for whether we have a negative prefix, then
   * the indeterminate ones. */
  using Type = std::pair<std::string_view, ltl_res>;
  static constexpr std::array seq = {
    Type{"LTL_BAD", ltl_res_bad},
    Type{"LTL_FAILING", ltl_res_failing},
    Type{"LTL_SUCCEEDING", ltl_res_succeeding},
  };

  for (const auto &[which, check] : seq)
  {
    size_t num_asserts = 0;

    /* Start by turning all assertions that aren't the sought prefix assertion
     * into skips. */
    for (auto &SSA_step : equation.SSA_steps)
      if (SSA_step.is_assert())
      {
        if (SSA_step.comment != which)
          SSA_step.type = goto_trace_stept::SKIP;
        else
          num_asserts++;
      }

    smt_convt::resultt solver_result = smt_convt::P_UNSATISFIABLE;
    log_status("Checking for {}", which);
    if (num_asserts != 0)
    {
      std::unique_ptr<smt_convt> smt_conv(create_solver("", ns, options));
      solver_result = run_decision_procedure(*smt_conv, equation);
      if (solver_result == smt_convt::P_SATISFIABLE)
        log_status("Found trace satisfying {}", which);
    }
    else
      log_warning("Couldn't find {} assertion", which);

    /* Turn skip steps back into assertions. */
    for (auto &SSA_step : equation.SSA_steps)
      if (SSA_step.is_skip())
        for (const auto &[which2, _] : seq)
          if (SSA_step.comment == which2)
          {
            SSA_step.type = goto_trace_stept::ASSERT;
            break;
          }

    switch (solver_result)
    {
    case smt_convt::P_SATISFIABLE:
      return check;
    case smt_convt::P_ERROR:
      return -2;
    case smt_convt::P_SMTLIB:
      return -1;
    case smt_convt::P_UNSATISFIABLE:
      continue;
    }
  }

  /* Otherwise, we just got a good prefix. */
  return ltl_res_good;
}

smt_convt::resultt bmct::multi_property_check(
  const symex_target_equationt &eq,
  size_t remaining_claims,
  smt_convt &runtime_solver)
{
  // Initial values
  smt_convt::resultt final_result = smt_convt::P_UNSATISFIABLE;
  std::mutex result_mutex;
  std::atomic<size_t> ce_counter{0};
  // How many claims this run has actually DECIDED (a solver verdict came back).
  // Three consumers, and none of them can be served by a count taken after the
  // loop, because the whole point is that the loop may not finish:
  //   * the "mid-solve after claim N of M" label on every incremental publish;
  //   * the PARTIAL report's `claims_decided` / `claims_total`;
  //   * the signal-safe snapshot the kill handler reads.
  std::atomic<size_t> decided_claims{0};
  // A solver non-answer is not a decided property and dominates the aggregate
  // result even if another parallel job later obtains SAT or UNSAT. P_SMTLIB
  // stays distinct so formula-output mode can emit every property.
  std::atomic<bool> solver_error_seen{false};
  std::atomic<bool> smtlib_seen{false};
  // Sequential default consumes `jobs` in iteration order; using a sorted
  // vector lets us solve user-source claims before c2goto/library claims so
  // multi-property doesn't burn the budget on spurious library-side dereference
  // / overflow checks ahead of the user's real bugs.
  std::vector<size_t> jobs;

  // Add summary tracking
  SimpleSummary summary;
  summary.simplified_properties = symex->get_cur_state().simplified_claims;
  summary.total_properties = remaining_claims + summary.simplified_properties;
  summary.passed_properties =
    summary.passed_properties + summary.simplified_properties;

  // For coverage info
  auto &reached_claims = symex->goto_functions.reached_claims;
  auto &reached_mul_claims = symex->goto_functions.reached_mul_claims;
  auto &reached_claims_mutex = symex->goto_functions.reached_claims_mutex;
  auto &reached_mul_claims_mutex =
    symex->goto_functions.reached_mul_claims_mutex;

  // "Assertion Cov"
  bool is_assert_cov = options.get_bool_option("assertion-coverage") ||
                       options.get_bool_option("assertion-coverage-claims");
  // "Condition Cov"
  bool is_cond_cov = options.get_bool_option("condition-coverage") ||
                     options.get_bool_option("condition-coverage-claims") ||
                     options.get_bool_option("condition-coverage-rm") ||
                     options.get_bool_option("condition-coverage-claims-rm");
  // "Branch Cov"
  bool is_branch_cov = options.get_bool_option("branch-coverage") ||
                       options.get_bool_option("branch-coverage-claims");
  bool is_branch_func_cov =
    options.get_bool_option("branch-function-coverage") ||
    options.get_bool_option("branch-function-coverage-claims");
  const bool is_path_probe =
    options.get_bool_option("solidity-path-probe-enabled");
  if (is_path_probe)
    is_branch_func_cov = false;
  // "k-Path Cov" — keyed off the dedicated boolean (see line ~717
  // comment); needed in the is_goto_cov disjunction so the
  // claim_slicer reads the witness comment, matching the form stored
  // in goto_coveraget::all_claims.
  bool is_k_path_cov = options.get_bool_option("k-path-coverage-enabled");
  // "Solidity complete-path Cov" — goals carry enc(pi) in the comment, same
  // as k-path, so it must join the is_goto_cov disjunction below or
  // claim_slicer would read the negated-expr instead of the comment and
  // every JSON entry would show up uncovered.
  bool is_path_cov = options.get_bool_option("solidity-path-coverage-enabled");

  // is_vb: enable verbose output coverage info if the option "--verbosity coverage:N" is set, where N should larger than 0
  // By enabling this, we will output the coverage information when handling each instrumentation assertion.
  bool is_vb = messaget::state.modules["coverage"] != VerbosityLevel::None;

  // For incr/kind in multi-property
  bool is_keep_verified = options.get_bool_option("keep-verified-claims");
  bool bs = options.get_bool_option("base-case");
  bool fc = options.get_bool_option("forward-condition");
  bool is = options.get_bool_option("inductive-step");

  // For multi-fail-fast
  const std::string fail_fast = options.get_option("multi-fail-fast");
  const bool is_fail_fast = !fail_fast.empty() ? true : false;
  const int fail_fast_limit = is_fail_fast ? stoi(fail_fast) : 0;
  std::atomic<int> fail_fast_cnt{0};

  if (is_fail_fast && fail_fast_limit < 0)
  {
    log_error("the value of multi-fail-fast should be positive!");
    abort();
  }

  // For color output
  bool is_color = options.get_bool_option("color");
  const std::string YELLOW = is_color ? "\033[33m" : "";

  // TODO: This is the place to check a cache
  jobs.reserve(remaining_claims);
  for (size_t i = 1; i <= remaining_claims; i++)
    jobs.push_back(i);

  // Published BEFORE the first solve, so that a run killed during job 1 still
  // has a denominator to report its partial numerator against. Set here rather
  // than in the pass, because it is the count of claims that survived
  // simplification and reached this loop — which is what "decided so far" is a
  // fraction of, and is not the instrumented path count.
  goto_coveraget::live_decided.store(0, std::memory_order_relaxed);
  goto_coveraget::claims_total_atomic.store(
    remaining_claims, std::memory_order_relaxed);

  // The exact set of claims this loop was given, recorded BEFORE it starts.
  // It is what lets a partial run tell "the loop never got to this claim" from
  // "the simplifier folded this claim away and no loop was ever going to see
  // it" -- two facts with opposite next actions that would otherwise share one
  // U-reason cell. Taken from the equation rather than from the job indices
  // because an index says nothing about WHICH claim it is until the per-job
  // slicer has run, and a job that never runs never slices.
  if (is_path_cov)
  {
    std::set<std::string> queued;
    for (const auto &step : eq.SSA_steps)
      if (step.is_assert() && !step.ignore)
        queued.insert(step.comment);
    goto_coveraget::claims_in_solve_loop.swap(queued);

    // ---- ARM THE PER-KEY MULTIPLICITY CHECK ----
    //
    // The ceiling is the number of transactions the harness executes, because
    // that is how many times one assert instruction can be reached. The rule
    // that produces it is get_tx_bound() in the Solidity frontend: an explicit
    // --solidity-max-tx wins, and path coverage's default is 2. `0` is NOT
    // unbounded here -- coverage rewrites the dispatcher back-edge to a SKIP,
    // leaving exactly one transaction -- so it maps to 1, not to "no limit".
    //
    // --path-cov-max-claim-solves overrides it, and exists so the REFUSAL can
    // be exercised: with a sound instrumentation the ceiling is never reached,
    // so a check armed only from the transaction count could never be shown to
    // fire, and an unfireable check is the shape this pass has already shipped.
    size_t tx = 2;
    const std::string mt = options.get_option("solidity-max-tx");
    if (!mt.empty())
    {
      const long v = std::stol(mt);
      tx = v <= 0 ? 1 : (size_t)v;
    }
    // ---- THE TRANSACTION COUNT IS NOT THE WHOLE CEILING ----
    //
    // An external call is modelled as nondet RE-ENTRY into the contract's own
    // dispatcher, so one instrumented assert is instantiated once per re-entry
    // level, and the level count is `--unwind`. MEASURED on st1inch, 22 unit
    // logs (notes/coverage/D33): the five units that call out
    // (safeTransferFrom / safeTransfer / Address.sendValue) have a VCC/path
    // ratio of exactly 4.00 against `--unwind 4`, while the fifteen that do not
    // sit at 1.00. That is the documented behaviour of the re-entry model, not
    // duplicate instrumentation, and a ceiling of `tx` alone would abort every
    // one of those units.
    //
    // So the ceiling is tx x unwind: a sound upper bound on how many times one
    // assert INSTRUCTION can be reached. It is deliberately loose. The precise
    // duplication D33 names -- a constructor that calls a public unit, giving
    // the unit's body two identities under one claim key -- sits well inside
    // this bound and is NOT what this check is for; it is addressed by taking
    // the deployment out of the unit query (--path-cov-fixture). What this
    // catches is gross duplication, and what makes the D33 case visible is the
    // superseded-verdict counter below, which is exactly its harm: the reported
    // reason depends on which instantiation was solved first.
    size_t unwind = 4;
    const std::string uw = options.get_option("unwind");
    if (!uw.empty())
    {
      const long v = std::stol(uw);
      if (v > 0)
        unwind = (size_t)v;
    }
    // ---- AND THE BOUNDS ARE STILL NOT A CEILING. NOT ENFORCED BY DEFAULT ----
    //
    // `tx x unwind` was the second attempt and it is also wrong, which is what
    // decided this. MEASURED on
    // regression/esbmc-solidity/solidity_path_cov_residual_unit_call_obstacle
    // at `--solidity-max-tx 1 --unwind 1`, i.e. a derived ceiling of 1: the key
    // `c:path:2` is solved TWICE and the run is legitimate. `c` is a public
    // unit that units `a` and `b` also call internally, and the depth bound
    // leaves those calls unexpanded, so `c`'s body is instantiated once as its
    // own dispatched entry and once inside each caller. That is the recorded
    // unit-body double identity (notes/coverage/D33), it is not a defect of
    // this run, and the count it produces is NOT any of the run's bounds -- it
    // is a property of the call graph and of which calls the depth bound
    // expanded.
    //
    // So a sound ceiling is not derivable from tx and unwind, and a check that
    // aborts on a derived one turns two green regressions red. It is therefore
    // NOT enforced unless the caller states a ceiling explicitly with
    // --path-cov-max-claim-solves: an assertion the caller owns, on a
    // configuration the caller knows the shape of. The derived number is still
    // computed and printed, as context for reading the multiplicity, and is
    // labelled as not enforced so it cannot be mistaken for a bound that held.
    size_t allowed = 0; // 0 = measure, do not refuse
    const size_t derived_ceiling = tx * unwind;
    std::string origin =
      "this run explores " + std::to_string(tx) + " transaction(s) at unwind " +
      std::to_string(unwind) +
      ", but neither bounds how often one assert instruction is instantiated: "
      "a public unit called internally by another unit is instantiated once "
      "per caller as well";
    const std::string cap = options.get_option("path-cov-max-claim-solves");
    if (!cap.empty())
    {
      allowed = (size_t)std::stoul(cap);
      origin = "--path-cov-max-claim-solves set the ceiling to " +
               std::to_string(allowed) +
               "; nothing is enforced without it, because tx x unwind (" +
               std::to_string(derived_ceiling) +
               " here) is not a bound on instantiations";
      log_status(
        "--path-cov-max-claim-solves {}: one claim key may be decided at most "
        "{} time(s) this run, and the run is REFUSED beyond that. Without this "
        "option nothing is enforced: tx {} x unwind {} = {} is context, not a "
        "bound -- a public unit called internally by another unit is "
        "instantiated once per caller too",
        allowed,
        allowed,
        tx,
        unwind,
        derived_ceiling);
    }
    path_cov_allowed_solves = allowed;
    path_cov_allowed_solves_origin = origin;
  }

  // Reorder so user-source claims solve before c2goto/library claims. Walk
  // SSA_steps once, mapping each assertion's 1-based index to a priority.
  // Library paths contain "c2goto/library" or "/library/"; anything else (the
  // user's input source file) is user-side. Stable sort preserves intra-bucket
  // order, keeping CE numbering deterministic.
  //
  // Solidity complete-path coverage adds two families of user-side goals:
  //   * "<unit>:path:<enc>" complete path claims, which carry the CE payload
  //     VeriPUT consumes.
  //   * "<unit>:probe:branch:..." exit-latched branch probes, which are useful
  //     refuters but do not identify a complete path.
  //
  // Keep complete path claims first. On large units the probe grid can be
  // thousands of claims; solving it before any path claim turns a generation
  // run into a probe census with no usable path witness.
  if (remaining_claims > 0)
  {
    std::vector<unsigned> priority(remaining_claims + 1, 3);
    size_t counter = 0;
    for (auto const &step : eq.SSA_steps)
    {
      if (!step.is_assert())
        continue;
      ++counter;
      if (counter > remaining_claims)
        break;
      const std::string file = step.source.pc->location.get_file().as_string();
      const bool is_library =
        file.find("c2goto/library") != std::string::npos ||
        file.find("/library/") != std::string::npos;
      if (is_library)
        priority[counter] = 3;
      else if (
        is_path_cov && step.comment.find(":path:") != std::string::npos &&
        step.comment.find(":probe:") == std::string::npos)
        priority[counter] = 0;
      else if (is_path_cov && step.comment.find(":probe:") != std::string::npos)
        priority[counter] = 2;
      else
        priority[counter] = 1;
    }
    std::stable_sort(jobs.begin(), jobs.end(), [&priority](size_t a, size_t b) {
      return priority[a] < priority[b];
    });
  }

  /* This is a JOB that will:
   * 1. Generate a solver instance for a specific claim (@parameter i)
   * 2. Solve the instance
   * 3. Generate a Counter-Example (or Witness)
   *
   * This job also affects the environment by using:
   * - &ce_counter: for generating the Counter Example file name
   * - &final_result: if the current instance is SAT, then we known that the current k contains a bug
   *
   * Finally, this function is affected by the "multi-fail-fast" option, which makes this instance stop
   * if final_result is set to SAT
   */
  // FAULT INJECTION, shipped rather than kept in a throwaway build.
  //
  // The three mechanisms this file now carries -- the mid-solve payload
  // publish, the PARTIAL report on the exception path, and the signal arm --
  // all only fire on a run that does NOT reach a clean exit. A regression that
  // cannot produce such a run cannot pin any of them, and `test.desc` describes
  // exactly one invocation with no environment of its own and with --timeout /
  // --memlimit stripped by the harness. So the fault has to be reachable from
  // the command line or it is not testable at all, and an untested rescue path
  // is the shape this project has already shipped twice (a function written and
  // never called; a guard that was always true).
  //
  // Both are 0 (off) unless asked for, and both are refused outside
  // --solidity-path-coverage so no ordinary run can trip over them.
  const size_t fault_after =
    is_path_cov && !options.get_option("path-cov-fault-after").empty()
      ? (size_t)std::stoul(options.get_option("path-cov-fault-after"))
      : 0;
  const size_t fault_sigterm =
    is_path_cov && !options.get_option("path-cov-fault-sigterm").empty()
      ? (size_t)std::stoul(options.get_option("path-cov-fault-sigterm"))
      : 0;
  // Throws from INSIDE the counterexample harvest -- after the verdict is
  // recorded in claim_outcome and before the claim signature reaches
  // reached_claims. The two records of one refutation are written at different
  // times and everything between them can throw, so that window needs a fault
  // of its own: `fault_after` fires at the START of a job, by which point every
  // earlier claim has completed all of its side effects, and can therefore
  // never produce the state P16_Mapping produced.
  const size_t fault_mid_witness =
    is_path_cov && !options.get_option("path-cov-fault-mid-witness").empty()
      ? (size_t)std::stoul(options.get_option("path-cov-fault-mid-witness"))
      : 0;

  auto job_function = [this,
                       &eq,
                       &ce_counter,
                       &decided_claims,
                       &solver_error_seen,
                       &smtlib_seen,
                       &remaining_claims,
                       &fault_after,
                       &fault_sigterm,
                       &fault_mid_witness,
                       &final_result,
                       &result_mutex,
                       &summary,
                       &reached_claims,
                       &reached_mul_claims,
                       &reached_claims_mutex,
                       &reached_mul_claims_mutex,
                       &is_assert_cov,
                       &is_cond_cov,
                       &is_vb,
                       &is_branch_cov,
                       &is_branch_func_cov,
                       &is_k_path_cov,
                       &is_path_cov,
                       &is_keep_verified,
                       &is_fail_fast,
                       &fail_fast_limit,
                       &fail_fast_cnt,
                       &bs,
                       &fc,
                       &is,
                       &is_color,
                       &YELLOW,
                       &runtime_solver](const size_t &i) {
    // Fault injection (see the two constants above). Checked BEFORE this job
    // does anything, so the N claims already decided have completed every
    // side effect they own -- including the incremental covered-set publish,
    // which is the thing the step-1 regression is asserting survived.
    if (fault_after && decided_claims.load() >= fault_after)
    {
      log_error(
        "--path-cov-fault-after {}: injecting std::bad_alloc after {} decided "
        "claim(s) (fault injection; this is not a real allocation failure)",
        fault_after,
        decided_claims.load());
      throw std::bad_alloc();
    }
    if (fault_sigterm && decided_claims.load() >= fault_sigterm)
    {
      log_error(
        "--path-cov-fault-sigterm {}: raising SIGTERM after {} decided "
        "claim(s) (fault injection; this is not a real external kill)",
        fault_sigterm,
        decided_claims.load());
      raise(SIGTERM);
    }

    //"multi-fail-fast n": stop after first n SATs found.
    if (is_fail_fast && fail_fast_cnt >= fail_fast_limit)
      return;
    if (solver_error_seen.load(std::memory_order_relaxed))
      return;

    // Since this is just a copy, we probably don't need a lock
    symex_target_equationt local_eq = eq;

    // Set up the current claim and disable slice info output.
    // `is_goto_cov` flips claim_slicer's `claim_msg` source: in goto-cov
    // modes the slicer reads the comment (the original witness/guard
    // text we stored in insert_assert); otherwise it reads the negated
    // assertion expression. k-path goals are stored the same way as
    // branch / condition goals, so they must be in this disjunction —
    // otherwise the claim_sig built at line ~1751 disagrees with the
    // form in goto_coveraget::all_claims and every JSON entry shows up
    // as uncovered even when reached_claims has the matching reached
    // signature (PR #4330 review).
    bool is_goto_cov = is_assert_cov || is_cond_cov || is_branch_cov ||
                       is_branch_func_cov || is_k_path_cov || is_path_cov;
    claim_slicer claim(i, false, is_goto_cov, ns);
    claim.run(local_eq.SSA_steps);

    // Drop claims that verified to be failed
    // we use the "comment + location" to distinguish each claim
    // to avoid double verifying the claims that are already verified
    //! This algo is unsound, need a better signature to distinguish claims
    bool is_verified = false;
    std::string claim_sig = claim.claim_msg + "\t" + claim.claim_loc;
    const auto probe_claim_it = goto_coveraget::path_probe_claims.find(
      {claim.claim_msg, claim.claim_loc});
    const bool is_probe_claim =
      probe_claim_it != goto_coveraget::path_probe_claims.end();
    if (is_assert_cov)
    {
      // C++20 reached_mul_claims.contains
      std::lock_guard lock(reached_mul_claims_mutex);
      is_verified = reached_mul_claims.count(claim_sig) ? true : false;
    }
    else
    {
      // THE LOOKUP KEY MUST BE THE ONE THE INSERT USES. The insert below
      // discriminates -- `claim_sig` (msg + "\t" + loc) when `is_goto_cov`,
      // `claim_cstr` (msg + " at " + loc) otherwise -- and this lookup did not.
      // So under coverage it asked for a spelling that is never stored,
      // `is_verified` was ALWAYS false, and the skip a few lines down was dead
      // code: every symex instantiation of one instrumented assert was solved
      // again from scratch.
      //
      // MEASURED, EscrowDst.withdraw: 5 distinct claim keys, 425 VCCs, ~85
      // solves per path. All four obtainable witnesses were in hand after the
      // first 46 solves; the remaining 379 would have re-derived the same four
      // and the process ran out of memory at 8 GiB first. Same shape on
      // st1inch `setFeeReceiver` at 2x (10 VCCs for 5 paths). The tool's own
      // `Verdicts Preserved` line has been reporting this all along -- its
      // header calls a non-zero value "itself a defect (duplicate
      // instrumentation)" -- and nothing acted on it.
      //
      // The premise was checked before the change rather than after: every
      // repeated `Solving claim` line for one path is BYTE-IDENTICAL across all
      // its solves, so a lookup on `claim_sig` really does match. If they had
      // differed in their location suffix the repair would have fired zero
      // times while costing a string build per job
      // (notes/coverage/scripts/claim_key_identity.py is that check).
      //
      // SOUND FOR COVERAGE because `reached_claims` only ever receives REFUTED
      // keys (the insert sits inside the P_SATISFIABLE arm), and for coverage F
      // is monotone: once a path has a witness it is covered, and further
      // witnesses of the same path are `--all-witnesses` material, which is
      // enumerated INSIDE one solve, not across repeats. No 'P'/'U'/'B' verdict
      // can reach this skip.
      //
      // SCOPED TO PATH COVERAGE ON PURPOSE. Repairing it for `is_goto_cov`
      // would newly enable skipping under branch/condition/k-path coverage as
      // well, and the branch-coverage dataset this project compares against is
      // LOCKED. Moving its numbers as a side effect of a path-coverage fix is
      // precisely the kind of change that cannot be attributed afterwards.
      std::lock_guard lock(reached_claims_mutex);
      is_verified =
        reached_claims.count(is_path_cov ? claim_sig : claim.claim_cstr)
          ? true
          : false;
    }
    if (is_assert_cov && is_verified)
    {
      // insert to the multiset before skipping the verification process
      std::lock_guard lock(reached_mul_claims_mutex);
      reached_mul_claims.emplace(claim_sig);
    }

    // skip if we have already verified
    if (is_verified && !is_keep_verified)
    {
      ++summary.skipped_properties;
      return;
    }

    // Solidity complete-path coverage consumes overflow/div-by-zero assertions
    // as constraints for --path-cov-arith-resolve on a path claim. In
    // result-only driver runs, solving those safety assertions as independent
    // goals can spend the whole unit budget before any complete-path claim is
    // witnessed, and their counterexamples do not carry path_id/path_function
    // metadata the VeriPUT stage-2 salvage can use. Full reporting/testcase
    // runs keep the historical behaviour because the safety claim itself may
    // be user-visible there. Probe claims are still solved: they are the
    // explicit --path-cov-probe witness source.
    //
    // CERTIFICATION IS THE EXCEPTION, for the checks it was asked to carry.
    // In --path-cov-certify mode an overflow/division claim is not a side
    // goal: a refuted one IS the `RESULT: UNSAFE` verdict (the only way a
    // checked-arithmetic revert inside the box can be reported), and the
    // driver always passes --result-only. MEASURED on motivation_FeeVault
    // (discountBps in [1, 65535], feeBps - discountBps): with the skip in
    // force the per-claim pass never solved the overflow claims, the forward
    // condition then failed on them at every k, the run idled to
    // --max-k-step and reported UNDECIDED; the same query without
    // --result-only reported UNSAFE in one k-step with the refuting witness.
    const bool certify_safety_claim =
      goto_coveraget::path_cov_certify_mode &&
      (claim.claim_property == "overflow" ||
       claim.claim_property == "division-by-zero");
    if (
      is_path_cov && options.get_bool_option("result-only") &&
      !is_probe_claim && !certify_safety_claim &&
      claim.claim_property != "instrumented assertion")
    {
      ++summary.skipped_properties;
      return;
    }

    // Slice
    if (!options.get_bool_option("no-slice"))
    {
      symex_slicet slicer(options);
      slicer.run(local_eq.SSA_steps);
    }

    if (options.get_bool_option("ssa-features-dump"))
    {
      ssa_features features;
      features.run(local_eq.SSA_steps);
    }

    // Initialize a solver
    smt_convt *solver_ptr = &runtime_solver;
    std::unique_ptr<smt_convt> new_solver;
    if (!options.get_bool_option("smt-during-symex"))
    {
      new_solver = std::unique_ptr<smt_convt>(create_solver("", ns, options));
      solver_ptr = new_solver.get();
    }

    // Store solver name initially but not again
    std::call_once(summary.solver_name_flag, [&]() {
      summary.solver_name = solver_ptr->solver_text();
    });
    // WHAT ACTUALLY ENFORCED THE BUDGET, recorded from the solver rather than
    // assumed from the flag. A backend with no per-check limit records nothing,
    // and the empty string is turned into an explicit refusal here instead of
    // being left to read as "enforced": a report carrying `claim_timeout_s: 120`
    // for a run nothing bounded is the exact shape of a guard that never fires
    // while everything looks fine.
    if (is_path_cov && goto_coveraget::claim_budget_seconds > 0)
    {
      const std::string mech = smt_timeout_mechanism();
      goto_coveraget::claim_budget_mechanism =
        mech.empty() ? ("NOT ENFORCED: backend '" + solver_ptr->solver_text() +
                        "' has no per-query time limit, so the budget was "
                        "requested and could not be applied")
                     : mech;
    }
    // In coverage mode, only report instrumented coverage claims
    bool is_cov_silent =
      is_goto_cov && claim.claim_property != "instrumented assertion";

    if (!is_cov_silent)
      log_status(
        "Solving claim '{}' with solver {}",
        prettify_solidity_expr(claim.claim_cstr),
        solver_ptr->solver_text());

    // Save current instance with timing
    fine_timet solve_start = current_time();
    smt_convt::resultt solver_result =
      run_decision_procedure(*solver_ptr, local_eq);
    fine_timet solve_stop = current_time();

    // Show colored result after solving
    const std::string GREEN = is_color ? "\033[32m" : "";
    const std::string RED = is_color ? "\033[31m" : "";
    const std::string RESET = is_color ? "\033[0m" : "";

    if (!is_cov_silent)
    {
      if (solver_result == smt_convt::P_UNSATISFIABLE)
      {
        // Claim passed - show in green
        log_status(
          "{}✓ PASSED{}: '{}'",
          GREEN,
          RESET,
          prettify_solidity_expr(claim.claim_cstr));
      }
      else if (solver_result == smt_convt::P_SATISFIABLE)
      {
        if (is)
          // Inductive step could not prove this claim - show in yellow
          log_status(
            "{}? UNKNOWN{}: '{}'",
            YELLOW,
            RESET,
            prettify_solidity_expr(claim.claim_cstr));
        else
          // Claim failed (counterexample found) - show in red
          log_status(
            "{}✗ FAILED{}: '{}'",
            RED,
            RESET,
            prettify_solidity_expr(claim.claim_cstr));
      }
    }

    // ---- DID THIS CLAIM BLOW ITS BUDGET? ----
    //
    // The budget is enforced by the SOLVER (a native per-check-sat limit; see
    // solvers/solve.h), and every backend folds "unknown" into P_ERROR --
    // cvc5_conv.cpp maps isUnknown() straight onto it, and smt_convt::resultt
    // has no P_UNKNOWN to fold into instead. So the result alone cannot tell an
    // abandoned query from a genuine solver failure, and the wall clock is what
    // separates them.
    //
    // THE TEST IS UNCONDITIONAL ON WHAT THE SOLVER SAID BEYOND THAT: a SAT or
    // UNSAT answer is kept even if it arrived late, because the solver ANSWERED
    // and an answer is not something to throw away over a stopwatch. Only a
    // non-answer that took at least the budget is filed as abandoned.
    //
    // The 100 ms slack is for clock granularity and for the difference between
    // when the solver's own timer starts and when this one does; without it a
    // limit that fires at exactly the budget can measure as a hair under it and
    // be mislabelled `solver-unknown`, which is the quieter and more misleading
    // of the two errors.
    const bool budget_on = goto_coveraget::claim_budget_seconds > 0;
    const bool answered = solver_result == smt_convt::P_SATISFIABLE ||
                          solver_result == smt_convt::P_UNSATISFIABLE;
    // ---- THE ELAPSED TIME IS ALREADY IN MILLISECONDS ----
    //
    // `current_time()` returns `tv.tv_usec / 1000 + tv.tv_sec * 1000`
    // (util/time_stopping.cpp), i.e. MILLISECONDS. The old test multiplied that
    // difference by 1000 before comparing it against `budget_seconds * 1000`,
    // so it expanded to
    //
    //     ms * 1000 + 100 >= 120 * 1000      <=>      ms >= 119.9
    //
    // and fired at 120 MILLISECONDS instead of 120 seconds -- a factor of 1000.
    // Every unanswered query slower than about a tenth of a second was filed
    // `claim-budget-exceeded`, and the budget never actually gave a query the
    // time it advertised.
    //
    // MEASURED on st1inch `--focus-function setFeeReceiver --z3
    // --tuple-node-flattener`, whose own log contradicts itself on consecutive
    // lines:
    //
    //     Runtime decision procedure: 5.021s
    //     WARNING: claim budget exceeded (120s): ABANDONING
    //              'setFeeReceiver:path:15 at' ...
    //
    // Five seconds reported as over a two-minute budget. The consequence is not
    // only a wrong number: the five paths were filed under
    // `claim-budget-exceeded` -- "we abandoned it, nothing is known" -- when the
    // solver had ANSWERED, with `unknown`, in seconds. That is the
    // `solver-unknown` cell, and the two call for different next actions
    // (raise the budget vs change the encoding). The token that exists to stop
    // a U from absorbing an unexplained remainder was itself absorbing one.
    //
    // The 100 ms slack keeps its stated meaning now that both sides are in the
    // same unit: it covers clock granularity and the gap between the solver's
    // own timer starting and this one.
    const bool over_budget =
      is_path_cov && budget_on && !answered &&
      (double)(solve_stop - solve_start) + 100.0 >=
        (double)goto_coveraget::claim_budget_seconds * 1000.0;
    if (over_budget)
    {
      goto_coveraget::claim_budget_exceeded.fetch_add(
        1, std::memory_order_relaxed);
      log_warning(
        "claim budget exceeded ({}s): ABANDONING '{}' and continuing to the "
        "next claim. Nothing is known about this path -- it is reported U with "
        "reason 'claim-budget-exceeded', which is NOT 'solver-unknown' (the "
        "solver answering 'I do not know') and NOT 'bounded-holds' (it "
        "answering 'no witness'). Raise --path-cov-claim-timeout to spend more "
        "on it",
        goto_coveraget::claim_budget_seconds,
        prettify_solidity_expr(claim.claim_cstr));
    }

    // Tri-state ledger for Solidity complete-path coverage: record THIS
    // claim's verdict so the report can tell "proven at this bound" apart
    // from "could not decide" (reached_claims records only the refuted
    // ones, so both non-refuted cases would otherwise collapse into a
    // single indistinguishable "uncovered"). An inductive-step SAT means
    // "cannot prove", not a counterexample, so it maps to 'U'.
    if (is_path_cov && !is_probe_claim)
    {
      char verdict;
      if (over_budget)
        // Its own verdict value, not a shade of 'U'. path_u_reason_token's
        // `default` arm returns "" so the caller hard-fails on an unknown
        // verdict, which is exactly why a new one gets its own `case` there
        // rather than being folded into an existing letter.
        verdict = 'B';
      else if (solver_result == smt_convt::P_SATISFIABLE)
        verdict = is ? 'U' : 'F';
      else if (solver_result == smt_convt::P_UNSATISFIABLE)
        verdict = 'P';
      else
        verdict = 'U';
      std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
      // ---- A DECIDED VERDICT IS NEVER REPLACED BY A NON-DECISION ----
      //
      // This used to protect 'F' alone -- "a witness stays valid, so a later
      // phase's failure to reprove must never downgrade it" -- and left every
      // other cell last-writer-wins. 'P' needs exactly the same protection, and
      // for exactly the same reason: it is a DECISION (the solver answered
      // UNSAT), while 'U' and 'B' are the absence of one.
      //
      // MEASURED on st1inch `--focus-function setFeeReceiver --z3
      // --tuple-node-flattener`, one run, and it is not a corner case. The run
      // generates 10 VCCs for 5 paths and solves EACH PATH CLAIM TWICE under the
      // SAME (comment, location) key -- `paths_total` is 5, so `all_claims`, a
      // std::set of that pair, holds five:
      //
      //     path:13   0.010s   ✓ PASSED        then again  2.246s   no verdict
      //     path:12   0.010s   ✓ PASSED        then again  2.054s   no verdict
      //
      // Both were PROVEN in ten milliseconds and both are reported
      // `solver-unknown`. So an unknown share of st1inch's 59 `solver-unknown`
      // -- the number the branch gate turns into a 0 that reads as zero
      // coverage -- is not the solver failing to decide, it is this line
      // discarding a decision it had.
      //
      // 'P' -> 'F' IS STILL ALLOWED, and must be: a claim that held at one
      // exploration can be refuted at a deeper one, and that refutation is a
      // later, stronger DECISION. What is refused is 'P' -> 'U' and 'P' -> 'B',
      // which replace an answer with the absence of an answer.
      //
      // The duplicate solve itself is a SECOND defect and is not fixed here:
      // the same claim key is instrumented at more than one site, so half the
      // solving time on this unit buys nothing. Fixing the bookkeeping makes
      // the numbers right; fixing the duplication also makes them cheaper. They
      // are separate, and folding them into one change would leave neither
      // testable on its own.
      // ---- HOW MANY TIMES HAS THIS KEY BEEN DECIDED? ----
      //
      // Counted before the verdict is merged, so the count is of SOLVES and not
      // of changes: a key solved three times with the same answer each time is
      // still duplicate work, and a rule that only noticed disagreements would
      // report it as clean.
      //
      // The ceiling is the transaction bound: the transaction body is emitted
      // once per transaction, so one assert instruction is reached at most once
      // per transaction. Exceeding it means the claim is instrumented at more
      // than one site -- the duplicate instrumentation the comment below calls
      // a second defect -- and that is fatal rather than counted, because every
      // number this run publishes about that path is then the result of several
      // independent chances to witness it.
      {
        std::lock_guard lk(path_cov_solve_count_mutex);
        const size_t n = ++path_cov_solve_count[claim_sig];
        if (n > 1)
          path_cov_extra_solves.fetch_add(1, std::memory_order_relaxed);
        if (n > path_cov_max_solves)
        {
          path_cov_max_solves = n;
          path_cov_max_solves_key = claim_sig;
        }
        if (path_cov_allowed_solves > 0 && n > path_cov_allowed_solves)
        {
          log_error(
            "--solidity-path-coverage: INTERNAL DEFECT — claim key '{}' has "
            "now been handed to the solver {} times, and at most {} is "
            "allowed ({}). One assert instruction is reached at most once "
            "per transaction, so a higher count means the SAME claim is "
            "instrumented at more than one site. That is not a cost problem: "
            "the path gets several independent chances to be witnessed and "
            "every figure published about it is the result of all of them. "
            "Refusing rather than reporting, because a coverage number "
            "computed from duplicated claims cannot be corrected afterwards",
            prettify_solidity_expr(claim_sig),
            n,
            path_cov_allowed_solves,
            path_cov_allowed_solves_origin);
          abort();
        }
      }

      auto it_o = goto_coveraget::claim_outcome.find(claim_sig);
      if (it_o == goto_coveraget::claim_outcome.end())
        goto_coveraget::claim_outcome.emplace(claim_sig, verdict);
      else if (it_o->second == 'F')
      {
        // final, keep
      }
      else if (it_o->second == 'P' && verdict != 'F')
      {
        // A decision is already recorded and the new outcome is not one.
        // Counted rather than kept silently: "the guard fired" and "the guard
        // was never needed" must be distinguishable, and on this corpus the
        // count is the size of a number already published.
        goto_coveraget::verdicts_preserved.fetch_add(
          1, std::memory_order_relaxed);
      }
      else
      {
        // A DECIDED verdict is being replaced by a DIFFERENT decided one -- in
        // practice only P -> F, the legitimate upgrade: this path holds in one
        // transaction and is refuted in a later one, and the path's answer is
        // the disjunction. Counted here because it is precisely the event
        // `Verdicts Preserved` cannot observe (that counter fires only when the
        // later solve returns NO verdict), and because a silent overwrite and a
        // reasoned upgrade look identical from outside.
        if (
          (it_o->second == 'P' || it_o->second == 'F') &&
          verdict != it_o->second)
          path_cov_verdict_upgrades.fetch_add(1, std::memory_order_relaxed);
        it_o->second = verdict;
      }

      if (
        goto_coveraget::path_cov_certify_mode && verdict == 'F' &&
        (claim.claim_property == "overflow" ||
         claim.claim_property == "division-by-zero"))
        goto_coveraget::path_cov_certify_safety_refutations.emplace(
          claim.claim_msg, claim.claim_loc);

      goto_coveraget::publish_path_cov_assertion_partial_row_locked(claim_sig);
    }
    else if (is_probe_claim)
    {
      const char verdict =
        over_budget
          ? 'B'
          : (solver_result == smt_convt::P_SATISFIABLE
               ? (is ? 'U' : 'F')
               : (solver_result == smt_convt::P_UNSATISFIABLE ? 'P' : 'U'));
      std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
      auto [it, inserted] =
        goto_coveraget::path_probe_outcome.emplace(claim_sig, verdict);
      if (!inserted && it->second != 'F')
      {
        if (verdict == 'F' || (it->second != 'P' && verdict == 'P'))
          it->second = verdict;
      }
    }

    double solve_time_s = (solve_stop - solve_start);

    // Atomically update summary with timing and results
    double old_total_time_s = summary.total_time_s;
    double new_total_time_s;
    do
    {
      new_total_time_s = old_total_time_s + solve_time_s;
    } while (!summary.total_time_s.compare_exchange_weak(
      old_total_time_s, new_total_time_s));

    if (!answered)
    {
      summary.unknown_properties++;
      if (is_path_cov)
        goto_coveraget::path_cov_solver_inconclusive.store(
          true, std::memory_order_relaxed);
      if (solver_result == smt_convt::P_ERROR)
        solver_error_seen.store(true, std::memory_order_relaxed);
      else
        smtlib_seen.store(true, std::memory_order_relaxed);
      std::lock_guard lock(result_mutex);
      final_result = solver_result;
      return;
    }

    // This claim now has a decided SAT/UNSAT verdict. P_ERROR/P_SMTLIB return
    // above, so fault-injection and partial-report counts cannot call them
    // decided work.
    const size_t decided_now = ++decided_claims;
    goto_coveraget::live_decided.store(decided_now, std::memory_order_relaxed);

    if (solver_result == smt_convt::P_SATISFIABLE)
    {
      if (is)
        summary.unknown_properties++;
      else
        summary.failed_properties++;
    }
    else if (solver_result == smt_convt::P_UNSATISFIABLE)
      summary.passed_properties++;

    // If an assertion instance is verified to be violated
    if (solver_result == smt_convt::P_SATISFIABLE)
    {
      // Inductive step SAT means unprovable (UNKNOWN), not a real
      // counterexample — skip trace generation and return early.
      if (is)
      {
        std::lock_guard lock(result_mutex);
        final_result = solver_result;
        return;
      }

      // ---- THE CHAIN REJECTS THIS COUNTEREXAMPLE: RE-SOLVE ONCE ----
      //
      // MEASURED, and it is why this exists: `D10_WrapNotPanic.add` is
      // `require(amt > 0); bal += amt;` and the witness comes back
      // `amt = 2^256-1`, `bal: 500 -> 499`, classified `exit_kind: normal`, and
      // emitted as a bare call asserting a normal exit. `forge test` answers
      // `[FAIL: panic: arithmetic underflow or overflow (0x11)]`. The `require`
      // is load-bearing -- without it the solver picks 0 and the test is green
      // -- so the defect is not "the solver likes extremes": NOTHING in the
      // formula distinguishes a wrapping member of the path's domain from a
      // non-wrapping one, and once the cheap value is excluded the choice is
      // unconstrained.
      //
      // WHY THIS ORDER (solve as before, then re-solve only a WITNESSED path).
      // The alternative -- assume no-overflow on the FIRST solve -- pays one
      // extra query on every path that comes back UNSAT, which on st1inch is
      // the 69 `bounded-holds`. Witnessed paths are single digits per unit, so
      // paying there is the cheap side. It also needs no way to ask "did this
      // model wrap": preferring a non-wrapping witness whenever one exists is
      // strictly stronger than detecting the wrap first, and it removes the
      // only step that would have re-derived arithmetic OUTSIDE the model --
      // a second implementation of an arithmetic the model already performs is
      // free to disagree with it, which is the geometric ladder's own wrap
      // defect.
      //
      // THE PATH CONSTRAINT IS CARRIED FOR FREE, and that closes the hole in
      // the original proposal. The query IS this path's claim
      // `assert(!(tr == enc && cnt == depth))`, so every SAT model is already
      // on this path and adding a conjunct cannot move it to another one. The
      // hole was real, but it belonged to the `assume(no overflow);
      // assert(false)` FORM of the re-solve, not to this architecture.
      // Consequently the UNSAT of this query is exactly the proof that the
      // path is reachable ONLY by overflowing -- free, from the same query.
      std::unique_ptr<smt_convt> arith_solver;
      std::unique_ptr<symex_target_equationt> arith_eq;
      if (
        is_path_cov && !is_probe_claim &&
        options.get_bool_option("path-cov-arith-resolve") &&
        !options.get_bool_option("smt-during-symex") &&
        claim.claim_property == "instrumented assertion")
      {
        arith_eq = std::make_unique<symex_target_equationt>(eq);
        claim_slicer rclaim(i, false, is_goto_cov, ns);
        rclaim.run(arith_eq->SSA_steps);

        // WHICH STEPS, decided on the MACHINE FIELD goto_check sets, never on
        // the comment prose. `add_guarded_claim` stamps
        // `location.property()`, and two different producers share the value
        // `overflow` -- `overflow_check` writes "arithmetic overflow on ..."
        // while `cast_overflow_check` writes "Narrowing cast overflow on ..."
        // and is ON BY DEFAULT for Solidity. A text match would have to know
        // both sentences and would silently miss the next one.
        //
        // THE GUARD HAS TO BE FOLDED IN BY HAND. `convert_internal_step`
        // (symex_target_equation.cpp) uses an ASSUME's `cond` and IGNORES its
        // `guard` -- assumes join the assumption chain unconditionally. So
        // converting the assert as-is would assume "no overflow" even on symex
        // branches where the operation never executes, which can exclude a
        // legitimate witness of this path and report a FALSE
        // "reachable only by overflowing". `implies(guard, cond)` is what
        // makes the assumption say what the assert said.
        size_t n_arith = 0;
        for (auto &st : arith_eq->SSA_steps)
        {
          if (!st.is_assert() || !st.source.is_set)
            continue;
          const std::string prop =
            st.source.pc->location.property().as_string();
          if (prop != "overflow" && prop != "division-by-zero")
            continue;
          st.type = goto_trace_stept::ASSUME;
          st.cond =
            is_nil_expr(st.guard) ? st.cond : implies2tc(st.guard, st.cond);
          st.ignore = false;
          ++n_arith;
        }
        goto_coveraget::arith_conditions_seen.fetch_add(
          n_arith, std::memory_order_relaxed);

        // ⚠ WHAT THIS DELIBERATELY DOES NOT RECORD, and what it costs later.
        //
        // Every matching condition of BOTH families is assumed together and ONE
        // query is issued, so an UNSAT is a statement about the CONJUNCTION:
        // "this path, with every overflow AND every division-by-zero condition
        // holding, is unsatisfiable". It does not say which family -- let alone
        // which operation -- is responsible, and only the path key is stored
        // below (`arith_revert_only_paths` is a bare set, unlike
        // `named_obstacle_paths`, which is a map to a reason).
        //
        // That is enough to REFUSE a case, which is all this does today. It is
        // NOT enough to RENDER one: `vm.expectRevert(stdError.arithmeticError)`
        // asserts Panic 0x11 exactly, and forge matches the revert data byte for
        // byte, so emitting the wrong code is a RED test. Whoever adds the
        // rendering needs the family, and the cheap way to get it is to record
        // the SET of `property()` values actually converted on THIS claim: when
        // that set is a single family the UNSAT is attributable to it by
        // construction, with no extra query. When it holds both, one extra
        // solve per family decides it, and when both of those are UNSAT the
        // firing Panic depends on execution ORDER, which no verdict supplies --
        // there the honest rendering is a bare `vm.expectRevert()`, which
        // asserts exactly what was proved and nothing more.
        //
        // `unchecked { }` is NOT a hazard here, and that is READ rather than
        // assumed: goto_check.cpp's `overflow_check` returns early on
        // `loc.get("#sol_unchecked") == "1"`, so no overflow claim is emitted
        // inside an unchecked block and there is nothing for this loop to
        // convert. P18_Unchecked measured the same thing from the other side --
        // an unchecked block and a normal one produce a byte-identical model.

        // Nothing to assume => nothing to re-solve. Not an error: a unit with
        // no checked operation on this path is the common case, and paying a
        // query to learn that would make the mechanism cost something on every
        // contract instead of on the ones it helps.
        if (n_arith > 0)
        {
          // The conversion must happen BEFORE this slice. symex_slicet::run
          // skips `ignore`d steps outright, so an arithmetic condition left as
          // an ignored ASSERT contributes no symbols to `depends` and the
          // assignments defining its OPERANDS are then free to be sliced away
          // -- after which asserting it would reference symbols the formula no
          // longer constrains and the solver would satisfy it by choosing
          // them. An answer about nothing.
          if (!options.get_bool_option("no-slice"))
          {
            symex_slicet rslicer(options);
            rslicer.run(arith_eq->SSA_steps);
          }
          arith_solver =
            std::unique_ptr<smt_convt>(create_solver("", ns, options));
          const fine_timet ra_start = current_time();
          const smt_convt::resultt ra =
            run_decision_procedure(*arith_solver, *arith_eq);
          const fine_timet ra_stop = current_time();
          goto_coveraget::arith_resolve_queries.fetch_add(
            1, std::memory_order_relaxed);
          goto_coveraget::arith_resolve_ms.fetch_add(
            (size_t)(ra_stop - ra_start), std::memory_order_relaxed);

          if (ra == smt_convt::P_SATISFIABLE)
          {
            // A witness of THIS path that satisfies every enabled arithmetic
            // check. Everything downstream -- the trace, the payload harvest,
            // the Foundry case -- is built from this model instead.
            // SWAP the step lists rather than assign the equation:
            // symex_target_equationt holds `const namespacet &`, so its copy
            // ASSIGNMENT is implicitly deleted (copy construction, used above,
            // is fine). Both equations name the same namespace, so exchanging
            // the only member that differs is exactly the intended effect and
            // is O(1).
            local_eq.SSA_steps.swap(arith_eq->SSA_steps);
            solver_ptr = arith_solver.get();
            goto_coveraget::arith_resolve_replaced.fetch_add(
              1, std::memory_order_relaxed);
            log_status(
              "--path-cov-arith-resolve: '{}' had a counterexample the chain "
              "rejects; re-solved under {} arithmetic condition(s) and took "
              "the non-wrapping witness instead",
              prettify_solidity_expr(claim.claim_msg),
              n_arith);
          }
          else if (ra == smt_convt::P_UNSATISFIABLE)
          {
            // NOT a failure of the re-solve. It is a PROOF: no input reaches
            // this path without violating a checked operation, so on chain the
            // path is reachable only through a Panic revert. Its own cell, and
            // never folded into U -- a U says "we could not decide" and this
            // was decided.
            {
              std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
              goto_coveraget::arith_revert_only_paths.emplace(
                claim.claim_msg, claim.claim_loc);
            }
            log_status(
              "--path-cov-arith-resolve: '{}' is reachable ONLY by violating a "
              "checked arithmetic operation -- the re-solve under {} "
              "condition(s) is UNSAT, which PROVES it. On chain this path is "
              "reached through a Panic revert, so its witness is kept but the "
              "path must not be emitted as a normal-exit test",
              prettify_solidity_expr(claim.claim_msg),
              n_arith);
          }
          else
            log_warning(
              "--path-cov-arith-resolve: the re-solve of '{}' returned neither "
              "sat nor unsat, so the original (possibly chain-rejected) "
              "witness is kept unchanged. This is NOT a proof that the path "
              "needs an overflow",
              prettify_solidity_expr(claim.claim_msg));
        }
      }

      // Every REFUTING witness of a certification query is minimised -- the
      // checked-arithmetic claims and the exit asserts alike; only the
      // non-vacuity witness is left as solved, because it is not a refutation
      // (it is expected to FAIL and its model is a member of the path). The
      // exit-assert case matters as much as the safety case: MEASURED (freeN),
      // the relation refuter `amount > deposits` came back with arbitrary
      // `discountBps`/`block.*`, and the driver's multi-coordinate retreat
      // pinned all of them, certifying a point where the sliver was available.
      if (
        goto_coveraget::path_cov_certify_mode &&
        solver_result == smt_convt::P_SATISFIABLE && !is_probe_claim &&
        claim.claim_msg.find("#nonvacuous") == std::string::npos &&
        (claim.claim_property == "overflow" ||
         claim.claim_property == "division-by-zero" ||
         claim.claim_msg.find(":path:") != std::string::npos))
        path_cov_minimise_certify_witness(
          local_eq, *solver_ptr, prettify_solidity_expr(claim.claim_msg));

      bool is_compact_trace = true;
      if (
        options.get_bool_option("no-slice") &&
        !options.get_bool_option("compact-trace"))
        is_compact_trace = false;
      // The path-coverage CE payload is harvested from THIS trace. A compact
      // trace drops every step flagged `hidden`, and that is exactly where the
      // EVM environment (msg./tx./block.) and the harness-side writes are
      // assigned — the report's `env` block would come back empty even though
      // the values are in the model. Building the full trace changes nothing
      // about what is solved; it only costs trace-construction time.
      if (is_path_cov && options.get_bool_option("cov-report-json"))
        is_compact_trace = false;

      // --all-witnesses: re-solve with blocking clauses on the nondet input
      // tuple to enumerate further violating inputs at the current k.
      // No re-encoding: we only push extra assertions onto the live solver.
      const bool enumerate = options.get_bool_option("all-witnesses");
      size_t max_w = 1;
      if (enumerate)
      {
        const std::string mw = options.get_option("max-witnesses");
        const int mw_val = mw.empty() ? 16 : std::stoi(mw);
        // 0 means unlimited (only meaningful with --all-witnesses).
        max_w = (mw_val == 0) ? SIZE_MAX : (size_t)mw_val;
      }

      std::vector<witness_recordt> witnesses;
      // ---- ONE CE PAYLOAD PER WITNESS, NOT ONE PER CLAIM ----
      //
      // `--all-witnesses` was fully wired for path coverage and reached every
      // consumer EXCEPT the one this project reads. Per witness the loop below
      // already emitted a `--cex-output` file, a GraphML/YAML witness, a
      // testcase XML, an HTML/JSON report, a pytest/ctest case and a FOUNDRY
      // case -- and then harvested the counterexample payload for the FIRST
      // witness only, discarding the rest. So Foundry got N tests per path and
      // cov-report.json got one set of inputs, which is exactly backwards for a
      // stage-2 ladder that consumes the report.
      //
      // The extra witnesses are also nearly free: they cost N-1 further
      // dec_solve() calls on ONE already-encoded solver instance (one push_ctx,
      // a blocking clause per witness, one pop_ctx) -- no second encoding, no
      // second symex, no second per-claim slice.
      std::vector<goto_coveraget::path_ce_t> ce_all;
      enumeration_stop_reasont stop_reason =
        enumerate ? enumeration_stop_reasont::Unsat
                  : enumeration_stop_reasont::Disabled;

      // Cache option lookups so the per-witness loop body is cheap.
      const std::string cex_output = options.get_option("cex-output");
      const std::string graphml_path =
        options.get_option("witness-output-graphml");
      const std::string yaml_path = options.get_option("witness-output-yaml");
      const bool want_graphml = !graphml_path.empty();
      const bool want_yaml = !yaml_path.empty();
      const bool want_testcase = options.get_bool_option("generate-testcase");
      const bool want_html = options.get_bool_option("generate-html-report");
      const bool want_json = options.get_bool_option("generate-json-report");
      const bool want_pytest =
        options.get_bool_option("generate-pytest-testcase");
      const bool want_ctest =
        options.get_bool_option("generate-ctest-testcase");
      const bool want_foundry =
        options.get_bool_option("generate-foundry-testcase");

      // Emit testcase metadata once per claim (not once per witness).
      if (want_testcase)
        generate_testcase_metadata();

      const bool path_probe_replayable_only =
        is_path_cov && options.get_bool_option("path-cov-probe");

      // Drive enumeration with a separate variable so the original SAT
      // outcome stays in `solver_result` for downstream bookkeeping
      // (final_result, fail-fast counter, claim cleanup).
      smt_convt::resultt enum_result = solver_result;
      bool ctx_pushed = false;
      while (enum_result == smt_convt::P_SATISFIABLE)
      {
        witness_recordt w;
        build_goto_trace(local_eq, *solver_ptr, w.trace, is_compact_trace);
        // Collecting nondet values walks every SSA step and queries the
        // solver model per nondet symbol — non-trivial on coverage runs
        // with many claims and large arrays. Skip it when we don't need
        // it: the values are only consumed by `make_blocking_expr` (only
        // when enumerating) and by the multi-witness pretty-printer
        // (only when --all-witnesses is set, i.e. enumerate==true).
        // The legacy single-witness renderer does not use them.
        if (enumerate)
        {
          size_t probe_skipped_before_model = 0;
          w.nondet_inputs = collect_nondet_values(
            local_eq,
            *solver_ptr,
            path_probe_replayable_only,
            &probe_skipped_before_model);
          if (path_probe_replayable_only)
          {
            const auto before = w.nondet_inputs.size();
            w.nondet_inputs.erase(
              std::remove_if(
                w.nondet_inputs.begin(),
                w.nondet_inputs.end(),
                [](const collected_nondet_value &v) {
                  const std::string &lhs = v.lhs_symbol_name;
                  const bool source_local =
                    lhs.rfind("sol:@", 0) == 0 &&
                    lhs.find("@F@") != std::string::npos;
                  const bool replayable_env =
                    lhs.find("@msg_sender") != std::string::npos ||
                    lhs.find("@msg_value") != std::string::npos;
                  const bool scalar = is_unsignedbv_type(v.type) ||
                                      is_signedbv_type(v.type) ||
                                      is_bool_type(v.type);
                  return !scalar || (!source_local && !replayable_env);
                }),
              w.nondet_inputs.end());
            if (is_probe_claim)
            {
              goto_coveraget::path_probe_nondets_kept.fetch_add(
                w.nondet_inputs.size(), std::memory_order_relaxed);
              goto_coveraget::path_probe_nondets_dropped.fetch_add(
                probe_skipped_before_model + before - w.nondet_inputs.size(),
                std::memory_order_relaxed);
            }
          }
        }
        w.ce_index = ce_counter++;

        // Emit machine-readable artifacts NOW, while this witness's solver
        // model is still live. After the next dec_solve(), the model is
        // either gone (UNSAT) or replaced by the next witness's values.
        if (!cex_output.empty())
        {
          std::ofstream out(fmt::format("{}-{}", w.ce_index, cex_output));
          show_goto_trace(out, ns, w.trace);
        }
        // For graphml/yaml the writer reads the path from `options`;
        // override per-witness so multiple witnesses don't overwrite the
        // same file (and so it's safe under --parallel-solving).
        if (want_graphml)
          violation_graphml_goto_trace(
            options,
            ns,
            w.trace,
            fmt::format("{}-{}", w.ce_index, graphml_path));
        if (want_yaml)
          violation_yaml_goto_trace(
            options, ns, w.trace, fmt::format("{}-{}", w.ce_index, yaml_path));
        if (want_testcase)
          generate_testcase(
            "testcase-" + std::to_string(w.ce_index) + ".xml",
            local_eq,
            *solver_ptr);
        if (want_html)
          generate_html_report(
            std::to_string(w.ce_index), ns, w.trace, options);
        if (want_json)
          generate_json_report(std::to_string(w.ce_index), ns, w.trace);
        if (want_pytest)
          pytest_gen.collect(local_eq, *solver_ptr);
        if (want_ctest)
          ctest_gen.collect(local_eq, *solver_ptr, ns);
        if (want_foundry)
        {
          // ---- A PATH PROVEN REVERT-ONLY MUST NOT BECOME A TEST ----
          //
          // goto_coverage.h states the rule for the obstacle machinery and it
          // applies verbatim here: "a marked path must not be turned into a
          // test. Marking without excluding would be worthless." Recording
          // `arith_revert_only` in the report and then handing the same
          // counterexample to the emitter would reproduce, one mechanism later,
          // exactly the defect a6ea07f2e9 fixed -- where the marking ran on
          // every path, the readers existed, and their gate (`status == U`)
          // excluded the only dangerous case (`status == F`).
          //
          // REFUSED HERE, NOT IN foundry.cpp BESIDE THE OBSTACLE REFUSAL. The
          // reason is that a REFUSAL needs no per-call state: the decision is
          // "do not hand this counterexample over", and this is the only call
          // site that feeds the coverage-mode emitter, so the test sits where
          // the proof was established a few dozen lines above.
          //
          // ⚠ AN EARLIER VERSION OF THIS COMMENT ALSO CLAIMED that putting it
          // in the emitter "would need the fact carried there through a second
          // channel". THAT WAS FALSE and is corrected rather than deleted,
          // because it would have misdirected the next reader.
          // `arith_revert_only_paths` is a static keyed by the SAME
          // (comment, location) pair as `normal_exit_paths` and
          // `named_obstacle_paths`, which foundry.cpp already reads at
          // reconstruct()'s segment site and at its refuted-claim site. There
          // is no second channel; it is the first one.
          //
          // ⚠ AND THERE IS A REAL HAZARD FOR WHOEVER DOES WIRE THE EMITTER,
          // which the obstacle precedent never had to face. Those two sets are
          // filled at INSTRUMENTATION time, single-threaded, so foundry.cpp's
          // unlocked reads of them are safe. THIS one is filled HERE, inside
          // the per-claim job loop, and `--parallel-solving` runs one thread
          // per job; reconstruct() runs unlocked on a job thread and scans
          // every guard-true assert, i.e. OTHER claims' keys, which other jobs
          // may be inserting concurrently. Any read of this set from
          // foundry.cpp must take `claim_outcome_mutex` -- the mutex the write
          // above and the read below both use.
          //
          // COVERAGE IS UNAFFECTED. The path IS witnessed and stays F -- it is
          // reachable on chain, through a Panic revert. What is refused is
          // rendering it as a bare call that ASSERTS a normal exit, which is
          // red on the unmodified contract. Rendering it correctly needs
          // vm.expectRevert(stdError.arithmeticError / divisionError) and is
          // its own change.
          // ---- AND THE REFUSAL IS DEFEATED UNLESS THIS COMES FIRST ----
          //
          // MEASURED on notes/coverage/poc/D16_OnlyByOverflow.sol, which exists
          // to exercise the proof arm. With --overflow-check on, goto_check's
          // OWN overflow claim is another job in this very loop: it is refuted
          // (an overflow IS possible), its witness loop runs, and it calls
          // foundry().collect() like any other claim. The case it reconstructs
          // is the SAME call as the path claim's, so:
          //
          //   without the flag  the two cases share a fingerprint, dedup keeps
          //                     one, and the PATH claim supplies its name --
          //                     the run reports `3 of 3 case(s) name the
          //                     obligation`;
          //   with the flag     the path claim is refused, the arithmetic
          //                     claim's copy survives, and the identical
          //                     wrapping call is emitted anyway as
          //                     `// claim: not recorded` -- the run reports
          //                     `2 of 3`.
          //
          // So refusing the path claim alone moved the defect rather than
          // removing it, and made the artefact WORSE: same red test, now with
          // no provenance back to the report. A claim that is not an
          // instrumented path goal has no business producing a
          // complete-path-coverage test case, and under path coverage that is
          // exactly what these are.
          //
          // Narrowed to path coverage on purpose. Branch coverage's goals carry
          // the same property string, so this cannot change their output; and
          // no path-coverage run passed an arithmetic check before this flag
          // existed (INVOCATION_DECISIONS row 6 says not to), so there is no
          // prior behaviour here to preserve.
          const bool non_path_claim =
            is_path_cov && (is_probe_claim ||
                            claim.claim_property != "instrumented assertion");
          if (non_path_claim)
            log_debug(
              "coverage",
              "not collecting a Foundry case for the non-instrumented claim "
              "'{}' ({}): under path coverage only an enumerated path goal may "
              "produce a test",
              claim.claim_msg,
              claim.claim_property);
          bool revert_only = false;
          {
            std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
            revert_only = goto_coveraget::arith_revert_only_paths.count(
                            {claim.claim_msg, claim.claim_loc}) > 0;
          }
          if (non_path_claim)
          {
            // counted separately from the revert-only refusal: they are
            // different facts and a shared counter would make either
            // unreadable.
          }
          else if (revert_only)
          {
            goto_coveraget::arith_revert_only_suppressed.fetch_add(
              1, std::memory_order_relaxed);
            log_warning(
              "--path-cov-arith-resolve: REFUSING to emit a Foundry case for "
              "'{}'. The re-solve PROVED this path is reachable only by "
              "violating a checked arithmetic operation, so on chain it is "
              "reached through a Panic revert -- a bare call asserting a "
              "normal exit would be RED on the unmodified contract. The path "
              "remains witnessed (F) and counted as covered; only the "
              "rendering is refused",
              prettify_solidity_expr(claim.claim_msg));
          }
          else
            // Collect into the external strategy-level generator when threaded
            // (--k-induction), else the owned member (plain BMC).
            foundry().collect(local_eq, *solver_ptr, ns);
        }

        // Solidity complete-path coverage: harvest this path's CE payload
        // (concrete inputs + post-state) NOW, while this witness's solver
        // model is live — after the next dec_solve() it is gone.
        //
        // EVERY witness, not just the first. The old guard was
        // `is_path_cov && witnesses.empty()`, documented as "one CE per
        // complete path is what the report contracts for" — but the contract
        // was the limitation, not a reason for it: with --all-witnesses the
        // model for witnesses 2..N is built, printed and handed to the Foundry
        // generator, and only this harvest threw it away. Default behaviour is
        // unchanged, because without --all-witnesses the loop breaks after one.
        if (is_path_cov)
        {
          // FAULT INJECTION into the window between the two records of one
          // refutation. `claim_outcome[sig] = 'F'` is already written at this
          // point; `reached_claims.emplace(sig)` is not, and will not be if
          // this throws. That is the state P16_Mapping reached by running out
          // of memory here, and the only way a regression can reach it.
          if (fault_mid_witness && decided_claims.load() >= fault_mid_witness)
          {
            log_error(
              "--path-cov-fault-mid-witness {}: injecting std::bad_alloc "
              "INSIDE the counterexample harvest, after {} decided claim(s) "
              "(fault injection; this is not a real allocation failure)",
              fault_mid_witness,
              decided_claims.load());
            throw std::bad_alloc();
          }
          goto_coveraget::path_ce_t ce;
          ce.sliced = !options.get_bool_option("no-slice");
          // Same condition the dispatch uses to set protect_ce_symbols: with
          // the JSON requested, the payload symbols are exempt from slicing, so
          // `sliced` alone must not be read as "the payload was cut down".
          ce.payload_symbols_protected =
            options.get_bool_option("cov-report-json");
          ce.compact_trace = is_compact_trace;
          ce.revert_pre_rollback = goto_coveraget::revert_paths.count(
                                     {claim.claim_msg, claim.claim_loc}) > 0;
          // Ordered so the emitted post-state is deterministic across runs.
          std::map<std::string, std::string> last_state;
          // The EVM environment, LAST write wins -- see the `is_env` branch
          // below for why first-wins was wrong and what it cost. Separate from
          // `input_seen` on purpose: sharing one set lets an environment name
          // and a parameter name suppress each other.
          std::map<std::string, std::string> last_env;
          // The environment AT THE UNIT'S ENTRY, taken at the entry mark the
          // certify/assert queries insert. The box bounds an ENTRY box; the
          // last-write `last_env` can report a later reseed (MEASURED:
          // block.number 2^256-2 in the payload while the bound's own symbol
          // sat at x_pi's 0), and the driver then cuts a coordinate that
          // never moved (TODO 30 #4). Empty outside those modes.
          std::map<std::string, std::string> entry_env;
          std::set<std::string> input_seen;
          // FIRST WRITE WINS, its own set, for the same reason `last_env` has
          // its own map: a local's name may collide with a parameter's and one
          // shared set would let them suppress each other silently.
          std::set<std::string> nondet_local_seen;
          // Scope prefix this path's function parameters carry. A Solidity
          // parameter symbol is `sol:@C@<contract>@F@<method>@<param>` (see
          // foundry.h's parse_param_symbol), i.e. the method appears WITHOUT
          // the `#N` disambiguator that the goto function id carries. So take
          // claim_msg's function id ("<function-id>:path:<enc>") and drop the
          // `#N` before using it as a prefix, or nothing would ever match.
          std::string fn_scope;
          // Contract scope prefix (`sol:@C@<Contract>@`). Mappings and dynamic
          // arrays do NOT live inside the contract object: the frontend lowers
          // them to CONTRACT-LEVEL global stores, whose ids carry this prefix
          // WITHOUT the `@F@` function marker. That is what separates a state
          // store from a function-scoped local.
          std::string contract_scope;
          // The goto function id, `#N` INTACT — that is the key
          // path_entry_ghost is stored under, so it must not be truncated the
          // way fn_scope is.
          std::string fn_id_full;
          // ---- WHERE A TUPLE RETURN LIVES, AND WHY IT NEEDS ITS OWN KEY ----
          //
          // MEASURED on notes/coverage/poc/P27_TupleReturn.sol: a Solidity
          // function returning `(uint256, uint256)` emits NO `RETURN`
          // instruction at all. The frontend lowers it to per-member writes
          //
          //     ASSIGN tuple_instance$42.mem0 = 11;
          //     ASSIGN tuple_instance$42.mem1 = 12;
          //
          // and falls straight to END_FUNCTION. So the return-value GHOST, which
          // hooks the RETURN instruction, cannot see a tuple by construction --
          // and the values were instead landing in the contract-scope-store
          // branch below and being published as
          // `state_written_value_unavailable`, i.e. as a state variable named
          // `tuple_instance$42.mem0` that the path supposedly wrote. That is a
          // wrong LABEL on a right value, which is worse than a missing one.
          //
          // The instance is named with the FUNCTION'S OWN AST NODE ID, so it can
          // be tied to this path's unit exactly rather than by matching the
          // string `tuple_instance` anywhere: `two_scalars#42` owns
          // `tuple_instance$42` and `mixed_width#65` owns `tuple_instance$65`
          // (both verified in the same dump). Without that tie an internal
          // callee inlined into this unit would donate its own tuple.
          std::string tuple_want;
          {
            std::string fn_id;
            if (is_probe_claim)
            {
              const auto goal_it = goto_coveraget::path_probe_goals.find(
                probe_claim_it->second.goal_id);
              if (goal_it != goto_coveraget::path_probe_goals.end())
                fn_id = goal_it->second.unit;
            }
            else
            {
              const auto p = claim.claim_msg.rfind(":path:");
              if (p != std::string::npos)
                fn_id = claim.claim_msg.substr(0, p);
              else if (
                goto_coveraget::path_cov_certify_mode &&
                (claim.claim_property == "overflow" ||
                 claim.claim_property == "division-by-zero"))
              {
                const std::string &nv =
                  goto_coveraget::path_cov_certify_nonvacuous_key.first;
                const size_t q = nv.rfind(":path:");
                if (q != std::string::npos)
                  fn_id = nv.substr(0, q);
              }
            }
            if (!fn_id.empty())
            {
              fn_id_full = fn_id;
              const auto fpos = fn_id.find("@F@");
              if (fpos != std::string::npos)
                contract_scope = fn_id.substr(0, fpos + 1);
              const auto hash =
                fn_id.find('#', fpos == std::string::npos ? 0 : fpos + 3);
              if (hash != std::string::npos && !contract_scope.empty())
                tuple_want =
                  contract_scope + "tuple_instance$" + fn_id.substr(hash + 1);
              if (hash != std::string::npos)
                fn_id.erase(hash);
              fn_scope = fn_id + "@";
            }
          }
          // ---- WHAT THIS UNIT IS DECLARED TO RETURN ----
          //
          // Taken HERE, beside the id it is a property of, because this is
          // where the goto function id and the namespace both exist. The report
          // that consumes it lives in another function with neither -- an
          // earlier version of this change did the lookup there and did not
          // compile, which is the harmless form of a fact kept in two places.
          //
          // ⛔ LEFT EMPTY WHEN THE LOOKUP FAILS. The consumer prints the old
          // three-way sentence in that case; a missing declaration is not
          // evidence of a void return, and filling in "none" here would put a
          // manufactured fact exactly where the caveat used to be.
          ce.declared_return.clear();
          if (!fn_id_full.empty())
          {
            const symbolt *fsym = ns.lookup(irep_idt(fn_id_full));
            if (fsym && fsym->type.is_code())
            {
              const typet &rt = to_code_type(fsym->type).return_type();
              ce.declared_return =
                (rt.is_nil() || rt.id() == "empty") ? "none" : "present";
            }
          }
          // Entry detection, using the SAME algorithm ESBMC already uses for
          // `--show-funccall-trace` (goto_trace.cpp): every trace step carries
          // the call stack at that point, innermost-first. Diff each step's
          // stack against the previous one by common SUFFIX (the outermost end)
          // and whatever remains at the front was pushed by this step. A new
          // invocation of this path's function begins exactly when one of those
          // newly-pushed frames is it.
          //
          // Two earlier attempts were wrong and are recorded so they are not
          // retried. (1) The instrumentation's `tr = 1` is folded into the
          // following `tr = tr*2 + guard`, so it never reaches the trace.
          // (2) Comparing the location's BARE function name cannot tell one
          // contract's `f` from another's, and cannot see a recursive or
          // re-entrant call into the same function at all — it looks like
          // execution never left. Stack frames carry the full goto function id,
          // so both cases are exact.
          //
          // Steps with an EMPTY stack are skipped rather than treated as a
          // reset: goto_trace.cpp documents that an empty stack means "captured
          // outside any function context", not "no frames on the stack", and
          // treating it as a reset re-emits the whole chain spuriously.
          // Recursion makes "the entry state" ambiguous: with `f` nested three
          // deep there are three different entry states, and the assert that
          // the solver refuted belongs to exactly ONE of them. Measured on a
          // path reachable only inside a recursive call, snapshotting on the
          // most recent push reported the INNERMOST invocation's state, which
          // was neither the outer nor the owning one.
          //
          // So key each snapshot by how many frames of this function were on
          // the stack when it was taken, and at the end pick the depth the
          // violated assert itself is at.
          std::vector<stack_framet> prev_stack;
          std::map<size_t, std::map<std::string, std::string>> entry_by_depth;
          size_t prev_target_depth = 0;
          size_t assert_target_depth = 0;
          bool assert_depth_known = false;
          auto count_target_frames =
            [&fn_id_full](const std::vector<stack_framet> &s) {
              size_t n = 0;
              for (const auto &fr : s)
                if (fr.function.as_string() == fn_id_full)
                  ++n;
              return n;
            };
          std::map<std::string, std::string> entry_snapshot;
          // Base symbol id of a possibly-indexed lvalue (`m[k]` -> `m`).
          auto base_sym_id = [](expr2tc e) -> std::string {
            for (;;)
            {
              if (is_index2t(e))
                e = to_index2t(e).source_value;
              else if (is_member2t(e))
                e = to_member2t(e).source_value;
              else
                break;
            }
            return is_symbol2t(e) ? to_symbol2t(e).thename.as_string()
                                  : std::string();
          };
          std::set<std::string> unrendered_seen;
          // The unit's own return value and the runtime record that a RETURN
          // actually executed. Accumulated locally and combined after the walk,
          // for the same reason the environment is: the pair that belongs in the
          // payload is the one in force when the walk stopped at this path's own
          // assert, and a half-updated pair published mid-walk would pair one
          // path's flag with another's value.
          std::string ret_value;
          bool ret_flag = false;
          uint64_t observed_path_id = 0;
          uint64_t observed_path_depth = 0;
          bool observed_path_id_known = false;
          bool observed_path_depth_known = false;
          auto observer_it =
            goto_coveraget::path_observer_symbols.find(fn_id_full);
          // A TUPLE return, member index -> value. Keyed on the INDEX rather
          // than the name so `mem10` cannot sort between `mem1` and `mem2`,
          // which a lexicographic map would do silently and which would publish
          // the members of the tuple in the wrong order -- a wrong value wearing
          // the right shape.
          std::map<unsigned long, std::string> ret_members;
          // Low-level-call success destructures are source-marked by the
          // Solidity frontend. Keep them per recursive invocation: the first
          // `_s` in a trace may belong to a reentrant call, while the violated
          // path assertion belongs to a different `go` frame.
          std::map<size_t, std::map<std::string, std::string>>
            extcall_success_by_depth;
          for (const auto &st : w.trace.steps)
          {
            // Stop at THIS path's own violated assert. The harness runs
            // --solidity-max-tx transactions (default 2) and the trace spans
            // all of them, so scanning past this point would let a LATER
            // transaction's writes overwrite the post-state of the invocation
            // that this path actually describes.
            if (st.is_assert() && st.comment == claim.claim_msg)
            {
              ce.scoped_to_claim = true;
              // Which invocation does the refuted assert belong to? Its own
              // stack says so.
              if (!st.stack_trace.empty())
              {
                assert_target_depth = count_target_frames(st.stack_trace);
                assert_depth_known = assert_target_depth > 0;
              }
              break;
            }
            // Entry detection runs on EVERY step, not only assignments: the
            // frame is pushed by the call instruction itself.
            if (!st.stack_trace.empty())
            {
              const auto &cur = st.stack_trace;
              size_t common = 0;
              while (common < prev_stack.size() && common < cur.size() &&
                     prev_stack[prev_stack.size() - 1 - common] ==
                       cur[cur.size() - 1 - common])
                ++common;
              bool pushed_target = false;
              for (size_t k = cur.size() - common; k-- > 0;)
                if (cur[k].function.as_string() == fn_id_full)
                {
                  pushed_target = true;
                  break;
                }
              const size_t d = count_target_frames(cur);
              // Record only on an INCREASE, and only for the depth just
              // reached: whatever state has accumulated is what that
              // particular invocation started from.
              if (pushed_target && d > prev_target_depth)
                entry_by_depth[d] = last_state;
              prev_target_depth = d;
              prev_stack = cur;
            }
            if (!st.is_assignment())
              continue;
            if (is_nil_expr(st.lhs) || is_nil_expr(st.value))
              continue;
            // ---- ENTRY MARK: re-take the entry snapshot ----
            //
            // `--path-cov-certify` / `--path-cov-assert` insert their
            // establish assignments (a relation-backed entry state, or a
            // FREED coordinate) as the unit body's first instructions and
            // follow them with an assignment to `__ESBMC_*_entry_mark$N`.
            // The snapshot taken above, at the frame push, predates those
            // assignments; the entry state the QUERY was about is the one in
            // force here. Same depth bookkeeping as the push-time snapshot.
            if (is_symbol2t(st.lhs) &&
                to_symbol2t(st.lhs).thename.as_string().find("_entry_mark$") !=
                  std::string::npos)
            {
              if (!st.stack_trace.empty())
                entry_by_depth[count_target_frames(st.stack_trace)] = last_state;
              entry_env = last_env;
              continue;
            }
            // Diagnostic for the classification below: `--verbosity
            // coverage:9` prints every assignment the harvest sees, with the
            // things the classification keys on. Without it, an empty
            // `inputs`/`entry_storage` is indistinguishable from a rule that
            // silently never matches — which is exactly how the two bugs above
            // (location-based extcall matching, `tr = 1` as entry marker) were
            // found.
            {
              const expr2tc dnd = symex_slicet::get_nondet_symbol(st.rhs);
              log_debug(
                "coverage",
                "ce step: sym='{}' name='{}' fn='{}' nondet='{}'",
                is_symbol2t(st.lhs) ? to_symbol2t(st.lhs).thename.as_string()
                                    : std::string("<not-a-symbol>"),
                from_expr(ns, "", st.lhs),
                st.pc->location.get_function().as_string(),
                (!is_nil_expr(dnd) && is_symbol2t(dnd))
                  ? to_symbol2t(dnd).thename.as_string()
                  : std::string("-"));
              std::string frames;
              for (const auto &fr : st.stack_trace)
                frames +=
                  (frames.empty() ? "" : " < ") + fr.function.as_string();
              log_debug(
                "coverage",
                "   stack[{}]: {}",
                st.stack_trace.size(),
                frames.empty() ? std::string("<empty>") : frames);
            }
            const std::string name = from_expr(ns, "", st.lhs);
            if (
              observer_it != goto_coveraget::path_observer_symbols.end() &&
              is_symbol2t(st.lhs) && is_constant_int2t(st.value))
            {
              const std::string sid = to_symbol2t(st.lhs).thename.as_string();
              if (sid == observer_it->second.first)
              {
                observed_path_id =
                  to_constant_int2t(st.value).value.to_uint64();
                observed_path_id_known = true;
                continue;
              }
              if (sid == observer_it->second.second)
              {
                observed_path_depth =
                  to_constant_int2t(st.value).value.to_uint64();
                observed_path_depth_known = true;
                continue;
              }
            }
            // NOT extcall_returns. The `_ESBMC_Nondet_Extcall_<C>` symbols are
            // the RE-ENTRY model's own choice bits (which public method the
            // outside world calls back into), not the value the external call
            // returned to the contract. Measured: on the two paths of
            // `if (ok)`, which by construction disagree about `ok`, these
            // symbols carry the IDENTICAL tuple — so they cannot be `ok`.
            // Publishing them under `extcall_returns` would be a wrong answer
            // wearing the right label, so they stay counted as plumbing.
            // See ce_extraction.extcall_returns_unavailable_reason below.
            if (
              is_symbol2t(st.lhs) &&
              to_symbol2t(st.lhs).thename.as_string().find(
                "_ESBMC_Nondet_Extcall") != std::string::npos)
            {
              ++ce.dropped_internal;
              continue;
            }
            // ---- THE UNIT'S OWN RETURN VALUE ----
            //
            // Materialised by the instrumenter at the RETURN site
            // (goto_coverage.cpp, "MATERIALISE THE UNIT'S OWN RETURN VALUE"),
            // because it exists nowhere else: the dispatcher calls a unit with
            // no lvalue and the RETURN carries an expression, so before that
            // change no trace step held the value at all -- measured, 208
            // classified assignments on P19_ReturnShapes.tern_lit and not one of
            // them was the return.
            //
            // Taken HERE, ahead of the classification below, for the same reason
            // the extcall symbols are: that classification has exactly three
            // outcomes -- parameter -> inputs, environment -> env, otherwise
            // dropped -- and a ghost is none of the first two, so it would be
            // filed as harness plumbing.
            //
            // A non-constant model value is left UNKNOWN rather than rendered:
            // `final_state` already shipped an unevaluated expression string
            // once ("0xFFFF... / 0") and a consumer parsing this as an integer
            // would refuse it, or worse, print it into a test.
            // The FLAG is checked first and is the only thing that authorises
            // publishing a value. MEASURED with the value alone: tern_lit's
            // revert path (enc=2, which never reaches a RETURN) published the
            // entry initialisation as `return_value: "0"`. 0 is also a real
            // return value, so nothing about the value itself can separate the
            // two cases -- only a runtime record of "a RETURN executed" can.
            if (
              is_symbol2t(st.lhs) &&
              to_symbol2t(st.lhs).thename.as_string().find(
                "__ESBMC_path_retset$") != std::string::npos)
            {
              if (is_constant_expr(st.value))
                ret_flag = !is_false(st.value);
              continue;
            }
            if (
              is_symbol2t(st.lhs) &&
              to_symbol2t(st.lhs).thename.as_string().find(
                "__ESBMC_path_ret$") != std::string::npos)
            {
              // Last write before this path's own assert wins, matching how the
              // environment is harvested. A non-constant model value is left
              // alone rather than rendered: `final_state` already shipped an
              // unevaluated expression string once ("0xFFFF... / 0").
              if (is_constant_expr(st.value))
                ret_value = from_expr(ns, "", st.value);
              continue;
            }
            // Mapping / dynamic-array state: these do NOT live in the contract
            // object, so the `this->` test below never sees them and they would
            // be discarded as harness noise — leaving a reader to infer the
            // mapping was untouched. Recognise them by their contract-level id
            // and record them as state; when the model value is not a constant
            // (an infinite-array store), record the NAME so the write is still
            // visible rather than silently lost.
            const std::string bid = base_sym_id(st.lhs);
            // A member of THIS unit's tuple instance is the unit's own return
            // value, and it must be claimed BEFORE the contract-scope branch
            // below -- that branch's test (`sol:@C@...` with no `@F@`) is true
            // of the tuple instance, which is how these values were being
            // published as unrenderable contract state.
            //
            // No runtime flag is needed here, unlike the scalar case. The
            // members are written only on a path that actually returns (the
            // revert arm of P27's `two_scalars` jumps straight past them), so
            // the presence of the write IS the runtime evidence the flag
            // supplies for a RETURN.
            if (
              !tuple_want.empty() && bid == tuple_want && is_member2t(st.lhs) &&
              is_constant_expr(st.value))
            {
              const irep_idt &memid = to_member2t(st.lhs).member;
              const std::string mem = memid.as_string();
              // PROJECT. A tuple member write is lowered as a WHOLE-OBJECT
              // update, exactly like the `this->x = v` case a few lines below,
              // so the model hands back the ENTIRE tuple struct. MEASURED
              // without this projection on P27_TupleReturn.two_scalars:
              //     mem0 -> "{ .mem0=11, .mem1=0 }"
              //     mem1 -> "{ .mem0=11, .mem1=12 }"
              // i.e. every member reporting the whole aggregate, and the
              // rendered "tuple" being a pair of structs rather than a pair of
              // values. On the unequal-width unit it additionally exposed the
              // struct's `anon_pad$2` padding member.
              expr2tc mv = st.value;
              if (is_constant_struct2t(mv))
              {
                const struct_type2t &sty = to_struct_type(mv->type);
                for (size_t i = 0; i < sty.member_names.size(); ++i)
                  if (sty.member_names[i] == memid)
                  {
                    mv = to_constant_struct2t(mv).datatype_members[i];
                    break;
                  }
              }
              if (
                mem.rfind("mem", 0) == 0 && is_constant_expr(mv) &&
                !is_constant_struct2t(mv) && !is_constant_array2t(mv))
              {
                char *endp = nullptr;
                const unsigned long k = strtoul(mem.c_str() + 3, &endp, 10);
                if (endp != nullptr && *endp == '\0')
                {
                  ret_members[k] = from_expr(ns, "", mv);
                  continue;
                }
              }
              // A member whose name is not `mem<N>` is NOT silently folded in:
              // falling through leaves it to the branch below, which reports it
              // as written-and-unrenderable. Publishing it as part of the tuple
              // would put an unknown component in a value a test asserts on.
            }
            if (
              !contract_scope.empty() && bid.rfind(contract_scope, 0) == 0 &&
              bid.find("@F@") == std::string::npos)
            {
              // ESBMC's own contract-scope plumbing (address-binding tables,
              // call temporaries) is not user state.
              if (
                name.rfind("$", 0) == 0 ||
                name.find("_bind_cname") != std::string::npos ||
                name.find("return_value$") != std::string::npos)
              {
                ++ce.dropped_internal;
                continue;
              }
              // A mapping/array element write (`m[k] = v`) yields a SCALAR
              // model value — that is the useful post-state. The companion
              // whole-store assignment (`m = with(m,k,v)`) yields the entire
              // infinite array, which is the same information in a form no
              // consumer can use and which would dwarf the report; name it as
              // written-but-unrendered instead of dumping thousands of zeros.
              // Only an ELEMENT write (`m[k] = v`) carries a usable post-state
              // value. A bare whole-store assignment is the `with(...)` bulk
              // form of the same update; recording it would print the entire
              // infinite array — and its zero-initialised form reads as "the
              // mapping is empty", contradicting the element entry beside it.
              const bool scalar = is_index2t(st.lhs) &&
                                  is_constant_expr(st.value) &&
                                  !is_constant_array2t(st.value) &&
                                  !is_constant_struct2t(st.value);
              if (scalar)
                last_state[name] = from_expr(ns, "", st.value);
              else if (unrendered_seen.insert(name).second)
                ce.state_written_unrendered.push_back(name);
              continue;
            }
            // Never guess: a value that did not come back from the model as a
            // constant is dropped rather than rendered symbolically.
            if (!is_constant_expr(st.value))
              continue;
            // A Solidity state write `this->x = v` is lowered as a whole-object
            // update, so the model hands back the ENTIRE contract struct.
            // Project out the written member, otherwise every state variable
            // would be reported as the same opaque object literal.
            expr2tc val_expr = st.value;
            if (is_member2t(st.lhs) && is_constant_struct2t(val_expr))
            {
              const irep_idt &mem = to_member2t(st.lhs).member;
              const struct_type2t &sty = to_struct_type(val_expr->type);
              for (size_t i = 0; i < sty.member_names.size(); ++i)
                if (sty.member_names[i] == mem)
                {
                  val_expr = to_constant_struct2t(val_expr).datatype_members[i];
                  break;
                }
            }
            // ---- THE GUARD ABOVE TESTS THE WRONG EXPRESSION ----
            //
            // `is_constant_expr(st.value)` is checked BEFORE the projection, and
            // a Solidity state write is lowered as a whole-object update: the
            // model hands back the entire contract struct, which IS a
            // constant_struct, so the test passes. The value actually published
            // is the PROJECTED MEMBER, and that can be anything the simplifier
            // declined to fold.
            //
            // MEASURED on notes/coverage/poc/P18_Unchecked.sol, unit `div`,
            // path 6:
            //
            //     final_state {"r": "0xFFFF...FFFF / 0"}
            //
            // -- an unevaluated division, as a STRING, in a field contracted to
            // hold values. The simplifier explicitly refuses to fold a zero
            // divisor (smt_conv), so `a / 0` never becomes a constant and
            // from_expr renders all of it. `--div-by-zero-check` does NOT change
            // it: that cell is byte-identical, because the check adds a claim
            // and constrains no model.
            //
            // EXECUTION_PLAN §7.1 predicted this one step later -- it expected
            // `type(uint256).max`, bvudiv's total-function value -- and warned
            // the value would flow into R2 assertions. The R1/R2 ladder is built
            // from this exact field, and a consumer either parses it as an
            // integer (solidity_path_generalise.py's coord_values refuses the
            // coordinate on failure, safe but lossy) or renders it into a test.
            //
            // Dropped and NAMED, through the channel that already exists for
            // "this path wrote it and the value is not renderable" -- the same
            // one mapping and dynamic-array stores use. Silently omitting it
            // would let a reader infer the variable was UNCHANGED, which is a
            // wrong conclusion rather than a missing one.
            if (!is_constant_expr(val_expr))
            {
              const std::string bare_un =
                name.find("this->") != std::string::npos
                  ? name.substr(name.rfind("->") + 2)
                  : name;
              // THE SAME PLUMBING FILTER ITS TWO NEIGHBOURS USE. Without it
              // this reported `_ESBMC_Object_<C>.$address` and
              // `_ESBMC_bind_cname` as user state the path wrote and could not
              // render -- measured on P18 the moment the drop was added. Those
              // are ESBMC's own address-binding fields; the contract-scope
              // branch above drops them as `dropped_internal` and the `this->`
              // branch below refuses them by the same two tests, so a third
              // spelling of the rule would have been a third thing to keep in
              // step. Reporting them would be noise in a list whose whole value
              // is that every name in it is a real state variable.
              if (
                bare_un.empty() || bare_un[0] == '$' ||
                bare_un.rfind("_ESBMC", 0) == 0 ||
                bare_un.find("_bind_cname") != std::string::npos ||
                bare_un.find("return_value$") != std::string::npos)
              {
                ++ce.dropped_internal;
                continue;
              }
              if (unrendered_seen.insert(bare_un).second)
                ce.state_written_unrendered.push_back(bare_un);
              continue;
            }
            const std::string val = from_expr(ns, "", val_expr);
            if (st.pc->location.get_bool("sol_extcall_success"))
            {
              const size_t depth = count_target_frames(st.stack_trace);
              if (depth > 0)
                extcall_success_by_depth[depth][name] = val;
              continue;
            }
            // WHOLE-OBJECT restore: require(cond) / revert("msg") lower to a
            // rollback block that assigns the entry snapshot back as ONE
            // aggregate — `*this = _sol_save_this` — never per field. Tracking
            // only `this->member` writes would miss it and report the
            // pre-rollback value as the post-state, i.e. a WRONG value. Adopt
            // every member of the restored object as the new post-state.
            if (name.rfind("*this", 0) == 0 && is_constant_struct2t(st.value))
            {
              const struct_type2t &sty = to_struct_type(st.value->type);
              const auto &mems =
                to_constant_struct2t(st.value).datatype_members;
              for (size_t i = 0; i < sty.member_names.size() && i < mems.size();
                   ++i)
              {
                const std::string mn = sty.member_names[i].as_string();
                if (
                  mn.empty() || mn[0] == '$' || mn.rfind("_ESBMC", 0) == 0 ||
                  mn.rfind("anon_pad", 0) == 0)
                  continue;
                if (is_constant_expr(mems[i]))
                  last_state[mn] = from_expr(ns, "", mems[i]);
              }
              continue;
            }
            if (name.find("this->") != std::string::npos)
            {
              // Contract state variable: the LAST write on this path is the
              // post-state this path produces. ESBMC's own bookkeeping fields
              // on the contract object ($address/$code/$codehash/binding) are
              // not user state and would only add noise.
              const std::string bare = name.substr(name.rfind("->") + 2);
              if (
                !bare.empty() && bare[0] != '$' && bare.rfind("_ESBMC", 0) != 0)
                last_state[bare] = val;
            }
            else
            {
              // A nondet-sourced assignment is a value the harness CHOSE. But
              // "chosen by the harness" is far broader than "call argument":
              // it also covers the allocator tables, dispatcher choice bits and
              // temporaries. Classify instead of dumping the whole bag, which
              // would bury the one value a consumer needs (and bloat the report
              // with multi-KB internal arrays).
              const expr2tc nd = symex_slicet::get_nondet_symbol(st.rhs);
              if (is_nil_expr(nd))
                continue;
              // A Solidity parameter/local carries the mangled id
              // `sol:@C@<Contract>@F@<fn>#N@<name>`. Requiring the prefix to be
              // THIS path's own function id keeps the call arguments of the
              // unit under test and drops harness/dispatcher locals that merely
              // share the shape (`_new_ts`, `addr`, `tmp`, ...).
              std::string sym_id;
              if (is_symbol2t(st.lhs))
                sym_id = to_symbol2t(st.lhs).thename.as_string();
              const bool in_fn_scope =
                !fn_scope.empty() && sym_id.rfind(fn_scope, 0) == 0;
              // A prefix match says "declared inside this function", which is
              // true of a PARAMETER and of every local alike. Only a parameter
              // is something the caller supplies, so ask the symbol table
              // instead of the name. Before this, an external call's return
              // value (a local) was reported under `inputs`, i.e. as an
              // argument a generated test could pass — it cannot.
              bool is_param = false;
              if (in_fn_scope && !sym_id.empty())
              {
                const symbolt *psym = ns.lookup(irep_idt(sym_id));
                is_param = psym && psym->is_parameter;
              }
              // ⚠ This used to read "external-call returns were already taken
              // above, before the contract-scope-store branch could discard
              // them". No such code exists anywhere -- ce.extcall_returns has a
              // declaration, three readers and ZERO writers -- so the comment
              // asserted a step that had never been written. It cost a session:
              // the empty field was read as "the harvest cannot see the value",
              // when in fact the value arrives here resolved and falls off the
              // end of the classification below for want of a bucket.
              // A comment claiming a step that does not exist is worse than no
              // comment, because it redirects the next reader away from the
              // line that actually drops the value.
              // ---- IS THIS ASSIGNMENT IN A FRAME BELOW THE UNIT UNDER TEST? --
              //
              // `in_fn_scope` above is a NAME test: the symbol's own mangled id
              // must start with this unit's scope. That is true of the unit's
              // parameters and locals and FALSE of a callee's locals, which
              // carry the callee's scope -- so a value produced one frame down
              // is invisible to it.
              //
              // MEASURED, B5_ExtcallInCallee, two units differing in exactly
              // that and nothing else -- same low-level call, same success bit,
              // same `if (!success) revert`, same state write:
              //
              //   probeInline  (assembly in the unit's own body)
              //     path 6  extcall_returns [{"symbol":"success","value":"0"}]
              //     path 7  extcall_returns [{"symbol":"success","value":"1"}]
              //   probeLib     (identical work, one frame down in a library)
              //     path 6  extcall_returns []   dropped 23
              //     path 7  extcall_returns []   dropped 23
              //
              // The second is farming/deposit's shape: its call is inside
              // `SafeERC20.safeTransferFrom`, and on that unit the harvest
              // reports an empty list beside 197 and 199 dropped values.
              //
              // So membership is asked of the FRAME as well as of the name.
              //
              // ⛔ THE FRAME IS THE FULL GOTO FUNCTION ID, and the first attempt
              // at this compared against its tail, which never matched anything.
              // The tail came from a census line QUOTED IN A COMMENT elsewhere
              // in the tree as `stack[3]: probe#32 < Nondet_Extcall < Main` --
              // an abbreviation someone wrote by hand. The tool's own output,
              // re-run for this, is:
              //
              //   sym='sol:@C@B5Lib@F@mustCall@success#9' fn='mustCall'
              //     stack[3]: sol:@C@B5_ExtcallInCallee@F@probeLib#44
              //             < sol:@C@B5_ExtcallInCallee@_ESBMC_Nondet_Extcall...
              //             < sol:@C@B5_ExtcallInCallee@F@_ESBMC_Main...
              //
              // -- full ids, and equal to `fn_id_full` verbatim. The abbreviated
              // quote cost one build and one run; the rule it breaks is that a
              // paraphrase of a tool's output is not the tool's output.
              //
              // NOTE the library call is INLINED: `mustCall` is not a frame of
              // its own, so a test looking for the callee on the stack would
              // find nothing either. What identifies the value is that the
              // UNIT's frame is on the stack while the symbol's own scope is
              // somewhere else.
              //
              // ⛔ STILL RESTRICTED TO SOLIDITY SYMBOLS, AND PLUMBING IS STILL
              // EXCLUDED. The frame test alone would admit any C-level harness
              // temporary assigned while the unit is on the stack, and even
              // among `sol:@` symbols the census shows the address-binding
              // object `sol:@_ESBMC_Object_<C>#` carrying the unit's frame. The
              // two extra tests are the same ones the contract-scope branch
              // already applies, for the same reason: every name published here
              // has to be a quantity from the source.
              bool in_unit_frame = false;
              if (
                !fn_id_full.empty() && sym_id.rfind("sol:@", 0) == 0 &&
                !name.empty() && name[0] != '$' && name.rfind("_ESBMC", 0) != 0)
              {
                for (const auto &fr : st.stack_trace)
                  if (fr.function.as_string() == fn_id_full)
                  {
                    in_unit_frame = true;
                    break;
                  }
              }
              const bool is_env = name.rfind("msg_", 0) == 0 ||
                                  name.rfind("tx_", 0) == 0 ||
                                  name.rfind("block_", 0) == 0;
              if (is_param)
              {
                if (input_seen.insert(name).second)
                  ce.inputs.emplace_back(name, val);
              }
              else if (is_env)
              {
                // ---- LAST WRITE WINS FOR THE ENVIRONMENT, NOT FIRST ----
                //
                // A parameter is assigned once, at the call, so first-wins is
                // right for `inputs`. The EVM environment is not: it is written
                // once at declaration (`solidity_blockchain.c`, before the
                // harness runs) and then RE-SEEDED at the top of every
                // dispatcher iteration by `_sol_per_tx_reseed`. First-wins
                // therefore always published the declaration-time value and
                // silently discarded the one the transaction actually ran under.
                //
                // MEASURED on notes/coverage/poc/D09_ValueGate.sol -- six lines,
                // one unit, no source decision, so the only decision in the run
                // is the synthetic ABI value gate. From the `set:path:2`
                // counterexample, verbatim:
                //
                //     State 11  solidity_blockchain.c:31
                //       msg_value = 0
                //     State 51  solidity_misc.c:171  _sol_per_tx_reseed
                //       msg_value = 0xFFFF...FFFF
                //     State 72  D09_ValueGate.sol:36  set
                //       path_tr$0 = 2
                //     Violated property: ...:path:2
                //
                // `path:2` is the depth-1 revert taken when value is sent to a
                // NONPAYABLE entry, so it requires `msg.value != 0`, and the
                // report published 0 -- the condition under which the path is
                // NOT taken. The sibling `set:path:3` is the control: there the
                // reseed chooses 0 and 0 is correct, so the old code was right
                // half the time by coincidence.
                //
                // THE COST WAS NOT A MISSING FIELD. Both arms of the gate then
                // render as the same call, so ONE emitted Foundry case carries
                // BOTH path ids -- 37 of 161 cases across the PoC set -- and a
                // path no test can reach is counted as covered. The same
                // mechanism reseeds msg_sender (State 50), which is the other 6
                // of the 63 payload-vs-path contradictions
                // `notes/coverage/scripts/ce_consistency.py` reports, so 61 of
                // the 63 are this one line.
                //
                // "LAST" IS BOUNDED BY THE LOOP, NOT BY THE TRACE. This walk
                // already breaks at this path's own violated assert (see the
                // `st.comment == claim.claim_msg` guard above), so the last
                // write kept here is the reseed of the transaction that ENTERED
                // the unit -- not a later transaction's. That is what makes this
                // correct at `--solidity-max-tx 2` and not only at 1. Solidity
                // cannot assign msg.* inside a function, so there is no write
                // between entry and the assert to prefer over the reseed.
                //
                // Its own map, no longer sharing `input_seen` with the
                // parameters: one shared set means an environment name and a
                // parameter name that happen to collide silently suppress each
                // other, and `std::map` additionally makes the published order
                // deterministic across runs rather than trace-order.
                last_env[name] = val;
              }
              else if ((in_fn_scope || in_unit_frame) && !sym_id.empty())
              {
                // ---- THE MISSING BUCKET, cause (a) of three ----
                //
                // A value that is nondet-sourced, declared INSIDE the unit under
                // test, and not one of its parameters. It arrived here resolved
                // and then fell off the end of the classification for want of a
                // bucket -- which is why `extcall_returns` had a declaration,
                // three readers and zero writers, and why every report said
                // "not harvested at all".
                //
                // WHY IT MATTERS, measured on farming/deposit: six of its seven
                // paths end at the same verdict -- the single-point check of
                // certification REFUTED -- and the driver names two possible
                // causes it cannot separate. For two of them it says the
                // refuting witness and the path's counterexample "agree on every
                // coordinate AND every scalar quantity in the payload", leaving
                // the external-call return as a NAMED CANDIDATE that could not
                // appear in the comparison because it was never in the payload.
                // Populating this field is what turns that candidate into a
                // finding or eliminates it.
                //
                // ⛔ THE NAME IS HONEST ABOUT WHAT IT IS. This is a nondet LOCAL,
                // and an external call's return is the common way one appears --
                // but nothing here proves that is what a given entry is. A
                // consumer must read it as "a quantity the harness chose that no
                // test can pass as an argument", which is the property that makes
                // it interesting, and not as "the callee returned this".
                //
                // ⛔ IT IS NOT AN INPUT. It stays out of `inputs` deliberately:
                // that field is contracted to hold values a generated test can
                // supply as call arguments, and a local is not one. Publishing it
                // there is a defect this file already fixed once.
                //
                // FIRST WRITE WINS, unlike the environment directly above: a
                // local is assigned where it is bound, and a later reassignment
                // is a different value of the same name rather than a reseed of
                // the same one.
                if (nondet_local_seen.insert(name).second)
                  ce.extcall_returns.emplace_back(name, val);
              }
              else
                ++ce.dropped_internal;
            }
          }
          for (const auto &[n, v] : last_state)
            ce.final_state.emplace_back(n, v);
          // BOTH conditions. The flag says a RETURN executed on this path; the
          // non-empty string says its value came back from the model as a
          // constant. Either one alone would publish something the other
          // contradicts.
          if (ret_flag && !ret_value.empty())
          {
            ce.return_value = ret_value;
            ce.return_value_known = true;
          }
          // A tuple return, rendered in MEMBER ORDER as `(v0, v1, ...)`. A unit
          // returns either a single value or a tuple, never both, so the two
          // arms cannot fight over the field; the `else if` says so rather than
          // leaving a last-writer-wins that would silently prefer one shape.
          else if (!ret_members.empty())
          {
            std::string t = "(";
            bool first = true;
            for (const auto &[k, v] : ret_members)
            {
              t += (first ? "" : ", ") + v;
              first = false;
            }
            ce.return_value = t + ")";
            ce.return_value_known = true;
          }
          // Published after the walk, for the same reason `final_state` is: the
          // value that belongs in the report is the one in force when the unit
          // ran, and that is only known once the walk has stopped at this path's
          // own assert.
          for (const auto &[n, v] :
               ((goto_coveraget::path_cov_certify_mode ||
                 goto_coveraget::path_cov_assert_mode) &&
                    !entry_env.empty()
                  ? entry_env
                  : last_env))
            ce.env.emplace_back(n, v);
          if (assert_depth_known)
          {
            auto xd = extcall_success_by_depth.find(assert_target_depth);
            if (xd != extcall_success_by_depth.end())
              for (const auto &[n, v] : xd->second)
                ce.extcall_returns.emplace_back(n, v);
          }
          // Pick the snapshot for the invocation the refuted assert sits in.
          // Anything else would be a different invocation's entry state, which
          // for a recursive function is a different number — reporting it would
          // be a wrong value, not an approximate one.
          if (assert_depth_known)
          {
            auto ed = entry_by_depth.find(assert_target_depth);
            if (ed != entry_by_depth.end())
            {
              entry_snapshot = ed->second;
              ce.entry_storage_known = true;
            }
          }
          for (const auto &[n, v] : entry_snapshot)
            ce.entry_storage.emplace_back(n, v);
          ce.observed_path_id = observed_path_id;
          ce.observed_path_depth = observed_path_depth;
          ce.observed_path_known =
            observed_path_id_known && observed_path_depth_known;
          ce_all.push_back(std::move(ce));

          // Publish the first payload before asking for extra witnesses. If a
          // later blocking-clause solve or model harvest runs out of memory, the
          // concrete member that already refuted this path must still survive in
          // cov-ce-journal.json for the driver to salvage a replay test.
          if (
            !is_probe_claim && ce_all.size() == 1 &&
            !goto_coveraget::path_ce_journal_path.empty())
          {
            {
              std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
              goto_coveraget::path_ce[claim_sig] = ce_all.front();
              goto_coveraget::path_ce_all[claim_sig] = ce_all;
            }
            goto_coveraget::write_path_ce_journal_atomic(
              fmt::format(
                "after first witness for claim {} of {}",
                decided_now,
                remaining_claims),
              /*complete=*/false);
          }
        }

        witnesses.push_back(std::move(w));

        if (!enumerate)
          break;
        if (witnesses.size() >= max_w)
        {
          stop_reason = enumeration_stop_reasont::CapHit;
          break;
        }

        // If this witness has no nondet inputs we can't enumerate further —
        // there's nothing meaningful to block. Mark the reason so the user
        // doesn't read "UNSAT" as "exhaustive".
        if (witnesses.back().nondet_inputs.empty())
        {
          stop_reason = enumeration_stop_reasont::NoInputs;
          break;
        }

        // Open a single SMT context frame the first time we add a blocking
        // clause. Every subsequent blocking clause goes into the same frame;
        // the matching pop_ctx() after the loop drops them all in one shot.
        // This keeps the feature safe under --smt-during-symex, where
        // solver_ptr aliases the shared runtime_solver: blocking clauses
        // asserted while enumerating claim A cannot leak into claim B.
        // (Push must come *after* the first model read — bitwuzla and other
        // backends invalidate the current model on push.)
        if (!ctx_pushed)
        {
          solver_ptr->push_ctx();
          ctx_pushed = true;
        }

        // Block this input tuple and re-solve on the same instance.
        expr2tc block = make_blocking_expr(witnesses.back().nondet_inputs);
        solver_ptr->assert_expr(block);
        enum_result = solver_ptr->dec_solve();
      }

      // dec_solve() can return P_ERROR / P_SMTLIB; in that case the witness
      // set is *not* exhaustive — flag it explicitly.
      if (
        stop_reason == enumeration_stop_reasont::Unsat &&
        enum_result != smt_convt::P_UNSATISFIABLE &&
        enum_result != smt_convt::P_SATISFIABLE)
        stop_reason = enumeration_stop_reasont::Error;

      // Drop every blocking clause we asserted; the next claim's solve
      // sees the solver in its pre-enumeration state.
      if (ctx_pushed)
        solver_ptr->pop_ctx();

      // Publish the payloads. `path_ce` keeps the FIRST witness, unchanged, so
      // every existing consumer (the Foundry emitter, audit_certify_witness,
      // the CE journal, the covered set) reads exactly what it read before;
      // `path_ce_all` carries the whole set. Two maps rather than one changed
      // type, because a changed type is a change to four consumers at once and
      // three of them want one witness.
      if (is_path_cov && !ce_all.empty())
      {
        std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
        if (is_probe_claim)
        {
          auto &dst =
            goto_coveraget::path_probe_observations[probe_claim_it->second
                                                      .goal_id];
          for (auto &ce : ce_all)
            dst.push_back(std::move(ce));
        }
        else
        {
          goto_coveraget::path_ce[claim_sig] = ce_all.front();
          goto_coveraget::path_ce_all[claim_sig] = std::move(ce_all);
        }
      }

      // Store claim signature (once — multiple witnesses are still one claim)
      if (is_assert_cov)
      {
        std::lock_guard lock(reached_mul_claims_mutex);
        reached_mul_claims.emplace(claim_sig);
      }
      else
      {
        std::lock_guard lock(reached_claims_mutex);
        if (is_goto_cov)
          reached_claims.emplace(claim_sig);
        else
          reached_claims.emplace(claim.claim_cstr);
        // Item 2e: persist this newly-witnessed branch edge immediately
        // and atomically, so a mid-run kill (the heavy --coverage-whole-
        // unit case dies in-solve before report_coverage) still saves
        // partial progress and bounded re-runs accumulate monotonically.
        // Branch coverage only; key == Item 2's (claim_msg, claim_loc);
        // under the same mutex as reached_claims.
        // Solidity complete-path coverage uses the SAME cross-run covered-set
        // so an escalation round (tx=1 -> 2 -> 3) does not re-instrument a
        // path whose CE is already in hand; its key is the same
        // (claim_msg, claim_loc) pair, where claim_msg is the "fn:path:enc"
        // comment, unique per complete path.
        // Complete-path coverage persists through the CONTENT-ADDRESSED writer
        // instead: its key is the stable id, and `covered_set` is empty for
        // this metric by construction. Incremental (not just at run end) for
        // the same reason as branch coverage — a mid-solve kill on a large
        // contract must not throw away the paths already witnessed.
        // Signal-safe numerator. Bumped under reached_claims_mutex, beside the
        // insertion it counts, so the two cannot drift; read only by the kill
        // handler, which cannot walk reached_claims (a std::unordered_set,
        // possibly mid-rehash when the signal lands).
        if (is_path_cov && !is_probe_claim)
          goto_coveraget::live_F.fetch_add(1, std::memory_order_relaxed);
        if (
          is_path_cov && !is_probe_claim &&
          !goto_coveraget::path_covered_outpath.empty())
          goto_coveraget::write_path_covered_set_atomic(fmt::format(
            "mid-solve after claim {} of {}", decided_now, remaining_claims));
        // THE PAYLOAD, ON DISK, NOW. Unlike the covered set above this needs no
        // opt-in flag, because the run that lost five witnesses to an OOM was
        // not passing one and no collector in this project ever has. Written
        // here rather than at the end of the run for the only reason that
        // matters: the end of the run may not happen.
        if (
          is_path_cov && !is_probe_claim &&
          !goto_coveraget::path_ce_journal_path.empty())
          goto_coveraget::write_path_ce_journal_atomic(
            fmt::format("after claim {} of {}", decided_now, remaining_claims),
            /*complete=*/false);
        if (
          is_branch_cov && !goto_coveraget::covered_set_outpath.empty() &&
          goto_coveraget::covered_set.emplace(claim.claim_msg, claim.claim_loc)
            .second)
        {
          goto_coveraget::write_covered_set_atomic();
          // "data even on UNKNOWN", covered-set-mode numerator: one
          // more universe edge witnessed+persisted this run. A loaded
          // prior set only raises true coverage, so this running count
          // is a sound lower bound on the covered-set authoritative
          // |all_claims ∩ (covered_set ∪ reached_claims)|.
          goto_coveraget::covered_run.fetch_add(1, std::memory_order_relaxed);
        }
        // "data even on UNKNOWN", default-mode numerator: mirror the
        // canonical bmc.cpp:901 reached_claims.size() (intentionally
        // incl. non-universe entries). Under reached_claims_mutex so
        // the size() read is consistent; grows only here (erases happen
        // exclusively inside report_coverage, which re-syncs the active
        // counter afterwards).
        if (is_branch_cov)
          goto_coveraget::live_reached.store(
            reached_claims.size(), std::memory_order_relaxed);
      }

      // for verbose output of cond coverage.
      // NOT for path coverage: report_coverage_verbose only knows the four
      // legacy metrics and its else-branch is `log_error(...); abort()`, so
      // passing `--verbosity coverage:N` alongside --solidity-path-coverage
      // used to kill the run with a core dump the moment the first path was
      // witnessed. Path coverage already prints per-claim progress through the
      // PASSED/FAILED lines above.
      if (is_vb && !is_path_cov)
        report_coverage_verbose(
          claim,
          claim_sig,
          is_assert_cov,
          is_cond_cov,
          is_branch_cov,
          is_branch_func_cov,
          reached_claims,
          reached_mul_claims);
      else if (!is_cov_silent)
      {
        report_multi_property_trace(
          smt_convt::P_SATISFIABLE, witnesses, stop_reason, claim.claim_msg);
      }

      {
        std::lock_guard lock(result_mutex);
        final_result = solver_result;
      }

      // Update fail-fast-counter
      fail_fast_cnt++;

      // for kind && incr: remove verified claims
      // whenever we find a property violation, we remove the claim
      if (!is_keep_verified && (bs || fc || is))
      {
        clear_verified_claims_in_ssa(local_eq, claim, is_goto_cov);
        clear_verified_claims_in_goto(claim, is_goto_cov);
      }
    }
    else if (solver_result == smt_convt::P_UNSATISFIABLE)
      // for kind && incr: remove verified claims
      // when we find a property proven correct in
      // either forward condition or inductive step
      if (!is_keep_verified && !bs)
      {
        clear_verified_claims_in_ssa(local_eq, claim, is_goto_cov);
        clear_verified_claims_in_goto(claim, is_goto_cov);
      }
  };

  // ---- THE REPORT MUST OUTLIVE THE JOB LOOP ----
  //
  // report_coverage sits AFTER this loop and INSIDE run_thread's try
  // (bmc.cpp:2405), and the only verification-phase catch is at :2559. So an
  // allocation failure in any job unwound straight past the report: a CAUGHT
  // OOM cost the ENTIRE report, not part of it. Measured on aqua at 8 g -- 938
  // decided claims and 5 of 15 witnesses, discarded at 51.5% completion.
  //
  // The fix is to make the tail run on the exception path too, then rethrow so
  // nothing downstream sees a different control flow than before. It is
  // deliberately NOT a per-job catch that swallows the failure and continues:
  // when memory is genuinely exhausted the next job throws too, and a loop that
  // absorbs N allocation failures reports N claims as "solver-unknown" while
  // spending the rest of the budget failing. Stopping and keeping the work is
  // the honest behaviour.
  // ---- THE RESCUE MUST NOT NEED MEMORY IT NO LONGER HAS ----
  //
  // MEASURED, and it is the whole reason this exists: with the catch below in
  // place but no cushion, the aqua whole-contract run at 8 g DID reach the
  // rescue, printed "Writing a PARTIAL report with the 938 of 1822 claim(s)
  // decided so far", got as far as the [Coverage] block -- and then threw a
  // SECOND std::bad_alloc while building the JSON, because building a report
  // for 2846 claims needs tens of megabytes and the process had just failed to
  // get any. `cov-report.json` was still not written. A rescue that allocates
  // at the moment allocation is impossible is not a rescue.
  //
  // So a block is reserved BEFORE the solve and released as the first act of
  // the rescue, returning it to the allocator's free list where the report can
  // reuse it without growing the data segment (which is what --memlimit caps:
  // RLIMIT_DATA, esbmc_parseoptions.cpp:691-708).
  //
  // Deliberately NOT touched: untouched pages cost address space, which is the
  // resource under pressure, and no RSS. Sized against the measurement above --
  // aqua's 2846-claim report is ~1.6 MB of text and tens of MB of DOM, so 128
  // MiB is comfortable at 1.6% of an 8 g budget. Only allocated for path
  // coverage, which is the mode that dies this way.
  std::unique_ptr<char[]> oom_cushion;
  if (is_path_cov)
  {
    try
    {
      oom_cushion.reset(new char[128u * 1024u * 1024u]);
    }
    catch (const std::bad_alloc &)
    {
      // Already too tight to reserve. Say so rather than proceed silently: it
      // predicts that the partial report will not fit either.
      log_warning(
        "could not reserve the 128 MiB rescue cushion; if this run dies of an "
        "allocation failure the PARTIAL report may not fit in memory either");
    }
  }

  auto emit_partial = [&](const std::string &reason) {
    // FIRST, before anything that allocates.
    oom_cushion.reset();
    log_error(
      "the per-claim solve loop did not finish ({}). Writing a PARTIAL report "
      "with the {} of {} claim(s) decided so far, rather than discarding them. "
      "It is marked partial in the JSON (`partial: true`, and the same under "
      "`summary`) and on stdout; it must NOT be read as a measurement of this "
      "program",
      reason,
      decided_claims.load(),
      remaining_claims);
    // The rescue gets its own handler so that a failure INSIDE it cannot
    // replace the original reason with its own. Without this, a second
    // bad_alloc thrown while building the JSON propagates out of the catch
    // clause in place of the `throw;` below, and the log then blames the
    // report writer for a run that died in the solver -- the wrong cause,
    // reported confidently.
    try
    {
      report_simple_summary(summary);
      if (
        bs && !fc && !is && !options.get_bool_option("k-induction") &&
        !options.get_bool_option("incremental-bmc"))
        report_coverage(
          options,
          reached_claims,
          reached_mul_claims,
          pytest_gen,
          ctest_gen,
          foundry_gen,
          reason);
    }
    catch (const std::bad_alloc &)
    {
      log_error(
        "the PARTIAL report itself could not be built: there was not enough "
        "memory left to serialise it, even after releasing the rescue cushion. "
        "{} of {} claim(s) had been decided. The counterexample payloads of "
        "every path witnessed so far are still on disk in cov-ce-journal.json, "
        "which is written per witness and needs no allocation at this point; "
        "that file, NOT this run's absent cov-report.json, is what survived",
        decided_claims.load(),
        remaining_claims);
    }
    catch (...)
    {
      log_error(
        "the PARTIAL report itself failed to be written. {} of {} claim(s) had "
        "been decided; the witnesses are in cov-ce-journal.json",
        decided_claims.load(),
        remaining_claims);
    }
  };
  try
  {
    // PARALLEL
    if (options.get_bool_option("parallel-solving"))
    {
      /* NOTE: I would love to use std::for_each here, but it is not giving
       * the result I would expect. My guess is either compiler version
       * or some magic flag that we are not using.
       *
       * Nevertheless, we can achieve the same results by just creating
       * threads.
       */

      // TODO: Running everything in parallel might be a bad idea.
      //       Should we also add a thread pool?
      std::vector<std::thread> parallel_jobs;
      for (const auto &i : jobs)
        parallel_jobs.push_back(std::thread(job_function, i));

      // Main driver
      for (auto &t : parallel_jobs)
      {
        t.join();
      }
      // We could remove joined jobs from the parallel_jobs vector.
      // However, its probably not worth for small vectors.
    }
    // SEQUENTIAL
    else
      std::for_each(std::begin(jobs), std::end(jobs), job_function);
  }
  // Typed arms, not a bare catch(...), because the REASON is the point: a
  // reader of a partial report has to be able to tell an out-of-memory kill
  // from a frontend error from an internal invariant, and "unknown exception"
  // is the answer that sends them back to the log they no longer have.
  catch (const std::bad_alloc &)
  {
    emit_partial(
      "std::bad_alloc — the process ran out of memory during the "
      "per-claim solve");
    throw;
  }
  catch (const std::string &e)
  {
    emit_partial("error: " + e);
    throw;
  }
  catch (const char *e)
  {
    emit_partial(std::string("error: ") + e);
    throw;
  }
  catch (const std::exception &e)
  {
    emit_partial(std::string("exception: ") + e.what());
    throw;
  }
  catch (...)
  {
    // Reached only by a throw of a type nothing here knows about. Kept rather
    // than dropped: the alternative is that the one exception nobody
    // anticipated is also the one that costs the whole report.
    emit_partial(
      "an exception of unrecognised type escaped the per-claim "
      "solve loop");
    throw;
  }

  // show summary
  report_simple_summary(summary);

  // For coverage with fixed bound unwinding
  if (
    bs && !fc && !is && !options.get_bool_option("k-induction") &&
    !options.get_bool_option("incremental-bmc"))
    report_coverage(
      options,
      reached_claims,
      reached_mul_claims,
      pytest_gen,
      ctest_gen,
      foundry_gen);

  if (solver_error_seen.load(std::memory_order_relaxed))
    return smt_convt::P_ERROR;
  if (smtlib_seen.load(std::memory_order_relaxed))
    return smt_convt::P_SMTLIB;
  if (bs && !is_path_cov && final_result == smt_convt::P_SATISFIABLE)
    return final_result;
  return final_result;
}

void bmct::report_simple_summary(const SimpleSummary &summary) const
{
  if (options.get_bool_option("result-only"))
    return;

  // ANSI color codes
  bool is_color = options.get_bool_option("color");
  const std::string GREEN = is_color ? "\033[32m" : "";
  const std::string RED = is_color ? "\033[31m" : "";
  const std::string RESET = is_color ? "\033[0m" : "";

  // Build the properties summary string with colors
  std::ostringstream properties_oss;
  properties_oss << "Properties: " << summary.total_properties << " verified";

  if (summary.passed_properties > 0)
    properties_oss << " " << GREEN << "✓ " << summary.passed_properties
                   << " passed" << RESET;

  if (summary.skipped_properties > 0)
    properties_oss << ", " << GREEN << "✓ " << summary.skipped_properties
                   << " skipped" << RESET;

  if (summary.failed_properties > 0)
    properties_oss << ", " << RED << "✗ " << summary.failed_properties
                   << " failed" << RESET;

  if (summary.unknown_properties > 0)
  {
    const std::string YELLOW = is_color ? "\033[33m" : "";
    properties_oss << ", " << YELLOW << "? " << summary.unknown_properties
                   << " unknown" << RESET;
  }

  // Build the timing summary string
  double avg_time = summary.total_properties > 0
                      ? summary.total_time_s / summary.total_properties
                      : 0.0;

  std::ostringstream timing_oss;
  timing_oss << "Solver: " << summary.solver_name
             << " • Decision procedure total time: "
             << time2string(summary.total_time_s) << "s"
             << " • Avg: " << std::fixed << std::setprecision(1)
             << time2string(avg_time) << "s/property";

  // Output the summary
  log_result("{}", properties_oss.str());
  log_result("{}", timing_oss.str());
}
