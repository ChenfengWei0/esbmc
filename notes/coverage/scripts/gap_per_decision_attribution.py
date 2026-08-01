#!/usr/bin/env python3
"""PER-DECISION gap attribution for subgoal 2, EXECUTION_PLAN 3 step 1.3.

WHAT THIS MEASURES AND WHY IT IS NOT `gap_attribution.py`
--------------------------------------------------------
`branch_gate.py` compares COUNTS (`ours >= bar`) because METHODOLOGY 4 defines
the baseline's reach as a per-file COUNT of flat lines, and the locked
`notes/coverage/data/esbmc_<bench>.json` records no per-decision identity.

But the identity survives OUTSIDE that JSON. `collect.py` runs the baseline with
`--coverage-covered-set /tmp/cov_<bench>/union_pair2.json`, and that file records
one `{cond, loc}` per covered edge with `loc` carrying the flat LINE. So for a
benchmark whose baseline was collected in the same sweep that produced the locked
JSON, both sides ARE available as sets of flat lines:

    baseline(file) = lines(union_pair2.json)         & canon(file)
    ours(file)     = lines(F-claim `decisions`)      & canon(file)

`setcmp.py` already prints the two totals. This script prints the per-DECISION
row -- one line per canonical decision, with the enclosing definition and which
side reached it -- and then attributes each MISSED decision to one of the three
buckets EXECUTION_PLAN 3 step 1.3 names.

PROVENANCE WARNING THAT MUST BE READ BEFORE ANY NUMBER HERE IS QUOTED
--------------------------------------------------------------------
The two sides come from two different sweeps:
  * baseline  -- /tmp/cov_<bench>/union_pair2.json, written 2026-07-30 by
                 collect.py (the same run that wrote notes/coverage/data/).
  * ours      -- notes/coverage/pathcov/<bench>/reports/, written 2026-08-01.
They are commensurable in UNIT (both intersected with the same canonical
decision lines of the same flat) but they are not the same run, and the product
side is a later binary. The script prints both mtimes so this cannot be forgotten.

THE THREE BUCKETS, AND WHAT EVIDENCE EACH ONE REQUIRES
------------------------------------------------------
  (1) STATE GUARD -- the path needs state only a prior transaction can
      establish. The producer states this condition in its own report
      (`summary.known_limitation_entry_state`): "paths guarded by state that an
      earlier transaction would have to establish are reported U at this tx
      bound". The observable is therefore a U claim with `u_reason ==
      bounded-holds` in a unit that WAS entered.
  (2) DRIVER NEVER ENTERED THE UNIT -- two distinct observables, and they are
      NOT merged here:
        (2a) claim level: `u_reason == unit-not-entered`.
        (2b) run level:  the owning unit produced NO report at all -- killed by
             the outer timeout, or `skipped: library-has-no-dispatcher`.
      (2b) is the one that fires on this corpus; (2a) is measured and is 0.
  (3) GENUINELY INFEASIBLE -- requires an `I` verdict. `bmc.cpp`'s
      `path_cov_can_prove_unreachable()` is `return false`, so `I` is NEVER
      emitted (EXECUTION_PLAN 2 step 0.6, A3). This bucket is therefore
      STRUCTURALLY UNMEASURABLE on this corpus and the script prints 0 with the
      reason rather than folding its members into (1).

Anything the evidence does not place in one of those three is printed BY NAME in
its own bucket rather than assigned. The named residual buckets are:
  X-nopath   the owning unit was fully witnessed (0 U claims) and no witnessed
             path walks this decision. Candidate for (3), but also the exact
             signature of a call site withdrawn by degradation or the
             call-depth bound (`degraded_call_sites` reaches no reader --
             branch_gate.py records the same gap), so it is NOT called (3).
  X-budget   the owning unit was entered but its undetermined paths carry
             `not-solved-this-run` / `solver-unknown` / `claim-budget-exceeded`
             -- a budget or solver result, not a reach result.
  X-noowner  no enumerated unit syntactically reaches the enclosing body
             (constructor bodies; library `external` functions no unit calls).

WHY OWNERSHIP IS COMPUTED FROM THE AST CALL GRAPH
-------------------------------------------------
A canonical decision inside an `internal` body or a modifier belongs to no unit
by itself; it is covered only through a unit that inlines it. So this script
builds the call graph from the solast (`FunctionCall.expression.referencedDeclaration`
plus each `FunctionDefinition`'s `modifiers[].modifierName.referencedDeclaration`)
and takes the transitive closure from every unit the collector actually ran. A
decision's OWNERS are the units whose closure contains its enclosing definition.
Ownership computed this way is SYNTACTIC: it says a call site exists, never that
the tool expanded it.

It also UNDER-approximates on virtual dispatch: an override called through the
base declaration carries the BASE's `referencedDeclaration`, so the derived body
sits in no closure. Measured instance, visible in the table: EscrowSrc's
`Escrow._validateImmutables` shows `owners = -` although BOTH sides reach it. A
decision with no owner is therefore printed as `X-noowner` and NAMED, never
counted as unreachable.

`entered-caller?` in the table is the column that keeps two different findings
apart, and the reason the (2b) bucket is split when it is printed:
  * a body whose only owning unit was skipped/killed AND which no ENTERED unit
    calls is out of the primary contract's reach by SCOPE -- branch coverage
    instruments it directly, complete-path enumeration has no unit for it;
  * a body an entered unit does call, yet no witnessed path walks it, is a
    reach result inside a unit we did run.

THE CALL GRAPH IS NOT LOAD-BEARING FOR ANY BUCKET, AND THIS IS WHY
------------------------------------------------------------------
MEASURED false-negative rate of the closure above, printed per benchmark as
`graph-missed-but-reached`: on this corpus 7 of 68 canonical decisions are
REACHED by a witnessed path while the closure says no unit reaches their body
(`Escrow._validateImmutables` on both Escrows -- virtual dispatch; farming's
`FarmingPool._update` and `UserAccounting.updateBalances` -- `using L for T`
binding). A classifier resting on the closure would call those unreachable.

So every bucket below is decided by REPORT evidence, and the closure is printed
beside it as context only:
  * `body-expanded` -- the enclosing definition's own name appears in some
    witnessed path's `decisions[].function`. That is the tool saying it walked
    a decision inside that body, so the body WAS expanded.
  * the run state of the unit whose tag the collector recorded.

A HYPOTHESIS THAT WAS TESTED HERE AND FALSIFIED, recorded so it is not re-run
----------------------------------------------------------------------------
"The Escrow gap is the call-depth bound." It is not. Cross-tabulating reach
against shortest call distance from an entered unit (modifier edges free):
farming REACHES decisions at distance 3 (`UserAccounting.farmedPerToken`,
`FarmAccounting.farmedSinceCheckpointScaled`) while EscrowSrc MISSES at
distance 1 (`BaseEscrow.onlyValidSecret`) and 2 (`BaseEscrow._ethTransfer`).
There is no distance threshold that separates the two. The distance column is
still printed, because the falsification is the finding.

A LIMIT THAT BOUNDS EVERY (1) BELOW
-----------------------------------
MEASURED here and printed: U claims carry NO `decisions` array (only F claims
do). So a missed decision cannot be linked to the specific U claim that would
have witnessed it. Bucket (1) is therefore assigned at UNIT granularity -- "this
decision's owning unit has undetermined paths and all of them are
bounded-holds" -- not at decision granularity. Stated so the bucket is not read
as finer than it is.

Reads only; writes nothing; never invokes esbmc.

Usage:  python3 notes/coverage/scripts/gap_per_decision_attribution.py [bench ...]
"""
import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
SCRIPTS = REPO / "notes/coverage/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "notes"))

