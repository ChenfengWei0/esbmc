/*******************************************************************\

   Module:

   Author: Ben YIU, yspb1g08@ecs.soton.ac.uk Lucas Cordeiro,
     lcc08r@ecs.soton.ac.uk

\*******************************************************************/


#ifndef EXECUTION_STATE_H_
#define EXECUTION_STATE_H_

#include <irep2.h>

#include <iostream>
#include <deque>
#include <set>
#include <map>
#include <list>
#include <algorithm>
#include <std_expr.h>

#include "symex_target.h"
#include "goto_symex_state.h"
#include "goto_symex.h"
#include "renaming.h"

class reachability_treet;

/**
 *  Class representing a global state of variables and threads.
 *  This is made up of two parts: first a "level 2" state and value_set pair
 *  of objects, recording the SSA numbers of variables assigned to, and
 *  what particular pointers can possibly point at (the value set). Then,
 *  there's a vector of goto_symex_statet's recording a set of threads and
 *  their state (call stack, program counter).
 *
 *  We also contain a few things that technically should be in
 *  reachability_treet, such as DFS_traversed recording what context switches
 *  have been taken.
 *
 *  A large amount of functionality is implemented by extending goto_symext.
 *  The idea is that reachability_treet symex_step's this object until we
 *  notify it about a context switch point. We catch some threading-specific
 *  instructions in execution_statet::symex_step, and pass the rest to
 *  goto_symext::symex_step. And rather than spraying hooks over goto_symext to
 *  detect context switch points, we override a few virtual functions and pass
 *  the operations being observed back up to reachability_treet.
 *
 *  Some circumstances require goto_symext fetching data from execution_statet,
 *  such as fetching our thread number or suchlike, or creating a new thread.
 *  These are handled through intrinsic function calls.
 */

class execution_statet : public goto_symext
{
  public: class ex_state_level2t; // Forward dec

  public:
  /**
   *  Default constructor.
   *  Takes the environment we'll be working with, and sets up a single thread
   *  with a function call to the "main" function just encoded. Two of these
   *  arguments can have unknown subclasses: _target and l2init get clone'd
   *  each time we need a new one (think the blasted factory pattern).
   *  @param goto_functions GOTO functions we'll operate over.
   *  @param ns Namespace we're working with.
   *  @param _target Symex target to receive assigns/asserts/etc in trace.
   *  @param context Context we'll be working in.
   *  @param l2init Initial level2t state (blank).
   *  @param options Options we're going to operate with.
   */
  execution_statet(const goto_functionst &goto_functions, const namespacet &ns,
                   reachability_treet *art,
                   symex_targett *_target,
                   contextt &context,
                   ex_state_level2t *l2init,
                   const optionst &options);

  /**
   *  Default copy constructor.
   *  Used each time we duplicate an execution_statet in reachability_treet.
   *  Does what you might expect, but also updates any ex_state_level2t objects
   *  in the new execution_statet to point at the right object. */
  execution_statet(const execution_statet &ex);
  execution_statet &operator=(const execution_statet &ex);

  /**
   *  Default destructor.
   */
  virtual ~execution_statet();

  // Types

  typedef std::string (*serialise_fxn)(execution_statet &ex_state,
                                       const exprt &rhs);
  typedef std::map<const irep_idt, serialise_fxn> expr_id_map_t;

  /**
   *  execution_statet specific level2t.
   *  The feature of this class is that we maintain a pointer to the ex_state
   *  that owns this level2t, which is updated whenever this class gets copied.
   *  We also override some level2t methods, so that we can encode a node_id in
   *  the names that are generated. (This is for --schedule).
   */
  class ex_state_level2t : public renaming::level2t
  {
  public:
    ex_state_level2t(execution_statet &ref);
    virtual ~ex_state_level2t();
    virtual ex_state_level2t *clone(void) const;
    virtual void rename(expr2tc &lhs_symbol, unsigned count);
    virtual void rename(expr2tc &identifier);

    execution_statet *owner;
  };

  /**
   *  State-hashing level2t.
   *  When using this level2t, any assignment made is caught, and the symbolic
   *  names are hashed. This is the primary handler for state hashing.
   */
  class state_hashing_level2t : public ex_state_level2t
  {
  public:
    state_hashing_level2t(execution_statet &ref);
    virtual ~state_hashing_level2t(void);
    virtual state_hashing_level2t *clone(void) const;
    virtual void make_assignment(expr2tc &lhs_symbol,
                                     const expr2tc &const_value,
                                     const expr2tc &assigned_value);
    crypto_hash generate_l2_state_hash() const;
    typedef std::map<irep_idt, crypto_hash> current_state_hashest;
    current_state_hashest current_hashes;
  };

