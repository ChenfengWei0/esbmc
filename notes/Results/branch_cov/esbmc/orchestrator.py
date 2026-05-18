#!/usr/bin/env python3
"""orchestrator.py — project-level branch-coverage run algorithm.

Implements Steps 1-5 of notes/Results/branch_cov/PROJECT_RUN_ALGORITHM.md
(the finalized, empirically-pinned algorithm). Step 0 (flatten) is NOT
re-implemented here: it is flatten_inputs.sh's job (forge/hardhat). This
orchestrator *consumes* flat single-source inputs and *guards* the
precondition by refusing a multi-source .solast with the flatten hint.

What this adds over run_pilot.sh (which is a flat measurement loop):
  1. classify every contract in the flat unit
  2. pick run mode per entry (default vs whole-unit+exclude-3rd-party)
  3. order runs cheapest-first
  4. drive ESBMC with ONE shared crash-safe --coverage-covered-set union
  5. report the cumulative union/total only (per-run % is junk)

Usage:
  orchestrator.py --esbmc <bin> [--solc <bin>] [--timeout S]
                  [--union <path>] [--third-party Name ...]
                  [--workdir <dir>] TARGET.sol [TARGET.sol ...]

Each TARGET.sol must already be flattened (single self-contained file).
stdlib only; Python 3.8+.
"""
import argparse
import bisect
import json
import os
import re
import subprocess
import sys
import tempfile

# ---------- .solast parsing ----------------------------------------------


def load_solast(path):
    """Strip solc's `JSON AST (compact format):` / `===` header, parse the
    SourceUnit JSON. Refuse multi-source (un-flattened) input."""
    raw = open(path, "r", encoding="utf-8", errors="replace").read()
    headers = raw.count("\n=======")
    if headers > 1:
        sys.exit(
            f"[Step 0 guard] {path}: multi-source .solast ({headers} source "
            "sections). The algorithm REQUIRES a flattened single-source "
            "input (PROJECT_RUN_ALGORITHM.md Step 0). Flatten first with "
            "flatten_inputs.sh (forge/hardhat flatten), then regenerate the "
            ".solast.")
    brace = raw.find("{")
    if brace < 0:
        sys.exit(f"{path}: no JSON object found")
    return json.loads(raw[brace:])


def collect_contracts(ast):
    """Return {name: contract-info}. linBase is resolved to names via the
    node-id -> name map (linearizedBaseContracts is a C3-ordered id array,
    [self, ...ancestors])."""
    id2name, defs = {}, []

    def walk(n):
        if isinstance(n, dict):
            if n.get("nodeType") == "ContractDefinition":
                id2name[n["id"]] = n["name"]
                defs.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(ast)

    out = {}
    for c in defs:
        fns = []
        ifs = 0

        def count_ifs(n):
            nonlocal ifs
            if isinstance(n, dict):
                if n.get("nodeType") == "IfStatement":
                    ifs += 1
                for v in n.values():
                    count_ifs(v)
            elif isinstance(n, list):
                for x in n:
                    count_ifs(x)

        for node in c.get("nodes", []):
            if isinstance(node, dict) and node.get(
                    "nodeType") == "FunctionDefinition":
                fns.append({
                    "name": node.get("name"),
                    "vis": node.get("visibility"),
                    "kind": node.get("kind"),
                    "impl": node.get("implemented", True),
                })
            count_ifs(node)
        out[c["name"]] = {
            "abstract": c.get("abstract", False),
            "kind": c.get("contractKind"),
            "lin": [id2name.get(i) for i in c.get("linearizedBaseContracts", [])],
            "fns": fns,
            "ifs": ifs,
            # AST `src` is "byteOffset:len:fileIdx" into the flat source.
            "src_off": int(c.get("src", "0:0:0").split(":")[0]),
        }
    return out


# ---------- L2: provenance-based auto third-party ------------------------

# forge flatten:  `// lib/openzeppelin-contracts/.../X.sol`
# hardhat flatten: `// File @openzeppelin/contracts/.../X.sol@v5.3.0` (current)
#                  `// File: @openzeppelin/contracts/.../X.sol`       (older)
#                  `// File contracts/X.sol`   (project-own)
# `File:?` tolerates both the current (no colon) and older (colon) hardhat
# markers; the optional group is skipped entirely for forge `// lib/...`.
_PROV = re.compile(rb"^//\s*(?:File:?\s+)?(\S+\.sol)", re.M)


