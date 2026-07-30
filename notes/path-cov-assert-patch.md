I have read everything. Here is the deliverable.

---

# A. Stale anchors in `notes/path-cov-assert-plan.md`

I re-checked **every** `file:line` the plan cites. The tree has barely moved: almost all are exact. The ones that are wrong or need care:

| plan says | reality | corrected anchor |
|---|---|---|
| §7 fixture 5: "the negative-lookahead idiom already used at `solidity_path_cov_entry_storage_pinned/test.desc:9`: `^(?!(.\|\n)*VERIFICATION SUCCESSFUL)`" | that file has **8** lines; line 8 is `^(?!(.\|\n)*could not be separated)`. The idiom quoted does not exist anywhere. | The real verdict-suppression idiom is **`solidity_path_cov_certify_hole_empty_refused/test.desc:5-6`**: `\A(?!(.\|\n)*^VERIFICATION SUCCESSFUL$)` and `\A(?!(.\|\n)*^VERIFICATION FAILED$)` (two separate lines, `\A`-anchored, `$`-terminated). The `^(?!...)` form the plan quotes is weaker and would be satisfied by any line that is not the first. |
| §1.1 / §1.2: decimal-STRINGS comment at `goto_coverage.cpp:2524–2534` | comment runs **2521–2534**; the quoted sentence is at **2526–2529** | `goto_coverage.cpp:2526–2534` |
| §0: certify body "5387–5668" | `if (certify_on)` at 5387, `continue;` at **5668**, closing `}` at **5669** | insert the new branch **after line 5669**, before `size_t ins_idx = 0;` at 5671 |
| §4.3: decision-recorder gate "3806–3812" | `trace_decisions` is **3806–3809**; `record_decisions` is **3812** | edit target is **3806–3809** |
| h:502–506 `state_written_unrendered` | **h:501–506** | cosmetic |
| §5.2 "the outer-box banner at 733–738" | 733–738 is the *"{} of {} ladder probe(s) reached the solver"* line. The "these are candidates, not certificates" closing banner is at **1097–1101** | use 1097–1101 as the model for the closing banner |
| Verdict-appendix "still unverified": `solidity_convert_contract.cpp:151-153` | 151–153 is the `// The symbol's value is what clang_c_maint...` comment about the singleton, not the dispatcher's `this` argument | the plan's own "unverified" label stands; the citation is simply wrong |

**One anchor the plan does not give, which the patch needs and which I confirmed:** the contract-object symbol id is built at `solidity_convert_contract.cpp:44–51`

```cpp
void solidity_convertert::get_static_contract_instance_name(
  const std::string c_name, std::string &name, std::string &id)
{
  name = "_ESBMC_Object_" + c_name;
  id = "sol:@" + name + "#";
}
```

so the id is **exactly** `sol:@_ESBMC_Object_<C>#` — trailing `#`, nothing after. Condition (A) is therefore closable by plain string equality, not by a prefix walk.

Everything else the plan cites (4994, 5000, 5150–5177, 5225–5227, 5313–5316, 5351–5353, 5355–5357, 5359, 5376, 5409–5442, 5444–5448, 5449–5476, 5506–5538, 5542–5586, 5591–5618, 5635–5647, 5686–5693, 5798–5825, 2285–2345, 2535, 2697–2706, 2760, 2794–2796, 3324–3397, 3468, 3536–3538, 3917, 3957–3959, 3970, 3973, 4012, 4703, 5070–5096, 6601–6615; h:740/742/876–902/907; options.cpp 909–922/923; parseoptions 4118/4145/4200–4203; bmc.cpp 722*/1134/1136–1149/1150–1168/2669/2904–2921/3286–3288/3357/3372) matches the current tree. (*722 not read — see UNVERIFIED.)

---

# B. The patch

## B.1 `src/esbmc/options.cpp`

**MATCH** (unique — end of the `path-cov-outer-box` entry into the start of `solidity-path-coverage`):

```cpp
      "counterexample, used to reject a subtraction cut that would carve away a "
      "known member of the path's domain. Resolution is (hi-lo)/(probes+1): a "
      "non-adaptive batch cannot give logarithmic precision, so refine with a "
      "second batch on a narrower span"},
     {"solidity-path-coverage",
```

**REPLACE WITH:**

```cpp
      "counterexample, used to reject a subtraction cut that would carve away a "
      "known member of the path's domain. Resolution is (hi-lo)/(probes+1): a "
      "non-adaptive batch cannot give logarithmic precision, so refine with a "
      "second batch on a narrower span"},
     {"path-cov-assert",
      boost::program_options::value<std::string>()->value_name("file"),
      "Synthesise and CERTIFY post-state assertions for ONE enumerated path "
      "over an input REGION. JSON: {unit, enc, depth, "
      "region:[{name,lo,hi,holes}], "
      "vars:[{name,abs_lo,abs_hi,delta_dir,delta_lo,delta_hi}]}. The region is "
      "ASSUMED at entry (exactly the require/bound a generated Foundry test "
      "would carry) and each candidate is asserted at THAT path's own exit "
      "under the path-identity antecedent `tr != enc || cnt != depth`, so it is "
      "vacuous on every other path. The assumption is fixed and only the "
      "assertions vary, so the whole ladder is judged in ONE run. A REFUTED "
      "candidate is the ladder working, not a failure; the run's verdict line "
      "is therefore NOT the result — the per-candidate HOLDS / REFUTED / "
      "no-verdict table is. Mutually exclusive with --path-cov-certify and "
      "--path-cov-outer-box"},
     {"solidity-path-coverage",
```

## B.2 `src/esbmc/esbmc_parseoptions.cpp`

**MATCH** (unique — the two existing stage-2 reads at 4200–4203 plus the following line):

```cpp
      if (cmdline.isset("path-cov-certify"))
        tmp.path_cov_certify_path = cmdline.getval("path-cov-certify");
      if (cmdline.isset("path-cov-outer-box"))
        tmp.path_cov_outer_box_path = cmdline.getval("path-cov-outer-box");
      tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");
```

**REPLACE WITH:**

```cpp
      if (cmdline.isset("path-cov-certify"))
        tmp.path_cov_certify_path = cmdline.getval("path-cov-certify");
      if (cmdline.isset("path-cov-outer-box"))
        tmp.path_cov_outer_box_path = cmdline.getval("path-cov-outer-box");
      // Stage-3 post-state assertion synthesis. Read beside the other two so
      // the pass keeps having no command-line dependency of its own.
      if (cmdline.isset("path-cov-assert"))
        tmp.path_cov_assert_path = cmdline.getval("path-cov-assert");

      // ---- THE THREE STAGE-2/3 MODES ARE MUTUALLY EXCLUSIVE ----
      //
      // They are branches at the end of solidity_path_coverage() and each one
      // `continue`s out of the per-unit loop, so the FIRST one tested wins and
      // the others never fire. That precedence is silent and it already has a
      // measured consequence: `--path-cov-outer-box` together with
      // `--path-cov-certify` runs the outer-box branch, certify emits not one
      // assume and not one assert, `certify_units_matched` stays 0, and the run
      // then dies at the route-5 gate with a message blaming the UNIT NAME —
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
        if (stage2.size() > 1)
        {
          std::string names;
          for (const auto &n : stage2)
            names += (names.empty() ? "" : ", ") + n;
          log_error(
            "--solidity-path-coverage: {} were given together ({}). These are "
            "three mutually exclusive stage-2/3 modes implemented as three "
            "branches at the end of one pass, and each one leaves the unit loop "
            "as soon as it fires — so passing two does not run two, it runs the "
            "first and silently discards the rest. Historically that discarded "
            "run then failed with a message about the unit NAME, which is not "
            "what was wrong. Pass exactly one.",
            stage2.size(),
            names);
          return true;
        }
      }
      tmp.cov_assume_asserts = cmdline.isset("cov-assume-asserts");
```

## B.3 `src/goto-programs/goto_coverage.h`

### B.3.1 the instance member

**MATCH** (unique — h:741–742 plus the following blank line and comment):

```cpp
  // Spec for the outer-box batch (see report_outer_boxes). Empty => disabled.
  std::string path_cov_outer_box_path = "";

  // Set by solidity_path_coverage() when a certification query was emitted, so
```

**REPLACE WITH:**

```cpp
  // Spec for the outer-box batch (see report_outer_boxes). Empty => disabled.
  std::string path_cov_outer_box_path = "";

  // ---- STAGE 3: POST-STATE ASSERTION SYNTHESIS (--path-cov-assert <json>) ----
  //
  // The certification query says WHICH inputs walk a path. It says nothing
  // about what that path DOES, and a generated test needs both: the region
  // becomes the test's `require`, and the post-state assertion becomes its
  // `assertEq`. This mode synthesises the second half and certifies it under
  // the first: assume the region at unit entry, then assert each candidate at
  // THAT path's own exit under the path-identity antecedent
  // `tr != enc || cnt != depth`, so every candidate is vacuous on every other
  // path and the whole ladder is judged in one run.
  //
  // Only SIGNS and BOUNDS are ever emitted (post == pre, post != pre,
  // post >= pre, ..., an interval on post, a bounded delta). There is
  // deliberately no `post == <model value>` rung: a model value is a fact about
  // one counterexample, and asserting it would produce a test that is red on
  // any input the region admits but the solver did not pick.
  //
  // Empty => disabled, and the pass behaves exactly as before.
  std::string path_cov_assert_path = "";

  // Set by solidity_path_coverage() when a certification query was emitted, so
```

### B.3.2 the candidate record, the statics, the reporter

**MATCH** (unique — h:905–907):

```cpp
  // Read the probe verdicts, print each path's outer box, then subtract the
  // siblings' boxes and print the certified region. Called after solving.
  static void report_outer_boxes();
```

**REPLACE WITH:**

```cpp
  // Read the probe verdicts, print each path's outer box, then subtract the
  // siblings' boxes and print the certified region. Called after solving.
  static void report_outer_boxes();

  // ---- STAGE 3: the candidate ladder and how its verdicts are read back ----
  //
  // One record per emitted assertion. `key` is the (comment, location) pair
  // that multi_property_check files the verdict under, so the reporter reads
  // exactly the claims this mode created and nothing else.
  //
  // `rung` and `var` together are unique BY CONSTRUCTION, and that is load
  // bearing rather than tidy: `all_claims` is a std::set of (comment,
  // location), so two candidates sharing a comment at one location silently
  // collapse into one claim — which reads downstream as a candidate that was
  // never asked about, not as one that was lost. The emitter asserts the
  // uniqueness instead of relying on it (same lesson as the outer box's probe
  // de-duplication).
  struct assert_candidatet
  {
    uint64_t enc = 0;
    std::string var;  // state variable base name
    std::string rung; // "eq"|"ne"|"ge"|"le"|"gt"|"lt"|"abs"|"delta"
    std::string text; // human-readable candidate, e.g. "post >= pre"
    std::pair<std::string, std::string> key; // claim key, to read the verdict
  };
  static bool path_cov_assert_mode;
  static std::vector<assert_candidatet> path_cov_assert_candidates;

  // Print the per-candidate verdict table. Called after solving, INSTEAD of the
  // [Coverage] block: in this mode a claim that HOLDS is the wanted outcome, so
  // the coverage counters would report a completely successful ladder as 0%.
  //
  // Order is the emission order (state variables in the contract object's own
  // component order, rungs in a fixed order), never completion order, so the
  // printed table is identical under --parallel-solving.
  static void report_path_cov_assertions();
```

## B.4 `src/goto-programs/goto_coverage.cpp`

### B.4.1 static definitions

**MATCH** (unique — 82–84):

```cpp
std::map<std::string, std::string> goto_coveraget::path_cov_refused_coords;
std::string goto_coveraget::path_cov_fingerprint;
```

**REPLACE WITH:**

```cpp
std::map<std::string, std::string> goto_coveraget::path_cov_refused_coords;
bool goto_coveraget::path_cov_assert_mode = false;
std::vector<goto_coveraget::assert_candidatet>
  goto_coveraget::path_cov_assert_candidates;
std::string goto_coveraget::path_cov_fingerprint;
```

### B.4.2 file-scope: the hoisted bound record, the ONE parser, the shared refusal gates, the exact object lookup