  // Macros

  /** Increase number of context switches this ex_state has taken */
  void
  increment_context_switch()
  {
    CS_number++;
  }

  /** Increase number of time slices performed by this ex_state */
  void
  increment_time_slice()
  {
    TS_number++;
  }

  /** Reset the number of time slices performed by this ex_state */
  void
  reset_time_slice()
  {
    TS_number = 0;
  }

  /** Get the number of context switches performed by this ex_state */
  int
  get_context_switch()
  {
    return CS_number;
  }

  /** Get the number of time slices performed by this ex_state */
  int
  get_time_slice()
  {
    return TS_number;
  }

  /** Reset record of what context switches were taken from this ex_state */
  void
  resetDFS_traversed()
  {
    for (unsigned int i = 0; i < threads_state.size(); i++)
      DFS_traversed.at(i) = false;
  }

  /** Fetch the thread ID of the current active thread */
  unsigned int
  get_active_state_number() {
    return active_thread;
  }

  /** Set internal thread startup data */
  void set_thread_start_data(unsigned int tid, const expr2tc &argdata)
  {
    if (tid >= thread_start_data.size()) {
      std::cerr << "Setting thread data for nonexistant thread " << tid;
      std::cerr << std::endl;
      abort();
    }

    thread_start_data[tid] = argdata;
  }

  /** Fetch internal thread startup data */
  const expr2tc &get_thread_start_data(unsigned int tid) const
  {
    if (tid >= thread_start_data.size()) {
      std::cerr << "Getting thread data for nonexistant thread " << tid;
      std::cerr << std::endl;
      abort();
    }

    return thread_start_data[tid];
  }

  // Methods

  /**
   *  Duplicate this execution state.
   *  This is just a vehicle for subclasses to get their hooks in when this
   *  class gets duplicated.
   *  @see schedule_execution_statet
   *  @see dfs_execution_statet
   *  @return New, duplicated execution state
   */
  virtual execution_statet *clone(void) const = 0;

  /**
   *  Make one symbolic execution step.
   *  Take one instruction and interpret it. Can result in any action, such as
   *  a thread ending, causing a context switch, a function call being taken,
   *  a thread being created, and so forth.
   *  @param art reachability_treet we're operating with (defunct?)
   */
  virtual void symex_step(reachability_treet &art);

  /**
   *  Symbolically assign a value.
   *  Entirely handed off to goto_symext::symex_assign. However this method
   *  also passes the assignment to a reachability_treet analysis function to
   *  see whether the assignment should be generating a context switch.
   *  @param code Code representing assignment we're making.
   */
  virtual void symex_assign(const expr2tc &code);

  /**
   *  Symbolically assert something.
   *  Implemented by calling goto_symext::claim. However, we also pass the
   *  claimed expression onto the reachability_treet analysis functions, to
   *  see whether or not we should generate a context switch because of a read
   *  in this claim.
   *  @param expr Expression that we're asserting is true.
   *  @param msg Textual message explaining this assertion.
   */
  virtual void claim(const expr2tc &expr, const std::string &msg);

  /**
   *  Perform a jump across GOTO code.
   *  Implemented by goto_symext::symex_goto. This is a concrete action, not
   *  symbolic. Although the guard is symbolic. The expression of the guard can
   *  read global state, and can thus possibly result in a context switch being
   *  generated; so we pass the guard to a reachability_treet analysis function
   *  too.
   *  @param old_guard Guard of the goto jump being performed.
   */
  virtual void symex_goto(const expr2tc &old_guard);

  /**
   *  Assume some expression is true.
   *  Implemented by goto_symext::assume. Potentially causes a context switch,
   *  so we pass the assumption expression on to a reachability_treet analysis
   *  function.
   *  @param assumption Expression of the thing we're assuming to be true.
   */
  virtual void assume(const expr2tc &assumption);

  /**
   *  Fetch reference to count of dynamic objects in this state.
   *  The goto_symext class knows that such a count exists, just it doesn't
   *  store it itself. So we instead provide a hook for it to fetch a reference
   *  to the true counter.
   *  @return Reference to the count of global dynamic objects.
   */
  virtual unsigned int &get_dynamic_counter(void);

