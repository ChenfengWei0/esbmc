#!/usr/bin/env python3
"""Stage 4 sweep: one PUT per CERTIFIED region, and a table of what came out.

The funnel's last column was 0 for a wiring reason, so the first thing stage 4
needs is a number that is MEASURED rather than hand-run: this script walks
every certified region stage 2 recorded and asks `solidity_path_put.py` for a
PUT, then prints what each one produced and why.

THE REGION IS PARSED BACK OUT OF THE SWEEP'S OWN PROSE, with the SAME regexes
the driver prints it with (`<name> in [lo, hi]` optionally `\\ {v, w}`, and
`<name> == <v>` for a pin). That is deliberate: re-deriving the region by
re-running stage 2 would make stage 4's input a DIFFERENT measurement from the
one the funnel counts, and the two would drift the moment either sweep is
re-run. Reading the recorded artefact keeps `A` and `B` the same 7 regions.

esbmc is run ONE AT A TIME, by construction: each PUT costs two sequential
esbmc invocations and this loop is serial.
"""

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
from solidity_ast_dependencies import path_function_artifact_suffix  # noqa: E402
from veriput_subjects import SubjectError, subject_from_record  # noqa: E402
from veriput_path_guard import ensure_path_not_protected  # noqa: E402
from veriput_recipe import (STRONG_RECIPE_VERSION, STRONG_PUT_AUTO_UNWIND,  # noqa: E402
                            STRONG_PUT_AUTO_PARTIAL_LOOPS,
                            STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET,
                            STRONG_PUT_FUZZ_RUNS,
                            STRONG_PUT_LIFT_UNCONSTRAINED_CALLDATA,
                            STRONG_PUT_R2_CANDIDATE_BUDGET,
                            STRONG_PUT_R2_DEPTH,
                            STRONG_PUT_R2_TERM_BUDGET)
NOTES = os.path.abspath(os.path.join(HERE, "..", ".."))
INPUTS = os.path.join(NOTES, "coverage", "inputs")
CERT = os.path.join(NOTES, "coverage", "certify", "results.jsonl")
# The PoC set's own stage-2 sweep. Two files rather than one because they are
# two different questions: on a real contract "not certified" mixes the
# method's limits with the contract's difficulty, while a PoC is one shape and
# nothing else. Which file a row came from therefore travels with the row.
POC_CERT = os.path.join(NOTES, "coverage", "certify", "poc_results.jsonl")
POC_SRC = os.path.join(NOTES, "coverage", "poc")
OUT = os.path.join(NOTES, "coverage", "put_roundtrip")
ESBMC = os.path.join(REPO, "build", "src", "esbmc", "esbmc")
PUT = os.path.join(REPO, "scripts", "solidity_path_put.py")
FORGE_STD = os.path.join(
    NOTES, "coverage-comparison", "_foundry_roundtrip", "aqua_forge",
    "lib", "forge-std")

# benchmark key -> (flat basename, contract under test)
BENCHES = {
    "aqua_Aqua": ("aqua__Aqua.flat.sol", "Aqua"),
    "cross_chain_swap_EscrowSrc": ("cross-chain-swap__EscrowSrc.flat.sol",
                                   "EscrowSrc"),
    "cross_chain_swap_EscrowDst": ("cross-chain-swap__EscrowDst.flat.sol",
                                   "EscrowDst"),
    "farming": ("farming__FarmingPool.flat.sol", "FarmingPool"),
    "limit_order_protocol": ("limit-order-protocol__MakerTraitsLib.flat.sol",
                             "MakerTraitsLib"),
    "st1inch_St1inch": ("st1inch__St1inch.flat.sol", "St1inch"),
}

POC_UNITS = os.path.join(NOTES, "coverage", "poc_units")
EXIT_KIND_CACHE = {}


def corpus_inputs_dir(bench, unit):
    """Where THIS (benchmark, unit)'s source lives now, or None.

    The shared corpus directory `notes/coverage/inputs/` has been DELETED: it
    was the benchmark, and every driver reached a whole-corpus sweep by
    building `INPUTS / <basename>` off a benchmark key. Each PoC now owns
    hardlinks to the files its own unit needs.

    Stage 4 is the one consumer that cannot use a single per-process override
    the way the two drivers do: it walks SEVERAL units, from several
    benchmarks, in one pass, so the directory has to be resolved PER ROW. That
    is what this does -- it asks the PoC index which PoC is this exact
    (benchmark, unit) and returns that PoC's private input directory.

    Returns None when no PoC matches, and the caller then falls back to the old
    shared path and fails there with its own message. Silently substituting
    another unit's directory would be worse than the missing file: the
    basenames are identical across PoCs of one benchmark, so a wrong directory
    would RESOLVE and stage 4 would emit a PUT against a source nobody chose.
    """
    idx = os.path.join(POC_UNITS, "index.json")
    if not os.path.exists(idx):
        return None
    try:
        pocs = json.load(open(idx)).get("pocs") or []
    except ValueError:
        return None
    for pid in pocs:
        p = os.path.join(POC_UNITS, pid, "poc.json")
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p))
        except ValueError:
            continue
        if d.get("benchmark") == bench and d.get("unit") == unit:
            return d.get("inputs_dir")
    return None


FOUNDRY_TOML = """[profile.default]
src = "src"
test = "test"
libs = ["lib"]
via_ir = true
optimizer = true
optimizer_runs = 200
"""

CONCRETE_FALLBACK_WITNESS_CHECKS = {
    "SUCCESSFUL",
    "COMPLETE-WITNESS-NO-COORDINATE",
}
CONCRETE_ONLY_STAGE2_SOURCES = {
    "cleared-concrete-fallback": "cleared_not_certified_fallback",
    "timeout-concrete-fallback": "timeout_concrete_fallback",
}

# Byte for byte the driver's own printers (solidity_path_generalise.py:800,
# and the `, {n} == {v}` pin suffix built at its report block). One grammar,
# two readers; if the driver ever changes how it prints a region this parser
# must fail loudly rather than silently read half of it, which is why the
# caller refuses a row whose region parses empty.
INTERVAL_RE = re.compile(r"(\S+) in \[(\d+), (\d+)\](?: \\ \{([0-9, ]+)\})?")
PIN_RE = re.compile(r"(\S+) == (\d+)")


def claim_path_id_int(raw):
    if raw is None:
        return None
    text = str(raw)
    m = re.match(r"^(\d+)(?:#.*)?$", text)
    if not m:
        return None
    return int(m.group(1))


def normalize_exit_kind(kind):
    return "unknown" if kind == "undetermined" else kind


def parse_certified(text):
    region, holes = {}, {}
    for m in INTERVAL_RE.finditer(text):
        region[m.group(1)] = [int(m.group(2)), int(m.group(3))]
        if m.group(4):
            holes[m.group(1)] = sorted(
                {int(v) for v in m.group(4).split(",") if v.strip()})
    consumed = set(region)
    pins = {}
    for m in PIN_RE.finditer(text):
        # `x in [0, 5]` also matches `PIN_RE` on nothing, but a coordinate that
        # is already an interval must never be re-read as a pin.
        if m.group(1) in consumed:
            continue
        pins[m.group(1)] = int(m.group(2))
    return region, holes, pins


def parse_pins(raw):
    if not raw:
        return {}
    data = raw
    if isinstance(raw, str):
        try:
            data = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    pins = {}
    for key, value in data.items():
        try:
            pins[str(key)] = int(str(value), 0)
        except (TypeError, ValueError):
            continue
    return pins


def cleared_concrete_fallback_rows(record):
    """Stage-2 NOT_CERTIFIED paths whose concrete replay is authenticated.

    This is intentionally narrower than `concrete_fallback=true`: the replay is
    usable only when the single-point witness check returned SUCCESSFUL.  UNKNOWN
    is not proof and FAILED is a refutation of the concrete point.
    """
    rows = []
    not_certified = record.get("not_certified") or {}
    details = record.get("not_certified_details") or {}
    if isinstance(details, list):
        by_enc = {str(d.get("enc")): d for d in details if isinstance(d, dict)}
    elif isinstance(details, dict):
        by_enc = {str(k): v for k, v in details.items() if isinstance(v, dict)}
    else:
        by_enc = {}
    coords = [str(c) for c in (record.get("coords") or [])]
    row_pins = parse_pins(record.get("pins"))
    for enc, reason in not_certified.items():
        detail = by_enc.get(str(enc)) or {}
        if detail.get("concrete_fallback") is not True:
            continue
        if detail.get("witness_check") not in CONCRETE_FALLBACK_WITNESS_CHECKS:
            continue
        ce = detail.get("ce") or {}
        if not isinstance(ce, dict):
            continue
        region = {}
        for coord in coords:
            if coord not in ce:
                continue
            try:
                value = int(str(ce[coord]), 0)
            except (TypeError, ValueError):
                continue
            region[coord] = [value, value]
        pins = {k: v for k, v in row_pins.items() if k not in region}
        rows.append({
            "enc": str(enc),
            "region": region,
            "pins": pins,
            "reason": reason,
            "detail": detail,
        })
    return rows


