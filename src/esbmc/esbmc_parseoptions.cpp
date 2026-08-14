#include <ac_config.h>

#ifndef _WIN32
extern "C"
{
#  include <fcntl.h>
#  include <unistd.h>

#  ifdef HAVE_SENDFILE_ESBMC
#    include <sys/sendfile.h>
#  endif

#  include <sys/resource.h>
#  include <sys/time.h>
#  include <sys/types.h>
}
#else
// MSVC's pipe-capable subprocess helpers are spelt with a leading underscore.
#  define popen _popen
#  define pclose _pclose
#endif

#include <esbmc/bmc.h> // also pulls goto-programs/goto_coverage.h, whose
                       // signal-safe snapshot timeout_handler reads
                       // (goto_coverage.h has no include guard — must
                       // not be included a second time here)
#include <esbmc/esbmc_parseoptions.h>
#ifdef ENABLE_SOLIDITY_FRONTEND
#  include <solidity-frontend/solidity_tod_analysis.h>
#  include <solidity-frontend/solidity_tod_harness.h>
#endif
#include <cctype>
#include <fstream>
#include <clang-c-frontend/clang_c_language.h>
#include <util/config.h>
#include <util/filesystem.h>
// The ONE parser for --focus-function's value; --path-cov-instrument-only is
// validated as a SUBSET of it here, with the same matcher the frontend
// dispatcher and the path-coverage pass use, so the three cannot disagree.
#include <util/focus_function.h>
#include <csignal>
#include <cstdlib>
#include <limits>
#include <util/expr_util.h>
#include <iostream>
#include <atomic>
#include <mutex>
#include <thread>
#include <goto-programs/add_race_assertions.h>
#include <goto-programs/goto_atomicity_check.h>
#include <goto-programs/goto_check.h>
#include <goto-programs/goto_convert_functions.h>
#include <goto-programs/goto_inline.h>
#include <goto-programs/goto_k_induction.h>
#include <goto-programs/goto_loop_invariant.h>
#include <goto-programs/abstract-interpretation/interval_analysis.h>
#include <goto-programs/abstract-interpretation/gcse.h>
#include <goto-programs/loop_numbers.h>
#include <goto-programs/goto_binary_reader.h>
#include <goto-programs/write_goto_binary.h>
#include <goto-programs/remove_no_op.h>
#include <goto-programs/remove_unreachable.h>
#include <goto-programs/set_claims.h>
#include <goto-programs/show_claims.h>
#include <goto-programs/loop_unroll.h>
#include <goto-programs/mark_decl_as_non_det.h>
#include <goto-programs/assign_params_as_non_det.h>
#include <goto2c/goto2c.h>
#include <util/irep.h>
#include <langapi/languages.h>
#include <langapi/mode.h>
#include <memory>
#include <pointer-analysis/goto_program_dereference.h>
#include <pointer-analysis/show_value_sets.h>
#include <pointer-analysis/value_set_analysis.h>
#include <util/symbol.h>
#include <util/time_stopping.h>
#include <goto-programs/goto_cfg.h>
#include <langapi/language_util.h>
#include <goto-programs/contracts/contracts.h>

#ifndef _WIN32
#  include <sys/wait.h>
#  include <fcntl.h>
#  ifdef __GLIBC__
#    include <execinfo.h>
#  endif
#endif

#ifdef ENABLE_GOTO_CONTRACTOR
#  include <goto-programs/goto_contractor.h>
#endif

#define BT_BUF_SIZE 256

// ANSI color/style escape sequences for terminal output
#define CLR_BOLD_CYAN "\033[1;36m"
#define CLR_BOLD "\033[1m"
#define CLR_RESET "\033[0m"

extern "C" const char buildidstring_buf[];
extern "C" const unsigned int buildidstring_buf_size;

static std::string_view esbmc_version_string()
{
  return {buildidstring_buf, buildidstring_buf_size};
}

enum PROCESS_TYPE
{
  BASE_CASE,
  FORWARD_CONDITION,
  INDUCTIVE_STEP,
  NUM_CHILD_PROCESSES,
  PARENT = NUM_CHILD_PROCESSES
};

struct resultt
{
  PROCESS_TYPE type;
  uint64_t k;
};

#ifndef _WIN32
// "data even on UNKNOWN": emit the partial branch-coverage summary the
// SIGALRM kill would otherwise discard. report_coverage (the normal
// "Branch Coverage:" line) only runs at a conclude/exhaustion point;
// --timeout _exit()s mid-solve before any of them. The numerator is
// already known (goto_coveraget::live_reached, kept exactly equal to
// the authoritative reached count) and the denominator was fixed at
// instrumentation time. Strictly async-signal-safe: only atomic loads,
// stack buffer, manual unsigned->decimal, one write(2). No malloc, no
// iostream, no std::set access (it may be mid-mutation at SIGALRM).
// Shared async-signal-safe primitives for both arms below. No malloc, no
// iostream, no locale, no std::string: a signal can land inside the allocator
// or inside the log mutex, and a handler that touches either can deadlock a
// process that was about to die anyway -- turning "partial data" into "no data
// and a hang".
namespace
{
struct sigbuf
{
  char buf[512];
  size_t n = 0;
  void put(const char *s)
  {
    while (*s && n < sizeof(buf))
      buf[n++] = *s++;
  }
  void put_uint(size_t v)
  {
    char tmp[24];
    size_t t = 0;
    do
    {
      tmp[t++] = static_cast<char>('0' + v % 10);
      v /= 10;
    } while (v && t < sizeof(tmp));
    while (t && n < sizeof(buf))
      buf[n++] = tmp[--t];
  }
  void flush()
  {
    ssize_t w = write(STDOUT_FILENO, buf, n);
    (void)w; // nothing actionable in a signal handler if write() fails
    n = 0;
  }
};
} // namespace

// ---- THE PATH-COVERAGE ARM ----
//
// 27 runs across the corpus were killed at a 180 s outer timeout and every one
// of them emitted NOTHING, because the only rescue was gated on
// `branch_cov_active` -- an atomic whose sole writer is branch_coverage()
// (goto_coverage.cpp). solidity_path_coverage() wrote none of the signal-safe
// atomics, so on a path-coverage run the handler returned at its first line and
// the killed run's zero was indistinguishable, in the gate table, from a
// measured zero.
//
// A SECOND ARM, NOT A WIDENED CONDITION. The branch metric's numerator and
// denominator are meaningless here: `total_branch` counts decision edges,
// `total_paths_atomic` counts complete paths, and printing one under the
// other's heading would be a wrong number wearing the right label.
//
// WHAT IT CAN AND CANNOT DO, said in the text it prints rather than left for a
// reader to assume: it is a LOWER BOUND, it carries no counterexample payload,
// and no cov-report.json was written. The payload for the paths it counts is in
// cov-ce-journal.json when --cov-report-json was given -- which is exactly why
// the journal had to land before this arm existed.
static void emit_path_coverage_on_signal()
{
  if (!goto_coveraget::path_cov_active.load(std::memory_order_relaxed))
    return;
  const size_t total =
    goto_coveraget::total_paths_atomic.load(std::memory_order_relaxed);
  const size_t decided =
    goto_coveraget::live_decided.load(std::memory_order_relaxed);
  const size_t claims =
    goto_coveraget::claims_total_atomic.load(std::memory_order_relaxed);
  size_t f = goto_coveraget::live_F.load(std::memory_order_relaxed);
  if (f > total && total) // defensive: numerator can never exceed the universe
    f = total;

  sigbuf b;
  b.put(
    "\n[Coverage]\nReport Completeness: PARTIAL — terminated by signal "
    "before verification concluded\nComplete Paths : ");
  b.put_uint(total);
  b.put("\nClaims Decided : ");
  b.put_uint(decided);
  b.put(" of ");
  b.put_uint(claims);
  b.put("\nPath Status: F ");
  b.put_uint(f);
  b.put(
    " (partial: LOWER BOUND, no cov-report.json was written, and this line "
    "carries no counterexample payload. The payload for these paths is in "
    "cov-ce-journal.json when --cov-report-json was given)\n");
  b.flush();
}

static void emit_branch_coverage_on_timeout()
{
  emit_path_coverage_on_signal();
  if (!goto_coveraget::branch_cov_active.load(std::memory_order_relaxed))
    return;
  const size_t total =
    goto_coveraget::total_branch_atomic.load(std::memory_order_relaxed);
  if (total == 0)
    return;
  // Mode-correct numerator: covered-set runs use covered_run (sound
  // lower bound on |all_claims ∩ (covered_set ∪ reached)|); default
  // runs use live_reached (== the canonical reached_claims.size()).
  // Both are re-synced to the exact authoritative value at every
  // report_coverage, so this is exact after the first report and a
  // lower bound before it.
  size_t reached =
    goto_coveraget::covered_set_mode.load(std::memory_order_relaxed)
      ? goto_coveraget::covered_run.load(std::memory_order_relaxed)
      : goto_coveraget::live_reached.load(std::memory_order_relaxed);
  if (reached > total) // defensive: numerator can never exceed universe
    reached = total;
  const size_t pct = reached * 100 / total;

  char buf[160];
  size_t n = 0;
  auto put = [&](const char *s) {
    while (*s && n < sizeof(buf))
      buf[n++] = *s++;
  };
  auto put_uint = [&](size_t v) {
    char tmp[20];
    size_t t = 0;
    do
    {
      tmp[t++] = static_cast<char>('0' + v % 10);
      v /= 10;
    } while (v && t < sizeof(tmp));
    while (t && n < sizeof(buf))
      buf[n++] = tmp[--t];
  };
  // Same shape as report_coverage's branch-cov block so existing
  // stdout consumers (orchestrator / lcov-compare) parse it identically.
  put("\n[Coverage]\nBranches : ");
  put_uint(total);
  put("\nReached : ");
  put_uint(reached);
  put("\nBranch Coverage: ");
  put_uint(pct);
  put("% (partial: run terminated before verification concluded)\n");
  ssize_t w = write(STDOUT_FILENO, buf, n);
  (void)w; // nothing actionable in a signal handler if write() fails
}

void timeout_handler(int)
{
  // Emit the partial coverage FIRST: log_error / cleanup below are the
  // pre-existing (not strictly async-signal-safe) calls; if one hangs
  // on the allocator/log mutex the sound partial number is already on
  // stdout.
  emit_branch_coverage_on_timeout();
  log_error("Timed out");
  file_operations::cleanup_registered_tmps();
  // Use _exit to avoid atexit handlers that may deadlock the allocator
  _exit(1);
}

// "data even on UNKNOWN", external-kill arm. esbmc's own --timeout is
// SIGALRM (timeout_handler above), but external bounders — the
// regression harness (testing_tool.py STRIPS --timeout and kills the
// process group with SIGTERM, grace, then SIGKILL), `timeout(1)`, CI
// runners, and the TWO_TRACK/project-run orchestrator — terminate via
// SIGTERM (or SIGINT on ctrl-C). Without a handler the default action
// discards the already-computed partial branch coverage. Emit it in
// the SIGTERM→SIGKILL grace window using the same async-signal-safe
// path. SIGKILL is uncatchable; for that case only the Item 2e
// covered-set JSON survives (needs --coverage-covered-set). Installed
// unconditionally (the kill arrives regardless of any esbmc flag, and
// --timeout is stripped by the harness anyway).
void term_handler(int sig)
{
  // Emit FIRST (see timeout_handler): the partial number must survive
  // even if the pre-existing log_error/cleanup below hang.
  emit_branch_coverage_on_timeout();
  if (sig == SIGINT)
    log_error("Interrupted");
  else
    log_error("Terminated");
  file_operations::cleanup_registered_tmps();
  // Conventional 128+signum; _exit to skip atexit (allocator deadlock).
  _exit(sig == SIGINT ? 130 : 143);
}
#endif

#ifndef _WIN32
/* This will produce output on stderr that looks somewhat like this:
 *   Signal 6, backtrace:
 *   src/esbmc/esbmc(+0xad52e)[0x556c5dcdb52e]
 *   /lib64/libc.so.6(+0x39d50)[0x7f7a8f475d50]
 *   /lib64/libc.so.6(+0x89d9c)[0x7f7a8f4c5d9c]
 *   /lib64/libc.so.6(raise+0x12)[0x7f7a8f475ca2]
 *   /lib64/libc.so.6(abort+0xd3)[0x7f7a8f45e4ed]
 *   src/esbmc/esbmc(+0x62e3e5)[0x556c5e25c3e5]
 *   src/esbmc/esbmc(+0x61f7f1)[0x556c5e24d7f1]
 *   [...]
 *
 *   Memory map:
 *   [...]
 *
 * The backtrace can be translated into proper function symbols via addr2line,
 * e.g.
 *
 *   cat bt | tr -d '[]' | tr '()' ' ' | grep esbmc | \
 *   while read f a b; do echo $a | tr -d '+'; done | \
 *   xargs addr2line -iapfCr -e src/esbmc/esbmc
 */
static void segfault_handler(int sig)
{
  ::signal(sig, SIG_DFL);
  void *buffer[BT_BUF_SIZE];
#  ifdef __GLIBC__
  int n = backtrace(buffer, BT_BUF_SIZE);
  dprintf(STDERR_FILENO, "\nSignal %d, backtrace:\n", sig);
  backtrace_symbols_fd(buffer, n, STDERR_FILENO);
#  endif
  int fd = open("/proc/self/maps", O_RDONLY);
  if (fd != -1)
  {
    dprintf(STDERR_FILENO, "\nMemory map:\n");
    for (ssize_t rd; (rd = read(fd, buffer, sizeof(buffer))) > 0 ||
                     (rd == -1 && errno == EINTR);)
      rd = write(STDERR_FILENO, buffer, rd < 0 ? 0 : rd);
    close(fd);
  }
  ::raise(sig);
}
#endif

// This transforms a string representation of a time interval
// written in the form <number><suffix> into seconds.
// The following suffixes corresponding to time units are supported:
//
//  s - seconds,
//  m - minutes,
//  h - hours,
//  d - days.
//
// When <suffix> is empty, the default time unit is seconds.
// If <suffix> is not empty, and its final character is not in the list above,
// this method throws an error.
//
// \param str - string representation of a time interval,
// \return - number of seconds that represents the input string value.
uint64_t esbmc_parseoptionst::read_time_spec(const char *str)
{
  uint64_t mult;
  int len = strlen(str);
  if (!isdigit(str[len - 1]))
  {
    switch (str[len - 1])
    {
    case 's':
      mult = 1;
      break;
    case 'm':
      mult = 60;
      break;
    case 'h':
      mult = 3600;
      break;
    case 'd':
      mult = 86400;
      break;
    default:
      log_error("Unrecognized timeout suffix");
      abort();
    }
  }
  else
  {
    mult = 1;
  }

  uint64_t timeout = strtol(str, nullptr, 10);
  timeout *= mult;
  return timeout;
}

// This transforms a string representation of a memory limit
// written in the form <number><suffix> into megabytes.
// The following suffixes corresponding to memory size units are supported:
//
//  b - bytes,
//  k - kilobytes,
//  m - megabytes,
//  g - gigabytes.
//
// When <suffix> is empty, the default unit is megabytes.
// If <suffix> is not empty, and its final character is not in the list above,
// this method throws an error.
//
// \param str - string representation of a memory limit,
// \return - number of megabytes that represents the input string value.
uint64_t esbmc_parseoptionst::read_mem_spec(const char *str)
{
  uint64_t mult;
  int len = strlen(str);
  if (!isdigit(str[len - 1]))
  {
    switch (str[len - 1])
    {
    case 'b':
      mult = 1;
      break;
    case 'k':
      mult = 1024;
      break;
    case 'm':
      mult = 1024 * 1024;
      break;
    case 'g':
      mult = 1024 * 1024 * 1024;
      break;
    default:
      log_error("Unrecognized memlimit suffix");
      abort();
    }
  }
  else
  {
    mult = 1024 * 1024;
  }

  uint64_t size = strtol(str, nullptr, 10);
  size *= mult;
  return size;
}

static std::string format_target()
{
  const char *endian = nullptr;
  switch (config.ansi_c.endianess)
  {
  case configt::ansi_ct::IS_LITTLE_ENDIAN:
    endian = "little";
    break;
  case configt::ansi_ct::IS_BIG_ENDIAN:
    endian = "big";
    break;
  case configt::ansi_ct::NO_ENDIANESS:
    endian = "no";
    break;
  }
  assert(endian);
  const char *lib = nullptr;
  switch (config.ansi_c.lib)
  {
  case configt::ansi_ct::LIB_NONE:
    lib = "system";
    break;
  case configt::ansi_ct::LIB_FULL:
    lib = "esbmc";
    break;
  }
  assert(lib);
  std::ostringstream oss;
  oss << config.ansi_c.word_size << "-bit " << endian << "-endian "
      << config.ansi_c.target.to_string() << " with " << lib << "libc";
  return oss.str();
}