  /** Like get_dynamic_counter, but with nondet symbols. */
  virtual unsigned int &get_nondet_counter(void);

  /**
   *  Fetch name of current execution guard.
   *  The execution guard being the guard of the interleavings up to this point
   *  being true and feasable. This is a symbolic name for it.
   *  @see execute_guard
   *  @return Symbol of current execution state guard
   */
  expr2tc get_guard_identifier();

  /**
   *  Get reference to current thread state.
   *  @return Reference to current thread state.
   */
  goto_symex_statet & get_active_state();
  const goto_symex_statet & get_active_state() const;

  /**
   *  Get atomic number count for current thread state.
   *  @see atomic_numbers
   *  @return Atomic number count for current thread state.
   */
  unsigned int get_active_atomic_number();

  /** Increase current threads atomic number count */
  void increment_active_atomic_number();

  /** Decrease current threads atomic number count */
  void decrement_active_atomic_number();

  /**
   *  Perform a context switch to thread ID i.
   *  Essentially this just updates the counter indicating which thread is
   *  currently active. It also causes the execution guard to be reexecuted.
   *  @see execute_guard.
   *  @param i Thread ID to switch to.
   */
  void switch_to_thread(unsigned int i);

  /**
   *  Determines if current state guard is false.
   *  Used to check whether the interleaving we're exploring has a false guard,
   *  meaning that any further interleaving we take is unviable. If
   *  --smt-thread-guard is enabled, we ask the solver.
   *  @return True when state guard is false
   */
  bool is_cur_state_guard_false(void);

  /**
   *  Generates execution guard that's true if this interleaving can be reached.
   *  We can context switch between many points in many threads; not all of
   *  them are feasible though, and some of them place later constraints on the
   *  contents of program state. This is because the instruction causing the
   *  switch can be guarded in an if/else block. The test 01_pthread27 is an
   *  excellent example of this.
   *
   *  To get around this, we have a state guard that is only true if the guards
   *  at the context switch points up to here are all true. We copy the symbol
   *  for this value into the guards of all currently executing threads. This
   *  means that any assertion after a context switch is guarded by the
   *  conditions on all the previous switches that have happened.
   */
  void execute_guard(void);

  /**
   *  Attempt to explore a thread.
   *  Checks the current DFS state to see whether this thread has already been
   *  explored, or whether there are other reasons to not explore it. If it's
   *  explorable, we return true, *and* mark it as explored in DFS_traversed
   *  @see DFS_traversed.
   *  @param tid Thread ID we wish to explore.
   *  @return True if the desired thread is explorable now.
   */
  bool dfs_explore_thread(unsigned int tid);

  /**
   *  Test to see if interleavings are blocked by the current state.
   *  There can be a variety of reasons why interleavings are blocked; there
   *  can be only one thread, we can be in an atomic block or insn, we can
   *  have reached our context bound.
   *  @return True if the current state prohibits context switches.
   */
  bool check_if_ileaves_blocked(void);

  /**
   *  Create a new thread.
   *  Creates and initializes a new thread, running at the start of the GOTO
   *  program prog.
   *  @param prog GOTO program to start new thread at.
   *  @return Thread ID of newly created thread.
   */
  unsigned int add_thread(const goto_programt *prog);

  /**
   *  Record a thread as ended.
   *  Updates internal records to say that the thread has ended. The thread
   *  itself can continue executing until the next context switch point, when
   *  the scheduler notices it's unschedulable. So, always ensure end_thread is
   *  followed by forcing a context switch So, always ensure end_thread is
   *  followed by forcing a context switch.
   */
  void end_thread(void);

  /**
   *  Perform any necessary steps after a context switch point. Whether or not
   *  it was taken. Resets DFS record, POR records, executes thread guard.
   */
  void update_after_switch_point(void);

  /**
   *  Analyze the contents of an assignment for threading.
   *  If the assignment touches any kind of shared state, we track the accessed
   *  variables for POR decisions made in the future, and also ask the RT obj
   *  whether or not it wants to generate an interleaving.
   *  @param assign Container of code_assign2t object.
   */
  void analyze_assign(const expr2tc &assign);

  /**
   *  Analyze the contents of a read for threading.
   *  If the read touches any kind of shared state, we track the accessed
   *  variables for POR decisions made in the future, and also ask the RT obj
   *  whether or not it wants to generate an interleaving.
   *  @param expr Container of expression possibly touching global state.
   */
  void analyze_read(const expr2tc &expr);

