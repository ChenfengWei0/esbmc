I've read the four files (plus the existing regression fixtures) end to end. Here is the plan.

---

# Implementation plan — `--path-cov-assert <json>`: post-state assertion synthesis and certification for one enumerated path

**Status of this document:** design + file/line anchors only. No code was written, nothing was built or run.

Everything below is anchored to the tree at `/home/samson/workspace/esbmc` (branch `solidity`). Line numbers are from the current files.

---

## 0. Where the mode lives, and why there

Stage 2 already has two branches at the **end of `solidity_path_coverage()`**, both of which `continue` out of the per-unit loop:

- OUTER BOX — `goto_coverage.cpp:4994` `// ---- STAGE 2, step 1: the outer-box batch ----`, body 5000–5374.
- CERTIFY — `goto_coverage.cpp:5376` `// ---- STAGE 2: the certification query ----`, body 5387–5668.

The new mode is a **third branch in the same place**, for the reason stated verbatim at 5376–5386:

> "Everything above still ran — expansion, the ABI gate, Phase-1 `tr`/`cnt` accounting, the `tr`-completeness invariant, the exit census, the decision-set census — because they are the defences, and certifying against accounting that nothing checked would be certifying nothing. It also matters that the query below reads the SAME `tr` the enumeration writes."

That argument transfers unchanged: the assertion's antecedent is `tr != enc || cnt != depth`, i.e. the very same accounting.

**Placement inside the branch chain matters.** The three branches are mutually exclusive by `continue`, and today `outer_on` is tested first (5000) so `--path-cov-outer-box` + `--path-cov-certify` together silently makes certify never fire — after which `certify_units_matched == 0` and the run exits 1 at 5814 with a message blaming the *unit name*. Do not extend that hazard: reject any two of the three stage-2 flags at the dispatch site (see §8).

---

## 1. The JSON spec

### 1.1 Format

```json
{
  "unit":  "<fn name or full unit id>",
  "enc":   5,
  "depth": 2,
  "region": [
    { "name": "amt", "lo": "1", "hi": "100", "holes": ["7"] },
    { "name": "state.bal", "lo": "50", "hi": "50" },
    { "name": "msg.value", "lo": "0", "hi": "0" }
  ],
  "vars": [
    { "name": "bal",
      "abs_lo": "0",   "abs_hi": "100",
      "delta_dir": "inc", "delta_lo": "1", "delta_hi": "100" }
  ]
}
```

- `unit` / `enc` / `depth` — identical semantics and identical parse to certify: `j.at("unit").get<std::string>()`, `j.at("enc").get<uint64_t>()`, `j.at("depth").get<uint64_t>()` (`goto_coverage.cpp:2694–2696`).
- `region` — **the same shape `--path-cov-certify` already parses under the key `box`**. `lo`/`hi`/`holes` are decimal **strings**, never JSON numbers, for the reason recorded at `goto_coverage.cpp:2524–2534`:

  > "`lo`/`hi`/`holes` are decimal STRINGS, not JSON numbers: Solidity inputs are up to 256 bits and a JSON number would be silently truncated to a double on the way in — a certified box quietly covering the wrong region is the one outcome this query exists to prevent."

- `vars` — optional per-variable constants for the last two rungs. Omitting `abs_*` omits that rung for that variable; omitting `delta_*` omits the delta rung. Omitting `vars` entirely still emits R1 and the four sign rungs for every eligible state variable (see §2), which is the intended default.

### 1.2 Can the certify parser be reused as-is?

**No — it is not a function.** The certify spec parse is an inline block, `goto_coverage.cpp:2681–2760`, inside `solidity_path_coverage()`, and its record type is a **function-local struct**:

```cpp
// goto_coverage.cpp:2535
  struct certify_boundt
  {
    std::string name, lo, hi;
    std::vector<std::string> holes;
  };
```

with the bound loop at 2697–2706.

**Edit (not new code):** hoist `certify_boundt` to file scope — put it immediately above `walk_fields` (`goto_coverage.cpp:550`), which is already the file-scope home of the coordinate machinery — rename it `path_cov_boundt`, and extract the loop into a file-scope helper:

```cpp
// NEW, file scope, next to walk_fields (goto_coverage.cpp ~ line 545)
static void parse_bounds(const nlohmann::json &j,
                         const char *key,
                         std::vector<path_cov_boundt> &out);
```

taking the JSON key name as a parameter so certify keeps `"box"` and the new mode uses `"region"`. Then `goto_coverage.cpp:2697–2706` becomes one call, and the new branch's parse becomes the same call.

Extract rather than duplicate, and the reason is not tidiness: §6 shows that **four of the five documented false-certificate routes are properties of this parse plus the structural gate below it**. A copy is a copy that will not receive the next fix. The regression plan (§7, fixture group 7) pins exactly this by re-running three existing certify refusal fixtures through the new flag.

The `ce` map (`goto_coverage.cpp:2707–2709`, feeding `path_cov_certify_ce`) is **not** parsed by the new mode: it exists to turn a refutation into the next box, and a refuted post-state candidate is not shrunk, it is dropped from the ladder.

### 1.3 New CLI plumbing

**`src/esbmc/options.cpp`** — new entry in the `"Coverage options"` group, immediately after `path-cov-outer-box` (which occupies lines 909–922) and before `solidity-path-coverage` (923):

```cpp
{"path-cov-assert",
 boost::program_options::value<std::string>()->value_name("file"),
 "Synthesise and CERTIFY post-state assertions for ONE enumerated path over "
 "an input REGION. JSON: {unit, enc, depth, region:[{name,lo,hi,holes}], "
 "vars:[{name,abs_lo,abs_hi,delta_dir,delta_lo,delta_hi}]}. The region is "
 "ASSUMED at entry (exactly the require/bound a generated Foundry test would "
 "carry) and each candidate is asserted at THAT path's own exit under the "
 "path-identity antecedent, so it is vacuous on every other path. The "
 "assumption is fixed and only the assertions vary, so the whole ladder is "
 "judged in ONE run. A refuted candidate is the ladder working, not a "
 "failure; the run's verdict line is therefore NOT the result — the "
 "per-candidate HOLDS / REFUTED / no-verdict report is."},
```

**`src/esbmc/esbmc_parseoptions.cpp`** — the dispatch block is `if (cmdline.isset("solidity-path-coverage"))` at **4118**, and the two stage-2 reads are at **4200–4203**:

```cpp
      if (cmdline.isset("path-cov-certify"))
        tmp.path_cov_certify_path = cmdline.getval("path-cov-certify");
      if (cmdline.isset("path-cov-outer-box"))
        tmp.path_cov_outer_box_path = cmdline.getval("path-cov-outer-box");
```

Add the third read there, plus the exclusivity check (§8).