// This method creates a set of options based on the CMD arguments passed to
// ESBMC. Also, it sets some options that are used across various
// ESBMC stages but which are not available via CMD.
//
// \param options - the options object created and updated by this method.
void esbmc_parseoptionst::get_command_line_options(optionst &options)
{
  if (config.set(cmdline))
    exit(1);

  log_status("Target: {}", format_target());

  // Copy all flags that are set to non-default values in CMD into options
  options.cmdline(cmdline);
  set_verbosity_msg();

  // Resolve --color option: validate and convert to boolean
  options.set_option("color", resolve_color_option());

  if (cmdline.isset("git-hash"))
  {
    log_result("{}", esbmc_version_string());
    exit(0);
  }

  if (cmdline.isset("list-solvers"))
  {
    // Generated for us by autoconf,
    log_result("Available solvers: {}", ESBMC_AVAILABLE_SOLVERS);
    exit(0);
  }

  // Below we make some additional adjustments (e.g., adding some options
  // that are used by ESBMC at later stages but which are not available
  // through CMD, setting groups of options based depending on
  // particular CMD flags)
  if (cmdline.isset("bv"))
    options.set_option("int-encoding", false);

  if (cmdline.isset("ir"))
    options.set_option("int-encoding", true);

  if (cmdline.isset("ir-ieee"))
  {
    options.set_option("int-encoding", true);
    options.set_option("ir-ieee", true);
  }
  if (cmdline.isset("fixedbv"))
    options.set_option("fixedbv", true);
  else
    options.set_option("floatbv", true);

  if (cmdline.isset("context-bound"))
    options.set_option("context-bound", cmdline.getval("context-bound"));
  else
    options.set_option("context-bound", -1);

  if (cmdline.isset("deadlock-check"))
  {
    options.set_option("deadlock-check", true);
    options.set_option("atomicity-check", false);
  }
  else
    options.set_option("deadlock-check", false);

  if (cmdline.isset("compact-trace"))
    options.set_option("no-slice", true);

  if (
    cmdline.isset("smt-thread-guard") || cmdline.isset("smt-symex-guard") ||
    cmdline.isset("smt-symex-assert") || cmdline.isset("smt-symex-assume"))
  {
    log_status(
      "Enabling --smt-during-symex to use features that involve encoding SMT "
      "during symex");
    options.set_option("smt-during-symex", true);
  }

  // check the user's parameters to run incremental verification
  if (!cmdline.isset("unlimited-k-steps"))
  {
    // Get max number of iterations
    uint64_t max_k_step = strtoul(cmdline.getval("max-k-step"), nullptr, 10);

    // Get the increment
    uint64_t k_step_inc = strtoul(cmdline.getval("k-step"), nullptr, 10);

    // Get the start of the base-case, default 1
    uint64_t k_step_base = strtoul(cmdline.getval("base-k-step"), nullptr, 10);

    // check whether k-step is greater than max-k-step
    if (k_step_inc >= max_k_step)
    {
      log_error(
        "Please specify --k-step smaller than max-k-step if you want to use "
        "incremental verification.");
      abort();
    }

    // check whether k_step_inc is greater than max-k-step
    if (k_step_base >= max_k_step)
    {
      log_error(
        "Please specify --base-k-step smaller than max-k-step if you want "
        "to use incremental verification.");
      abort();
    }
  }

  if (cmdline.isset("coverage-multi-tx"))
  {
    // --coverage-multi-tx keeps the multi-transaction dispatcher loop LIVE in
    // Solidity coverage mode (see the neutralization site below), and the
    // Foundry generator reconstructs the ordered call sequence from that loop's
    // per-iteration dispatcher-guard markers.
    //
    // --solidity-max-tx N instead makes get_tx_bound() emit a DETERMINISTIC
    // unroll of N transaction bodies (no loop). That reconstructs unreliably —
    // the generator can mis-attribute which method ran in each transaction (it
    // emitted `fire(); fire();` where `arm(); fire();` was needed) — so the two
    // flags are incompatible: route the transaction bound through the live loop.
    if (cmdline.isset("solidity-max-tx"))
    {
      log_error(
        "--coverage-multi-tx is incompatible with --solidity-max-tx: an "
        "explicit tx bound forces a deterministic unroll whose Foundry "
        "reconstruction is unreliable. Bound the live dispatcher loop with "
        "--incremental-bmc (recommended) or --unwind N instead.");
      abort();
    }
    // The live loop is an unbounded `while(nondet_bool())`; symex needs a
    // whole-program bounding strategy or it diverges. Loop-specific bounds
    // (--unwindset / --unwindsetname) are NOT accepted here: they target a named
    // loop, not necessarily the dispatcher, so they cannot guarantee the loop is
    // bounded. Require an explicit global strategy rather than guessing.
    if (
      !cmdline.isset("unwind") && !cmdline.isset("incremental-bmc") &&
      !cmdline.isset("k-induction") && !cmdline.isset("k-induction-parallel") &&
      !cmdline.isset("termination") && !cmdline.isset("base-case") &&
      !cmdline.isset("forward-condition") && !cmdline.isset("inductive-step"))
    {
      log_error(
        "--coverage-multi-tx keeps the unbounded multi-transaction dispatcher "
        "loop live; it needs a global bounding strategy. Add --incremental-bmc "
        "(recommended: discovers the transaction depth dynamically) or "
        "--unwind N (N reaches up to ~N-1 transactions).");
      abort();
    }
  }

  // The deterministic-unroll path (--solidity-max-tx N, N>=2) reconstructs a
  // multi-transaction Foundry sequence UNRELIABLY: the generator can
  // mis-attribute which method ran in each transaction (emitting fire();fire();
  // where arm();fire(); was needed). Surface it and point at the reliable path.
  if (
    cmdline.isset("generate-foundry-testcase") &&
    cmdline.isset("solidity-max-tx") && !cmdline.isset("coverage-multi-tx"))
  {
    const long n = strtol(cmdline.getval("solidity-max-tx"), nullptr, 10);
    if (n >= 2)
      log_warning(
        "--solidity-max-tx {} with --generate-foundry-testcase reconstructs "
        "multi-transaction sequences unreliably (methods can be mis-attributed "
        "across transactions). For reliable ordered sequences use "
        "--coverage-multi-tx --incremental-bmc instead.",
        n);
  }

  if (cmdline.isset("base-case"))
  {
    options.set_option("base-case", true);
    options.set_option("no-unwinding-assertions", true);
    options.set_option("partial-loops", false);
  }

  if (cmdline.isset("forward-condition"))
  {
    options.set_option("forward-condition", true);
    options.set_option("no-unwinding-assertions", false);
    options.set_option("partial-loops", false);
    options.set_option("no-assertions", true);
  }

  if (cmdline.isset("inductive-step"))
  {
    options.set_option("inductive-step", true);
    options.set_option("no-unwinding-assertions", true);
    options.set_option("partial-loops", false);
  }

  if (cmdline.isset("validate-correctness-witness"))
  {
    const std::string witness = cmdline.getval("witness");
    const boost::filesystem::path wp(witness);
    if (wp.extension() != ".yaml" && wp.extension() != ".yml")
    {
      log_error(
        "Witness file has extension {}, expected yaml or yml.",
        wp.extension().string());
      abort();
    }
    options.set_option("validate-correctness-witness", true);
    options.set_option("witness", witness);
  }

  // --loop-invariant implicitly enables k-induction solving so that
  // do_bmc_strategy runs the full base/forward/inductive-step loop.
  if (
    cmdline.isset("loop-invariant") ||
    cmdline.isset("validate-correctness-witness"))
    options.set_option("k-induction", true);

  // Check for conflicting strategies
  if (cmdline.isset("k-induction") && cmdline.isset("termination"))
  {
    log_warning(
      "Both --k-induction and --termination specified. "
      "Using --k-induction (which does not include termination checking).");
    // Optionally disable termination flag
    options.set_option("termination", false);
  }

  // interval-symex-guard is designed for plain BMC loop-counter tracking.
  // Disable it for advanced verification modes whose GOTO/symex transformations
  // are incompatible with a single shared (non-forked) interval_domaint:
  //   - incremental-BMC reuses one goto_symext across unwind iterations
  //   - k-induction (base/forward/inductive) havocs loop variables
  //   - loop-invariant and function contracts inject havoc+assume sequences
  if (
    options.get_bool_option("k-induction") ||
    cmdline.isset("k-induction-parallel") || cmdline.isset("incremental-bmc") ||
    cmdline.isset("termination") || cmdline.isset("enforce-contract") ||
    cmdline.isset("enforce-all-contracts") ||
    cmdline.isset("replace-call-with-contract") ||
    cmdline.isset("replace-all-contracts") || cmdline.isset("base-case") ||
    cmdline.isset("forward-condition") || cmdline.isset("inductive-step"))
    options.set_option("no-interval-symex-guard", true);

  // Havoc-using modes (k-induction's 3 phases, loop-invariant transform,
  // function contracts) all rely on `make_nondet_assign`-style preambles
  // that nondet loop-modified variables.  For struct-typed lhs (e.g.
  // Solidity's `_ESBMC_Object_<C>`), nondet'ing the whole struct also
  // clobbers pointer-typed fields that hold object identity (e.g. the
  // backing buffer of `uint[3] x`).  Subsequent body writes through
  // those fields then deref nondet pointers, producing spurious deref-
  // failure VCCs and UNKNOWN.  Enabling value-set analysis is what
  // teaches the havoc preambles to (a) skip pointer-typed lhs and
  // (b) emit per-field havocs for struct-typed lhs that preserve
  // pointer fields.  Auto-enable for every mode that runs a havoc
  // transform so the fix is reachable without requiring users to layer
  // flags.  Plain BMC is unaffected: it neither runs a havoc transform
  // nor exercises the runtime hook in `symex_dereference.cpp` (which is
  // additionally gated on `inductive-step`).
  if (
    options.get_bool_option("k-induction") ||
    cmdline.isset("k-induction-parallel") || cmdline.isset("base-case") ||
    cmdline.isset("forward-condition") || cmdline.isset("inductive-step") ||
    cmdline.isset("loop-invariant") || cmdline.isset("loop-invariant-check") ||
    cmdline.isset("enforce-contract") ||
    cmdline.isset("enforce-all-contracts") ||
    cmdline.isset("replace-call-with-contract") ||
    cmdline.isset("replace-all-contracts"))
    options.set_option("add-symex-value-sets", true);

  if (
    cmdline.isset("overflow-check") || cmdline.isset("unsigned-overflow-check"))
    options.set_option("disable-inductive-step", true);

  if (cmdline.isset("ub-shift-check"))
    options.set_option("ub-shift-check", true);

  if (cmdline.isset("timeout"))
  {
#ifdef _WIN32
    log_error("Timeout unimplemented on Windows, sorry");
    abort();
#else
    const char *time = cmdline.getval("timeout");
    uint64_t timeout = read_time_spec(time);
    signal(SIGALRM, timeout_handler);
    alarm(timeout);
#endif
  }

#ifndef _WIN32
  // Unconditional (independent of --timeout, which external harnesses
  // strip): emit partial branch coverage on external SIGTERM/SIGINT
  // before the killer's SIGKILL. See term_handler.
  signal(SIGTERM, term_handler);
  signal(SIGINT, term_handler);
#endif

  if (cmdline.isset("memlimit"))
  {
#ifdef _WIN32
    log_error("Can't memlimit on Windows, sorry");
    abort();
#else
    uint64_t size = read_mem_spec(cmdline.getval("memlimit"));

    struct rlimit lim;
    lim.rlim_cur = size;
    lim.rlim_max = size;
    if (setrlimit(RLIMIT_DATA, &lim) != 0)
    {
      perror("Couldn't set memory limit");
      abort();
    }
#endif
  }

#ifndef _WIN32
  struct rlimit lim;
  if (cmdline.isset("enable-core-dump"))
  {
    lim.rlim_cur = RLIM_INFINITY;
    lim.rlim_max = RLIM_INFINITY;
    if (setrlimit(RLIMIT_CORE, &lim) != 0)
    {
      perror("Couldn't unlimit core dump size");
      abort();
    }
  }
  else
  {
    lim.rlim_cur = 0;
    lim.rlim_max = 0;
    if (setrlimit(RLIMIT_CORE, &lim) != 0)
    {
      perror("Couldn't disable core dump size");
      abort();
    }
  }
#endif

#ifndef _WIN32
  if (cmdline.isset("segfault-handler"))
  {
    signal(SIGSEGV, segfault_handler);
    signal(SIGABRT, segfault_handler);
  }
#endif

  // parallel solving activates "--multi-property"
  if (cmdline.isset("parallel-solving"))
  {
    options.set_option("base-case", true);
    options.set_option("multi-property", true);
  }

  // --all-witnesses also activates --multi-property
  if (cmdline.isset("all-witnesses"))
  {
    if (cmdline.isset("max-witnesses"))
    {
      int max_w = std::stoi(cmdline.getval("max-witnesses"));
      if (max_w < 0)
      {
        log_error("--max-witnesses must be >= 0 (got {})", max_w);
        abort();
      }
    }

    const bool was_multi = options.get_bool_option("multi-property") ||
                           cmdline.isset("multi-property");
    if (!was_multi)
      log_status("--all-witnesses: auto-enabling --multi-property");
    options.set_option("multi-property", true);
    // Don't disturb base-case if the user explicitly picked a different
    // k-induction phase (forward-condition-only or inductive-step-only).
    if (!cmdline.isset("forward-condition") && !cmdline.isset("inductive-step"))
      options.set_option("base-case", true);
  }

  // If multi-property is on, we should set base-case
  if (cmdline.isset("multi-property"))
  {
    options.set_option("base-case", true);
  }

  /* compatibility: --cvc maps to --cvc4 */
  if (cmdline.isset("cvc"))
    options.set_option("cvc4", true);

  if (cmdline.isset("log-message"))
    options.set_option("log-message", true);

  if (cmdline.isset("keep_alive_running"))
    options.set_option("keep_alive_running", true);

  if (cmdline.isset("keep-alive-interval"))
    options.set_option(
      "keep-alive-interval", cmdline.getval("keep-alive-interval"));

  if (cmdline.isset("override-return-annotation"))
    options.set_option("override-return-annotation", true);

  if (cmdline.isset("witness-output-yaml"))
  {
    std::string filename = cmdline.getval("witness-output-yaml");
    boost::filesystem::path n(filename);

    if (n.extension() == ".yaml" || n.extension() == ".yml")
    {
      // expected extension
    }
    else if (!n.has_extension())
    {
      if (n != "-")
        options.set_option("witness-output-yaml", filename + ".yml");
    }
    else
    {
      log_error(
        "Output file has extension {}, expected yaml or yml.",
        n.extension().string());
      abort();
    }
  }

  if (cmdline.isset("witness-output-graphml"))
  {
    std::string filename = cmdline.getval("witness-output-graphml");
    boost::filesystem::path n(filename);

    if (n.extension() == ".graphml")
    {
      // expected extension
    }
    else if (!n.has_extension())
    {
      if (n != "-")
        options.set_option("witness-output-graphml", filename + ".graphml");
    }
    else
    {
      log_error(
        "Output file has extension {}, expected graphml.",
        n.extension().string());
      abort();
    }
  }

  if (cmdline.isset("witness-output"))
  {
    std::string filename = cmdline.getval("witness-output");
    boost::filesystem::path n(filename);
    n.replace_extension("");

    options.set_option("witness-output-yaml", filename + ".yml");
    options.set_option("witness-output-graphml", filename + ".graphml");
  }

  if (cmdline.isset("dump-violation-info"))
    options.set_option(
      "dump-violation-info", cmdline.getval("dump-violation-info"));

  // Solidity complete-path coverage needs REVERTS to be observable rather than
  // path-pruned: a `require`/`revert` failure is one of the paths it enumerates,
  // and the legacy lowering both erases it (`__ESBMC_assume(false)`) and leaves
  // the surviving edge byte-identical to a plain early `return`, so the exit
  // cannot be classified. Publish the flag as a boolean here — BEFORE
  // `config.options` is captured — because the frontend runs long before the
  // coverage dispatch, and `get_bool_option` on the raw NULL-valued CLI flag
  // would read as false. Setting it enables the in-tree revert-observation gate
  // (solidity_convert.cpp), which is regression-locked by the assert_revert_*
  // suite and, unlike --bound, does not change external-call modelling.
  if (cmdline.isset("solidity-path-coverage"))
    options.set_option("solidity-path-coverage-enabled", true);

  if (cmdline.isset("path-cov-probe"))
  {
    const bool branch_function =
      cmdline.isset("branch-function-coverage") ||
      cmdline.isset("branch-function-coverage-claims");
    if (!cmdline.isset("solidity-path-coverage") || !branch_function)
    {
      log_error(
        "--path-cov-probe requires both --solidity-path-coverage and "
        "--branch-function-coverage. The path pass must own the branch "
        "latches; "
        "running either ordinary pass alone cannot attribute a branch-prefix "
        "counterexample to a complete path.");
      abort();
    }
    if (!cmdline.isset("all-witnesses"))
    {
      log_error(
        "--path-cov-probe requires --all-witnesses: one branch-arm witness "
        "cannot establish coordinate variation on a complete path.");
      abort();
    }
    if (
      cmdline.isset("max-witnesses") &&
      std::stoi(cmdline.getval("max-witnesses")) == 1)
    {
      log_error(
        "--path-cov-probe requires --max-witnesses 0 or at least 2; one "
        "witness "
        "cannot distinguish a singleton from an under-sampled coordinate.");
      abort();
    }
    options.set_option("solidity-path-probe-enabled", true);
  }
  else if (
    cmdline.isset("solidity-path-coverage") &&
    (cmdline.isset("branch-function-coverage") ||
     cmdline.isset("branch-function-coverage-claims")))
  {
    log_error(
      "--solidity-path-coverage and --branch-function-coverage cannot be "
      "composed directly: they mutate one GOTO program and publish "
      "incompatible "
      "claim universes. Add --path-cov-probe for the exit-latched hybrid mode, "
      "or run exactly one coverage mode.");
    abort();
  }

  // ---- --path-cov-arith-resolve: REFUSE rather than silently do nothing ----
  //
  // The mechanism re-solves a witnessed path claim with goto_check's own
  // arithmetic conditions assumed. Those conditions only EXIST if a check was
  // enabled, so without one there is nothing to assume and the flag would be a
  // no-op that looks like a fix. This tool has already shipped a function that
  // was never called and a guard that was always true; a third mechanism whose
  // absence is invisible is not acceptable.
  //
  // The dependency is stated rather than papered over. Setting the check
  // options from here would have to survive whatever the Solidity
  // standard-checks expansion does to them afterwards, and a mechanism that
  // depends on option-application ORDER is one that breaks the day the order
  // changes -- silently, in the direction of doing nothing.
  if (cmdline.isset("path-cov-arith-resolve"))
  {
    if (!cmdline.isset("solidity-path-coverage"))
    {
      log_error(
        "--path-cov-arith-resolve is only meaningful with "
        "--solidity-path-coverage: it re-solves a COMPLETE PATH claim, "
        "and no other mode emits one.");
      abort();
    }
    if (
      !cmdline.isset("overflow-check") && !cmdline.isset("div-by-zero-check") &&
      !cmdline.isset("unsigned-overflow-check"))
    {
      log_error(
        "--path-cov-arith-resolve needs the conditions it is supposed to "
        "assume, and no arithmetic check is enabled. Add --overflow-check "
        "and/or --div-by-zero-check. Refusing rather than running: with no "
        "check enabled goto_check emits no overflow / division-by-zero claim, "
        "so this flag would re-solve nothing and report a clean run -- "
        "indistinguishable from the defect being fixed.");
      abort();
    }
    options.set_option("path-cov-arith-resolve", true);
    log_status(
      "--path-cov-arith-resolve: a witnessed path whose counterexample "
      "violates an enabled arithmetic check will be re-solved ONCE with that "
      "check's own condition assumed. A non-wrapping witness replaces the "
      "wrapping one; if none exists the path is reachable only through a "
      "checked-arithmetic revert, which is counted in its own cell and NOT "
      "folded into U. The cost -- claims re-solved and seconds spent -- is "
      "printed at the end of the run rather than left to be inferred.");
  }

  config.options = options;
}

