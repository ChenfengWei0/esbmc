#!/usr/bin/env python3
"""
collect.py -- LOCKED v1 (2026-05-20).  See notes/coverage/METHODOLOGY.md.

  python3 collect.py esbmc  <bench-key>
  python3 collect.py native <project>

Methodology (binding):
  - Denominator per file = canonical AST decision count (METHODOLOGY §2),
    computed by ast_decisions.py walking the flat's solc-compact JSON.
  - ESBMC reach per file = (unique flat-lines in union.json) ∩
    (canonical decision flat-lines for that file).  Bounded by denom.
  - Native reach per file = unique BRDA lines with arm > 0 in lcov for
    that file, bounded by canonical count.
  - Scope = files whose `// File X` block in the flat lies under the
    project's own production-source tree (no test/mock/script/iface).
  - branchesTotal is IDENTICAL for ESBMC and native of the same bench
    by construction.  If any rerun changes branchesTotal for fixed
    inputs, it is a pipeline bug to fix immediately.
"""
import argparse, json, os, re, subprocess, sys, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_decisions import (canonical_decisions, parse_flat_file_blocks,
                            file_at_flat_line)

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
INPUTS = REPO / "notes/coverage/inputs"
NATIVE_BASE = REPO / "notes/coverage-comparison"
DATA = REPO / "notes/coverage/data"

# The project-own contract set used to be derived by scanning a checked-out copy
# of each 1inch repo under notes/coverage-comparison/<project>/<src root>, keyed
# on FILE STEMS.  Those trees are gone.  With the scan returning an empty set the
# collector excluded EVERY contract in the flat -- the primary included -- and
# still exited 0, reporting 0% for a run that verified nothing.  A scope input
# whose absence silently rewrites the scope is not an input this pipeline may
# depend on, so it is PINNED in the repository instead.
# See notes/coverage/inputs/own_contracts.json for the set and its provenance.
OWN_CONTRACTS = INPUTS / "own_contracts.json"

PROJECT_SRC = {
    "aqua":                 ("aqua",                 "src/src"),
    "cross_chain_swap":     ("cross-chain-swap",     "src/contracts"),
    "farming":              ("farming",              "src/contracts"),
    "limit_order_protocol": ("limit-order-protocol", "src/contracts"),
    "st1inch":              ("st1inch",              "src/contracts"),
}

SOLC830 = "/usr/local/bin/solc"
SOLC823 = str(Path.home() / ".solc-select/artifacts/solc-0.8.23/solc-0.8.23")
BENCHES = {
    "aqua_Aqua":                    ("aqua__Aqua.flat.sol",                          "Aqua",           SOLC830, "aqua"),
    "cross_chain_swap_EscrowDst":   ("cross-chain-swap__EscrowDst.flat.sol",         "EscrowDst",      SOLC823, "cross_chain_swap"),
    "cross_chain_swap_EscrowSrc":   ("cross-chain-swap__EscrowSrc.flat.sol",         "EscrowSrc",      SOLC823, "cross_chain_swap"),
    "farming":                      ("farming__FarmingPool.flat.sol",                "FarmingPool",    SOLC830, "farming"),
    "limit_order_protocol":         ("limit-order-protocol__MakerTraitsLib.flat.sol","MakerTraitsLib", SOLC830, "limit_order_protocol"),
    "st1inch_St1inch":              ("st1inch__St1inch.flat.sol",                    "St1inch",        SOLC823, "st1inch"),
}

ESBMC_FLAGS_BASE = ["--branch-coverage-claims", "--k-induction", "--unlimited-k-steps",
                    "--memlimit", "8g", "--no-assertions"]

# Pair 1 (whole-contract harness): longer timeout - k-induction needs budget.
PAIR1_INNER_TIMEOUT = "600"   # seconds; ESBMC --timeout
PAIR1_OUTER_TIMEOUT = 720     # seconds; outer wrapper

# Pair 2 (per-function with --focus-function): a single focused fn typically
# converges fast OR yields most of its reach in the first ~30s; longer rarely
# adds reach.  60s strikes the productive balance.
PAIR2_INNER_TIMEOUT = "60"
PAIR2_OUTER_TIMEOUT = 90

ESBMC_FLAGS_PAIR1 = ESBMC_FLAGS_BASE + ["--timeout", PAIR1_INNER_TIMEOUT]
ESBMC_FLAGS_PAIR2 = ESBMC_FLAGS_BASE + ["--timeout", PAIR2_INNER_TIMEOUT]

