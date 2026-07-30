# Checked arithmetic and division by zero: what ESBMC actually models

Scope: `src/solidity-frontend/solidity_convert_expr.cpp` (read in full, 6532
lines), `src/goto-programs/goto_check.cpp` (read in full, 1313 lines),
`src/goto-programs/goto_program.{h,cpp}` (read in full),
`src/esbmc/esbmc_parseoptions.cpp` (read in full, 5062 lines),
`src/esbmc/options.cpp` (read in full), `src/solvers/smt/smt_overflow.cpp`
(read in full), `src/solidity-frontend/solidity_convert_stmt.cpp`
(lines 1-1400), `src/c2goto/library/solidity/` (listed; `solidity_builtins.c`,
`solidity_misc.c`, `solidity_types.h`, `solidity_units.c` read in full),
`docs/claude/solidity/approximation-ledger.md` (read in full).
Delegated full reads (§2 (vii)): `src/solvers/smt/smt_conv.{cpp,h}`,
`src/util/expr_simplifier.cpp`, `src/goto-symex/*`, and the z3 / bitwuzla /
boolector / cvc5 / smtlib backends.

---

## 1. Lowering of `/`, `%` and `+`

All three are produced by the *same* `switch (opcode)` in
`solidity_convertert::get_binary_operator_expr`. Every arm is a one-line
`exprt(<id>, t)` construction; there is no guard, no branch, no revert, no
library call, and no `#sol_*` annotation on any of them.

`src/solidity-frontend/solidity_convert_expr.cpp:5705-5741`:

```cpp
  case SolidityGrammar::ExpressionT::BO_Add:
  {
    if (t.is_floatbv())
      assert(!"Solidity does not support FP arithmetic as of v0.8.6.");
    else
      new_expr = exprt("+", t);
    break;
  }
  case SolidityGrammar::ExpressionT::BO_Sub:
  {
    if (t.is_floatbv())
      assert(!"Solidity does not support FP arithmetic as of v0.8.6.");
    else
      new_expr = exprt("-", t);
    break;
  }
  case SolidityGrammar::ExpressionT::BO_Mul:
  {
    if (t.is_floatbv())
      assert(!"Solidity does not support FP arithmetic as of v0.8.6.");
    else
      new_expr = exprt("*", t);
    break;
  }
  case SolidityGrammar::ExpressionT::BO_Div:
  {
    if (t.is_floatbv())
      assert(!"Solidity does not support FP arithmetic as of v0.8.6.");
    else
      new_expr = exprt("/", t);
    break;
  }
  case SolidityGrammar::ExpressionT::BO_Rem:
  {
    new_expr = exprt("mod", t);
    break;
  }
```

The only post-processing is the shared tail at
`solidity_convert_expr.cpp:5874-5886` — an implicit type conversion of the two
operands to the solc-declared `commonType`, then `copy_to_operands(lhs, rhs)`:

```cpp
  // 4.1 check if it needs implicit type conversion
  if (common_type.id() != "")
  {
    convert_type_expr(ns, lhs, common_type, expr);
    convert_type_expr(ns, rhs, common_type, expr);
  }
  else if (lhs.type() != rhs.type())
    convert_type_expr(ns, rhs, lhs, expr);

  // 4.2 Copy to operands
  new_expr.copy_to_operands(lhs, rhs);
```

**Answer to Q1:** `a / b` produces a bare `exprt("/", t)`, `a % b` a bare
`exprt("mod", t)`, and `a + b` a bare `exprt("+", t)` — the previously
established fact about `+` is confirmed, and `/` and `%` are lowered in exactly
the same way. **No guard, branch, or revert is synthesised by the frontend for
any arithmetic operator.**

Compound forms are the same story — `src/solidity-frontend/solidity_convert_expr.cpp:5972-5981`:

```cpp
  case SolidityGrammar::ExpressionT::BO_DivAssign:
  {
    new_expr = side_effect_exprt("assign_div");
    break;
  }
  case SolidityGrammar::ExpressionT::BO_RemAssign:
  {
    new_expr = side_effect_exprt("assign_mod");
    break;
  }
```

