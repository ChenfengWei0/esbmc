# --focus-function: implementation and multi-value feasibility

Read-only probe. No file outside this one was modified; no binary, cmake or make was run.

**Files read in full** (needed so the "every site" claim below can be scoped honestly):
`src/esbmc/options.cpp`, `src/solidity-frontend/solidity_convert.cpp`,
`src/solidity-frontend/solidity_convert_constructor.cpp`,
`src/solidity-frontend/solidity_convert_contract.cpp`,
`src/solidity-frontend/solidity_convert.h`,
`src/solidity-frontend/solidity_language.cpp`,
`src/solidity-frontend/README.md`, `src/goto-programs/goto_coverage.h`,
`src/util/cmdline.{h,cpp}`, `src/util/options.cpp`, and the four
`regression/esbmc-solidity/*focus*` test directories.
**Partially read** (ranges given in *Uncertainties*): `goto_coverage.cpp`,
`bmc.cpp`, `esbmc_parseoptions.cpp`, `solidity_convert_modifier.cpp`.

---

## 1. Filter site

`src/solidity-frontend/solidity_convert_constructor.cpp`, inside
`solidity_convertert::get_unbound_function()` — the builder of
`_ESBMC_Nondet_Extcall_<C>`.

Gate, **lines 333–336**:

```cpp
    // --focus-function: when the caller is the target contract and a focus
    // function is set, restrict the dispatch loop to only that function.
    // Other contracts (e.g., cross-contract targets reached from inside the
    // focus function) keep their full nondet dispatch.
    const bool focus_applies = !focus_func.empty() && tgt_cnt_set.size() == 1 &&
                               c_name == *tgt_cnt_set.begin();
```

Filter, **lines 358–361**, inside `for (const auto &method : methods)` where
`methods == funcSignatures[c_name]`:

```cpp
      if (focus_applies && func_name != focus_func)
        // focus-function mode: skip all non-focus functions on the target
        // contract to avoid unnecessary verification overhead.
        continue;
```

`func_name` is `method.name` (line 347). That is it — one `continue`. Nothing
else in the dispatcher builder consults `focus_func`.

There is a second, *validating* site (not a filter) in
`src/solidity-frontend/solidity_convert.cpp:252–321`, run at the top of
`convert()`: it rejects `--focus-function` + `--function`, auto-selects the
single verifiable contract when `--contract` is absent, rejects >1 target
contract, and then requires the name to exist:

```cpp
        if (m.name != focus_func)
          continue;
        if (m.visibility != "public" && m.visibility != "external" &&
            config.options.get_option("no-visibility").empty())
          continue;
        if (m.name == focus_cnt)   continue;   // constructor
        if (m.name == "receive" || m.name == "fallback") continue;
        found = true;
```

Not found ⇒ `log_error("--focus-function '{}' is not a public/external function of contract '{}'.")` and `convert()` returns `true` (hard failure).

---

## 2. Matching semantics

| Question | Answer, from the code |
|---|---|
| Equality or prefix? | **Exact `std::string` equality** at both sites (`func_name != focus_func`, `m.name != focus_func`). No `rfind(...,0)`, no `compare(0,n,...)`, no `substr` anywhere on `focus_func` in the code I read. |
| Case sensitivity | Case-**sensitive** (plain `operator!=`). No normalisation anywhere. |
| What is `method.name`? | `funcSignatures[cname]` is built in `solidity_convert.cpp:1522–1556` from the solc AST: `func_name = func_node["name"]`, or `func_node["kind"]` for `receive`/`fallback`, or the contract name for the constructor. So the comparison is against the **source-level function name**. |
| Overloads (same name, different signature) | `funcSignatures` holds one entry **per FunctionDefinition**, keyed by AST id in `method.id` (`sol:@C@C@F@f#<node-id>`). The filter is name-only, so `--focus-function f` keeps **every overload named `f`** as a dispatcher branch. Each still resolves to its own exact declaration via the AST-node-id lookup at lines 377–420, so this is dispatch-all-overloads, not mis-binding. There is no way to select one overload. |
| `receive` / `fallback` | Explicitly **rejected as focus targets** by the validator (`solidity_convert.cpp:306`), even though the dispatcher does offer them as ordinary branches. So `--focus-function receive` always errors out. |
| Constructor | Rejected too (`m.name == focus_cnt`). |

### The README's "prefix-matched" claim is not implemented

`src/solidity-frontend/README.md:843–845` (in the *Structural coverage*
section) says:

> - Modifier-renamed functions (e.g. `deposit_onlyPositive`) are
>   prefix-matched, so `--focus-function deposit` targets the modified form.