def cert_row_timed_out(record):
    if record.get("exit") == 124:
        return True
    if str(record.get("bucket") or "").upper() == "TIMEOUT":
        return True
    diagnostic = record.get("driver_diagnostic") or {}
    progress = record.get("generalise_progress") or {}
    run_timeout = record.get("run_timeout_s") or progress.get("timeout_s")
    try:
        run_timeout = float(run_timeout)
        wall_s = float(record.get("wall_s") or 0)
    except (TypeError, ValueError):
        return False
    if run_timeout <= 0 or wall_s < max(1.0, run_timeout * 0.9):
        return False
    return (
        str(record.get("bucket") or "").upper() == "KILLED"
        and record.get("witnessed") is None
        and diagnostic.get("tag") == "esbmc-no-cov-report")


def timeout_concrete_fallback_rows(record):
    """Timed-out Stage-2 rows whose partial witness names replayable paths.

    These rows are not proofs and they do not provide a certified region.  They
    only keep a concrete replay opportunity when Stage 2 reached a path witness
    and then died in the final certification query.
    """
    if not cert_row_timed_out(record):
        return []
    if record.get("certified") or record.get("not_certified"):
        return []
    journal = record.get("partial_witness_journal") or {}
    if not isinstance(journal, dict):
        return []
    try:
        witness_count = int(journal.get("witness_count") or 0)
    except (TypeError, ValueError):
        witness_count = 0
    if witness_count <= 0:
        return []
    row_pins = parse_pins(record.get("pins"))
    rows = []
    for path in journal.get("paths") or []:
        if not isinstance(path, dict):
            continue
        enc = claim_path_id_int(path.get("path_id"))
        path_function = path.get("path_function")
        if enc is None or not path_function:
            continue
        try:
            path_witnesses = int(path.get("witness_count") or 0)
        except (TypeError, ValueError):
            path_witnesses = 0
        if path_witnesses <= 0:
            continue
        rows.append({
            "enc": str(enc),
            "path_function": str(path_function),
            "region": {},
            "pins": row_pins,
            "reason": "Stage-2 certification timed out after witnessing path",
            "detail": {
                "witness_check": "TIMEOUT-WITNESSED",
                "source_stage": journal.get("source_stage"),
                "claims_decided": journal.get("claims_decided"),
                "claims_total": journal.get("claims_total"),
                "partial": journal.get("partial"),
            },
        })
    return rows


def report_exit_kind(report_path, path_function, enc):
    """The path's exit kind from the Stage-1 report, or None.

    Stage 4 can spend minutes per certified region. When a unit contains both
    rollback-only negative paths and a normal path with a semantic post-state
    oracle, running the normal path last risks spending the budget before the
    strongest PUT is even attempted. The cert row names its enumeration report;
    use that already-authenticated artefact only for scheduling priority.
    """
    if not report_path:
        return None
    report_path = os.path.abspath(report_path)
    if report_path not in EXIT_KIND_CACHE:
        found = {}
        try:
            d = json.load(open(report_path))
            for c in d.get("claims") or []:
                pid = claim_path_id_int(c.get("path_id"))
                if pid is None:
                    continue
                pf = c.get("path_function")
                exit_kind = normalize_exit_kind(c.get("exit_kind"))
                found[(pf, pid)] = exit_kind
                found.setdefault((None, pid), exit_kind)
        except (OSError, ValueError):
            found = {}
        EXIT_KIND_CACHE[report_path] = found
    return (EXIT_KIND_CACHE[report_path].get((path_function, int(enc)))
            or EXIT_KIND_CACHE[report_path].get((None, int(enc))))


def current_binary_identity():
    """The identity of the executable THIS run would use.

    Same three fields as `pathcov_collect.py::binary_identity()` and as the
    `binary` block `solidity_path_put.py` now writes into put.json, so one
    comparison rule covers a runs.jsonl row and a put.json alike. `binaryMtime`
    is the load-bearing field: HEAD alone cannot separate two builds of one
    commit, which is the state this tree is in whenever a fix is uncommitted.
    """
    def _sh(args):
        try:
            return subprocess.run(args, capture_output=True, text=True,
                                  cwd=REPO, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": (int(os.stat(ESBMC).st_mtime)
                        if os.path.exists(ESBMC) else 0),
    }


def stale_reason(rec, now):
    """Why this put.json may not be counted, or None.

    Absence of the field is NOT treated as agreement. A put.json written before
    the stamp existed carries no evidence either way, and 'no evidence' must not
    read as 'same binary' -- that is precisely how the Aug-3 artefact was
    counted in the first place.
    """
    b = rec.get("binary")
    if not isinstance(b, dict):
        return ("put.json carries no `binary` block, so the executable that "
                "wrote it is unknown; it predates the stamp. Re-emit this row")
    if b.get("binaryMtime") != now.get("binaryMtime"):
        return (f"put.json was written by a DIFFERENT executable "
                f"(binaryMtime {b.get('binaryMtime')} vs {now.get('binaryMtime')}"
                f", head {b.get('head')} vs {now.get('head')}). Re-emit this row")
    return None


def record_key(record):
    key = record.get("benchmark") or record.get("poc")
    unit = record.get("unit")
    return f"{key}.{unit}" if key and unit else None


def stage4_selector_matches(only, key, unit, path_function=None):
    """Whether a Stage-4 row is selected by --only.

    A dotted selector means the operator named a full `<benchmark>.<unit>`;
    treat it as exact so `f` does not also select `fView`.  Bare short tokens
    keep the historical substring behaviour for broad sweeps.
    """
    if not only:
        return True
    row_key = f"{key}.{unit}" if key and unit else None
    exact = {unit, row_key}
    if path_function:
        exact.add(str(path_function))
        if key:
            exact.add(f"{key}.{path_function}")
    if only in exact:
        return True
    if "." in only or "@" in only or ":path:" in only:
        return False
    return bool(row_key and only in row_key)


def _not_certified_detail(record, enc):
    details = record.get("not_certified_details") or {}
    if isinstance(details, dict):
        got = details.get(str(enc))
        return got if isinstance(got, dict) else {}
    if isinstance(details, list):
        for row in details:
            if isinstance(row, dict) and str(row.get("enc")) == str(enc):
                return row
    return {}


def classify_not_certified(record, enc, reason):
    """Classify a NOT_CERTIFIED path for accounting only.

    This is not a proof step. It separates paths that can still fall back to a
    concrete replay from paths the current gate-cell method has already
    attributed to an unsupported harness-controlled split.
    """
    detail = _not_certified_detail(record, enc)
    if detail.get("concrete_fallback") is False:
        return "method_unsupported"
    if detail.get("concrete_fallback") is True:
        return "concrete_fallback"
    if record.get("static_extcall_inseparable") and \
            "external-call behavior" in str(reason):
        return "method_unsupported"
    return "detail_unknown"


def stage2_path_accounting(cert_path, only=""):
    """Summarise Stage-2 path outcomes for the rows selected by Stage 4."""
    out = {
        "records": 0,
        "witnessed": 0,
        "witnessed_unknown": 0,
        "certified": 0,
        "not_certified": 0,
        "concrete_fallback": 0,
        "method_unsupported": 0,
        "detail_unknown": 0,
        "no_verdict": 0,
    }
    if not os.path.exists(cert_path):
        return out
    for line in open(cert_path):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        key = record_key(record)
        row_key = record.get("benchmark") or record.get("poc")
        unit = record.get("unit")
        path_function = record.get("path_function")
        if not stage4_selector_matches(only, row_key, unit, path_function):
            continue
        out["records"] += 1
        certified = record.get("certified") or {}
        not_certified = record.get("not_certified") or {}
        out["certified"] += len(certified)
        out["not_certified"] += len(not_certified)
        witnessed = record.get("witnessed")
        if isinstance(witnessed, int):
            out["witnessed"] += witnessed
            gap = witnessed - len(certified) - len(not_certified)
            if gap > 0:
                out["no_verdict"] += gap
        else:
            out["witnessed_unknown"] += 1
        for enc, reason in not_certified.items():
            cls = classify_not_certified(record, enc, reason)
            out[cls] += 1
    return out


def recipe_requires_certified_details(version):
    """Whether Stage 4 needs machine-readable certified-region metadata."""
    if not isinstance(version, str):
        return False
    prefix = "veriput-strong/"
    if not version.startswith(prefix):
        return False
    head = version[len(prefix):].split("-", 1)[0]
    try:
        return int(head) >= 15
    except ValueError:
        return version >= "veriput-strong/15-relation-establish"


def apply_strong_put_recipe(args):
    """Apply the shared versioned Stage-4 recipe after argparse defaults."""
    if not getattr(args, "strong_recipe", False):
        return None
    args.auto_unwind = STRONG_PUT_AUTO_UNWIND
    args.auto_partial_loops = STRONG_PUT_AUTO_PARTIAL_LOOPS
    args.lift_unconstrained_calldata = STRONG_PUT_LIFT_UNCONSTRAINED_CALLDATA
    args.propose_r2 = True
    args.r2_depth = STRONG_PUT_R2_DEPTH
    args.r2_term_budget = STRONG_PUT_R2_TERM_BUDGET
    args.r2_candidate_budget = STRONG_PUT_R2_CANDIDATE_BUDGET
    args.fuzz_r2_prefilter = True
    args.fuzz_runs = STRONG_PUT_FUZZ_RUNS
    args.fuzz_r2_candidate_budget = STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET
    return STRONG_RECIPE_VERSION