import ast_decisions                       # noqa: E402
from collect import is_project_own_marker  # noqa: E402

PATHCOV = REPO / "notes/coverage/pathcov"
DATA = REPO / "notes/coverage/data"
LOC_RE = re.compile(r"line\s+(\d+)")

DEFAULT_BENCHES = [
    "cross_chain_swap_EscrowSrc",
    "cross_chain_swap_EscrowDst",
    "farming",
    "aqua_Aqua",
]

# u_reasons that mean "the tx bound / entry state stopped this", per the
# producer's own `known_limitation_entry_state` text.
STATE_REASONS = {"bounded-holds"}
# u_reasons that mean "we ran out of something", not "we could not reach".
BUDGET_REASONS = {"not-solved-this-run", "solver-unknown",
                  "claim-budget-exceeded", "run-died-before-solving"}
ENTRY_REASONS = {"unit-not-entered"}


# --------------------------------------------------------------------------
# AST: definitions, call graph, modifier edges
# --------------------------------------------------------------------------
def ast_model(flat_path):
    """(defs, calls, b2l) for the flat.

    defs:  node_id -> {name, kind, vis, contract, start, end}
    calls: node_id -> set(node_id)   callees + modifiers used
    """
    flat_path = Path(flat_path)
    b2l = ast_decisions.byte_to_line(flat_path.read_bytes())
    solast = flat_path.with_suffix(".sol.solast")
    if not solast.exists():
        solast = Path(str(flat_path) + ".solast")
    ast = ast_decisions.extract_ast_json(solast)

    defs, calls = {}, defaultdict(set)

    def span(node):
        s = node.get("src")
        if not s:
            return None
        start, length, _ = s.split(":")
        start, length = int(start), int(length)
        return (ast_decisions.line_of(b2l, start),
                ast_decisions.line_of(b2l, start + max(length - 1, 0)))

    def collect_calls(node, owner):
        """Every referencedDeclaration reachable under `node` that names a
        definition. Deliberately over-approximates: a reference inside a
        nested definition is re-attributed when that definition is walked."""
        if isinstance(node, list):
            for c in node:
                collect_calls(c, owner)
            return
        if not isinstance(node, dict):
            return
        nt = node.get("nodeType")
        if nt == "FunctionCall":
            exp = node.get("expression") or {}
            rd = exp.get("referencedDeclaration")
            if isinstance(rd, int):
                calls[owner].add(rd)
        elif nt == "ModifierInvocation":
            mn = node.get("modifierName") or {}
            rd = mn.get("referencedDeclaration")
            if isinstance(rd, int):
                calls[owner].add(rd)
        elif nt == "Identifier" and isinstance(
                node.get("referencedDeclaration"), int):
            # `using A for B` / library member access lands here too; harmless,
            # ownership only over-approximates.
            calls[owner].add(node["referencedDeclaration"])
        elif nt == "MemberAccess" and isinstance(
                node.get("referencedDeclaration"), int):
            calls[owner].add(node["referencedDeclaration"])
        for k, v in node.items():
            if k == "nodeType":
                continue
            if isinstance(v, (list, dict)):
                collect_calls(v, owner)

    def walk(node, contract, ckind):
        if isinstance(node, list):
            for c in node:
                walk(c, contract, ckind)
            return
        if not isinstance(node, dict):
            return
        nt = node.get("nodeType")
        if nt == "ContractDefinition":
            contract, ckind = node.get("name"), node.get("contractKind")
        elif nt in ("FunctionDefinition", "ModifierDefinition"):
            sp = span(node)
            nid = node.get("id")
            if sp and isinstance(nid, int):
                if nt == "ModifierDefinition":
                    kind, vis = "modifier", "modifier"
                else:
                    kind = node.get("kind") or "function"
                    vis = node.get("visibility") or "?"
                defs[nid] = {
                    "name": node.get("name") or f"<{kind}>",
                    "kind": kind, "vis": vis, "contract": contract,
                    "contract_kind": ckind, "start": sp[0], "end": sp[1],
                }
                collect_calls(node.get("body"), nid)
                collect_calls(node.get("modifiers"), nid)
        for k, v in node.items():
            if k == "nodeType":
                continue
            if isinstance(v, (list, dict)):
                walk(v, contract, ckind)

    walk(ast, None, None)
    return defs, calls