**MATCH** (unique — the tail of `audit_certify_witness` and the head of `walk_fields`'s comment, 530–535):

```cpp
    witnessless.size(),
    names);
  abort();
}

// Walk a dotted field path (`taker`, `timelocks.deployedAt`) down from `e`.
```

**REPLACE WITH:**

```cpp
    witnessless.size(),
    names);
  abort();
}

// ---- ONE bound record and ONE parser for every stage-2/3 region spec ----
//
// This was a struct LOCAL to solidity_path_coverage() (`certify_boundt`) with
// its parse loop inlined beside it. Hoisted here so --path-cov-certify and
// --path-cov-assert read their intervals through the same code, and the reason
// is not tidiness: FOUR of the five documented false-certificate routes live in
// this parse and in the two gates below it (empty box, coordinate bounded
// twice, punched-empty box, out-of-type bound). A copied parser is a copy that
// will not receive the next fix — and the copy would be the one whose spec is
// ASSUMED at unit entry, where an unsatisfiable assumption does not make one
// claim vacuous, it makes an entire candidate ladder hold at once.
struct path_cov_boundt
{
  std::string name, lo, hi;
  std::vector<std::string> holes;
};

// Read `j[key]` (an array of {name, lo, hi, holes?}) into `out`. The key is a
// parameter so certify keeps "box" and stage 3 uses "region" without a second
// parser existing.
//
// `lo`/`hi`/`holes` are decimal STRINGS, never JSON numbers: Solidity inputs
// are up to 256 bits and a JSON number would be silently truncated to a double
// on the way in — a region quietly covering the wrong values is the one outcome
// these queries exist to prevent. A missing field throws (`.at`), which the
// caller turns into a fatal, named parse failure; defaulting it would produce a
// plausible full report answering a different question.
static void parse_bounds(
  const nlohmann::json &j,
  const char *key,
  std::vector<path_cov_boundt> &out)
{
  for (const auto &b : j.value(key, nlohmann::json::array()))
  {
    path_cov_boundt cb;
    cb.name = b.at("name").get<std::string>();
    cb.lo = b.at("lo").get<std::string>();
    cb.hi = b.at("hi").get<std::string>();
    for (const auto &h : b.value("holes", nlohmann::json::array()))
      cb.holes.push_back(h.get<std::string>());
    out.push_back(cb);
  }
}

// ---- Routes 1-3: the THREE ways a region is empty before any type is known --
//
// Returns the reason, or "" when this bound is structurally fine. `seen_names`
// carries across the whole spec and is mutated, which is what makes route 2
// (the same coordinate bounded twice) visible at all.
//
// Why a refusal and not a warning: an unsatisfiable entry assumption means
// nothing executes, so every assertion downstream of it holds FOR WANT OF AN
// EXECUTION. The run then prints a certificate next to a region that contains
// no input. That is a false certificate, not a weak one, and there is nothing
// to reinterpret afterwards — which is why the gate sits before the query is
// formed rather than where its answer is read.
static std::string path_cov_structural_refusal(
  const path_cov_boundt &b,
  std::set<std::string> &seen_names)
{
  if (string2integer(b.hi) < string2integer(b.lo))
    return "the box is EMPTY on this coordinate (lo=" + b.lo +
           " > hi=" + b.hi +
           "), so the entry assumption is unsatisfiable and every exit "
           "assert would hold for want of an execution";
  // Closes the obvious hole in the test above: bounding one name twice can
  // intersect to nothing while each bound is individually fine, and a per-bound
  // test would wave both through. Duplicates carry no meaning in this spec, so
  // refusing them costs nothing and leaves no case where "not empty by this
  // test" and "not empty" come apart.
  if (!seen_names.insert(b.name).second)
    return "the coordinate is bounded TWICE in this spec; two bounds on "
           "one name can intersect to an empty box while each is "
           "individually well-formed, which the emptiness test above "
           "would not see";
  if (!b.holes.empty())
  {
    // A PUNCHED interval has a SECOND way of being empty and `lo <= hi` cannot
    // see it: `[5,5] \ {5}` passes that test and admits no input at all.
    // Counting only the DISTINCT holes INSIDE [lo, hi] is what makes this
    // exact — a hole outside the interval removes nothing, and counting it
    // would refuse a perfectly good box.
    const BigInt lo = string2integer(b.lo), hi = string2integer(b.hi);
    std::set<std::string> inside;
    for (const auto &h : b.holes)
    {
      const BigInt hv = string2integer(h);
      if (hv >= lo && hv <= hi)
        inside.insert(integer2string(hv));
    }
    if (BigInt((int64_t)inside.size()) >= (hi - lo + 1))
      return "the PUNCHED box is EMPTY on this coordinate: [" + b.lo + ", " +
             b.hi + "] holds " + integer2string(hi - lo + 1) +
             " value(s) and the holes remove all of them, so the entry "
             "assumption is unsatisfiable. `lo <= hi` does NOT catch "
             "this — the interval is well-formed and the punching is "
             "what empties it";
  }
  return std::string();
}

// Does a decimal from a spec fit an unsigned bit-vector coordinate?
//
// Every constant in these queries is built with constant_int2tc ON THE
// COORDINATE'S OWN TYPE, so a decimal above the type's maximum WRAPS and the
// query is emitted about a different number than the one written down. The
// verdict then describes something nobody asked for, and if it comes back
// SUCCESSFUL it is a false certificate — the same shape as the signed hole in
// coord_expressible, arrived at through the value instead of the type.
static bool path_cov_fits_type(
  const type2tc &t,
  const std::string &dec,
  std::string &tmax_out)
{
  BigInt tmax = 1;
  for (unsigned w = 0; w < t->get_width(); ++w)
    tmax *= 2;
  tmax -= 1;
  tmax_out = integer2string(tmax);
  const BigInt v = string2integer(dec);
  return v >= 0 && v <= tmax;
}

// ---- Route 4: every lo / hi / hole must fit the coordinate's own type ----
//
// Separate from the structural gate because this is the first point at which
// the coordinate's TYPE is known; the structural gate can only compare decimals
// with each other. coord_expressible has already restricted `bt` to an unsigned
// bit-vector, so the admissible range is exactly [0, 2^width - 1].
static std::string
path_cov_out_of_type_refusal(const path_cov_boundt &b, const type2tc &bt)
{
  std::vector<std::pair<std::string, std::string>> vals = {
    {"lo", b.lo}, {"hi", b.hi}};
  for (const auto &h : b.holes)
    vals.push_back({"hole", h});
  for (const auto &[what, txt] : vals)
  {
    std::string tmax;
    if (path_cov_fits_type(bt, txt, tmax))
      continue;
    return "the " + what + " value " + txt +
           " does not fit the coordinate's own type (admissible range [0, " +
           tmax + "])";
  }
  return std::string();
}

// ---- The contract instance object of ONE named contract, by EXACT id ----
//
// resolve_coord picks the object by SUBSTRING (`id.find(scope_contract)`), and
// that test is wrong here in two ways that are both completely silent. With no
// --contract (or with --coverage-whole-unit) `scope_contract` is EMPTY and the
// test degenerates to "any contract object in the program"; and with
// `--contract Escrow` the substring matches `sol:@_ESBMC_Object_EscrowSrc#`,
// which is exactly the shape of the EscrowSrc/EscrowDst benchmarks.
//
// For a post-state assertion, reading the wrong object is the worst available
// outcome. The instrumented unit writes object X, the exit read looks at object
// Y that nothing touched, so `post == pre` HOLDS vacuously and `post != pre` is
// REFUTED — a full, plausible, entirely green ladder about a contract that was
// never measured, with no error anywhere. The must-flip R1 pair is what makes
// that visible, and this function is what stops it.
//
// Exact equality is available because the frontend builds the id as
// `"sol:@" + "_ESBMC_Object_" + <C> + "#"`
// (solidity_convert_contract.cpp:44-51) — trailing '#', nothing after it.
static const symbolt *
path_cov_contract_object(contextt &ctx, const std::string &contract)
{
  if (contract.empty())
    return nullptr;
  const std::string want = "sol:@_ESBMC_Object_" + contract + "#";
  const symbolt *obj = nullptr;
  ctx.foreach_operand([&](const symbolt &s) {
    if (obj == nullptr && s.id.as_string() == want)
      obj = &s;
  });
  return obj;
}

// Is this component of the contract object a USER state variable?
//
// The object carries ESBMC's own fields ($address, $balance, $code, $codehash,
// $mutex_<C>, _ESBMC_bind_cname, $dynamic_pool, padding). Same four-way filter
// bmc.cpp:3357 applies when it restores the whole object, deliberately reusing
// the stricter of the two forms in that file, so a candidate can never be
// emitted about `$balance`.
static bool path_cov_user_state_name(const std::string &n)
{
  return !(
    n.empty() || n[0] == '$' || n.rfind("_ESBMC", 0) == 0 ||
    n.rfind("anon_pad", 0) == 0);
}

// Walk a dotted field path (`taker`, `timelocks.deployedAt`) down from `e`.
```

### B.4.3 certify spec: use the hoisted record and the shared parser

**MATCH** (unique — 2535–2539):

```cpp
  struct certify_boundt
  {
    std::string name, lo, hi;
    std::vector<std::string> holes;
  };
```

**REPLACE WITH:**

```cpp
  // (The record and its parser now live at file scope as `path_cov_boundt` /
  // `parse_bounds`, shared with --path-cov-assert. See the comment there for
  // why sharing rather than copying is a soundness decision.)
```

**MATCH** (unique — 2680):

```cpp
  std::vector<certify_boundt> certify_box;
```

**REPLACE WITH:**

```cpp
  std::vector<path_cov_boundt> certify_box;
```

**MATCH** (unique — 2697–2706):

```cpp
      for (const auto &b : j.value("box", nlohmann::json::array()))
      {
        certify_boundt cb;
        cb.name = b.at("name").get<std::string>();
        cb.lo = b.at("lo").get<std::string>();
        cb.hi = b.at("hi").get<std::string>();
        for (const auto &h : b.value("holes", nlohmann::json::array()))
          cb.holes.push_back(h.get<std::string>());
        certify_box.push_back(cb);
      }
```

**REPLACE WITH:**

```cpp
      parse_bounds(j, "box", certify_box);
```

### B.4.4 certify structural gate: call the shared helper (identical messages)

**MATCH** (unique — 5428–5476, the body of the empty-box block):

```cpp
      {
        std::set<std::string> box_names;
        for (const auto &b : certify_box)
        {
          std::string bad;
          if (string2integer(b.hi) < string2integer(b.lo))
            bad = "the box is EMPTY on this coordinate (lo=" + b.lo +
                  " > hi=" + b.hi +
                  "), so the entry assumption is unsatisfiable and every exit "
                  "assert would hold for want of an execution";
```

…through…

```cpp
            if (BigInt((int64_t)inside.size()) >= (hi - lo + 1))
              bad = "the PUNCHED box is EMPTY on this coordinate: [" + b.lo +
                    ", " + b.hi + "] holds " + integer2string(hi - lo + 1) +
                    " value(s) and the holes remove all of them, so the entry "
                    "assumption is unsatisfiable. `lo <= hi` does NOT catch "
                    "this — the interval is well-formed and the punching is "
                    "what empties it";
          }
          if (!bad.empty())
```

**REPLACE the whole span 5428–5477 (from `      {` at 5428 through `          if (!bad.empty())` at 5477) WITH:**

```cpp
      {
        // The three structural routes now live in path_cov_structural_refusal
        // at file scope, shared with --path-cov-assert. The wording is
        // unchanged — the refusal MESSAGE is what three regressions pin, and a
        // shared gate that reworded itself would look like a fix and read as a
        // regression.
        std::set<std::string> box_names;
        for (const auto &b : certify_box)
        {
          const std::string bad = path_cov_structural_refusal(b, box_names);
          if (!bad.empty())
```

*(the `log_error(...) ; exit(1); } }` that follows at 5479–5491 is untouched and still closes correctly: the `for` body becomes `{ const std::string bad = ...; if (!bad.empty()) { log_error(...); exit(1); } }`.)*

### B.4.5 certify out-of-type gate: call the shared helper (identical message)

**MATCH** (unique — 5557–5586):

```cpp
        {
          BigInt tmax = 1;
          for (unsigned w = 0; w < bt->get_width(); ++w)
            tmax *= 2;
          tmax -= 1;
          std::vector<std::pair<std::string, std::string>> vals = {
            {"lo", b.lo}, {"hi", b.hi}};
          for (const auto &h : b.holes)
            vals.push_back({"hole", h});
          for (const auto &[what, txt] : vals)
          {
            const BigInt v = string2integer(txt);
            if (v >= 0 && v <= tmax)
              continue;
            log_error(
              "--path-cov-certify: unit '{}' — REFUSING THE QUERY on coordinate "
              "'{}': the {} value {} does not fit the coordinate's own type "
              "(admissible range [0, {}]). The bound is built as a constant of "
              "that type, so an out-of-range decimal WRAPS and the query would "
              "be emitted about a different value than the one written here — "
              "answering SUCCESSFUL about a box nobody asked for. Certification "
              "is not attempted",
              uid,
              b.name,
              what,
              txt,
              integer2string(tmax));
            exit(1);
          }
        }
```

**REPLACE WITH:**

```cpp
        {
          // Route 4, shared with --path-cov-assert (path_cov_out_of_type_refusal
          // at file scope). The helper returns the value-specific half of the
          // sentence, so the concatenation below is byte-identical to what it
          // printed before — which is what the regression pins.
          const std::string bad = path_cov_out_of_type_refusal(b, bt);
          if (!bad.empty())
          {
            log_error(
              "--path-cov-certify: unit '{}' — REFUSING THE QUERY on coordinate "
              "'{}': {}. The bound is built as a constant of "
              "that type, so an out-of-range decimal WRAPS and the query would "
              "be emitted about a different value than the one written here — "
              "answering SUCCESSFUL about a box nobody asked for. Certification "
              "is not attempted",
              uid,
              b.name,
              bad);
            exit(1);
          }
        }
```

### B.4.6 the stage-3 spec parse and banner (new; after the certify spec block)

**MATCH** (unique — 2758–2764, the tail of the certify banner and the head of the slicing block):

```cpp
      certify_unit,
      certify_enc,
      certify_depth,
      certify_box.size());
  }

  // Keep the counterexample payload alive through slicing, WITHOUT switching
```

**REPLACE WITH:**

```cpp
      certify_unit,
      certify_enc,
      certify_depth,
      certify_box.size());
  }

  // ---- STAGE 3: post-state assertion synthesis spec (--path-cov-assert) ----
  //
  // {"unit": ..., "enc": N, "depth": D,
  //  "region": [{"name","lo","hi","holes"?}, ...],
  //  "vars":   [{"name","abs_lo"?,"abs_hi"?,
  //              "delta_dir"?,"delta_lo"?,"delta_hi"?}, ...]}
  //
  // `region` is byte for byte the shape certify parses under "box" and goes
  // through the SAME parser. `vars` is optional; omitting it emits the equality
  // and sign rungs for every eligible state variable, which is the intended
  // default.
  struct assert_vart
  {
    std::string name;
    bool has_abs = false;
    std::string abs_lo, abs_hi;
    bool has_delta = false;
    std::string delta_dir, delta_lo, delta_hi;
  };
  bool assert_on = false;
  // Did the spec ever find its unit? Route 5, mirrored from certify: a spec
  // matching NO unit emits no assume and no assert, so nothing is checked and
  // the run prints VERIFICATION SUCCESSFUL with exit 0.
  size_t assert_units_matched = 0;
  std::string assert_unit;
  uint64_t assert_enc = 0, assert_depth = 0;
  std::vector<path_cov_boundt> assert_region;
  std::vector<assert_vart> assert_vars;
  // "vars was written down" is NOT "vars named something". They are two
  // different entry conditions into the same symptom (an empty ladder) and they
  // need two different messages — see N1 at the gate after the unit loop.
  bool assert_vars_present = false;
  path_cov_assert_mode = false;
  path_cov_assert_candidates.clear();
  if (!path_cov_assert_path.empty())
  {
    std::ifstream ain(path_cov_assert_path);
    if (!ain)
    {
      log_error("--path-cov-assert: cannot open '{}'", path_cov_assert_path);
      abort();
    }
    try
    {
      nlohmann::json j;
      ain >> j;
      assert_unit = j.at("unit").get<std::string>();
      assert_enc = j.at("enc").get<uint64_t>();
      assert_depth = j.at("depth").get<uint64_t>();
      parse_bounds(j, "region", assert_region);
      assert_vars_present = j.contains("vars");
      for (const auto &v : j.value("vars", nlohmann::json::array()))
      {
        assert_vart av;
        av.name = v.at("name").get<std::string>();
        if (v.contains("abs_lo") || v.contains("abs_hi"))
        {
          // Half an interval is not an interval. Refused rather than completed
          // with a type bound, because `post <= 100` and `0 <= post <= 100` are
          // the same claim on an unsigned type only by accident of the type,
          // and the spec would then be answered about something it did not say.
          av.abs_lo = v.at("abs_lo").get<std::string>();
          av.abs_hi = v.at("abs_hi").get<std::string>();
          av.has_abs = true;
        }
        if (
          v.contains("delta_lo") || v.contains("delta_hi") ||
          v.contains("delta_dir"))
        {
          // A DIRECTION IS MANDATORY, and defaulting it is a false-certificate
          // route. Candidate variables are unsigned (coord_expressible refuses
          // signed outright), so `post - pre` WRAPS on a decrease: a spec that
          // meant "decreases by 1..10" and was defaulted to `inc` would be
          // answered about the wrapped difference, which can sit anywhere in
          // the type. Requiring the word is one line; guessing it is a verdict
          // about a different quantity.
          av.delta_dir = v.at("delta_dir").get<std::string>();
          av.delta_lo = v.at("delta_lo").get<std::string>();
          av.delta_hi = v.at("delta_hi").get<std::string>();
          if (av.delta_dir != "inc" && av.delta_dir != "dec")
            throw std::runtime_error(
              "variable '" + av.name + "': delta_dir must be \"inc\" or \"dec\"");
          av.has_delta = true;
        }
        assert_vars.push_back(av);
      }
    }
    catch (const std::exception &ex)
    {
      // Fatal rather than "ignore and fall back to enumeration", for the same
      // reason certify is: a malformed spec that silently produced an ordinary
      // coverage run would print a full, plausible report answering a different
      // question.
      log_error(
        "--path-cov-assert: cannot parse '{}' ({}). Expected "
        "{{\"unit\":..., \"enc\":N, \"depth\":D, "
        "\"region\":[{{\"name\":...,\"lo\":\"..\",\"hi\":\"..\"}}], "
        "\"vars\":[{{\"name\":...,\"abs_lo\":\"..\",\"abs_hi\":\"..\","
        "\"delta_dir\":\"inc|dec\",\"delta_lo\":\"..\",\"delta_hi\":\"..\"}}]}}",
        path_cov_assert_path,
        ex.what());
      abort();
    }
    // ---- N1, entry condition (a): `vars` is present and names NOTHING ----
    //
    // Zero candidates are emitted, nothing is checked, and the run prints
    // VERIFICATION SUCCESSFUL with exit 0 — the same shape as route 5, and
    // indistinguishable from a ladder that passed. Refused here, at the parse,
    // because it is knowable here; entry condition (b) (every eligible variable
    // refused) can only be known after the unit is walked and has its own gate
    // and its own message. Closing one of the two would leave a run that looks
    // exactly like a fix.
    if (assert_vars_present && assert_vars.empty())
    {
      log_error(
        "--path-cov-assert: the spec contains an EMPTY \"vars\" array, so not "
        "one candidate assertion would be emitted and the run would print "
        "VERIFICATION SUCCESSFUL for a ladder it never built. Omit \"vars\" "
        "entirely to get the default ladder over every eligible state "
        "variable, or name the variables to assert about");
      exit(1);
    }
    assert_on = true;
    path_cov_assert_mode = true;
    log_status(
      "--path-cov-assert: POST-STATE ASSERTION LADDER for unit '{}' path enc={} "
      "depth={} over {} region bound(s) and {} explicit variable spec(s). The "
      "region is ASSUMED at entry — it is exactly the `require` a generated "
      "test would carry — and each candidate is asserted at THIS path's own "
      "exit under `tr != enc || cnt != depth`, so it is vacuous on every other "
      "path. One fixed assumption, a whole ladder of assertions, ONE run. NO "
      "[Coverage] block is printed and the run's VERIFICATION "
      "SUCCESSFUL/FAILED line is NOT the result: a REFUTED candidate is the "
      "ladder working. The result is the per-candidate table printed after "
      "solving",
      assert_unit,
      assert_enc,
      assert_depth,
      assert_region.size(),
      assert_vars.size());
  }

  // Keep the counterexample payload alive through slicing, WITHOUT switching