def append_stage4_driver_options(cmd, args, path_function, exit_kind,
                                 stage2_source, stage2_witness_check, piece,
                                 pins):
    if args.foundry_fixture:
        cmd += ["--foundry-fixture", args.foundry_fixture]
    if args.auto_partial_loops:
        cmd += ["--auto-partial-loops"]
    if args.lift_unconstrained_calldata:
        cmd += ["--lift-unconstrained-calldata"]
    if path_function:
        cmd += ["--path-function", path_function]
    if exit_kind:
        cmd += ["--exit-kind", exit_kind]
    if args.propose_r2 and stage2_source not in CONCRETE_ONLY_STAGE2_SOURCES:
        cmd += ["--propose-r2", "--r2-depth", str(args.r2_depth),
                "--r2-term-budget", str(args.r2_term_budget),
                "--r2-candidate-budget", str(args.r2_candidate_budget)]
    if (args.fuzz_r2_prefilter
            and stage2_source not in CONCRETE_ONLY_STAGE2_SOURCES):
        cmd += ["--fuzz-r2-prefilter", "--fuzz-runs", str(args.fuzz_runs),
                "--fuzz-r2-candidate-budget",
                str(args.fuzz_r2_candidate_budget),
                "--fuzz-r2-prefilter-timeout", str(args.forge_timeout)]
    if stage2_source in CONCRETE_ONLY_STAGE2_SOURCES:
        cmd += ["--concrete-only", "--test-suffix", "_fb"]
        cmd += ["--concrete-stage2-source",
                CONCRETE_ONLY_STAGE2_SOURCES[stage2_source]]
        if stage2_witness_check:
            cmd += [
                "--concrete-stage2-witness-check",
                str(stage2_witness_check),
            ]
    for extra in args.esbmc_arg:
        cmd.append(f"--esbmc-arg={extra}")
    # ONLY when this row IS a piece, so an unsplit region's command line is
    # byte-identical to every one already recorded.
    if piece:
        cmd += ["--piece", str(piece)]
    for n, v in pins.items():
        cmd += ["--pin", f"{n}={v}"]
    return cmd


def print_stage2_path_accounting(accounting):
    print()
    print("STAGE 2 PATH ACCOUNTING for the selected unit(s)")
    print(f"  records                         : {accounting['records']}")
    print(f"  witnessed paths                 : {accounting['witnessed']}"
          + (f" ({accounting['witnessed_unknown']} record(s) unknown)"
             if accounting["witnessed_unknown"] else ""))
    print(f"  certified paths                 : {accounting['certified']}")
    print(f"  not-certified paths             : {accounting['not_certified']}")
    print(f"  ... with concrete fallback       : "
          f"{accounting['concrete_fallback']}")
    print(f"  ... method-level unsupported     : "
          f"{accounting['method_unsupported']}")
    print(f"  ... legacy/unknown detail        : "
          f"{accounting['detail_unknown']}")
    print(f"  witnessed paths with no verdict  : {accounting['no_verdict']}")
    print("  These are Stage-2 outcomes, not B: fuzz/Foundry can refute "
          "candidates, while ESBMC certification is still the proof gate.")