**`src/goto-programs/goto_coverage.h`** — new instance member beside the other two (h:740 `path_cov_certify_path`, h:742 `path_cov_outer_box_path`):

```cpp
  // ---- STAGE 3: POST-STATE ASSERTION SYNTHESIS (--path-cov-assert <json>) ----
  std::string path_cov_assert_path = "";
```

New statics beside `report_outer_boxes()` (h:907), with definitions added to the block at `goto_coverage.cpp:65–88`:

```cpp
  struct assert_candidatet
  {
    uint64_t enc = 0;
    std::string var;   // state variable base name
    std::string rung;  // "eq" | "ne" | "ge" | "le" | "gt" | "lt" | "abs" | "delta"
    std::string text;  // human-readable candidate, e.g. "post_bal >= pre_bal"
    std::pair<std::string, std::string> key; // claim key, to read the verdict
  };
  static bool path_cov_assert_mode;
  static std::vector<assert_candidatet> path_cov_assert_candidates;
  static void report_path_cov_assertions();
```

`path_cov_refused_coords` (h:903) is **reused**, not duplicated — see §2.3 and §6.

---

## 2. Enumerating and snapshotting the scalar state variables

### 2.1 Getting the contract instance object and its components

The existing helper is `resolve_coord`'s `state.` branch, `goto_coverage.cpp:3340–3367`:

```cpp
      const symbolt *obj = nullptr;
      cov_context->foreach_operand([&](const symbolt &s) {
        if (obj != nullptr)
          return;
        const std::string id = s.id.as_string();
        if (
          id.rfind("sol:@_ESBMC_Object_", 0) == 0 &&
          (scope_contract.empty() ||
           id.find(scope_contract) != std::string::npos))
          obj = &s;
      });
      if (obj == nullptr)
        return false;
      const typet ostruct = ns.follow(obj->type);
      if (ostruct.id() != "struct")
        return false;
      out = symbol2tc(migrate_type(ostruct), obj->id);
      return walk_fields(ns, out, field);
```

The new mode needs the same object but **iterated rather than looked up**. Factor the object lookup out of `resolve_coord` into a small lambda declared just above it (`goto_coverage.cpp:3324`), so both callers use one implementation:

```cpp
  auto contract_object = [&]() -> const symbolt * { /* body = lines 3345-3356 */ };
```

Then enumeration is:

```cpp
  const symbolt *obj = contract_object();
  const typet ostruct = ns.follow(obj->type);
  for (const auto &comp : to_struct_type(ostruct).components())
  { ... }
```

Variable name = `comp.get("#base_name")` when non-empty, else `comp.get_name()` — that is exactly the pair `walk_fields` matches on (`goto_coverage.cpp:565–566`), so a name reported here is a name `state.<name>` can be written back as.

`pre_v` and `post_v` are both built from
`expr2tc live = symbol2tc(migrate_type(ostruct), obj->id); walk_fields(ns, live, name);`
— i.e. the same member expression `resolve_coord` produces for `state.<name>`.

### 2.2 Bookkeeping fields that must be skipped

The object carries ESBMC's own fields. Two independent places in the tree already state the filter, and the new code must use the same one or it will emit candidates about `$address`:

- `bmc.cpp:3357` (the `*this` whole-object restore): `if (mn.empty() || mn[0] == '$' || mn.rfind("_ESBMC", 0) == 0 || mn.rfind("anon_pad", 0) == 0) continue;`
- `bmc.cpp:3372`: `if (!bare.empty() && bare[0] != '$' && bare.rfind("_ESBMC", 0) != 0)`

Use the four-way form (`bmc.cpp:3357`) — it is the stricter of the two.

### 2.3 Which types must be REFUSED

`coord_expressible` (`goto_coverage.cpp:2285–2345`) is the whitelist, and its own header comment says why the direction is fixed:

> "So the test is 'is it one of the kinds we can bound?' and NOT 'is it one of the kinds we know to be broken'. Unrecognised types are unbounded in number: three projects fell over on three DIFFERENT ones (mapping, string, calldata struct) with the identical failure shape, and a blacklist would have caught at most the one that was written down."

So the candidate ladder is emitted **only** for `is_unsignedbv_type`. Refused with the reason string `coord_expressible` fills in:

| kind | line | why it must be refused here |
|---|---|---|
| signed bit-vector | 2316 | the constant is built on the coordinate's own type; `2^256-1` is `-1` under `bvsle`. Same hole, arrived at through the ladder constants instead of the box. |
| array | 2325 | "the frontend lowers strings, bytes, mappings and dynamic arrays to arrays" |
| struct / union | 2329 | needs a coordinate per field — a different coordinate kind |
| pointer | 2333 | a contract/interface handle, "not an input a test can set" |
| bool | 2337 | "a two-point domain has no interval to measure" — note R1 (`==`/`!=`) *would* be expressible on a bool, but R2 is not, and mixing would produce a half-ladder. Refuse and say so. |

**The second, harder omission.** `resolve_coord`'s own SCOPE note (`goto_coverage.cpp:3308–3318`) is the key fact:

> "`state.<field>` resolves against the contract INSTANCE OBJECT's struct components, so it covers scalar state variables and nothing else. A mapping or dynamic array is NOT a field of that object — the frontend lowers those to contract-scope globals (`sol:@C@<C>@<name>`) — so `state.balances` does not resolve"

A mapping therefore **never appears in the component list at all**. Iterating components and reporting "these are the state variables" would silently drop it, and per the `path_cov_refused_coords` header (h:876–893):

> "In the query, 'the box omits c' and 'c is unconstrained' are the SAME constraint — so a refused coordinate that simply vanished from the report would read as 'measured, and it came out as the whole type'"

So the enumeration is **two scans, not one**:

1. the object's struct components (above);
2. a second `cov_context->foreach_operand` scan for contract-scope stores, using the identical test the slicing exemption uses at `goto_coverage.cpp:2794–2796` and the CE harvest uses at `bmc.cpp:3286–3288`:
   `id.rfind("sol:@C@",0)==0 && id.find("@F@")==std::string::npos`
   (scoped to `scope_contract` when set). Every hit is recorded in `path_cov_refused_coords` with the reason *"a mapping or dynamic array: lowered to a contract-scope global, not a component of the contract object, so no scalar candidate can be formed"* — and **no** candidate is emitted.

This mirrors `path_ce_t::state_written_unrendered` (h:502–506), which exists for exactly this reason: "omitting them entirely would let a consumer infer 'unchanged', which is a silent wrong conclusion."

### 2.4 Snapshotting `pre_v` at entry

Copy the outer-box snapshot verbatim — `goto_coverage.cpp:5070–5096`:

```cpp
        const type2tc ct = cexpr->type;
        symbolt ssym;
        ssym.type = migrate_type_back(ct);
        ssym.name = "__ESBMC_outer$" + i2string(ghost_counter++);
        ssym.id = "path_cov::" + id2string(ssym.name);
        ssym.lvalue = true;
        ssym.static_lifetime = false;
        ssym.is_extern = false;
        symbolt *psn;
        cov_context->move(ssym, psn);
        expr2tc sn = symbol2tc(migrate_type(psn->type), psn->id);
        auto entry = goto_program.instructions.begin();
        goto_programt::instructiont dcl;
        dcl.type = DECL;
        dcl.code = code_decl2tc(ct, psn->id);
        dcl.location = entry->location;
        dcl.location.property("skipped");
        ...
        goto_program.instructions.insert(entry, asg);
```

with `__ESBMC_outer$` → `__ESBMC_pre$`. Three details that are load-bearing:

- `.location.property("skipped")` — without it the instruction enters the decision-set census as a user construct and (for the ASSUME below) is misread as a lowered-away branch; the certify branch says so at 5613–5615.
- plain `instructions.insert`, **not** `insert_swap`. The ABI gate comment at `goto_coverage.cpp:3536–3538` records what `insert_swap` does here: "insert_swap moves the instruction's CONTENT, so the iterator that named the original first instruction ends up naming the newly inserted one — the branch below then targets itself and the function acquires a self-loop."
- `cov_context` must be non-null; already enforced for the whole pass at `goto_coverage.cpp:2388–2395`.

The snapshot is taken from `live` — the same member expression `post_v` will read at the exit. Its purpose is the one the outer-box branch states at 5008–5012: "a parameter may have been reassigned in between; asserting on the live symbol would then bound the wrong value."

> **UNVERIFIED, and it is the single most important open point.** I have confirmed that reading `state.<field>` off the object symbol works **at entry** (the outer-box branch does exactly that, and `regression/esbmc-solidity/solidity_path_cov_entry_storage_pinned/test.desc:4` pins a run measured under `state.bal == 50`). I have **not** confirmed that the same expression, evaluated **at the exit**, observes the writes the unit performed — i.e. whether a Solidity `this->x = v` inside the unit body updates that same `sol:@_ESBMC_Object_*` symbol or goes through a `this` pointer parameter that symex may treat as a distinct object. `goto_coverage.cpp:2773–2776` asserts the former ("a Solidity `this->x = v` is an update of THIS symbol"), which is evidence but not proof for the exit read.
> To settle it, read `src/solidity-frontend/solidity_convert.cpp` (how member access on `this` is lowered, and whether the unit body receives a `this` parameter). Cheapest empirical settle: regression fixture group 1 (§7) — its `post != pre` half must come back REFUTED on a path that writes. If the exit read is bound to the wrong object, `post == pre` holds on a writing path and **both halves of the pair come out green**, which is precisely the failure the pair is designed to expose.

---

## 3. Where the entry ASSUME goes, and how pi's own exit is found

### 3.1 The region ASSUME

Copy the certify branch's insertion — `goto_coverage.cpp:5591–5618`:

```cpp
        expr2tc bguard = and2tc(
          greaterthanequal2tc(bs, constant_int2tc(bt, string2integer(b.lo))),
          lessthanequal2tc(bs, constant_int2tc(bt, string2integer(b.hi))));
        for (const auto &h : b.holes)
        {
          bguard = and2tc(
            bguard,
            notequal2tc(bs, constant_int2tc(bt, string2integer(h))));
          ++holes_emitted;
        }

        goto_programt::instructiont asm_i;
        asm_i.type = ASSUME;
        asm_i.guard = bguard;
        asm_i.location = goto_program.instructions.begin()->location;
        asm_i.location.property("skipped");
        asm_i.function = goto_program.instructions.begin()->location.get_function();
        goto_program.instructions.insert(goto_program.instructions.begin(), asm_i);
```

Two things to carry over exactly:

- `holes_emitted` is incremented **inside the conjunction**, not from `b.holes.size()`. The comment at 5599–5605 is a measured fault-injection result: "with the conjunction disabled the query correctly flipped to FAILED while the line above still reported '1 hole(s) punched' — a counter that reads the SPEC cannot witness whether the spec reached the formula."
- Insertion order relative to the `pre_v` snapshots: insert the ASSUMEs first, then the snapshots, so `pre_v` is read under the region. Semantically irrelevant (both precede every body statement) but it keeps the trace readable and matches how a generated Foundry test reads.

Note that both land **before** the Phase-1 `tr`/`cnt` DECL+init (inserted at 3721–3757) and before the ABI value gate (3513–3587), because all three use `instructions.begin()` at different points in the pass. That is the placement the certify branch already ships, and it is correct: the region constrains inputs only.

### 3.2 Finding pi's own exit

The outer-box branch already does this — `goto_coverage.cpp:5150–5177`:

```cpp
      for (const auto &[penc, pdepth] : path_cov_outer_box_paths)
      {
        goto_programt::targett exit_pc;
        bool found = false;
        for (auto &e : to_insert)
        {
          const std::string &cm = std::get<2>(e);
          const size_t q = cm.rfind(":path:");
          if (q == std::string::npos)
            continue;
          if (strtoull(cm.substr(q + 6).c_str(), nullptr, 10) == penc)
          {
            exit_pc = std::get<0>(e);
            found = true;
            break;
          }
        }
        if (!found)
        {
          log_warning("--path-cov-outer-box: path enc={} is not among this "
                      "unit's enumerated paths; ...", penc);
          continue;
        }
```

`to_insert` is declared at `goto_coverage.cpp:3957–3959` as
`std::vector<std::tuple<goto_programt::targett, expr2tc, std::string, bool, std::string>>`
— (insertion pc, guard, claim comment, `is_revert`, stable id) — and is still in scope, fully populated, at the branch, because both stage-2 branches `continue` **before** the insertion loop at 5671.

Reuse this search verbatim. **Two changes, both mandatory (see §6):**

1. `!found` must be **fatal**, not a warning. In the outer-box mode a missing path costs one measurement; here it means zero asserts are emitted and the run prints `VERIFICATION SUCCESSFUL` with exit 0.
2. Cross-check the depth. `path_decision_depth` is filled at `goto_coverage.cpp:4012` keyed `{comment, loc->location.as_string()}`; look up the found exit's key and refuse if it disagrees with the spec's `depth`.

Placing the asserts at pi's own exit (rather than every exit, as certify does) is correct here, and the outer-box branch states the reason at 5145–5148:

> "Putting every probe at every exit would multiply the claim count by the number of exits for no information: at another exit the antecedent `tr == enc` is false and the implication is vacuous."

The certify query is the opposite case precisely because *there* the assertion is the path identity itself (h:722–729).