```

### B.4.7 decision-recorder gate (diagnostic)

**MATCH** (unique — 3806–3809):

```cpp
    const bool trace_decisions =
      outer_on && (f_it->first.as_string() == outer_unit ||
                   f_it->first.as_string().find("@F@" + outer_unit + "#") !=
                     std::string::npos);
```

**REPLACE WITH:**

```cpp
    // The gate names ONE unit per mode, and the `outer_on` / `assert_on` guard
    // in front of each name is not decoration: with the mode off, `outer_unit`
    // is empty and `find("@F@" + "" + "#")` matches every unit in the program —
    // which is precisely the whole-contract cost this gate exists to avoid.
    auto spec_names_this_unit = [&](const std::string &spec) {
      return f_it->first.as_string() == spec ||
             f_it->first.as_string().find("@F@" + spec + "#") !=
               std::string::npos;
    };
    const bool trace_decisions = (outer_on && spec_names_this_unit(outer_unit)) ||
                                 (assert_on && spec_names_this_unit(assert_unit));
```

### B.4.8 THE STAGE-3 BRANCH (new)

**MATCH** (unique — 5666–5672, the tail of the certify branch and the head of the insertion loop):

```cpp
      certify_enc,
      certify_depth,
      exits.size());
      total_paths += exits.size();
      continue;
    }

    size_t ins_idx = 0;