# ---------- utilities -----------------------------------------------------

def pct(n, d):
    return round(100.0 * n / d, 2) if d else 0.0

def load_existing(path):
    return json.loads(path.read_text()) if path.exists() else {}

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")

def run(cmd, timeout):
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return cp.returncode, cp.stdout + cp.stderr, time.time() - t0
    except subprocess.TimeoutExpired as e:
        return -1, (e.stdout or "") + (e.stderr or ""), time.time() - t0

PROD_BLOCKS = ("test/", "tests/", "mock/", "mocks/", "script/", "scripts/",
               "interfaces/", "interface/", ".t.sol")

def is_project_own_marker(marker, project):
    """A flat marker is project-own iff (a) it lives under the project's
    source tree (`contracts/...` or `src/...` — NOT `node_modules/`,
    `lib/` (forge deps), or `@` (hardhat deps)), and (b) is not a
    test/mock/script/interface helper.
    """
    if marker is None or marker == "<preamble>": return False
    low = marker.lower()
    if any(b in low for b in PROD_BLOCKS): return False
    # 3rd-party deps:
    #   hardhat: paths start with `@<vendor>/` (e.g. @openzeppelin/...)
    #   forge:   paths start with `lib/<vendor>/` or `node_modules/...`
    if marker.startswith("@"): return False
    if marker.startswith("lib/"): return False
    if marker.startswith("node_modules/"): return False
    return True

# ---------- lcov parser ---------------------------------------------------

def parse_lcov(lcov_path):
    by_file = {}
    cur = None
    for raw in lcov_path.read_text().splitlines():
        if raw.startswith("SF:"):
            cur = raw[3:]
            by_file[cur] = {"brda_ln": defaultdict(list), "brf": 0, "brh": 0,
                            "fn": []}
        elif raw.startswith("BRDA:"):
            parts = raw[5:].split(",")
            ln = int(parts[0]); count = parts[3]
            v = None if count == "-" else int(count)
            by_file[cur]["brda_ln"][ln].append(v)
        elif raw.startswith("BRF:"):
            by_file[cur]["brf"] = int(raw[4:])
        elif raw.startswith("BRH:"):
            by_file[cur]["brh"] = int(raw[4:])
        elif raw.startswith("FN:"):
            ln, name = raw[3:].split(",", 1)
            by_file[cur]["fn"].append((int(ln), name))
    return by_file

def lcov_reached_lines(rec):
    """Set of original-file lines where at least one BRDA arm count > 0."""
    return {ln for ln, arms in rec["brda_ln"].items()
            if any(v is not None and v > 0 for v in arms)}

def lcov_instrumented_lines(rec):
    """All unique lines that lcov has any BRDA record for."""
    return set(rec["brda_ln"].keys())

def lcov_match_file(by_file, marker):
    """Find the lcov SF whose path tail matches the flat's marker
    (e.g. 'contracts/St1inch.sol').  Returns (sf_key, rec) or (None, None)."""
    for sf, rec in by_file.items():
        if sf.endswith("/" + marker) or sf == marker:
            return sf, rec
    # also try by basename
    for sf, rec in by_file.items():
        if os.path.basename(sf) == os.path.basename(marker):
            return sf, rec
    return None, None

# ---------- esbmc reach extraction ---------------------------------------

def parse_union_json(union_path):
    """Set of flat-lines that ESBMC --coverage-covered-set marks reached."""
    if not union_path.exists():
        return set()
    d = json.loads(union_path.read_text())
    out = set()
    loc_re = re.compile(r"line\s+(\d+)")
    for c in d.get("covered", []):
        m = loc_re.search(c.get("loc", ""))
        if m: out.add(int(m.group(1)))
    return out

def parse_show_claims(sc_text):
    return {int(m.group(1)) for m in re.finditer(r"line\s+(\d+)", sc_text)}

# ---------- per-bench ESBMC collection -----------------------------------