Placement mechanics: `insert_assert(goto_program, exit_pc, guard, comment)` — `goto_coverage.cpp:5968`, which does `insert_swap(it++, instruction); it--;`, i.e. inserts **before** `exit_pc` and leaves the iterator valid, so a whole ladder can be emitted at the same `exit_pc` in a loop, exactly as the outer-box probes are at 5342–5362.

---

## 4. The candidate ladder and its claim comments

### 4.1 The guard shape

Per candidate `C`:

```cpp
  expr2tc not_this_path = or2tc(
    notequal2tc(tr,  constant_int2tc(utype, BigInt(enc))),
    notequal2tc(cnt, constant_int2tc(utype, BigInt(depth))));
  insert_assert(goto_program, exit_pc, or2tc(not_this_path, C), comment);
```

which is byte-for-byte the outer-box construction at `goto_coverage.cpp:5225–5227` and `5359`. `tr`, `cnt` and `utype` are the same locals the enumeration writes (3598, 3608, 3625).

The eight rungs, with `P = post_v` (live member expr) and `Q = pre_v` (ghost snapshot), both of the same unsigned type `t`:

| rung | expr |
|---|---|
| `eq` | `equality2tc(P, Q)` |
| `ne` | `notequal2tc(P, Q)` |
| `ge` | `greaterthanequal2tc(P, Q)` |
| `le` | `lessthanequal2tc(P, Q)` |
| `gt` | `greaterthan2tc(P, Q)` |
| `lt` | `lessthan2tc(P, Q)` |
| `abs` | `and2tc(greaterthanequal2tc(P, k(abs_lo)), lessthanequal2tc(P, k(abs_hi)))` |
| `delta` | see below |

`k(s) = constant_int2tc(t, string2integer(s))`.

**The delta rung and unsigned wrap.** `coord_expressible` refuses signed types outright (2316), so every candidate variable is unsigned and `P - Q` **wraps** when `P < Q`. A naive `a <= P - Q <= b` would then hold on a large decrease. So the spec carries `delta_dir` and the rung is emitted as a conjunction that makes the subtraction meaningful:

- `"delta_dir": "inc"` → `and2tc(greaterthanequal2tc(P, Q), and2tc(ge(sub2tc(t,P,Q), k(delta_lo)), le(sub2tc(t,P,Q), k(delta_hi))))`
- `"delta_dir": "dec"` → the mirror, with `Q - P` and `Q >= P`.

`delta_lo`/`delta_hi` are magnitudes and must fit `t` (§6, route 4). A spec with `delta_*` but no `delta_dir` is refused at parse time rather than defaulted — defaulting to `inc` would silently certify a decreasing path's delta bound as "holds" for the wrong reason.

Only signs and bounds are ever emitted. There is **no** rung of the form `post_v == <model value>`.

### 4.2 The claim comment — the hard constraint

`goto_coverage.cpp:5635–5644`:

> "Comment shape MUST stay `<unit-id>:path:<enc>` — the unit id first, with nothing in front of it. MEASURED: a leading `certify:` made the report's `path_function` read `certify:sol:@C@Box@F@f#18`, the counterexample harvest builds the expected argument scope from that string, every nondet then failed the scope test and was filed as harness-internal (dropped 19 -> 25, `inputs` empty). The refutation still printed a verdict, so the loss was silent"

The consumer is `bmc.cpp:3106–3122`, which does `claim.claim_msg.rfind(":path:")`, takes `substr(0, p)` as the goto function id, then derives `contract_scope` (up to `@F@`) and `fn_scope` (with `#N` stripped) — and `bmc.cpp:1524–1529`, which publishes `path_function` / `path_id` from the same split.

**So the candidate id is a SUFFIX**, exactly as both existing stage-2 branches already do:

- outer box, `goto_coverage.cpp:5351–5353`:
  `id2string(f_it->first) + ":path:" + std::to_string(penc) + "#" + (upper ? "ub" : "lb") + "_" + c.name + "_" + integer2string(v)`
- certify, `goto_coverage.cpp:5645–5647`:
  `id2string(f_it->first) + ":path:" + std::to_string(certify_enc) + "#exit" + std::to_string(exit_idx++)`

New shape:

```
<unit-id>:path:<enc>#<rung>_<var>
```

e.g. `sol:@C@St@F@dep#12:path:5#ge_bal`. Uniqueness is required, not merely nice: `all_claims` is a `std::set<std::pair<...>>`, so a duplicated comment at the same location silently drops one claim — which is why the outer box de-duplicates its probe values at 5313–5316 ("a duplicated probe is a duplicated claim name, which collides in `all_claims` and silently drops one"). `(rung, var)` is unique by construction; assert it anyway when inserting into `path_cov_assert_candidates`.

Side effect to state rather than discover: `claim_entry["path_id"]` in the JSON becomes the string `"5#ge_bal"`. That already happens for outer-box probes and certify exits, and the one place that parses it numerically — `bmc.cpp:1556–1557`, `strtoull(claim_msg.substr(pos + 6).c_str(), nullptr, 10)` — stops at `#` and still yields 5.

### 4.3 Registering the candidate

For every emitted assert, in the same order as the outer box at 5355–5357:

```cpp
  const std::string loc = exit_pc->location.as_string();
  all_claims.insert({comment, loc});
  path_cov_assert_candidates.push_back({enc, var, rung, text, {comment, loc}});
  insert_assert(goto_program, exit_pc, or2tc(not_this_path, C), comment);
```

`all_claims.insert` **first** — that is the ordering every other branch uses, and it is what makes the claim visible to `audit_entry_liveness` (`goto_coverage.cpp:1104`) even if the solve never reaches it.

One more small edit: the decision-sequence recorder gate at `goto_coverage.cpp:3806–3812` is

```cpp
    const bool trace_decisions =
      outer_on && (f_it->first.as_string() == outer_unit ||
                   f_it->first.as_string().find("@F@" + outer_unit + "#") !=
                     std::string::npos);
```

Extend it to the assert unit so the per-path decision sequence can be printed for the unit under assertion too. This is a diagnostic, not a soundness item.

---

## 5. Reading the verdicts back, and what the report says

### 5.1 No bmc.cpp change is needed to capture verdicts

`is_path_cov` in `multi_property_check` is keyed off `solidity-path-coverage-enabled` (`bmc.cpp:2669`), which the dispatch already sets (`esbmc_parseoptions.cpp:4145`). The verdict ledger at `bmc.cpp:2904–2921` therefore records every new claim automatically:

```cpp
    if (is_path_cov)
    {
      char verdict;
      if (solver_result == smt_convt::P_SATISFIABLE)  verdict = is ? 'U' : 'F';
      else if (solver_result == smt_convt::P_UNSATISFIABLE) verdict = 'P';
      else verdict = 'U';
      std::lock_guard lock(goto_coveraget::claim_outcome_mutex);
      ...
```