// This is the main entry point of ESBMC. Here ESBMC performs initialisation
// of the algorithms that will be run over the GOTO program at later stages
//
//  1) Parse CMD                            (see "get_command_line_options")
//  2) Create and preprocess a GOTO program (see "get_goto_functions")
//  3) Set user-specified claims            (see "set_claims")
//  4) Perform Bounded Model Checking
//    - Run a particular verification strategy if specified
//      in CMD (see "do_bmc_strategy"), or
//    - Perform a single run of Bounded Model Checking and rely
//      on the simplifier to determine the sufficient verification bound
//      (see "do_bmc")
int esbmc_parseoptionst::doit()
{
  // Configure msg output
  if (cmdline.isset("file-output"))
  {
    FILE *f = fopen(cmdline.getval("file-output"), "w+");
    /* TODO: handle failure */
    out = f;
    messaget::state.out = f;
  }

  // Print a banner with version info to stdout
  {
    FILE *output_stream = messaget::state.out;
    messaget::state.out = stdout;
    log_status(
      "ESBMC version {} {}-bit {} {}",
      ESBMC_VERSION,
      sizeof(void *) * 8,
      config.this_architecture(),
      config.this_operating_system());
    messaget::state.out = output_stream;
  }

  if (cmdline.isset("version"))
    return 0;

  // Unwinding of transition systems
  if (cmdline.isset("module") || cmdline.isset("gen-interface"))
  {
    log_error("This version has no support for hardware modules.");
    return 1;
  }

  // Preprocess the input program.
  // (This will not have any effect if OLD_FRONTEND is not enabled.)
  if (cmdline.isset("preprocess"))
  {
    preprocessing();
    return 0;
  }

  // Initialize goto_functions algorithms
  {
    // Loop unrolling
    if (cmdline.isset("goto-unwind") && !cmdline.isset("unwind"))
    {
      size_t unroll_limit = cmdline.isset("unlimited-goto-unwind") ? -1 : 1000;
      goto_preprocess_algorithms.push_back(
        std::make_unique<bounded_loop_unroller>(unroll_limit));
    }

    // Unroll intrinsic support
    goto_preprocess_algorithms.emplace_back(
      std::make_unique<apply_intrinsic_unroller>());

    // Explicitly marking all declared variables as "nondet"
    goto_preprocess_algorithms.emplace_back(
      std::make_unique<mark_decl_as_non_det>(context));

    if (cmdline.isset("function") && cmdline.isset("assign-param-nondet"))
    {
      // assign parameters to "nondet"
      goto_preprocess_algorithms.emplace_back(
        std::make_unique<assign_params_as_non_det>(context));
    }
  }

  // Run this before the main flow. This method performs its own
  // parsing and preprocessing.
  // This is an old implementation of parallel k-induction algorithm.
  // Eventually we will modify it and implement parallel version for all
  // available strategies. Just run it first before everything else
  // for now.
  if (cmdline.isset("k-induction-parallel"))
    return doit_k_induction_parallel();

  // Parse ESBMC options (CMD + set internal options)
  optionst options;
  get_command_line_options(options);

  // for solidity: detect .sol files in positional args or via --sol
  {
    bool is_solidity = cmdline.isset("sol");
    if (!is_solidity)
    {
      for (const auto &arg : cmdline.args)
      {
        if (arg.size() >= 4 && arg.substr(arg.size() - 4) == ".sol")
        {
          is_solidity = true;
          break;
        }
      }
    }
    if (is_solidity)
    {
      // Mark the run as Solidity so downstream analyses (e.g.
      // pointer-analysis VSA) can gate Solidity-specific precision
      // tweaks off this flag without reparsing cmdline.args. Mirrored
      // into the global `config.options` because analyses outside BMC's
      // local `options` scope (static analysers, value_set_domain's
      // transform, etc.) read from the config.
      //
      // Critical: this is a *distinct* option from the user-facing `sol`,
      // which is a string-valued path (--sol <contract.sol>). Reusing the
      // same name would overwrite the user's path with the string "1"
      // (boolean-true serialised into a string option), corrupting
      // contract_path downstream and — among other things — wiping out
      // the contents buffer used for line-number computation, collapsing
      // every Solidity source location to line 1.
      options.set_option("solidity-mode", true);
      config.options.set_option("solidity-mode", true);
      options.set_option(
        "no-align-check", true); // no need to check alignment in solidity
      options.set_option("no-unlimited-scanf-check", true);
      options.set_option(
        "force-malloc-success", true); // for calloc in the 'newexpression'

      // Solidity contracts are wrapped in a `while(nondet) dispatch()`
      // harness whose unwinding assertion can never be proven (the
      // condition is non-deterministic, so the loop has no static
      // bound).  As a result the k-induction forward-condition phase
      // always returns "unable to prove" for every k, burning solver
      // budget across all k iterations without contributing to a
      // proof — only the inductive step can close the verification.
      // Auto-disable forward condition in dispatcher mode (non-
      // `--function`) to recover that budget.
      // `--function` verifies a single function whose internal loops
      // CAN be bounded (`for (i=0; i<N; ++i)`), so leave forward
      // enabled there.
      if (
        !cmdline.isset("function") &&
        !cmdline.isset("enable-forward-condition"))
        options.set_option("disable-forward-condition", true);

      // `--sol <path>` is documented (in options.cpp) as equivalent to
      // a positional argument. boost::program_options parses `<path>`
      // as the VALUE of `--sol`, which does not reach `cmdline.args`;
      // the downstream `create_goto_program` sees an empty positional
      // list and bails with "Please provide a program to verify". Lift
      // the value into `cmdline.args` only when the positional list is
      // empty: the regression suite uses `--sol <display-name>
      // contract.solast` to supply a source-mapping name alongside a
      // pre-compiled AST, so we must not disturb that form (in which
      // case `cmdline.args` already has the .solast and `--sol` is a
      // label, not a source file).
      if (
        cmdline.args.empty() && cmdline.isset("sol") &&
        !std::string(cmdline.getval("sol")).empty())
      {
        cmdline.args.push_back(cmdline.getval("sol"));
      }

      // Auto-select the best SMT backend for Solidity when the user did not
      // explicitly ask for one. Z3 is significantly slower than modern QF_BV
      // engines on the 256-bit bit-vector arithmetic pervasive in Solidity
      // (uint256, mappings, etc.), so prefer Bitwuzla / CVC5 / Boolector.
      // The auto-selection also covers incremental modes (k-induction,
      // incremental-bmc, falsification): Solidity contracts model storage
      // as recursive struct datatypes which Z3 sometimes rejects with
      // "datatype is not well-founded" — Bitwuzla handles them correctly.
      const bool user_picked_solver =
        cmdline.isset("z3") || cmdline.isset("cvc5") ||
        cmdline.isset("bitwuzla") || cmdline.isset("boolector") ||
        cmdline.isset("yices") || cmdline.isset("mathsat") ||
        cmdline.isset("cvc4") || cmdline.isset("smtlib") ||
        cmdline.isset("default-solver");

      // Nested-dynamic-array shapes (`T[][]`, `T[N][][M]`, etc.) hit the
      // Phase-0 bare-smt_sort abort under default Bitwuzla because the
      // tuple flattener cannot represent `array<array<tuple, N>, ...>`.
      // CVC5 with `--cvc5-native-tuples` (commit 41878f36cb) handles them
      // via native datatype encoding. Detect the pattern in the source by
      // scanning for `[]` immediately followed by `[` — the marker of a
      // nested-dynamic-array dimension. False positives (memory or local
      // arrays) only result in CVC5 being chosen instead of Bitwuzla,
      // which is sound (just potentially slower for non-nested shapes).
      const std::string padded_solvers =
        std::string(" ") + ESBMC_AVAILABLE_SOLVERS + " ";
      const bool cvc5_available =
        padded_solvers.find(" cvc5 ") != std::string::npos;
      bool nested_dyn_detected = false;
      // A scalar-valued ≥3-level nested mapping
      // (`mapping(K1=>mapping(K2=>mapping(K3=>uint256)))`) lowers to a
      // CONST_ARRAY-initialised infinite mapping array that Bitwuzla
      // cannot equate (asymmetric `(= ca freshsym)` from IS-havoc →
      // "Equality over constant arrays not fully supported yet" abort
      // under assertion BMC; k-induction non-convergence under
      // coverage). CVC5 handles every scalar depth cleanly (regression
      // duals nested_mapping_write_{3,4}lvl_uint256_*). The
      // typeIdentifier marker is `t_mapping$_` (NOT `t_array$_`), so it
      // is detected separately below and routed to plain CVC5 (no
      // native-tuples — that flag is array-tuple-encoding-specific).
      // See notes/Results/branch_cov/STAGE5_RESIDUAL_DIAG.md.
      bool deep_mapping_detected = false;
      // Pattern B: --k-induction with multi-contract dispatch.  The
      // generated $call#0 / $call#1 dispatcher (solidity_convert_call.cpp
      // around line 2777+ / 3135+) emits a sequential O(N) if-else
      // chain on `this->$address`.  Under k-induction iteration this
      // chain is replicated per step, producing a linear chain of
      // 256-bit BV equalities that Bitwuzla's BV-quantifier engine
      // balloons on (see src/solidity-frontend/README.md:564 and memory
      // reference_cvc5_vs_bitwuzla_eoa.md).  CVC5's array+datatype
      // engine handles the chain in seconds.
      //
      // The trigger requires multi-contract AND value-call AND
      // (--bound OR --reentry-check): the dispatcher chain on
      // `this->$address` and the EOA-balance linear scan are only
      // materialized under bounded inter-contract modelling. Without
      // --bound / --reentry-check, ESBMC nondeterministically models
      // external calls and there is no chain to be amplified by
      // k-induction iteration, so CVC5's array+datatype advantage
      // evaporates and Bitwuzla (or the default fallback) handles the
      // residual VC at parity. A multi-contract test without
      // value-call (e.g. inheritance with direct method calls)
      // doesn't materialize the EOA-fallback path, so the dispatcher
      // chain stays small and Bitwuzla is fine. Conversely, a
      // value-call test without --k-induction is BMC-mode and either
      // has too few EOA scans for the chain to matter (single-EOA:
      // Bitwuzla wins) or is best handled with explicit --cvc5
      // (multi-EOA UNSAT: case-by-case). See memory
      // reference_solidity_solver_auto_hint.md "Why NOT auto-route
      // bare value-call".
      int contract_decl_count = 0;
      bool value_call_detected = false;
      if (!user_picked_solver && cvc5_available)
      {
        // Detection strategy:
        //
        //   (1) Prefer a `.solast` file in cmdline.args — it carries
        //       precise `typeIdentifier` strings.  Scan for three
        //       consecutive `t_array$_t_array$_t_array$_` substrings,
        //       the unambiguous marker of ≥3-dimensional array types
        //       (where the flattener produces bare smt_sorts that
        //       trip the Phase-0 abort under default Bitwuzla).
        //
        //   (2) Fall back to `.sol` source scanning only if no
        //       `.solast` is present.  Source-level bracket counting
        //       can't reliably distinguish type declarations from
        //       index expressions, so the fallback is conservative —
        //       it requires three consecutive `[]` (truly empty
        //       brackets, the most reliable type-level marker).
        const std::string ext_solast = ".solast";
        const std::string ext_sol = ".sol";
        std::string solast_path, sol_path;
        for (const auto &arg : cmdline.args)
        {
          if (
            arg.size() >= ext_solast.size() &&
            arg.compare(
              arg.size() - ext_solast.size(), ext_solast.size(), ext_solast) ==
              0)
          {
            solast_path = arg;
          }
          else if (
            arg.size() >= ext_sol.size() &&
            arg.compare(arg.size() - ext_sol.size(), ext_sol.size(), ext_sol) ==
              0)
          {
            sol_path = arg;
          }
        }

        const std::string &scan_path =
          !solast_path.empty() ? solast_path : sol_path;
        const bool scanning_solast = !solast_path.empty();

        std::ifstream f(scan_path);
        if (f.is_open())
        {
          static const std::string ta_marker = "t_array$_";
          int ta_run = 0;
          size_t ta_match_pos = 0;
          static const std::string tm_marker = "t_mapping$_";
          int tm_run = 0;
          size_t tm_match_pos = 0;
          // A struct-VALUED nested mapping (≥2 levels) needs CVC5 too: its
          // K≥2 array-of-struct zero-init lowers to per-field const-arrays
          // Bitwuzla cannot equate.  `t_struct$_` trails the `t_mapping$_`
          // markers within the same typeIdentifier.
          static const std::string ts_marker = "t_struct$_";
          size_t ts_match_pos = 0;
          int empty_bracket_run = 0;
          static const std::string mp_marker = "mapping(";
          size_t mp_match_pos = 0;
          int mapping_chain_run = 0; // .sol: consecutive `mapping(`
          int prev_emit = -1; // last non-whitespace char (.sol scan only)
          bool in_line = false, in_block = false, in_str = false;
          char str_quote = '\0';
          int c;
          while ((c = f.get()) != EOF)
          {
            char ch = static_cast<char>(c);
            // Comment/string stripping only for .sol source — .solast
            // is JSON where typeIdentifier values live INSIDE double
            // quotes; stripping them would skip the markers we need.
            if (!scanning_solast)
            {
              if (in_line)
              {
                if (ch == '\n')
                  in_line = false;
                continue;
              }
              if (in_block)
              {
                if (ch == '*' && f.peek() == '/')
                {
                  f.get();
                  in_block = false;
                }
                continue;
              }
              if (in_str)
              {
                if (ch == '\\')
                {
                  f.get();
                  continue;
                }
                if (ch == str_quote)
                  in_str = false;
                continue;
              }
              if (ch == '/' && f.peek() == '/')
              {
                f.get();
                in_line = true;
                continue;
              }
              if (ch == '/' && f.peek() == '*')
              {
                f.get();
                in_block = true;
                continue;
              }
              if (ch == '"' || ch == '\'')
              {
                in_str = true;
                str_quote = ch;
                continue;
              }
            }

            if (scanning_solast)
            {
              // Sliding match on `t_array$_` markers in
              // typeIdentifier strings.  Three consecutive markers
              // ⇒ array of array of array of ... (≥3 nesting).
              if (ch == ta_marker[ta_match_pos])
              {
                ++ta_match_pos;
                if (ta_match_pos == ta_marker.size())
                {
                  ++ta_run;
                  ta_match_pos = 0;
                  if (ta_run >= 3)
                  {
                    nested_dyn_detected = true;
                    break;
                  }
                }
              }
              else
              {
                ta_match_pos = (ch == ta_marker[0]) ? 1 : 0;
                if (ta_match_pos == 0)
                  ta_run = 0;
              }
              // Count `t_mapping$_` markers WITHIN ONE typeIdentifier
              // JSON string.  A nested mapping
              // `mapping(K1=>mapping(K2=>mapping(K3=>V)))` lowers to a
              // single typeIdentifier `t_mapping$_<K1>_$_t_mapping$_
              // <K2>_$_t_mapping$_<V>...` — the markers are SEPARATED by
              // key/value types (unlike the array case's adjacent
              // `t_array$_t_array$_`), so the run must NOT reset on the
              // intra-string gap; it resets only at the `"` string
              // boundary.  This confines the count to one type, so
              // three *separate* 1-level mappings (three distinct
              // typeIdentifier strings) never reach 3 — only a genuine
              // ≥3-level nested mapping does.
              if (ch == '"')
              {
                tm_run = 0;
                tm_match_pos = 0;
                ts_match_pos = 0;
              }
              else
              {
                // ≥3 nested mapping levels (any leaf): Bitwuzla const-array
                // abort on the scalar-leaf init.
                if (ch == tm_marker[tm_match_pos])
                {
                  ++tm_match_pos;
                  if (tm_match_pos == tm_marker.size())
                  {
                    tm_match_pos = 0;
                    if (++tm_run >= 3)
                    {
                      deep_mapping_detected = true;
                      break;
                    }
                  }
                }
                else
                  tm_match_pos = (ch == tm_marker[0]) ? 1 : 0;

                // ≥2 nested mapping levels with a STRUCT leaf: the K≥2
                // array-of-struct SoA zero-init emits per-field const-arrays
                // Bitwuzla cannot equate — route to CVC5 as well.
                if (ch == ts_marker[ts_match_pos])
                {
                  ++ts_match_pos;
                  if (ts_match_pos == ts_marker.size())
                  {
                    ts_match_pos = 0;
                    if (tm_run >= 2)
                    {
                      deep_mapping_detected = true;
                      break;
                    }
                  }
                }
                else
                  ts_match_pos = (ch == ts_marker[0]) ? 1 : 0;
              }
            }
            else
            {
              // .sol fallback: count CONSECUTIVE empty brackets
              // `[][][]` etc.  Three-or-more in a row marks a real
              // 3+D type declaration; index expressions like
              // `a[i][j]` or `a[0][0][0]` have non-empty inner brackets
              // and don't bump the run.  Whitespace between `]` and
              // `[` is allowed.
              if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r')
                continue;
              if (prev_emit == '[' && ch == ']')
              {
                ++empty_bracket_run;
                if (empty_bracket_run >= 3)
                {
                  nested_dyn_detected = true;
                  break;
                }
              }
              else if (ch != '[' || prev_emit != ']')
              {
                // Not part of `]\[` or `[]` chain → reset.
                if (!(prev_emit == ']' && ch == '['))
                  empty_bracket_run = 0;
              }
              // .sol fallback for ≥3-level nested mapping: count
              // `mapping(` tokens in a contiguous run.  A real nested
              // mapping `mapping(K1=>mapping(K2=>mapping(K3=>V)))`
              // never contains `;{}` between its `mapping(` tokens, so
              // those reset the run.  Conservative over-approx (perf-
              // only false positives, acceptable per this detector's
              // documented design); the .solast arm is the precise
              // path the regression suite exercises.
              if (ch == ';' || ch == '{' || ch == '}')
              {
                mapping_chain_run = 0;
                mp_match_pos = 0;
              }
              else if (ch == mp_marker[mp_match_pos])
              {
                ++mp_match_pos;
                if (mp_match_pos == mp_marker.size())
                {
                  mp_match_pos = 0;
                  if (++mapping_chain_run >= 3)
                  {
                    deep_mapping_detected = true;
                    break;
                  }
                }
              }
              else
                mp_match_pos = (ch == mp_marker[0]) ? 1 : 0;
              prev_emit = ch;
            }
          }
        }

        // Pattern B contract count: prefer .sol (text-greppable with
        // word-boundary check); fall back to .solast (JSON) when .sol
        // isn't given as a positional argument — which happens when
        // testing_tool.py invokes ESBMC with `--sol contract.sol`
        // (a flag, not positional) plus `contract.solast` (positional).
        if (sol_path.empty() && !solast_path.empty())
        {
          // .solast fallback: each contract definition appears as
          // `"contractKind":"contract"` in the JSON.  No comment/string
          // filtering needed — JSON has neither, and the marker only
          // appears as a key/value pair.  Note: this counts both regular
          // contracts and abstract contracts (both use "contract"), but
          // not interfaces (`"contractKind":"interface"`) or libraries
          // (`"contractKind":"library"`) — matching .sol semantics.
          std::ifstream lf(solast_path);
          if (lf.is_open())
          {
            static const std::string p_kind = "\"contractKind\":\"contract\"";
            // Value-call markers in solidity solast typeIdentifier
            // strings: transfer/send for built-in address calls;
            // barecall_payable for low-level `.call{value:}`.
            static const std::string p_xfer = "t_function_transfer";
            static const std::string p_send_id = "t_function_send";
            static const std::string p_bare = "t_function_barecall_payable";
            size_t mp_kind = 0, mp_xfer = 0, mp_send = 0, mp_bare = 0;
            auto step =
              [](char ch, size_t &pos, const std::string &pat) -> bool {
              if (ch == pat[pos])
                ++pos;
              else
                pos = (ch == pat[0]) ? 1 : 0;
              if (pos == pat.size())
              {
                pos = 0;
                return true;
              }
              return false;
            };
            int c;
            while ((c = lf.get()) != EOF)
            {
              char ch = static_cast<char>(c);
              if (step(ch, mp_kind, p_kind))
                ++contract_decl_count;
              if (step(ch, mp_xfer, p_xfer))
                value_call_detected = true;
              if (step(ch, mp_send, p_send_id))
                value_call_detected = true;
              if (step(ch, mp_bare, p_bare))
                value_call_detected = true;
            }
          }
        }
        else if (!sol_path.empty())
        {
          std::ifstream sf(sol_path);
          if (sf.is_open())
          {
            static const std::string p_contract = "contract";
            static const std::string p_xfer = ".transfer(";
            static const std::string p_send_d = ".send(";
            static const std::string p_call = ".call{";
            size_t mp_contract = 0;
            size_t mp_xfer = 0, mp_send = 0, mp_call = 0;
            char prev_for_contract = '\0';
            bool sin_line = false, sin_block = false, sin_str = false;
            char sstr_quote = '\0';
            int sc;
            while ((sc = sf.get()) != EOF)
            {
              char ch = static_cast<char>(sc);
              if (sin_line)
              {
                if (ch == '\n')
                  sin_line = false;
                continue;
              }
              if (sin_block)
              {
                if (ch == '*' && sf.peek() == '/')
                {
                  sf.get();
                  sin_block = false;
                }
                continue;
              }
              if (sin_str)
              {
                if (ch == '\\')
                {
                  sf.get();
                  continue;
                }
                if (ch == sstr_quote)
                  sin_str = false;
                continue;
              }
              if (ch == '/' && sf.peek() == '/')
              {
                sf.get();
                sin_line = true;
                continue;
              }
              if (ch == '/' && sf.peek() == '*')
              {
                sf.get();
                sin_block = true;
                continue;
              }
              if (ch == '"' || ch == '\'')
              {
                sin_str = true;
                sstr_quote = ch;
                continue;
              }

              // Value-call detection (sliding match, no boundary check
              // needed — the leading `.` is the boundary).
              auto step = [ch](size_t &pos, const std::string &pat) -> bool {
                if (ch == pat[pos])
                  ++pos;
                else
                  pos = (ch == pat[0]) ? 1 : 0;
                if (pos == pat.size())
                {
                  pos = 0;
                  return true;
                }
                return false;
              };
              if (step(mp_xfer, p_xfer))
                value_call_detected = true;
              if (step(mp_send, p_send_d))
                value_call_detected = true;
              if (step(mp_call, p_call))
                value_call_detected = true;

              // `contract` keyword count: must be a whole word —
              // preceded by non-identifier char (or start-of-file) AND
              // followed by non-identifier char.  Filters out usage as
              // an identifier substring (rare but possible in import
              // paths etc., already string-stripped above).
              if (mp_contract == 0)
              {
                bool boundary =
                  (prev_for_contract == '\0' ||
                   !(std::isalnum(
                       static_cast<unsigned char>(prev_for_contract)) ||
                     prev_for_contract == '_'));
                if (boundary && ch == p_contract[0])
                  mp_contract = 1;
              }
              else if (ch == p_contract[mp_contract])
              {
                ++mp_contract;
                if (mp_contract == p_contract.size())
                {
                  int nx = sf.peek();
                  bool word_end =
                    (nx == EOF ||
                     !(std::isalnum(static_cast<unsigned char>(nx)) ||
                       nx == '_'));
                  if (word_end)
                    ++contract_decl_count;
                  mp_contract = 0;
                }
              }
              else
              {
                mp_contract = (ch == p_contract[0]) ? 1 : 0;
              }

              prev_for_contract = ch;
            }
          }
        }
      }

      // Pattern B fires only when ALL four signals align:
      //   (1) k-induction iteration multiplier
      //   (2) ≥2 contracts (dispatcher chain materializes)
      //   (3) value-call (transfer/send/.call{value:}) — the EOA
      //       fallback path that makes the chain hot
      //   (4) --bound or --reentry-check: the `this->$address` chain
      //       and the EOA-balance linear scan are only materialized
      //       under bounded inter-contract modelling. Without either,
      //       external calls are nondet, the chain doesn't exist, and
      //       CVC5's array+datatype advantage evaporates — Bitwuzla
      //       (or the default fallback order) handles the residual
      //       VC at parity. (--reentry-check internally enables
      //       --bound through the reentry harness.)
      // Without any of (1)..(4), Bitwuzla handles the workload fine.
      const bool kind_multi_contract_detected =
        cmdline.isset("k-induction") &&
        (cmdline.isset("bound") || cmdline.isset("reentry-check")) &&
        contract_decl_count >= 2 && value_call_detected;

      if (!user_picked_solver)
      {
        const char *chosen = nullptr;
        if (nested_dyn_detected)
        {
          // Force CVC5 for nested-dyn-array shapes (Bitwuzla aborts).
          chosen = "cvc5";
          if (!cmdline.isset("no-cvc5-native-tuples"))
            options.set_option("cvc5-native-tuples", true);
        }
        else if (deep_mapping_detected)
        {
          // ≥3-level nested mapping with a scalar leaf.  Bitwuzla
          // aborts on the CONST_ARRAY-initialised infinite mapping
          // array; CVC5's array engine handles it.  Plain CVC5 — NO
          // native-tuples (that is array-tuple-encoding-specific;
          // mirrors Pattern B's plain-CVC5 selection below).
          chosen = "cvc5";
        }
        else if (kind_multi_contract_detected)
        {
          // Pattern B: k-induction-amplified linear if-else chain on
          // 256-bit address equality.  Bitwuzla balloons; CVC5 fast.
          // No native-tuples — that's nested-array-specific encoding.
          chosen = "cvc5";
        }
        else
        {
          const char *preferred[] = {"bitwuzla", "cvc5", "boolector", "z3"};
          for (const char *name : preferred)
          {
            if (
              padded_solvers.find(std::string(" ") + name + " ") !=
              std::string::npos)
            {
              chosen = name;
              break;
            }
          }
        }
        if (chosen)
        {
          options.set_option("default-solver", chosen);
          if (nested_dyn_detected)
          {
            const bool native_on = !cmdline.isset("no-cvc5-native-tuples");
            log_status(
              "Solidity: detected nested-dynamic-array shape; auto-selecting "
              "'cvc5'{} (Bitwuzla's tuple flattener cannot encode "
              "`T[][]`-style storage). Override with --bitwuzla / --z3 "
              "or pass --no-cvc5-native-tuples to disable native-tuple "
              "encoding.",
              native_on ? " with --cvc5-native-tuples" : "");
          }
          else if (deep_mapping_detected)
          {
            log_status(
              "Solidity: detected >=3-level nested-mapping shape (or a "
              ">=2-level struct-valued mapping); auto-selecting 'cvc5' "
              "(Bitwuzla aborts on the CONST_ARRAY-initialised infinite "
              "mapping array — \"Equality over constant arrays not fully "
              "supported\" under assertion BMC, k-induction non-convergence "
              "under coverage). Override with --bitwuzla / --z3 / "
              "--boolector.");
          }
          else if (kind_multi_contract_detected)
          {
            const char *bound_reason =
              cmdline.isset("bound") ? "--bound" : "--reentry-check";
            log_status(
              "Solidity: detected --k-induction with multi-contract "
              "dispatch ({} contracts) AND value-routing call "
              "(.transfer/.send/.call{{value:}}) under {}; auto-selecting "
              "'cvc5' (Bitwuzla balloons on the k-induction-amplified "
              "linear if-else chain over 256-bit address equality "
              "materialized in bounded inter-contract mode). Override "
              "with --bitwuzla / --z3 / --boolector.",
              contract_decl_count,
              bound_reason);
          }
          else
          {
            log_status(
              "Solidity: auto-selecting '{}' as SMT backend (Z3 is much "
              "slower on 256-bit bit-vector arithmetic). Override with "
              "--z3 / --cvc5 / --bitwuzla / --boolector or --default-solver.",
              chosen);
          }
        }
      }
    }
  }

#ifdef ENABLE_SOLIDITY_FRONTEND
  // TOD harness generation is driven by one of two flags:
  //   --tod-balance-check  -> balance-TOD: pair shares address(this).balance
  //   --tod-race-check     -> storage-race TOD: pair shares a non-balance var
  // Each flag takes "auto" (discover pairs) or "f1,f2" (specific pair).
  if (cmdline.isset("tod-balance-check") || cmdline.isset("tod-race-check"))
  {
    if (cmdline.isset("tod-balance-check") && cmdline.isset("tod-race-check"))
    {
      log_error(
        "--tod-balance-check and --tod-race-check are mutually exclusive");
      return 1;
    }
    const bool balance_mode = cmdline.isset("tod-balance-check");
    const char *tod_flag =
      balance_mode ? "tod-balance-check" : "tod-race-check";
    const solidity_tod::Mode tod_mode = balance_mode
                                          ? solidity_tod::Mode::BalanceOnly
                                          : solidity_tod::Mode::RaceOnly;
    const char *mode_tag = balance_mode ? "balance" : "race";
    // ---- shared: locate .sol / .solast paths ----
    std::string sol_path, solast_path;
    if (cmdline.isset("sol"))
      sol_path = cmdline.getval("sol");
    for (const auto &arg : cmdline.args)
    {
      if (arg.size() >= 4 && arg.substr(arg.size() - 4) == ".sol")
      {
        if (sol_path.empty())
          sol_path = arg;
      }
      else if (arg.size() >= 7 && arg.substr(arg.size() - 7) == ".solast")
        solast_path = arg;
    }
    if (sol_path.empty())
    {
      log_error("--{} requires a .sol source file", tod_flag);
      return 1;
    }

    std::ifstream sol_file(sol_path);
    if (!sol_file.is_open())
    {
      log_error("Cannot open source file: {}", sol_path);
      return 1;
    }
    std::string sol_source(
      (std::istreambuf_iterator<char>(sol_file)),
      std::istreambuf_iterator<char>());

    if (solast_path.empty())
    {
      solast_path = sol_path + "ast";
      std::string cmd =
        "solc --ast-compact-json " + sol_path + " > " + solast_path;
      if (cmdline.isset("solc-bin"))
        cmd = std::string(cmdline.getval("solc-bin")) + " --ast-compact-json " +
              sol_path + " > " + solast_path;
      if (system(cmd.c_str()) != 0)
      {
        log_error("solc failed: {}", cmd);
        return 1;
      }
    }

    std::ifstream ast_file(solast_path);
    if (!ast_file.is_open())
    {
      log_error("Cannot open AST file: {}", solast_path);
      return 1;
    }
    nlohmann::json ast;
    {
      std::string line, json_block;
      while (getline(ast_file, line))
        if (line.find(".sol =======") != std::string::npos)
          break;
      while (getline(ast_file, line))
      {
        if (line.find(".sol =======") != std::string::npos)
          break;
        json_block += line + "\n";
      }
      if (json_block.empty())
      {
        log_error("No JSON block found in {}", solast_path);
        return 1;
      }
      ast = nlohmann::json::parse(json_block);
    }

    std::string contract_name;
    if (cmdline.isset("contract"))
      contract_name = cmdline.getval("contract");
    if (contract_name.empty())
    {
      log_error("TOD: --contract is required to identify the target contract");
      return 1;
    }

    // ---- decide pair list ----
    std::vector<std::pair<std::string, std::string>> pairs;
    std::string tod_val = cmdline.getval(tod_flag);
    bool auto_mode = (tod_val == "auto");

    if (auto_mode)
    {
      const nlohmann::json *cdef = nullptr;
      if (ast.contains("nodes"))
        for (const auto &n : ast["nodes"])
          if (
            n.value("nodeType", "") == "ContractDefinition" &&
            n.value("name", "") == contract_name)
          {
            cdef = &n;
            break;
          }
      if (!cdef)
      {
        log_error(
          "--{}: contract '{}' not found in AST", tod_flag, contract_name);
        return 1;
      }
      auto candidates =
        solidity_tod::find_tod_candidates(*cdef, tod_mode, &ast);
      log_status(
        "--{}: discovered {} candidate pair(s) in '{}'",
        tod_flag,
        candidates.size(),
        contract_name);
      for (const auto &p : candidates)
      {
        log_status("  - {} vs {}", p.func_a, p.func_b);
        pairs.emplace_back(p.func_a, p.func_b);
      }
      if (pairs.empty())
      {
        log_status(
          "--{}: no {} TOD pairs detected — done.", tod_flag, mode_tag);
        return 0;
      }
    }
    else
    {
      auto comma = tod_val.find(',');
      if (comma == std::string::npos)
      {
        log_error(
          "--{} expects 'auto' or two comma-separated function names",
          tod_flag);
        return 1;
      }
      pairs.emplace_back(tod_val.substr(0, comma), tod_val.substr(comma + 1));
    }

    // Each pair -> its own .sol file (one TOD_<a>_<b> contract per file).
    TodHarnessMode harness_mode =
      balance_mode ? TodHarnessMode::Balance : TodHarnessMode::Race;
    std::string sol_dir;
    {
      auto slash = sol_path.find_last_of("/\\");
      sol_dir = (slash == std::string::npos) ? std::string(".")
                                             : sol_path.substr(0, slash);
    }
    auto harness_path_for = [&](const std::string &fa, const std::string &fb) {
      return sol_dir + "/tod_" + mode_tag + "_" + fa + "_" + fb +
             "_harness.sol";
    };

    // --dump-harness: print the first pair and exit.
    if (cmdline.isset("dump-harness"))
    {
      std::string harness = generate_tod_harness(
        sol_source,
        ast,
        contract_name,
        pairs[0].first,
        pairs[0].second,
        harness_mode);
      if (harness.empty())
        return 1;
      std::cout << harness;
      return 0;
    }

    // ---- single pair: generate file, verify in-process ----
    if (!auto_mode)
    {
      std::string harness = generate_tod_harness(
        sol_source,
        ast,
        contract_name,
        pairs[0].first,
        pairs[0].second,
        harness_mode);
      if (harness.empty())
        return 1;
      std::string harness_path =
        harness_path_for(pairs[0].first, pairs[0].second);
      std::ofstream out(harness_path);
      if (!out.is_open())
      {
        log_error("Cannot write harness to {}", harness_path);
        return 1;
      }
      out << harness;
      out.close();
      log_status("TOD harness written to {}", harness_path);

      std::string harness_contract =
        "TOD_" + pairs[0].first + "_" + pairs[0].second;
      cmdline.args.clear();
      cmdline.args.push_back(harness_path);
      config.cname = harness_contract;
      options.set_option("contract", harness_contract);
      options.set_option("bound", true);
      options.set_option("no-standard-checks", true);
      // The generated TOD harness drives two transactions in two orders via
      // the dispatcher loop; it must use the unbounded harness, not the
      // bounded-by-default unroll (which would hide the ordering race).
      options.set_option("solidity-max-tx", "0");
      config.options = options;
    }
    // ---- auto: one .sol per pair, one subprocess per .sol ----
    else
    {
      std::string esbmc = executable_path.string();
      // --solidity-max-tx 0: the TOD harness needs the unbounded dispatcher
      // loop to explore both transaction orderings (bounded-by-default would
      // hide the race).
      std::string forwarded =
        " --bound --no-standard-checks --solidity-max-tx 0";
      if (cmdline.isset("unwind"))
        forwarded += std::string(" --unwind ") + cmdline.getval("unwind");
      if (cmdline.isset("no-unwinding-assertions"))
        forwarded += " --no-unwinding-assertions";
      if (cmdline.isset("incremental-bmc"))
        forwarded += " --incremental-bmc";
      if (cmdline.isset("max-k-step"))
        forwarded +=
          std::string(" --max-k-step ") + cmdline.getval("max-k-step");
      for (const char *flag :
           {"cvc5", "bitwuzla", "boolector", "z3", "yices", "mathsat"})
        if (cmdline.isset(flag))
          forwarded += std::string(" --") + flag;

      // Materialise every harness file first so workers only spawn ESBMC.
      std::vector<std::string> harness_paths(pairs.size());
      std::vector<bool> harness_ok(pairs.size(), false);
      for (size_t i = 0; i < pairs.size(); ++i)
      {
        const auto &p = pairs[i];
        std::string harness = generate_tod_harness(
          sol_source, ast, contract_name, p.first, p.second, harness_mode);
        if (harness.empty())
        {
          log_error(
            "--{}: harness generation failed for {}/{}",
            tod_flag,
            p.first,
            p.second);
          continue;
        }
        std::string hp = harness_path_for(p.first, p.second);
        std::ofstream out(hp);
        if (!out.is_open())
        {
          log_error("Cannot write harness to {}", hp);
          continue;
        }
        out << harness;
        harness_paths[i] = hp;
        harness_ok[i] = true;
      }

      unsigned int jobs = 0;
      if (cmdline.isset("tod-jobs"))
        jobs = std::stoul(cmdline.getval("tod-jobs"));
      if (jobs == 0)
      {
        unsigned hw = std::thread::hardware_concurrency();
        if (hw == 0)
          hw = 1;
        jobs = std::min<unsigned>(hw, pairs.size());
      }
      if (jobs == 0)
        jobs = 1;
      log_status(
        "--{}: launching {} parallel ESBMC job(s) over {} pair(s)",
        tod_flag,
        jobs,
        pairs.size());

      std::atomic<size_t> next_idx{0};
      std::atomic<size_t> fail_count{0}, succ_count{0}, error_count{0};
      std::mutex failed_mutex;
      std::vector<std::string> failed_pairs;

      auto worker = [&]() {
        for (;;)
        {
          size_t i = next_idx.fetch_add(1);
          if (i >= pairs.size())
            return;
          if (!harness_ok[i])
          {
            error_count.fetch_add(1);
            continue;
          }
          const auto &p = pairs[i];
          std::string c = "TOD_" + p.first + "_" + p.second;
          std::string cmd = esbmc + " " + harness_paths[i] + forwarded +
                            " --contract " + c + " 2>&1";
          log_status("--{}: verifying {} ({})", tod_flag, c, harness_paths[i]);
          FILE *fp = popen(cmd.c_str(), "r");
          if (!fp)
          {
            log_error("popen failed for: {}", cmd);
            error_count.fetch_add(1);
            continue;
          }
          std::string output;
          char buf[4096];
          while (fgets(buf, sizeof(buf), fp))
            output += buf;
          pclose(fp);

          if (output.find("VERIFICATION SUCCESSFUL") != std::string::npos)
          {
            log_status("  -> {} SUCCESSFUL", c);
            succ_count.fetch_add(1);
          }
          else if (output.find("VERIFICATION FAILED") != std::string::npos)
          {
            log_fail(
              "  -> {} FAILED  (TOD vulnerability between {} and {})",
              c,
              p.first,
              p.second);
            fail_count.fetch_add(1);
            std::lock_guard<std::mutex> lk(failed_mutex);
            failed_pairs.push_back(c);
          }
          else
          {
            log_error("  -> {} ERROR (no verdict)", c);
            error_count.fetch_add(1);
          }
        }
      };

      std::vector<std::thread> threads;
      threads.reserve(jobs);
      for (unsigned t = 0; t < jobs; ++t)
        threads.emplace_back(worker);
      for (auto &th : threads)
        th.join();

      log_status(
        "--{} summary: {} pair(s) — {} clean, {} TOD found, {} error",
        tod_flag,
        pairs.size(),
        succ_count.load(),
        fail_count.load(),
        error_count.load());
      if (!failed_pairs.empty())
      {
        log_fail("TOD-vulnerable pairs:");
        for (const auto &c : failed_pairs)
          log_fail("  - {}", c);
      }
      return (fail_count.load() > 0 || error_count.load() > 0) ? 1 : 0;
    }
  }
#endif

  // Create and preprocess a GOTO program
  if (get_goto_program(options, goto_functions))
    return 6;

  // Output claims about this program
  // (Fedor: should be moved to the output method perhaps)
  if (cmdline.isset("show-claims"))
  {
    const namespacet ns(context);
    show_claims(ns, goto_functions);
    return 0;
  }

  // Set user-specified claims
  // (Fedor: should be moved to the preprocessing method perhaps)
  if (set_claims(goto_functions))
    return 7;

  // Leave without doing any Bounded Model Checking
  if (options.get_bool_option("skip-bmc"))
    return 0;

  // Now run one of the chosen strategies
  if (
    cmdline.isset("termination") || cmdline.isset("incremental-bmc") ||
    cmdline.isset("falsification") || cmdline.isset("k-induction") ||
    cmdline.isset("loop-invariant"))
    return do_bmc_strategy(options, goto_functions);

  // If no strategy is chosen, just rely on the simplifier
  // and the flags set through CMD
  bmct bmc(goto_functions, options, context);
  return do_bmc(bmc);
}