def ensure_project(name, flat, shared=None):
    """The forge project a PUT is written into.

    `shared` names ONE project every source is copied into, instead of one
    project per source. The corpus keeps a project per benchmark, because a
    benchmark's flat is 70-180 KB and compiling four of them for every test run
    is the cost of a mistake. The PoC set is the opposite case: 35 contracts of
    ~1-8 KB whose whole point is to be measured TOGETHER, and one project means
    ONE `forge test` produces the whole table rather than 35 that have to be
    added up by hand -- and a total added up by hand is a total nobody can
    re-run.
    """
    proj = os.path.join(OUT, shared or name)
    for d in ("src", "test", "lib"):
        os.makedirs(os.path.join(proj, d), exist_ok=True)
    with open(os.path.join(proj, "foundry.toml"), "w") as f:
        f.write(FOUNDRY_TOML)
    dst = os.path.join(proj, "src", os.path.basename(flat))
    if not os.path.exists(dst):
        with open(flat, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
    lib = os.path.join(proj, "lib", "forge-std")
    if not os.path.exists(lib):
        os.symlink(FORGE_STD, lib)
    return proj


def archive_generated_tests(project, reason="superseded"):
    """Move old generated Foundry tests out of Forge's compile set.

    Stage 4 intentionally reuses one project per subject so a full run pays one
    Forge compile.  A targeted re-run must still be a measurement of the rows
    selected now, not of stale `.t.sol` files left by a previous selector.  The
    files are renamed rather than deleted so raw artefacts remain auditable.
    """
    test_dir = os.path.join(project, "test")
    if not os.path.isdir(test_dir):
        return 0
    stamp = str(time.time_ns())
    moved = 0
    for name in sorted(os.listdir(test_dir)):
        if not name.endswith(".t.sol"):
            continue
        path = os.path.join(test_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            head = open(path, errors="ignore").read(4096)
        except OSError:
            continue
        if ("Auto-generated by ESBMC" not in head
                and "Foundry coverage test reconstructed" not in head):
            continue
        dst = os.path.join(test_dir, f"{name}.{reason}.{stamp}.disabled")
        os.rename(path, dst)
        moved += 1
    return moved


def cells_of(results):
    """(cell names over rows that PRODUCED a PUT, count of rows that did not).

    ⛔ SEPARATED FROM main() ON PURPOSE. This is the part that DECIDES whether
    a table may be quoted, and a deciding part has to be provable in both
    directions: silent when the only extra "cell" comes from a region that was
    never run, and firing when two rows that WERE run disagree. Left inline it
    could only ever be checked by reading it.

    A row counts as having produced a PUT when its return code is 0 AND its
    record names a file. An emitted row whose record carries no cell keeps the
    UNRECORDED sentinel -- that one IS a real unknown about a real run.
    """
    made, norun = [], 0
    for r in results:
        rc, rec = r[4], (r[5] or {})
        if rc == 0 and rec.get("file"):
            made.append((rec.get("cell") or {}).get("name", "UNRECORDED"))
        else:
            norun += 1
    return sorted(set(made)), norun


def run_forge(project, timeout):
    """Run Forge with a hard timeout over its whole process group."""
    start = time.monotonic()
    proc = subprocess.Popen(["forge", "test", "--json"], cwd=project,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        stdout, stderr = proc.communicate()
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    return (proc.returncode, stdout, stderr, timed_out,
            round(time.monotonic() - start, 3))


def main():
    global OUT
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", default="focus",
                    help="dispatcher alphabet: focus, whole, or a comma list "
                         "containing the target plus its recorded state "
                         "writers. Default focus with --max-tx 1 is the GATE "
                         "cell; a comma list with --max-tx 2 is an ARTEFACT "
                         "cell.")
    ap.add_argument("--poc", action="store_true",
                    help="read the PoC SET's stage-2 sweep "
                         "(certify/poc_results.jsonl) instead of the corpus's, "
                         "and write every PUT into ONE shared forge project so "
                         "a single `forge test` produces the whole table. The "
                         "two sweeps answer different questions and their rows "
                         "must never share a table: on a real contract 'not "
                         "certified' mixes the method's limits with the "
                         "contract's difficulty, while a PoC is one shape.")
    ap.add_argument("--cert", default=None,
                    help="read the certified regions from THIS file instead of "
                         "the default for --poc / the corpus. Needed the moment "
                         "a sweep has more than one ARM: the --skip-bracket arm "
                         "writes poc_results_skipbracket.jsonl and produced 32 "
                         "certified units the bracketed arm reports as KILLED, "
                         "and without this flag stage 4 could only ever be run "
                         "on whichever arm happens to own the default filename. "
                         "The arm a table came from is printed with it, because "
                         "two arms' PUT counts must never be summed.")
    ap.add_argument("--only", default="", metavar="SELECTOR",
                    help="emit only the regions selected by this token. A "
                         "dotted `<benchmark>.<unit>` or mangled "
                         "`path_function` selector is exact, while a bare "
                         "short token keeps the historical substring match. "
                         "WITHOUT IT EVERY "
                         "MEASUREMENT COSTS THE WHOLE SWEEP: each region is "
                         "two sequential esbmc invocations, so re-checking one "
                         "row after a one-line fix was costing twenty runs. "
                         "⛔ A value that matches NOTHING is a hard failure, "
                         "not an empty table -- an empty sweep prints a "
                         "well-formed report reading `0 of 0` and exits 0, "
                         "which is indistinguishable from a real answer.")
    ap.add_argument("--strong-recipe", action="store_true",
                    help="apply the shared versioned Stage-4 recipe "
                         f"({STRONG_RECIPE_VERSION}): auto-unwind, typed R2, "
                         "and one-sided Foundry refutation before ESBMC "
                         "certifies survivors")
    ap.add_argument("--propose-r2", action="store_true",
                    help="passed through to the driver: issue one typed R2 "
                         "candidate batch per certified region")
    ap.add_argument("--r2-depth", type=int, choices=(0, 1), default=1)
    ap.add_argument("--r2-term-budget", type=int, default=96)
    ap.add_argument("--r2-candidate-budget", type=int, default=128)
    ap.add_argument("--fuzz-r2-prefilter", action="store_true",
                    dest="fuzz_r2_prefilter",
                    help="pass the one-sided Foundry R2 refutation filter to "
                         "the PUT driver; every survivor still goes to ESBMC")
    ap.add_argument("--fuzz-runs", type=int, default=256)
    ap.add_argument("--fuzz-r2-candidate-budget", type=int, default=128)
    ap.add_argument("--forge-only", action="store_true",
                    help="do NOT emit anything: read the put.json each region "
                         "already produced, run `forge test` per project, and "
                         "print the five-gate B table. Costs no esbmc run, so "
                         "B can be re-measured after a forge/solc change or "
                         "after a PUT is edited by hand.")
    ap.add_argument("--emit-cleared-concrete-fallbacks", action="store_true",
                    help="also emit concrete-only replay tests for "
                         "NOT_CERTIFIED paths whose Stage-2 detail has "
                         "concrete_fallback=true and a cleared or complete "
                         "witness status, plus timed-out certification rows "
                         "whose partial journal already names witnessed "
                         "paths. These are raw/valid concrete tests only, "
                         "never PUTs or region proofs.")
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--auto-unwind", type=int, default=0,
                    help="passed to the driver: on an UNDECIDED-TRUNCATED "
                         "ladder, widen the loops the tool NAMED and retry, up "
                         "to N times. aqua `dock` is the recorded case.")
    ap.add_argument("--auto-partial-loops", action="store_true",
                    help="passed to the driver: after --auto-unwind is spent, "
                         "try the ladder once with --partial-loops, the third "
                         "repair named by ESBMC's UNDECIDED-TRUNCATED message")
    ap.add_argument("--lift-unconstrained-calldata", action="store_true",
                    help="passed to the driver: lift declared calldata "
                         "parameters absent from the certified region as "
                         "full-domain fuzz inputs when their type is supported")
    ap.add_argument("--timeout", type=int, default=600,
                    help="generation budget for each PUT driver invocation; "
                         "the driver shares it across its ESBMC children and "
                         "reserves time to assemble/write the PUT artifact")
    ap.add_argument("--forge-timeout", type=int, default=300,
                    help="per Forge invocation; Forge is run twice per project "
                         "for replay filtering and the final green gate")
    ap.add_argument("--memlimit-gib", type=int, default=8, metavar="N",
                    help="per ESBMC process; the official POC recipe passes 8")
    ap.add_argument("--foundry-fixture", default=None,
                    help="JSON fixture passed only to solidity_path_put.py's "
                         "Foundry assembly step. It is not forwarded to the "
                         "ESBMC concrete testcase emission run, so it can "
                         "repair a red local constructor replay without "
                         "changing the certified Stage-2 input.")
    ap.add_argument("--esbmc-arg", action="append", default=[], metavar="ARG",
                    help="one solver/encoder argument passed to every PUT/R2 "
                         "ESBMC invocation. Repeatable; use the = form for "
                         "values beginning with a dash.")
    ap.add_argument("--out-root", default=OUT,
                    help="project and scratch root. A single POC should point "
                         "this at its own output directory.")
    args = ap.parse_args()
    main_start = time.monotonic()
    stage4_recipe_version = apply_strong_put_recipe(args)
    if args.timeout <= 0:
        sys.exit("--timeout must be positive")
    if args.forge_timeout <= 0:
        sys.exit("--forge-timeout must be positive")
    if (args.r2_term_budget <= 0 or args.r2_candidate_budget <= 0
            or args.fuzz_runs <= 0 or args.fuzz_r2_candidate_budget <= 0):
        sys.exit("R2 term/candidate budgets and fuzz run/candidate budgets "
                 "must be positive")
    if args.memlimit_gib <= 0:
        sys.exit("--memlimit-gib must be positive")
    if args.scope not in ("focus", "whole"):
        scope_names = [name.strip() for name in args.scope.split(",")
                       if name.strip()]
        if not scope_names or ",".join(scope_names) != args.scope:
            sys.exit("--scope must be focus, whole, or a canonical "
                     "comma-separated function list")
    try:
        ensure_path_not_protected("--out-root", args.out_root)
    except ValueError as exc:
        sys.exit(str(exc))
    OUT = os.path.abspath(args.out_root)
    os.makedirs(OUT, exist_ok=True)
    cert_path = args.cert or (POC_CERT if args.poc else CERT)
    if not os.path.exists(cert_path):
        sys.exit(f"no certify sweep at {cert_path}")
    rows = []
    n_certified = 0   # BEFORE --only, so the header can say what was filtered
    n_cleared_fallback = 0
    n_timeout_fallback = 0
    for line in open(cert_path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        # WHICH SWEEP A ROW CAME FROM IS READ OFF THE ROW, not off the flag.
        # certify_poc.py keys its records on `poc`, certify_all.py on
        # `benchmark`. Detecting it here means pointing --cert at the wrong file
        # cannot silently produce rows resolved against the wrong sources; it
        # produces a row this loop refuses by name.
        key = r.get("benchmark") or r.get("poc")
        is_poc = "poc" in r
        try:
            row_subject = subject_from_record(r)
        except SubjectError as exc:
            print(f"  SKIP {key}.{r.get('unit')} cert row: {exc}")
            continue
        if args.emit_cleared_concrete_fallbacks:
            for fb in cleared_concrete_fallback_rows(r):
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "not-certified key is not numeric, so this concrete "
                          "fallback cannot be resolved to a path")
                    continue
                n_cleared_fallback += 1
                if not stage4_selector_matches(
                        args.only, key, r["unit"], r.get("path_function")):
                    continue
                exit_kind = report_exit_kind(
                    r.get("enumeration_report"), r.get("path_function"), enc_i)
                rows.append((key, is_poc, r["unit"], r.get("path_function"),
                             enc_i, None, None, [], False, {}, exit_kind,
                             row_subject, "cleared-concrete-fallback",
                             fb["region"], {}, fb["pins"],
                             fb["detail"].get("witness_check")))
            for fb in timeout_concrete_fallback_rows(r):
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "partial witness path id is not numeric, so this "
                          "timeout concrete fallback cannot be resolved")
                    continue
                n_timeout_fallback += 1
                path_function = fb.get("path_function") or r.get("path_function")
                if not stage4_selector_matches(
                        args.only, key, r["unit"], path_function):
                    continue
                exit_kind = report_exit_kind(
                    r.get("enumeration_report"), path_function, enc_i)
                rows.append((key, is_poc, r["unit"], path_function,
                             enc_i, None, None, [], False, {}, exit_kind,
                             row_subject, "timeout-concrete-fallback",
                             fb["region"], {}, fb["pins"],
                             fb["detail"].get("witness_check")))
        if r.get("bucket") != "CERTIFIED":
            continue
        certified_details = r.get("certified_details") or {}
        for enc, text in (r.get("certified") or {}).items():
            # ---- `<enc>` OR `<enc>#<piece>` --------------------------------
            #
            # certify_all.py records a path split by --max-region-pieces as one
            # entry PER PIECE: the pieces are different boxes, each certified by
            # its own query, and the path's region is their union. They are
            # therefore several regions about ONE enc, and each gets its own
            # PUT.
            #
            # The piece travels all the way to the emitter's `--piece`, which
            # puts it in the test function name, the test contract name and the
            # file name. Without that, piece 4 OVERWRITES piece 3's .t.sol and
            # the B gate below keys both on one test name -- so this is not
            # cosmetic, it is what makes two boxes two measurements.
            #
            # Before certify_all could read a piece line at all these regions
            # were recorded as `certified: {}`; measured on farming
            # setDistributor, where the driver's own line said `5 certified
            # region(s)` and the row said 0.
            enc_s = str(enc)
            base, _, piece = enc_s.partition("#")
            try:
                enc_i = int(base)
            except ValueError:
                print(f"  SKIP {key}.{r['unit']} enc={enc_s}: the certified key "
                      f"is neither `<enc>` nor `<enc>#<piece>`, so this row "
                      f"cannot be resolved to a path")
                continue
            details = certified_details.get(enc_s) or certified_details.get(base) or {}
            if recipe_requires_certified_details(r.get("recipe_version")) and not details:
                print(
                    f"REFUSED: {key}.{r['unit']} enc={enc_s} was recorded "
                    f"under recipe {r.get('recipe_version')}, which may "
                    "certify entry relations that are NOT recoverable from "
                    "the prose `certified` region string. This row has no "
                    "matching `certified_details` entry, so Stage 4 cannot "
                    "know whether it must materialize an entry relation such "
                    "as `state._owner := msg.sender`. Re-run Stage 2 with the "
                    "current certify_all.py instead of silently emitting a PUT "
                    "for a different entry slice.")
                return 2
            establish = details.get("established") or []
            # THE ARM'S OWN FLAG TRAVELS WITH THE REGION. Absent means the
            # row predates --pin-extcall, i.e. false -- never "unknown", because
            # every sweep that could set it writes it.
            # ---- THE DERIVATION CONFIGURATION TRAVELS WITH THE ROW ----
            #
            # The work order requires a rendered width to say WHICH STEP
            # produced it, and forbids a width that rests only on a
            # neighbourhood probe. The certified region string carries no such
            # information -- it is `name in [lo, hi]` and nothing else -- but
            # the sweep row does, because it records the switches the arm ran
            # under. It is ROW granularity, not per coordinate, and it is
            # labelled as such on the emitted test rather than being allowed
            # to read as if each bound had been traced.
            deriv = {k: r.get(k) for k in
                     ("level0", "level0_perturb", "level0_points",
                      "probe_ladder", "probe_ladder_budget", "probes",
                      "geometric_bracket", "sibling_subtraction",
                      "claim_budget",
                      "shrink_rounds", "refine_rounds", "skip_bracket",
                      "cut_policy", "max_region_pieces", "max_holes")
                     if r.get(k) is not None}
            n_certified += 1
            if not stage4_selector_matches(
                    args.only, key, r["unit"], r.get("path_function")):
                continue
            exit_kind = report_exit_kind(
                r.get("enumeration_report"), r.get("path_function"), enc_i)
            rows.append((key, is_poc, r["unit"], r.get("path_function"),
                         enc_i, piece or None, text,
                         establish, bool(r.get("pin_extcall")), deriv, exit_kind,
                         row_subject, "certified-region", None, None, None,
                         None))

    # ---- THE ARM OWNS ITS OWN PROJECT AND WORKDIR ----
    #
    # Without this, running stage 4 on a second arm writes its PUTs over the
    # first arm's, in the same forge project and the same `_wd/<bench>__<unit>__
    # <enc>` directory, and `--forge-only` then reports arm 2's tests under
    # whichever arm the reader has in mind. Two ledgers for one fact, with no
    # line anywhere saying they diverged. The suffix is derived from the cert
    # file's own name so it cannot drift from the input it describes.
    arm = ""
    default_cert = POC_CERT if args.poc else CERT
    if os.path.abspath(cert_path) != os.path.abspath(default_cert):
        arm = "__" + os.path.splitext(os.path.basename(cert_path))[0]

    # ⛔ THE TOTAL IS WHAT STAGE 2 RECORDED, NOT WHAT --only KEPT. Printing
    # `len(rows)` here made a filtered run announce "0 CERTIFIED region(s)
    # recorded by stage 2" -- a false statement about stage 2 produced by this
    # sweep's own filter, and exactly the kind of line that gets quoted later.
    print(f"=== {n_certified} CERTIFIED region(s) recorded by stage 2 "
          f"({os.path.basename(cert_path)}) ==="
          + (f"\n=== {n_cleared_fallback} cleared concrete fallback(s) "
             "available from Stage 2 ==="
             if args.emit_cleared_concrete_fallbacks else "")
          + (f"\n=== {n_timeout_fallback} timeout concrete fallback(s) "
             "available from Stage 2 partial witnesses ==="
             if args.emit_cleared_concrete_fallbacks else "")
          + (f"\n=== --only '{args.only}' keeps {len(rows)} of them; the "
             f"other {(n_certified + n_cleared_fallback + n_timeout_fallback) - len(rows)} were NOT measured by this run "
             f"and their absence is a filter, not a result ==="
             if args.only else ""))
    stage2_accounting = stage2_path_accounting(cert_path, args.only)
    print_stage2_path_accounting(stage2_accounting)
    if arm:
        print(f"=== ARM {arm[2:]}: PUTs go to their OWN project and workdir, so "
              f"this table does not overwrite or get confused with another "
              f"arm's. Two arms' PUT counts must never be summed. ===")
    results = []
    # ⛔ AN --only THAT SELECTS NOTHING IS A HARD FAILURE. The sweep would
    # otherwise print a complete, well-formed `0 of 0 certified region(s)`
    # table and exit 0 -- indistinguishable from "this arm has no regions",
    # which is a different fact entirely. Same shape as poc_funnel's --only,
    # and it is here because that one was found by being read wrong first.
    if args.only and not rows:
        total_rows = n_certified + n_cleared_fallback + n_timeout_fallback
        print(f"⛔ --only '{args.only}' selected NONE of the {total_rows} "
              f"Stage-4 candidate row(s) in this arm. It matches against "
              f"`<benchmark>.<unit>`. Refusing to print an empty sweep, which "
              f"reads exactly like a real measurement of nothing.")
        return 2
    if args.scope not in ("focus", "whole"):
        scope_names = set(args.scope.split(","))
        missing_targets = sorted({row[2] for row in rows} - scope_names)
        if missing_targets:
            print(f"REFUSED: --scope {args.scope!r} omits target unit(s) "
                  f"{', '.join(missing_targets)}. The dispatcher cannot enter "
                  "their certified paths.")
            return 2
    def _exit_priority(kind):
        return {"normal": 0, "unknown": 1, None: 2, "revert": 3}.get(
            kind, 2)
    ordered_rows = [r for _i, r in sorted(
        enumerate(rows), key=lambda ir: (_exit_priority(ir[1][10]), ir[0]))]
    if ordered_rows != rows:
        print("[order] normal-exit certified region(s) are emitted first, using "
              "the Stage-1 report's exit_kind. This changes scheduling only; "
              "regions and certification are unchanged")
    cleaned_projects = set()
    for (bench, is_poc, unit, path_function, enc, piece, text, establish,
         pin_extcall, deriv, exit_kind, row_subject, stage2_source,
         region_override, holes_override, pins_override,
         stage2_witness_check) in ordered_rows:
        # The label every downstream name is built from, derived ONCE and in
        # the same shape the emitter builds it (`p<K>`). Two derivations is how
        # the gate below comes to look up a function the emitted file does not
        # contain.
        encs = f"{enc}#{piece}" if piece else str(enc)
        is_corpus = False
        if row_subject is not None:
            flat = row_subject.flat_sol
            ast = row_subject.solast
            contract = row_subject.contract
            is_corpus = True
            if not os.path.exists(flat):
                print(f"  SKIP {bench}.{unit} enc={encs}: prepared subject "
                      f"source is missing at {flat}")
                continue
            if not os.path.exists(ast):
                print(f"  SKIP {bench}.{unit} enc={encs}: prepared subject "
                      f"AST is missing at {ast}")
                continue
        elif is_poc:
            # certify_poc.py runs the driver with `--contract <stem>`, so the
            # contract name IS the file stem; resolving it any other way would
            # be a second convention that can disagree with the sweep's.
            flat = os.path.join(POC_SRC, bench + ".sol")
            contract = bench
            if not os.path.exists(flat):
                print(f"  SKIP {bench}.{unit} enc={encs}: no PoC source at "
                      f"{flat}")
                continue
        elif bench not in BENCHES:
            print(f"  SKIP {bench}.{unit} enc={encs}: unknown benchmark key")
            continue
        else:
            flat_name, contract = BENCHES[bench]
            is_corpus = True
            # PER ROW, because one pass covers several units of several
            # benchmarks and there is no longer one directory that holds them
            # all. See corpus_inputs_dir: a miss returns None and falls back to
            # the deleted shared path, which then fails by name -- it never
            # borrows a neighbouring PoC's directory, where the identical
            # basename would resolve and quietly emit against a source nobody
            # chose.
            own = corpus_inputs_dir(bench, unit)
            flat = os.path.join(own or INPUTS, flat_name)
            if own and not os.path.exists(flat):
                print(f"  SKIP {bench}.{unit} enc={encs}: this unit's PoC "
                      f"input directory {own} does not hold {flat_name}. The "
                      f"shared corpus is deleted, so this is the only copy "
                      f"there should be -- rebuild it with "
                      f"notes/coverage/scripts/poc_split.py rather than "
                      f"letting stage 4 resolve somewhere else")
                continue
        # ---- TWO AST NAMING CONVENTIONS, AND ONLY ONE IS RIGHT PER SOURCE ----
        #
        # The corpus flats are named `<x>.flat.sol` and their AST is generated
        # ALONGSIDE as `<x>.flat.sol.solast` -- suffix APPENDED. The PoC set uses
        # `Path.with_suffix('.solast')` (certify_poc.py), i.e. the extension is
        # REPLACED: `D09_ValueGate.solast`, not `D09_ValueGate.sol.solast`.
        #
        # Appending for both cost the entire first PoC stage-4 sweep: all seven
        # certified regions came back `exit=6 0.0s emitted=[]`, which reads like
        # an emitter that produced nothing and is actually
        #
        #     ERROR: failed to open input file .../D09_ValueGate.sol.solast
        #
        # -- esbmc dying on the command line before it verified anything. The
        # refusal message the driver prints for that outcome ("This is an
        # EMISSION outcome, not a property of the region") is correct and was
        # still misleading, because the emission never ran.
        #
        # The PoC branch follows certify_poc.py's convention rather than
        # inventing a third: whichever file stage 2 generated is the one stage 4
        # must read, or the two stages are looking at different ASTs.
        if row_subject is None:
            ast = (os.path.splitext(flat)[0] + ".solast") if is_poc \
                else (flat + ".solast")
        if not os.path.exists(ast):
            print(f"  SKIP {bench}.{unit} enc={encs}: no AST at {ast}")
            continue
        if pin_extcall:
            # ⛔ NOT A SKIP FOR CONVENIENCE. The region is real and it certified;
            # what is missing is any way for the emitted test to be INSIDE it.
            print(f"  REFUSE {bench}.{unit} enc={encs}: this region was "
                  f"certified with --pin-extcall, i.e. under a fixed value for "
                  f"a quantity the HARNESS chose inside the execution (an "
                  f"external call's success bit is the usual one). A generated "
                  f"test chooses ARGUMENTS and cannot choose what a callee "
                  f"returns, so emitting it would produce a file that states "
                  f"the certified property while not being known to run inside "
                  f"the certified slice at all. WHAT LIFTS THIS: an emitter "
                  f"that makes the value happen -- a mock at the called address "
                  f"whose behaviour matches the pinned value -- after which "
                  f"this row becomes emittable unchanged")
            continue
        if stage2_source in CONCRETE_ONLY_STAGE2_SOURCES:
            region = dict(region_override or {})
            holes = dict(holes_override or {})
            pins = dict(pins_override or {})
        else:
            region, holes, pins = parse_certified(text)
        if stage2_source not in CONCRETE_ONLY_STAGE2_SOURCES and not region and not pins:
            print(f"  SKIP {bench}.{unit} enc={encs}: the recorded region "
                  f"parsed EMPTY, which is a PARSER failure, not an empty "
                  f"region -- refusing to emit a PUT over nothing")
            continue
        proj = ensure_project(bench + arm, flat,
                              shared=("poc" + arm) if is_poc else None)
        if not args.forge_only and proj not in cleaned_projects:
            moved = archive_generated_tests(proj)
            if moved:
                print(f"  [clean] archived {moved} old generated test file(s) "
                      f"from {os.path.basename(proj)} before this Stage-4 run")
            cleaned_projects.add(proj)
        pf_label = path_function_artifact_suffix(path_function)
        plabel = f"p{piece}" if piece else ""
        wd = os.path.join(
            OUT, "_wd", f"{bench}__{unit}{pf_label}__{enc}{plabel}{arm}")
        os.makedirs(wd, exist_ok=True)
        cmd = [sys.executable, PUT, "--esbmc", ESBMC, "--sol", flat,
               "--ast", ast, "--contract", contract, "--unit", unit,
               "--enc", str(enc), "--region", json.dumps(region),
               "--holes", json.dumps(holes),
               "--establish", json.dumps(establish),
               "--forge-project", proj, "--workdir", wd,
               "--timeout", str(args.timeout),
               "--memlimit", f"{args.memlimit_gib}g",
               # The CELL is a property of the measurement, not a default of
               # this sweep. INVOCATION_DECISIONS.md prints two command lines
               # and forbids quoting one into the other's table, so it is an
               # argument here and it is printed with the result.
               "--scope", args.scope, "--max-tx", str(args.max_tx),
               "--auto-unwind", str(args.auto_unwind),
               "--derived-by", json.dumps(deriv)]
        append_stage4_driver_options(
            cmd, args, path_function, exit_kind, stage2_source,
            stage2_witness_check, piece, pins)
        j = os.path.join(wd, "put.json")
        if args.forge_only:
            # RE-READ, never re-emit. The B gate has to be re-runnable without
            # two esbmc invocations per region, or it is a number nobody checks
            # between sweeps -- which is how it came to be assembled by hand in
            # the first place. A region with no put.json is rc=1 here, i.e. it
            # never produced a PUT, which is exactly what the table should say.
            rec = json.load(open(j)) if os.path.exists(j) else {}
            rc = 0 if rec.get("file") else 1
            results.append((bench, unit, enc, piece, rc, rec, proj, region,
                            is_corpus, contract))
            continue
        print(f"\n--- {bench}.{unit} enc={encs} ---")
        p = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(p.stdout)
        sys.stdout.write(p.stderr)
        rec = json.load(open(j)) if os.path.exists(j) else {}
        results.append((bench, unit, enc, piece, p.returncode, rec, proj,
                        region, is_corpus, contract))

    print("\n" + "=" * 84)
    print("STAGE 4: certified region -> PUT with oracle")
    if stage4_recipe_version:
        print(f"  Stage-4 recipe                  : {stage4_recipe_version}")
    # THE CELL TRAVELS WITH THE TABLE. A run of the ARTEFACT cell may not be
    # quoted into the branch-coverage gate table and a run of the GATE cell may
    # not be quoted as the method's reach, so the table has to say which it is
    # rather than leaving the reader to remember the flags.
    cells, n_norun = cells_of(results)
    print(f"CELL: scope={args.scope} --solidity-max-tx={args.max_tx} "
          f"-> {', '.join(cells) if cells else 'no run recorded one'}"
          + (f", --auto-unwind {args.auto_unwind}" if args.auto_unwind else ""))
    if n_norun:
        print(f"   ({n_norun} certified region(s) in this arm produced no PUT "
              f"and therefore carry NO CELL. That is a missing measurement, "
              f"not a second cell, and it is deliberately not folded into the "
              f"line above -- doing so printed the MIXED-CELLS refusal over "
              f"tables whose emitted rows were all one cell.)")
    if len(cells) > 1:
        print("** MIXED CELLS IN ONE TABLE. These rows are not comparable and "
              "the table must not be quoted anywhere. **")
    print("=" * 84)
    print(f"{'benchmark':<28}{'unit':<16}{'enc':>7}{'rc':>4}"
          f"{'fuzz':>6}{'asserts':>9}  outcome")
    n_put = n_fuzz = n_oracle = n_both = n_concrete = 0
    for bench, unit, enc, piece, rc, rec, _proj, _region, _is_corpus, \
            _contract in results:
        encs = f"{enc}#{piece}" if piece else str(enc)
        st = rec.get("stats") or {}
        kind = rec.get("kind") or "put"
        fz, ar = st.get("fuzz_params", 0), st.get("asserts", 0)
        # PRINTED APART, for the same reason gate 3 counts them apart: a rung
        # under `if (_put_ok)` runs only when the call did not revert, and on
        # farming setDistributor 13 that branch is never taken on 256 draws
        # (measured with guard_probe.py against a RED control). `12` there was
        # 10 assertions and 2 pieces of decoration.
        gd = st.get("guarded_asserts", 0)
        ar_txt = f"{ar - gd}+{gd}c" if gd else str(ar)
        uncond = ar - gd
        if rc == 0 and kind == "concrete":
            n_concrete += 1
            outcome = "CONCRETE " + os.path.basename(rec.get("file", ""))
        elif rc == 0 and uncond > 0:
            n_put += 1
            n_fuzz += 1 if fz else 0
            n_oracle += 1 if uncond else 0
            n_both += 1 if (fz and uncond) else 0
            outcome = os.path.basename(rec.get("file", ""))
        elif rc == 0:
            outcome = "REFUSED: zero unconditional assertions"
        elif rc == 2:
            outcome = "REFUSED: " + str(rec.get("refused"))
        elif args.forge_only:
            # NOT "REFUSED". In --forge-only nothing was run, so a missing
            # put.json means this region has NEVER BEEN THROUGH STAGE 4 -- most
            # often because stage 2 certified it after the last emit sweep. The
            # earlier wording reported the emitter's verdict for a run that did
            # not happen, which is the same "prints a state it no longer has"
            # shape this file catches elsewhere, and it made 20 fresh regions
            # look like 20 refusals.
            outcome = "NOT EMITTED YET (no put.json; re-run without --forge-only)"
        else:
            outcome = "REFUSED (see log above)"
        print(f"{bench:<28}{unit:<16}{encs:>7}{rc:>4}{fz:>6}{ar_txt:>9}  "
              f"{outcome}")
    print()
    print(f"  PUTs emitted                     : {n_put} of {len(results)} "
          f"Stage-4 candidate row(s)")
    print(f"  Concrete replays emitted         : {n_concrete}")
    print(f"  ... of which carry FUZZ parameters: {n_fuzz}")
    print(f"  ... of which carry an ORACLE      : {n_oracle}")
    print(f"  ... of which carry BOTH           : {n_both}")
    print()
    print("  A PUT with no fuzz parameter is still a PUT -- it carries the")
    print("  certified region as an established entry state and the oracle --")
    print("  but it is ONE point of the region, not a fuzz test over it, and")
    print("  the two must not be added together.")
    print()
    print("  ⚠ NONE of the four counters above is B. They are properties of the")
    print("  EMITTED TEXT; B additionally requires the test to be GREEN on the")
    print("  unmodified contract, which only forge can say. See the gate below.")

    emission_wall_s = round(time.monotonic() - main_start, 3)
    b_summary = b_report(results, args.forge_timeout)
    total_wall_s = round(time.monotonic() - main_start, 3)
    summary_path = os.path.join(OUT, "put-summary.json")
    with open(summary_path, "w") as stream:
        json.dump({
            "schema": "veriput-put-summary/1",
            "cert_path": os.path.abspath(cert_path),
            "only": args.only,
            "scope": args.scope,
            "max_tx": args.max_tx,
            "auto_unwind": args.auto_unwind,
            "auto_partial_loops": args.auto_partial_loops,
            "stage4_recipe_version": stage4_recipe_version,
            "stage2": stage2_accounting,
            "cell": {
                "labels": sorted(cells),
                "missing": n_norun,
                "mixed": len(cells) > 1,
            },
            "emission": {
                "stage4_candidate_rows": len(results),
                "certified_region_rows": len(results),
                "puts_emitted": n_put,
                "concrete_replays_emitted": n_concrete,
                "with_fuzz_parameters": n_fuzz,
                "with_oracle": n_oracle,
                "with_both": n_both,
            },
            "timing": {
                "generation_timeout_s": args.timeout,
                "forge_timeout_s_per_run": args.forge_timeout,
                "generation_wall_s": emission_wall_s,
                "emission_wall_s": emission_wall_s,
                "foundry_replay_wall_s":
                    b_summary.get("foundry_replay", {}).get("wall_s", 0.0),
                "total_wall_s": total_wall_s,
                "foundry_replay_outside_generation_timeout": True,
            },
            "deliverable_b": b_summary,
        }, stream, indent=2, sort_keys=True)
    print(f"\n  wrote machine-readable summary: {summary_path}")
    return 0