def enclosing_def(defs, line):
    """Innermost definition id containing `line`, or None."""
    best, best_id = None, None
    for nid, d in defs.items():
        if d["start"] <= line <= d["end"]:
            width = d["end"] - d["start"]
            if best is None or width < best:
                best, best_id = width, nid
    return best_id


def closure(calls, roots):
    seen, stack = set(), list(roots)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(calls.get(n, ()))
    return seen


def distances(defs, calls, roots):
    """Shortest call distance from any root. A ModifierDefinition edge costs 0
    because the compiler splices the modifier into its function; a
    FunctionDefinition edge costs 1."""
    from collections import deque
    dist, dq = {}, deque()
    for r in roots:
        dist[r] = 0
        dq.append(r)
    while dq:
        n = dq.popleft()
        for m in calls.get(n, ()):
            if m not in defs:
                continue
            w = 0 if defs[m]["kind"] == "modifier" else 1
            nd = dist[n] + w
            if nd < dist.get(m, 1 << 30):
                dist[m] = nd
                (dq.appendleft if w == 0 else dq.append)(m)
    return dist


MONO = re.compile(r"__mono_\d+$")


def body_expanded(d, walked_functions):
    """Did some witnessed path walk a decision INSIDE this definition?

    `decisions[].function` names the enclosing body. A modifier appears
    prefixed with its host unit (`cancel_onlyCaller`); a monomorphised callee
    carries a `__mono_N` suffix. Both are normalised. The prefixed form is
    accepted ONLY for modifiers -- accepting it for functions would let
    `_withdraw` match `withdraw` and turn a missed body into an expanded one.
    """
    name = d.get("name")
    if not name:
        return False
    for w in walked_functions:
        w = MONO.sub("", w or "")
        if w == name:
            return True
        if d.get("kind") == "modifier" and w.endswith("_" + name):
            return True
    return False