```

*(note: the two lines after `exits.size());` are indented 6 spaces in the file — reproduce verbatim as shown)*

**REPLACE WITH:**

```cpp
      certify_enc,
      certify_depth,
      exits.size());
      total_paths += exits.size();
      continue;
    }

    // ---- STAGE 3: POST-STATE ASSERTION SYNTHESIS (--path-cov-assert) ----
    //
    // Third branch in the same place and for the same reason as the two above:
    // expansion, the ABI gate, Phase-1 `tr`/`cnt` accounting, the
    // `tr`-completeness invariant, the exit census and the decision-set census
    // have all already run, and the antecedent asserted here — `tr != enc ||
    // cnt != depth` — IS that accounting. Asserting a post-state against
    // bookkeeping nothing checked would be asserting nothing.
    if (assert_on)
    {
      const std::string uid = f_it->first.as_string();
      if (
        uid != assert_unit &&
        uid.find("@F@" + assert_unit + "#") == std::string::npos)
        continue; // other units contribute nothing to this ladder
      ++assert_units_matched;

      // Everything inserted at entry goes BEFORE this iterator, in emission
      // order: region ASSUMEs first, then the `pre` snapshots, so each snapshot
      // is taken under the region and the trace reads the way a generated
      // Foundry test reads. Captured once because the certify branch's
      // `insert(instructions.begin(), ...)` always inserts at the very FRONT,
      // which would put a snapshot taken afterwards ahead of its own assumption.
      auto entry = goto_program.instructions.begin();

      // ---- N4: A NAMED OBSTACLE UNIT MAY NOT BE GIVEN AN ORACLE ----
      //
      // Both flags are computed above and in scope. On an obstructed unit the
      // model admits executions the chain does not have, so a HOLDS verdict
      // here authorises an `assertEq` that can be RED on the unmodified
      // contract — the single outcome this pipeline must never produce. A
      // certified post-state assertion is EXACTLY the artefact the header's
      // rule is about: "a marked path must be excluded from the sibling set AND
      // must not be turned into a test. Marking without excluding would be
      // worthless." The two routes are named apart because they need different
      // fixes.
      if (unit_has_lost_decision || unit_calls_gated_unit)
      {
        log_error(
          "--path-cov-assert: unit '{}' — REFUSING THE LADDER: this unit is a "
          "NAMED OBSTACLE ({}{}{}). A post-state assertion is what a generated "
          "test turns into an assertEq, and on an obstructed unit the model "
          "admits an execution the chain does not have — so a candidate that "
          "HOLDS here can still be RED on the unmodified contract. No candidate "
          "is emitted: an assertion that cannot be trusted is worse than none",
          uid,
          unit_has_lost_decision
            ? "a source-level decision the frontend lowered to a "
              "control-flow-free assume, so the reverting execution does not "
              "exist in the model at all"
            : "",
          (unit_has_lost_decision && unit_calls_gated_unit) ? "; and " : "",
          unit_calls_gated_unit
            ? "it still calls another UNIT's own gated body unexpanded (" +
                residual_unit_names +
                "), routing an INTERNAL call through the EXTERNAL-entry body "
                "and its ABI value gate"
            : "");
        exit(1);
      }

      // ---- Routes 1-3: the region's structure, before any type is known ----
      {
        std::set<std::string> region_names;
        for (const auto &b : assert_region)
        {
          const std::string bad =
            path_cov_structural_refusal(b, region_names);
          if (!bad.empty())
          {
            log_error(
              "--path-cov-assert: unit '{}' — REFUSING THE LADDER on region "
              "coordinate '{}': {}. An unsatisfiable entry assumption is worse "
              "here than in certification: nothing executes, so EVERY candidate "
              "on the ladder holds for want of an execution and the report "
              "reads as a whole set of certified post-state assertions. No "
              "ladder is emitted",
              uid,
              b.name,
              bad);
            exit(1);
          }
        }
      }

      // ---- Resolve, type-check and ASSUME the region at unit entry ----
      const symbolt *fsym = ns.lookup(f_it->first);
      size_t bounds_emitted = 0;
      // Counted at the EMISSION, inside the conjunction, never from
      // `b.holes.size()`. MEASURED on the certify side: with the conjunction
      // disabled the query correctly flipped to FAILED while a spec-derived
      // counter still reported "1 hole(s) punched" — a counter that reads the
      // SPEC cannot witness whether the spec reached the formula.
      size_t holes_emitted = 0;
      for (const auto &b : assert_region)
      {
        expr2tc bs;
        std::string why;
        if (!resolve_coord(fsym, b.name, bs))
          why =
            "the name does not resolve to an input of this unit. Name a "
            "parameter of this unit, an environment value as `msg.value` / "
            "`tx.origin` / `block.timestamp`, or a state variable at entry as "
            "`state.<field>` (which reaches the contract object's own "
            "components only — a mapping or a dynamic array does not resolve)";
        else
          coord_expressible(bs->type, why);
        if (!why.empty())
        {
          // REFUSE THE LADDER, not just the coordinate — the certify
          // disposition, not the outer box's. Dropping a requested region bound
          // would assume a strictly WIDER region, and every candidate would
          // then be certified over inputs nobody asked about. "The region omits
          // c" and "c is unconstrained" are the SAME constraint to the solver
          // and opposite claims to a reader.
          log_error(
            "--path-cov-assert: unit '{}' — REFUSING THE LADDER because region "
            "coordinate '{}' cannot be expressed: {}. Dropping the bound would "
            "assume a WIDER region than the one asked for, and every candidate "
            "would be certified over inputs nobody requested",
            uid,
            b.name,
            why);
          exit(1);
        }
        const type2tc bt = bs->type;
        // ---- Route 4 on the region ----
        {
          const std::string bad = path_cov_out_of_type_refusal(b, bt);
          if (!bad.empty())
          {
            log_error(
              "--path-cov-assert: unit '{}' — REFUSING THE LADDER on region "
              "coordinate '{}': {}. The bound is built as a constant of that "
              "type, so an out-of-range decimal WRAPS and the region assumed "
              "would not be the region written here",
              uid,
              b.name,
              bad);
            exit(1);
          }
        }
        expr2tc bguard = and2tc(
          greaterthanequal2tc(bs, constant_int2tc(bt, string2integer(b.lo))),
          lessthanequal2tc(bs, constant_int2tc(bt, string2integer(b.hi))));
        for (const auto &h : b.holes)
        {
          bguard = and2tc(
            bguard, notequal2tc(bs, constant_int2tc(bt, string2integer(h))));
          ++holes_emitted;
        }
        goto_programt::instructiont asm_i;
        asm_i.type = ASSUME;
        asm_i.guard = bguard;
        asm_i.location = entry->location;
        // "skipped" keeps this out of the decision-set census, which flags a
        // user-source ASSUME as a lowered-away branch. This one is ours.
        asm_i.location.property("skipped");
        asm_i.function = entry->location.get_function();
        goto_program.instructions.insert(entry, asm_i);
        ++bounds_emitted;
      }

      // ---- Find pi's OWN exit, and refuse three ways it can be wrong ----
      goto_programt::targett exit_pc;
      size_t exit_idx = 0;
      bool found = false;
      for (size_t i = 0; i < to_insert.size(); ++i)
      {
        const std::string &cm = std::get<2>(to_insert[i]);
        const size_t q = cm.rfind(":path:");
        if (q == std::string::npos)
          continue;
        if (strtoull(cm.substr(q + 6).c_str(), nullptr, 10) == assert_enc)
        {
          exit_pc = std::get<0>(to_insert[i]);
          exit_idx = i;
          found = true;
          break;
        }
      }
      // ---- N2: the path's `enc` does not exist for this unit ----
      //
      // The outer-box branch only WARNS here, and correctly so: there a missing
      // path costs one measurement out of many. Here it means the ladder was
      // emitted nowhere at all, nothing is checked, and the run prints
      // VERIFICATION SUCCESSFUL with exit 0 — route 5 by another door.
      if (!found)
      {
        log_error(
          "--path-cov-assert: unit '{}' — REFUSING THE LADDER: path enc={} is "
          "not among this unit's {} enumerated path(s), so not one assertion "
          "would be emitted and the run would print VERIFICATION SUCCESSFUL for "
          "a ladder it never built. Check `enc` against the enumeration run's "
          "`{}:path:<enc>` claim lines",
          uid,
          assert_enc,
          to_insert.size(),
          uid);
        exit(1);
      }
      // ---- N3: `depth` disagrees with the enumerated depth ----
      //
      // The most dangerous of the new routes, because today it produces NO
      // diagnostic at all. The antecedent is `tr != enc || cnt != depth`; a
      // wrong `depth` makes it TRUE on every single execution, so every
      // candidate holds VACUOUSLY and the report reads as a fully successful
      // certification of the whole ladder — the exact output a correct run
      // produces when everything really does hold. One map lookup closes it.
      {
        auto dit = path_decision_depth.find(
          {std::get<2>(to_insert[exit_idx]), exit_pc->location.as_string()});
        if (dit == path_decision_depth.end() || dit->second != assert_depth)
        {
          log_error(
            "--path-cov-assert: unit '{}' — REFUSING THE LADDER: the spec says "
            "path enc={} has depth={}, the enumeration says {}. The antecedent "
            "is `tr != enc || cnt != depth`, so a wrong depth is TRUE on every "
            "execution: every candidate would hold vacuously and the report "
            "would be indistinguishable from a completely successful "
            "certification. This is refused rather than warned about precisely "
            "because the wrong answer looks exactly like the right one",
            uid,
            assert_enc,
            assert_depth,
            dit == path_decision_depth.end()
              ? std::string("<no depth recorded for this exit>")
              : std::to_string(dit->second));
          exit(1);
        }
      }
      // ---- N5: what KIND of exit is it? ----
      //
      // THE TRAP: `revert_paths` / `rollback_revert_paths` /
      // `undetermined_exit_paths` are filled by the insertion loop below, which
      // this branch `continue`s past — so in this mode they are EMPTY and
      // reading them would classify every exit as normal. The classification
      // has to come from the locals that are computed before the branch.
      {
        const bool is_error_revert = std::get<3>(to_insert[exit_idx]);
        const bool is_rollback = rollback_exits.count(exit_idx) != 0;
        const bool is_undetermined = undetermined_exits.count(exit_idx) != 0;
        if (is_error_revert)
        {
          // A custom-error `revert E()` lowers to a `#sol_error` callee with NO
          // state rollback, so the state at that instruction is the state AT
          // THE REVERT POINT. On chain every write of a reverted transaction is
          // undone, so this "post-state" is one that never exists. An assertion
          // about it is not weak, it is about nothing.
          log_error(
            "--path-cov-assert: unit '{}' — REFUSING THE LADDER: path enc={} "
            "exits through a CUSTOM-ERROR revert (`revert E()`), which the "
            "frontend lowers with no state rollback. The state readable there "
            "is the state at the revert point, not the EVM post-state — on "
            "chain every write of that transaction is undone. A post-state "
            "assertion there describes a state that does not exist",
            uid,
            assert_enc);
          exit(1);
        }
        if (is_undetermined)
        {
          log_error(
            "--path-cov-assert: unit '{}' — REFUSING THE LADDER: path enc={} "
            "has an UNDETERMINED exit — no positive evidence separates a "
            "reverting execution from a normal one there. An undetermined exit "
            "cannot become an oracle: half the readings of it make the "
            "post-state a rolled-back state that never existed",
            uid,
            assert_enc);
          exit(1);
        }
        if (is_rollback)
          // ALLOWED and LABELLED. Here the rollback IS modelled (`*this =
          // _sol_save_this` is one aggregate write to the same object,
          // performed at the revert point and therefore before the assertion),
          // so the exit read really is the correctly restored state and
          // `post == pre` holding is a true and useful statement. It must be
          // reported as "the rollback held", never as "the function did not
          // change state" — the two are the same numbers and opposite claims.
          log_warning(
            "--path-cov-assert: unit '{}' path enc={} exits through a ROLLBACK "
            "revert (require / revert(\"msg\")). The rollback IS modelled, so "
            "the values read below are the correctly RESTORED state and the "
            "ladder is emitted — but read every verdict as a statement about a "
            "REVERTING transaction. In particular `post == pre` holding here "
            "means the rollback worked, NOT that the function leaves state "
            "alone",
            uid,
            assert_enc);
      }

      // ---- Enumerate the candidate state variables ----
      //
      // (A): by EXACT contract match, never by the substring test resolve_coord
      // uses. See path_cov_contract_object for what reading the wrong object
      // does to this mode specifically.
      const symbolt *obj =
        path_cov_contract_object(*cov_context, contract_of(uid));
      if (obj == nullptr)
      {
        log_error(
          "--path-cov-assert: unit '{}' — REFUSING THE LADDER: no contract "
          "instance object 'sol:@_ESBMC_Object_{}#' exists for this unit's own "
          "contract. The object is resolved by EXACT name on purpose: a "
          "substring match would happily pick a DIFFERENT contract's object, "
          "and the exit read would then observe an object nothing wrote — "
          "`post == pre` would hold vacuously and the whole ladder would come "
          "back green for a contract that was never measured",
          uid,
          contract_of(uid));
        exit(1);
      }
      const typet ostruct = ns.follow(obj->type);
      if (ostruct.id() != "struct")
      {
        log_error(
          "--path-cov-assert: unit '{}' — REFUSING THE LADDER: the contract "
          "instance object does not follow to a struct, so its state variables "
          "cannot be enumerated",
          uid);
        exit(1);
      }

      // Component base names, collected first, for two independent uses: the
      // candidate ladder below, and the second scan's exclusion set.
      std::vector<std::string> comp_names;
      for (const auto &comp : to_struct_type(ostruct).components())
      {
        std::string vn = comp.get("#base_name").as_string();
        if (vn.empty())
          vn = comp.get_name().as_string();
        if (path_cov_user_state_name(vn))
          comp_names.push_back(vn);
      }

      // ---- SECOND SCAN: the state variables that are NOT components ----
      //
      // A mapping or dynamic array is not a field of the contract object — the
      // frontend lowers those to contract-scope globals `sol:@C@<C>@<name>#N` —
      // so iterating components alone would let them VANISH from the report.
      // That is not a smaller answer, it is a wrong one: in this mode an absent
      // variable reads as "no assertion was needed", i.e. as "unchanged". Same
      // reason path_ce_t::state_written_unrendered exists.
      //
      // Names already seen as components are excluded, because the frontend
      // also registers contract-scope symbols for ordinary scalars; without the
      // exclusion every scalar would be reported as both measured and refused.
      {
        const std::string cpfx = "sol:@C@" + contract_of(uid) + "@";
        cov_context->foreach_operand([&](const symbolt &s) {
          const std::string id = s.id.as_string();
          if (id.rfind(cpfx, 0) != 0 || id.find("@F@") != std::string::npos)
            return;
          std::string nm = id.substr(cpfx.size());
          const size_t hash = nm.find('#');
          if (hash != std::string::npos)
            nm = nm.substr(0, hash);
          if (!path_cov_user_state_name(nm))
            return;
          if (
            std::find(comp_names.begin(), comp_names.end(), nm) !=
            comp_names.end())
            return;
          path_cov_refused_coords[nm] =
            "a mapping or dynamic array: the frontend lowers it to a "
            "contract-scope global, not a component of the contract object, so "
            "no scalar post-state candidate can be formed for it. Its absence "
            "from the table below is a REFUSAL, not a measurement — reading it "
            "as \"unchanged\" is a claim about a variable nothing was asserted "
            "on";
        });
      }

      // Was every explicitly named variable actually found? A `vars` entry
      // naming a variable that does not exist is N1's first entry condition
      // arriving one step later, and it is worth its own message: the ladder
      // would come out short (or empty) with nothing saying why.
      std::set<std::string> named_wanted, named_seen;
      for (const auto &v : assert_vars)
        named_wanted.insert(v.name);

      // ---- The antecedent, and the ladder ----
      //
      // Byte for byte the outer box's construction: the candidate is asserted
      // only under this path's identity, so at any other exit — and on any
      // other execution — the implication is vacuous and costs nothing.
      const expr2tc not_this_path = or2tc(
        notequal2tc(tr, constant_int2tc(utype, BigInt(assert_enc))),
        notequal2tc(cnt, constant_int2tc(utype, BigInt(assert_depth))));

      size_t emitted = 0, vars_emitted = 0;
      auto emit_rung = [&](
                         const std::string &var,
                         const std::string &rung,
                         const std::string &text,
                         const expr2tc &cand) {
        // THE COMMENT SHAPE IS A HARD CONSTRAINT: `<unit-id>:path:<enc>` with
        // the unit id FIRST and nothing in front of it, the candidate id a
        // SUFFIX. MEASURED on the certify side: a leading `certify:` made the
        // report's `path_function` read `certify:sol:@C@Box@F@f#18`, the
        // counterexample harvest builds the expected argument scope from that
        // string, every nondet then failed the scope test and was filed as
        // harness-internal — `inputs` empty, verdict still perfectly correct,
        // loss entirely silent. Here the witness is what makes a REFUTED
        // candidate actionable, so losing it loses the point of the refutation.
        const std::string comment = id2string(f_it->first) +
                                    ":path:" + std::to_string(assert_enc) + "#" +
                                    rung + "_" + var;
        const std::string loc = exit_pc->location.as_string();
        // all_claims FIRST, before the insert — the ordering every other branch
        // uses, and what makes the claim visible to audit_entry_liveness even
        // if the solve never reaches it.
        if (!all_claims.insert({comment, loc}).second)
        {
          // (rung, var) is unique by construction, so reaching here means the
          // construction changed. Asserted rather than assumed because the
          // failure is silent: all_claims is a SET, so the duplicate is dropped
          // and the candidate reads as one that was never asked about.
          log_error(
            "--path-cov-assert: INTERNAL DEFECT — duplicate claim key '{}' at "
            "{}. Claim keys are a set, so one of the two candidates would be "
            "silently dropped and would read in the table below as a candidate "
            "nobody asked for",
            comment,
            loc);
          abort();
        }
        path_cov_assert_candidates.push_back(
          {assert_enc, var, rung, text, {comment, loc}});
        insert_assert(
          goto_program, exit_pc, or2tc(not_this_path, cand), comment);
        ++emitted;
      };

      for (const auto &comp : to_struct_type(ostruct).components())
      {
        std::string vname = comp.get("#base_name").as_string();
        if (vname.empty())
          vname = comp.get_name().as_string();
        if (!path_cov_user_state_name(vname))
          continue;
        const assert_vart *spec = nullptr;
        for (const auto &v : assert_vars)
          if (v.name == vname)
            spec = &v;
        if (!assert_vars.empty() && spec == nullptr)
          continue; // an explicit `vars` list is a whitelist
        if (spec != nullptr)
          named_seen.insert(vname);

        // The live member expression, built exactly as resolve_coord builds
        // `state.<field>`, so a variable named here and a value reported by the
        // counterexample harvest refer to the same thing by construction.
        expr2tc live = symbol2tc(migrate_type(ostruct), obj->id);
        if (!walk_fields(ns, live, vname))
        {
          path_cov_refused_coords[vname] =
            "the component does not resolve through the contract object's "
            "field walk, so no post-state expression can be built for it";
          continue;
        }
        const type2tc vt = live->type;

        // ---- (F): coord_expressible is the WRONG gate for the equality rungs
        //
        // It refuses `bool` because a two-point domain has no interval to
        // measure. That is right for the interval and delta rungs and wrong for
        // `post == pre` / `post != pre`, which are perfectly expressible on a
        // bool — and reusing it as written would silently drop every boolean
        // state variable, which is exactly the class of variable a flag-setting
        // function is about. So the gate is split: equality needs equality,
        // ordering and intervals need an interval.
        std::string why;
        const bool interval_ok = coord_expressible(vt, why);
        const bool equality_ok = interval_ok || is_bool_type(vt);
        if (!equality_ok)
        {
          path_cov_refused_coords[vname] = why;
          continue;
        }
        if (!interval_ok)
          // Recorded rather than omitted, and recorded under a key that says
          // WHICH rungs are missing. A partially-emitted variable that reported
          // nothing would read as a variable whose sign was measured and came
          // out unconstrained.
          path_cov_refused_coords[vname + " [ordering/interval rungs]"] =
            why +
            ". The equality rungs (post == pre / post != pre) ARE emitted for "
            "it — only the ordering, interval and delta rungs are not";

        // ---- pre_v: the entry snapshot ----
        //
        // Taken from the SAME member expression the exit read uses. Its purpose
        // is the outer box's: the assertion sits at the exit, and reading only
        // the live symbol there would compare the post-state with itself.
        // `.location.property("skipped")` is load bearing — without it the
        // instruction enters the decision-set census as a user construct.
        // Plain list insert, never insert_swap: insert_swap moves the
        // instruction's CONTENT, so the iterator naming the original first
        // instruction ends up naming the new one and the function acquires a
        // self-loop (measured, on the ABI gate).
        symbolt ssym;
        ssym.type = migrate_type_back(vt);
        ssym.name = "__ESBMC_pre$" + i2string(ghost_counter++);
        ssym.id = "path_cov::" + id2string(ssym.name);
        ssym.lvalue = true;
        ssym.static_lifetime = false;
        ssym.is_extern = false;
        symbolt *psn;
        cov_context->move(ssym, psn);
        expr2tc pre_v = symbol2tc(migrate_type(psn->type), psn->id);
        goto_programt::instructiont dcl;
        dcl.type = DECL;
        dcl.code = code_decl2tc(vt, psn->id);
        dcl.location = entry->location;
        dcl.location.property("skipped");
        dcl.function = entry->location.get_function();
        goto_program.instructions.insert(entry, dcl);
        goto_programt::instructiont asg;
        asg.type = ASSIGN;
        asg.code = code_assign2tc(pre_v, live);
        asg.location = entry->location;
        asg.location.property("skipped");
        asg.function = entry->location.get_function();
        goto_program.instructions.insert(entry, asg);
        ++vars_emitted;

        // R1 — the equality rungs. Emitted as a PAIR, always, and that is the
        // one thing this mode can testify to on its own: the two are
        // necessarily opposite, so a run in which both HOLD is a run in which
        // the exit read is not observing the unit's writes (condition (A)), and
        // a run in which both are REFUTED is one in which the antecedent never
        // matched. Neither needs fault injection to be visible.
        emit_rung(vname, "eq", "post == pre", equality2tc(live, pre_v));
        emit_rung(vname, "ne", "post != pre", notequal2tc(live, pre_v));

        if (!interval_ok)
          continue;

        emit_rung(
          vname, "ge", "post >= pre", greaterthanequal2tc(live, pre_v));
        emit_rung(vname, "le", "post <= pre", lessthanequal2tc(live, pre_v));
        emit_rung(vname, "gt", "post > pre", greaterthan2tc(live, pre_v));
        emit_rung(vname, "lt", "post < pre", lessthan2tc(live, pre_v));

        // Every ladder constant must fit the VARIABLE's own type, for exactly
        // the reason a region bound must (route 4): the constant is built on
        // that type, so an out-of-range decimal wraps and the run answers about
        // a bound nobody wrote.
        auto require_fits = [&](const char *what, const std::string &dec) {
          std::string tmax;
          if (path_cov_fits_type(vt, dec, tmax))
            return;
          log_error(
            "--path-cov-assert: unit '{}' — REFUSING THE LADDER: variable '{}' "
            "{} value {} does not fit its own type (admissible range [0, {}]). "
            "The bound is built as a constant of that type, so an out-of-range "
            "decimal WRAPS and the candidate asserted would not be the "
            "candidate written here",
            uid,
            vname,
            what,
            dec,
            tmax);
          exit(1);
        };

        if (spec != nullptr && spec->has_abs)
        {
          require_fits("abs_lo", spec->abs_lo);
          require_fits("abs_hi", spec->abs_hi);
          emit_rung(
            vname,
            "abs",
            "post in [" + spec->abs_lo + ", " + spec->abs_hi + "]",
            and2tc(
              greaterthanequal2tc(
                live, constant_int2tc(vt, string2integer(spec->abs_lo))),
              lessthanequal2tc(
                live, constant_int2tc(vt, string2integer(spec->abs_hi)))));
        }
        if (spec != nullptr && spec->has_delta)
        {
          require_fits("delta_lo", spec->delta_lo);
          require_fits("delta_hi", spec->delta_hi);
          // ---- THE DIRECTION CONJUNCT IS NOT DECORATION ----
          //
          // Candidate variables are unsigned, so `post - pre` WRAPS when the
          // value decreased: a decrease of d shows up as 2^w - d. A naive
          // `lo <= post - pre <= hi` therefore HOLDS on a decreasing path
          // whenever the wrapped difference happens to land in the window —
          // and for a wide window (`[0, 2^w-1]`, the "any increase" spec a
          // driver writes first) it holds on EVERY decreasing path. The
          // `post >= pre` conjunct is what makes the subtraction mean the thing
          // the spec's word `inc` says.
          const expr2tc d = spec->delta_dir == "inc"
                              ? sub2tc(vt, live, pre_v)
                              : sub2tc(vt, pre_v, live);
          const expr2tc dir = spec->delta_dir == "inc"
                                ? greaterthanequal2tc(live, pre_v)
                                : greaterthanequal2tc(pre_v, live);
          emit_rung(
            vname,
            "delta",
            (spec->delta_dir == "inc" ? std::string("post - pre in [")
                                      : std::string("pre - post in [")) +
              spec->delta_lo + ", " + spec->delta_hi + "] with " +
              (spec->delta_dir == "inc" ? "post >= pre" : "pre >= post"),
            and2tc(
              dir,
              and2tc(
                greaterthanequal2tc(
                  d, constant_int2tc(vt, string2integer(spec->delta_lo))),
                lessthanequal2tc(
                  d, constant_int2tc(vt, string2integer(spec->delta_hi))))));
        }
      }

      // A named variable that matched nothing: the ladder is short and nothing
      // would say so.
      for (const auto &w : named_wanted)
        if (named_seen.count(w) == 0)
        {
          log_error(
            "--path-cov-assert: unit '{}' — REFUSING THE LADDER: \"vars\" names "
            "'{}', which is not a scalar component of this contract's instance "
            "object. Either it does not exist, or it is a mapping / dynamic "
            "array (lowered to a contract-scope global, so no scalar post-state "
            "candidate can be formed). Emitting a SHORTER ladder would answer a "
            "different question than the one the spec asked",
            uid,
            w);
          exit(1);
        }

      log_status(
        "--path-cov-assert: unit '{}' — assumed {} region bound(s) ({} hole(s) "
        "punched) at entry and emitted {} candidate assertion(s) over {} state "
        "variable(s) at path enc={} depth={}'s OWN exit. Every candidate "
        "carries the antecedent `tr != {} || cnt != {}`, so at any other exit "
        "and on any other execution it is vacuous — which is what lets the "
        "whole ladder be judged in ONE run instead of one query per candidate",
        uid,
        bounds_emitted,
        holes_emitted,
        emitted,
        vars_emitted,
        assert_enc,
        assert_depth,
        assert_enc,
        assert_depth);
      total_paths += emitted;
      continue;
    }

    size_t ins_idx = 0;