  /**
   *  Get list of globals accessed by expr.
   *  Exactly how this works, I do not know.
   *  @param ns Namespace to work under.
   *  @expr Expression to count global writes in.
   *  @return Number of global refs in this expression.
   */
  void get_expr_globals(const namespacet &ns, const expr2tc &expr,
                        std::set<expr2tc> &global_list);

  /**
   *  Check for scheduling dependancies. Whether it exists between the variables
   *  accessed by the last transition of thread j and the last transition of
   *  thread l.
   *  @param j Most recently executed thread id
   *  @param l Other thread id to check dependancy with
   *  @return True if scheduling dependancy exists between threads j and l
   */
  bool check_mpor_dependancy(unsigned int j, unsigned int l) const;

  /**
   *  Calculate MPOR schedulable threads. I.E. what threads we can schedule
   *  right now without violating the "quasi-monotonic" property.
   */
  void calculate_mpor_constraints(void);

  /** Accessor method for mpor_schedulable. Ensures its access is within bounds
   *  and is read-only. */
  bool is_transition_blocked_by_mpor(void) const
  {
    return mpor_says_no;
  }

  /** Accessor method for cswitch_forced. Sets it to true. */
  void force_cswitch(void)
  {
    cswitch_forced = true;
  }

  /**
   *  Has a context switch point occured.
   *  Four things can justify this:
   *   1. cswitch forced by atomic end or yield.
   *   2. Global data read/written.
   *  @return True if context switch is now triggered
   */
  bool has_cswitch_point_occured(void) const;

  /**
   *  Can execution continue in this thread?
   *  Answer is no if the thread has ended or there's nothing on the call stack
   *  @return False if there are no further instructions to execute.
   */
  bool can_execution_continue(void) const;

  /**
   *  Generate hash of entire execution state.
   *  This takes all current symbolic assignments to variables contained in the
   *  l2 renaming object, and their precomputed hashes, concatonates them with
   *  the current program counter of each thread, and hashes that. This results
   *  in a full hash of the current execution state.
   *  @return Hash of entire current execution state.
   */
  crypto_hash generate_hash(void) const;

  /**
   *  Generate hash of an expression.
   *  @param rhs Expression to hash.
   *  @return Hash of passed in expression.
   */
  crypto_hash update_hash_for_assignment(const exprt &rhs);

  /**
   *  Serialise expressions contents into a string.
   *  @param rhs Expresson to serialise
   *  @return String, serialised version of rhs.
   */
  std::string serialise_expr(const exprt &rhs);

  /**
   *  Print stack trace of each thread to stdout.
   *  Uses the passed in namespace; also uses whatever level of indentation
   *  the caller provides. Primarily a debug feature.
   *  @param ns Namespace to work in.
   *  @param indent Indentation to print each stack trace with
   */
  void print_stack_traces(unsigned int indent = 0) const;

  public:

  /** Pointer to reachability_treet that owns this ex_state */
  reachability_treet *owning_rt;
  /** Stack of thread states. The index into this vector is the thread ID of
   *  the goto_symex_statet at that location */
  std::vector<goto_symex_statet> threads_state;
  /** Atomic section count. Every time an atomic begin is executed, the
   *  atomic_number corresponding to the thread is incremented, allowing nested
   *  atomic begins and ends. A nonzero atomic number for a thread means that
   *  interleavings are disabled currently. */
  std::vector<unsigned int> atomic_numbers;
  /** Record of which context switches have been taken from this state.
   *  Every time a context switch is taken, the bool in this vector is set to
   *  true at the corresponding thread IDs index. */
  std::vector<bool> DFS_traversed;
  /** Storage for threading libraries thread start data. See version history
   *  of when this was introduced to fully understand why; essentially this
   *  is a workaround to prevent too much nondeterminism entering into the
   *  thread starting process. */
  std::vector<expr2tc> thread_start_data;
  /** Last active thread's ID. */
  unsigned int last_active_thread;
  /** Global L2 state of this execution_statet. It's also copied as a reference
   *  into each threads own state. */
  ex_state_level2t *state_level2;
  /** Global pointer tracking state record. */
  value_sett global_value_set;
  /** Current active states thread ID. */
  unsigned int active_thread;
  /** Name prefix for execution guard. */
  irep_idt guard_execution;
  /** Number of timeslices observed by this ex_state. */
  int TS_number;
  /** Number of nondeterministic symbols in this state. */
  unsigned nondet_count;
  /** Number of dynamic objects in this state. */
  unsigned dynamic_counter;
  /** Identifying number for this execution state. Used to distinguish runs
   *  in --schedule mode. */
  unsigned int node_id;
  /** True if the guard on this interleaving becomes false.
   *  Means that there is no path from here on where any assertion may
   *  become satisfiable. */
  bool interleaving_unviable;
  /** State guard prior to a GOTO instruction causing a cswitch. Any thread
   *  interleaved after a GOTO will be composed with this guard, rather than
   *  the guard from any of the branches of the GOTO itself. */
  expr2tc pre_goto_guard;