// This is the parallel version of k-induction algorithm.
// This is an old implementation and should be revisited sometime in the
// future.
int esbmc_parseoptionst::doit_k_induction_parallel()
{
#ifdef _WIN32
  log_error("Windows does not support parallel kind");
  abort();
#else
  // Pipes for communication between processes
  int forward_pipe[2], backward_pipe[2];

  // Process type
  PROCESS_TYPE process_type = PARENT;

  if (pipe(forward_pipe))
  {
    log_status("\nPipe Creation Failed, giving up.");
    _exit(1);
  }

  if (pipe(backward_pipe))
  {
    log_status("\nPipe Creation Failed, giving up.");
    _exit(1);
  }

  /* Set file descriptor non-blocking */
  fcntl(
    backward_pipe[0], F_SETFL, fcntl(backward_pipe[0], F_GETFL) | O_NONBLOCK);

  pid_t children_pid[3];
  short num_p = 0;

  // We need to fork 3 times: one for each step
  for (unsigned p = 0; p < 3; ++p)
  {
    pid_t pid = fork();

    if (pid == -1)
    {
      log_status("\nFork Failed, giving up.");
      _exit(1);
    }

    // Child process
    if (!pid)
    {
      process_type = PROCESS_TYPE(p);
      break;
    }
    // Parent process

    children_pid[p] = pid;
    ++num_p;
  }

  if (process_type == PARENT && num_p != 3)
  {
    log_error("Child processes were not created sucessfully.");
    abort();
  }

  optionst options;

  if (process_type != PARENT)
  {
    // Get full set of options
    get_command_line_options(options);

    // Generate goto functions and set claims
    if (get_goto_program(options, goto_functions))
      return 6;

    if (cmdline.isset("show-claims"))
    {
      const namespacet ns(context);
      show_claims(ns, goto_functions);
      return 0;
    }

    if (set_claims(goto_functions))
      return 7;
  }

  // Get max number of iterations
  uint64_t max_k_step = cmdline.isset("unlimited-k-steps")
                          ? UINT_MAX
                          : strtoul(cmdline.getval("max-k-step"), nullptr, 10);

  // Get the increment
  uint64_t k_step_inc = strtoul(cmdline.getval("k-step"), nullptr, 10);

  // Get the start of the base-case, default 1
  uint64_t k_step_base = strtoul(cmdline.getval("base-k-step"), nullptr, 10);
  if (k_step_base >= max_k_step)
  {
    log_error(
      "Please specify --base-k-step smaller than max-k-step if you want "
      "to use incremental verification.");
    abort();
  }

  // All processes were created successfully
  switch (process_type)
  {
  case PARENT:
  {
    // Communication to child processes
    close(forward_pipe[1]);
    close(backward_pipe[0]);

    struct resultt a_result;
    bool finished[NUM_CHILD_PROCESSES] = {};
    bool intentionally_killed[NUM_CHILD_PROCESSES] = {};
    const char *process_name[NUM_CHILD_PROCESSES] = {
      "base case", "forward condition", "inductive step"};
    uint64_t solution[NUM_CHILD_PROCESSES] = {
      max_k_step, max_k_step, max_k_step};

    // Keep reading until we find an answer
    while (
      !(finished[BASE_CASE] && finished[FORWARD_CONDITION] &&
        finished[INDUCTIVE_STEP]))
    {
      // Perform read and interpret the number of bytes read
      bool valid_read = true;
      int read_size = read(forward_pipe[0], &a_result, sizeof(resultt));
      if (read_size != sizeof(resultt))
      {
        if (read_size == 0)
        {
          // Client hung up; check child status but don't interpret result.
          valid_read = false;
        }
        else
        {
          // Invalid size read.
          log_error("Short read communicating with kinduction children");
          log_error("Size {}, expected {}", read_size, sizeof(resultt));
          abort();
        }
      }

      // Check if any child process has terminated
      for (int i = 0; i < NUM_CHILD_PROCESSES; i++)
      {
        if (finished[i])
          continue;

        int status;
        pid_t result = waitpid(children_pid[i], &status, WNOHANG);
        if (result <= 0)
          continue;

        if (intentionally_killed[i] || WIFEXITED(status))
        {
          finished[i] = true;
        }
        else if (WIFSIGNALED(status))
        {
          log_warning(
            "{} process was terminated by signal {:d}.",
            process_name[i],
            WTERMSIG(status));
          std::fill(finished, finished + NUM_CHILD_PROCESSES, true);
        }
      }

      if (!valid_read)
        continue;

      switch (a_result.type)
      {
      case BASE_CASE:
      case FORWARD_CONDITION:
      case INDUCTIVE_STEP:
        finished[a_result.type] = true;
        solution[a_result.type] = a_result.k;
        break;

      default:
        log_error("Message from unrecognized k-induction child process");
        abort();
      }

      // If either the base case found a bug or the forward condition
      // finds a solution, present the result
      if (
        finished[BASE_CASE] && (solution[BASE_CASE] != 0) &&
        (solution[BASE_CASE] != max_k_step))
        break;

      // If the either the forward condition or inductive step finds a
      // solution, first check if base case couldn't find a bug in that code,
      // if there is no bug, inductive step can present the result
      if (
        finished[FORWARD_CONDITION] && (solution[FORWARD_CONDITION] != 0) &&
        (solution[FORWARD_CONDITION] != max_k_step))
      {
        // If base case finished, then we can present the result
        if (finished[BASE_CASE])
          break;

        // Otherwise, kill the inductive step process
        intentionally_killed[INDUCTIVE_STEP] = true;
        kill(children_pid[INDUCTIVE_STEP], SIGKILL);

        // And ask base case for a solution

        // Struct to keep the result
        struct resultt r = {process_type, 0};

        r.k = solution[FORWARD_CONDITION];

        // Write result
        auto const len = write(backward_pipe[1], &r, sizeof(r));
        assert(len == sizeof(r) && "short write");
        (void)len; //ndebug
      }

      else if (
        finished[INDUCTIVE_STEP] && (solution[INDUCTIVE_STEP] != 0) &&
        (solution[INDUCTIVE_STEP] != max_k_step))
      {
        // If base case finished, then we can present the result
        if (finished[BASE_CASE])
          break;

        // Otherwise, kill the forward condition process
        intentionally_killed[FORWARD_CONDITION] = true;
        kill(children_pid[FORWARD_CONDITION], SIGKILL);

        // And ask base case for a solution

        // Struct to keep the result
        struct resultt r = {process_type, 0};

        r.k = solution[INDUCTIVE_STEP];

        // Write result
        auto const len = write(backward_pipe[1], &r, sizeof(r));
        assert(len == sizeof(r) && "short write");
        (void)len; //ndebug
      }
    }

    for (int i : children_pid)
      kill(i, SIGKILL);

    // Check if a solution was found by the base case
    if (
      finished[BASE_CASE] && (solution[BASE_CASE] != 0) &&
      (solution[BASE_CASE] != max_k_step))
    {
      log_result(
        "\nBug found by the base case (k = {})\nVERIFICATION FAILED",
        solution[BASE_CASE]);
      return true;
    }

    // Check if a solution was found by the forward condition
    if (
      finished[FORWARD_CONDITION] && (solution[FORWARD_CONDITION] != 0) &&
      (solution[FORWARD_CONDITION] != max_k_step))
    {
      // We should only present the result if the base case finished
      // and haven't crashed (if it crashed, solution will be UINT_MAX)
      if (finished[BASE_CASE] && (solution[BASE_CASE] != max_k_step))
      {
        log_success(
          "\nSolution found by the forward condition; "
          "all states are reachable (k = {:d})\n"
          "VERIFICATION SUCCESSFUL",
          solution[FORWARD_CONDITION]);
        return false;
      }
    }

    // Check if a solution was found by the inductive step
    if (
      finished[INDUCTIVE_STEP] && (solution[INDUCTIVE_STEP] != 0) &&
      (solution[INDUCTIVE_STEP] != max_k_step))
    {
      // We should only present the result if the base case finished
      // and haven't crashed (if it crashed, solution will be UINT_MAX)
      if (finished[BASE_CASE] && (solution[BASE_CASE] != max_k_step))
      {
        log_success(
          "\nSolution found by the inductive step "
          "(k = {:d})\n"
          "VERIFICATION SUCCESSFUL",
          solution[INDUCTIVE_STEP]);
        return false;
      }
    }

    // Couldn't find a bug or a proof for the current depth
    log_fail("\nVERIFICATION UNKNOWN");
    return false;
  }

  case BASE_CASE:
  {
    // Set that we are running base case
    options.set_option("base-case", true);
    options.set_option("forward-condition", false);
    options.set_option("inductive-step", false);

    options.set_option("no-unwinding-assertions", true);
    options.set_option("partial-loops", false);

    // Start communication to the parent process
    close(forward_pipe[0]);
    close(backward_pipe[1]);

    // Struct to keep the result
    struct resultt r = {process_type, 0};

    // Run bmc and only send results in two occasions:
    // 1. A bug was found, we send the step where it was found
    // 2. It couldn't find a bug
    for (uint64_t k_step = k_step_base; k_step <= max_k_step;
         k_step += k_step_inc)
    {
      bmct bmc(goto_functions, options, context);
      bmc.options.set_option("unwind", integer2string(k_step));

      log_progress("Checking base case, k = {:d}\n", k_step);

      // If an exception was thrown, we should abort the process
      int res = smt_convt::P_ERROR;
      try
      {
        res = do_bmc(bmc);
      }
      catch (...)
      {
        break;
      }

      // Send information to parent if no bug was found
      if (res == smt_convt::P_SATISFIABLE)
      {
        r.k = k_step;

        // Write result
        auto const len = write(forward_pipe[1], &r, sizeof(r));
        assert(len == sizeof(r) && "short write");
        (void)len; //ndebug

        log_status("Base case process finished (bug found).\n");
        return true;
      }

      // Check if the parent process is asking questions

      // Perform read and interpret the number of bytes read
      struct resultt a_result;
      int read_size = read(backward_pipe[0], &a_result, sizeof(resultt));
      if (read_size != sizeof(resultt))
      {
        if (read_size == 0)
        {
          // Client hung up; continue on, but don't interpret the result.
          continue;
        }
        if (read_size == -1 && errno == EAGAIN)
        {
          // No data available yet
          continue;
        }
        else
        {
          // Invalid size read.
          log_error("Short read communicating with kinduction parent");
          log_error("Size {}, expected {}", read_size, sizeof(resultt));

          abort();
        }
      }

      // We only receive messages from the parent
      assert(a_result.type == PARENT);

      // If the value being asked is greater or equal the current step,
      // then we can stop the base case. It can be equal, because we
      // have just checked the current value of k
      if (a_result.k < k_step)
        break;

      // Otherwise, we just need to check the base case for k = a_result.k
      max_k_step = a_result.k + k_step_inc;
    }

    // Send information to parent that a bug was not found
    r.k = 0;

    auto const len = write(forward_pipe[1], &r, sizeof(r));
    assert(len == sizeof(r) && "short write");
    (void)len; //ndebug

    log_status("Base case process finished (no bug found).\n");
    return false;
  }

  case FORWARD_CONDITION:
  {
    // Set that we are running forward condition
    options.set_option("base-case", false);
    options.set_option("forward-condition", true);
    options.set_option("inductive-step", false);

    options.set_option("no-unwinding-assertions", false);
    options.set_option("partial-loops", false);
    options.set_option("no-assertions", true);

    // Start communication to the parent process
    close(forward_pipe[0]);
    close(backward_pipe[1]);

    // Struct to keep the result
    struct resultt r = {process_type, 0};

    // Run bmc and only send results in two occasions:
    // 1. A proof was found, we send the step where it was found
    // 2. It couldn't find a proof
    for (uint64_t k_step = k_step_base + 1; k_step <= max_k_step;
         k_step += k_step_inc)
    {
      bmct bmc(goto_functions, options, context);
      bmc.options.set_option("unwind", integer2string(k_step));

      log_status("Checking forward condition, k = {:d}", k_step);

      // If an exception was thrown, we should abort the process
      int res = smt_convt::P_ERROR;
      try
      {
        res = do_bmc(bmc);
      }
      catch (...)
      {
        break;
      }

      if (options.get_bool_option("disable-forward-condition"))
        break;

      // Send information to parent if no bug was found
      if (res == smt_convt::P_UNSATISFIABLE)
      {
        r.k = k_step;

        // Write result
        auto const len = write(forward_pipe[1], &r, sizeof(r));
        assert(len == sizeof(r) && "short write");
        (void)len; //ndebug

        log_status("Forward condition process finished (safety proven).");
        return false;
      }
    }

    // Send information to parent that it couldn't prove the code
    r.k = 0;

    auto const len = write(forward_pipe[1], &r, sizeof(r));
    assert(len == sizeof(r) && "short write");
    (void)len; //ndebug

    log_status("Forward condition process finished (safety not proven).");
    return true;
  }

  case INDUCTIVE_STEP:
  {
    // Set that we are running inductive step
    options.set_option("base-case", false);
    options.set_option("forward-condition", false);
    options.set_option("inductive-step", true);

    options.set_option("no-unwinding-assertions", true);
    options.set_option("partial-loops", true);

    // Start communication to the parent process
    close(forward_pipe[0]);
    close(backward_pipe[1]);

    // Struct to keep the result
    struct resultt r = {process_type, 0};

    // Run bmc and only send results in two occasions:
    // 1. A proof was found, we send the step where it was found
    // 2. It couldn't find a proof
    for (uint64_t k_step = k_step_base + 1; k_step <= max_k_step;
         k_step += k_step_inc)
    {
      bmct bmc(goto_functions, options, context);

      bmc.options.set_option("unwind", integer2string(k_step));

      log_status("Checking inductive step, k = {:d}", k_step);

      // If an exception was thrown, we should abort the process
      int res = smt_convt::P_ERROR;
      try
      {
        res = do_bmc(bmc);
      }
      catch (...)
      {
        break;
      }

      if (options.get_bool_option("disable-inductive-step"))
        break;

      // Send information to parent if no bug was found
      if (res == smt_convt::P_UNSATISFIABLE)
      {
        r.k = k_step;

        // Write result
        auto const len = write(forward_pipe[1], &r, sizeof(r));
        assert(len == sizeof(r) && "short write");
        (void)len; //ndebug

        log_status("Inductive process finished (safety proven).");
        return false;
      }
    }

    // Send information to parent that it couldn't prove the code
    r.k = 0;

    auto const len = write(forward_pipe[1], &r, sizeof(r));
    assert(len == sizeof(r) && "short write");
    (void)len; //ndebug

    log_status("Inductive process finished (safety not proven).");
    return true;
  }

  default:
    assert(0 && "Unknown process type.");
  }

#endif

  return 0;
}