Two contrasts worth recording, because they show the frontend *does* know how
to synthesise a branch/revert when it wants to:

- `require` / `revert` (`solidity_convert_expr.cpp:2227-2271`) go through
  `build_revert_rollback_block`, which emits
  `if (!cond) { *this = _sol_save_this; return [nondet]; }` — a real control-flow
  exit.
- `--bounds-check` pointer-array indexing (`solidity_convert_expr.cpp:4570-4575`)
  emits a `code_assertt`, i.e. a claim, not a branch.

Neither mechanism is applied to `/`, `%`, `+`, `-`, `*`.

Also note `addmod` / `mulmod` in `src/c2goto/library/solidity/solidity_builtins.c:42-54`
are `wide % (uint512_t)k` with no `k != 0` guard, so they inherit whatever `%`
means at the SMT level (see §2).

---

## 2. Semantics of `a / 0` with the check OFF

### (c) — it falls through to the IR/SMT division semantics.

Reasoning, from code:

**(i) Nothing in the frontend constrains the divisor.** §1 above: the entire
lowering of `/` is `exprt("/", t)` plus operand typecasts. I paginated the whole
6532-line file; there is no `__ESBMC_assume(b != 0)`, no `if (b == 0)` ternary,
and no `Panic` emission anywhere in it.

**(ii) The only divisor constraint in the tool is inside the check itself, and
it is an `ASSERT`, not an `ASSUME`.** `src/goto-programs/goto_check.cpp:148-174`:

```cpp
void goto_checkt::div_by_zero_check(
  const expr2tc &expr,
  const guardt &guard,
  const locationt &loc)
{
  if (disable_div_by_zero_check)
    return;

  assert(is_div2t(expr) || is_modulus2t(expr));

  // add division by zero subgoal
  expr2tc side_2;
  if (is_div2t(expr))
    side_2 = to_div2t(expr).side_2;
  else
    side_2 = to_modulus2t(expr).side_2;

  expr2tc zero = gen_zero(side_2->type);
  assert(!is_nil_expr(zero));

  add_guarded_claim(
    notequal2tc(side_2, zero),
    "division by zero",
    "division-by-zero",
    loc,
    guard);
}
```

With `disable_div_by_zero_check` true this returns immediately and emits
nothing. Even when it *does* fire, `add_guarded_claim` produces an `ASSERT`
(§3) — so it never removes the `b == 0` case from the model; it only reports it.

The mechanism is worth stating precisely, because "there is a `divisor != 0`
expression in the formula" is easy to misread as a constraint. In
`src/goto-symex/symex_target_equation.cpp:764-768` an ASSERT step becomes
`assertions.push_back(invert_ast(implies(assumptions, cond)))`, and `convert()`
(`:687-688`) asserts the **disjunction of the negated** assertions. So
`divisor != 0` is something the solver is asked to *violate*; it is never
something the solver must respect. The `div2t` node itself reaches `bvudiv`
with the divisor entirely unconstrained, and a model with `divisor == 0` is
legal for the formula whether or not the claim is present.

**(iii) The check is OFF by default for Solidity.** Solidity implicitly sets
`--no-standard-checks`, `src/esbmc/esbmc_parseoptions.cpp:3445-3460`:

```cpp
    // Solidity implicit default: enable --no-standard-checks for any
    // Solidity run. C-level safety checks (pointer/align/vla/scanf/...)
    // emit false positives on Yul-lowered code, and the two
    // semantically-meaningful checks (bounds, div-by-zero) are now
    // opt-in via the positive --bounds-check / --div-by-zero-check.
    ...
      if (is_solidity)
        options.set_option("no-standard-checks", true);
```

and the umbrella expands to the individual negatives at
`esbmc_parseoptions.cpp:3503-3529` (pre-`goto_convert`) and again at
`esbmc_parseoptions.cpp:3683-3704` (pre-`goto_check`):

