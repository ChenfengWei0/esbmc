# Function Calls / Return / Frames

`src/goto-symex/symex_function.cpp` (673 LOC). Handles direct calls,
function-pointer calls, recursion bounds, argument binding, return,
and (via `pop_frame`) cleanup.

## Entry points

| Function | Line | Purpose |
|---|---|---|
| `symex_function_call(code)` | 208 | Dispatch: symbol-typed target → `symex_function_call_code`; else → `symex_function_call_deref`. |
| `symex_function_call_code(expr)` | 218 | Direct call: resolve body, set up frame, bind args. |
| `symex_function_call_deref(expr)` | 405 | Function-pointer call: enumerate targets via value-set. |
| `argument_assignments(id, type, args)` | 52 | Bind actuals to formals via `symex_decl` + `symex_assign`. |
| `run_next_function_ptr_target(first)` | 513 | Iterate over queued ptr-call targets. |
| `pop_frame()` | 565 | Frame teardown on END_FUNCTION. |
| `symex_end_of_function()` | 610 | Thin wrapper over `pop_frame`. |
| `make_return_assignment(assign, code)` | 615 | Build the lhs=rv assignment for the caller. |
| `symex_return(code)` | 648 | Queue the current state at `end_of_function`; kill the guard. |
| `get_unwind_recursion(id, counter)` | 16 | Check per-function recursion bound. |

## Direct call walkthrough

```
symex_function_call_code(expr)
  ├── find body in goto_functions.function_map
  │     └── missing body: emit warning, substitute nondet return, return
  ├── ++function_unwind[id]
  ├── check get_unwind_recursion — emit recursion assertion/assumption and bail if exceeded
  ├── read arguments with analyze_args + rename (at caller's renaming level)
  ├── rename_address(call.ret)   ← identify the storage of the return lvalue
  ├── new_frame():
  │     frame.level1 = previous_frame.level1 (inherit L1 so globals keep their numbers)
  │     frame.level1.thread_id = current thread
  │     frame.calling_location = caller pc
  │     frame.entry_guard = cur_state->guard
  │     frame.va_index = argument_assignments(...)   ← declares+assigns each formal
  │     frame.end_of_function = --body.end()
  │     frame.return_value = ret_value
  │     frame.function_identifier = id
  │     frame.hidden = body.hide
  └── pc = body.begin()
```

Two subtleties:

- **Arguments are renamed at the caller's L1/L2** before the new frame
  is pushed. This makes them visible to the callee's body (which is
  about to get its own L1 namespace) as "the caller's view of the
  value". Once the frame is pushed and `symex_decl` is called for the
  formal, the formal gets its own L1 slot; the assign then binds that
  formal's L1 symbol to the renamed-actual value.
- **`rename_address` on the return lvalue**, not `rename`. We want the
  *storage location* of the lvalue, not its current SSA version — the
  caller hasn't written to it yet, and we'll write the callee's return
  value into that location later.

## Argument binding — `argument_assignments`

`symex_function.cpp:52`. For each formal `name_idx`:

1. Too few actuals → claim false with "not enough arguments" message.
2. Skip nameless formals (C allows `void f(int)` — no symbol to bind).
3. Skip nil / string-literal actuals.
4. Type match. If types differ:
   - Number↔pointer mismatch → insert typecast.
   - `pointer→struct` (Python ObjectRef → struct) → insert a
     dereference.
   - Otherwise → `log_error` + abort.
5. `symex_decl(code_decl(formal))` — this mints a fresh L1 number for
   the formal in the new frame.
6. `symex_assign(formal = actual)` — regular SSA step emission.
7. If `ellipsis`, add `va_arg0..N` symbols, decl and assign each.
   Return the starting index (stored in `frame.va_index`).

## Recursion bound

`get_unwind_recursion` (line 16) returns true once
`cur_state->function_unwind[id] > max_unwind`. The effect mirrors a
loop over-unwind:

- Without `--no-unwinding-assertions`: `claim false` with
  "recursion unwinding assertion" — a verification failure flag.
- With `--no-unwinding-assertions`: emit an `assume ¬guard` → silently
  prune this recursion path.

**k-way recursion caveat** (comment at line 246): a function with `k`
recursive call-sites and a `--unwind N` bound produces `O(k^N)`
inlinings, not `O(N)`. The user-facing guidance is to set
`--unwind D+1` where `D` is the actual max recursion depth reachable.

## Body-less function

If `goto_function.body_available == false` (extern, weak, etc.):

- With `--unknown-method-args-check`: every pointer-type argument is
  overwritten with `INVALID` — a way to propagate "the callee could
  have invalidated the pointed-to object".