# ---- THE FIVE GATES, IN ONE PLACE, RUN BY THE SCRIPT ----
#
# WORKORDER's deliverable B is a CONJUNCTION of five conditions, and until now
# this script printed three of them (emitted / fuzz / oracle) while the other two
# -- bound width and `forge test` green -- were checked by hand, in a different
# command, and written into a message. That is the proxy-instead-of-deliverable
# shape: every number this script printed was true and none of them was B, so the
# B that got quoted was assembled by a human across two runs and could not be
# re-derived by re-running anything.
#
# MEASURED, and it is why this is not cosmetic: on the corpus, four of the seven
# emitted PUTs (aqua dock/push/rawBalances/safeBalances) carry FUZZ PARAMETERS and
# are GREEN, and all four have ZERO assertions -- their body is `try { ... } catch
# {}`, which cannot fail whatever the contract does. Counting "emitted + fuzz +
# green" would have reported 7; B is at most 3.
#
# WIDTH IS READ FROM THE REGION THIS SCRIPT ALREADY PARSED, not re-derived: a
# coordinate whose certified interval is a single point (lo == hi) is established,
# not fuzzed, so a `bound(x, v, v)` is a constant wearing a fuzz parameter's type.
# At least ONE fuzzed coordinate must have hi > lo or gate 2 fails.
def disable_red_replays(projects, forge_timeout):
    """Rename RED CONCRETE REPLAYS out of forge's `test*` prefix. Never a PUT.

    THE SAME SELF-CHECK GATE forge_roundtrip.py already runs, brought to stage 4
    because the two emitters were disagreeing about the same suite. MEASURED,
    farming/deposit: the roundtrip's project disabled both of its concrete cases
    as "RED on the unmodified contract", and stage 4's project shipped the same
    two cases enabled -- so one artefact said the replay was not ours to claim
    and the other handed it over.

    ⛔ A PUT IS NEVER DISABLED, and that is the whole reason this is a separate
    function rather than a copy of the roundtrip's loop. Disabling a red PUT
    would turn gate 4 from a measurement into a formality: the row would go green
    because the test stopped running. A red PUT stays red and is reported red.

    A concrete replay is different in kind: it asserts the exit the MODEL
    predicted, and the model gives an external call a nondet return, so it may
    choose an outcome the chain does not produce. That is a known, recorded gap
    -- not a defect in the region under test -- and the roundtrip already
    decided that such a case is kept, renamed, and not counted.
    """
    timing = {
        "runs": 0,
        "timeouts": 0,
        "wall_s": 0.0,
    }
    for proj in projects:
        _rc, stdout, _stderr, timed_out, wall_s = run_forge(
            proj, forge_timeout)
        timing["runs"] += 1
        timing["timeouts"] += 1 if timed_out else 0
        timing["wall_s"] += wall_s
        if timed_out:
            print(f"  [self-check] {os.path.basename(proj)}: forge timed out "
                  f"after {forge_timeout}s; no replay was disabled")
            continue
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            continue
        red = {}
        for suite, body in data.items():
            for name, res in (body.get("test_results") or {}).items():
                fn = name.split("(")[0]
                if res.get("status") == "Failure" and fn.startswith("test_cov"):
                    red.setdefault(suite.split(":")[0], []).append(fn)
        for path, fns in sorted(red.items()):
            # `path` is the source file forge reports for the suite.
            full = path if os.path.isabs(path) else os.path.join(proj, path)
            if not os.path.exists(full):
                print(f"  [self-check] cannot locate {path} under "
                      f"{os.path.basename(proj)} -- NOT disabling anything in "
                      f"it; the red test(s) {sorted(fns)} stay enabled and will "
                      f"be reported red")
                continue
            with open(full) as fh:
                txt = fh.read()
            changed = []
            for fn in sorted(fns):
                if fn.startswith("test_put_"):
                    print(f"  [self-check] {os.path.basename(full)}: {fn} is "
                          f"RED and is a PUT -- LEFT ENABLED on purpose. A red "
                          f"PUT is a failed gate 4, not a test to hide")
                    continue
                needle = f"  function {fn}() public {{"
                if needle not in txt:
                    print(f"  [self-check] {os.path.basename(full)}: {fn} is "
                          f"RED but its declaration was not found verbatim, so "
                          f"nothing was renamed -- it stays enabled")
                    continue
                txt = txt.replace(
                    needle,
                    f"  // DISABLED: RED on the unmodified contract, so its\n"
                    f"  // coverage is not ours to claim. Kept, renamed out of\n"
                    f"  // forge's `test*` prefix, so the artefact still shows\n"
                    f"  // what was generated. The PUT in this file is NOT\n"
                    f"  // affected: a red PUT is never disabled.\n"
                    f"  function disabled_{fn}() public {{")
                changed.append(fn)
            if changed:
                with open(full, "w") as fh:
                    fh.write(txt)
                print(f"  [self-check] {os.path.basename(full)}: disabled "
                      f"{len(changed)} red concrete replay(s): "
                      + ", ".join(changed))
    timing["wall_s"] = round(timing["wall_s"], 3)
    return timing