# --------------------------------------------------------------------------
# The two sides
# --------------------------------------------------------------------------
def baseline_lines(bench):
    p = Path(f"/tmp/cov_{bench}/union_pair2.json")
    if not p.exists():
        return None, None
    d = json.loads(p.read_text())
    out = set()
    for c in d.get("covered", []):
        m = LOC_RE.search(c.get("loc", ""))
        if m:
            out.add(int(m.group(1)))
    return out, p


def product_side(bench):
    """(lines, per_report, index). `lines` excludes the synthetic ABI gate and
    unrecorded steps -- the same two exclusions branch_gate.py applies."""
    idx_p = PATHCOV / bench / "index.json"
    if not idx_p.exists():
        return None, None, None
    idx = json.loads(idx_p.read_text())
    rdir = Path(idx.get("reportsDir", PATHCOV / bench / "reports"))
    lines, per_report = set(), {}
    for p in sorted(rdir.glob("*.json")):
        d = json.loads(p.read_text())
        st, ur, fns = defaultdict(int), defaultdict(int), set()
        n_u_with_dec = 0
        for c in d.get("claims", []):
            st[c.get("status", "?")] += 1
            if c.get("status") == "U":
                ur[str(c.get("u_reason") or "<none>")] += 1
                if c.get("decisions"):
                    n_u_with_dec += 1
            if c.get("status") != "F":
                continue
            for e in c.get("decisions") or []:
                fns.add(e.get("function"))
                if "unrecorded_prefix_enc" in e or e.get("synthetic_abi_gate"):
                    continue
                ln = e.get("line")
                if isinstance(ln, int) and ln > 0:
                    lines.add(ln)
        s = d.get("summary", {})
        # The producer's own aggregate U_reasons is the authority; the per-claim
        # `u_reason` field is absent on this corpus (measured, printed below).
        agg = {k: v for k, v in (s.get("U_reasons") or {}).items() if v}
        per_report[p.stem] = {
            "status": dict(st), "u_reason_claim": dict(ur),
            "u_reason_summary": agg, "partial": d.get("partial"),
            "paths_total": s.get("paths_total"), "covered": s.get("covered"),
            "decision_functions": sorted(x for x in fns if x),
            "u_with_decisions": n_u_with_dec,
            "mtime": p.stat().st_mtime,
        }
    return lines, per_report, idx