keyed on `claim_sig == claim_msg + "\t" + claim_loc`.

### 5.2 The new reporter

New static `goto_coveraget::report_path_cov_assertions()`, modelled on `report_outer_boxes()` (`goto_coverage.cpp:605–1102`), specifically its verdict-reading loop at 652–701:

```cpp
  size_t decided = 0, undecided = 0;
  {
    std::lock_guard lock(claim_outcome_mutex);
    for (const auto &p : path_cov_outer_box_probes)
    {
      auto it = claim_outcome.find(p.key.first + "\t" + p.key.second);
      if (it == claim_outcome.end()) { ++undecided; continue; }
      ...
```

Mapping, with the third state **explicit**:

| `claim_outcome` | verdict | meaning printed |
|---|---|---|
| `'P'` | **HOLDS** | the candidate is true for every input in the region **at this exploration**. Never "proven": `path_cov_can_prove_unreachable()` (`bmc.cpp:722`) returns `false` and h:206–217 names this state `bounded-holds`. |
| `'F'` | **REFUTED** | there is an input in the region walking pi whose post-state violates the candidate. The counterexample is that input. |
| `'U'` | **NO VERDICT** (solver unknown) | |
| *absent* | **NO VERDICT** (never reached the solver) | |

Both no-verdict routes are one reported state with two named causes, mirroring the `not-solved-this-run` / `solver-unknown` split at h:210–216. Print **all four counts every time, zeros included** — the rule at `bmc.cpp:1265–1276` ("a category that stops occurring is noticed, a category that silently disappears from the output is not").

Report contents, per variable, in spec order:

```
--path-cov-assert: unit '<uid>' path enc=E depth=D over N region coordinate(s)
--path-cov-assert: <var>: post == pre  HOLDS
--path-cov-assert: <var>: post != pre  REFUTED
--path-cov-assert: <var>: post >= pre  HOLDS
--path-cov-assert: <var>: post >  pre  NO VERDICT (solver unknown)
--path-cov-assert: <var>: 0 <= post <= 100  HOLDS
--path-cov-assert: ladder summary — 8 candidate(s): 4 HOLDS, 3 REFUTED, 1 no verdict
--path-cov-assert: 2 state variable(s) were REFUSED and carry NO candidate: balances (…), owner (…)
```

The refused block is printed **before** any verdict, copying `report_outer_boxes` at 703–720 and its stated reason: "a refused coordinate emits no probe, so it appears in no box below — and an absence reads as 'not asked about', while the truth is 'asked about and refused'."

And one banner that must not be omitted, by analogy with the outer-box banner at 733–738:

> the run's `VERIFICATION SUCCESSFUL` / `FAILED` line is **not** the result of this mode. A refuted candidate is the ladder working. The result is the per-candidate table.

### 5.3 Wiring the call

`bmc.cpp:1150–1168`, inside `else if (is_path_cov)`, currently:

```cpp
    if (goto_coveraget::path_cov_outer_box_mode)
    {
      goto_coveraget::report_outer_boxes();
    }
    else if (goto_coveraget::path_cov_certify_mode)
    {
      goto_coveraget::audit_certify_witness(
        options.get_bool_option("cov-report-json"));
      log_status("--path-cov-certify: no [Coverage] block is printed …");
    }
    else
    {
      // … the normal coverage block …
```

Add a third arm `else if (goto_coveraget::path_cov_assert_mode) { goto_coveraget::report_path_cov_assertions(); }`. Being inside this `if/else` chain is what suppresses the `[Coverage]` block, and the reason at 1136–1149 applies verbatim: the claims here HOLD in the success case, so the coverage counters would print `Path Coverage: 0%` for a completely successful run.

**Keep `audit_entry_liveness` (`bmc.cpp:1134`), which runs before the chain.** It is the precondition that stops a never-entered unit from making every candidate hold vacuously — exactly the argument h:280–284 makes for keeping `I` disabled. It works unchanged here: it splits the unit out of the comment with `rfind(":path:")` (`goto_coverage.cpp:1136–1139`), which the suffixed comment still satisfies.

**Do not reuse `audit_certify_witness` (h:783–806).** Its rule is "a refutation without a witness is a hard failure", and its premise is that a refutation is a defect-shaped event whose witness shrinks the box. Here a refutation is an expected, normal outcome and there is no box to shrink. Report per candidate whether `path_ce[claim_sig].inputs` was non-empty; never abort. (This asymmetry is the same kind that made `audit_certify_witness` add its own `ce_payload_requested` narrowing at h:800–805 after it produced a false positive on its first real run.)

---

## 6. Failure modes that must be refused BEFORE the query is emitted

The certify branch documents five false-certificate routes. Their applicability:

| # | route | anchor | applies to `--path-cov-assert`? |
|---|---|---|---|
| 1 | **empty box** (`lo > hi`) | 5409–5442 | **Unchanged.** The region is the assumption; an unsatisfiable assumption means nothing executes and **every** candidate holds. Worse than in certify: it certifies a whole ladder at once. |
| 2 | **coordinate bounded twice** | 5444–5448 | **Unchanged.** "two bounds on one name can intersect to an empty box while each is individually well-formed, which the emptiness test above would not see" |
| 3 | **punched-empty box** | 5449–5476 | **Unchanged.** `[5,5] \ {5}` passes `lo <= hi` and admits no input. |
| 4 | **out-of-type bound** | 5542–5586 | **Unchanged, plus an extension.** Every region `lo`/`hi`/`hole` must fit the coordinate's type. **Additionally**, `abs_lo`/`abs_hi`/`delta_lo`/`delta_hi` must fit the *variable's* type, for the identical wrap reason — a ladder constant above `2^w-1` wraps and the run answers about a bound nobody wrote. |
| 5 | **unit matched nothing** | 5798–5825 | **Unchanged.** Mirror `certify_units_matched`: a counter incremented at 5652, checked after the unit loop at 5814, `exit(1)`. |

Also unchanged from certify: an **unexpressible REGION coordinate refuses the whole query** (`goto_coverage.cpp:5506–5538`, `exit(1)`), because "dropping the bound would certify a WIDER box than the one asked for". The asymmetry at h:894–902 is preserved in the other direction: an unexpressible **candidate variable** refuses only that variable and is recorded (§2.3).

### New routes, specific to assertions

There are **five**, and they are five *entry conditions*, not five symptoms — closing four of them leaves a run that looks identical to a correct one.

**N1 — the ladder is empty.** Two distinct entry conditions with one symptom: (a) `vars` is present but names nothing / names only variables the spec excludes; (b) every eligible variable was refused by §2.3. Either way zero asserts are emitted, nothing is checked, and the run prints `VERIFICATION SUCCESSFUL` with exit 0 — the same shape as route 5. **Both** must be closed, with a distinct message each, and both must `exit(1)`. Closing only one produces an outcome indistinguishable from a fix.