There is no prefix match in the code. What actually happens is simpler, and the
observable behaviour the sentence describes is still correct — for a different
reason:

- `populate_function_signature()` runs from `populate_auxiliary_vars()` at the
  **top** of `convert()` (`solidity_convert.cpp:1081–1091`), i.e. **before** any
  modifier processing. So `funcSignatures[C]` contains `deposit`, never
  `deposit_onlyPositive`.
- The modifier machinery creates a *separate auxiliary* symbol:
  `get_modifier_function_name()` (`solidity_convert_modifier.cpp:1185–1194`)
  yields `name = func_name + "_" + mod_name`, `id = "sol:@C@<C>@F@<name>#0"`.
  `insert_modifier_json()` pushes a synthetic `FunctionDefinition` for it into
  the AST at lines 1136–1183 — again after `funcSignatures` was built.
- The public function `deposit` keeps its own name and its own id.

So `--focus-function deposit` works by **exact match on the original name**, and
the README's mechanism (prefix matching) does not exist. The sentence should be
corrected rather than relied on: nothing would keep working if someone tried
`--focus-function deposit_onlyPositive`, which is exactly what the sentence
invites — the validator would reject it, since `deposit_onlyPositive` is not in
`funcSignatures`.

---

## 3. Every site that reads focus-function