def _is_dependency_path(p):
    """Mirror notes/coverage-comparison/_filter_effective.sh: project-own
    iff under the project source root and NOT interfaces/ or mocks/.
    Everything else (lib/, @scope/ packages, node_modules, interfaces/,
    mocks/) is excluded from the native lcov denominator, so we exclude
    it too."""
    p = p.lstrip("./")
    if p.startswith("lib/") or p.startswith("@") or "node_modules/" in p:
        return True
    parts = p.split("/")
    return "interfaces" in parts or "mocks" in parts


def auto_third_party(flat_sol, contracts):
    """Map each contract's AST byte-offset onto the flat file's
    `// File`/`// lib/` provenance block; a contract is auto third-party
    iff its origin path is a dependency / interface / mock path. No
    provenance markers (e.g. a hand-flattened file) -> empty set ->
    graceful fallback to the explicit --third-party list."""
    data = open(flat_sol, "rb").read()
    marks = []  # (byte_offset_of_line, decoded_path)
    pos = 0
    for line in data.splitlines(keepends=True):
        m = _PROV.match(line)
        if m:
            marks.append((pos, m.group(1).decode("utf-8", "replace")))
        pos += len(line)
    if not marks:
        return set()
    marks.sort()
    offs = [o for o, _ in marks]
    tp = set()
    for name, info in contracts.items():
        i = bisect.bisect_right(offs, info["src_off"]) - 1
        if i >= 0 and _is_dependency_path(marks[i][1]):
            tp.add(name)
    return tp


# ---------- Step 1: classify ---------------------------------------------


def has_external_entry(info):
    """Empirically-pinned predicate: >=1 implemented public/external
    non-constructor function. (abstract+public IS runnable standalone;
    abstract+internal-only is NOT — see PROJECT_RUN_ALGORITHM.md Step 1.)"""
    return any(f["impl"] and f["kind"] == "function"
               and f["vis"] in ("public", "external") for f in info["fns"])


def classify(contracts, third_party):
    cls = {}
    for name, info in contracts.items():
        if name in third_party or info["kind"] == "interface":
            cls[name] = "third_party"
        elif info["kind"] == "library":
            # A library is NEVER a standalone run. It is dependency code
            # covered TRANSITIVELY through the whole-unit run of whatever
            # contract calls it — exactly the mechanism that covers an own
            # abstract base. `--contract <lib>` is meaningless (libraries
            # are not deployable); `--function <libfn>` in isolation is an
            # over-approximation (any-input reachability, not the
            # project's actual call paths). The `--function` per-library
            # path is reserved for an explicit *pure-library-benchmark*
            # input mode (MakerTraitsLib-style: no caller in the unit),
            # which is out of scope here.
            cls[name] = "library"
        elif has_external_entry(info):
            cls[name] = "entry"
        else:  # abstract / internal-only contract base
            cls[name] = "own_base"
    return cls


# ---------- Steps 2-3: plan (run mode + order) ---------------------------


def plan(contracts, cls, third_party):
    """One run per `entry` contract. whole-unit+exclude iff there is own
    dependency code to cover transitively — an own (non-third-party)
    abstract ancestor, OR any project-own library in the unit (default
    semantics-A mode would scope library/base code OUT, leaving it
    uncovered). Else default per-contract. Ordered cheapest-first by
    static IfStatement count (ancestor-before is subsumed: a cheap
    base-carrying descendant sorts early, and the shared union makes
    final order-correctness moot — it is a set)."""
    unit_has_own_lib = "library" in cls.values()
    runs = []
    for name, info in contracts.items():
        if cls[name] != "entry":
            continue
        own_ancestors = [
            a for a in info["lin"][1:]
            if a and cls.get(a) != "third_party"
        ]
        whole = bool(own_ancestors) or unit_has_own_lib
        excl = sorted({a for a in info["lin"][1:]
                       if a and cls.get(a) == "third_party"} | third_party)
        # cost = static branches actually instrumented this run:
        # whole-unit = entire file; default = just this contract's own.
        cost = (sum(c["ifs"] for c in contracts.values())
                if whole else info["ifs"])
        runs.append({
            "contract": name,
            "whole": whole,
            "exclude": excl if whole else [],
            "cost": cost,
            "covers": [name] + own_ancestors,
        })
    runs.sort(key=lambda r: (r["cost"], r["contract"]))
    return runs


# ---------- Step 4: drive ESBMC, shared crash-safe union -----------------

COV_BLOCK = re.compile(r"\[Coverage\].*?(?=\Z|\n\[|\nVERIFICATION|\nESBMC ver)",
                       re.S)