```

### B.4.9 the units-matched and empty-ladder gates after the unit loop

**MATCH** (unique — 5814–5825):

```cpp
  if (certify_on && certify_units_matched == 0)
  {
    log_error(
      "--path-cov-certify: unit '{}' matched NO enumerated unit, so not one "
      "assume and not one assert was emitted -- the run would otherwise print "
      "VERIFICATION SUCCESSFUL for a query it never asked. Check the name "
      "against the report's `path_function` (mangled form "
      "sol:@C@<contract>@F@<name>#<id>). This is a false certificate, not a "
      "weak result",
      certify_unit);
    exit(1);
  }
```

**REPLACE WITH:**

```cpp
  if (certify_on && certify_units_matched == 0)
  {
    log_error(
      "--path-cov-certify: unit '{}' matched NO enumerated unit, so not one "
      "assume and not one assert was emitted -- the run would otherwise print "
      "VERIFICATION SUCCESSFUL for a query it never asked. Check the name "
      "against the report's `path_function` (mangled form "
      "sol:@C@<contract>@F@<name>#<id>). This is a false certificate, not a "
      "weak result",
      certify_unit);
    exit(1);
  }

  // ---- ROUTE 5 FOR STAGE 3: a ladder that matched no unit ----
  //
  // Same failure as certify's, and it must be tested BEFORE the empty-ladder
  // gate below: "nothing matched" and "everything was refused" both produce
  // zero candidates, and answering the first with the second's message sends
  // the reader to look at the contract's state variables when the real problem
  // is a typo in the unit name.
  if (assert_on && assert_units_matched == 0)
  {
    log_error(
      "--path-cov-assert: unit '{}' matched NO enumerated unit, so not one "
      "assumption and not one candidate assertion was emitted -- the run would "
      "otherwise print VERIFICATION SUCCESSFUL for a ladder it never built. "
      "Check the name against the enumeration run's `path_function` (mangled "
      "form sol:@C@<contract>@F@<name>#<id>)",
      assert_unit);
    exit(1);
  }
  // A spec identifying ONE path may not be answered by two units. Ambiguity
  // here is not a smaller answer: the two units have different bodies, so the
  // two ladders are different claims printed under one heading.
  if (assert_on && assert_units_matched > 1)
  {
    log_error(
      "--path-cov-assert: unit '{}' matched {} enumerated units. A post-state "
      "ladder identifies ONE path of ONE unit; two matches would print two "
      "different sets of claims under one heading. Name the unit in its full "
      "mangled form (sol:@C@<contract>@F@<name>#<id>)",
      assert_unit,
      assert_units_matched);
    exit(1);
  }
  // ---- N1, entry condition (b): every eligible variable was REFUSED ----
  //
  // The second of N1's two entry conditions; (a) is the empty `vars` array,
  // refused at the parse. They share one symptom and need two messages, because
  // closing only one leaves a run whose output is identical to a fixed one.
  if (assert_on && path_cov_assert_candidates.empty())
  {
    std::string refused;
    for (const auto &[cn, why] : path_cov_refused_coords)
      refused += (refused.empty() ? "" : "; ") + cn;
    log_error(
      "--path-cov-assert: unit '{}' — REFUSING THE LADDER: NOT ONE candidate "
      "assertion could be formed. Every state variable of this contract was "
      "refused{}{}. Zero assertions means nothing is checked, and the run would "
      "print VERIFICATION SUCCESSFUL with exit 0 — the same output a fully "
      "successful ladder produces. A contract whose state is entirely mappings "
      "or dynamic arrays is the common case: those are lowered to "
      "contract-scope globals, not components of the contract object, and no "
      "scalar post-state candidate exists for them",
      assert_unit,
      refused.empty() ? "" : ": ",
      refused);
    exit(1);
  }