```cpp
      auto set_neg_unless_pos = [&](const char *neg, const char *pos) {
        if (!cmdline.isset(pos))
          options.set_option(neg, true);
      };
      ...
      set_neg_unless_pos("no-div-by-zero-check", "div-by-zero-check");
      ...
      set_neg_unless_pos("no-bounds-check", "bounds-check");
      set_neg_unless_pos("no-narrowing-check", "narrowing-check");
```

`--overflow-check` is separate and read positively in `goto_checkt`'s
constructor (`goto_check.cpp:32`), so it is off unless passed.

**(iv) Candidate (a) is ruled out.** EVM semantics (`DIV` by zero = 0) would
require an `if_exprt(b == 0, 0, a/b)` somewhere. It exists nowhere in the
Solidity frontend, and the approximation ledger records that it was
*deliberately removed* from the one place it used to be —
`docs/claude/solidity/approximation-ledger.md`, row 1:

> **Yul `div`/`mod`/`addmod`/`mulmod`** lower directly to `div_exprt`/`mod_exprt`
> (no if-guard) so goto_check's `--div-by-zero-check` (on by default) fires on
> reachable zero divisors and reports "division by zero". The prior if-guard
> `if(b==0) 0 else bvudiv(a,b)` silenced this check entirely (the false-branch's
> path guard `b != 0` made the assert tautological).

**(v) Candidate (b) is ruled out.** A `Panic(0x12)` revert would need either a
frontend-synthesised branch (§1: none) or a Solidity-specific `Panic` model in
`src/c2goto/library/solidity/` (§4: none). Both are absent.

**(vi) The ledger states (c) in as many words.** Same row 1, "False negatives"
column:

> (b) **`--no-div-by-zero-check` soundness gap**: with the check disabled, Yul
> `div(_, 0)` evaluates to SMT-LIB's bvudiv-zero result (all-1s = MAX), NOT
> Yul's spec value 0. Default checks ON closes this; opting out is the user's
> responsibility.

The statement is written about Yul `div`, but the divisor is unconstrained for
the *same reason* in the Solidity-level `/` case — both reach the goto layer as
a bare `div2t` and the check is the only thing that ever looks at the divisor.

**(vii) Confirmed at the SMT layer by direct read.** `div2t` / `modulus2t` are
pure type dispatch to a *total* SMT-LIB function — `src/solvers/smt/smt_conv.cpp:1565-1601`
(`case expr2t::div_id`):

```cpp
    else if (int_encoding)
    {
      a = mk_div(args[0], args[1]);
    }
    else if (is_unsignedbv_type(d.side_1) && is_unsignedbv_type(d.side_2))
    {
      a = mk_bvudiv(args[0], args[1]);
    }
    else
    {
      assert(is_signedbv_type(d.side_1) && is_signedbv_type(d.side_2));
      a = mk_bvsdiv(args[0], args[1]);
    }
```

and `smt_conv.cpp:2061-2083` for `modulus_id` (`mk_mod` / `mk_bvsmod` /
`mk_bvumod`). No `mk_eq(divisor, zero)`, no `mk_ite`, no `assert_ast`.

Confirmed clean, each read end-to-end with the line range recorded:
`smt_conv.cpp` (1..5536), `smt_conv.h` (1..1138),
`goto-symex/symex_target_equation.cpp` (1..1139) — the sole goto→SMT bridge,
which performs no arithmetic rewriting — `util/expr_simplifier.cpp`,
`solvers/z3/z3_conv.cpp` (1..1860) and `solvers/smtlib/smtlib_conv.cpp`
(1..1527). See Uncertainty 4 for what is *not* covered.

There is also no *guarded fallback* hiding in the base class: `smt_convt`'s own
`mk_bvudiv` / `mk_bvsdiv` / `mk_bvumod` / `mk_bvsmod` / `mk_div` / `mk_mod`
(`smt_conv.cpp:5246-5286`) are bare `abort()` stubs that every backend must
override. So the only possible behaviour is whatever the backend's native call
does, and each override that was read is three `assert()` sort checks plus the
native call.