**N2 — the path's `enc` does not exist for this unit.** The outer-box `!found` arm merely warns (5169–5176) because there it costs one measurement. Here it means the ladder was emitted nowhere. **Fatal.**

**N3 — `depth` disagrees with the enumerated depth.** This is the most dangerous of the five because it produces *no diagnostic at all today*. The antecedent is `tr != enc || cnt != depth`; a wrong `depth` makes it TRUE on every execution, so **every candidate holds vacuously** and the report reads as a fully successful certification of the whole ladder. The check is one lookup: `path_decision_depth` at `goto_coverage.cpp:4012`, keyed `{comment, loc}` from the exit found in §3.2. Refuse with `exit(1)`.
*(The same gate would be worth adding to the outer-box branch, which has the same exposure for the same reason. I am not claiming it is broken there — the outer-box driver derives `depth` from the report — only that the gate is cheap and the failure is silent.)*

**N4 — the unit is a NAMED OBSTACLE.** Both flags are computed before the branch and are in scope: `unit_has_lost_decision` (`goto_coverage.cpp:3917`) and `unit_calls_gated_unit` (3468). h:384–396 states the rule:

> "a marked path must be excluded from the sibling set used for the stage-3 subtraction AND must not be turned into a test. Marking without excluding would be worthless."

A *certified post-state assertion* is precisely the artefact that becomes a test's `assertEq`. On an obstructed unit the model admits executions the chain does not have, so a HOLDS verdict there authorises an assertion that can be red on the unmodified contract. **Refuse the query**, naming the obstacle. Note this is the same lesson as `goto_coverage.cpp:5711–5722`, where `normal_exit_paths` and `named_obstacle_paths` contradicted each other and the authorisation won.

**N5 — pi's exit is a revert or is undetermined.** *And there is a trap here.* The sets `revert_paths` / `rollback_revert_paths` / `undetermined_exit_paths` are filled in the insertion loop at `goto_coverage.cpp:5686–5693`, which both stage-2 branches `continue` past — so in the new mode **they are empty**. The classification must be read from the locals that *are* computed before the branch:
- `is_revert` = `std::get<3>(e)` of the matched `to_insert` entry (custom-error `#sol_error` exit),
- `rollback_exits` (declared 3970),
- `undetermined_exits` (declared 3973),
indexed by the position of the matched entry in `to_insert`.

Then:
- **custom-error revert → refuse.** h:518–526: the frontend lowers it to `#sol_error` with *no* rollback, so the state at that instruction is the state **at the revert point**, not the EVM post-state (on-chain every write is undone). A post-state assertion there is about a state that does not exist.
- **undetermined exit → refuse.** `goto_coverage.cpp:4703`: "an undetermined exit cannot become an oracle".
- **rollback revert → allow, labelled.** Here the rollback *is* modelled (h:352–358), so `post_v` is the correctly restored state, and `post == pre` holding is a true and useful statement. The report must say the exit reverts so nobody reads the assertion as describing a successful transaction.

---

## 7. Regression plan

All fixtures under `regression/esbmc-solidity/`, following the existing layout (`contract.sol`, a `.json` spec, `test.desc` = `CORE` / source / args / regex lines). Two disciplines carried over from the existing suite:

- **The harness matches program OUTPUT only** (`bmc.cpp:1198–1201`), so every property must be pinned on a stdout line, never on `cov-report.json`.
- **The pair discipline**, stated at `solidity_path_cov_certify_box_inside/contract.sol:18–34`: "PASSING THIS TEST ON ITS OWN IS NOT EVIDENCE OF ANYTHING… The test is the PAIR, and the property being tested is that the two verdicts are consistently OPPOSITE." A detector that carries its own must-flip control needs no injection; a one-directional detector does.
- Specs are **read-only**; the tool must never write them back (same rule as `certify_box_inside/contract.sol:36–38`).

### Fixtures

**1. `solidity_path_cov_assert_r1_pair_{unchanged,written}`** — the core must-flip pair. One contract, one scalar `uint256 bal`, two paths; on one path `bal` is untouched, on the other it is written. Two specs differing only in `enc`.
*unchanged*: `eq` HOLDS, `ne` REFUTED. *written*: `eq` REFUTED, `ne` HOLDS.
**Fails when the mechanism stops firing:** if no assert is emitted, N1 fires and both halves exit non-zero. If the antecedent is broken open (e.g. the wrong `enc`), every candidate HOLDS and the pair goes all-green — which is also the exact signature of the §2.4 UNVERIFIED risk (`post_v` bound to the wrong object), so this pair settles that question too.

**2. `solidity_path_cov_assert_sign_ladder`** — a path that strictly increments. `ge`, `gt` HOLD; `le`, `lt` REFUTED; `eq` REFUTED, `ne` HOLDS. Six candidates, **one run**.
**Pins:** the batch property itself. Assert `ladder summary — 6 candidate(s)` on stdout; if the branch degenerates to one claim per run the count line changes.

**3. `solidity_path_cov_assert_delta_{fits,tight,wrapped}`** — a path with a known `+7`.
*fits*: `delta_lo=1, delta_hi=10, dir=inc` HOLDS. *tight*: `delta_hi=6` REFUTED. *wrapped*: same spec pointed at the **decrementing** path — must be REFUTED, not HOLDS.
**Pins:** the unsigned-wrap guard of §4.1. Drop the `P >= Q` conjunct and *wrapped* flips green.

**4. `solidity_path_cov_assert_refuses_mapping`** — a contract with `mapping(address=>uint) balances;` and one scalar `uint total`. Expect: exactly one candidate variable (`total`), plus a `REFUSED … balances` line naming it.
**Pins:** §2.3's second scan. Delete it and the mapping vanishes from the output entirely — which is why the test asserts the *presence* of the refusal line, not just the candidate count.

**5. `solidity_path_cov_assert_zero_candidates_refused`** — a contract whose only state is a mapping, so every variable is refused. Must exit non-zero with a named reason, and must **not** print a success line. Pin the absence with the negative-lookahead idiom already used at `solidity_path_cov_entry_storage_pinned/test.desc:9`:
`^(?!(.|\n)*VERIFICATION SUCCESSFUL)`
Twin `..._empty_vars_refused` with an explicitly empty `vars` array, to close N1's *second* entry condition separately.

**6. `solidity_path_cov_assert_depth_mismatch_refused`** — correct `enc`, `depth+1`. Must refuse.
**Fault injection required** (one-directional detector): remove the N3 lookup and the run prints an all-HOLDS ladder with exit 0. Twin `..._enc_absent_refused` for N2.