// This method iteratively applies one of the verification strategies
// for different unwinding bounds up to the specified maximum depth.
//
// ESBMC features 4 verification strategies:
//
//  1) Incremental
//  2) Termination
//  3) Falsification
//  4) k-induction
//
// Applying a strategy in this context means solving a particular sequence
// of decision problems from the list below for the given unwinding bound k:
//
//  - Base case             (see "is_base_case_violated")
//  - Forward condition     (see "does_forward_condition_hold")
//  - Inductive step        (see "is_inductive_step_violated")
//
// \param options - options for setting the verification strategy
// and controlling symbolic execution
// \param goto_functions - GOTO program under verification
int esbmc_parseoptionst::do_bmc_strategy(
  optionst &options,
  goto_functionst &goto_functions)
{
  // Get max number of iterations
  uint64_t max_k_step = cmdline.isset("unlimited-k-steps")
                          ? UINT_MAX
                          : strtoul(cmdline.getval("max-k-step"), nullptr, 10);

  // Get the increment
  unsigned k_step_inc = strtoul(cmdline.getval("k-step"), nullptr, 10);

  // Get the start of the base-case, default 1
  unsigned k_step_base = strtoul(cmdline.getval("base-k-step"), nullptr, 10);

  // For pytest test generation
  pytest_generator pytest_gen;

  // For ctest test generation
  ctest_generator ctest_gen;

  // For Foundry (*.t.sol) test generation
  foundry_generator foundry_gen;

  if (k_step_base >= max_k_step)
  {
    log_error(
      "Please specify --base-k-step smaller than max-k-step if you want "
      "to use incremental verification.");
    abort();
  }

  // Track whether any violation was found across all k steps.
  // In multi-property mode the loop continues past a violation to check
  // remaining properties, so we must remember the failure for the final verdict.
  bool any_violation_found = false;

  // Helper: emit the final verdict and return the correct exit code once a
  // proof or refutation has been found.  In multi-property mode the loop may
  // have continued past an earlier violation, so we must return 1 even when
  // the closing step (FC/IS) itself succeeds — but only when the user
  // explicitly asked for multi-property (per-claim) reporting.
  //
  // --parallel-solving flips "multi-property" on internally (see the
  // parseoptions setup) purely so the BC round can dispatch per-property
  // queries across solver threads; the caller's intent is still "verify
  // the program", not "report every bug".  For that implicit case we keep
  // the historical verdict (UNKNOWN on exhaustion), leaving the
  // EXPLICIT-MP path as the only one that converts a recorded violation
  // into FAILED via this helper.
  const bool mp_explicit = cmdline.isset("multi-property");
  auto conclude = [&]() -> int {
    // In coverage mode violations are expected; always report success.
    if (any_violation_found && !is_coverage)
    {
      if (mp_explicit)
      {
        log_fail("\nVERIFICATION FAILED");
        return 1;
      }
      // Implicit MP (parallel-solving): historical verdict was UNKNOWN.
      // Emit it here because the short-circuit path above returns before
      // reaching the loop's UNKNOWN fall-through at the end of this fn.
      log_fail("VERIFICATION UNKNOWN");
      return 0;
    }
    return 0;
  };

  // Under --multi-property, when FC or IS succeed, the remaining
  // assertions are GLOBALLY safe (FC: no paths longer than k exist;
  // IS: inductive invariant holds on the remaining claims).  Marking
  // them ensures the next iteration's multi_property_check sees
  // nothing to verify and exits cleanly via the (B)-style
  // "all claims resolved" short-circuit below.
  auto mark_all_asserts_safe = [](goto_functionst &funcs) {
    for (auto &f : funcs.function_map)
      for (auto &i : f.second.body.instructions)
        if (i.is_assert())
          i.make_skip();
  };

  // Count assertions that are still live.  Used as the "all claims
  // resolved" signal in multi-property mode: once both violated and
  // proven claims have been make_skip'd, a zero count means there's
  // nothing left for the outer k-loop to check.  Kept as a fallback
  // for the small-program case where the entire goto body actually
  // drains; for larger programs whose goto contains unreachable
  // asserts (other contract methods not in --focus-function, library
  // models), this count never reaches 0 and the outer loop relies on
  // the "no new violation this round" signal instead.
  auto count_active_asserts = [](const goto_functionst &funcs) -> size_t {
    size_t n = 0;
    for (const auto &f : funcs.function_map)
      for (const auto &i : f.second.body.instructions)
        if (i.is_assert())
          ++n;
    return n;
  };

  // Whether --multi-property mode is active (checked once to avoid
  // redundant cmdline.isset lookups in the per-k body).
  const bool mp_active = cmdline.isset("multi-property") ||
                         options.get_bool_option("multi-property");

  // Trying all bounds from 1 to "max_k_step" in "k_step_inc"
  uint64_t last_k_step = k_step_base;
  for (uint64_t k_step = k_step_base; k_step <= max_k_step;
       k_step += k_step_inc)
  {
    last_k_step = k_step;
    // k-induction
    if (options.get_bool_option("k-induction"))
    {
      bool is_bcv =
        is_base_case_violated(options, goto_functions, k_step, &foundry_gen)
          .is_true();
      if (is_bcv)
      {
        any_violation_found = true;
        // Suppress spurious VERIFICATION SUCCESSFUL from report_result at
        // subsequent k steps where no new violations are found.
        options.set_option("kind-violation-found", true);
      }

      if (is_bcv && !mp_active)
        return 1;

      // Multi-property short-circuit: if all assertions have been decided
      // (violated-and-cleared by multi_property_check, or otherwise
      // simplified), there is nothing left to prove.  Before this check
      // existed, the k-loop kept running FC/IS on an empty formula up to
      // max_k_step, yielding the misleading "VERIFICATION UNKNOWN" verdict
      // even after every claim had already been resolved.
      if (mp_active && count_active_asserts(goto_functions) == 0)
      {
        log_status("[Multi-property] all claims resolved at k = {:d}", k_step);
        if (is_coverage)
          report_coverage(
            options,
            goto_functions.reached_claims,
            goto_functions.reached_mul_claims,
            pytest_gen,
            ctest_gen,
            foundry_gen);
        return conclude();
      }

      // Forward condition.  Without MP, skip when BC already found a bug
      // (saves a round-trip but is otherwise equivalent).  With MP, keep
      // running FC so it can discharge the remaining (non-violated)
      // claims as GLOBALLY safe — marking them via mark_all_asserts_safe
      // so the next iteration sees zero active asserts and terminates
      // cleanly.
      if (!is_bcv || mp_active)
      {
        if (does_forward_condition_hold(options, goto_functions, k_step)
              .is_false())
        {
          if (is_coverage)
            goto_coveraget::path_cov_k_induction_proved = true;
          if (mp_active)
            mark_all_asserts_safe(goto_functions);
          if (is_coverage)
            report_coverage(
              options,
              goto_functions.reached_claims,
              goto_functions.reached_mul_claims,
              pytest_gen,
              ctest_gen,
              foundry_gen);
          return conclude();
        }
      }

      // Inductive step.  Same rationale as FC under MP: discharge safe
      // remaining claims.  Skipped at k=1 (no induction premise).
      if (k_step > 1 && (!is_bcv || mp_active))
      {
        if (is_inductive_step_violated(options, goto_functions, k_step)
              .is_false())
        {
          if (is_coverage)
            goto_coveraget::path_cov_k_induction_proved = true;
          if (mp_active)
            mark_all_asserts_safe(goto_functions);
          if (is_coverage)
            report_coverage(
              options,
              goto_functions.reached_claims,
              goto_functions.reached_mul_claims,
              pytest_gen,
              ctest_gen,
              foundry_gen);
          return conclude();
        }
      }
    }
    // termination
    if (options.get_bool_option("termination"))
    {
      if (does_forward_condition_hold(options, goto_functions, k_step)
            .is_false())
        return 0;

      /* Disable this for now as it is causing more than 100 errors on SV-COMP
      if(!is_inductive_step_violated(options, goto_functions, k_step))
        return false;
      */
    }
    // incremental-bmc
    if (options.get_bool_option("incremental-bmc"))
    {
      bool is_bcv =
        is_base_case_violated(options, goto_functions, k_step, &foundry_gen)
          .is_true();
      if (is_bcv)
      {
        any_violation_found = true;
        options.set_option("kind-violation-found", true);
      }

      if (is_bcv && !mp_active)
        return 1;

      if (mp_active && count_active_asserts(goto_functions) == 0)
      {
        log_status("[Multi-property] all claims resolved at k = {:d}", k_step);
        if (is_coverage)
          report_coverage(
            options,
            goto_functions.reached_claims,
            goto_functions.reached_mul_claims,
            pytest_gen,
            ctest_gen,
            foundry_gen);
        return conclude();
      }

      if (!is_bcv || mp_active)
      {
        if (does_forward_condition_hold(options, goto_functions, k_step)
              .is_false())
        {
          if (mp_active)
            mark_all_asserts_safe(goto_functions);
          if (is_coverage)
            report_coverage(
              options,
              goto_functions.reached_claims,
              goto_functions.reached_mul_claims,
              pytest_gen,
              ctest_gen,
              foundry_gen);
          return conclude();
        }
      }
    }
    // falsification
    if (options.get_bool_option("falsification"))
    {
      if (is_base_case_violated(options, goto_functions, k_step, &foundry_gen)
            .is_true())
        return 1;
    }
  }

  if (
    options.get_bool_option("multi-property") &&
    options.get_bool_option("k-induction"))
    diagnose_unknown_properties(options, goto_functions, last_k_step);

  // Exhaustion semantics under --multi-property + --k-induction.
  //
  // Reaching max_k_step without FC/IS holding means: the program's
  // remaining (non-violated) claims could NOT be proven safe within
  // our budget.  The fact that earlier k rounds found per-claim
  // violations is a side effect — those violations are recorded and
  // listed for the user — but the AGGREGATE verdict for this run is
  // UNKNOWN, not FAILED, because we never closed the safety question
  // for the outstanding claims.
  //
  // (Contrast with plain BMC: any violation → FAILED, because BMC is
  //  a bug-finder that stops on the first violation.  In k-induction
  //  we are trying to PROVE safety; not proving it ≠ having proven
  //  a bug for every claim.)
  if (any_violation_found && !is_coverage)
  {
    log_status(
      "[Multi-property] k-induction bound exhausted at k = {:d}; "
      "earlier rounds recorded per-claim violations, but remaining "
      "claims could not be proven safe — aggregate verdict is UNKNOWN",
      last_k_step);
    log_fail("VERIFICATION UNKNOWN");
    return 0;
  }

  log_status("Unable to prove or falsify the program, giving up.");
  log_fail("VERIFICATION UNKNOWN");

  if (is_coverage)
    report_coverage(
      options,
      goto_functions.reached_claims,
      goto_functions.reached_mul_claims,
      pytest_gen,
      ctest_gen,
      foundry_gen);
  return 0;
}

// This checks whether "there is a set of inputs that reaches and violates
// an assertion when all the loops in the verified program are unwound up to
// the given bound k".
//
// \param options - options for controlling the symbolic execution
// \param goto_function - GOTO program under investigation
// \param k_step - depth to which all loops in the program are unrolled
// \return
//    TV_TRUE if such assertion violation (i.e., a bug) is found,
//    TV_FALSE if all reachable assertions hold for all input values
// in "goto_functions" with all its loops unrolled up to "k_step",
//    TV_UNKNOWN - otherwise.
tvt esbmc_parseoptionst::is_base_case_violated(
  optionst &options,
  goto_functionst &goto_functions,
  const uint64_t &k_step,
  foundry_generator *foundry_gen)
{
  options.set_option("base-case", true);
  options.set_option("forward-condition", false);
  options.set_option("inductive-step", false);
  options.set_option("no-unwinding-assertions", true);
  options.set_option("partial-loops", false);
  options.set_option("unwind", integer2string(k_step));

  // Collect Foundry coverage cases into the strategy-level generator (when
  // provided) so they survive to do_bmc_strategy's report_coverage. Under
  // --k-induction the per-phase bmct's report_coverage is suppressed
  // (bmc.cpp), so without this the collected cases would be discarded with the
  // throwaway bmct. Only the base case collects; FC/IS do not.
  bmct bmc(goto_functions, options, context, foundry_gen);

  log_progress("Checking base case, k = {:d}", k_step);
  switch (do_bmc(bmc))
  {
  case smt_convt::P_UNSATISFIABLE:
    return tvt(tvt::TV_FALSE);

  case smt_convt::P_SMTLIB:
  case smt_convt::P_ERROR:
    break;

  case smt_convt::P_SATISFIABLE:
    log_result("\nBug found (k = {:d})", k_step);
    return tvt(tvt::TV_TRUE);

  default:
    log_result("Unknown BMC result");
    abort();
  }

  return tvt(tvt::TV_UNKNOWN);
}

// This checks whether "there is a set of inputs for which one of the loop
// conditions is still satisfied after it has been executed
// (i.e., unrolled) at least k times".
//
// \param options - options for controlling the symbolic execution
// \param goto_function - GOTO program under investigation
// \param k_step - depth to which all loops in the program are unrolled
// \return
//    TV_TRUE if there is a set of input values for which at least
// one of the loops in the program can be executed more than "k_step" times.
//    TV_FALSE if all reachable loops have at most "k_step" iterations
// for all input values in "goto_functions".
//    TV_UNKNOWN - otherwise.
tvt esbmc_parseoptionst::does_forward_condition_hold(
  optionst &options,
  goto_functionst &goto_functions,
  const uint64_t &k_step)
{
  if (options.get_bool_option("disable-forward-condition"))
    return tvt(tvt::TV_UNKNOWN);

  options.set_option("base-case", false);
  options.set_option("forward-condition", true);
  options.set_option("inductive-step", false);
  options.set_option("no-unwinding-assertions", false);
  options.set_option("partial-loops", false);

  // We have to disable assertions in the forward condition but
  // restore the previous value after it
  bool no_assertions = options.get_bool_option("no-assertions");

  // Turn assertions off
  options.set_option("no-assertions", true);
  options.set_option("unwind", integer2string(k_step));

  bmct bmc(goto_functions, options, context);

  log_progress("Checking forward condition, k = {:d}", k_step);
  auto res = do_bmc(bmc);

  // Restore the no assertion flag, before checking the other steps
  options.set_option("no-assertions", no_assertions);

  switch (res)
  {
  case smt_convt::P_SATISFIABLE:
    return tvt(tvt::TV_TRUE);

  case smt_convt::P_SMTLIB:
  case smt_convt::P_ERROR:
    break;

  case smt_convt::P_UNSATISFIABLE:
    log_result(
      "\nSolution found by the forward condition; "
      "all states are reachable (k = {:d})",
      k_step);
    return tvt(tvt::TV_FALSE);

  default:
    log_fail("Unknown BMC result");
    abort();
  }

  return tvt(tvt::TV_UNKNOWN);
}

// This tries to prove the inductive step: "assuming nondeterministic
// inputs for every loop, and assuming that all assertions hold for
// the first k iterations of every loop, all assertions will also hold
// when all loops in the program are unrolled to k+1."
// ("Loop inputs" are the variables whose values change inside the loop.)
//
// \param options - options for controlling the symbolic execution
// \param goto_function - GOTO program under investigation
// \param k_step - depth to which all loops in the program are unrolled
// \return -
//    TV_TRUE if there is a set of values for which all assertions in
// all loops hold for the first "k" iterations but not one of the assertions in
// one of the loops is violated during the "k+1" iterations.
//    TV_FALSE if the the inductive step holds.
//    TV_UNKNOWN - otherwise.
tvt esbmc_parseoptionst::is_inductive_step_violated(
  optionst &options,
  goto_functionst &goto_functions,
  const uint64_t &k_step)
{
  if (options.get_bool_option("disable-inductive-step"))
    return tvt(tvt::TV_UNKNOWN);

  if (strtoul(cmdline.getval("max-inductive-step"), nullptr, 10) < k_step)
    return tvt(tvt::TV_UNKNOWN);

  options.set_option("base-case", false);
  options.set_option("forward-condition", false);
  options.set_option("inductive-step", true);
  options.set_option("no-unwinding-assertions", true);
  options.set_option("partial-loops", true);
  options.set_option("unwind", integer2string(k_step));

  bmct bmc(goto_functions, options, context);

  log_progress("Checking inductive step, k = {:d}", k_step);
  switch (do_bmc(bmc))
  {
  case smt_convt::P_SATISFIABLE:
    return tvt(tvt::TV_TRUE);

  case smt_convt::P_SMTLIB:
  case smt_convt::P_ERROR:
    break;

  case smt_convt::P_UNSATISFIABLE:
    log_result(
      "\nSolution found by the inductive step "
      "(k = {:d})",
      k_step);
    return tvt(tvt::TV_FALSE);

  default:
    log_fail("Unknown BMC result\n");
    abort();
  }

  return tvt(tvt::TV_UNKNOWN);
}

// This is a wrapper method that does a single round of
// symbolic execution of the given GOTO program and creates
// a decision problem specified by the verification options.
// In brief, they are used to control what assertions and
// assumptions are injected into the verified bounded trace
// during symbolic execution.
//
// \param bmc - the bmc object that contains all the necessary
// information (see below) to perform a single run of Bounded Model Checking:
//
//  1) GOTO program,
//  2) verification options.
//  3) program context,
int esbmc_parseoptionst::do_bmc(bmct &bmc)
{
  log_progress("Starting Bounded Model Checking");

  smt_convt::resultt res = bmc.start_bmc();

  // A solver that answers UNKNOWN (or errors out on an unsupported
  // construct) is a *solver limitation*, not an ESBMC invariant violation:
  // it must not take the process down.
  //
  // The motivating case: `--k-induction` on a Solidity contract. The
  // inductive step havocs the loop-modified state, which for a storage
  // mapping means an equality between the CONST_ARRAY-initialised global
  // and a fresh symbol. Bitwuzla answers UNKNOWN with "Equality over
  // constant arrays not fully supported yet" (the same limitation the
  // Solidity solver auto-hint above already documents for >=3-level nested
  // mappings), dec_solve maps that to P_ERROR — and the `abort()` that used
  // to sit here turned it into SIGABRT / exit 134, killing the whole run.
  // That also made dead code of the `case smt_convt::P_ERROR: break;` arm
  // that `is_base_case_violated`, `does_forward_condition_hold` and
  // `is_inductive_step_violated` each already have to degrade to TV_UNKNOWN.
  //
  // First try to recover: if the backend was auto-selected (the user did
  // not name one) retry the query once with CVC5, whose array engine
  // handles the shape. `bmct::options` is a reference to the caller's
  // option set, so the switch also sticks for the remaining k steps.
  static bool solver_fallback_attempted = false;
  if (res == smt_convt::P_ERROR && !solver_fallback_attempted)
  {
    const bool user_picked_solver =
      cmdline.isset("z3") || cmdline.isset("cvc5") || cmdline.isset("cvc4") ||
      cmdline.isset("cvc") || cmdline.isset("bitwuzla") ||
      cmdline.isset("boolector") || cmdline.isset("yices") ||
      cmdline.isset("mathsat") || cmdline.isset("smtlib") ||
      cmdline.isset("default-solver");
    const std::string padded_solvers =
      std::string(" ") + ESBMC_AVAILABLE_SOLVERS + " ";
    const bool cvc5_available =
      padded_solvers.find(" cvc5 ") != std::string::npos;
    if (
      !user_picked_solver && cvc5_available &&
      bmc.options.get_option("default-solver") != "cvc5")
    {
      solver_fallback_attempted = true;
      log_warning(
        "The auto-selected SMT backend could not decide this query "
        "(unsupported construct or UNKNOWN). Retrying with 'cvc5' and "
        "keeping it for the rest of the run; pass --bitwuzla / --z3 to "
        "pin a backend explicitly.");
      bmc.options.set_option("default-solver", "cvc5");
      config.options.set_option("default-solver", "cvc5");
      res = bmc.start_bmc();
    }
  }

  if (res == smt_convt::P_ERROR)
    log_warning(
      "The solver could not decide this query; treating it as inconclusive "
      "and continuing. Retry with a different backend (--cvc5 / --z3 / "
      "--bitwuzla) if this repeats on every step.");

#ifdef HAVE_SENDFILE_ESBMC
  if (bmc.options.get_bool_option("memstats"))
  {
    int fd = open("/proc/self/status", O_RDONLY);
    sendfile(2, fd, nullptr, 100000);
    close(fd);
  }
#endif

  return res;
}

bool esbmc_parseoptionst::set_claims(goto_functionst &goto_functions)
{
  try
  {
    if (cmdline.isset("claim"))
      ::set_claims(goto_functions, cmdline.get_values("claim"));
  }

  catch (const char *e)
  {
    log_error("{}", e);
    return true;
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
    return true;
  }

  catch (int)
  {
    return true;
  }

  return false;
}

// This method performs a wide range of actions that can be broadly divided
// into 3 main steps:
//
//  1) creating a GOTO program,
//  2) processing the GOTO program, and
//  3) outputting the GOTO program.
//
// This method is typically used as the second stage
// (right after parsing the command line options) by the verification methods
// (i.e., BMC, k-induction, etc).
//
// \param options - various options used during the above steps,
// \param goto_functions - the "created and processed" GOTO program.
bool esbmc_parseoptionst::get_goto_program(
  optionst &options,
  goto_functionst &goto_functions)
{
  try
  {
    fine_timet create_start = current_time();
    if (create_goto_program(options, goto_functions))
      return true;
    fine_timet create_stop = current_time();
    log_status(
      "GOTO program creation time: {}s",
      time2string(create_stop - create_start));

    fine_timet process_start = current_time();
    if (process_goto_program(options, goto_functions))
      return true;
    fine_timet process_stop = current_time();
    log_status(
      "GOTO program processing time: {}s",
      time2string(process_stop - process_start));
    if (output_goto_program(options, goto_functions))
      return true;
  }

  catch (const char *e)
  {
    log_error("{}", e);
    return true;
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
    return true;
  }

  catch (std::bad_alloc &)
  {
    log_error("Out of memory");
    return true;
  }

  return false;
}