```

### B.4.10 the reporter (new; after `report_outer_boxes()`)

**MATCH** (unique — 1097–1104, the closing banner of `report_outer_boxes` and the head of `audit_entry_liveness`):

```cpp
  log_status(
    "--path-cov-outer-box: these regions are CANDIDATES, not certificates. The "
    "subtraction is sound only if path enumeration is complete for this unit; "
    "run --path-cov-certify on each region to confirm it, which is the same "
    "confirmation the method already prescribes when any path is undecided");
}

void goto_coveraget::audit_entry_liveness(const std::string &focus_function)
```

**REPLACE WITH:**

```cpp
  log_status(
    "--path-cov-outer-box: these regions are CANDIDATES, not certificates. The "
    "subtraction is sound only if path enumeration is complete for this unit; "
    "run --path-cov-certify on each region to confirm it, which is the same "
    "confirmation the method already prescribes when any path is undecided");
}

void goto_coveraget::report_path_cov_assertions()
{
  if (!path_cov_assert_mode)
    return;

  // ---- Refused variables FIRST, before any verdict ----
  //
  // A refused variable emits no candidate, so it appears in no row below — and
  // an absence reads as "not asked about" while the truth is "asked about and
  // refused". In THIS mode the misreading is worse than in the outer box: a
  // state variable with no row reads as one that needed no assertion, i.e. as
  // one that does not change.
  if (!path_cov_refused_coords.empty())
  {
    std::string refused;
    for (const auto &[cn, why] : path_cov_refused_coords)
      refused += (refused.empty() ? "" : "; ") + cn + " (" + why + ")";
    log_warning(
      "--path-cov-assert: {} state variable(s) carry NO candidate and appear in "
      "NO row below: {}. Their absence is a REFUSAL, not a measurement — read "
      "as \"unchanged\" it would be a claim about a variable nothing was "
      "asserted on",
      path_cov_refused_coords.size(),
      refused);
  }

  // ---- The banner that must not be omitted ----
  log_status(
    "--path-cov-assert: the run's VERIFICATION SUCCESSFUL / FAILED line is NOT "
    "the result of this mode. A REFUTED candidate is the ladder WORKING: it "
    "means there is an input in the region walking this path whose post-state "
    "violates the candidate, and that input is the counterexample. The result "
    "of this run is the per-candidate table below");

  size_t holds = 0, refuted = 0, nv_unknown = 0, nv_unreached = 0;
  {
    // path_ce is read under the same lock, matching audit_certify_witness.
    std::lock_guard lock(claim_outcome_mutex);
    // Emission order, never completion order: under --parallel-solving the
    // claims finish in an arbitrary order, and a table whose ROW ORDER depended
    // on that would make every pinned line flaky for a reason unrelated to what
    // it pins.
    for (const auto &c : path_cov_assert_candidates)
    {
      const std::string sig = c.key.first + "\t" + c.key.second;
      auto it = claim_outcome.find(sig);
      std::string verdict;
      // The third state is EXPLICIT and has two named causes, mirroring the
      // not-solved-this-run / solver-unknown split. Collapsing them would hide
      // the difference between "the solver could not answer" and "the claim
      // never reached the solver", which are opposite kinds of problem.
      if (it == claim_outcome.end())
      {
        ++nv_unreached;
        verdict = "NO VERDICT (never reached the solver)";
      }
      else if (it->second == 'P')
      {
        // Never "proven". path_cov_can_prove_unreachable() returns false for
        // every coverage configuration, so this is bounded-holds: true for
        // every input of the region AT THIS EXPLORATION.
        ++holds;
        verdict = "HOLDS";
      }
      else if (it->second == 'F')
      {
        ++refuted;
        verdict = "REFUTED";
      }
      else
      {
        ++nv_unknown;
        verdict = "NO VERDICT (solver unknown)";
      }

      // The witness is REPORTED, never demanded. audit_certify_witness makes a
      // witness-less refutation a hard failure, and that rule does not transfer
      // here: there a refutation is a defect-shaped event whose witness shrinks
      // the box, while here a refutation is an expected, normal outcome and
      // there is no box to shrink. Aborting on it would be the audit accusing
      // the tool of a defect it does not have — the same false positive both
      // existing audits produced on their first real run.
      std::string witness;
      if (it != claim_outcome.end() && it->second == 'F')
      {
        auto ce = path_ce.find(sig);
        if (ce != path_ce.end() && !ce->second.inputs.empty())
        {
          std::string vals;
          for (const auto &[n, v] : ce->second.inputs)
            vals += (vals.empty() ? "" : ", ") + n + "=" + v;
          witness = "  [witness: " + vals + "]";
        }
        else
          witness =
            "  [no witness input recorded — pass --cov-report-json if the "
            "refuting input is wanted; the verdict itself is unaffected]";
      }
      log_status(
        "--path-cov-assert: {}: {}  {}{}", c.var, c.text, verdict, witness);
    }
  }

  // All four counts, every time, zeros included: a category that stops
  // occurring is noticed, a category that silently disappears from the output
  // is not.
  log_status(
    "--path-cov-assert: ladder summary — {} candidate(s): {} HOLDS, {} REFUTED, "
    "{} no verdict (solver unknown), {} no verdict (never reached the solver). "
    "HOLDS is BOUNDED-holds: true for every input of the region under THIS "
    "exploration (tx/unwind bound, post-constructor entry state), never "
    "\"proven\"",
    path_cov_assert_candidates.size(),
    holds,
    refuted,
    nv_unknown,
    nv_unreached);
}