  protected:
  /** Number of context switches performed by this ex_state */
  int CS_number;
  /** For each thread, a set of symbols that were read by the thread in the
   *  last transition (run). Renamed to level1, as that identifies each piece of
   *  data that could have storage in C. */
  std::vector<std::set<expr2tc> > thread_last_reads;
  /** For each thread, a set of symbols that were written by the thread in the
   *  last transition (run). Renamed to level1, as that identifies each piece of
   *  data that could have storage in C. */
  std::vector<std::set<expr2tc> > thread_last_writes;
  /** Dependancy chain for POR calculations. In mpor paper, DCij elements map
   *  to dependancy_chain[i][j] here. */
  std::vector<std::vector<int> > dependancy_chain;
  /** MPOR scheduling outcome. If we've just taken a transition that MPOR
   *  rejects, this becomes true. For various reasons, we can't tell whether or
   *  not MPOR rejects a transition in advance. */
  bool mpor_says_no;
  /** Indicates a manatory context switch should occur. Can happen when an
   *  atomic_end instruction has occured, or when a __ESBMC_yield(); runs */
  bool cswitch_forced;

  // Static stuff:

  public:
  static expr_id_map_t init_expr_id_map();
  static bool expr_id_map_initialized;
  static expr_id_map_t expr_id_map;
  static unsigned int node_count;
};

/**
 *  Class for performing a DFS thread exploration.
 *  On the whole, just uses the same functionality provided by execution_statet
 *  but with the only modification that this class resets a portion of the
 *  global string pool when it destructs, to prevent string pool inflation.
 *  Specifically; names that have been generated in execution states that are
 *  generated later in execution that this one will all be no longer used after
 *  this one is destructed. So no need to keep their names around.
 */

class dfs_execution_statet : public execution_statet
{
  public:
  dfs_execution_statet(
                   const goto_functionst &goto_functions,
                   const namespacet &ns,
                   reachability_treet *art,
                   symex_targett *_target,
                   contextt &context,
                   const optionst &options)
      : execution_statet(goto_functions, ns, art, _target, context,
                         options.get_bool_option("state-hashing")
                             ? new state_hashing_level2t(*this)
                             : new ex_state_level2t(*this),
                             options)
  {
  };

  dfs_execution_statet(const dfs_execution_statet &ref);
  dfs_execution_statet *clone(void) const;
  virtual ~dfs_execution_statet(void);
};

/**
 *  Execution state class for --schedule exploration.
 *  Provides additional storage for tracking the number of claims that have
 *  been made, doesn't delete the trace/equation on destruction.
 */

class schedule_execution_statet : public execution_statet
{
  public:
  schedule_execution_statet(
                   const goto_functionst &goto_functions,
                   const namespacet &ns,
                   reachability_treet *art,
                   symex_targett *_target,
                   contextt &context,
                   const optionst &options,
                   unsigned int *ptotal_claims,
                   unsigned int *premaining_claims)
      : execution_statet(goto_functions, ns, art, _target, context,
                         new ex_state_level2t(*this), options)
  {
    this->ptotal_claims = ptotal_claims;
    this->premaining_claims = premaining_claims;
    *ptotal_claims = 0;
    *premaining_claims = 0;
  };

  schedule_execution_statet(const schedule_execution_statet &ref);
  schedule_execution_statet *clone(void) const;
  virtual ~schedule_execution_statet(void);
  virtual void claim(const expr2tc &expr, const std::string &msg);

  unsigned int *ptotal_claims;
  unsigned int *premaining_claims;
};

#endif /* EXECUTION_STATE_H_ */