Naming trap worth recording — `src/solvers/smtlib/smtlib_conv.cpp:36-41`:

```cpp
  "bvudiv",  /* SMT_FUNC_BVUDIV, */
  "bvsdiv",  /* SMT_FUNC_BVSDIV, */
  "bvsrem",  /* SMT_FUNC_BVSMOD, */
  "bvurem",  /* SMT_FUNC_BVUMOD, */
```

ESBMC's `mk_bvsmod` emits SMT-LIB **`bvsrem`**, not `bvsmod`; `mk_bvumod`
emits **`bvurem`**.

The simplifier explicitly declines to touch a literal zero divisor —
`src/util/expr_simplifier.cpp:883-884`, inside the `DivModtor` functor that
`div2t::do_simplify` / `modulus2t::do_simplify` delegate to:

```cpp
      // Denominator is zero? Don't simplify
      if (get_value(c2) == 0)
        return expr2tc();
```

Returning nil leaves the `div2t` intact for the SMT layer. There is no
`x/0 → 0` rule, and the comment at `:939-942` records that `x/x → 1` and
`x%x → 0` are deliberately absent so the div-by-zero VCC is not masked.

The clincher that the omission is deliberate rather than an oversight: the
`ieee_div_id` arm under int-encoding **does** build a divisor-zero `ite`
(`smt_conv.cpp:1896-1897` → `:2004`/`:2012`, `a = mk_ite(div_by_zero, inf_result, real_result);`).
The exact machinery needed for EVM `DIV`-by-zero semantics exists at this layer
and is applied only to floating-point division.

**Consequence for red-test generation.** With the check off, the model is free
to satisfy:

| expression | model (SMT-LIB) | real EVM opcode | real Solidity >= 0.8.0 |
|---|---|---|---|
| `a / 0` (uintN) | `bvudiv(a,0)` = all-ones = `type(uintN).max` | `0` | revert `Panic(0x12)` |
| `a % 0` (uintN) | `bvurem(a,0)` = `a` | `0` | revert `Panic(0x12)` |
| `a / 0` (intN) | `bvsdiv(a,0)` = `-1` if `a >= 0`, `1` if `a < 0` | `0` | revert `Panic(0x12)` |
| `a % 0` (intN) | `bvsrem(a,0)` = `a` | `0` | revert `Panic(0x12)` |

So the model disagrees with the real contract on the *value* **and** with real
Solidity on the *control flow*. A test generated from a model path that runs
through `a / 0` and then asserts on the result will not be red on the real
contract — it will revert before reaching the assertion.

**Third regime: under `--ir` / `--int-encoding` it is worse.** The first branch
of the dispatch (`smt_conv.cpp:1571`, `:2065`) sends `div2t` / `modulus2t` to
`mk_div` / `mk_mod`, which the SMT-LIB backend emits as bare `/` and `%`
(`smtlib_conv.cpp:36-41`). SMT-LIB leaves integer division by zero
**underspecified** — the solver may return *any* value, and different solvers
(or different runs) may return different ones. So under int-encoding `a / 0` is
not merely the wrong constant, it is nondeterministic. Bit-vector encoding is
the Solidity default, so this is not the common path, but any `--ir` run
inherits it.

---

## 3. Shape of the check when ON — **SINGLE-SUCCESSOR `ASSERT`**

This is the crux, and it is unambiguous. Every check in `goto_check.cpp`
(div-by-zero, overflow, narrowing cast, bounds, shift-UB, NaN, pointer-relation)
funnels through one emitter, `src/goto-programs/goto_check.cpp:1001-1029`:

```cpp
void goto_checkt::add_guarded_claim(
  const expr2tc &expr,
  const std::string &comment,
  const std::string &property,
  const locationt &location,
  const guardt &guard)
{
  expr2tc e = expr;

  // first try simplifier on it
  base_type(e, ns);
  simplify(e);

  if (!options.get_bool_option("all-claims") && is_true(e))
    return;

  // add the guard
  expr2tc new_expr = guard.is_true() ? e : implies2tc(guard.as_expr(), e);

  // Check if we're not adding the same assertion twice
  if (assertions.insert(new_expr).second)
  {
    goto_programt::targett t = new_code.add_instruction(ASSERT);
    t->guard = new_expr;
    t->location = location;
    t->location.comment(comment);
    t->location.property(property);
  }
}
```

`new_code.add_instruction(ASSERT)` constructs a default `instructiont(ASSERT)`
(`goto_program.h:619-624`, `goto_program.h:371-383`) whose `targets` list is
**empty** — `has_target()` is false, and nothing in `add_guarded_claim` ever
calls `set_target` / `make_goto`. There is no second exit.

The CFG confirms it at `src/goto-programs/goto_program.cpp:301-308`:

```cpp
  else if (i.is_assume() || i.is_assert())
  {
    // This is an ASSERT or ASSUME with a guard that
    // might hold (i.e., definitely not FALSE), so the next target
    // might be reached.
    if (!is_false(i.guard))
      successors.push_back(next);
  }
```

Exactly one successor — the textually next instruction. Contrast the `GOTO`
arm immediately above it (`goto_program.cpp:274-294`), which pushes both the
jump targets *and* `next`. An `ASSERT` is a claim in the fall-through path;
it does **not** fork control flow, and consequently:

- a checked-arithmetic failure is **not** a distinct path;
- it cannot be enumerated as a separate complete-path class;
- the "failure" continuation of `a/b` and the "success" continuation are the
  same edge, carrying the same (unconstrained-divisor) value.

The instructions are spliced in *before* the checked instruction —
`goto_check.cpp:1282-1288`:

```cpp
    // insert new instructions -- make sure targets are not moved
    while (!new_code.instructions.empty())
    {
      goto_program.insert_swap(it, new_code.instructions.front());
      new_code.instructions.pop_front();
      ++it;
    }
```

so the shape is a linear `ASSERT cond; <the original instruction>`, never a
diamond.

For completeness, the checks other than div-by-zero that a Solidity run can
turn on all use the same emitter:

- overflow: `goto_check.cpp:373-378` (`add_guarded_claim(overflow, "arithmetic overflow on " + get_expr_id(expr), "overflow", loc, guard)`)
- narrowing cast: `goto_check.cpp:319-320`
- narrowing assignment: `goto_check.cpp:1256-1261`
- array bounds: `goto_check.cpp:988` and `:998`

and the frontend's own opt-in bounds check emits a `code_assertt`
(`solidity_convert_expr.cpp:4570-4575`, `:4620-4625`), which is likewise a claim.

**Answer to Q3: SINGLE-SUCCESSOR `ASSERT`.** No two-way branch, no revert exit,
anywhere in the checked-arithmetic machinery.

### 3a. Side finding: `--overflow-check` alone already flags division by zero

Worth knowing, because it changes which flag you need. `goto_check.cpp:1143-1155`
routes `div_id` / `modulus_id` into **both** `div_by_zero_check` *and*
(via fallthrough) `overflow_check`:

```cpp
  case expr2t::div_id:
  case expr2t::modulus_id:
    div_by_zero_check(expr, guard, loc);
    /* fallthrough */

  case expr2t::neg_id:
  case expr2t::add_id:
  case expr2t::sub_id:
  case expr2t::mul_id:
  {
    overflow_check(expr, guard, loc);
    break;
  }
```

and the SMT lowering of that `overflow2t` for an **unsigned** divisor makes
"divisor == 0" an overflow condition — `src/solvers/smt/smt_overflow.cpp:158-188`:

```cpp
  case expr2t::div_id:
  case expr2t::modulus_id:
  {
    if (is_signed)
    {
      // Handle signed division/modulus overflow cases
      // Dividing the most negative integer (MIN_INT) by -1 causes overflow
      ...
      return convert_ast(and2tc(is_minus_one, is_min_int));
    }

    // Detect unsigned integer overflow for division and modulus
    // Overflow occurs when dividing by zero
    expr2tc is_div_by_zero = equality2tc(opers.side_2, zero);

    // Overflow occurs if the dividend is greater than the maximum representable value
    expr2tc max_unsigned = constant_int2tc(
      opers.side_1->type, BigInt::power2(opers.side_1->type->get_width()) - 1);
    expr2tc is_overflow = greaterthan2tc(opers.side_1, max_unsigned);

    // Return overflow condition for unsigned division/modulus
    return convert_ast(or2tc(is_div_by_zero, is_overflow));
  }
```

The second disjunct (`side_1 > 2^width - 1`) is unsatisfiable for a bitvector of
that width, so for `uintN` this reduces to exactly `divisor == 0`. Consequences:

- On Solidity (`uint256` everywhere) `--overflow-check` **alone** will report a
  reachable zero divisor, as a claim labelled `"arithmetic overflow on div"`
  with property `"overflow"` — not `"division-by-zero"`.
- Conversely, for a **signed** (`intN`) divisor the overflow arm checks only
  `MIN_INT / -1`; the zero-divisor case there is covered *only* by
  `--div-by-zero-check`.
- Both are still `add_guarded_claim` → single-successor `ASSERT`. This changes
  *which flag reports it*, not the control-flow shape.

---

## 4. Solidity-specific `Panic` handling; `unchecked { }` blocks

### Panic(0x11) / Panic(0x12): **not modelled at all.**

- `src/c2goto/library/solidity/` contains: `solidity_abi.c`,
  `solidity_address.c`, `solidity_array.c`, `solidity_blockchain.c`,
  `solidity_builtins.c`, `solidity_bytes.c`, `solidity_crypto.c`,
  `solidity_mapping.c`, `solidity_misc.c`, `solidity_string.c`,
  `solidity_types.h`, `solidity_units.c`. `solidity_builtins.c` and
  `solidity_misc.c` (the two that would host such a model — they hold
  `sol_pow_uint`, `addmod`/`mulmod`, `selfdestruct`, the revert-observation
  flag) contain **no** `Panic`, no error-selector constant, and no
  arithmetic-failure path. `addmod`/`mulmod` do a raw `% k` with no `k != 0`
  guard.
- The frontend's revert machinery is name-driven: `sol_name == "revert"` /
  `"require"` in `get_call_expr` (`solidity_convert_expr.cpp:2227`, `:2246`) and
  the `RevertStatement` arm in `solidity_convert_stmt.cpp:984-1016`. It is
  reached only from a *source-level* `revert` / `require` / `revert CustomError`.
  Nothing routes an arithmetic result into it.
- `goto_check`'s checks carry `location.property("overflow")` /
  `("division-by-zero")` — plain ESBMC property tags, not EVM panic codes.
- The try/catch lowering explicitly documents that ESBMC cannot tell panic
  classes apart (`solidity_convert_stmt.cpp:1120-1122`):

  > Build the catch arm: single clause, or multiple clauses chained with a
  > nondet selector (ESBMC cannot tell Error/Panic/low-level apart, so which
  > handler runs is over-approximated as nondet).

So a `try { } catch (Panic) { }` around arithmetic will not correlate with a real
overflow, and there is no arithmetic-originated revert for the
revert-observation flag (`_ESBMC_sol_reverted_flag`, `solidity_misc.c:226-244`)
to observe.

### `unchecked { }`: a **location flag**, honoured by the overflow checks only.

Set in `src/solidity-frontend/solidity_convert_stmt.cpp:144-158`
(`solidity_convertert::get_block`, `BlockT::Statement` arm):

