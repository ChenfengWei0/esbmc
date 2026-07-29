#include <goto-programs/goto_functions.h>
#include <goto-programs/goto_convert_class.h>
#include <goto-programs/loop_unroll.h>
#include <langapi/language_util.h>
#include <unordered_set>
#include <array>
#include <atomic>
#include <map>
#include <mutex>

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

  // Solidity complete-path coverage (paper: entry->exit path coverage for
  // test generation). Unlike k_path_coverage (prefix witnesses asserted AT
  // the branch), this snapshots each decision's guard VALUE into a ghost
  // symbol on the decision edge (ASSIGN c_i = guard, via cov_context), then
  // asserts assert(!(c_1==d_1 && ... && c_k==d_k)) on each edge reaching
  // END_FUNCTION for every enumerated complete decision sequence. A probe
  // is falsified => that complete path is feasible => multi_property emits
  // its CE. The per-path enc(pi) is encoded into the claim comment so
  // claim_sig stays unique per path (bmc.cpp:2000 is otherwise unsound).
  // Slice 1: loop-free functions (acyclic DFS, scalar snapshots). Loops
  // (occurrence-indexed snapshot slots) are Slice 2. Requires cov_context.
  void solidity_path_coverage();

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

  // ---- Complete-path coverage: CONTENT-ADDRESSED cross-run path keys ----
  //
  // Branch coverage keys an edge by (condition text, location) — content
  // addressed already. Complete-path coverage used to key a path by its ordinal
  // `enc`, which is a position in one particular enumeration. That is unsafe
  // across runs: the decision set has changed three times (short-circuit
  // operands, the ABI non-payable gate, internal-call expansion), and each
  // change RENUMBERS every path. A key witnessed under one numbering then
  // silently designates a DIFFERENT path under the next, so a path can be
  // skipped as "already covered" when nothing has covered it — no crash, no
  // warning, coverage simply reads low. An ordinal key cannot be made safe;
  // it has to be replaced.
  //
  // The stable key is the decision SEQUENCE itself:
  //     unit signature, then per decision (site, polarity, occurrence index)
  // where `site` is the decision's SOURCE LOCATION (plus its operand index, for
  // several folded short-circuit operands sharing one location) rather than any
  // serial number. Enumeration order, added decisions elsewhere, and a changed
  // unit set then leave a path's key untouched: an old key either matches
  // exactly or does not match at all — it can never designate a different path.
  //
  // `path_covered_ids` holds the ids loaded from the previous run;
  // `path_stable_id` maps this run's claim key -> its stable id so the run-end
  // write-back can record exactly the paths that were witnessed.
  static std::set<std::string> path_covered_ids;
  static std::map<std::pair<std::string, std::string>, std::string>
    path_stable_id;
  static std::string path_covered_outpath;

  // Fail-closed guard for the file above. The stable key protects against
  // RE-NUMBERING; it cannot protect against a change that alters what a path IS
  // (different source, a decision kind added to the set, a different loop or
  // recursion bound). This fingerprint covers exactly those, and a mismatch
  // DISCARDS the cache and recomputes — deliberately with no migration path,
  // because migration logic is where this class of silent error hides.
  static std::string path_cov_fingerprint;

  // Serialise the complete-path covered set + its fingerprint (atomic publish,
  // same .tmp-then-rename discipline as write_covered_set_atomic). No-op when
  // no --coverage-covered-set was given.
  static void write_path_covered_set_atomic();

  // Was this claim's path already witnessed in an EARLIER round?
  //
  // Complete-path coverage does NOT use `covered_set` — that is the branch
  // metric's (condition, location) pair set, and solidity_path_coverage()
  // deliberately leaves it empty because an ordinal-keyed pair cannot survive a
  // re-numbering. The cross-run identity here is the content-addressed stable
  // id, so the test has to go through path_stable_id -> path_covered_ids.
  //
  // Getting this wrong is not cosmetic: a path skipped because a previous round
  // already witnessed it has NO verdict this run, so reading `covered_set`
  // (always empty) reports it as U — a path with a counterexample in hand,
  // filed under "we could not decide". Measured: with --coverage-covered-set
  // the file was never even written, so the whole cross-run mechanism was dead
  // in both directions.
  static bool
  path_witnessed_earlier(const std::pair<std::string, std::string> &claim_key);

  // ---- U REASON TOKEN: why is this path not witnessed? ----
  //
  // U is a FIRST-CLASS DELIVERABLE, not an internal diagnostic. The claim being
  // made is "every uncovered path carries a named reason; there is no
  // unexplained remainder". A U cell that can also absorb an implementation
  // defect makes that claim false — and it has absorbed one three times already
  // (ordinal-key mismatch, the RETURN exit, `rawBalances`). Each time the broken
  // case was indistinguishable from an honest solver timeout, because "we do not
  // know" is a legitimate, expected outcome.
  //
  // So every U must be classifiable, and a U that is not is a HARD FAILURE
  // rather than a quiet residue. The tokens:
  //
  //   named-obstacle      the unit is disqualified (the model admits an
  //                       execution the chain does not have). Not an unknown at
  //                       all — a declared exclusion that no verdict can change.
  //   bounded-holds       the solver proved no witness AT THIS EXPLORATION.
  //                       Honest and precise: it is not "unreachable", it is
  //                       "no witness within the declared tx/unwind bound from
  //                       the post-constructor entry state".
  //   solver-unknown      the solver returned UNKNOWN/error, or an inductive
  //                       step could not prove it. The genuine "we do not know".
  //   not-solved-this-run the claim was instrumented but never reached the
  //                       solver. The entry-liveness audit catches the case
  //                       where a WHOLE unit is in this state; this token is
  //                       exactly the per-claim residue it cannot see.
  //   unit-not-entered    the harness never entered this path's unit, so no
  //                       classification of the path itself means anything.
  //
  // ORDER MATTERS, and `unit-not-entered` must sit SECOND — directly under
  // named-obstacle and ABOVE the two tokens that would otherwise swallow it:
  //
  //   * a unit that is entered but whose claims hold vacuously gets verdict 'P'
  //     and would be filed `bounded-holds` — "no witness within the bound",
  //     which is a statement about the path when the truth is that nothing ran;
  //   * a unit whose claims were never generated at all (measured on a real
  //     benchmark: 120166 instrumented paths, ZERO verification conditions) has
  //     no verdict and would be filed `not-solved-this-run`.
  //
  // Both are strictly less informative than the real reason, so putting them
  // first loses the one fact worth having. The ordering rule is the same one
  // that puts named-obstacle at the top: when the unit is disqualified, or was
  // never entered, every finer classification of its paths is meaningless.
  //
  // This is NOT a reserved slot — it fires today. With --focus-function the
  // audit deliberately does not abort for the units the narrowing excluded, so
  // their paths reach the report; measured before the ordering was fixed, they
  // were all reported `not-solved-this-run` on a run whose own log line one
  // line earlier said the unit had not been entered.
  //
  // This is an INVARIANT ASSERTION, not a detector: the live tokens partition
  // the possible states by construction (a claim's verdict is one of
  // {none, P, U, F}, and F means witnessed, hence not U). It therefore needs no
  // fault injection — its value is as a tripwire for a future change that
  // introduces a U with no reason. THE PARTITION ARGUMENT ONLY HOLDS WHILE THE
  // CLASSIFICATION HAS NO CATCH-ALL: the implementation's `default` arm returns
  // "" so the caller aborts. Mapping `default` to any token instead would make
  // the abort dead code while everything still looked fine — do not add one when
  // a fifth verdict value appears; add an explicit case for it.
  //
  // Returns the token, or "" when the path is not a U (which callers treat as
  // the hard failure above).
  static std::string
  path_u_reason_token(const std::pair<std::string, std::string> &claim_key);

  // The token names, in report order. Printed in full every time, zeros
  // included, so a category that stops occurring is visible rather than absent.
  static const std::vector<std::string> &path_u_reason_tokens();

  // ENTRY-LIVENESS AUDIT — hard-fails when a unit's results are vacuous.
  //
  // Measured on a real benchmark (St1inch): 120166 paths were instrumented and
  // symex generated ZERO verification conditions — the harness never called any
  // unit. Every path was then reported "U", which is indistinguishable from an
  // honest solver timeout, so a completely empty run looked like a merely hard
  // one. Nothing crashed and nothing warned; the defect was found by a human
  // reading a log line.
  //
  // Two levels of the same failure, deliberately sharing ONE channel so that
  // implementing half of it cannot leave the other half silent:
  //   * a unit whose claims never reached the solver was never entered;
  //   * zero verification conditions overall is the extreme case of that, where
  //     EVERY unit was never entered.
  //
  // The witness is positive: a claim that produced a solver verdict proves the
  // unit was executed. Absence of a verdict is not read as "undecided" — for a
  // whole unit it is read as "this run says nothing about this unit", which is
  // a tool failure, not a result. Claims deliberately skipped via the cross-run
  // covered set are excluded, since not instrumenting them is intentional.
  //
  // This is also what currently makes it safe to keep `I` disabled: if
  // unreachability were ever emitted, a never-entered unit would have every one
  // of its `assert(tr != enc)` hold vacuously and be reported as PROVEN
  // INFEASIBLE — the most damaging wrong answer this pass could give. The audit
  // is the precondition for ever enabling it.
  // `focus_function` is --focus-function's value, empty when unset. With it set
  // the dispatcher deliberately calls only that entry, so every OTHER unit is
  // legitimately never entered and must not be treated as a defect — measured:
  // the first real use of this audit aborted on exactly that. The premise
  // ("a unit with instrumented claims should be entered") only holds for the
  // focused unit, so the check is narrowed to where it holds rather than
  // weakened everywhere.
  static void audit_entry_liveness(const std::string &focus_function);

  // Units the audit found had claims instrumented but NONE decided, i.e. the
  // harness never entered them — mapped to WHY, because the two causes are
  // opposite in nature and must not be collapsed:
  //
  //   "excluded by --focus-function"  INTENDED and declared. The narrowing says
  //                                   this unit is not supposed to be entered
  //                                   in this run; its paths are meant to be
  //                                   witnessed by the run that focuses on them
  //                                   and unioned via the covered set. Normal
  //                                   output, labelled `unit-not-entered`.
  //   "harness never entered it"      A DEFECT. Measured on a real benchmark:
  //                                   120166 instrumented paths, zero
  //                                   verification conditions. Hard failure.
  //
  // Keeping the reason (rather than a bare set) is what makes this usable BEYOND
  // token selection. The planned entry-liveness WITNESS — an `assert(false)` at
  // each unit's body head that must be refuted — has the rule "not refuted =>
  // hard failure", and applying that rule uniformly would abort the moment
  // anyone passes --focus-function. It must consult this same distinction:
  // SKIP the check for focus-excluded units, REQUIRE refutation for the rest.
  // Recording only "not entered" would force that layer to rediscover the split.
  //
  // Filled by audit_entry_liveness (which runs before any figure is printed).
  // Today the defect entries never survive to a reader — the audit aborts on
  // them — but both are recorded so the shape is right when that changes.
  static std::map<std::string, std::string> units_not_entered;

  // ---- Solidity complete-path coverage: tri-state (F/I/U) reporting ----
  //
  // `reached_claims` alone cannot distinguish "proven unreachable" from
  // "could not decide": both are simply absent from it. The tri-state
  // report needs the per-claim solver verdict, so multi_property_check
  // records it here as it solves (keyed by claim_sig == "msg\tloc"):
  //   'F' — refuted (P_SATISFIABLE): the path is feasible and a
  //         counterexample (concrete input) is in hand.
  //   'P' — proven (P_UNSATISFIABLE) AT THE CURRENT EXPLORATION. This is a
  //         CANDIDATE for I and nothing more: it means "no witness within this
  //         tx/unwind bound, from this entry state", NOT unreachability.
  //         NOTHING currently upgrades it. In particular --solidity-max-tx 0
  //         does NOT: coverage rewrites the dispatcher back-edge to a SKIP, so
  //         that flag explores ONE transaction — fewer than
  //         --solidity-max-tx 2. Every 'P' is therefore reported as U with
  //         `bounded_holds: true`. See path_cov_can_prove_unreachable() in
  //         bmc.cpp, which is the single place to change if a havoc'd-entry or
  //         loop-live exploration mode is ever added.
  //   'U' — undecided: solver UNKNOWN/error, or the inductive step could
  //         not prove it.
  // Written under a mutex (--parallel-solving runs jobs on threads).
  static std::map<std::string, char> claim_outcome;
  static std::mutex claim_outcome_mutex;

  // Complete paths whose exit is a detected custom-error revert
  // (`revert E()` reaching a `#sol_error` call). Keyed like all_claims, so
  // the report can label each path's exit_kind normal|revert. Filled by
  // solidity_path_coverage() at instrumentation time.
  static std::set<std::pair<std::string, std::string>> revert_paths;

  // Complete paths that exit through a ROLLBACK revert: `require(cond)` /
  // `require(cond,"msg")` / `revert("msg")` lower to a block that restores the
  // entry snapshot (`*this = _sol_save_this`) and jumps to END_FUNCTION. These
  // reach END_FUNCTION like a normal exit, so without this set they would be
  // reported `exit_kind: "normal"` — actively wrong, since the transaction
  // reverts. Unlike `revert_paths` (custom errors) the rollback IS modelled,
  // so their post-state is the correctly restored one and needs no warning.
  static std::set<std::pair<std::string, std::string>> rollback_revert_paths;

  // Paths whose exit shape cannot be classified: they reach END_FUNCTION while
  // skipping the epilogue and carry no rollback restore. A `require` failing
  // before any state write and a plain early `return` both compile to exactly
  // this, with nothing on the edge to separate them. Reported as
  // `exit_kind: "undetermined"` — labelling them "normal" would assert that a
  // reverted transaction succeeded.
  static std::set<std::pair<std::string, std::string>> undetermined_exit_paths;

  // Paths along which the MODEL AND THE EVM DISAGREE, keyed like all_claims,
  // with the value naming the obstacle.
  //
  // These are not imprecision — they are paths where a counterexample would
  // describe an execution that does not exist on-chain, so a test built from
  // one is red when run while being labelled certified. That is the single
  // failure mode this pipeline must never produce, and it is why the marking is
  // not merely reported: a marked path must be excluded from the sibling set
  // used for the stage-3 subtraction AND must not be turned into a test.
  // Marking without excluding would be worthless.
  //
  // Counted and reported as an ABSOLUTE number, never folded into a coverage
  // ratio: an obstacle is not partial credit.
  //
  // Marking is PER PATH, not per unit — a path of the same unit that never
  // walks the offending site is unaffected and stays fully usable. The rule is
  // conservative: any path whose decision sequence passes through the site is
  // marked.
  static std::map<std::pair<std::string, std::string>, std::string>
    named_obstacle_paths;

  // Paths whose certified region is NARROWER than it would otherwise be,
  // because their unit lost paths to the goal cap. Keyed like all_claims.
  //
  // This is deliberately NOT named_obstacle_paths, and the reason is mechanical.
  // Certification is a query — `assume(L <= x <= U); assert(tr == pi)` — and the
  // goal cap limits only how many EXIT ASSERTS are emitted. Phase-1 accounting
  // still updates `tr`/`cnt` on every decision of every path, dropped ones
  // included, so an input that walks a dropped path carries that path's number
  // at the exit, the query fails on it, and the candidate interval is rejected
  // and shrunk. The query never needed the dropped path to have been enumerated.
  //
  // A `require` lowered to a control-flow-free assume is the opposite case and
  // stays an obstacle: there the reverting execution does not exist in the model
  // at all, so no query can see it and no interval can be shrunk away from it.
  // Existing-but-unenumerated and non-existent are different failures, and only
  // the second can ship a test that is red on the unmodified contract.
  static std::map<std::pair<std::string, std::string>, std::string>
    truncation_weakened;

  // ---- DEGRADATION: which call points a unit gave up to fit its budget ----
  //
  // Degradation and TRUNCATION are two different mechanisms with two different
  // soundness stories, so they get two separate reports and are never merged:
  //
  //   * DEGRADATION withdraws call points BEFORE enumeration. The callee stays
  //     a call, so symex still executes it; it simply stops contributing
  //     decisions to the caller's path identity. The path classes get COARSER
  //     but still partition the input space (two different decision sequences
  //     still differ in polarity at their first differing index; execution is
  //     still deterministic), so the enumeration stays sound. What is lost is
  //     assertion STRENGTH, and it is lost at named, reported places rather
  //     than everywhere.
  //   * TRUNCATION drops enumerated paths at the goal cap. Those paths exist
  //     and symex will execute them; they are simply missing from the sibling
  //     set.
  //
  // The order is fixed: degradation fires FIRST, truncation is the last-resort
  // backstop. In the intended steady state truncation never fires at all — so
  // if it does fire, that is a result in its own right (the degradation policy
  // was not aggressive enough for that unit) and is reported as such, never
  // folded into the degradation report.
  //
  // Keyed by unit id; the value names each withdrawn call point (callee plus
  // the source location of the call), because "this unit was degraded" is not
  // actionable while "this unit stopped recording the decisions of THIS call"
  // is.
  static std::map<std::string, std::vector<std::string>> degraded_call_sites;

  // The CE payload of a witnessed (F) path: the concrete values that make the
  // path execute, harvested from the solver model while it is still live.
  // This is the half of the report a downstream generaliser actually consumes
  // — a path id alone says "reachable", these values say "with WHAT".
  //   inputs      — first nondeterministically-sourced value per symbol
  //                 (the harness's chosen call arguments / environment)
  //   final_state — LAST value written to each contract state variable
  //                 (`this->x`) on this path, i.e. the post-state
  // Both are recorded ONLY from concrete model values; a symbolic or missing
  // value is dropped rather than guessed.
  //
  // `sliced`/`compact_trace`/`payload_symbols_protected` record HOW the trace
  // was produced, because all three affect what can be harvested: the symex
  // slicer keeps only steps the CLAIM depends on, and a path claim's guard
  // mentions only the ghost accumulators, so state-variable writes would be
  // sliced away — unless the payload symbols were exempted from slicing
  // (protect_ce_symbols, which --cov-report-json turns on) or slicing was
  // switched off entirely. Without these flags an empty final_state would be
  // ambiguous between "this path writes no state" and "the writes were sliced
  // away".
  struct path_ce_t
  {
    // Solidity call arguments only: a nondet-sourced write to a symbol whose
    // mangled id has the `sol:@C@<C>@F@<f>@<name>` shape. Harness plumbing
    // (allocator tables, dispatcher choice bits, temporaries) is NOT an input
    // and is counted in `dropped_internal` instead of polluting this list.
    std::vector<std::pair<std::string, std::string>> inputs;
    // EVM environment the path was witnessed under (msg.*/tx.*/block.*),
    // kept separate because it is context, not an argument.
    std::vector<std::pair<std::string, std::string>> env;
    // How many nondet values were classified as harness-internal and dropped.
    // Reported so the omission is visible rather than silent.
    size_t dropped_internal = 0;
    // What the external calls on this path RETURNED. Under --unbound the
    // frontend models an external call as a nondet return plus a nondet
    // re-entry, so this value is chosen by the model, not by the caller —
    // keeping it out of `inputs` is the difference between a replayable test
    // and one that tries to pass an unpassable argument.
    std::vector<std::pair<std::string, std::string>> extcall_returns;
    // Contract state at the moment this path's function was ENTERED. A path
    // guarded by state that an earlier transaction established is only
    // reproducible if the entry state is known too.
    std::vector<std::pair<std::string, std::string>> entry_storage;
    // False when the entry marker was not seen in the trace (e.g. the entry
    // assignment was sliced, or the path was witnessed in an earlier round), so
    // an empty entry_storage is never read as "the contract started empty".
    bool entry_storage_known = false;
    std::vector<std::pair<std::string, std::string>> final_state;
    // State variables this path provably WROTE but whose value could not be
    // rendered as a constant (mappings / dynamic arrays lower to infinite-array
    // globals whose model value is the whole store). Listed by name so the
    // reader knows the variable changed: omitting them entirely would let a
    // consumer infer "unchanged", which is a silent wrong conclusion.
    std::vector<std::string> state_written_unrendered;
    bool sliced = true;
    bool compact_trace = true;
    // The symbols this payload is built from (contract objects, contract-scope
    // mapping/array stores, msg./tx./block.) were registered as no-slice names,
    // so `sliced == true` here does NOT mean the payload was cut down. This is
    // what makes an empty `final_state` readable as "this path writes no state"
    // rather than "the writes were sliced away".
    bool payload_symbols_protected = false;
    // The harvest stopped at this path's own violated assert, so values from
    // any LATER transaction in a multi-tx harness cannot leak in. False means
    // the assert was not found in the trace and the whole trace was scanned —
    // the post-state may then belong to a later transaction.
    bool scoped_to_claim = false;
    // This path exits through a custom-error `revert E()`, which the Solidity
    // frontend lowers to a `#sol_error` callee containing only ASSUME(false)
    // — with NO state rollback (unlike require/revert("msg"), which restore
    // `*this` from an entry snapshot). So the harvested post-state is the
    // state AT THE REVERT POINT, not the EVM post-state (on-chain every write
    // in the reverted transaction is undone). Reported, never silently used.
    bool revert_pre_rollback = false;
  };
  static std::map<std::string, path_ce_t> path_ce;

  // Each enumerated path's DECISION DEPTH, keyed like all_claims.
  //
  // Reported because the next stage cannot be driven without it: every stage-2
  // query identifies a path by `tr == enc && cnt == depth`, and `enc` alone does
  // not identify it — the `cnt` conjunct is what stops a longer path whose
  // 64-bit `tr` wrapped from firing a shorter path's claim. A driver reading
  // only the report had to be told the depth by hand, which is exactly the kind
  // of out-of-band knowledge that makes an interface not one.
  static std::map<std::pair<std::string, std::string>, uint64_t>
    path_decision_depth;

  // Item 2e: serialize covered_set to covered_set_outpath crash-safely
  // (write a .tmp then atomic rename). Called both incrementally as
  // each edge is witnessed P_SATISFIABLE (bmc.cpp) and once at run end,
  // so a mid-run kill still persists every edge proven so far and
  // bounded re-runs accumulate monotonically. No-op if outpath empty.
  static void write_covered_set_atomic();

  // "data even on UNKNOWN": an external kill (SIGALRM from esbmc's own
  // --timeout, or SIGTERM/SIGINT from timeout(1)/CI/orchestrator) lands
  // mid-solve, so report_coverage (the stdout "Branch Coverage:" line,
  // only reached at a normal conclude/exhaustion point) never runs even
  // though the numerator is already known. The signal handler reads
  // this async-signal-safe snapshot and emits a SOUND LOWER BOUND
  // before _exit. It is EXACT once any report_coverage has run (every
  // such call re-syncs the active counter to its authoritative value);
  // between the first per-claim hook and the first report it is a
  // lower bound. Handler does only atomic loads + write(2); it never
  // touches the std::set (mid-mutation under SIGALRM).
  //   branch_cov_active   — set once branch-coverage mode is live
  //   total_branch_atomic — |all_claims| (denominator), set at
  //                          instrumentation, pre-solve; mirrors
  //                          total_branch
  //   covered_set_mode    — true iff --coverage-covered-set given; it
  //                          selects which numerator the handler reads
  //   live_reached        — DEFAULT mode numerator == reached_claims
  //                          .size() (the canonical bmc.cpp:901 count,
  //                          which intentionally includes non-universe
  //                          entries); updated under reached_claims_
  //                          mutex at the per-claim hook
  //   covered_run         — COVERED-SET mode numerator == count of
  //                          universe edges newly witnessed+persisted
  //                          THIS run (covered_set.emplace().second at
  //                          the Item 2e hook). A loaded prior set only
  //                          raises true coverage, so this is a sound
  //                          lower bound on the covered-set authoritative
  //                          |all_claims ∩ (covered_set ∪ reached)|.
  static std::atomic<bool> branch_cov_active;
  static std::atomic<size_t> total_branch_atomic;
  static std::atomic<bool> covered_set_mode;
  static std::atomic<size_t> live_reached;
  static std::atomic<size_t> covered_run;

  std::string target_function = "";
  bool cov_assume_asserts = false;

  // Context for creating+registering ghost snapshot symbols in
  // solidity_path_coverage(). namespacet only exposes a const context, so
  // the pass needs a mutable contextt& to move() new symbols in (pattern:
  // assign_params_as_non_det / symbol_generator). Set by the dispatch site
  // (esbmc_parseoptions.cpp) where `context` is available; nullptr => the
  // pass aborts with an actionable error rather than silently no-op.
  contextt *cov_context = nullptr;
  // Per-unit budget on enumerated complete paths (--path-cov-max-goals).
  // TWO mechanisms are keyed off it, in a fixed order (see
  // degraded_call_sites): degradation withdraws call points until the unit
  // fits, and only if that fails does the enumeration truncate at the cap. On
  // truncation the dropped count is always reported — never a silent cut.
  size_t path_cov_max_goals = 10000;
  // Loop bound for path enumeration: each back-edge is followed at most this
  // many times per path, so complete paths are enumerated up to this many
  // loop iterations. Set from --unwind by the dispatch (default 4). Must
  // match the symex unwind bound, or enumerated paths and solver-explored
  // paths disagree.
  size_t path_cov_unwind = 4;
  // When non-empty (set from --contract), branch_coverage() instruments
  // ONLY decisions whose lexically-declaring Solidity contract equals this
  // name (location's "sol_decl_contract", stamped by the frontend). This
  // makes per-contract branch coverage count C's own source decisions
  // only; inherited/library decisions are attributed to their own
  // declaring contract and excluded. Empty => no scoping (unchanged
  // whole-unit behaviour, e.g. C/C++/no --contract).
  std::string scope_contract = "";

  // --cov-report-json: the report exists FOR the per-path counterexample
  // payload (call arguments / EVM environment / post-state). The symex slicer
  // keeps only what the CLAIM depends on, and a path claim's guard mentions
  // nothing but the ghost accumulators `tr`/`cnt` — so every contract-state
  // write and every environment read is sliced out and the payload comes back
  // empty. When this is set, solidity_path_coverage() registers exactly the
  // symbols the harvest reads (contract instance objects, contract-scope
  // mapping/dynamic-array stores, msg./tx./block.) in config.no_slice_names,
  // ESBMC's existing per-symbol slicing exemption. Slicing itself stays ON, so
  // the c2goto crypto/ABI tables and the rest of the harness plumbing are
  // still removed from the formula.
  bool protect_ce_symbols = false;

  // ---- STAGE 2: THE CERTIFICATION QUERY (--path-cov-certify <json>) ----
  //
  // Everything downstream of path enumeration rests on ONE query:
  //     assume(L <= x <= U);  assert(tr == pi)
  // "every input in the box walks path pi". It is what makes certification
  // immune to goal-cap truncation (the cap drops exit asserts, never the
  // Phase-1 `tr` accounting, so an input reaching a dropped path still carries
  // that path's number and the query rejects the box). Until now that argument
  // lived only in a comment — the query itself had no implementation at all,
  // which made it the one contributed mechanism no code had ever exercised.
  //
  // THE ASSERT GOES ON EVERY EXIT OF THE UNIT, not on pi's exit. An input in
  // the box that walks a DIFFERENT path leaves through a different exit; with
  // the assert only on pi's exit it would never be checked and the query would
  // hold vacuously — a permanently green check in the single place where being
  // green must mean something. On every exit, that same input hits
  // `tr == enc` at ITS exit, fails, and the counterexample IS the witness that
  // shrinks the box.
  //
  // Deliberately implemented as a branch at the END of solidity_path_coverage()
  // rather than a pass of its own: expansion, the ABI gate, Phase-1 accounting,
  // the `tr`-completeness invariant and both censuses must all still run. They
  // are the defences, and a certification mode that bypassed them would be
  // certifying against accounting nobody checked. It also matters that the query
  // uses the SAME `tr` the enumeration uses — if the two used different
  // accounting, the immunity-to-truncation argument would not hold.
  //
  // Empty => disabled, and the pass behaves exactly as before.
  std::string path_cov_certify_path = "";
  // Spec for the outer-box batch (see report_outer_boxes). Empty => disabled.
  std::string path_cov_outer_box_path = "";

  // Set by solidity_path_coverage() when a certification query was emitted, so
  // the reporting side can behave differently WITHOUT re-reading the CLI.
  static bool path_cov_certify_mode;
  // The coordinate names the box bounds. Kept because the witness audit below
  // needs to know what a refutation is obliged to report.
  static std::vector<std::string> path_cov_certify_box_names;
  // The box being certified, and the path's own counterexample if the driver
  // supplied it. Kept so that a REFUTATION can be turned into the next box to
  // try instead of just a verdict: the witness is the input inside the box that
  // leaves the path, so the box has to be cut on the witness's side, and the
  // path's counterexample is what says which side that is. Without both, the
  // loop has to fall back to blind bisection, which is the search that was
  // withdrawn.
  static std::vector<std::array<std::string, 3>> path_cov_certify_box;
  static std::map<std::string, std::string> path_cov_certify_ce;

  // A REFUTATION WITHOUT A WITNESS IS WORTHLESS — hard-fail on one.
  //
  // The verdict and the witness fail INDEPENDENTLY, which is what makes this
  // worth an invariant rather than a comment. Measured: decorating the claim
  // comment with a `certify:` prefix left the report's `path_function` reading
  // `certify:sol:@C@Box@F@f#18`, the counterexample harvest builds the expected
  // argument scope from that string, every nondet failed the scope test and was
  // filed as harness-internal (dropped 19 -> 25) — and the run still printed a
  // perfectly correct FAILED verdict with an empty `inputs`.
  //
  // The damage is entirely downstream: the refuting INPUT is what the box gets
  // shrunk with, so a witness-less refutation leaves the generalisation loop
  // with nothing to do. Its symptom would be "the loop does not converge" or
  // "the loop collapses to a point", several hundred lines away from a string
  // prefix. Checking it here costs one comparison; finding it there costs hours.
  //
  // `ce_payload_requested` narrows the check to where its premise holds. The
  // counterexample payload is only harvested when the report asks for it; a run
  // that never asked has no witness to be missing, and demanding one there is
  // the audit accusing the tool of a defect it does not have. MEASURED on this
  // audit's very first real use — the same false positive the entry-liveness
  // audit produced on ITS first use (with --focus-function), and the same fix:
  // narrow to where the premise holds rather than weaken the check everywhere.
  static void audit_certify_witness(bool ce_payload_requested);

  // ---- STAGE 2, step 1: THE OUTER BOX (--path-cov-outer-box <json>) ----
  //
  // The certification query answers "is this box inside D_pi?". It does not say
  // where to look, and the way NOT to find out is to widen from the CE point and
  // ask again each time — that is a search, it has no terminating condition, and
  // it sits exactly on the "too coarse fails / too fine is expensive" dilemma
  // that got the widening route withdrawn.
  //
  // Instead measure the OUTER box first: `assume(tr == pi); assert(temp_c <= U)`
  // says D_pi is CONTAINED in the box. Assumption fixed, assertions varied — so
  // an entire ladder of candidate bounds, for every coordinate and every path,
  // is judged in ONE run. Then the certified region is computed by SUBTRACTING
  // the siblings' outer boxes, which costs no query at all: path domains
  // partition the input space, so an input in nobody else's outer box must walk
  // this path.
  //
  // The ladder's SPAN comes from the sibling counterexamples — a sibling CE at
  // value v proves the boundary lies between this path's CE and v, so the probes
  // go there instead of across the whole 256-bit type. The tool does not compute
  // that span: the driver passes it in, keeping the method abstract and the tool
  // free of any dependence on its own report format.
  //
  // Resolution is stated honestly rather than implied: K non-adaptive probes in
  // one batch give resolution (hi-lo)/(K+1), NOT log(hi-lo) — that would need K
  // adaptive rounds. Refining is the driver's job, and each round is one more
  // batch.
  struct outer_box_probet
  {
    uint64_t enc = 0;
    std::string coord;
    bool upper = false; // true: `temp_c <= value`; false: `temp_c >= value`
    std::string value;
    std::pair<std::string, std::string> key; // claim key, to read the verdict
  };
  static bool path_cov_outer_box_mode;
  static std::vector<outer_box_probet> path_cov_outer_box_probes;
  // Every path enumerated for the target unit, as (enc, depth). Needed because
  // the subtraction is over SIBLINGS, so a path that got no probes still has to
  // be visible as "unmeasured" rather than silently treated as absent.
  static std::vector<std::pair<uint64_t, uint64_t>> path_cov_outer_box_paths;
  // (enc, coordinate) -> that path's counterexample value on it. Supplied by the
  // driver from the enumeration run. Needed because a subtraction cut is legal
  // only if it keeps a KNOWN member of the path's domain; without one there is
  // nothing to stop the greedy cut from carving away the real region.
  static std::map<std::pair<uint64_t, std::string>, std::string>
    path_cov_outer_box_ce;
  // Each coordinate's own TYPE range, as decimal strings. This is an outer
  // bound for free — every value of a `uint256` is in [0, 2^256-1] — and taking
  // it is not an optimisation but a correctness fix: a probe like `a >= 0` on an
  // unsigned type is a tautology, gets simplified out of the formula, and comes
  // back with NO verdict. Read as "this bound was not established" it leaves the
  // coordinate half-open and blocks the subtraction entirely, which is exactly
  // what happened before this was added. Probes then only ever TIGHTEN it.
  static std::map<std::string, std::pair<std::string, std::string>>
    path_cov_outer_box_type_range;
  // Coordinates pinned for this batch. Every measured and every subtracted
  // region is a statement about the SLICE through these values, so they are
  // printed with the region — a region measured under `bal == 0` and rendered
  // without it would be a claim about inputs that were never examined.
  //
  // Holds the pins that were ACTUALLY APPLIED. A pin naming a coordinate the
  // tool cannot express is dropped from here (and recorded below) rather than
  // left in, because this list is what the region is LABELLED with: keeping an
  // unapplied pin would print "measured under state.s == 0" for a measurement
  // in which nothing constrained `state.s`.
  static std::vector<std::pair<std::string, std::string>>
    path_cov_outer_box_pins;

  // ---- Coordinates the tool REFUSED to express, and why ----
  //
  // Two things can make a coordinate unusable, and they used to be one thing:
  // an ABORT. The name may not resolve (a mapping, a field access like
  // `immutables.taker`), or it may resolve to a value this stage cannot bound
  // (a string, a contract/interface handle, an aggregate — the SMT layer then
  // dies with "Projecting from non-tuple based AST" or a "Tuple AST mismatch"
  // assertion, i.e. a core dump instead of a recorded failure). Measured five
  // times across three projects and three different types; unrecognised types
  // are unbounded in number, so the rule is REFUSE BY DEFAULT with a whitelist
  // of what can be bounded, not accept-by-default with a crash on the unknown.
  //
  // Recorded rather than silently omitted. In the query, "the box omits c" and
  // "c is unconstrained" are the SAME constraint — so a refused coordinate that
  // simply vanished from the report would read as "measured, and it came out as
  // the whole type", which is a claim about a measurement that never happened.
  //
  // The refusal is NOT symmetric between the two stages, and the asymmetry is
  // the point:
  //   * OUTER BOX — refuse the coordinate, keep measuring the others. The box
  //     is a containment statement per coordinate; one missing coordinate costs
  //     information, not correctness. It must never be treated as a measured
  //     `[0, TYPE_MAX]` bound for THIS path, which would widen this path's own
  //     region and break the only-ever-narrower invariant.
  //   * CERTIFY — refuse the QUERY. Dropping a requested bound and answering
  //     SUCCESSFUL would certify a WIDER box than the one asked for, which is
  //     the single outcome that query exists to prevent.
  static std::map<std::string, std::string> path_cov_refused_coords;

  // Read the probe verdicts, print each path's outer box, then subtract the
  // siblings' boxes and print the certified region. Called after solving.
  static void report_outer_boxes();

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

  // Foundry revert fidelity: conservative straight-line walk from an edge's
  // first instruction. Returns true iff the edge unconditionally reaches a
  // revert terminator (a FUNCTION_CALL to a `#sol_error` function — a lowered
  // `revert CustomError(...)`) before any control-flow change (goto/return/
  // end/throw/catch) or downstream merge, so a nested conditional revert never
  // taints the enclosing edge. Used by branch_coverage() to stamp the reverting
  // edge's probe with `sol_revert_edge`; the Foundry generator reads that off
  // the covered claim to emit `vm.expectRevert()`.
  bool edge_reaches_error_revert(
    goto_programt::const_targett it,
    goto_programt::const_targett end) const;

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