void goto_coveraget::audit_entry_liveness(const std::string &focus_function)
```

## B.5 `src/esbmc/bmc.cpp`

**MATCH** (unique — 1158–1170):

```cpp
    else if (goto_coveraget::path_cov_certify_mode)
    {
      goto_coveraget::audit_certify_witness(
        options.get_bool_option("cov-report-json"));
      log_status(
        "--path-cov-certify: no [Coverage] block is printed in certification "
        "mode — the claims here are `assume(box); assert(tr == pi)`, so a "
        "CERTIFIED box makes them hold and would be counted as uncovered. The "
        "result of this run is the VERIFICATION SUCCESSFUL / FAILED verdict "
        "below, and on FAILED the counterexample input inside the box");
    }
    else
    {
```

**REPLACE WITH:**

```cpp
    else if (goto_coveraget::path_cov_certify_mode)
    {
      goto_coveraget::audit_certify_witness(
        options.get_bool_option("cov-report-json"));
      log_status(
        "--path-cov-certify: no [Coverage] block is printed in certification "
        "mode — the claims here are `assume(box); assert(tr == pi)`, so a "
        "CERTIFIED box makes them hold and would be counted as uncovered. The "
        "result of this run is the VERIFICATION SUCCESSFUL / FAILED verdict "
        "below, and on FAILED the counterexample input inside the box");
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
```

---

# C. Where each refusal is enforced

### The plan's five structural refusals (shared with certify — ONE implementation)

| # | refusal | enforced at |
|---|---|---|
| 1 | **empty box** (`lo > hi`) | `path_cov_structural_refusal()` (B.4.2), first branch; called by stage 3 in B.4.8 under *"Routes 1-3"* and by certify in B.4.4 |
| 2 | **coordinate bounded twice** | `path_cov_structural_refusal()` second branch, via the mutated `seen_names` set; same two call sites |
| 3 | **punched-empty box** | `path_cov_structural_refusal()` third branch (distinct holes inside `[lo,hi]`); same two call sites |
| 4 | **out-of-type bound** | `path_cov_out_of_type_refusal()` (B.4.2) for region bounds, called in B.4.8 right after `coord_expressible`; **extended** to the ladder constants by the `require_fits` lambda in B.4.8 (`abs_lo`, `abs_hi`, `delta_lo`, `delta_hi`); certify call site rewritten in B.4.5 |
| 5 | **unit matched nothing** | `assert_units_matched == 0` gate in B.4.9, tested *before* the empty-ladder gate |

Also carried over: an **unexpressible REGION coordinate refuses the whole ladder** (B.4.8, `if (!why.empty())` after `resolve_coord`/`coord_expressible`) — the certify disposition, not the outer box's.

### The five new refusals

| # | refusal | enforced at |
|---|---|---|
| **N1** entry cond. (a) — `vars` present but names nothing | two places, two messages: empty `vars` array → B.4.6, at the parse (`assert_vars_present && assert_vars.empty()`); `vars` naming a non-component → B.4.8, the `named_wanted` / `named_seen` loop at the end of the branch |
| **N1** entry cond. (b) — every eligible variable refused | B.4.9, `path_cov_assert_candidates.empty()` gate, placed *after* the units-matched gate so the two never borrow each other's message |
| **N2** — `enc` absent for this unit | B.4.8, `if (!found)` after the `to_insert` scan — **fatal**, not the outer box's warning |
| **N3** — `depth` disagrees | B.4.8, the `path_decision_depth.find({comment, loc})` lookup immediately after N2 |
| **N4** — named obstacle | B.4.8, the very first gate in the branch (`unit_has_lost_decision \|\| unit_calls_gated_unit`), before anything is inserted |
| **N5** — revert / undetermined exit | B.4.8, the block after N3, reading `std::get<3>(to_insert[exit_idx])`, `rollback_exits`, `undetermined_exits` — the **branch-local** sets, never `goto_coveraget::revert_paths` (which is empty here). custom-error revert → `exit(1)`; undetermined → `exit(1)`; rollback → emitted with a `log_warning` label |

Two more that are enforced and are worth naming because they are not in the plan's list:

- **condition (A), wrong contract object** — `path_cov_contract_object()` (B.4.2) resolves by exact id; the `obj == nullptr` gate in B.4.8 refuses rather than falling back.
- **duplicate claim key** — `emit_rung`'s `all_claims.insert(...).second` check in B.4.8 (`abort()`), because a `std::set` collision drops one candidate silently.

**Not enforced, stated explicitly:** condition **(B)** (`new`-created instances — the same unit body running with `this` pointing at a heap instance while the exit read sees the singleton). The patch does **not** detect it. `should_treat_as_new` is in `solidity_convert.h`, is not reachable from `goto_coverage.cpp`, and I did not read it (see UNVERIFIED). Under a single `--contract` target without `--bound` the appendix says the singleton is the only instance, which is the configuration all the fixtures use; outside it this mode is unguarded.

---

# D. Regression fixtures

All under `regression/esbmc-solidity/`. **A new directory is only picked up by `SUBDIRLIST` at CMake *configure* time** (`regression/CMakeLists.txt`, `add_esbmc_regression`) — re-run cmake, `ctest -R` alone will not see them. The harness matches `stdout + stderr` with `re.MULTILINE`, every regex must match, and it strips `--timeout` / `--memlimit` **together with their value** (`testing_tool.py:137`, `generate_run_argument_list`).

---

## 1. `solidity_path_cov_assert_r1_pair_unchanged` / `..._written`

Both directories share this `contract.sol`:

```solidity
// STAGE 3 — the MUST-FLIP pair, and the only self-testing check this mode has.
//
// One scalar state variable, two paths, differing in exactly one thing: whether
// the path WRITES it.
//   enc=2, depth=1  -> the `a > 10` branch, which writes `bal`
//   enc=3, depth=1  -> the fall-through, which does not
// (the enc<->branch mapping is the one recorded for this identical `if (a > N)
// { return 1; } return 0;` shape in solidity_path_cov_certify_box_inside)
//
// The two specs differ ONLY in `enc` and in the region on `a`. Expected:
//   _written    eq REFUTED, ne HOLDS
//   _unchanged  eq HOLDS,   ne REFUTED
//
// PASSING EITHER HALF ALONE IS NOT EVIDENCE OF ANYTHING. The property under
// test is that the two verdicts are consistently OPPOSITE, and that is what
// makes this pair a detector rather than a smoke test:
//
//   * if no assert is emitted at all, N1's zero-candidate gate fires and BOTH
//     halves exit non-zero;
//   * if the antecedent is broken open (a wrong enc, or the N3 depth gate
//     removed), `tr != enc || cnt != depth` is true on every execution, every
//     candidate holds vacuously, and the pair comes out ALL GREEN;
//   * if the exit read is bound to the WRONG contract object — the substring
//     hazard `path_cov_contract_object` exists to close — the unit's writes
//     land somewhere this mode never looks, `post == pre` holds on the writing
//     path too, and the pair again comes out ALL GREEN.
//
// The last one is the reason this pair is the first fixture: it is the only
// place in the whole mode where reading the wrong object produces a visible
// symptom rather than a plausible report.
//
// `payable`, so no ABI value gate is synthesised and msg.value needs no bound.
// `bal` is not public, so no getter unit is generated to confuse the unit name.
// The region pins state.bal into [0, 100] so `bal + 1` cannot wrap: the sign
// rungs are then decided by the arithmetic and not by the entry state.
pragma solidity ^0.8.0;

contract St {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 1;
            return 1;
        }
        return 0;
    }
}
```

`solidity_path_cov_assert_r1_pair_written/spec.json`:

```json
{ "unit": "f", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" },
              { "name": "state.bal", "lo": "0", "hi": "100" } ] }
```

`solidity_path_cov_assert_r1_pair_written/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract St --path-cov-assert spec.json --solidity-max-tx 1
^--path-cov-assert: POST-STATE ASSERTION LADDER for unit 'f' path enc=2 depth=1 over 2 region bound\(s\)
^--path-cov-assert: bal: post == pre  REFUTED
^--path-cov-assert: bal: post != pre  HOLDS
^--path-cov-assert: ladder summary — 6 candidate\(s\): 3 HOLDS, 3 REFUTED, 0 no verdict \(solver unknown\), 0 no verdict \(never reached the solver\)
```

`solidity_path_cov_assert_r1_pair_unchanged/spec.json`:

```json
{ "unit": "f", "enc": 3, "depth": 1,
  "region": [ { "name": "a", "lo": "0", "hi": "10" },
              { "name": "state.bal", "lo": "0", "hi": "100" } ] }
```

`solidity_path_cov_assert_r1_pair_unchanged/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract St --path-cov-assert spec.json --solidity-max-tx 1
^--path-cov-assert: POST-STATE ASSERTION LADDER for unit 'f' path enc=3 depth=1 over 2 region bound\(s\)
^--path-cov-assert: bal: post == pre  HOLDS
^--path-cov-assert: bal: post != pre  REFUTED
^--path-cov-assert: ladder summary — 6 candidate\(s\): 4 HOLDS, 2 REFUTED, 0 no verdict \(solver unknown\), 0 no verdict \(never reached the solver\)
```

*(on the non-writing path `post == pre`, so eq/ge/le HOLD and ne/gt/lt are REFUTED → 4/2.)*

---

## 2. `solidity_path_cov_assert_sign_ladder`

`contract.sol`:

```solidity
// STAGE 3 — the whole ladder in ONE run, and the batch property that says so.
//
// A path that STRICTLY INCREMENTS pins all six sign rungs at once:
//   eq REFUTED   ne HOLDS
//   ge HOLDS     gt HOLDS
//   le REFUTED   lt REFUTED
// with a single fixed assumption and six varying assertions.
//
// WHAT THIS PINS THAT THE PAIR DOES NOT: the `ladder summary — 6 candidate(s)`
// line. The batch is the whole economic argument of this mode — one run per
// path rather than one run per candidate — and it is invisible in the verdicts
// themselves. An implementation that degenerated to one claim per run would
// still print six correct verdicts across six runs; the count line is what
// notices.
//
// The region pins state.bal into [0, 100] so `bal + 1` cannot wrap. Without it
// `ge` and `gt` would be decided by whether the entry state can reach 2^256-1,
// which is a fact about the harness and not about this path.
pragma solidity ^0.8.0;

contract Inc {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 1;
            return 1;
        }
        return 0;
    }
}
```

`spec.json`:

```json
{ "unit": "f", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" },
              { "name": "state.bal", "lo": "0", "hi": "100" } ] }
```

`test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract Inc --path-cov-assert spec.json --solidity-max-tx 1
^--path-cov-assert: bal: post == pre  REFUTED
^--path-cov-assert: bal: post != pre  HOLDS
^--path-cov-assert: bal: post >= pre  HOLDS
^--path-cov-assert: bal: post <= pre  REFUTED
^--path-cov-assert: bal: post > pre  HOLDS
^--path-cov-assert: bal: post < pre  REFUTED
^--path-cov-assert: ladder summary — 6 candidate\(s\): 3 HOLDS, 3 REFUTED, 0 no verdict \(solver unknown\), 0 no verdict \(never reached the solver\)
```

---

## 3. `solidity_path_cov_assert_delta_fits` / `..._tight` / `..._wrapped`

All three share this `contract.sol`:

```solidity
// STAGE 3 — the DELTA rung and the unsigned-wrap guard behind it.
//
// Two paths over one scalar:
//   enc=2, depth=1  -> `bal + 7`   (increases by exactly 7)
//   enc=3, depth=1  -> `bal - 7`   (decreases by exactly 7)
//
// Three specs, one contract:
//   _fits     enc=2, inc, [1, 10]   -> HOLDS (the delta really is 7)
//   _tight    enc=2, inc, [1, 6]    -> REFUTED (the window excludes 7)
//   _wrapped  enc=3, inc, [0, 2^256-1] -> MUST BE REFUTED
//
// _wrapped is the one that matters and it is a fault-injection control that
// needs no injection. Candidate variables are UNSIGNED (coord_expressible
// refuses signed outright), so on the decreasing path `post - pre` wraps to
// 2^256 - 7 — which is inside `[0, 2^256-1]`. A naive
// `lo <= post - pre <= hi` therefore HOLDS there, and holds for the most
// natural spec a driver writes first ("any increase"). The `post >= pre`
// conjunct is the only thing that refuses it: delete that conjunct and
// _wrapped flips green while _fits and _tight stay exactly as they were.
//
// The region pins state.bal into [7, 100] on the decreasing path so that
// `post >= pre` is false for EVERY admitted input — the refutation is then
// about the direction and not about which entry value the solver happened to
// pick.
pragma solidity ^0.8.0;

contract Delta {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 7;
            return 1;
        }
        bal = bal - 7;
        return 0;
    }
}
```

`solidity_path_cov_assert_delta_fits/spec.json`:

```json
{ "unit": "f", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" },
              { "name": "state.bal", "lo": "0", "hi": "100" } ],
  "vars": [ { "name": "bal",
              "delta_dir": "inc", "delta_lo": "1", "delta_hi": "10" } ] }
```

`solidity_path_cov_assert_delta_fits/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract Delta --path-cov-assert spec.json --solidity-max-tx 1
^--path-cov-assert: bal: post - pre in \[1, 10\] with post >= pre  HOLDS
^--path-cov-assert: ladder summary — 7 candidate\(s\): 4 HOLDS, 3 REFUTED, 0 no verdict \(solver unknown\), 0 no verdict \(never reached the solver\)
```

`solidity_path_cov_assert_delta_tight/spec.json`:

```json
{ "unit": "f", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" },
              { "name": "state.bal", "lo": "0", "hi": "100" } ],
  "vars": [ { "name": "bal",
              "delta_dir": "inc", "delta_lo": "1", "delta_hi": "6" } ] }
```

`solidity_path_cov_assert_delta_tight/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract Delta --path-cov-assert spec.json --solidity-max-tx 1
^--path-cov-assert: bal: post - pre in \[1, 6\] with post >= pre  REFUTED
^--path-cov-assert: ladder summary — 7 candidate\(s\): 3 HOLDS, 4 REFUTED, 0 no verdict \(solver unknown\), 0 no verdict \(never reached the solver\)
```

`solidity_path_cov_assert_delta_wrapped/spec.json`:

```json
{ "unit": "f", "enc": 3, "depth": 1,
  "region": [ { "name": "a", "lo": "0", "hi": "10" },
              { "name": "state.bal", "lo": "7", "hi": "100" } ],
  "vars": [ { "name": "bal",
              "delta_dir": "inc",
              "delta_lo": "0",
              "delta_hi": "115792089237316195423570985008687907853269984665640564039457584007913129639935" } ] }