- If the call has a return lvalue, assign it a fresh
  `nondet$symex::<n>` symbol.
- Bump pc and return — no frame push.

This is where external / stub functions lose information: ESBMC has
no body to symex, so it can only model the caller-visible effects
(nondet return, pointer invalidation). For C library models we
instead provide a body in `c2goto/library/`; for Solidity we provide
bodies for `__ESBMC_*` intrinsics.

## Function pointers

`symex_function_call_deref` (line 405):

1. Dereference the call.function pointer — produces either a
   `symbol`, a chain of `if(c, &f, &g)`, or a failed symbol.
2. `get_function_list(expr)` (line 361) walks the if-chain and
   collects a `list<(guard, function_symbol)>`.
3. The list is stored on `frame.cur_function_ptr_targets`; the
   `function_ptr_call_loc` records the call site; a
   `function_ptr_combine_target` is picked as the merge point.
4. The first target is called immediately (normal
   `symex_function_call_code`).
5. When that call's END_FUNCTION fires, `run_next_function_ptr_target`
   either kicks off the next queued target (guarded) or, when the
   queue is empty, resumes control at the combine target.

This means multiple-target pointer calls are NOT nondet-dispatched —
they are explored as a set of guarded paths that later merge at the
combine target. The guards come from the if-expression structure
produced by the value-set dereference.

## Return

**`make_return_assignment`** (line 615) builds `frame.return_value =
ret_operand`, with a typecast if needed. Returns `true` if an
assignment was built.

**`symex_return`** (line 648):

1. Queue the current state on the frame's `goto_state_map` at the
   frame's `end_of_function` — the return is a "goto the end of the
   function" in GOTO form.
2. If `stack_limit > 0 && no_return_value_opt`, emit stack-size
   claims over the return operand.
3. `cur_state->guard.make_false()` — kill this path. The queued
   state at end_of_function carries the "returned from here" guard;
   everything after the RETURN in the caller is guarded false until
   `merge_gotos` kicks in at the end.

## `pop_frame`

`symex_function.cpp:565`. Fired by `symex_end_of_function`
(on END_FUNCTION). It:

1. Restores `cur_state->source.pc/prog` from
   `frame.calling_location`.
2. Restores the guard from `frame.entry_guard` (unless we're
   mid-exception and `stack_catch` is nonempty — in that case,
   exception machinery owns the guard).
3. For every local variable registered in `frame.local_variables`:
   - If it's a `return_value$_alloca$` name, call `symex_free` on it.
   - Erase from the per-thread value-set.
   - Remove from L2 renaming.
4. Decrement `function_unwind[id]` for the recursion bound.
5. `cur_state->pop_frame()` — actual `call_stack.pop_back()`.

Step 3 is what cleans up local SSA versions so they can't leak into
subsequent calls. The `alloca` free is also critical for memory-leak
checks — `alloca`'d objects are logically freed on scope exit.

## Return-value routing and pointer-pass

For a call `int x = f(&y)`:

1. Caller's `y` gets its address-of computed and renamed at the
   caller's scope.
2. The pointer is passed as an argument symbol and stored in the
   callee's formal (a fresh L1 name on the callee).
3. Inside the callee, `*p = 3` resolves via the value-set — which has
   tracked that `p` points at `y` — and writes to the *caller's
   storage for `y`*, not a local copy.
4. Therefore pointer-passed-to-function mutations cross frame
   boundaries **at the value-set level**, not through any
   function-frame mechanism.

**This is why the k-induction havoc bug
([k-induction.md](k-induction.md) §Bugs) exists**: the havoc analysis
works at the GOTO-program level, looking at *syntactic* writes in the
loop body. It sees `dispatch(&obj)` — a function call with
`&obj` as argument — and (incorrectly) does not add `obj` to the
write set even though a write happens through the pointer at symex
time.

## Debugging tips

- **"Function not found in function_map"** — abort path. The frontend
  likely forgot to emit the goto_function for it; check
  `--show-goto-functions`.
- **"No body for function X"** — expected for stubs. If you didn't
  expect it, you likely missed a declaration-before-use ordering
  issue in the frontend. Search for `X` in the goto-functions dump
  and confirm `body_available`.
- **"Recursion unwinding assertion" failure** — either bump `--unwind`
  or check whether recursion is actually unbounded (missing base
  case).
- **"argument type mismatch"** — the frontend emitted an ASSIGN with
  incompatible lhs/rhs types. Review the frontend's typing pass; the
  symex error is usually a symptom of earlier type confusion.
- **"Return value lands in the wrong SSA variable"** — check
  `rename_address(call.ret)` at the call site. If the lvalue was
  `rename`'d instead of `rename_address`'d, the storage identity
  would be wrong.