def collect_esbmc(bench_key):
    flat_rel, primary, solc, project = BENCHES[bench_key]
    flat = INPUTS / flat_rel
    solast = INPUTS / (flat_rel + ".solast")
    log_dir = Path(f"/tmp/cov_{bench_key}")
    log_dir.mkdir(parents=True, exist_ok=True)

    # ---------- canonical denominator (AST) ----------
    if not solast.exists() or solast.stat().st_mtime < flat.stat().st_mtime:
        with solast.open("w") as f:
            subprocess.run([solc, "--ast-compact-json", str(flat)],
                           stdout=f, check=True)

    decisions_by_file, blocks = canonical_decisions(flat)
    # Restrict to project-own markers
    own_markers = sorted({m for _, _, m in blocks if is_project_own_marker(m, project)})
    # `decisions_by_file[marker]` is a set of flat line numbers (one entry
    # per decision point, multiple on same line collapse — METHODOLOGY §2).
    canon_flat_lines = {m: decisions_by_file.get(m, set()) for m in own_markers}

    # ---------- ESBMC run with union persistence ----------
    union_json = log_dir / "union.json"
    if union_json.exists(): union_json.unlink()

    # Need to exclude every contract in the flat that is NOT project-own.
    own_names = own_contract_names(bench_key)
    flat_contract_names = parse_flat_top_contract_names(flat)
    excludes = []
    # sorted(): the exclude list used to be built by iterating a set, so two
    # runs on identical inputs produced commands differing only in argument
    # order -- noise in every diff of the stored JSON.
    for name in sorted(flat_contract_names):
        if name not in own_names:
            excludes += ["--coverage-exclude-contract", name]

    # Methodology routing: pure-library entry has no dispatcher harness,
    # so `--contract <Lib>` errors "no targets".  For library primaries
    # Pair 1's natural definition IS the union of `--function fn` per
    # public/internal fn (a library is statelessly callable; there is no
    # state-bearing contract to wrap).  Route through the same path
    # Pair 2 uses (a SHARED union json so both pairs agree).
    pkind = primary_contract_kind(flat, primary)
    nf_log = log_dir / "no_function.log"
    if pkind == "library":
        cmd = ["<routed-via-multi-function-because-primary-is-library>"]
        all_cmds_recorded = list(cmd)
        rc, out, wall = 0, "", 0.0
        nf_log.write_text("(library primary; Pair 1 reuses Pair 2 union.)\n")
    else:
        # Pair 1 strategy for contract primaries (LOCKED 2026-05-21):
        # Run `--contract C --coverage-whole-unit` ONCE first (whole-
        # dispatcher exploration; k-induction proves reach + unreach as
        # far as budget allows), then for EACH external method of C run
        # `--contract C --focus-function <fn> --coverage-whole-unit` to
        # narrow the dispatcher and let k-induction satisfy that fn's
        # specific modifier chain (some methods need 3+ modifiers to all
        # pass — easy under focus, hard under the full dispatcher's
        # combinatorial explosion).  All runs share the SAME
        # --coverage-covered-set union JSON, so reaches accumulate.
        # Soundness: each run is a sound ESBMC run; union of sound
        # reaches is sound.  Methodology stays "whole-contract harness"
        # — every sub-run keeps the constructor + state init intact.
        all_cmds = []
        # Pass 1: full dispatcher
        cmd_full = ([str(ESBMC), str(solast), "--sol", str(flat), "--contract", primary,
                     "--coverage-whole-unit",
                     "--coverage-covered-set", str(union_json)] + excludes + ESBMC_FLAGS_PAIR1)
        rc, out, wall = run(cmd_full, timeout=PAIR1_OUTER_TIMEOUT)
        all_cmds.append(" ".join(cmd_full))
        nf_log.write_text(out)
        # Pass 2..N: per-method --focus-function under the same --contract
        callables = enumerate_own_callable_functions(flat, project)
        # Multi-focus must cover EVERY external method callable on the
        # primary entry — including inherited methods declared in base
        # contracts (e.g. BaseEscrow's `rescueFunds` is callable on
        # EscrowSrc via inheritance).  Filter to all own contracts in
        # the flat that aren't excluded; the `--contract <Primary>` part
        # of the command still anchors the dispatcher to Primary, so
        # `--focus-function <fn>` resolves to the inherited copy.
        own_now = own_names
        per_method = [(c, fn, k) for c, fn, k in callables
                      if k != "library" and c in own_now]
        for cname, fname, ckind in per_method:
            cmd_focus = ([str(ESBMC), str(solast), "--sol", str(flat),
                         "--contract", primary, "--focus-function", fname,
                         "--coverage-whole-unit",
                         "--coverage-covered-set", str(union_json)] + excludes + ESBMC_FLAGS_PAIR2)
            rc_f, out_f, t_f = run(cmd_focus, timeout=PAIR2_OUTER_TIMEOUT)
            all_cmds.append(" ".join(cmd_focus))
            (log_dir / f"p1_focus_{cname}_{fname}.log").write_text(out_f)
            wall += t_f
        cmd = all_cmds[0]  # for backward-compat JSON field; full cmd list below
        all_cmds_recorded = all_cmds

    union_lines = parse_union_json(union_json)

    # show-claims for instrumentation-gap diagnostic
    sc_cmd = ([str(ESBMC), str(solast), "--sol", str(flat), "--contract", primary,
               "--branch-coverage-claims", "--coverage-whole-unit"] + excludes +
              ["--no-assertions", "--show-claims"])
    _, sc_out, _ = run(sc_cmd, timeout=60)
    (log_dir / "show_claims.log").write_text(sc_out)
    sc_lines = parse_show_claims(sc_out)

    # ---------- native lcov reaches ----------
    # The native column comes from forge/lcov and CANNOT be changed by anything
    # on the ESBMC side, so when the lcov file is absent the honest thing is to
    # carry the previously recorded numbers forward and SAY SO -- not to
    # recompute them as 0, which reads as "native covered nothing".
    sub, _ = PROJECT_SRC[project.replace("-", "_")]
    lcov_path = NATIVE_BASE / sub / "_results" / "lcov.info"
    by_file = parse_lcov(lcov_path) if lcov_path.exists() else {}
    prev_blob = load_existing(DATA / f"esbmc_{bench_key}.json")
    native_carried = not lcov_path.exists()
    prev_native = {}
    if native_carried:
        for section in ("no_function", "per_function"):
            for prec in prev_blob.get(section, {}).get("perFile", []):
                n = prec.get("native", {})
                if "reached" in n:
                    prev_native.setdefault(prec["file"], n)
        if not prev_native:
            sys.exit(f"{lcov_path} is missing and {DATA}/esbmc_{bench_key}.json "
                     f"carries no previous native numbers to fall back on -- "
                     f"refusing to report native reach as 0")

    # ---------- per-file aggregation ----------
    per_file = []
    total_denom = 0
    total_esbmc = 0
    total_native = 0
    for marker in own_markers:
        c_lines = canon_flat_lines[marker]
        denom = len(c_lines)
        if denom == 0:
            continue

        # ESBMC reach: intersect union flat-lines with canonical flat-lines
        esbmc_reach_set = union_lines & c_lines
        esbmc_reach = min(len(esbmc_reach_set), denom)

        # ESBMC instrumented: --show-claims lines bucketed to this file's flat block
        s, e, _ = next(b for b in blocks if b[2] == marker)
        sc_in_file = {ln for ln in sc_lines if s <= ln <= e}
        esbmc_instr = len(sc_in_file)

        # Native: match flat marker → lcov SF
        sf_key, rec = lcov_match_file(by_file, marker)
        if rec is not None:
            n_reach = lcov_reached_lines(rec)
            n_instr = lcov_instrumented_lines(rec)
            native_reach = min(len(n_reach), denom)
            native_instr = len(n_instr)
        elif native_carried and marker in prev_native:
            native_reach = prev_native[marker].get("reached", 0)
            native_instr = prev_native[marker].get("instrumented", 0)
            sf_key = prev_native[marker].get("lcovSourceFile")
        else:
            native_reach = 0
            native_instr = 0

        per_file.append({
            "file": marker,
            "astDecisions": denom,
            "esbmc": {
                "instrumented": esbmc_instr,
                "reached": esbmc_reach,
                "coveragePct": pct(esbmc_reach, denom),
            },
            "native": {
                "lcovSourceFile": sf_key,
                "instrumented": native_instr,
                "reached": native_reach,
                "coveragePct": pct(native_reach, denom),
            },
        })
        total_denom += denom
        total_esbmc += esbmc_reach
        total_native += native_reach

    # ---------- Pair 2: per-function multi-run ----------
    pair2 = collect_pair2(bench_key, flat, solast, project, log_dir, canon_flat_lines,
                          own_markers, blocks, by_file, prev_native, native_carried)

    # For library primaries, copy Pair 2's reach as Pair 1 (libraries have
    # no dispatcher harness; their natural Pair 1 == Pair 2).  See
    # METHODOLOGY.md §3 (library scope).
    if pkind == "library":
        per_file = pair2["perFile"][:]
        total_denom = pair2["total"]["branchesTotal"]
        total_esbmc = pair2["total"]["esbmcReached"]
        total_native = pair2["total"]["nativeReached"]

    blob = {
        "benchmark": bench_key,
        "project": project,
        "primary": {"name": primary, "kind": "contract"},
        "flatInput": str(flat),
        "methodology": "see notes/coverage/METHODOLOGY.md (LOCKED 2026-05-20)",
        "ownContractsPinnedFrom": str(OWN_CONTRACTS),
        "nativeSource": (
            f"lcov: {lcov_path}" if not native_carried else
            f"CARRIED FORWARD from the previous {DATA}/esbmc_{bench_key}.json -- "
            f"{lcov_path} is absent.  The native column comes from forge/lcov and "
            f"cannot be changed by an ESBMC-side change, so it is reproduced rather "
            f"than recomputed as 0."),
        "per_function": pair2,
        "no_function": {
            # `" ".join(cmd)` when `cmd` is a STRING joins its CHARACTERS, which
            # is what this field held for every benchmark up to and including
            # the 2026-07-30 re-baseline: "/ h o m e / s a m s o n / ...".
            # It is not cosmetic. The documented way to recover the project-own
            # contract set (notes/coverage/inputs/own_contracts.json) is to
            # subtract the --coverage-exclude-contract names from the flat's
            # contracts; a reader who reaches for THIS field -- the natural one
            # for the Pair-1 scope -- finds ZERO such tokens, because every one
            # is spelled a character at a time. That yields own = ALL contracts,
            # the maximal scope: the same defect just fixed, with its sign
            # flipped. Only the per_function commands were ever usable.
            "commandUsed": cmd if isinstance(cmd, str) else " ".join(cmd),
            # The per-method Pair-1 commands were executed and recorded nowhere.
            "allCommandsUsed": all_cmds_recorded,
            "wallSeconds": round(wall, 2),
            "exitCode": rc,
            "ownFilesInScope": [p["file"] for p in per_file],
            "excludedContractsCount": len(excludes) // 2,
            "perFile": per_file,
            "total": {
                "branchesTotal": total_denom,
                "esbmcReached": total_esbmc,
                "esbmcCoveragePct": pct(total_esbmc, total_denom),
                "nativeReached": total_native,
                "nativeCoveragePct": pct(total_native, total_denom),
                "invariant_branchesTotal_shared":
                    "denominator above is the canonical AST decision count "
                    "for the project-own files in this entry's flat (§2 of METHODOLOGY); "
                    "both ESBMC and native reach are reported against this SAME number.",
            },
            "rawLogPath": str(nf_log),
            "unionJsonPath": str(union_json),
        },
    }
    return blob