**7. `solidity_path_cov_assert_shared_refusals_{empty_box,duplicate_bound,hole_empty}`** — reuse the contracts and specs of `solidity_path_cov_certify_empty_box`, `..._certify_duplicate_bound`, `..._certify_hole_empty_refused` verbatim, with `region` in place of `box` and `--path-cov-assert` in place of `--path-cov-certify`.
**Pins:** that §1.2's extraction actually happened. The moment the parser is copied instead of shared, one of these three goes green while the certify twin stays red.

**8. `solidity_path_cov_assert_obstacle_refused`** — reuse `solidity_path_cov_library_require_obstacle`'s contract (a `require` in an internal library, lowered to a branch-free assume). Must refuse, naming the obstacle. Twin using `solidity_path_cov_residual_unit_call_obstacle` for the second obstacle route, since h:396–401 and `goto_coverage.cpp:5844–5869` count them apart — one fixture would let the other route regress silently.

**9. `solidity_path_cov_assert_revert_exit_{custom_error_refused,rollback_labelled}`** — reuse `solidity_path_cov_custom_error_revert` and `solidity_path_cov_require_rollback`. The first must refuse; the second must emit and carry the reverting-exit label.
**Pins:** N5, including the trap that the three exit-kind sets are empty in this branch. If the implementation reads `goto_coveraget::revert_paths` instead of the locals, *both* halves emit and the first goes green.

**10. `solidity_path_cov_assert_witness_scope`** — one REFUTED candidate on a unit with a real `uint256` parameter, run with `--cov-report-json`. Assert on stdout that the refutation reports a non-empty witness.
**Pins:** the §4.2 comment-prefix rule. Prefix the comment with anything and, per the measured result at `goto_coverage.cpp:5635–5644`, the harvest's scope test rejects every nondet, `inputs` empties, and the verdict still prints correctly — a silent loss this fixture makes loud.

### Global fault-injection matrix