// This method creates a GOTO program from the source specified by the
// command line options. A GOTO program can be created:
//
//  1) from a GOTO binary file,
//  2) by parsing the input program files.
//
// \param options - options to be passed through,
// \param goto_functions - this is where the created GOTO program is stored.
bool esbmc_parseoptionst::create_goto_program(
  optionst &options,
  goto_functionst &goto_functions)
{
  try
  {
    if (cmdline.args.size() == 0)
    {
      log_error("Please provide a program to verify");
      return true;
    }

    // If the user is providing the GOTO functions, we don't need to parse
    if (cmdline.isset("binary"))
    {
      if (cmdline.isset("cprover"))
        log_warning(
          "Be sure you are manually linking with the cprover libraries. This "
          "will be automated in the future.");
      if (read_goto_binary(goto_functions))
        return true;

      if (cmdline.isset("function"))
      {
        Forall_goto_program_instructions (
          it, goto_functions.function_map["__ESBMC_main"].body)
        {
          if (!it->is_function_call())
            continue;

          if (
            !is_symbol2t(to_code_function_call2t(it->code).function) ||
            to_symbol2t(to_code_function_call2t(it->code).function).thename !=
              "c:@F@main")
            continue;

          to_code_function_call2t(it->code).function =
            symbol2tc(get_empty_type(), cmdline.getval("function"));
        }
      }

      goto_functions.update();
    }
    else
    {
      if (parse_goto_program(options, goto_functions))
        return true;
    }
  }

  catch (const char *e)
  {
    log_error("{}", e);
    return true;
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
    return true;
  }

  catch (std::bad_alloc &)
  {
    log_error("Out of memory");
    return true;
  }

  return false;
}

// This method creates a GOTO program from the given GOTO binary.
//
// \param goto_functions - this is where the created GOTO program is stored.
bool esbmc_parseoptionst::read_goto_binary(goto_functionst &goto_functions)
{
  log_progress("Reading GOTO program from file");
  goto_binary_reader goto_reader;
  for (const auto &arg : cmdline.args)
  {
    if (goto_reader.read_goto_binary(arg, context, goto_functions))
    {
      log_error("Failed to open `{}'", arg);
      return true;
    }
  }

  return false;
}

// This method creates a GOTO program by parsing the input program files.
//
// \param options - options to be passed to the program parser,
// \param goto_functions - this is where the created GOTO program is stored.
bool esbmc_parseoptionst::parse_goto_program(
  optionst &options,
  goto_functionst &goto_functions)
{
  try
  {
    if (parse(cmdline))
      return true;

    if (cmdline.isset("parse-tree-too") || cmdline.isset("parse-tree-only"))
    {
      std::ostringstream oss;
      for (auto &it : langmap)
        it.second->show_parse(oss);
      log_status("{}", oss.str());
      if (cmdline.isset("parse-tree-only"))
        exit(0);
    }

    // Typechecking (old frontend) or adjust (clang frontend)
    if (typecheck())
      return true;
    if (final())
      return true;

    // we no longer need any parse trees or language files
    clear_parse();

    if (cmdline.isset("symbol-table-too") || cmdline.isset("symbol-table-only"))
    {
      std::ostringstream oss;
      show_symbol_table_plain(oss);
      log_status("{}", oss.str());
      if (cmdline.isset("symbol-table-only"))
        exit(0);
    }

    // Solidity implicit default: enable --no-standard-checks for any
    // Solidity run. C-level safety checks (pointer/align/vla/scanf/...)
    // emit false positives on Yul-lowered code, and the two
    // semantically-meaningful checks (bounds, div-by-zero) are now
    // opt-in via the positive --bounds-check / --div-by-zero-check.
    {
      bool is_solidity = cmdline.isset("sol");
      if (!is_solidity)
        for (const auto &arg : cmdline.args)
          if (arg.size() >= 4 && arg.substr(arg.size() - 4) == ".sol")
          {
            is_solidity = true;
            break;
          }
      if (is_solidity)
        options.set_option("no-standard-checks", true);

      // Solidity coverage auto-enable: drop user/library asserts (under
      // branch/condition coverage modes only — assertion-coverage is exempt
      // because it would self-zero) and symex pointer-points-to claims, so
      // coverage metrics aren't contaminated by stdlib/Solidity-model guards
      // nor by dynamic dereference claims. Must run BEFORE goto_convert
      // because no-assertions is consumed by convert_assert
      // (goto_convert.cpp:978).
      if (is_solidity)
      {
        const bool any_assert_cov = cmdline.isset("assertion-coverage") ||
                                    cmdline.isset("assertion-coverage-claims");
        const bool any_branch_or_cond_cov =
          cmdline.isset("branch-coverage") ||
          cmdline.isset("branch-coverage-claims") ||
          cmdline.isset("branch-function-coverage") ||
          cmdline.isset("branch-function-coverage-claims") ||
          cmdline.isset("condition-coverage") ||
          cmdline.isset("condition-coverage-claims") ||
          cmdline.isset("condition-coverage-rm") ||
          cmdline.isset("condition-coverage-claims-rm") ||
          cmdline.isset("solidity-path-coverage");
        if (any_branch_or_cond_cov)
          options.set_option("no-assertions", true);
        if (any_branch_or_cond_cov || any_assert_cov)
        {
          // Mirror set_neg_unless_pos pattern below so explicit
          // --symex-pointer-check wins over the auto-enable.
          if (!cmdline.isset("symex-pointer-check"))
            options.set_option("no-symex-pointer-check", true);
        }
      }
    }

    // Expand --no-standard-checks into individual options before goto_convert,
    // because VLA size checks are generated during goto conversion.
    // NOTE: `no-narrowing-check` is deliberately NOT expanded here — it
    // is only read by `goto_check` (a separate pass after goto_convert),
    // and setting it at this point has been observed to destabilise
    // error-trace construction in bounded-mode runs that hit a
    // counter-example (mapping_12 regression). The second expansion
    // block (before `goto_check`) does the right thing.
    if (
      cmdline.isset("no-standard-checks") ||
      options.get_bool_option("no-standard-checks"))
    {
      // Positive opt-in flags override the umbrella for the two
      // Solidity-relevant checks. SMTChecker idiom:
      //   --no-standard-checks --div-by-zero-check
      // means "disable everything standard except div-by-zero".
      auto set_neg_unless_pos = [&](const char *neg, const char *pos) {
        if (!cmdline.isset(pos))
          options.set_option(neg, true);
      };
      // Couple the pointer-deref check to --bounds-check: a symbolic-size
      // dynamic array (`new T[](n)`) lowers to a malloc'd region and its OOB
      // access surfaces as a *dereference* failure, so --bounds-check must be
      // able to re-enable pointer-check (otherwise no flag can detect it).
      // Only when bounds-check is EFFECTIVELY on: a contradictory
      // `--bounds-check --no-bounds-check` keeps pointer-check disabled too.
      if (!cmdline.isset("bounds-check") || cmdline.isset("no-bounds-check"))
        options.set_option("no-pointer-check", true);
      set_neg_unless_pos("no-div-by-zero-check", "div-by-zero-check");
      options.set_option("no-pointer-relation-check", true);
      options.set_option("no-unlimited-scanf-check", true);
      options.set_option("no-vla-size-check", true);
      options.set_option("no-align-check", true);
      set_neg_unless_pos("no-bounds-check", "bounds-check");
    }

    log_progress("Generating GOTO Program");
    goto_convert(context, options, goto_functions);
  }

  catch (const char *e)
  {
    log_error("{}", e);
    return true;
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
    return true;
  }

  catch (std::bad_alloc &)
  {
    log_error("Out of memory");
    return true;
  }

  return false;
}