def collect_pair2(bench_key, flat, solast, project, log_dir, canon_flat_lines, own_markers,
                  blocks, by_file, prev_native=None, native_carried=False):
    """Pair 2: multi-`--function` ESBMC collection.  Reach is unioned via
    `--coverage-covered-set <shared_union>` so all per-fn runs accumulate
    into one set.  Compared against the SAME AST canonical denominator
    (the union over own_markers) so the §8 invariant holds.
    """
    union_p2 = log_dir / "union_pair2.json"
    if union_p2.exists(): union_p2.unlink()

    callable_fns = enumerate_own_callable_functions(flat, project)

    # Pair 2 reuses Pair 1's exclude list so the SSA stays the same
    # scope (project-own).  Wider scope = more probes ESBMC must reason
    # about per fn, with no payoff (excluded probes never enter the
    # canonical-decision intersection anyway).
    own = own_contract_names(bench_key)
    flat_names = parse_flat_top_contract_names(flat)
    p1_excludes = []
    for n in sorted(flat_names):
        if n not in own:
            p1_excludes += ["--coverage-exclude-contract", n]

    fn_results = []
    for cname, fname, ckind in callable_fns:
        log = log_dir / f"fn_{cname}_{fname}.log"
        if ckind == "library":
            cmd = ([str(ESBMC), str(solast), "--sol", str(flat),
                    "--function", fname,
                    "--coverage-covered-set", str(union_p2)] + ESBMC_FLAGS_PAIR2)
        else:
            cmd = ([str(ESBMC), str(solast), "--sol", str(flat),
                    "--contract", cname, "--focus-function", fname,
                    "--coverage-whole-unit",
                    "--coverage-covered-set", str(union_p2)] + p1_excludes + ESBMC_FLAGS_PAIR2)
        rc, out, t = run(cmd, timeout=PAIR2_OUTER_TIMEOUT)
        log.write_text(out)
        bs = re.findall(r"^Branches\s*:\s*(\d+)\s*$", out, re.M)
        rs = re.findall(r"^Reached\s*:\s*(\d+)\s*$", out, re.M)
        b = int(bs[-1]) if bs else 0
        r = int(rs[-1]) if rs else 0
        status = "ok"
        if "ambiguous" in out.lower(): status = "ambiguous"
        elif "no targets" in out.lower() or "no verification targets" in out.lower(): status = "no_targets"
        elif rc != 0 and not bs: status = f"error rc={rc}"
        fn_results.append({
            "contract": cname, "function": fname,
            "commandUsed": " ".join(cmd),
            "wallSeconds": round(t, 2),
            "exitCode": rc,
            "status": status,
            "rawBranches": b, "rawReached": r,
            "rawLogPath": str(log),
        })

    # Reach from union (intersected with canonical per file)
    union_lines = parse_union_json(union_p2)
    per_file = []
    total_denom = 0; total_esbmc = 0; total_native = 0
    for marker in own_markers:
        c_lines = canon_flat_lines[marker]
        denom = len(c_lines)
        if denom == 0: continue
        e_reach_set = union_lines & c_lines
        e_reach = min(len(e_reach_set), denom)
        sf_key, rec = lcov_match_file(by_file, marker)
        if rec is not None:
            n_reach = min(len(lcov_reached_lines(rec)), denom)
        elif native_carried and prev_native and marker in prev_native:
            n_reach = prev_native[marker].get("reached", 0)
        else:
            n_reach = 0
        per_file.append({
            "file": marker,
            "astDecisions": denom,
            "esbmc": {"reached": e_reach, "coveragePct": pct(e_reach, denom)},
            "native": {"reached": n_reach, "coveragePct": pct(n_reach, denom)},
        })
        total_denom += denom; total_esbmc += e_reach; total_native += n_reach

    return {
        "functions": fn_results,
        "unionJsonPath": str(union_p2),
        "perFile": per_file,
        "total": {
            "branchesTotal": total_denom,
            "esbmcReached": total_esbmc,
            "esbmcCoveragePct": pct(total_esbmc, total_denom),
            "nativeReached": total_native,
            "nativeCoveragePct": pct(total_native, total_denom),
        },
    }