def parse_coverage(stdout):
    """Last [Coverage] block -> (branches, reached, pct_str). 'No branch
    detected' -> (0,0,'n/a'); absent -> (None,None,None)."""
    blocks = COV_BLOCK.findall(stdout)
    if not blocks:
        return None, None, None
    b = blocks[-1]
    if "No branch detected" in b:
        return 0, 0, "n/a"
    mb = re.search(r"^Branches\s*:\s*(\d+)", b, re.M)
    mr = re.search(r"^Reached\s*:\s*(\d+)", b, re.M)
    mp = re.search(r"^Branch Coverage:\s*([0-9.]+)%", b, re.M)
    return (int(mb.group(1)) if mb else None,
            int(mr.group(1)) if mr else None,
            mp.group(1) if mp else None)


def run_one(args, sol, solast, r):
    cmd = [
        args.esbmc, solast, "--sol", sol, "--contract", r["contract"],
        "--branch-coverage-claims", "--k-induction", "--unlimited-k-steps",
        "--coverage-covered-set", args.union,
        "--memlimit", "8g", "--timeout", str(args.timeout),
        "--quiet", "--no-assertions",
    ]
    if r["whole"]:
        cmd.append("--coverage-whole-unit")
        for e in r["exclude"]:
            cmd += ["--coverage-exclude-contract", e]
    # outer timeout > inner --timeout so ESBMC's own SIGALRM fires first;
    # Item 2e has already atomically banked covered probes by then.
    outer = args.timeout + 30
    try:
        p = subprocess.run(["timeout", str(outer)] + cmd,
                            capture_output=True, text=True)
        out, code = p.stdout + p.stderr, p.returncode
    except Exception as e:  # noqa: BLE001
        out, code = f"orchestrator: failed to launch: {e}", -1
    br, re_, pct = parse_coverage(out)
    # "data even on UNKNOWN": if ESBMC was killed (own --timeout SIGALRM,
    # or the outer `timeout` SIGTERM) before k-induction concluded, its
    # signal handler now still emits the [Coverage] block, tagged
    # "(partial: ...)". The block's Branches is the full static
    # denominator; its covered probes (banked crash-safe in union.json)
    # are a LOWER BOUND. Surfacing the flag keeps the project aggregate
    # honestly labelled as a lower bound rather than silently exact.
    partial = "(partial:" in out
    return {"cmd": cmd, "exit": code, "branches": br, "reached": re_,
            "pct": pct, "partial": partial, "stdout": out}