// This method performs various analyses and transformations
// on the given GOTO program. They involve all the techniques that we class
// as "static analyses" - performed on the given GOTO program before it is
// symbolically executed. Examples of such techniques include:
//
//  - interval analysis,
//  - removal of unreachable code,
//  - preprocessing the program for k-induction,
//  - applying GOTO contractors,
//  - ...
//
// \param options - various options used by the processing methods,
// \param goto_functions - reference to the GOTO program to be processed.
bool esbmc_parseoptionst::process_goto_program(
  optionst &options,
  goto_functionst &goto_functions)
{
  try
  {
    namespacet ns(context);

    bool is_mul =
      cmdline.isset("multi-property") || cmdline.isset("parallel-solving");
    is_coverage = cmdline.isset("assertion-coverage") ||
                  cmdline.isset("assertion-coverage-claims") ||
                  cmdline.isset("condition-coverage") ||
                  cmdline.isset("condition-coverage-claims") ||
                  cmdline.isset("condition-coverage-rm") ||
                  cmdline.isset("condition-coverage-claims-rm") ||
                  cmdline.isset("branch-coverage") ||
                  cmdline.isset("branch-coverage-claims") ||
                  cmdline.isset("branch-function-coverage") ||
                  cmdline.isset("branch-function-coverage-claims") ||
                  cmdline.isset("k-path-coverage") ||
                  cmdline.isset("k-path-coverage-claims") ||
                  cmdline.isset("solidity-path-coverage");

    // For coverage mode, treat extra input files (cmdline.args[1:]) as include
    // files so that the coverage location_pool covers all input sources.
    if (is_coverage && cmdline.args.size() > 1)
      for (size_t i = 1; i < cmdline.args.size(); i++)
        config.ansi_c.include_files.push_back(cmdline.args[i]);

    // For Solidity coverage mode: neutralize the multi-transaction harness loop.
    // The _ESBMC_Main_* functions contain a while(nondet_bool()) loop that calls
    // user functions repeatedly. This causes massive symex overhead in coverage
    // mode where we only need each function executed once. Convert backward GOTOs
    // (loop back-edges) in _ESBMC_Main* functions to SKIPs so the loop body
    // executes exactly once.
    //
    // --coverage-multi-tx opts OUT of this neutralization: the loop stays live
    // (bounded by --unwind), so a branch reachable only through a state-building
    // call sequence (deposit(); withdraw();) becomes coverable and the Foundry
    // generator can reconstruct the multi-call sequence. Costs symex time, hence
    // opt-in.
    if (is_coverage && !cmdline.isset("coverage-multi-tx"))
    {
      bool is_sol = cmdline.isset("sol");
      if (!is_sol)
        for (const auto &arg : cmdline.args)
          if (arg.size() >= 4 && arg.substr(arg.size() - 4) == ".sol")
          {
            is_sol = true;
            break;
          }
      if (is_sol)
      {
        Forall_goto_functions (f_it, goto_functions)
        {
          std::string fname = f_it->first.as_string();
          if (fname.find("_ESBMC_Main") == std::string::npos)
            continue;
          Forall_goto_program_instructions (it, f_it->second.body)
          {
            if (it->is_backwards_goto())
              it->make_skip();
          }
        }
        goto_functions.update();
      }
    }

    // Solidity implicit default: re-applied here for the read_goto_binary
    // path that bypasses parse_goto_program's earlier setter.
    {
      bool is_solidity = cmdline.isset("sol");
      if (!is_solidity)
        for (const auto &arg : cmdline.args)
          if (arg.size() >= 4 && arg.substr(arg.size() - 4) == ".sol")
          {
            is_solidity = true;
            break;
          }
      if (is_solidity)
        options.set_option("no-standard-checks", true);

      // Coverage auto-enable on the read_goto_binary path. Only the symex
      // gate is meaningful here (no-assertions is too late: ASSERT
      // instructions were already baked into the loaded .goto), but we
      // set both for symmetry with the parse_goto_program branch.
      if (is_solidity)
      {
        const bool any_assert_cov = cmdline.isset("assertion-coverage") ||
                                    cmdline.isset("assertion-coverage-claims");
        const bool any_branch_or_cond_cov =
          cmdline.isset("branch-coverage") ||
          cmdline.isset("branch-coverage-claims") ||
          cmdline.isset("branch-function-coverage") ||
          cmdline.isset("branch-function-coverage-claims") ||
          cmdline.isset("condition-coverage") ||
          cmdline.isset("condition-coverage-claims") ||
          cmdline.isset("condition-coverage-rm") ||
          cmdline.isset("condition-coverage-claims-rm") ||
          cmdline.isset("solidity-path-coverage");
        if (any_branch_or_cond_cov)
          options.set_option("no-assertions", true);
        if (any_branch_or_cond_cov || any_assert_cov)
        {
          if (!cmdline.isset("symex-pointer-check"))
            options.set_option("no-symex-pointer-check", true);
        }
      }
    }

    // Expand --no-standard-checks before goto_check (also expanded before
    // goto_convert in parse_goto_program; re-expanding here is idempotent
    // and covers the read_goto_binary path).
    if (
      cmdline.isset("no-standard-checks") ||
      options.get_bool_option("no-standard-checks"))
    {
      auto set_neg_unless_pos = [&](const char *neg, const char *pos) {
        if (!cmdline.isset(pos))
          options.set_option(neg, true);
      };
      // See the goto_convert-side block above: --bounds-check re-enables the
      // pointer-deref check so symbolic-size dynamic-array OOB (a dereference
      // failure) is detectable. A contradictory `--bounds-check
      // --no-bounds-check` keeps pointer-check disabled too.
      if (!cmdline.isset("bounds-check") || cmdline.isset("no-bounds-check"))
        options.set_option("no-pointer-check", true);
      set_neg_unless_pos("no-div-by-zero-check", "div-by-zero-check");
      options.set_option("no-pointer-relation-check", true);
      options.set_option("no-unlimited-scanf-check", true);
      options.set_option("no-vla-size-check", true);
      options.set_option("no-align-check", true);
      set_neg_unless_pos("no-bounds-check", "bounds-check");
      set_neg_unless_pos("no-narrowing-check", "narrowing-check");
    }

    // Start by removing all no-op instructions and unreachable code
    if (!(cmdline.isset("no-remove-no-op")))
      remove_no_op(goto_functions);

    // We should skip this 'remove-unreachable' removal in goto-cov and multi-property
    // - multi-property wants to find all the bugs in the src code
    // - assertion-coverage wants to find out unreached codes (asserts)
    // - however, the optimization below will remove codes during the Goto stage
    if (
      !(cmdline.isset("no-remove-unreachable") || is_mul || is_coverage) ||
      cmdline.isset("condition-coverage-rm") ||
      cmdline.isset("condition-coverage-claims-rm"))
      remove_unreachable(goto_functions);

    // Apply all the initialized algorithms
    for (auto &algorithm : goto_preprocess_algorithms)
    {
      if (cmdline.isset("function"))
        algorithm->setTarget(cmdline.getval("function"));
      algorithm->run(goto_functions);
    }

    // do partial inlining
    if (!cmdline.isset("no-inlining"))
    {
      if (cmdline.isset("full-inlining"))
        goto_inline(goto_functions, options, ns);
      else
        goto_partial_inline(goto_functions, options, ns);
    }

    if (cmdline.isset("gcse"))
    {
      std::shared_ptr<value_set_analysist> vsa =
        std::make_shared<value_set_analysist>(ns);
      try
      {
        log_status("Computing Value-Set Analysis (VSA)");
        (*vsa)(goto_functions);
      }
      catch (vsa_not_implemented_exception &)
      {
        log_warning(
          "Unable to compute VSA due to incomplete implementation. Some GOTO "
          "optimizations will be disabled");
        vsa = nullptr;
      }
      catch (type2t::symbolic_type_excp &)
      {
        log_warning(
          "[GOTO] Unable to compute VSA due to symbolic type. Some GOTO "
          "optimizations will be disabled");
        vsa = nullptr;
      }
      catch (const std::string &e)
      {
        log_warning(
          "[GOTO] Unable to compute VSA due to: {}. Some GOTO "
          "optimizations will be disabled",
          e);
        vsa = nullptr;
      }

      if (cmdline.isset("no-library"))
        log_warning("Using CSE with --no-library might cause huge slowdowns!");

      if (!vsa)
        log_warning("Could not apply GCSE optimization due to VSA limitation!");
      else
      {
        goto_cse cse(context, vsa);
        cse.run(goto_functions);
      }
    }

    if (cmdline.isset("interval-analysis") || cmdline.isset("goto-contractor"))
    {
      interval_analysis(goto_functions, ns, options);
    }

    bool is_k_induction = cmdline.isset("inductive-step") ||
                          cmdline.isset("k-induction") ||
                          cmdline.isset("k-induction-parallel");

    if (cmdline.isset("validate-correctness-witness"))
    {
      log_status("Enable correctness witness validation 2.0");
      remove_no_op(goto_functions);
      goto_loop_invariant_combined(goto_functions);
    }

    if (cmdline.isset("loop-invariant"))
    {
      // Combined mode: Branch 1 (invariant inductivity check) +
      // ASSUME(INV) injected at end of loop body + k-induction (Branch 2).
      remove_no_op(goto_functions);
      goto_loop_invariant_combined(goto_functions);
      goto_k_induction(goto_functions);
    }
    else
    {
      // --k-induction and --loop-invariant-check are independent and may
      // both be specified.  remove_no_op only needs to run once.
      if (is_k_induction || cmdline.isset("loop-invariant-check"))
        remove_no_op(goto_functions);

      if (is_k_induction)
        goto_k_induction(goto_functions);

      if (cmdline.isset("loop-invariant-check"))
      {
        bool use_frame_rule = cmdline.isset("loop-frame-rule");
        goto_loop_invariant(goto_functions, context, use_frame_rule);
      }
    }

    if (
      cmdline.isset("goto-contractor") ||
      cmdline.isset("goto-contractor-condition"))
    {
#ifdef ENABLE_GOTO_CONTRACTOR
      goto_contractor(goto_functions, ns, options);
#else
      log_error(
        "Current build does not support contractors. If ibex is installed, add "
        "to your build process "
        "-DENABLE_GOTO_CONTRACTOR=ON -DIBEX_DIR=path-to-ibex");
      abort();
#endif
    }

    goto_check(ns, options, goto_functions);

    if (options.get_bool_option("atomicity-check"))
      goto_atomicity_check(goto_functions, ns, context);

    // Process function contracts if enabled
    bool has_enforce = cmdline.isset("enforce-contract");
    bool has_replace = cmdline.isset("replace-call-with-contract");
    bool has_enforce_all = cmdline.isset("enforce-all-contracts");
    bool has_replace_all = cmdline.isset("replace-all-contracts");
    if (has_enforce || has_replace || has_enforce_all || has_replace_all)
      process_function_contracts(
        goto_functions,
        has_replace,
        has_enforce,
        has_enforce_all,
        has_replace_all);

    // add re-evaluations of monitored properties
    add_property_monitors(goto_functions, ns);

    // Once again, remove all unreachable and no-op code that could have been
    // introduced by the above algorithms
    if (!(cmdline.isset("no-remove-no-op")))
      remove_no_op(goto_functions);

    if (!(cmdline.isset("no-remove-unreachable") || is_mul || is_coverage))
      remove_unreachable(goto_functions);

    goto_functions.update();

    if (
      cmdline.isset("data-races-check") ||
      cmdline.isset("data-races-check-only"))
    {
      log_status("Adding Data Race Checks");
      options.set_option("data-races-check", true);
      add_race_assertions(context, goto_functions);
    }

    //! goto-cov will also mutate the asserts added by esbmc (e.g. goto-check)
    if (
      cmdline.isset("assertion-coverage") ||
      cmdline.isset("assertion-coverage-claims"))
    {
      // for multi-property
      options.set_option("base-case", true);
      options.set_option("multi-property", true);
      options.set_option("keep-verified-claims", false);
      options.set_option("no-pointer-check", true);

      // enable '--no-unwinding-assertions' if '--unwind' is enabled
      if (cmdline.isset("unwind"))
        options.set_option("no-unwinding-assertions", true);

      std::string filename = cmdline.args[0];
      goto_coveraget tmp(ns, goto_functions, filename);
      // for function mode
      if (cmdline.isset("function"))
        tmp.set_target(cmdline.getval("function"));
      tmp.assertion_coverage();
    }

    if (
      cmdline.isset("condition-coverage") ||
      cmdline.isset("condition-coverage-claims") ||
      cmdline.isset("condition-coverage-rm") ||
      cmdline.isset("condition-coverage-claims-rm"))
    {
      // for multi-property
      options.set_option("base-case", true);
      options.set_option("multi-property", true);
      options.set_option("keep-verified-claims", false);
      // prevent adding property checking assertions during SymEx
      options.set_option("no-pointer-check", true);
      // unreachable conditions should be also considered as short-circuited

      // enable '--no-unwinding-assertions' if '--unwind' is enabled
      if (cmdline.isset("unwind"))
        options.set_option("no-unwinding-assertions", true);

      // for re-do remove-sideeffects
      options.set_option("goto-instrumented", false);

      //?:
      // if we do not want expressions like 'if(2 || 3)' get simplified to 'if(1||1)'
      // we need to enable the options below:
      //    options.set_option("no-simplify", true);
      //    options.set_option("no-propagation", true);
      // however, this will affect the performance, thus they are not enabled by default

      std::string filename = cmdline.args[0];
      goto_coveraget tmp(ns, goto_functions, filename);
      // for function mode
      if (cmdline.isset("function"))
        tmp.set_target(cmdline.getval("function"));

      // if we do not want to count the guard in the assertions
      if (cmdline.isset("no-cov-asserts"))
      {
        if (cmdline.isset("cov-assume-asserts"))
          tmp.replace_all_asserts_to_assume();
        else
          tmp.replace_all_asserts_to_guard(gen_true_expr());
      }
      tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");
      tmp.condition_coverage();

      // redo conversion to remove_sideeffect
      // Due to that we deliberately skip some of the sideeffects removal process when generating the Goto program.
      // This is to keep the condition/guards format and avoid introducing auxiliary variables, which will affect the coverage calculation.
      goto_coverage_rm temp(context, options, goto_functions);
      temp.remove_sideeffect();
    }

    if (
      cmdline.isset("branch-coverage") ||
      cmdline.isset("branch-coverage-claims"))
    {
      // for multi-property
      options.set_option("base-case", true);
      options.set_option("multi-property", true);
      options.set_option("keep-verified-claims", false);
      options.set_option("no-pointer-check", true);

      // enable '--no-unwinding-assertions' if '--unwind' is enabled
      if (cmdline.isset("unwind"))
        options.set_option("no-unwinding-assertions", true);

      std::string filename = cmdline.args[0];
      goto_coveraget tmp(ns, goto_functions, filename);
      // for function mode
      if (cmdline.isset("function"))
        tmp.set_target(cmdline.getval("function"));
      // Per-contract branch coverage (semantics A): scope the
      // denominator/numerator to decisions lexically declared inside the
      // --contract target only. --coverage-whole-unit opts out: keep the
      // contract as the harness entry but count the whole compilation unit.
      if (cmdline.isset("contract") && !cmdline.isset("coverage-whole-unit"))
        tmp.scope_contract = cmdline.getval("contract");
      // Cross-run persisted covered-set: edges already witnessed in a
      // prior run are not re-instrumented (the denominator is still the
      // full static universe, so % is never inflated).
      if (cmdline.isset("coverage-covered-set"))
        tmp.covered_set_path = cmdline.getval("coverage-covered-set");
      // Dependency exclusion (Item 5-d): decisions whose declaring
      // contract is in this set are dropped from BOTH denominator and
      // numerator. Meaningful under --coverage-whole-unit (a no-op in
      // default mode, where scope_contract already filters foreign code).
      if (cmdline.isset("coverage-exclude-contract"))
        for (const auto &c : cmdline.get_values("coverage-exclude-contract"))
          tmp.exclude_contracts.insert(c);
      tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");
      tmp.branch_coverage();
    }
    if (
      cmdline.isset("branch-function-coverage") ||
      cmdline.isset("branch-function-coverage-claims"))
    {
      if (cmdline.isset("path-cov-probe"))
      {
        log_status(
          "--path-cov-probe: suppressing the ordinary branch-function pass; "
          "the Solidity path pass will emit exit-latched branch-arm probes");
      }
      else if (!is_k_induction)
      {
        // for multi-property
        options.set_option("base-case", true);
        options.set_option("multi-property", true);
        options.set_option("keep-verified-claims", false);
        options.set_option("no-pointer-check", true);

        // enable '--no-unwinding-assertions' if '--unwind' is enabled
        if (cmdline.isset("unwind"))
          options.set_option("no-unwinding-assertions", true);

        std::string filename = cmdline.args[0];
        goto_coveraget tmp(ns, goto_functions, filename);
        tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");
        tmp.branch_function_coverage();
      }
    }

    if (
      cmdline.isset("k-path-coverage") ||
      cmdline.isset("k-path-coverage-claims"))
    {
      // Hard cap on the prefix depth. Goal count per branch grows as
      // 2^(N-1), and (N-1) >= 64 would overflow size_t in `1 << pdepth`.
      // 30 leaves 2^29 goals/branch — already far above any reasonable
      // --k-path-max-goals — and gives a comfortable safety margin from
      // the size_t shift limit. Defense-in-depth: also enforced inside
      // goto_coveraget::k_path_coverage().
      static constexpr int K_PATH_N_MAX = 30;

      options.set_option("base-case", true);
      options.set_option("multi-property", true);
      options.set_option("keep-verified-claims", false);
      options.set_option("no-pointer-check", true);
      // Separate boolean enable flag in the option_map. Required because
      // `optionst::get_bool_option(name)` is `atoi(value)`, so storing the
      // CLI int value of `--k-path-coverage` (which is `0` for the no-arg
      // case under boost's implicit_value, or any user-supplied integer)
      // would silently mis-report the feature as disabled in bmc.cpp.
      options.set_option("k-path-coverage-enabled", true);

      if (cmdline.isset("unwind"))
        options.set_option("no-unwinding-assertions", true);

      std::string filename = cmdline.args[0];
      goto_coveraget tmp(ns, goto_functions, filename);
      if (cmdline.isset("function"))
        tmp.set_target(cmdline.getval("function"));
      tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");

      // Resolve N: explicit --k-path-coverage=N > --unwind > fallback 4.
      // The CLI option uses implicit_value(INT_MIN), so `--k-path-coverage`
      // without `=N` parses as INT_MIN (the "no value" sentinel) and falls
      // through to --unwind / 4. Any other non-positive value (incl.
      // explicit `=0` or `=-1`) is rejected — silently falling through
      // would defeat the user's intent.
      const int K_PATH_N_SENTINEL = std::numeric_limits<int>::min();
      int n_arg = K_PATH_N_SENTINEL;
      if (cmdline.isset("k-path-coverage"))
        n_arg = atoi(cmdline.getval("k-path-coverage"));
      if (n_arg > 0)
      {
        if (n_arg > K_PATH_N_MAX)
        {
          log_error(
            "--k-path-coverage=N requires 1 <= N <= {} (got {})",
            K_PATH_N_MAX,
            n_arg);
          return true;
        }
        tmp.k_path_n = static_cast<size_t>(n_arg);
      }
      else if (n_arg != K_PATH_N_SENTINEL)
      {
        // Explicit non-positive value — reject rather than silently
        // falling back.
        log_error(
          "--k-path-coverage=N requires 1 <= N <= {} (got {})",
          K_PATH_N_MAX,
          n_arg);
        return true;
      }
      else if (cmdline.isset("unwind"))
      {
        int u = atoi(cmdline.getval("unwind"));
        if (u <= 0 || u > K_PATH_N_MAX)
        {
          log_error(
            "--k-path-coverage cannot derive N from --unwind={} (must be "
            "in 1..{}); pass --k-path-coverage=N explicitly",
            u,
            K_PATH_N_MAX);
          return true;
        }
        tmp.k_path_n = static_cast<size_t>(u);
      }
      else
      {
        tmp.k_path_n = 4;
        log_status(
          "--k-path-coverage: no N or --unwind specified; defaulting to "
          "N=4");
      }

      auto read_positive = [&](const char *flag, size_t &dst) -> bool {
        if (!cmdline.isset(flag))
          return true;
        int v = atoi(cmdline.getval(flag));
        if (v <= 0)
        {
          log_error("--{} requires a positive integer (got {})", flag, v);
          return false;
        }
        dst = static_cast<size_t>(v);
        return true;
      };
      if (!read_positive("k-path-witness-depth", tmp.k_path_witness_depth))
        return true;
      if (!read_positive("k-path-max-goals", tmp.k_path_max_goals))
        return true;

      tmp.k_path_coverage();
    }

    if (cmdline.isset("solidity-path-coverage"))
    {
      // `--multi-fail-fast N` stops after N satisfiable claims and abandons the
      // rest. Every abandoned path claim then has NO verdict, and the report
      // cannot tell "not reachable" from "never asked" — it would silently
      // report those paths as undecided while the run looked successful. The
      // whole point of the tri-state is that such a difference is visible, so
      // reject the combination instead of quietly producing a truncated ledger.
      if (cmdline.isset("multi-fail-fast"))
      {
        log_error(
          "--solidity-path-coverage is incompatible with --multi-fail-fast: "
          "fail-fast abandons the remaining path claims, so the report could "
          "not distinguish a path that is unreachable from one that was never "
          "solved. Drop --multi-fail-fast (every path must get a verdict).");
        return true;
      }

      // Mirror the branch/k-path dispatch: coverage runs as base-case
      // multi-property so each instrumented path assert is checked
      // independently (a violated assert == that complete path is feasible).
      options.set_option("base-case", true);
      options.set_option("multi-property", true);
      options.set_option("keep-verified-claims", false);
      options.set_option("no-pointer-check", true);
      // Dedicated boolean enable flag: get_bool_option is atoi(), and a NULL
      // flag stores "" (atoi -> 0), so bmc.cpp keys is_goto_cov off this.
      options.set_option("solidity-path-coverage-enabled", true);

      std::string filename = cmdline.args[0];
      goto_coveraget tmp(ns, goto_functions, filename);
      // Ghost snapshot symbols are moved into `context` (available here).
      tmp.cov_context = &context;
      if (cmdline.isset("function"))
        tmp.set_target(cmdline.getval("function"));
      // --contract scoping: only enumerate functions declared in the target
      // contract (not sibling contracts). --coverage-whole-unit opts out.
      if (cmdline.isset("contract") && !cmdline.isset("coverage-whole-unit"))
        tmp.scope_contract = cmdline.getval("contract");
      // --focus-function scoping. The frontend already narrows which entry the
      // DISPATCHER may call; this narrows what gets ENUMERATED AND INSTRUMENTED,
      // so a focused run's published numbers describe the focused unit instead
      // of the whole contract. Measured on aqua `--focus-function dock`: 2846
      // paths in the denominator, of which 2783 belong to units the dispatcher
      // cannot enter in that run, so `Path Coverage` read 0.07% where the honest
      // figure against `dock`'s own 63 paths is 3.17%.
      //
      // Read here rather than inside the pass, like every other knob, so the
      // pass keeps having no command-line dependency of its own.
      if (cmdline.isset("focus-function"))
        tmp.focus_function = cmdline.getval("focus-function");
      // ---- --path-cov-instrument-only: DISPATCH WIDE, INSTRUMENT NARROW ----
      //
      // The option above answers TWO questions with one value -- which entries
      // the harness may CALL, and which units are MEASURED -- and a
      // state-guarded path needs opposite answers to them: a second function in
      // the dispatcher so the state can be established, and a denominator that
      // does not move so the ladder's cells stay comparable. MEASURED on aqua:
      // `--focus-function dock,ship` raised the instrumented set from dock's 63
      // paths to 2796 (ship contributes 2733) and the run was killed at the
      // 300 s outer timeout with no usable answer, at tx=1 and again at tx=2.
      //
      // THE SUBSET IS CHECKED, NOT ASSUMED. `util/focus_function.h` is right
      // that the dispatcher filter and the instrumentation filter must not
      // disagree; the direction it names -- dispatchable but uninstrumented --
      // is what this option deliberately creates and it is the harmless one (a
      // smaller, honestly-published denominator). The other direction is not:
      // an instrumented unit the dispatcher cannot enter reports every path
      // `unit-not-entered`, which reads as "nothing reaches this code" and
      // means "nothing was asked to". So every name here must also be selected
      // by --focus-function, and a name that is not REFUSES the run.
      //
      // An EMPTY --focus-function selects everything (focus_function.h), so
      // `--path-cov-instrument-only dock` with no focus at all is legal and is
      // the widest useful cell: the whole contract in the dispatcher, one
      // unit's paths in the denominator.
      if (cmdline.isset("path-cov-instrument-only"))
      {
        const std::string io = cmdline.getval("path-cov-instrument-only");
        const std::vector<std::string> io_names = focus_function_names(io);
        if (io_names.empty())
        {
          log_error(
            "--path-cov-instrument-only was given the value '{}', which names "
            "no function at all. Pass one or more unit names, separated by "
            "commas or spaces.",
            io);
          return true;
        }
        std::string outside;
        for (const auto &n : io_names)
          if (!focus_function_selects(tmp.focus_function, n))
            outside += (outside.empty() ? "" : ", ") + ("'" + n + "'");
        if (!outside.empty())
        {
          log_error(
            "--path-cov-instrument-only names {}, which --focus-function '{}' "
            "does not select. An instrumented unit the dispatcher cannot enter "
            "reports every one of its paths as 'unit-not-entered' -- a zero "
            "that reads as 'nothing reaches this code' when it means 'nothing "
            "was asked to'. Add the name to --focus-function, or drop "
            "--focus-function entirely (an empty focus dispatches everything).",
            outside,
            tmp.focus_function);
          return true;
        }
        tmp.instrument_only = io;
      }
      // Cross-run persisted covered-set: a complete path already witnessed
      // (CE in hand) in an earlier escalation round is not re-instrumented,
      // so each round spends its budget only on paths still lacking a CE.
      // The denominator stays the full enumerated path set, so the reported
      // coverage is never inflated by the skip.
      if (cmdline.isset("coverage-covered-set"))
        tmp.covered_set_path = cmdline.getval("coverage-covered-set");
      // The JSON report's reason for existing is the per-path counterexample
      // payload (call arguments / EVM environment / post-state). A path claim's
      // guard mentions nothing but the ghost accumulators, so the symex slicer
      // — which keeps only what the claim depends on — removes every state
      // write and every environment read, and the payload comes back EMPTY.
      // Exempt exactly the symbols the harvest reads (see protect_ce_symbols)
      // instead of switching slicing off wholesale: the latter also keeps every
      // c2goto crypto/ABI table in the formula, for no benefit to the report.
      if (cmdline.isset("cov-report-json"))
      {
        tmp.protect_ce_symbols = true;
        // ...and the payload has to survive a run that does not finish. The
        // report is written once, after the job loop and inside the try an OOM
        // unwinds, so a run that dies keeps none of it. The journal is written
        // at the moment each path is witnessed. See path_ce_journal_path.
        tmp.emit_ce_journal = true;
        // The report is also the only place the per-path DECISION SEQUENCE can
        // be published, and it is what puts path coverage and branch coverage
        // on one denominator (the decisions walked). Recorded only for a run
        // that asks for the report, because the memory is per path PREFIX.
        tmp.emit_decision_sites = true;
      }
      tmp.path_cov_probe = cmdline.isset("path-cov-probe");
      // Per-unit path budget. Read BEFORE solidity_path_coverage() because the
      // pass uses it twice and in a fixed order: first as the target that
      // degradation withdraws call points to reach, then as the goal cap that
      // truncates whatever degradation could not fit. It also enters the
      // cross-run fingerprint, so changing it discards the covered set rather
      // than silently reusing entries computed under a different budget.
      if (cmdline.isset("path-cov-max-goals"))
      {
        const int v = atoi(cmdline.getval("path-cov-max-goals"));
        if (v <= 0)
        {
          log_error(
            "--path-cov-max-goals requires a positive integer (got {})", v);
          return true;
        }
        tmp.path_cov_max_goals = static_cast<size_t>(v);
      }
      if (cmdline.isset("path-cov-census-json"))
      {
        tmp.path_cov_census_out = cmdline.getval("path-cov-census-json");
        if (tmp.path_cov_census_out.empty())
        {
          log_error("--path-cov-census-json requires a non-empty output file");
          return true;
        }
        tmp.emit_decision_sites = true;
      }
      // ---- THE PER-CLAIM SOLVER BUDGET ----
      //
      // Read here with the other knobs, and published as a static so the solve
      // loop and the report read the same number rather than each re-deriving
      // it. Default 120 s, 0 = unlimited. Negative is refused rather than
      // clamped: a negative budget has no reading that is not a mistake, and
      // silently turning it into "unlimited" would give the caller the opposite
      // of what they typed.
      {
        int t = 120;
        if (cmdline.isset("path-cov-claim-timeout"))
          t = atoi(cmdline.getval("path-cov-claim-timeout"));
        if (t < 0)
        {
          log_error(
            "--path-cov-claim-timeout requires a non-negative integer (got "
            "{}); use 0 for unlimited",
            t);
          return true;
        }
        goto_coveraget::claim_budget_seconds = static_cast<size_t>(t);
        goto_coveraget::claim_budget_exceeded.store(
          0, std::memory_order_relaxed);
        goto_coveraget::claim_budget_mechanism.clear();
        // Published as MILLISECONDS into `options` so the solver backends can
        // read it. It has to be republished rather than read from the CLI
        // there: boost never pumps a DEFAULTED value into `optionst`
        // (optionst::cmdline, util/options.cpp), so an untouched
        // --path-cov-claim-timeout would read as empty in the backend and the
        // 120 s default would silently never apply -- a budget that is not
        // applied while the report says it was is worse than no budget.
        options.set_option(
          "path-cov-claim-timeout-ms", std::to_string((long long)t * 1000));
      }
      // Stage-2 certification query. Read here, alongside the other knobs the
      // pass consumes, so the pass itself has no command-line dependency.
      if (cmdline.isset("path-cov-certify"))
        tmp.path_cov_certify_path = cmdline.getval("path-cov-certify");
      if (cmdline.isset("path-cov-outer-box"))
        tmp.path_cov_outer_box_path = cmdline.getval("path-cov-outer-box");
      // Stage-3 post-state assertion synthesis. Read beside the other two so
      // the pass keeps having no command-line dependency of its own.
      if (cmdline.isset("path-cov-assert"))
      {
        tmp.path_cov_assert_path = cmdline.getval("path-cov-assert");
        // FORCED, for the same reason --unwind 4 is forced above: a candidate
        // that is TRUE gets discharged during simplification and never enters
        // the verdict ledger, which only records claims the solve loop filed.
        // The reporter then has nothing to read for it and prints "NO VERDICT
        // (never reached the solver)" -- turning the mode's WANTED outcome into
        // a non-answer, silently.
        //
        // MEASURED on the R1 must-flip pair. Without it:
        //   0 HOLDS, 3 REFUTED, 3 no verdict (never reached the solver)
        // With it:
        //   3 HOLDS, 3 REFUTED, 0 no verdict
        // Same program, same region, same six candidates. A mode whose entire
        // output is a HOLDS / REFUTED table cannot let HOLDS become no-verdict
        // as a side effect of an optimisation.
        options.set_option("no-simplify", true);
      }

      // ---- THE THREE STAGE-2/3 MODES ARE MUTUALLY EXCLUSIVE ----
      //
      // They are branches at the end of solidity_path_coverage() and each one
      // `continue`s out of the per-unit loop, so the FIRST one tested wins and
      // the others never fire. That precedence is silent and it already has a
      // measured consequence: `--path-cov-outer-box` together with
      // `--path-cov-certify` runs the outer-box branch, certify emits not one
      // assume and not one assert, `certify_units_matched` stays 0, and the run
      // then dies at the route-5 gate with a message blaming the UNIT NAME --
      // pointing the reader at a spelling mistake that does not exist.
      //
      // Rejected here rather than ordered here, because "which one wins" is not
      // a question with a right answer: the three modes ask three different
      // questions and a caller that passed two of them does not know which one
      // it got. Adding a third branch without this gate would have turned one
      // silent precedence into three.
      {
        std::vector<std::string> stage2;
        if (cmdline.isset("path-cov-outer-box"))
          stage2.push_back("--path-cov-outer-box");
        if (cmdline.isset("path-cov-certify"))
          stage2.push_back("--path-cov-certify");
        if (cmdline.isset("path-cov-assert"))
          stage2.push_back("--path-cov-assert");
        if (cmdline.isset("path-cov-probe") && !stage2.empty())
        {
          log_error(
            "--path-cov-probe is a stage-1 witness mode and cannot be combined "
            "with {}. Run the probe while enumerating paths, then run exactly "
            "one stage-2/3 mode from the saved report.",
            stage2.front());
          return true;
        }
        if (stage2.size() > 1)
        {
          std::string names;
          for (const auto &n : stage2)
            names += (names.empty() ? "" : ", ") + n;
          log_error(
            "--solidity-path-coverage: {} were given together ({}). These are "
            "three mutually exclusive stage-2/3 modes implemented as three "
            "branches at the end of one pass, and each one leaves the unit "
            "loop "
            "as soon as it fires -- so passing two does not run two, it runs "
            "the first and silently discards the rest. Historically that "
            "discarded run then failed with a message about the unit NAME, "
            "which is not what was wrong. Pass exactly one.",
            stage2.size(),
            names);
          return true;
        }
      }
      tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");
      tmp.path_cov_k_induction = is_k_induction;
      tmp.path_cov_k_induction_proved = false;
      // Align the offline enumeration bound with the symex unwind bound. The
      // two MUST agree, or "this path is feasible" as enumerated and "this path
      // is feasible" as explored are answers to different questions.
      //
      // With --unwind unset they did not agree: the enumeration bounded every
      // back-edge at path_cov_unwind (4) while symex was unbounded. That is not
      // just a mismatch on paper — it is fatal in practice, because a Solidity
      // external call is modelled as a nondet RE-ENTRY into the contract's own
      // dispatcher, so `t.call("")` recurses without a bound. Measured: a
      // two-function contract with one `.call` unwound `_ESBMC_Nondet_Extcall_C`
      // 944 times and died with `ERROR: Out of memory`; a plain `for` loop with
      // no --unwind did the same. Both terminate immediately once a bound
      // exists.
      //
      // So: honour an explicit --unwind, and otherwise ADOPT the enumeration's
      // own bound as the unwind bound and say so. Truncation stays visible —
      // report_coverage prints the truncated-loop warning and every JSON entry
      // carries bound.unwind — so this buys termination without hiding what was
      // cut.
      if (cmdline.isset("unwind"))
      {
        int u = atoi(cmdline.getval("unwind"));
        if (u > 0)
          tmp.path_cov_unwind = static_cast<size_t>(u);
      }
      else if (!is_k_induction)
      {
        options.set_option("unwind", std::to_string(tmp.path_cov_unwind));
        log_status(
          "--solidity-path-coverage: no --unwind given; bounding symbolic "
          "execution at {} to match the path enumeration's own loop bound. "
          "Without it an external call (modelled as nondet re-entry into this "
          "contract's dispatcher) or any loop runs unbounded until the memory "
          "limit. Pass --unwind N to choose a different bound",
          tmp.path_cov_unwind);
      }
      else
      {
        log_status(
          "--solidity-path-coverage: k-induction proof query; leaving symex "
          "unwind unset so --max-k-step controls the proof strategy. Path "
          "enumeration retains its own structural loop cap of {}",
          tmp.path_cov_unwind);
      }
      if (!is_k_induction)
        options.set_option("no-unwinding-assertions", true);
      tmp.solidity_path_coverage();
    }

    if (cmdline.isset("negating-property"))
    {
      std::string tgt_fname = cmdline.getval("negating-property");
      std::string filename = cmdline.args[0];
      goto_coveraget tmp(ns, goto_functions, filename);
      tmp.negating_asserts(tgt_fname);
    }
  }

  catch (const char *e)
  {
    log_error("{}", e);
    return true;
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
    return true;
  }

  catch (std::bad_alloc &)
  {
    log_error("Out of memory");
    return true;
  }

  return false;
}