def primary_contract_kind(flat_path, primary_name):
    """Return 'contract' | 'library' | 'interface' for the primary entry by
    scanning flat AST.  Used to route Pair 1 of pure-library benches
    through the multi-function path (libraries have no dispatcher harness
    so `--contract <Library>` errors).
    """
    from ast_decisions import extract_ast_json
    solast = Path(str(flat_path) + ".solast")
    if not solast.exists(): return "contract"
    ast = extract_ast_json(solast)
    for n in ast.get("nodes", []):
        if n.get("nodeType") == "ContractDefinition" and n.get("name") == primary_name:
            return n.get("contractKind", "contract")
    return "contract"

def own_contract_names(bench_key):
    """The project-own contract set for a benchmark, read from the pinned file.

    Hard-fails rather than returning an empty set: an empty set is not "no
    scope restriction", it excludes every contract in the flat including the
    primary, which produces a 0% report from a run that verified nothing.
    """
    if not OWN_CONTRACTS.exists():
        sys.exit(f"missing {OWN_CONTRACTS}: the project-own contract set is "
                 f"pinned there and has no fallback")
    d = json.loads(OWN_CONTRACTS.read_text())
    entry = d.get("benchmarks", {}).get(bench_key)
    if not entry or not entry.get("ownContracts"):
        sys.exit(f"{OWN_CONTRACTS} has no non-empty ownContracts for {bench_key}")
    return set(entry["ownContracts"])