| injection | fixtures that must go red |
|---|---|
| delete the `insert_assert` in the new branch | 1, 2, 3 (via N1's zero-candidate gate) |
| force the antecedent true (`enc+1`) | 1, 2, 3 — every REFUTED half flips |
| skip the region ASSUME insertion | 3 (*tight*), and 1 if its paths are region-separated |
| return early from the structural refusal block | 5, 6, 7 |
| read `goto_coveraget::revert_paths` instead of the branch-local sets | 9 |
| prefix the claim comment | 10 |
| duplicate the spec parser instead of sharing it | 7 (on the next fix to either copy) |

---

## 8. Edit list, condensed

| file | anchor | new / edit |
|---|---|---|
| `src/esbmc/options.cpp` | `"Coverage options"` group, after `path-cov-outer-box` (909–922) | **new** option entry |
| `src/esbmc/esbmc_parseoptions.cpp` | `if (cmdline.isset("solidity-path-coverage"))` @4118; the stage-2 reads @4200–4203 | **edit** — read `path-cov-assert`; **new** — reject any two of the three stage-2 flags together (closes the existing silent-precedence hazard at `goto_coverage.cpp:5000` vs `5387`) |
| `src/goto-programs/goto_coverage.h` | beside `path_cov_certify_path` (740) / `path_cov_outer_box_path` (742); beside `report_outer_boxes()` (907) | **new** — `path_cov_assert_path`, `assert_candidatet`, `path_cov_assert_mode`, `path_cov_assert_candidates`, `report_path_cov_assertions()` |
| `src/goto-programs/goto_coverage.cpp` | static defs block 65–88 | **edit** — definitions for the new statics |
| " | `certify_boundt` @2535; bound loop @2697–2706 | **edit** — hoist to file scope as `path_cov_boundt`, extract `parse_bounds(json, key, out)` next to `walk_fields` (550) |
| " | after the certify spec block (2760) | **new** — `--path-cov-assert` spec parse + banner |
| " | `resolve_coord` @3324–3397 | **edit** — factor out the contract-object lookup (3345–3356) as a shared lambda |
| " | decision-recorder gate @3806–3812 | **edit** — include the assert unit |
| " | after the certify branch's `continue` (5668) | **new** — the whole stage-3 branch: structural refusals → coordinate resolution → region ASSUME → `pre_v` snapshots → exit lookup + depth check → ladder emission → `continue` |
| " | after the unit loop, beside the `certify_units_matched == 0` check (5814–5825) | **new** — `assert_units_matched == 0` gate (route 5) and the N1 zero-candidate gate |
| " | after `report_outer_boxes()` (1102) | **new** — `report_path_cov_assertions()` |
| `src/esbmc/bmc.cpp` | mode chain @1150–1168 inside `else if (is_path_cov)` | **edit** — third arm calling the new reporter |
| `regression/esbmc-solidity/` | — | **new** — 10 fixture groups per §7 |

No change is needed to `multi_property_check` (`bmc.cpp:2617`), to the verdict ledger (2904–2921), or to the CE harvest (3073–3455): all three key off `solidity-path-coverage-enabled`, which the dispatch already sets, and off the `:path:` comment shape, which §4.2 preserves.

---

## 9. Open points (UNVERIFIED)

1. **Does `post_v` read the unit's writes?** Discussed at length in §2.4. Settle by reading `src/solidity-frontend/solidity_convert.cpp` (lowering of `this->x` and whether the unit body carries a `this` parameter distinct from `sol:@_ESBMC_Object_<C>`). Empirically settled by fixture group 1.
2. **Struct-typed state variables.** `walk_fields` (`goto_coverage.cpp:550`) reaches `state.cfg.limit`, so a struct's *scalar fields* are expressible even though the struct itself is refused by `coord_expressible`. Whether the ladder should recurse one level into struct components is a scope decision I have deliberately left out of this plan; recursing is cheap (`walk_fields` already exists) but changes the candidate count, which is a reported number. Decide before fixture 4 is written.
3. **Where the assert lands for a RETURN exit.** `emit_exit` records the RETURN instruction itself (`goto_coverage.cpp:4364–4436`), and `insert_assert` inserts *before* it, so `post_v` is read before the frame exits. That is correct for state variables. I have **not** checked whether the function epilogue (`_saved_encl_addr` restore, recognised at 4069–4083) performs any state write that a post-state assertion ought to see; it restores enclosing-contract context rather than user state, so it should not, but confirming means reading the epilogue's emission in `src/solidity-frontend/solidity_convert.cpp`.
4. **`--parallel-solving`.** `claim_outcome` is mutex-protected (h:343) and the new reporter reads it under the same lock, so the design is thread-safe. I have not checked whether ladder claims are *ordered* deterministically across threads; the report should sort by `(var, rung)` in spec order rather than by completion, or the pinned stdout lines will be flaky under `--parallel-solving`.

---

# VERDICT ON THIS PLAN'S UNVERIFIED PREMISES (independent reader, source only)

## §9 item 1 — does an exit read of `state.<field>` observe the unit's writes? **CONFIRMED.**

The chain, each link with its own evidence:

1. The frontend really does route state writes through a `this` POINTER
   parameter, exactly as this plan feared:
   `solidity_convert_decl.cpp:261` gives every public/external function a
   parameter `<func_id>#this` of pointer type, and
   `solidity_convert_ref.cpp:171` lowers a state-variable reference to
   `member_exprt(this_ptr, name, type)`. So at goto level the lhs of `x = v`
   is `member(<this pointer>, x)`, NOT the object symbol.
2. symex removes that distinction before the assignment is recorded.
   `symex_assign.cpp:337-345` dereferences `lhs` while keeping `original_lhs`
   in its pre-dereference shape; `symex_assign_member`
   (`symex_assign.cpp:686-741`) rewrites `a.c = e` into `a = with(a, c, e)` and
   recurses to `symex_assign_symbol`; and `slice.cpp:90` asserts
   `is_symbol2t(SSA_step.lhs)` — every SSA assignment's lhs is a bare symbol.
3. That base symbol is `sol:@_ESBMC_Object_<C>`. The repository already asserts
   this, and the assertion is measured rather than argued
   (`goto_coverage.cpp:2769-2782`: "a Solidity `this->x = v` is an update of
   THIS symbol, so without it every final_state is empty"), and `no_slice`
   compares against `SSA_step.lhs`'s own name (`slice.cpp:4-8`).
4. `bmc.cpp:3325-3374` reads the write side back out by testing
   `from_expr(...).find("this->")`, using `original_lhs`
   (`build_goto_trace.cpp:82`) — the pre-dereference shape. Same assignment,
   two shapes, both accounted for.

So the in-code assertion at `goto_coverage.cpp:2769-2782` is evidence about
neither the entry read nor the exit read — it is evidence about the WRITE, and
that is precisely the missing link. The entry read never needed it; the exit
read did.

**Useful corollary:** the assertion does not need `--cov-report-json`. Slicing
only removes a state write when no assertion mentions the symbol, and this
mode's assertion contains `member(_ESBMC_Object_C, field)` in its own text, so
`slice.cpp:51-55` adds it to `depends` and the writes are kept.
`protect_ce_symbols` exists to keep the CE payload alive to the trace, which is
a different job.

## §9 item 3 — does the RETURN epilogue write user state? **CONFIRMED that it does not.**

The wrapper is emitted at `solidity_convert_modifier.cpp:486-676`; its exit
side is two assignments only, `:542-544` and `:546-548`, restoring
`c:@_ESBMC_enclosing_contract_address` and `c:@_ESBMC_enclosing_contract_this`.
Both are `c:@`-prefixed C-level globals — neither a component of
`sol:@_ESBMC_Object_<C>` nor a `sol:@C@<C>@<name>` contract-scope store.
Moreover, for a RETURN exit the epilogue does not execute at all: it is emitted
AFTER the body, so a `return` jumps over it (`goto_coverage.cpp:4165-4167`).

## FIVE CONDITIONS THIS PLAN MUST HANDLE — the verdicts above are conditional

**(A) `resolve_coord` picks the object by SUBSTRING match, and it can pick the
wrong one silently.** `goto_coverage.cpp:3346-3356` takes the FIRST symbol whose
id starts with `sol:@_ESBMC_Object_` and, when `scope_contract` is non-empty,
contains it. Two failure modes: with `scope_contract` empty (no `--contract`, or
`--coverage-whole-unit`, per `notes/path-coverage-invocation-contract.md` §4)
any contract's object may be chosen; and `--contract Escrow` matches
`sol:@_ESBMC_Object_EscrowSrc#`, which is exactly the shape of the
EscrowSrc/EscrowDst benchmarks. If the chosen object is not the one the
instrumented unit writes, the exit read sees an object nobody touched:
`post == pre` holds vacuously, `post != pre` is refuted, and **nothing reports
an error**. `--path-cov-assert` must resolve the object by EXACT match against
`contract_of(f_it->first)` (`goto_coverage.cpp:6601-6615`), not by reusing the
substring test.

**(B) `new`-created instances.** `should_treat_as_new`
(`solidity_convert.h:1610-1619`) returns false for a single `--contract` target
without `--bound`, making the static singleton the only instance and the verdict
unconditional. Outside that case the same unit body can run with `this` pointing
at a heap instance while the exit read still reads the singleton. The path
identity `tr == enc && cnt == depth` does not constrain which instance ran, so
this must be refused or declared, not assumed away.

**(C) external vs internal, and inheritance: no effect.** Unit-hood is decided
by the presence of `<id>#_sol_save_this` (`goto_coverage.cpp:2823-2825`), which
only public/external functions have; an inherited function's goto id still
carries the DERIVED contract in its `@C@` segment; and an internal call is
physically expanded into its caller, keeping the caller's `this`.

**(D) On a rollback-revert path the exit read sees the ROLLED-BACK state.**
`*this = _sol_save_this` is one aggregate write to the same object
(`bmc.cpp:3342-3347`), performed at the revert point, before the assertion.
That is semantically right, but it makes `post == pre` hold on every such path,
and the report must say "the rollback held" rather than "the function did not
change state".

**(E) The object struct carries non-user components.** `$address`, `$balance`,
`$code`, `$codehash`, `$mutex_<C>`, `_ESBMC_bind_cname`
(`solidity_convert_contract.cpp:467-479`) and `$dynamic_pool` are components of
the same struct. Enumerating "scalar state variables" must apply the same filter
`bmc.cpp:3357` uses (`$*`, `_ESBMC*`, `anon_pad*`) or the ladder emits
candidates about `$balance`. Mappings and dynamic arrays are not components at
all — they are `sol:@C@<C>@<name>` contract-scope globals — which is the second
scan this plan already specifies.

**(F) `coord_expressible` is the wrong gate for R1.** It refuses `bool` because
a two-point domain has no interval — correct for R2's interval and delta rungs,
wrong for R1, where `post == pre` / `post != pre` is perfectly expressible on a
bool. Reusing it as written silently drops every boolean state variable.

## Still UNVERIFIED after this pass

- The dispatcher's `this` argument for each public function is inferred from two
  sibling construction sites (`solidity_convert_call.cpp:1470-1472` and
  `:2414-2419`) plus `solidity_convert_contract.cpp:151-153`, not read directly;
  `get_unbound_function` was not located. Settle in the remainder of
  `solidity_convert_call.cpp`.
- Whether `dereference()` on the `this` parameter can produce `$failed_object`
  or a guarded ITE across several objects. Settle in
  `src/goto-symex/symex_dereference.cpp` and `src/pointer-analysis/dereference.cpp`.
  The measured assertion in (3) above already implies a single resolution.
