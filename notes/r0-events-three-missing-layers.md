# R0 "events and their order": which layer is missing, so the deferral is a decision

EXECUTION_PLAN §5 step 3.1 is R0 = exit kind + revert reason + **events and
their order**. The exit-kind half shipped and is pinned by regressions; this is
the receipt for why the other half has not, written so that deferring it is a
decision with a named blocker rather than a sentence in Threats. R0 is on the
"不许砍" list, so the row must say WHICH half shipped.

Produced by a delegated full read of the frontend, `bmc.cpp`, the emitter and
the trace machinery. Line numbers are from that read and are to be re-checked
when edited.

## 1. The frontend DOES model `emit` — so "events are hard to model in ESBMC" is false

`EmitStatement` is a grammar kind (`solidity_grammar.h:274`) and the handler
(`solidity_convert_stmt.cpp:1017-1029`) treats it as a function call. An event
gets a real function symbol with a synthesised empty body
(`solidity_convert_decl.cpp:1667-1672`, `:1814-1822`), and an unqualified
`emit E(a,b)` becomes a genuine `side_effect_expr_function_callt` with its
arguments converted and attached (`solidity_convert_expr.cpp:2983-3008`,
`solidity_convert_call.cpp:155-189`).

**So the identity and the argument expressions of an emit exist in the GOTO
program.** Nothing downstream consumes them.

### L0 — a live divergence worth fixing on its own merits, independent of R0

`emit L.E(a)` and `emit I.E(a)` — the QUALIFIED form — are **erased**:
`solidity_convert_expr.cpp:688-695` sets `new_expr = code_skipt()` with the
comment "Events have no runtime effect in our model". They reach that arm
because `is_sol_library_function` accepts `EventDefinition`
(`solidity_grammar.cpp:192-208`) and routes to `LibraryMemberCall`
(`:942-945`).

**The same source construct has two lowerings**, one of which produces a call
and one of which produces nothing, and nothing reports the difference. That is a
defect whether or not events are ever asserted.

Also: `indexed` is never read anywhere in the frontend files examined — event
parameters go through the generic `get_function_params`
(`solidity_convert_stmt.cpp:31-120`), which reads only the type and name.
UNVERIFIED for `solidity_convert_type.cpp`, `_util.cpp`,
`solidity_monomorphize.cpp`.

## 2. The counterexample cannot carry events, and the obvious route is UNSOUND

`path_ce_t` (`goto_coverage.h:473-527`) has thirteen fields and none can hold
them: `inputs` / `env` / `entry_storage` / `final_state` are rendered as JSON
OBJECTS (`bmc.cpp:1644-1654`, `:1713-1716`), so they cannot express repetition
or order; `extcall_returns` is the only array-shaped field and has zero writers.

Order does exist in principle — the harvest walks `w.trace.steps` in list order
(`bmc.cpp:3197`) and path coverage forces non-compact traces
(`bmc.cpp:2981-2982`). But four facts each independently break a trace-walking
harvest:

1. **There is no FUNCTION_CALL step kind.** `goto_trace_stept::typet` is
   `{ASSIGNMENT, ASSUME, ASSERT, OUTPUT, SKIP, RENUMBER, BREANCHING}`
   (`goto_trace.h:52-61`).
2. An empty-bodied callee produces **no step of its own** — the body holds only
   `END_FUNCTION` (`goto_convert_functions.cpp:167`).
3. The only steps an emit contributes are its **argument assignments**
   (`symex_function.cpp:247-249`), and those carry an **empty `stack_trace`**:
   `frame.function_identifier` is set at `symex_function.cpp:446`, AFTER
   `argument_assignments` runs at `:441-442`, and `gen_stack_trace` breaks on the
   first empty identifier (`goto_symex_state.cpp:543-546`). So the callee
   identity is not recoverable from those steps.
4. A **zero-argument event leaves no steps at all**, and argument assignments
   into an empty body are read by nothing, making them prime slicing candidates.
   UNVERIFIED whether the slicer removes them (`slice.cpp` would settle it).

⇒ A trace-walking harvest would silently miss zero-argument events and possibly
all sliced ones — a detector that fires on some shapes and not others with no way
to tell from the output which happened. That is the shape recorded in
[[detector-conditional-on-unknown]] and it is why this route must not be taken
"cheaply".

## 3. The emitter emits nothing about events, and actively filters them out

No `vm.expectEmit`, no `emit`, no event declaration, no `recordLogs` anywhere in
`foundry.cpp` / `foundry.h`. The only walk over Solidity code symbols
**excludes** events by construction: `foundry.cpp:852-857` returns early with
"an event / free function, not an interface method".

What DOES exist and would be reused: the pre-call cheatcode prelude is an
ordered string concatenation (`:2987`, `:2993`, `:3005`, `:3015`), so N ordered
`vm.expectEmit(...)` + `emit Ev(...)` pairs append naturally — but they must be
spliced BEFORE the prank append, since `foundry.cpp:3007-3010` documents that
`vm.prank` must be the last cheatcode before the call. Type rendering exists
(`arg_sol_type`, `sol_type_to_solidity`), and there is one declaration-writer to
copy (the interface-mock path, `:802-941` + `:2836-2848`).

`indexed` being unread is a PRECISION limit, not a blocker:
`vm.expectEmit(true,true,true,true)` plus a full `emit Ev(a,b,c)` checks
everything and lets solc pack the topics.

UNVERIFIED and not settleable from this repo: whether forge-std's `expectEmit`
tolerates extra un-asserted logs between asserted ones, which decides whether
asserting a SUBSET in order is sound. The §4.4 self-check gate turns a wrong
answer into a red test that gets discarded rather than a silently wrong artifact.

## 4. Verdict: (b) expensive — one layer is missing, and it is census-sized

Not (c): the frontend produces the call. Not (a): the exit-kind half was cheap
because its evidence already existed in a census that already ran; events have
**no producer anywhere**.

| layer | where | size |
|---|---|---|
| **L0** stop erasing the qualified form; stamp `#sol_is_event` on the symbol so downstream need not string-match an id | `solidity_convert_expr.cpp:688-695`; `solidity_convert_modifier.cpp:171-175` | small, 2 files, **and worth doing on its own merits** |
| **L1** per-path event observation — does not exist in any form | new field beside `goto_coverage.h:491`; producer near the `FUNCTION_CALL` scan at `goto_coverage.h:1098-1108`; value join in `bmc.cpp:3197-3449`; JSON emission mirroring `:1660-1663` | **the cost** |
| **L2** emitter | `foundry.h:56-132`; splice at `foundry.cpp:3011-3016`; declaration-writer `:802-941`; invert the filter at `:852-857`; claim-key join at `:1656/1678/1711` | moderate, every rendering piece pre-exists |

**L1 is why this is expensive.** Identity and order are STATIC (goto
instructions in DFS order); argument values are DYNAMIC (the SMT model); and
nothing in the tree joins a static per-path instruction sequence to model values
— decisions are joined via `enc`, and events have no such encoding. It also
needs a runtime completeness check (`#events observed on this path ==
#event call instructions on this path`), or it repeats the 0.4 failure of a
detector that is silent on the dangerous shape.

## The sentence to use, and the one not to use

Correct: *"R0 currently asserts exit kind; event assertions are not implemented
because the per-path event observation channel does not exist, and building it
is a census-sized piece of work rather than a field addition."*

Wrong: *"events are hard to model in ESBMC."* `solidity_convert_expr.cpp:2983-3008`
models them. And this must NOT become a Threats entry — it is scope, with a named
blocker and a table above saying what closing it costs.