def parse_flat_top_contract_names(flat_path):
    """Names of every contract/library/interface declared at top level in the flat."""
    src = flat_path.read_text().splitlines()
    decl = re.compile(r"^\s*(?:abstract\s+)?(contract|library|interface)\s+(\w+)\b")
    out = set()
    for line in src:
        m = decl.match(line)
        if m: out.add(m.group(2))
    return out

def enumerate_own_callable_functions(flat_path, project):
    """Walk the flat AST, return list of (contract_name, fn_name) for every
    public/external function defined in a project-own file (per the
    `// File <path>` block of the flat).

    Used for Pair 2 multi-function ESBMC collection.
    """
    from ast_decisions import byte_to_line, extract_ast_json
    b2l = byte_to_line(flat_path.read_bytes())
    blocks = parse_flat_file_blocks(flat_path)
    solast = Path(str(flat_path) + ".solast")
    if not solast.exists():
        return []
    ast = extract_ast_json(solast)
    out = []
    walk_collect_callable(ast, b2l, blocks, project, out)
    seen = set(); uniq = []
    for c, fn, k in out:
        if (c, fn) not in seen:
            seen.add((c, fn)); uniq.append((c, fn, k))
    return uniq

def walk_collect_callable(node, b2l, blocks, project, out, cur_contract=None, cur_kind=None):
    if node is None: return
    if isinstance(node, list):
        for c in node: walk_collect_callable(c, b2l, blocks, project, out, cur_contract, cur_kind)
        return
    if not isinstance(node, dict): return
    nt = node.get("nodeType")
    if nt == "ContractDefinition":
        cur_contract = node.get("name")
        cur_kind = node.get("contractKind", "contract")  # contract|library|interface
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk_collect_callable(v, b2l, blocks, project, out, cur_contract, cur_kind)
        return
    if nt == "FunctionDefinition" and cur_contract:
        vis = node.get("visibility")
        kind = node.get("kind", "function")
        # Libraries: also include internal fns (callable via `using` directives
        # and directly via --function).  Contracts: only public/external.
        allowed = ((cur_kind == "library" and vis in ("public", "external", "internal"))
                   or (cur_kind == "contract" and vis in ("public", "external")))
        if allowed and kind == "function":
            src = node.get("src")
            if src:
                ln = line_of_bytes(b2l, int(src.split(":")[0]))
                marker = file_at_flat_line(blocks, ln)
                if is_project_own_marker(marker, project):
                    out.append((cur_contract, node.get("name"), cur_kind))
        return
    for v in node.values():
        if isinstance(v, (list, dict)):
            walk_collect_callable(v, b2l, blocks, project, out, cur_contract, cur_kind)