def main(argv):
    benches = argv[1:] or DEFAULT_BENCHES
    print("# Per-decision gap attribution (subgoal 2, EXECUTION_PLAN 3 / 1.3)\n")
    print("Unit: canonical decision (METHODOLOGY 2), identified by flat line.")
    print("Baseline side: /tmp/cov_<bench>/union_pair2.json (Pair-2 covered set).")
    print("Product side:  F-claim `decisions`, minus synthetic ABI gate.\n")

    for bench in benches:
        print("\n" + "=" * 78)
        print(f"## `{bench}`\n")

        locked_p = DATA / f"esbmc_{bench}.json"
        if not locked_p.exists():
            print("locked baseline JSON missing")
            continue
        locked = json.loads(locked_p.read_text())
        flat = locked["flatInput"]
        project = locked.get("project")

        base, base_p = baseline_lines(bench)
        ours, per_report, idx = product_side(bench)
        if base is None:
            print("baseline union_pair2.json absent -- cannot do a SET comparison")
            continue
        if ours is None:
            print("product reports absent")
            continue

        def ts(p):
            return datetime.datetime.fromtimestamp(
                Path(p).stat().st_mtime).strftime("%Y-%m-%d %H:%M")

        print(f"provenance: baseline `{base_p}` {ts(base_p)}   "
              f"locked JSON {ts(locked_p)}   "
              f"reports dir {ts(PATHCOV / bench / 'reports')}")
        print(f"            DIFFERENT SWEEPS -- commensurable in unit, not in run\n")

        by_file, blocks = ast_decisions.canonical_decisions(Path(flat))
        own = sorted({m for _s, _e, m in blocks
                      if is_project_own_marker(m, project)})
        canon = {m: by_file.get(m, set()) for m in own if by_file.get(m)}

        defs, calls = ast_model(flat)

        # units the COLLECTOR actually enumerated, from its own index
        runs = {r["tag"]: r for r in idx["runs"]}
        unit_ids, unit_by_tag = {}, {}
        for tag, r in runs.items():
            cname, fname = r.get("contract"), r.get("function")
            cand = [nid for nid, d in defs.items()
                    if d["name"] == fname and d["contract"] == cname
                    and d["kind"] != "modifier"]
            if cand:
                unit_ids[tag] = cand[0]
                unit_by_tag[cand[0]] = tag
        reach = {tag: closure(calls, [nid]) for tag, nid in unit_ids.items()}

        # run-level status per unit tag
        def tag_state(tag):
            r = runs[tag]
            if r.get("skipped"):
                return "SKIPPED:" + r["skipped"]
            if r.get("killedByOuterTimeout"):
                return "KILLED"
            if not r.get("reportPresent"):
                return "NO-REPORT"
            return "REPORT"

        # report lookup by tag
        rep_of = {t: per_report.get(t) for t in runs}

        entered = {t for t in unit_ids if tag_state(t) == "REPORT"}
        dist = distances(defs, calls, [unit_ids[t] for t in entered])
        rev = defaultdict(set)
        for a, bs in calls.items():
            for bb2 in bs:
                if bb2 in defs:
                    rev[bb2].add(a)
        walked = set()
        for t in entered:
            r = rep_of.get(t)
            if r:
                walked.update(r["decision_functions"])

        rows, buckets = [], defaultdict(list)
        gm_reached = 0
        for fname in sorted(canon):
            for ln in sorted(canon[fname]):
                b, o = ln in base, ln in ours
                nid = enclosing_def(defs, ln)
                d = defs.get(nid, {})
                where = (f"{d.get('contract')}.{d.get('name')}"
                         f" [{d.get('vis')}/{d.get('kind')}]" if d else "-")
                owners = sorted(t for t, s in reach.items() if nid in s)
                ent = sorted(t for t in owners if t in entered)
                dd = dist.get(nid)
                exp = body_expanded(d, walked)
                nin = len(rev.get(nid, ()))
                if o and dd is None:
                    gm_reached += 1
                bucket = ""
                if b and not o:
                    bucket = classify(d, owners, ent, exp, nin, tag_state,
                                      rep_of)
                    buckets[bucket].append((fname, ln, where, owners, ent, dd))
                elif not b and not o:
                    bucket = "neither-side"
                    buckets[bucket].append((fname, ln, where, owners, ent, dd))
                elif o and not b:
                    bucket = "ONLY-PRODUCT"
                    buckets[bucket].append((fname, ln, where, owners, ent, dd))
                rows.append((fname, ln, b, o, where, ent, dd, exp, nin, bucket))

        # ---- the per-file, per-decision table -------------------------------
        print("### per-decision table\n")
        print("`dist` = shortest call distance from an ENTERED unit (modifier "
              "edges free); `-` = the syntactic call graph finds no route, "
              "which it also gets WRONG on virtual dispatch and `using L for "
              "T`. `body?` = some witnessed path walked a decision inside "
              "this body.\n")
        print("`in-deg` = how many definitions ANYWHERE in the flat call this "
              "body; 0 means nothing in this entry's compilation unit calls "
              "it at all.\n")
        print("| file | flat line | baseline | ours | enclosing | "
              "entered callers | in-deg | dist | body? | bucket |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for fname, ln, b, o, where, ent, dd, exp, nin, bucket in rows:
            print(f"| {fname} | {ln} | {'Y' if b else '.'} | "
                  f"{'Y' if o else '.'} | `{where}` | {len(ent)} | {nin} | "
                  f"{dd if dd is not None else '-'} | "
                  f"{'Y' if exp else '.'} | {bucket} |")
        print(f"\n`graph-missed-but-reached` = {gm_reached} -- decisions a "
              f"witnessed path DID walk although the syntactic closure finds "
              f"no route. This is the measured false-negative rate of the "
              f"call graph, and it is why no bucket rests on it.")

        # ---- per-file totals ------------------------------------------------
        print("\n### per-file totals\n")
        print("| file | canon | baseline | ours | both | only-baseline "
              "(THE GAP) | only-product |")
        print("|---|---|---|---|---|---|---|")
        tc = tb = to = tboth = tgap = tonly = 0
        for fname in sorted(canon):
            c = canon[fname]
            bb, oo = base & c, ours & c
            tc += len(c); tb += len(bb); to += len(oo)
            tboth += len(bb & oo); tgap += len(bb - oo); tonly += len(oo - bb)
            print(f"| {fname} | {len(c)} | {len(bb)} | {len(oo)} | "
                  f"{len(bb & oo)} | {len(bb - oo)} | {len(oo - bb)} |")
        print(f"| **TOTAL** | {tc} | {tb} | {to} | {tboth} | {tgap} | {tonly} |")

        # ---- BOTH SIDES MUST REPRODUCE THE GATE ----------------------------
        # The baseline column here is rebuilt from union_pair2.json, a file the
        # locked JSON does not contain. If that rebuild disagreed with the
        # locked `esbmc.reached` -- a COUNT the gate is actually judged on --
        # then this table would be a different measurement wearing the gate's
        # name, and every attribution below it would be about the wrong gap.
        # Checked per file, not on the total, because two per-file errors of
        # opposite sign cancel in a total.
        sec = locked.get("per_function") or {}
        pf = {p["file"]: p for p in sec.get("perFile", [])}
        bad = []
        for fname in sorted(canon):
            want = (pf.get(fname, {}).get("esbmc") or {}).get("reached")
            got = len(base & canon[fname])
            if want is not None and want != got:
                bad.append(f"{fname}: locked {want} vs union_pair2 {got}")
        tot_locked = (sec.get("total") or {}).get("esbmcReached")
        if bad:
            print(f"\n**BASELINE REBUILD DISAGREES WITH THE LOCKED JSON** -- "
                  + "; ".join(bad) + ". The set comparison above is not the "
                  "gate's baseline; do not attribute from it.")
        else:
            print(f"\nbaseline rebuild agrees with the locked "
                  f"`per_function.perFile[].esbmc.reached` on all "
                  f"{len(canon)} in-scope file(s) (locked total "
                  f"{tot_locked}, rebuilt {tb}); `ours` total {to} is the same "
                  f"numerator `branch_gate.py` prints.")

        # ---- attribution ----------------------------------------------------
        print("\n### attribution of the gap (only-baseline decisions)\n")
        order = ["(1) state guard",
                 "(2a) unit-not-entered claim",
                 "(2b) driver never entered the unit",
                 "(3) genuinely infeasible",
                 "X-constructor", "X-no-caller-in-flat",
                 "X-body-never-expanded", "X-budget", "X-mixed"]
        print("| bucket | count |")
        print("|---|---|")
        for k in order:
            print(f"| {k} | {len(buckets.get(k, []))} |")
        extra = [k for k in buckets
                 if k not in order and k not in ("neither-side",
                                                 "ONLY-PRODUCT")]
        for k in sorted(extra):
            print(f"| {k} | {len(buckets[k])} |")
        print(f"| (not in the gap) neither side | "
              f"{len(buckets.get('neither-side', []))} |")
        print(f"| (not in the gap) ONLY-PRODUCT | "
              f"{len(buckets.get('ONLY-PRODUCT', []))} |")

        for k in order + sorted(extra) + ["neither-side", "ONLY-PRODUCT"]:
            if not buckets.get(k):
                continue
            print(f"\n#### {k}\n")
            print("| file | line | enclosing | owning unit tags | "
                  "entered ones | dist |")
            print("|---|---|---|---|---|---|")
            for fname, ln, where, owners, ent, dd in buckets[k]:
                ow = ", ".join(owners) or "-"
                if len(ow) > 80:
                    ow = ow[:77] + "..."
                print(f"| {fname} | {ln} | `{where}` | {ow} | "
                      f"{', '.join(ent) or '-'} | "
                      f"{dd if dd is not None else '-'} |")

        # ---- the evidence the buckets rest on -------------------------------
        print("\n### run/report census the buckets are read from\n")
        print("| unit tag | run state | paths | F | U | U reasons "
              "(producer aggregate) |")
        print("|---|---|---|---|---|---|")
        for tag in sorted(runs):
            st = tag_state(tag)
            r = rep_of.get(tag)
            if r is None:
                print(f"| `{tag}` | {st} | - | - | - | - |")
                continue
            ur = ", ".join(f"{k}={v}" for k, v in
                           sorted(r["u_reason_summary"].items())) or "-"
            print(f"| `{tag}` | {st} | {r['paths_total']} | "
                  f"{r['status'].get('F', 0)} | {r['status'].get('U', 0)} | "
                  f"{ur} |")

        n_u = sum(r["status"].get("U", 0) for r in per_report.values() if r)
        n_u_dec = sum(r["u_with_decisions"] for r in per_report.values() if r)
        n_ur = sum(1 for r in per_report.values() if r and r["u_reason_claim"])
        # COUNTED, NOT ASSERTED. "U claims carry no decisions" is the reason
        # bucket (1) can only be assigned at unit granularity; asserting it
        # from the producer's source would make this table silently wrong the
        # day the producer starts publishing them.
        print(f"\nU claims total: {n_u}; of those, carrying a `decisions` "
              f"array: {n_u_dec}. While that is 0 no missed decision can be "
              f"linked to the U claim that would have witnessed it, so bucket "
              f"(1) is assignable only at UNIT granularity. ({n_ur} report(s) "
              f"carry a per-claim `u_reason` field; the rest are read from the "
              f"producer's `summary.U_reasons` aggregate.)")
        print("`I_proven_unreachable` across all reports: "
              f"{sum(json.loads(p.read_text()).get('summary', {}).get('I_proven_unreachable', 0) for p in sorted((PATHCOV / bench / 'reports').glob('*.json')))}"
              " -- bucket (3) cannot be populated while "
              "`path_cov_can_prove_unreachable()` is `return false`.")
    return 0


def classify(d, owners, ent, expanded, in_degree, tag_state, rep_of):
    """Bucket ONE missed decision. Decided in this order, and every test is a
    fact read off a report or an index record -- never off the call graph.

      X-constructor         a constructor is not a unit; complete-path
                            enumeration has no path set for it and branch
                            coverage instruments it. A SCOPE difference between
                            the two metrics, not one of (1)/(2)/(3).
      X-no-caller-in-flat   the enclosing body has no owning unit tag and no
                            entered caller: nothing in this entry's flat calls
                            it. The baseline reached it only because Pair 2 ran
                            `--function <lib fn>` on it in isolation, a route
                            the collector refuses on soundness grounds. Also a
                            scope difference.
      (2b)                  every owning unit tag exists but none produced a
                            report -- killed by the outer timeout, or skipped.
      X-body-never-expanded an entered unit does own it, yet NO witnessed path
                            of any entered unit walked a decision in this body.
                            The signature of a call site withdrawn by
                            degradation or the depth bound -- unquantifiable
                            today because `degraded_call_sites` reaches no
                            reader -- so it is NOT called (3).
      (1) / X-budget        the body WAS expanded, so the decision's arm was
                            simply never witnessed; which of the two depends on
                            what the owning unit's undetermined paths say.
    """
    if d.get("kind") == "constructor":
        return "X-constructor"
    states = {t: tag_state(t) for t in owners}
    with_report = [t for t, s in states.items() if s == "REPORT"]
    tried = [t for t, s in states.items()
             if s in ("KILLED", "NO-REPORT")]
    # A RUN WE STARTED AND LOST IS (2b), AND IT IS TESTED FIRST. Ordering
    # matters: `FarmingPool.rescueFunds` is a public entry, so nothing in the
    # flat calls it and its in-degree is 0 -- putting the in-degree test first
    # filed a unit whose run was KILLED at 300 s as "nothing calls it", which
    # is a budget result dressed as a scope result.
    if tried:
        return "(2b) driver never entered the unit"
    # NOTHING IN THE FLAT CALLS IT, and every owning tag was refused on scope
    # grounds. There is nothing for any driver to enter: the baseline reached it
    # only through Pair 2's `--function <lib fn>` route, which the collector
    # refuses because it verifies from an arbitrary state. This is the fact that
    # separates EscrowSrc's ImmutablesLib 0/8 from farming's libraries at 5/5
    # and 6/6 -- farming's ARE called from a unit.
    if in_degree == 0 and not ent:
        return "X-no-caller-in-flat"
    if not with_report:
        return "(2b) driver never entered the unit"
    if not expanded:
        return "X-body-never-expanded"
    saw_entry = saw_state = saw_budget = False
    for t in with_report:
        ur = rep_of[t]["u_reason_summary"]
        saw_entry |= any(k in ENTRY_REASONS for k in ur)
        saw_state |= any(k in STATE_REASONS for k in ur)
        saw_budget |= any(k in BUDGET_REASONS for k in ur)
    if saw_entry:
        return "(2a) unit-not-entered claim"
    if saw_state and saw_budget:
        return "X-mixed"
    if saw_state:
        return "(1) state guard"
    if saw_budget:
        return "X-budget"
    return "X-body-never-expanded"


if __name__ == "__main__":
    sys.exit(main(sys.argv))