def b_report(results, forge_timeout):
    print()
    print("=" * 84)
    print("DELIVERABLE B — all five WORKORDER gates, per PUT")
    print("=" * 84)
    replay_timing = disable_red_replays(
        sorted({r[6] for r in results if r[6]}), forge_timeout)
    final_gate_wall_s = 0.0

    # forge, once per project, and the verdict per TEST FUNCTION. Running it per
    # row would recompile the benchmark flat once per region (70-180 KB each),
    # and a per-row run cannot see a failure caused by two PUTs sharing a project.
    verdicts = {}
    forge_seen = {
        "put": {"Success": 0, "Failure": 0, "other": 0},
        "concrete": {"Success": 0, "Failure": 0, "other": 0},
    }
    for proj in sorted({r[6] for r in results if r[6]}):
        _rc, stdout, stderr, timed_out, wall_s = run_forge(proj, forge_timeout)
        replay_timing["runs"] += 1
        replay_timing["timeouts"] += 1 if timed_out else 0
        replay_timing["wall_s"] += wall_s
        final_gate_wall_s += wall_s
        if timed_out:
            print(f"  [forge] {os.path.basename(proj)}: timed out after "
                  f"{forge_timeout}s; every row in this project is UNKNOWN")
            continue
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # NAMED, not swallowed. A project that does not compile makes every
            # one of its rows UNKNOWN, and an UNKNOWN must never read as a pass.
            print(f"  [forge] {os.path.basename(proj)}: could NOT parse `forge "
                  f"test --json` output -- every row in this project is UNKNOWN "
                  f"below, which is NOT a pass. First 200 chars of stderr: "
                  f"{stderr[:200]!r}")
            continue
        for suite in data.values():
            for name, res in (suite.get("test_results") or {}).items():
                fn = name.split("(")[0]
                status = res.get("status")
                verdicts[fn] = status
                bucket = None
                if fn.startswith("test_put_"):
                    bucket = "put"
                elif fn.startswith("test_cov_"):
                    bucket = "concrete"
                if bucket:
                    key = status if status in ("Success", "Failure") else "other"
                    forge_seen[bucket][key] += 1

    print(f"{'benchmark':<24}{'unit':<16}{'enc':>7}  "
          f"{'1.fuzz':>7}{'2.width':>8}{'3.assert':>9}{'4.green':>8}"
          f"{'5.corpus':>9}  B")
    b = 0
    n_stale = 0
    n_refused = 0
    row_summaries = []
    # ONE identity for the whole table: asking git and stat per row would let
    # a mid-report rebuild split the table's own notion of "this tree".
    _now_binary = current_binary_identity()
    print(f"  this tree: head={_now_binary['head']} "
          f"srcDirty={_now_binary['srcDirty']} "
          f"binaryMtime={_now_binary['binaryMtime']}")
    valid_reference_tests = {"total": 0, "put": 0, "concrete": 0}
    stage2_sources = Counter()
    for bench, unit, enc, piece, rc, rec, proj, region, is_corpus, \
            contract_name in results:
        # SAME shape the emitter builds, and the reason it is here rather than
        # inferred: a lookup that cannot match is a gate that never fires, and
        # it fails in the direction that LOOKS like caution ('?') while
        # reporting nothing. This file has already been bitten by exactly that,
        # two lines down, when the name was built from the benchmark key.
        plabel = f"p{piece}" if piece else ""
        encs = f"{enc}#{piece}" if piece else str(enc)
        st = rec.get("stats") or {}
        kind = rec.get("kind") or "put"
        stage2_source = rec.get("stage2_source") or "certified_region"
        stage2_sources[stage2_source] += 1
        fz, ar = st.get("fuzz_params", 0), st.get("asserts", 0)
        # ---- GATE 3 COUNTS UNCONDITIONAL ASSERTIONS ONLY ------------------
        #
        # The emitter records `guarded_asserts` apart from the total because a
        # rung under `if (_put_ok)` is weaker: it runs only when the call did
        # not revert. This reader was taking the TOTAL, so a PUT whose guard
        # is never true would pass gate 3 on decoration.
        #
        # ⛔ MEASURED, farming setDistributor enc=13: `guard_probe.py` replaced
        # the two guarded assertions with `assertTrue(false, ...)` and forge
        # stayed GREEN over 256 draws, while the same false assertion placed
        # OUTSIDE the guard went RED. The true branch is never taken -- the
        # region pins a sender outside the owner bound, so every draw reverts.
        # Those two are 0 assertions, not 2, and the file's own header already
        # said so (`ORACLE: 10` + `CONDITIONAL: 2 further`) while this table
        # printed 12. One fact, two ledgers, and only the writing side carried
        # the distinction.
        guarded = st.get("guarded_asserts", 0)
        uncond = ar - guarded
        # Gate 1 counts the parameters the emitter actually wrote. Gate 2 asks
        # whether ANY coordinate the emitted test RENDERS is wider than a
        # point. Reading this from `stats` rather than from the raw region is
        # essential in both directions: state-only region width is not rendered
        # as fuzz input, while a calldata parameter absent from the certified
        # region can be lifted over its full type domain because Stage 2 proved
        # the path with that input unconstrained.
        refused = rc != 0
        stale = stale_reason(rec, _now_binary) if rc == 0 else None
        if kind == "concrete":
            want = rec.get("test")
            status = verdicts.get(want)
            g4 = status == "Success"
            g5 = is_corpus
            generated_ok = g4 and g5 and not stale and not refused
            if generated_ok:
                valid_reference_tests["total"] += 1
                valid_reference_tests["concrete"] += 1
            row_summaries.append({
                "kind": "concrete",
                "stage2_source": stage2_source,
                "benchmark": bench,
                "unit": unit,
                "enc": enc,
                "piece": piece,
                "rc": rc,
                "test": want,
                "forge_status": status,
                "gates": {
                    "fuzz": False,
                    "width": None if not refused else None,
                    "assert": None if not refused else None,
                    "green": g4 if not (refused or status is None) else None,
                    "corpus": g5 if not refused else None,
                },
                "b": False,
                "valid_reference_test": generated_ok,
                "refused": refused,
                "stale": stale,
                "fuzz_params": fz,
                "asserts": ar,
                "guarded_asserts": guarded,
                "unconditional_asserts": uncond,
                "rendered_width": st.get("rendered_width") or {},
                "file": rec.get("file"),
            })
            def m_concrete(x, unknown=False):
                return "?" if unknown else ("yes" if x else "NO")
            print(f"{bench:<24}{unit:<16}{encs:>7}  "
                  f"{'n/a':>7}{'n/a':>8}{'n/a':>9}"
                  f"{m_concrete(g4, refused or status is None):>8}"
                  f"{m_concrete(g5, refused):>9}  CONCRETE")
            if stale:
                print(f"      ⛔ NOT COUNTED: {stale}")
                n_stale += 1
            if refused:
                n_refused += 1
            continue

        # A PUT with zero unconditional assertions is not a deliverable PUT,
        # even if the emitter wrote a Foundry file and exited 0.  Treat it like
        # a refusal so raw accounting does not count a green no-oracle test.
        refused = refused or uncond <= 0
        g1 = rc == 0 and not refused and fz > 0
        g2 = any(width > 1 for width in (st.get("rendered_width") or {}).values())
        g3 = uncond > 0
        # The PUT's own test function, named by the emitter as
        # `test_put_<CONTRACT>_<unit>_path<enc>`; the concrete `test_cov_*` cases
        # in the same file are NOT the deliverable and must not decide this gate.
        #
        # THE CONTRACT, never the benchmark key. The first version of this line
        # built the name from `bench.split('_')[-1]`, which is 'Aqua' for
        # `aqua_Aqua` and 'farming' for `farming` -- so every farming row looked
        # up `test_put_farming_...` against an emitter that wrote
        # `test_put_FarmingPool_...` and came back '?'. A matcher that cannot
        # match is a gate that never fires, and it fails in the direction that
        # LOOKS like caution ('unknown') while actually reporting nothing.
        contract = contract_name or (BENCHES[bench][1]
                                     if bench in BENCHES else bench)
        legacy_piece = f"p{piece}" if piece else ""
        want = rec.get("test") or (
            f"test_put_{contract}_{unit}_path{enc}{legacy_piece}")
        status = verdicts.get(want)
        g4 = status == "Success"
        # Gate 5 is a property of the INPUT, not of the run: a hand-written PoC
        # satisfies every other gate and is still not B. Rows reaching this table
        # from the corpus sweep are corpus by construction; the PoC sweep sets
        # `is_poc` and is reported in its own table, so this is recorded rather
        # than inferred -- and it stays visible so a future shared table cannot
        # silently mix them.
        g5 = is_corpus
        # ---- GATE 0: WAS THIS ARTEFACT PRODUCED BY THIS TREE? ----
        # Not one of WORKORDER's five, and deliberately not folded into them: a
        # stale row's other five gates were measured by a build that no longer
        # exists, so reporting them as NO would be as wrong as reporting them as
        # yes. It is excluded from B and named.
        # ---- rc != 0 MEANS THIS TREE PRODUCED NO ARTEFACT FOR THIS ROW ------
        #
        # `rec` is the put.json ON DISK and the .t.sol is the one still sitting
        # in the project -- on a REFUSED emission both are the PREVIOUS run's.
        # Only gate 1 was guarded by `rc`; gates 2-5 read them anyway, and the
        # staleness check itself was gated on `rc == 0`, so a REFUSED row
        # printed
        #     1.fuzz NO   2.width yes   3.assert yes   4.green yes   5.corpus yes
        # while the summary said `0 row(s) were EXCLUDED as STALE`. Four gates
        # asserted about an artefact this run did not write, and the one line
        # that exists to catch exactly that was switched off for the rows that
        # needed it.
        #
        # MEASURED: the farming re-emit in which all three regions REFUSED on
        # `forge inspect: storage layout missing from artifact`. Nothing was
        # emitted and the table read as three nearly-passing rows.
        #
        # UNKNOWN, not NO: the gates were not evaluated against anything this
        # tree produced, so reporting them as failures would be as wrong as
        # reporting them as passes. Either way they are not B.
        ok = g1 and g2 and g3 and g4 and g5 and not stale and not refused
        b += 1 if ok else 0
        if ok:
            valid_reference_tests["total"] += 1
            valid_reference_tests["put"] += 1
        row_summaries.append({
            "kind": "put",
            "stage2_source": stage2_source,
            "benchmark": bench,
            "unit": unit,
            "enc": enc,
            "piece": piece,
            "rc": rc,
            "test": want,
            "forge_status": status,
            "gates": {
                "fuzz": g1,
                "width": g2 if not refused else None,
                "assert": g3 if not refused else None,
                "green": g4 if not (refused or status is None) else None,
                "corpus": g5 if not refused else None,
            },
            "b": ok,
            "valid_reference_test": ok,
            "refused": refused,
            "stale": stale,
            "fuzz_params": fz,
            "asserts": ar,
            "guarded_asserts": guarded,
            "unconditional_asserts": uncond,
            "rendered_width": st.get("rendered_width") or {},
            "file": rec.get("file"),
        })
        def m(x, unknown=False):
            return "?" if unknown else ("yes" if x else "NO")
        print(f"{bench:<24}{unit:<16}{encs:>7}  "
              f"{m(g1):>7}{m(g2, refused):>8}{m(g3, refused):>9}"
              f"{m(g4, refused or status is None):>8}{m(g5, refused):>9}  "
              + ("**B**" if ok else
                 ("REFUSED" if refused else ("STALE" if stale else ""))))
        if refused:
            if rc == 0:
                print("      ⛔ NOT COUNTED: this PUT has zero unconditional "
                      "assertions, so it is a no-oracle artifact even though "
                      "the emitter wrote a Foundry test.")
            else:
                print(f"      ⛔ NOT COUNTED: the emitter exited {rc} and "
                      "wrote no PUT in this tree, so gates 2-5 above are "
                      "UNKNOWN -- they would have been read off the PREVIOUS "
                      "run's put.json and the .t.sol still on disk. See the "
                      "per-region log for the refusal.")
            n_refused += 1
        if stale:
            print(f"      ⛔ NOT COUNTED: {stale}")
            n_stale += 1
    print()
    print(f"  B = {b} of {len(results)} Stage-4 candidate row(s)")
    print(f"  Forge-visible PUT test functions: "
          f"{forge_seen['put']['Success']} green / "
          f"{sum(forge_seen['put'].values())} total")
    print(f"  Forge-visible concrete replays  : "
          f"{forge_seen['concrete']['Success']} green / "
          f"{sum(forge_seen['concrete'].values())} total")
    print(f"  Reference-valid generated tests : "
          f"{valid_reference_tests['total']} total "
          f"({valid_reference_tests['put']} PUT, "
          f"{valid_reference_tests['concrete']} concrete)")
    print(f"  {n_refused} row(s) were EXCLUDED as REFUSED -- the emitter "
          f"produced no deliverable PUT for them in this tree, so nothing in "
          f"their row was measured here. They are UNKNOWN, not failures.")
    print(f"  {n_stale} row(s) were EXCLUDED as STALE -- their put.json names a "
          f"different executable, so nothing about them was measured by this "
          f"tree. Re-emit (drop --forge-only) to bring them back.")
    print("  A row failing gate 4 with '?' was never seen by forge -- that is an")
    print("  UNKNOWN, not a failure, and it is not counted toward B either.")
    return {
        "b": b,
        "stage4_candidate_rows": len(results),
        "certified_region_rows": len(results),
        "forge_seen": forge_seen,
        "refused": n_refused,
        "stale": n_stale,
        "valid_reference_tests": valid_reference_tests,
        "stage2_source_counts": dict(stage2_sources),
        "foundry_replay": {
            "outside_generation_timeout": True,
            "timeout_s_per_run": forge_timeout,
            "runs": replay_timing["runs"],
            "timeouts": replay_timing["timeouts"],
            "wall_s": round(replay_timing["wall_s"], 3),
            "self_check_wall_s": round(
                replay_timing["wall_s"] - final_gate_wall_s, 3),
            "final_gate_wall_s": round(final_gate_wall_s, 3),
        },
        "rows": row_summaries,
    }


if __name__ == "__main__":
    sys.exit(main())