| # | file:line | what it does with the value | breaks under a set? |
|---|---|---|---|
| 1 | `src/esbmc/options.cpp:140–145` | Declares the option as `boost::program_options::value<std::string>()->value_name("name")`. Single-valued. | **Yes** — a second `--focus-function` is a boost "multiple occurrences" error. |
| 2 | `src/solidity-frontend/solidity_language.cpp:53` — `focus_func_name = config.options.get_option("focus-function");` | The **only** frontend read. Stored on `solidity_languaget::focus_func_name`, passed as the 6th ctor arg to `solidity_convertert` at line 334. | **Yes** — `get_option` returns one `std::string`. |
| 3 | `src/solidity-frontend/solidity_convert.h:1394` — `std::string focus_func;` (init at `solidity_convert.cpp:45`) | The converter's member. | Type change needed. |
| 4 | `src/solidity-frontend/solidity_convert.cpp:252–321` | Validation: incompatible-with-`--function`; auto-select single contract; require exactly one `--contract`; require the name to be a public/external non-ctor non-receive/fallback method of that contract. | Needs a loop; error text needs the offending name. |
| 5 | `src/solidity-frontend/solidity_convert_constructor.cpp:333–336, 358–361` | **The filter.** | One-line change (`!focus_funcs.count(func_name)`). |
| 6 | `src/esbmc/bmc.cpp:1134` — `goto_coveraget::audit_entry_liveness(options.get_option("focus-function"));` | Reached only under `--solidity-path-coverage` (inside `report_coverage`'s `is_path_cov` branch). | **Yes** — same `get_option` collapse. |
| 7 | `src/goto-programs/goto_coverage.cpp:1100–1218` — `audit_entry_liveness(const std::string &focus_function)` | (a) `is_focused` lambda, lines 1111–1116: `const std::string tag = "@F@" + focus_function + "#"; return unit.find(tag) != npos;` — exact match on the name segment of the unit id `sol:@C@<C>@F@<fn>#<id>`. (b) splits never-entered units into `dead` (defect → `abort()`) vs `dead_by_design` (focus-excluded → informational). (c) fills `units_not_entered[unit] = "excluded by --focus-function '" + focus_function + "'"`. (d) prints the message pinned by a regression test. | **Yes** — needs any-of over the set, and both strings need a joined rendering. |
| 8 | `src/goto-programs/goto_coverage.h:285–292` | Doc comment on `audit_entry_liveness`, explains why the audit must not abort on focus-excluded units. | Comment only. |
| 9 | `src/esbmc/esbmc_parseoptions.cpp:2734` | Comment only ("other contract methods not in `--focus-function`") inside `count_active_asserts`. **No read.** | No. |

**`esbmc_parseoptions.cpp` never reads `focus-function`.** In particular the
whole coverage dispatch (`process_goto_program`, lines 3878–4236) sets
`tmp.scope_contract` from `--contract` only; there is no focus-derived scoping,
no report naming keyed on focus, and no `--function` incompatibility check
there — that check lives in the frontend (site 4).

---

## 4. Change needed for a set of names

### Recommended shape: keep `std::string`, accept a separated list

Do **not** switch the declaration to `value<std::vector<std::string>>()`. The
reason is mechanical and would bite silently:

`src/util/options.cpp:54–69`

```cpp
void optionst::cmdline(cmdlinet &cmds)
{
  for (const auto &it : cmds.vm)
    if (cmds.isset(option_name.c_str()) && !it.second.defaulted())
      for (const auto &value : cmds.get_values(option_name.c_str()))
        if (value.empty()) set_option(option_name, true);
        else               set_option(option_name, value);   // OVERWRITES
}
```

`set_option` overwrites, so for a repeatable option `config.options` /
`optionst::get_option` keep only the **last** value. Sites 2 and 6 both read
through exactly that channel, so `--focus-function A --focus-function B` would
parse cleanly and then silently verify only `B`. The multi-value reads in the
tree (`--coverage-exclude-contract`, `--claim`, `--no-slice-name`) all go through
`cmdline.get_values(...)` instead — and `cmdlinet` is **not** reachable from
`solidity_languaget`, which only sees `config.options`.

A separated string sidesteps the collapse entirely and matches the two existing
precedents: `--contract` is a `std::string` split on whitespace in
`solidity_convert.cpp:985–991`, and `--tod-race-check` / `--tod-balance-check`
already use the `f1,f2` comma spelling (`esbmc_parseoptions.cpp:1804–1813`).

### Per-site diff sketch

| Site | Change | Rough size |
|---|---|---|
| `options.cpp:140–145` | Keep `value<std::string>()`; change `value_name("name")` → `value_name("name[,name...]")` and extend the help text. | ~4 lines (text) |
| `solidity_language.h` / `.cpp:53` | Split `config.options.get_option("focus-function")` on `,` (and/or whitespace, mirroring `--contract`), trim, drop empties, build `std::set<std::string>`. Change `focus_func_name` to that type and the ctor call at line 334. | ~12 lines |
| `solidity_convert.h:39, 1394` | Ctor param and member `std::string focus_func` → `std::set<std::string> focus_funcs`; `solidity_convert.cpp:45` init. | ~4 lines |
| `solidity_convert.cpp:252–321` | Wrap the "is it a real public/external method" scan in `for (const auto &fn : focus_funcs)`; collect the unresolved names and report them all at once. Emptiness tests (`!focus_func.empty()`) → `!focus_funcs.empty()`. | ~20 lines |
| `solidity_convert_constructor.cpp:335, 358` | `focus_applies` unchanged except `!focus_funcs.empty()`; filter becomes `if (focus_applies && focus_funcs.count(func_name) == 0) continue;` | 2 lines |
| `goto_coverage.h:292` + `goto_coverage.cpp:1100–1190` | `audit_entry_liveness(const std::set<std::string>&)`; `is_focused` becomes "does any name in the set produce a matching `@F@<name>#` tag"; the two message strings need a joined rendering of the set. | ~15 lines |
| `bmc.cpp:1134` | Split the option string at the call site (or add a small shared helper used by both here and `solidity_language.cpp` so the two splits cannot drift). | ~5 lines |

**Total: roughly 60 lines across 7 files, no new data structures.**

### What could silently break

1. **The `optionst` last-wins collapse** described above. This is the trap: the
   repeatable-flag form compiles, parses, and runs, and is wrong.
2. **A pinned regression message.**
   `regression/esbmc-solidity/solidity_path_cov_focus_function_same_enumeration/test.desc`
   pins, verbatim:
   `^--solidity-path-coverage: 1 unit\(s\) were not entered because --focus-function narrowed the dispatcher to 'pub'`
   plus `^Reached : 3$` and
   `^U Reasons: named-obstacle 0, unit-not-entered 5, bounded-holds 0, solver-unknown 0, not-solved-this-run 0$`.
   Any change to how the focus name is rendered in that message breaks it; and
   adding a second focus name to a run changes `Reached` and `unit-not-entered`.
   The test's own header explains it is deliberately pinning both the count and
   the *order* of the U-reason slots.
3. **The two `--bound` helper dispatchers do not apply the filter today, and a
   set will not change that.** `build_bound_drive_helper`
   (`solidity_convert_constructor.cpp:498–673`) and
   `build_esol_state_forward_helper` (695–861) build the same
   `if (nondet_bool()) obj.f(...)` ladder over `funcSignatures[c_name]` with
   **no** focus check. So under `--bound`, a contract-typed parameter driven
   through `_ESBMC_nondet_new_<C>` is exercised over *all* its public methods
   even when `--focus-function` is set. `focus_function_1/test.desc` runs
   `--bound`, so this is live. Whether that is intended (the filter's comment
   says other contracts keep full dispatch) or an oversight is a separate
   question — but if multi-focus is added, decide it explicitly rather than
   inheriting it.
4. **Overload ambiguity becomes easier to hit.** Name-only matching already
   dispatches every overload of a name; with a set, `A,B` where `B` is
   overloaded quietly widens the harness more than the user asked for. Nothing
   breaks, but the flag's help text should say so.
5. **Error policy on a bad name.** Today one unknown name aborts the whole run.
   With a set, "one bad name out of three" needs a decision. Aborting (and
   naming the offender) is the safe default; silently dropping it would make a
   typo look like a clean narrow run.
6. **`focus_applies` still requires `tgt_cnt_set.size() == 1`.** Names are not
   contract-qualified, so a set is resolved entirely against the single
   `--contract` target. `--focus-function A,B` across two contracts is still
   rejected — by the same validator, with the same message.

---

## 5. The dispatcher-entry vs inlined-callee question

**Short answer: this is already the normal situation under plain `--contract`,
and multi-focus changes nothing about it. The hazard is not new.**

Three separate findings, in increasing order of relevance:

### (a) The dual identity already exists, by construction

`get_unbound_function` (`solidity_convert_constructor.cpp:338–445`) offers
**every** public/external method of the target as a dispatcher branch. Those same
methods are simultaneously ordinary internal callees: the frontend lowers a
`this.f(a)` self-call to the very same direct `FUNCTION_CALL` as a plain `f(a)`
— stated at `goto_coverage.cpp:2836–2845`:

> Measured on a `this.f(a)` self-call: the frontend lowers it to the very same
> direct FUNCTION_CALL as a plain `f(a)` and models no success/failure edge for
> it, so at this layer an external SELF-call is indistinguishable from an
> internal one.

The path-coverage design note is explicit that this is intended
(`goto_coverage.cpp:2166–2180`):

> a `public` function that is also called internally is BOTH a unit of its own
> (entered from outside, free arguments) AND expanded into its internal caller's
> paths (entered with computed arguments). Both descriptions are needed and they
> describe different input spaces.

### (b) In ordinary verification there is no ABI-level guard at all

The premise of the hazard — that a dispatcher entry passes through a non-payable
`msg.value` check and a calldata decode that an internal call skips — **does not
hold outside `--solidity-path-coverage`**. The frontend does not model the
non-payable revert; it only stamps `#sol_payable` on the function type for the
Foundry generator (`solidity_convert_modifier.cpp:107–114`). The coverage pass
says so in as many words at `goto_coverage.cpp:3474–3478`:

> The frontend does not model this (measured: a payable and a non-payable
> function with identical bodies enumerate identically) […]

There is no calldata decode either: the dispatcher builds a typed
`side_effect_expr_function_callt` with nondet arguments directly. `msg.value` is
re-havoc'd per transaction by `_sol_per_tx_reseed()` for both entry kinds. So in
plain `--contract` / `--focus-function` runs, a dispatcher entry and an internal
call reach an identical body over identical ambient state.

### (c) Under `--solidity-path-coverage` the gate does exist — and its dual-identity handling is keyed on visibility, not on focus

The gate is synthesised by the **coverage pass**, into the unit's own body
(`goto_coverage.cpp:3509–3550`):

```
    IF msg_value == 0 THEN GOTO <original first instruction>
    _ESBMC_sol_mark_revert();      // makes exit_kind = revert
    GOTO <END_FUNCTION>
```

and the surrounding comment (3498–3508) states that placing it in the body is
correct *only because of the physical expansion that runs first*:

> before expansion, a `public` function that was also called internally had ONE
> body serving two entry kinds, and the gate invented a revert on the internal
> one (measured: `g() payable` internally calling `f() public` admitted an
> execution where `f` took the value-reject edge, which cannot happen on-chain).
> After expansion the internal caller holds its own gate-free COPY of the
> callee, and this body is reachable only through the dispatcher […] Both entry
> kinds are now right, with no precondition to weaken.

Two defences keep it that way, and both are about visibility, never about focus:

- `withdrawable` (2930–2932) refuses to withdraw a degradation call point whose
  callee is itself a unit, precisely because leaving the call unexpanded would
  route an internal call through the gated body;
- a residual (depth-bound) call to a unit marks **every path of the calling
  unit** as a named obstacle (3175–3194, 3230–3249).

The decisive point for the question: what counts as a "unit" — and therefore
what gets a gate and what gets expanded — is `is_external_entry`
(`goto_coverage.cpp:2819–2821`), which tests for the existence of
`<function-id>#_sol_save_this`. That symbol is created by the **frontend** for
public/external/receive/fallback functions only
(`solidity_convert_modifier.cpp:232–262`). It has nothing to do with
`--focus-function`. Focus narrows *which entry the harness invokes*, and nothing
else.

That is pinned by a regression test rather than inferred.
`regression/esbmc-solidity/solidity_path_cov_focus_function_same_enumeration`
runs `--solidity-path-coverage --contract C --focus-function pub` on a contract
whose `pub` is both a public entry and an internal callee of `caller`, and
requires:

```
^--solidity-path-coverage: expanded 2 internal call\(s\) into their calling unit
^--solidity-path-coverage: instrumented 8 complete path\(s\) across 2 unit\(s\)
```

— identical to its sibling `internal_call_expands`, which runs the same contract
under the **full** dispatcher. The test's own header states the intent:

> It should not [narrow the enumeration], because enumeration is a static DFS
> over the goto program at instrumentation time, whereas `--focus-function` only
> changes which entry the harness invokes.

What *does* differ under focus is reachability: `3 of 8` claims reach the solver
versus `7 of 8`, and `caller`'s 5 paths are reported `unit-not-entered`.

### Consequence for multi-focus

Adding `--focus-function A,B` makes `B` a dispatcher entry *in addition to*
being an inlined callee of `A`. Both roles already exist and are already handled:

- non-coverage runs: no gate on either role, so nothing to reconcile;
- path-coverage runs: `B` is already a unit (gated body) and already physically
  expanded into `A` (gate-free copy), whether or not the dispatcher offers it.

The only observable change is that `B`'s unit is now *entered*, so its paths move
out of the `unit-not-entered` bucket and into real verdicts. That is the intended
effect, not a hazard.

---

## Uncertainties

1. **The "every site" table is not proven exhaustive over the whole tree.** The
   workspace bans grep, and reading every file that could conceivably contain
   the string was beyond what I could do in one pass. Ranges I did **not** read:
   - `src/goto-programs/goto_coverage.cpp` lines 3550–6602 (of 6602)
   - `src/esbmc/bmc.cpp` lines 1400–3575 (of 3575)
   - `src/esbmc/esbmc_parseoptions.cpp` lines 4400–5062 (of 5062)
   - `src/solidity-frontend/solidity_convert_modifier.cpp` lines 1280–1825
   - not opened at all: `solidity_convert_call.cpp`, `_decl.cpp`, `_expr.cpp`,
     `_stmt.cpp`, `_ref.cpp`, `_util.cpp`, `_type.cpp`, `_mapping.cpp`,
     `_tuple.cpp`, `_builtin.cpp`, `_inheritance.cpp`, `_literals.cpp`,
     `solidity_monomorphize.cpp`, `solidity_tod_*.cpp`, and the Foundry /
     pytest / ctest generators.
   The structural argument that limits the risk: `focus_func` is a **protected
   member of `solidity_convertert`** (`solidity_convert.h:1394`) initialised
   only in the constructor, so any further frontend use must be in one of the
   unopened `solidity_convert_*.cpp` files; and outside the frontend the only
   channel is `options.get_option("focus-function")`, of which I found exactly
   one instance (`bmc.cpp:1134`). Treat the table as "complete for the files
   listed at the top", not as "complete for the tree".
2. **Whether `--contract A --contract B` actually works.** `README.md:139–142`
   documents both the repeated form and the `"A B"` form, but `--contract` is
   declared `value<std::string>()` (`options.cpp:137–139`) and read through
   `config.options.get_option("contract")`, then split on whitespace
   (`solidity_convert.cpp:985–991`). The repeated form would have to survive
   boost's multiple-occurrences handling and then the `optionst` last-wins
   collapse. I did not test it. It matters here because it is the precedent a
   multi-focus flag would be modelled on — worth settling before copying it.
3. **`regression/esbmc-solidity/focus_function_3` does not exist** (only 1, 2,
   4). I did not find where it went; it is not under a `disabled/` directory I
   could see from the top-level listing.
4. **`focus_function_2/test.desc` does not pass `--focus-function` at all** —
   it is the negative control (same contract, full dispatcher, expects
   `VERIFICATION FAILED`). Noting it so the test names are not misread as three
   focus tests.
5. I did not read `goto_coveraget::filter` / `set_target` / `is_target_func`
   (they live in the unread tail of `goto_coverage.cpp`). They are driven by
   `--function`, not `--focus-function`, in every call site I did read
   (`esbmc_parseoptions.cpp:3895, 3931, 3969, 4040, 4151`), but I have not seen
   their bodies.