# ---------- main ---------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="flattened single-source .sol")
    ap.add_argument("--esbmc", required=True)
    ap.add_argument("--solc", default="/usr/local/bin/solc")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--union", default=None,
                    help="shared covered-set json (default: <workdir>/union.json)")
    ap.add_argument("--third-party", action="append", default=[],
                    metavar="Name", help="contract name to exclude (repeatable)")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--plan-only", action="store_true",
                    help="classify + print the run plan, then stop (no ESBMC)")
    args = ap.parse_args()

    args.workdir = args.workdir or tempfile.mkdtemp(prefix="cov_orch_")
    os.makedirs(args.workdir, exist_ok=True)
    user_union = args.union
    args.union = args.union or os.path.join(args.workdir, "union.json")
    if os.path.exists(args.union):
        if user_union:
            print(f"# WARNING: --union {args.union} already exists; removing "
                  "it. The orchestrator uses ONE fresh union per project run "
                  "(Step 4); it does not resume a prior cross-run union.")
        os.remove(args.union)  # fresh union per project run
    user_tp = set(args.third_party)

    print(f"# union: {args.union}")
    print(f"# third-party (manual --third-party): {sorted(user_tp) or '(none)'}")
    print("# third-party auto-detected from flatten provenance, mirroring "
          "_filter_effective.sh (lib/ @scope/ node_modules/ interfaces/ "
          "mocks/ excluded from denom+numer)\n")

    units = []
    for sol in args.targets:
        solast = os.path.join(
            args.workdir, os.path.basename(sol) + ".solast")
        with open(solast, "w") as fh:
            sp = subprocess.run([args.solc, "--ast-compact-json", sol],
                                stdout=fh, stderr=subprocess.PIPE, text=True)
        if sp.returncode != 0:
            sys.exit(f"solc failed on {sol}:\n{sp.stderr}")
        ast = load_solast(solast)              # Step 0 guard (multi-source)
        contracts = collect_contracts(ast)
        auto_tp = auto_third_party(sol, contracts)   # L2: provenance
        eff_tp = user_tp | auto_tp
        cls = classify(contracts, eff_tp)            # Step 1
        runs = plan(contracts, cls, eff_tp)          # Steps 2-3
        units.append((sol, solast, contracts, cls, runs))
        print(f"## {sol}")
        print(f"   auto third-party ({len(auto_tp)}): "
              f"{sorted(auto_tp) or '(none — no provenance; --third-party only)'}")
        for n, c in sorted(contracts.items()):
            tag = " (auto-tp)" if n in auto_tp else ""
            print(f"   {n:<20} {cls[n]:<12} abstract={c['abstract']} "
                  f"ifs={c['ifs']}{tag}")
        for r in runs:
            mode = (f"whole-unit, exclude={r['exclude']}"
                    if r["whole"] else "default")
            print(f"   -> run {r['contract']:<18} [{mode}] "
                  f"cost={r['cost']} covers={r['covers']}")
        print()

    if args.plan_only:
        print("# --plan-only: stopping before ESBMC.")
        return

    # Step 4: drive, ordered, shared union
    #
    # Denominator (proj_total) = sum over units of each unit's STATIC total:
    #   - whole-unit run present  -> that run's Branches IS the whole-file
    #     total (all whole-unit runs of a unit are equal; it already subsumes
    #     every default contract's branches), so take it once (no summing ->
    #     no double-count).
    #   - all-default unit        -> each default run is scoped (semantics-A)
    #     to ONE contract's own lexically-declared decisions; the spans are
    #     disjoint across contracts, so the unit total is their SUM. Taking
    #     only the last run (the prior bug) dropped every earlier entry.
    # Numerator (proj_cov) = union.json deduped covered probes across the
    # whole project run. That IS the Step-5 "cumulative union" and is
    # uniformly correct in BOTH modes; the per-run Reached is junk.
    proj_total = 0
    partial_units = 0
    for sol, solast, contracts, cls, runs in units:
        any_whole = False
        whole_branches = 0
        default_branches_sum = 0
        for r in runs:
            res = run_one(args, sol, solast, r)
            log = os.path.join(args.workdir,
                               f"{r['contract']}.{os.path.basename(sol)}.log")
            open(log, "w").write(res["stdout"])
            if res["partial"]:
                partial_units += 1
            print(f"[run] {r['contract']:<18} exit={res['exit']:<4} "
                  f"Branches={res['branches']} Reached={res['reached']} "
                  f"(per-run%={res['pct']}; junk — see Step 5)"
                  f"{' [PARTIAL — lower bound]' if res['partial'] else ''} "
                  f"log={log}")
            if res["branches"]:
                if r["whole"]:
                    any_whole = True
                    whole_branches = res["branches"]
                else:
                    default_branches_sum += res["branches"]
        unit_total = whole_branches if any_whole else default_branches_sum
        proj_total += unit_total
        print(f"   unit {os.path.basename(sol)}: static branches "
              f"= {unit_total} "
              f"({'whole-unit total' if any_whole else 'sum of default runs'})")

    union_n = 0
    if os.path.exists(args.union):
        union_n = len(json.load(open(args.union)).get("covered", []))
    proj_cov = union_n  # cumulative deduped covered across the whole run

    # Step 5: cumulative-only report
    pct = (100.0 * proj_cov / proj_total) if proj_total else 0.0
    print("\n==================== PROJECT COVERAGE ====================")
    print(f"  cumulative reached / static total : {proj_cov} / {proj_total}")
    print(f"  branch coverage                   : {pct:.4f}%")
    print(f"  union.json deduped covered probes : {union_n}")
    if partial_units:
        print(f"  NOTE: {partial_units} run(s) terminated before "
              f"k-induction concluded (emitted partial coverage via the "
              f"signal handler); their covered probes are a LOWER BOUND, "
              f"so the project coverage above is a SOUND LOWER BOUND, not "
              f"exact (data-even-on-UNKNOWN; STAGE5_RESIDUAL_DIAG.md "
              f"Stage G).")
    if len(units) > 1:
        print("  NOTE: multi-unit aggregate sums per-unit static totals; a "
              "shared own-base flattened into >1 unit is double-counted in "
              "the denominator (cross-unit identity residual, "
              "PROJECT_RUN_ALGORITHM.md). Single flat unit = exact.")
    print("==========================================================")


if __name__ == "__main__":
    main()