// This method provides different output methods for the given GOTO program.
// Depending on the provided options this method can:
//
//  - output the given GOTO program as text,
//  - translate the provided GOTO program into C,
//  - create a GOTO binary from this GOTO program,
//  - methods outputting some additional information of the GOTO program.
//
// \param options - various options setting the output methods,
// \param goto_functions - the GOTO program to be output.
bool esbmc_parseoptionst::output_goto_program(
  optionst &options,
  goto_functionst &goto_functions)
{
  try
  {
    namespacet ns(context);

    // show it?
    if (cmdline.isset("show-loops"))
    {
      show_loop_numbers(goto_functions);
      return true;
    }

    // show it?
    if (cmdline.isset("show-goto-value-sets"))
    {
      value_set_analysist value_set_analysis(ns);
      value_set_analysis(goto_functions);
      std::ostringstream oss;
      show_value_sets(goto_functions, value_set_analysis, oss);
      log_result("{}", oss.str());
      return true;
    }

    // Write the GOTO program into a binary
    if (cmdline.isset("output-goto"))
    {
      log_status("Writing GOTO program to file");
      std::ofstream oss(
        cmdline.getval("output-goto"), std::ios::out | std::ios::binary);
      if (write_goto_binary(oss, context, goto_functions))
      {
        log_error("Failed to generate goto binary file"); // TODO: explain why
        abort();
      };
      return true;
    }

    if (cmdline.isset("show-ileave-points"))
    {
      print_ileave_points(ns, goto_functions);
      return true;
    }

    // Output the GOTO program to the log (and terminate or continue) in
    // a human-readable format
    if (
      cmdline.isset("goto-functions-too") ||
      cmdline.isset("goto-functions-only"))
    {
      std::ostringstream oss;
      goto_functions.output(ns, oss);
      log_status("{}", oss.str());
      if (cmdline.isset("goto-functions-only"))
        exit(0);
    }

    if (cmdline.isset("dump-goto-cfg"))
    {
      goto_cfg cfg(goto_functions);
      cfg.dump_graph();
      return true;
    }

    // Print a flat list of every function call site with its arguments.
    // Output format: caller -> callee(arg1, arg2, ...) [file:line]
    // Nested calls appear as separate lines with compiler-generated
    // temporaries (e.g. return_value$_add$5) showing data flow.
    if (cmdline.isset("show-call-sites"))
    {
      for (const auto &f : goto_functions.function_map)
      {
        if (!f.second.body_available)
          continue;
        const std::string caller = f.first.as_string();
        forall_goto_program_instructions (i_it, f.second.body)
        {
          if (i_it->is_function_call())
          {
            const auto &fc = to_code_function_call2t(i_it->code);

            // Direct calls have a symbol; indirect calls (function
            // pointers) fall back to pretty-printing the expression.
            std::string callee;
            if (is_symbol2t(fc.function))
              callee = to_symbol2t(fc.function).get_symbol_name();
            else
              callee = from_expr(ns, "", fc.function);

            // Pretty-print each actual argument as a comma-separated list
            std::string args;
            for (size_t i = 0; i < fc.operands.size(); i++)
            {
              if (i > 0)
                args += ", ";
              args += from_expr(ns, "", fc.operands[i]);
            }

            std::string loc;
            if (!i_it->location.get_file().empty())
            {
              const auto &file = i_it->location.get_file();
              const auto &line = i_it->location.get_line();

              if (!line.empty())
                loc = " [" + file.as_string() + ":" + line.as_string() + "]";
              else
                loc = " [" + file.as_string() + "]";
            }
            log_status("{} -> {}({}){}", caller, callee, args, loc);
          }
        }
      }
      std::exit(0);
    }

    // Translate the GOTO program to C and output it into the log or
    // a specified output file
    if (cmdline.isset("goto2c"))
    {
      // Creating a translator here
      goto2ct goto2c(ns, goto_functions);
      goto2c.preprocess();
      goto2c.check();
      std::string res = goto2c.translate();

      const std::string &filename = options.get_option("output");
      if (!filename.empty())
      {
        // Outputting the translated program into the output file
        std::ofstream out(filename);
        assert(out);
        out << res;
      }
      else
        std::cout << res;
      return true;
    }
  }

  catch (const char *e)
  {
    log_error("{}", e);
    return true;
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
    return true;
  }

  return false;
}

// This performs the preprocessing of the input program
// when the old C/C++ frontend (i.e., from "ansi-c/" or "cpp/") is used.
void esbmc_parseoptionst::preprocessing()
{
  try
  {
    if (cmdline.args.size() != 1)
    {
      log_error("Please provide one program to preprocess");
      return;
    }

    std::string filename = cmdline.args[0];

    // To test that the file exists,
    std::ifstream infile(filename.c_str());
    if (!infile)
    {
      log_error("failed to open input file");
      return;
    }
#ifdef ENABLE_OLD_FRONTEND
    std::ostringstream oss;
    if (c_preprocess(filename, oss, false))
      log_error("PREPROCESSING ERROR");
    log_status("{}", oss.str());
#endif
  }
  catch (const char *e)
  {
    log_error("{}", e);
  }

  catch (const std::string &e)
  {
    log_error("{}", e);
  }

  catch (std::bad_alloc &)
  {
    log_error("Out of memory");
  }
}

void esbmc_parseoptionst::add_property_monitors(
  goto_functionst &goto_functions,
  namespacet &ns [[maybe_unused]])
{
  std::map<std::string, std::pair<std::set<std::string>, expr2tc>> monitors;

  context.foreach_operand([this, &monitors](const symbolt &s) {
    if (
      !has_prefix(s.name, "__ESBMC_property_") ||
      s.name.as_string().find("$type") != std::string::npos)
      return;

    // strip prefix "__ESBMC_property_"
    std::string prop_name = s.name.as_string().substr(17);
    std::set<std::string> used_syms;
    expr2tc main_expr = calculate_a_property_monitor(prop_name, used_syms);
    monitors[prop_name] = std::pair{used_syms, main_expr};
  });

  if (monitors.size() == 0)
    return;

  Forall_goto_functions (f_it, goto_functions)
  {
    /* do not instrument global entry function */
    if (f_it->first == "__ESBMC_main")
      continue;

    /* do also not instrument functions computing the propositions themselves */
    if (has_prefix(f_it->first, "c:@F@") && has_suffix(f_it->first, "_status"))
    {
      const std::string &name = f_it->first.as_string();
      std::string prop_name = name.substr(5, name.length() - 5 - 7);
      if (monitors.find(prop_name) != monitors.end())
        continue;
    }

    log_debug("ltl", "adding monitor exprs in function {}", f_it->first);
    goto_functiont &func = f_it->second;
    goto_programt &prog = func.body;
    Forall_goto_program_instructions (p_it, prog)
      add_monitor_exprs(p_it, prog.instructions, monitors);
  }

  // Find main function; find first function call; insert updates to each
  // property expression. This makes sure that there isn't inconsistent
  // initialization of each monitor boolean.
  goto_functionst::function_mapt::iterator f_it =
    goto_functions.function_map.find("__ESBMC_main");
  assert(f_it != goto_functions.function_map.end());
  std::string main_suffix = "@" + (config.main.empty() ? "main" : config.main);
  const symbol2t *entry_sym = nullptr;
  Forall_goto_program_instructions (p_it, f_it->second.body)
  {
    /* Find the call to the entry point, usually 'main'. At that point
     * everything like pthreads, etc., is already set up. */
    if (p_it->type != FUNCTION_CALL)
      continue;
    const code_function_call2t &func_call = to_code_function_call2t(p_it->code);
    if (!is_symbol2t(func_call.function))
      continue;
    const symbol2t &func_sym = to_symbol2t(func_call.function);
    if (!has_suffix(func_sym.thename, main_suffix))
      continue;

    /* found it */
    entry_sym = &func_sym;
    break;
  }
  assert(entry_sym);

  f_it = goto_functions.function_map.find(entry_sym->thename.as_string());
  assert(f_it != goto_functions.function_map.end());

  goto_programt &body = f_it->second.body;
  goto_programt::instructionst &insn_list = body.instructions;

  /* insert a call to start the monitor thread and after it also to kill it */
  goto_programt::instructiont new_insn;
  new_insn.function = entry_sym->thename;

  expr2tc func_sym = symbol2tc(get_empty_type(), "c:@F@ltl2ba_start_monitor");
  std::vector<expr2tc> args;
  new_insn.make_function_call(code_function_call2tc(expr2tc(), func_sym, args));
  insn_list.insert(insn_list.begin(), new_insn);

  func_sym = symbol2tc(get_empty_type(), "c:@F@ltl2ba_finish_monitor");
  new_insn.make_function_call(code_function_call2tc(expr2tc(), func_sym, args));
  // add this call before each 'return' instruction
  for (auto it = insn_list.begin(); it != insn_list.end(); ++it)
  {
    if (it->type != RETURN)
      continue;
    insn_list.insert(it, new_insn);
  }
}

static void collect_symbol_names(
  const expr2tc &e,
  const std::string &prefix,
  std::set<std::string> &used_syms)
{
  if (is_symbol2t(e))
  {
    const symbol2t &thesym = to_symbol2t(e);
    assert(thesym.rlevel == 0);
    std::string sym = thesym.get_symbol_name();

    used_syms.insert(sym);
  }
  else
  {
    e->foreach_operand([&prefix, &used_syms](const expr2tc &e) {
      if (!is_nil_expr(e))
        collect_symbol_names(e, prefix, used_syms);
    });
  }
}

expr2tc esbmc_parseoptionst::calculate_a_property_monitor(
  const std::string &name,
  std::set<std::string> &used_syms) const
{
  const symbolt *fn = context.find_symbol("c:@F@" + name + "_status");
  assert(fn);

  const codet &fn_code = to_code(fn->value);
  assert(fn_code.get_statement() == "block");
  assert(fn_code.operands().size() == 1);

  const codet &fn_ret = to_code(fn_code.op0());
  assert(fn_ret.get_statement() == "return");
  assert(fn_ret.operands().size() == 1);

  expr2tc new_main_expr;
  migrate_expr(fn_ret.op0(), new_main_expr);

  collect_symbol_names(new_main_expr, name, used_syms);

  return new_main_expr;
}

void esbmc_parseoptionst::add_monitor_exprs(
  goto_programt::targett insn,
  goto_programt::instructionst &insn_list,
  const std::map<std::string, std::pair<std::set<std::string>, expr2tc>>
    &monitors)
{
  // We've been handed an instruction, look for assignments to the
  // symbol we're looking for. When we find one, append a goto instruction that
  // re-evaluates a proposition expression. Because there can be more than one,
  // we put re-evaluations in atomic blocks.

  if (!insn->is_assign())
    return;

  code_assign2t &assign = to_code_assign2t(insn->code);

  // Don't allow propositions about things like the contents of an array and
  // suchlike.
  if (!is_symbol2t(assign.target))
    return;

  symbol2t &sym = to_symbol2t(assign.target);

  // Is this actually an assignment that we're interested in?
  std::string sym_name = sym.get_symbol_name();
  std::set<std::pair<std::string, expr2tc>> triggered;
  for (const auto &[prop, pair] : monitors)
    if (pair.first.find(sym_name) != pair.first.end())
      triggered.emplace(prop, pair.second);

  if (triggered.empty())
    return;

  goto_programt::instructiont new_insn;

  new_insn.type = ATOMIC_BEGIN;
  new_insn.function = insn->function;
  insn_list.insert(insn, new_insn);

  insn++;

#if 0
  new_insn.type = FUNCTION_CALL;
  expr2tc func_sym =
    symbol2tc(get_empty_type(), "c:@F@__ESBMC_switch_to_monitor");
  std::vector<expr2tc> args;
  new_insn.code = code_function_call2tc(expr2tc(), func_sym, args);
  new_insn.function = insn->function;
  insn_list.insert(insn, new_insn);
#endif

  new_insn.type = ATOMIC_END;
  new_insn.function = insn->function;
  insn_list.insert(insn, new_insn);
}

static unsigned int calc_globals_used(const namespacet &ns, const expr2tc &expr)
{
  if (is_nil_expr(expr))
    return 0;

  if (!is_symbol2t(expr))
  {
    unsigned int globals = 0;

    expr->foreach_operand([&globals, &ns](const expr2tc &e) {
      globals += calc_globals_used(ns, e);
    });
    return globals;
  }

  std::string identifier = to_symbol2t(expr).get_symbol_name();

  if (
    identifier == "NULL" || identifier == "__ESBMC_alloc" ||
    identifier == "__ESBMC_alloc_size")
    return 0;

  const symbolt *sym = ns.lookup(identifier);
  assert(sym);
  if (sym->static_lifetime || sym->type.is_dynamic_set())
    return 1;

  return 0;
}

void esbmc_parseoptionst::print_ileave_points(
  namespacet &ns,
  goto_functionst &goto_functions)
{
  forall_goto_functions (fit, goto_functions)
    forall_goto_program_instructions (pit, fit->second.body)
    {
      bool print_insn = false;

      switch (pit->type)
      {
      case GOTO:
      case ASSUME:
      case ASSERT:
      case ASSIGN:
        if (calc_globals_used(ns, pit->guard) > 0)
          print_insn = true;
        break;
      case FUNCTION_CALL:
      {
        const code_function_call2t &deref_code =
          to_code_function_call2t(pit->code);
        if (
          is_symbol2t(deref_code.function) &&
          to_symbol2t(deref_code.function).get_symbol_name() ==
            "c:@F@__ESBMC_yield")
          print_insn = true;
        break;
      }
      case NO_INSTRUCTION_TYPE:
      case OTHER:
      case SKIP:
      case LOCATION:
      case END_FUNCTION:
      case ATOMIC_BEGIN:
      case ATOMIC_END:
      case RETURN:
      case DECL:
      case DEAD:
      case THROW:
      case CATCH:
      case THROW_DECL:
      case THROW_DECL_END:
      case LOOP_INVARIANT:
        break;
      }

      if (print_insn)
        pit->output_instruction(ns, pit->function, std::cout);
    }
}

// Process function contracts if enabled
void esbmc_parseoptionst::process_function_contracts(
  goto_functionst &goto_functions,
  bool has_replace,
  bool has_enforce,
  bool has_enforce_all,
  bool has_replace_all)
{
  namespacet ns(context);
  code_contractst contracts(goto_functions, context, ns);

  // Reference to context for use in lambda
  contextt &ctx = context;

  // Lambda function to collect all functions with contracts
  // This includes functions with:
  // 1. Explicit contract clauses (__ESBMC_requires, __ESBMC_ensures, __ESBMC_assigns)
  // 2. __attribute__((annotate("__ESBMC_contract"))) annotation
  auto collect_functions_with_contracts =
    [&contracts, &goto_functions, &ctx]() {
      std::set<std::string> result;
      forall_goto_functions (it, goto_functions)
      {
        if (!it->second.body_available)
          continue;

        std::string func_name = id2string(it->first);

        // Use is_compiler_generated (which correctly handles C++ USR IDs like
        // "c:@F@fst#*1I#") instead of a raw '#' string filter, which would
        // incorrectly skip all C++ functions with parameters.
        if (contracts.is_compiler_generated(func_name))
          continue;

        // Check for explicit contract clauses in function body
        if (contracts.has_contracts(it->second.body))
        {
          result.insert(func_name);
          continue;
        }

        // Check for __attribute__((annotate("__ESBMC_contract"))) annotation
        symbolt *func_sym = ctx.find_symbol(it->first);
        if (func_sym && contracts.is_annotated_contract_function(*func_sym))
        {
          result.insert(func_name);
        }
      }
      return result;
    };

  // Lambda function to process function list (handles "*" wildcard)
  auto process_function_list = [&collect_functions_with_contracts](
                                 const std::list<std::string> &func_list) {
    std::set<std::string> result;
    for (const auto &func : func_list)
    {
      if (func == "*")
      {
        // "*" means all functions with contracts
        result = collect_functions_with_contracts();
        break; // "*" means all, so we can break after collecting
      }
      else
      {
        result.insert(func);
      }
    }
    return result;
  };

  // Process enforce-contract option
  if (has_enforce)
  {
    const std::list<std::string> &enforce_list =
      cmdline.get_values("enforce-contract");
    std::set<std::string> to_enforce = process_function_list(enforce_list);

    if (!to_enforce.empty())
    {
      log_status("Enforcing contracts for {} function(s)", to_enforce.size());
      // Pass --function entry point so the enforce wrapper allocates fresh
      // backing storage for pointer params (harness receives nil args).
      std::string entry_function =
        cmdline.isset("function") ? cmdline.getval("function") : "";
      // Assigns compliance check is always enabled: without it, functions can
      // lie about their assigns clause, causing false VERIFICATION SUCCESSFUL.
      contracts.enforce_contracts(to_enforce, entry_function, true);
    }
  }

  // Process replace-call-with-contract option
  if (has_replace)
  {
    const std::list<std::string> &replace_list =
      cmdline.get_values("replace-call-with-contract");
    std::set<std::string> to_replace = process_function_list(replace_list);

    if (!to_replace.empty())
    {
      log_status(
        "Replacing calls with contracts for {} function(s)", to_replace.size());
      contracts.replace_calls(to_replace);
    }
  }

  // Lambda to collect ONLY functions with __ESBMC_contract annotation
  auto collect_annotated_contract_functions =
    [&contracts, &goto_functions, &ctx]() {
      std::set<std::string> result;
      forall_goto_functions (it, goto_functions)
      {
        if (!it->second.body_available)
          continue;
        std::string func_name = id2string(it->first);
        if (contracts.is_compiler_generated(func_name))
          continue;
        symbolt *func_sym = ctx.find_symbol(it->first);
        if (func_sym && contracts.is_annotated_contract_function(*func_sym))
          result.insert(func_name);
      }
      return result;
    };

  // Process --enforce-all-contracts
  if (has_enforce_all)
  {
    std::set<std::string> to_enforce = collect_annotated_contract_functions();
    if (!to_enforce.empty())
    {
      log_status(
        "Enforcing annotated contracts for {} function(s)", to_enforce.size());
      std::string entry_function =
        cmdline.isset("function") ? cmdline.getval("function") : "";
      contracts.enforce_contracts(to_enforce, entry_function, true);
    }
  }

  // Process --replace-all-contracts
  if (has_replace_all)
  {
    std::set<std::string> to_replace = collect_annotated_contract_functions();
    if (!to_replace.empty())
    {
      log_status(
        "Replacing annotated calls for {} function(s)", to_replace.size());
      contracts.replace_calls(to_replace);
    }
  }
}

bool esbmc_parseoptionst::resolve_color_option() const
{
  const char *raw = cmdline.getval("color");
  std::string val = (raw && *raw) ? raw : "auto";
  if (val != "auto" && val != "always" && val != "never")
  {
    log_error(
      "Invalid value for --color: '{}'. Must be auto, always, or never.", val);
    exit(1);
  }
  return ENABLE_COLOR(val);
}

// Colorize --flag references found in description text with bold formatting.
// Matches "--" followed by one or more alphanumeric/hyphen characters,
// stopping at delimiters like '.', ',', ' ', ')', '\'', '"', or end of string.
static std::string colorize_flag_refs(const std::string &text)
{
  std::string result;
  size_t i = 0;
  while (i < text.size())
  {
    if (
      i + 2 < text.size() && text[i] == '-' && text[i + 1] == '-' &&
      (std::isalnum(text[i + 2]) || text[i + 2] == '-'))
    {
      size_t start = i;
      i += 2;
      while (i < text.size() && (std::isalnum(text[i]) || text[i] == '-'))
        i++;
      result += CLR_BOLD;
      result += text.substr(start, i - start);
      result += CLR_RESET;
    }
    else
    {
      result += text[i];
      i++;
    }
  }
  return result;
}

// This prints the ESBMC version and a list of CMD options
// available in ESBMC.
void esbmc_parseoptionst::help()
{
  // Redirect everything here to stdout
  FILE *outstream = messaget::state.out;
  messaget::state.out = stdout;

  bool use_color = resolve_color_option();

  // Print the "* * *     ESBMC x.y.z     * * *"
  auto const esbmc_string = fmt::format(" ESBMC {} ", ESBMC_VERSION);
  auto const title_start = std::string("* * * ");
  auto const title_end = std::string(" * * *");
  auto const inner =
    80 - title_start.length() - title_end.length() - esbmc_string.length();
  auto const left_pad = std::string(inner / 2, '=');
  auto const right_pad = std::string(inner - inner / 2, '=');
  log_status(
    "\n{}{}{}{}{}", title_start, left_pad, esbmc_string, right_pad, title_end);

  std::ostringstream oss;
  oss << cmdline.cmdline_options;

  if (!use_color)
  {
    log_status("{}", oss.str());
    return;
  }

  // Colorize: group headers in bold cyan, option names in bold,
  // and --flag references in descriptions in bold
  std::istringstream iss(oss.str());
  std::string line;
  while (std::getline(iss, line))
  {
    if (!line.empty() && line[0] != ' ' && line.back() == ':')
      // Group header (e.g. "Printing options:")
      fmt::print(messaget::state.out, CLR_BOLD_CYAN "{}" CLR_RESET "\n", line);
    else if (
      line.size() >= 3 && line[0] == ' ' && line[1] == ' ' && line[2] == '-')
    {
      // Option line: colorize the flag portion (up to the description)
      auto desc_pos = line.find("  ", 4);
      if (desc_pos != std::string::npos)
        fmt::print(
          messaget::state.out,
          CLR_BOLD "{}" CLR_RESET "{}\n",
          line.substr(0, desc_pos),
          colorize_flag_refs(line.substr(desc_pos)));
      else
        fmt::print(messaget::state.out, CLR_BOLD "{}" CLR_RESET "\n", line);
    }
    else
      fmt::print(messaget::state.out, "{}\n", colorize_flag_refs(line));
  }

  // Restore everything back to original output stream.
  messaget::state.out = outstream;
}

// When k-induction exhausts all k-steps without a definitive result, run one
// final per-VCC inductive-step check at the last k to identify which specific
// properties could not be resolved, without impacting the main k-induction loop.
void esbmc_parseoptionst::diagnose_unknown_properties(
  optionst &options,
  goto_functionst &goto_functions,
  const uint64_t k_step)
{
  if (options.get_bool_option("disable-inductive-step"))
    return;

  // Mirror the guards used by is_inductive_step_violated in the main loop:
  // inductive step is skipped for k==1 and capped by --max-inductive-step.
  if (k_step <= 1)
    return;
  if (strtoul(cmdline.getval("max-inductive-step"), nullptr, 10) < k_step)
    return;

  const bool saved_base_case = options.get_bool_option("base-case");
  const bool saved_forward_condition =
    options.get_bool_option("forward-condition");
  const bool saved_inductive_step = options.get_bool_option("inductive-step");
  const bool saved_no_unwinding =
    options.get_bool_option("no-unwinding-assertions");
  const bool saved_partial_loops = options.get_bool_option("partial-loops");
  const std::string saved_unwind = options.get_option("unwind");

  options.set_option("base-case", false);
  options.set_option("forward-condition", false);
  options.set_option("inductive-step", true);
  options.set_option("no-unwinding-assertions", true);
  options.set_option("partial-loops", true);
  options.set_option("unwind", integer2string(k_step));
  options.set_option("diagnose-unknown-properties", true);

  bmct bmc(goto_functions, options, context);

  log_progress(
    "\nDiagnosing unresolved properties (inductive step, k = {:d}):", k_step);
  do_bmc(bmc);

  options.set_option("base-case", saved_base_case);
  options.set_option("forward-condition", saved_forward_condition);
  options.set_option("inductive-step", saved_inductive_step);
  options.set_option("no-unwinding-assertions", saved_no_unwinding);
  options.set_option("partial-loops", saved_partial_loops);
  options.set_option("unwind", saved_unwind);
  options.set_option("diagnose-unknown-properties", false);
}