```cpp
    // Track unchecked blocks: save/restore flag using RAII pattern
    const bool is_unchecked = (block["nodeType"] == "UncheckedBlock");
    const bool prev_unchecked = in_unchecked_block;
    if (is_unchecked)
      in_unchecked_block = true;

    code_blockt _block;
    unsigned ctr = 0;
    // items() returns a key-value pair with key being the index
    for (auto const &stmt_kv : stmts.items())
    {
      locationt cl;
      get_location_from_node(stmt_kv.value(), cl);
      if (in_unchecked_block)
        cl.set("#sol_unchecked", "1");
```

restored at `solidity_convert_stmt.cpp:220-221`. It is a *statement location*
attribute — it changes nothing about the emitted expression tree, and the
arithmetic is still a bare `exprt("+", t)` inside an `unchecked` block, exactly
as outside one.

It is consumed in three places, all in `goto_check.cpp`, and all on the
overflow side:

```cpp
  // Skip overflow checks inside Solidity unchecked blocks
  if (
    config.language.lid == language_idt::SOLIDITY &&
    loc.get("#sol_unchecked") == "1")
    return;
```
(`goto_check.cpp:337-341`, in `overflow_check`)

```cpp
    // Skip overflow checks inside Solidity unchecked blocks
    if (loc.get("#sol_unchecked") == "1")
      return;
```
(`goto_check.cpp:280-282`, in `cast_overflow_check`)

and `goto_check.cpp:1233-1235`, gating the narrowing-assignment claim.

`div_by_zero_check` (`goto_check.cpp:148-174`) does **not** consult
`#sol_unchecked` — which matches real Solidity, where `unchecked` suppresses
`Panic(0x11)` but *not* `Panic(0x12)`.

Two caveats on the flag's reach that matter if you rely on it:

1. It is stamped on statements that are **direct children** of the
   `UncheckedBlock`'s statement list. Sub-expressions get their own locations
   from `get_expr`; the goto instruction inherits the statement location, which
   is what `goto_check` reads, so the common case works — but a construct that
   rebuilds an instruction's location downstream would drop it (the same class
   of hazard the `sol_source_return` marker at
   `solidity_convert_stmt.cpp:449-460` was written to work around, and which is
   documented there as MEASURED).
2. Because `unchecked` is *only* an overflow-check suppressor and the overflow
   check is off by default anyway, `unchecked { }` and a plain block produce a
   **byte-identical model** under the default Solidity flag set.

---

## 5. Approximation ledger entry

`docs/claude/solidity/approximation-ledger.md` **exists** (71 lines, 24 rows).

There is **no row dedicated to Solidity-level division by zero or to checked
arithmetic**. The nearest — and only — entry is **row 1, "Inline assembly"**,
which covers Yul `div`/`mod`/`addmod`/`mulmod`. Verbatim, the two relevant
fragments:

Rationale column:

> **Yul `div`/`mod`/`addmod`/`mulmod`** lower directly to `div_exprt`/`mod_exprt`
> (no if-guard) so goto_check's `--div-by-zero-check` (on by default) fires on
> reachable zero divisors and reports "division by zero". The prior if-guard
> `if(b==0) 0 else bvudiv(a,b)` silenced this check entirely (the false-branch's
> path guard `b != 0` made the assert tautological).

False-negatives column, item (b):

> **`--no-div-by-zero-check` soundness gap**: with the check disabled, Yul
> `div(_, 0)` evaluates to SMT-LIB's bvudiv-zero result (all-1s = MAX), NOT
> Yul's spec value 0. Default checks ON closes this; opting out is the user's
> responsibility.

Note the internal inconsistency worth flagging: row 1 asserts
`--div-by-zero-check` is "on by default", but `esbmc_parseoptions.cpp:3445-3460`
+ `:3697` make it **off** by default for every Solidity run (implicit
`--no-standard-checks`), and `options.cpp:645-648` documents the flag as
"Solidity: opt-in, default OFF". So for a `.sol` input the ledger's
"False negatives (b)" branch is the *default* regime, not the opt-out one.

There is no ledger row for:

- Solidity-level `/` and `%` by zero (as opposed to Yul's);
- the absence of `Panic(0x11)` / `Panic(0x12)` modelling;
- checked arithmetic being a claim rather than a control-flow exit.

---

## Uncertainties

1. **The old-irep simplifier folds `0 / x → 0` for a *symbolic* divisor.**
   `src/util/simplify_expr.cpp:592-598` and `:683-687` fold `0 / x → 0` and
   `0 % x → 0` gated on `ok0 && int_value0 == 0` — i.e. requiring only the
   *numerator* to be the constant zero, not the divisor. On that path a
   `0 / x` with symbolic `x` is erased before any check can see it, so a
   div-by-zero VC would be silently dropped even with `--div-by-zero-check` on.
   The irep2 simplifier (`expr_simplifier.cpp:901-904`) correctly requires
   **both** operands constant. Whether the Solidity pipeline ever routes through
   the old-irep simplifier was not traced. This is narrow (`0 / x` specifically,
   not `a / x`) but it is a real asymmetry between the two simplifiers and
   worth a follow-up if div-by-zero detection is ever relied on.

2. **`#sol_unchecked` propagation depth.** I verified where it is set and where
   it is read. I did not trace every path by which an instruction's `location`
   can be rebuilt between `get_block` and `goto_check` (`get_statement`'s own
   `new_expr.location() = loc`, the front/back-block flush, `move_to_front_block`
   on hoisted sub-expressions). The `sol_source_return` comment in the same file
   documents that at least two such overwrites exist and had to be worked around
   individually, so an `unchecked` arithmetic expression that gets hoisted into
   a front block may lose the flag. Since the overflow check is off by default
   this is currently unobservable, but it would matter under `--overflow-check`.

3. **Whether any *other* pass adds a divisor constraint.** I read `goto_check.cpp`
   in full and `esbmc_parseoptions.cpp` in full (which is where every goto-level
   pass is dispatched from: `remove_no_op`, `remove_unreachable`, the
   preprocess algorithms, inlining, GCSE, interval analysis, k-induction,
   `goto_check`, contracts, race assertions, the coverage passes). None of them
   is an arithmetic-guard inserter. I did not read `interval_analysis` itself —
   it is opt-in via `--interval-analysis` / `--goto-contractor` and adds
   *assumes* derived from an abstract domain, so in principle it could
   incidentally exclude `b == 0`; under the default flag set it does not run.

4. **Backends: coverage is delegated, and the two delegated passes disagreed on
   their own scope.** `smt_conv.cpp` (5536 lines), `smt_conv.h`,
   `expr_simplifier.cpp`, `symex_target_equation.cpp` and `z3_conv.cpp` /
   `smtlib_conv.cpp` were read end-to-end in both passes, with "none found"
   recorded against explicit line ranges — those I am confident in. Bitwuzla,
   boolector and cvc5 were reported clean by the first pass and listed as
   unread by the second; since each `mk_bvudiv` is a three-`assert()`-plus-
   native-call wrapper in every backend that *was* read, a guard hiding in one
   of them is implausible but not excluded by my own reading. `mathsat/`,
   `yices/`, `cvc4/` and `sat/bitblast_conv.cpp` were read by neither.
   Bitwuzla is the auto-selected default for Solidity
   (`esbmc_parseoptions.cpp:1596`), so the fully-unexamined backends are off
   the default path. `src/pointer-analysis/dereference.cpp` was not read.

   Structural bound on how much this can matter: `smt_convt`'s base
   `mk_bvudiv` / `mk_bvsdiv` / `mk_bvumod` / `mk_bvsmod` (`smt_conv.cpp:5246-5286`)
   are `abort()` stubs, so an unread backend cannot be *silently* inheriting a
   guarded fallback — it either overrides with its native call or crashes.

   Every delegated pass closed by flagging `solidity_convert_expr.cpp`'s
   `BO_Div` lowering as "the one place EVM semantics could still hide". That
   caveat is **discharged**: I read that file in full myself (6532 lines) and
   the lowering is the bare `exprt("/", t)` quoted in §1. No delegated
   uncertainty remains on the frontend side.