```

`solidity_path_cov_assert_delta_wrapped/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract Delta --path-cov-assert spec.json --solidity-max-tx 1
^--path-cov-assert: bal: post - pre in \[0, 115792089237316195423570985008687907853269984665640564039457584007913129639935\] with post >= pre  REFUTED
^--path-cov-assert: ladder summary — 7 candidate\(s\): 3 HOLDS, 4 REFUTED, 0 no verdict \(solver unknown\), 0 no verdict \(never reached the solver\)
```

---

## 4. `solidity_path_cov_assert_refuses_mapping`

`contract.sol`:

```solidity
// STAGE 3 — a mapping is REFUSED BY NAME, not dropped.
//
// `total` is a component of the contract instance object and gets the full
// six-rung ladder. `balances` is NOT a component at all: the frontend lowers a
// mapping to a contract-scope global `sol:@C@MapC@balances#N`, so iterating the
// object's components would leave it out of the report entirely.
//
// THAT OMISSION IS NOT A SMALLER ANSWER, IT IS A WRONG ONE. In this mode a
// state variable with no row reads as one that needed no assertion — i.e. as
// one that does not change — and this path writes it. Same reason
// path_ce_t::state_written_unrendered exists: "omitting them entirely would let
// a consumer infer 'unchanged', which is a silent wrong conclusion."
//
// So the test asserts the PRESENCE of the refusal line, not merely the
// candidate count. Delete the second scan and the count line still reads
// `6 candidate(s)` and looks perfectly correct; only the refusal line goes.
pragma solidity ^0.8.0;

contract MapC {
    mapping(address => uint256) balances;
    uint256 total;

    function dep(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            balances[msg.sender] = balances[msg.sender] + a;
            total = total + a;
            return 1;
        }
        return 0;
    }
}
```

`spec.json`:

```json
{ "unit": "dep", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" },
              { "name": "state.total", "lo": "0", "hi": "100" } ] }
```

`test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract MapC --path-cov-assert spec.json --solidity-max-tx 1
^WARNING: --path-cov-assert: 1 state variable\(s\) carry NO candidate and appear in NO row below: balances \(a mapping or dynamic array
^--path-cov-assert: total: post == pre  REFUTED
^--path-cov-assert: total: post != pre  HOLDS
^--path-cov-assert: ladder summary — 6 candidate\(s\):
```

---

## 5. `solidity_path_cov_assert_zero_candidates_refused` and `..._empty_vars_refused`

`solidity_path_cov_assert_zero_candidates_refused/contract.sol`:

```solidity
// STAGE 3 — N1, entry condition (b): EVERY state variable was refused.
//
// The only state this contract has is a mapping, which is a contract-scope
// global and not a component of the contract object — so not one scalar
// post-state candidate can be formed. Zero assertions are emitted, nothing is
// checked, and the run would print VERIFICATION SUCCESSFUL with exit 0: the
// SAME OUTPUT a completely successful ladder produces.
//
// That is route 5's shape reached by a different door, and it is why N1 has two
// gates with two messages instead of one. Its twin, `..._empty_vars_refused`,
// closes the OTHER entry condition (a spec whose `vars` array is empty) on a
// contract that has a perfectly good scalar. Closing only one of the two leaves
// a run whose output is indistinguishable from a fix.
//
// The last two regexes are how "no verdict line is printed" has to be written:
// the runner has no negative patterns, so every regex must MATCH, and the
// positive form of the negative is an anchored lookahead over the whole output.
pragma solidity ^0.8.0;

contract OnlyMap {
    mapping(address => uint256) balances;

    function dep(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            balances[msg.sender] = a;
            return 1;
        }
        return 0;
    }
}
```

`solidity_path_cov_assert_zero_candidates_refused/spec.json`:

```json
{ "unit": "dep", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" } ] }
```

`solidity_path_cov_assert_zero_candidates_refused/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract OnlyMap --path-cov-assert spec.json --solidity-max-tx 1
^ERROR: --path-cov-assert: unit 'dep' — REFUSING THE LADDER: NOT ONE candidate assertion could be formed
Zero assertions means nothing is checked
\A(?!(.|\n)*^VERIFICATION SUCCESSFUL$)
\A(?!(.|\n)*^VERIFICATION FAILED$)
```

`solidity_path_cov_assert_empty_vars_refused/contract.sol` — same text as fixture 1's `St` contract (copy it verbatim), with this header instead:

```solidity
// STAGE 3 — N1, entry condition (a): `vars` is present and names NOTHING.
//
// The contract has a perfectly usable scalar state variable, so nothing about
// the CONTRACT explains an empty ladder. The spec does: an empty `vars` array
// is a whitelist that admits nothing, so zero assertions are emitted and the
// run would print VERIFICATION SUCCESSFUL with exit 0.
//
// Its twin `..._zero_candidates_refused` closes the other entry condition. The
// two are separated on purpose: they produce ONE symptom and need TWO messages,
// and a reader sent to look at the contract's state variables when the problem
// is the spec loses the afternoon.
pragma solidity ^0.8.0;

contract St {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 1;
            return 1;
        }
        return 0;
    }
}
```

`solidity_path_cov_assert_empty_vars_refused/spec.json`:

```json
{ "unit": "f", "enc": 2, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" } ],
  "vars": [] }
```

`solidity_path_cov_assert_empty_vars_refused/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract St --path-cov-assert spec.json --solidity-max-tx 1
^ERROR: --path-cov-assert: the spec contains an EMPTY "vars" array
\A(?!(.|\n)*^VERIFICATION SUCCESSFUL$)
\A(?!(.|\n)*^VERIFICATION FAILED$)
```

---

## 6. `solidity_path_cov_assert_depth_mismatch_refused` and `..._enc_absent_refused`

`solidity_path_cov_assert_depth_mismatch_refused/contract.sol`:

```solidity
// STAGE 3 — N3: a `depth` that disagrees with the enumeration is REFUSED.
//
// This is the most dangerous of the new routes because today it produces NO
// DIAGNOSTIC AT ALL. The antecedent every candidate carries is
// `tr != enc || cnt != depth`. A wrong depth makes it TRUE on every single
// execution, so every candidate holds VACUOUSLY — and the report then reads as
// a fully successful certification of the whole ladder, character for character
// the output a correct run produces when everything really does hold.
//
// FAULT INJECTION IS REQUIRED HERE, and that is the point of writing it down:
// unlike the R1 pair, this detector is ONE-DIRECTIONAL — it never fires on a
// correct run, so it cannot testify for itself. Remove the path_decision_depth
// lookup and this fixture prints an all-HOLDS ladder and exits 0.
//
// `f` has one decision, so its real depth is 1; the spec says 2. Its twin
// `..._enc_absent_refused` covers N2 (an `enc` that is not among this unit's
// enumerated paths), which has the same consequence — the ladder emitted
// nowhere — reached from the other side.
pragma solidity ^0.8.0;

contract St {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 1;
            return 1;
        }
        return 0;
    }
}
```

`solidity_path_cov_assert_depth_mismatch_refused/spec.json`:

```json
{ "unit": "f", "enc": 2, "depth": 2,
  "region": [ { "name": "a", "lo": "11", "hi": "100" } ] }
```

`solidity_path_cov_assert_depth_mismatch_refused/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract St --path-cov-assert spec.json --solidity-max-tx 1
^ERROR: --path-cov-assert: unit '[^']*' — REFUSING THE LADDER: the spec says path enc=2 has depth=2, the enumeration says 1
every candidate would hold vacuously
\A(?!(.|\n)*^VERIFICATION SUCCESSFUL$)
\A(?!(.|\n)*^VERIFICATION FAILED$)
```

`solidity_path_cov_assert_enc_absent_refused/contract.sol` — same contract as above with this header:

```solidity
// STAGE 3 — N2: an `enc` that is not among this unit's enumerated paths.
//
// The outer-box branch only WARNS on this, and correctly so: there a missing
// path costs one measurement out of many and the rest of the batch is still
// worth having. Here it means the ladder was emitted NOWHERE — nothing is
// checked and the run prints VERIFICATION SUCCESSFUL with exit 0, which is
// route 5's failure arrived at through the path id instead of the unit name.
//
// Fault injection required (one-directional detector): downgrade the refusal to
// a warning and this fixture goes green with an empty ladder.
pragma solidity ^0.8.0;

contract St {
    uint256 bal;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 1;
            return 1;
        }
        return 0;
    }
}
```

`solidity_path_cov_assert_enc_absent_refused/spec.json`:

```json
{ "unit": "f", "enc": 99, "depth": 1,
  "region": [ { "name": "a", "lo": "11", "hi": "100" } ] }
```

`solidity_path_cov_assert_enc_absent_refused/test.desc`:

```
CORE
contract.sol
--solidity-path-coverage --contract St --path-cov-assert spec.json --solidity-max-tx 1
^ERROR: --path-cov-assert: unit '[^']*' — REFUSING THE LADDER: path enc=99 is not among this unit's
\A(?!(.|\n)*^VERIFICATION SUCCESSFUL$)
\A(?!(.|\n)*^VERIFICATION FAILED$)
```

---

# E. UNVERIFIED

Things I could not settle by reading, each with the file that would settle it. I have not guessed around any of them.

1. **The `enc` ↔ branch mapping in every fixture.** I took `enc=2 → the (a > N) then-arm`, `enc=3 → fall-through`, `depth=1` from the comment inside `regression/esbmc-solidity/solidity_path_cov_certify_box_inside/contract.sol:8-10`, for the byte-identical source shape. I could not run ESBMC to confirm it survives adding a state write to the then-arm. **Settle by:** running the contract once with `--solidity-path-coverage --contract St --solidity-max-tx 1` (no assert flag) and reading the `f:path:<enc>` claim lines. If the mapping is reversed, swap `enc` (and the region on `a`) between the two halves of each pair — nothing else changes.

2. **Whether checked arithmetic adds a decision.** `bal = bal + 1` / `bal - 7` in Solidity ≥0.8 is checked. `notes/path-coverage-invocation-contract.md` §5 records that no overflow/bounds flag is ever passed and that `Implementation_plan.md` §3.3's "C1" (lowering checked arithmetic into two-exit branches) is *not* listed as implemented — so I assumed depth stays 1. If it does not, N3 fires on every fixture with the enumeration's real depth in the message, which tells you the new depth directly. **Settle by:** the same trial run as (1) — the `depth=` in the enumeration output.

3. **Whether a mapping produces exactly one `sol:@C@<C>@<name>#N` symbol, and whether ordinary scalars produce one too.** I implemented the second scan with a components-exclusion set precisely because I could not confirm the second half. If scalars do *not* get contract-scope symbols the exclusion is a harmless no-op; if some other contract-scope symbol shape exists that survives the `$`/`_ESBMC`/`@F@` filters, fixture 4's refusal line will name it. **Settle by:** `src/solidity-frontend/solidity_convert.cpp` (state-variable declaration lowering) — the site that decides component vs contract-scope global.

4. **Condition (B), `new`-created instances.** Not enforced, and I did not read `should_treat_as_new` (`src/solidity-frontend/solidity_convert.h:1610-1619` per the plan's appendix). Under a single `--contract` without `--bound` the appendix says the singleton is the only instance; outside that the exit read can look at the singleton while `this` points at a heap object, and this patch does not detect it. **Settle by:** `src/solidity-frontend/solidity_convert.h` + `solidity_convert_call.cpp`.

5. **`path_cov_can_prove_unreachable()` at `bmc.cpp:722`.** Cited by the plan for why `'P'` must be reported as bounded-holds and never "proven". I did not read it; the reporter's wording ("HOLDS is BOUNDED-holds … never proven") is written to be correct either way, and the reporter does not call it. **Settle by:** `src/esbmc/bmc.cpp` around line 722.

6. **The CE-harvest scope derivation (`bmc.cpp:1524-1529`, `1556-1557`, `3106-3122`).** The plan's §4.2 claim that a `#<rung>_<var>` suffix survives `strtoull` and the `@F@` scope split is quoted from the plan, not read by me. The comment shape I emit is the same *prefix* shape both existing stage-2 branches already ship, so if the suffix broke the harvest it would already be broken for outer-box probes — but I did not verify it. **Settle by:** `src/esbmc/bmc.cpp` lines 1500–1560 and 3100–3130. Fixture group 10 in the plan (`_witness_scope`) is the empirical settle; I did not write it, since the parent's list did not require it.

7. **Whether the frontend synthesises a getter unit for a `public` state variable.** I sidestepped it by making every fixture's state variable non-public, so no fixture depends on the answer. **Settle by:** `src/solidity-frontend/solidity_convert_contract.cpp`.