def line_of_bytes(b2l, byte_pos):
    import bisect
    return bisect.bisect_right(b2l, byte_pos)

# ---------- per-project native collection (standalone view) --------------

def collect_native(project):
    """Independent native-only JSON (not used for the canonical comparison;
    that lives inside esbmc_<bench>.json).  Kept for transparency."""
    sub, src_root = PROJECT_SRC[project]
    lcov = NATIVE_BASE / sub / "_results" / "lcov.info"
    if not lcov.exists():
        sys.exit(f"missing {lcov}")
    by_file = parse_lcov(lcov)
    per = []
    for sf, rec in sorted(by_file.items()):
        n_instr = len(lcov_instrumented_lines(rec))
        n_reach = len(lcov_reached_lines(rec))
        if n_instr == 0: continue
        per.append({
            "sourceFile": sf,
            "isProduction": all(b not in sf.lower() for b in PROD_BLOCKS),
            "lcovBrdaLines": n_instr,
            "lcovReachedLines": n_reach,
            "coveragePct": pct(n_reach, n_instr),
        })
    return {
        "project": project,
        "lcovPath": str(lcov),
        "methodology": "see notes/coverage/METHODOLOGY.md (LOCKED 2026-05-20)",
        "note": ("This view is FOR REFERENCE ONLY -- not part of the locked "
                 "comparison.  For the official comparison see "
                 "esbmc_<bench>.json.no_function.total.{esbmcCoveragePct, nativeCoveragePct}."),
        "perFile": per,
    }

# ---------- main ---------------------------------------------------------

def cmd_esbmc(args):
    bench = args.bench
    if bench not in BENCHES: sys.exit(f"unknown bench: {bench}")
    out_path = DATA / f"esbmc_{bench}.json"
    blob = collect_esbmc(bench)
    save(out_path, blob)
    print(f"  -> {out_path}")

def cmd_native(args):
    proj = args.project
    if proj not in PROJECT_SRC: sys.exit(f"unknown project: {proj}")
    out_path = DATA / f"native_{proj}.json"
    save(out_path, collect_native(proj))
    print(f"  -> {out_path}")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    pe = sub.add_parser("esbmc"); pe.add_argument("bench"); pe.set_defaults(func=cmd_esbmc)
    pn = sub.add_parser("native"); pn.add_argument("project"); pn.set_defaults(func=cmd_native)
    args = ap.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
