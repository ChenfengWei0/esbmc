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
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


def ensure_foundry_tools_on_path():
    path = os.environ.get("PATH", "")
    dirs = path.split(os.pathsep) if path else []
    candidates = [
        os.path.expanduser("~/.foundry/bin"),
        os.path.expanduser("~/.local/bin"),
    ]
    extra = [d for d in candidates if d not in dirs and os.path.exists(os.path.join(d, "forge"))]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + dirs)


ensure_foundry_tools_on_path()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(
    os.environ.get("ESBMC_REPO") or os.environ.get("ESBMC_ROOT")
    or os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
from solidity_ast_dependencies import (  # noqa: E402
    path_function_artifact_suffix, path_function_declaration_id, unit_callable_facts,
    unit_contains_inline_assembly, unit_env_dependencies, unit_state_dependencies)
from solidity_path_put import (  # noqa: E402
    _concrete_return_literal, _public_state_getter_decl, _source_type_default_expr,
    authenticated_concrete_oracle_error, bind_return_lhs, find_unit_call,
    flatten_rendered_aggregate, public_state_getter_signature, split_top_level)
from veriput_subjects import (
    SubjectError,
    enumerate_subject_units,  # noqa: E402
    subject_from_record)
from veriput_path_guard import ensure_path_not_protected  # noqa: E402
from veriput_recipe import (
    STRONG_RECIPE_VERSION,
    STRONG_PUT_AUTO_UNWIND,  # noqa: E402
    STRONG_PUT_AUTO_PARTIAL_LOOPS,
    STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET,
    STRONG_PUT_PROOF_ESBMC_ARGS,
    STRONG_PUT_FUZZ_RUNS,
    STRONG_PUT_LIFT_UNCONSTRAINED_CALLDATA,
    STRONG_PUT_LIFT_UNCONSTRAINED_SENDER,
    STRONG_PUT_R2_CANDIDATE_BUDGET,
    STRONG_PUT_R2_DEPTH,
    STRONG_PUT_R2_TERM_BUDGET,
    is_structural_certificate_row,
)

NOTES = os.path.abspath(os.path.join(HERE, "..", ".."))
INPUTS = os.path.join(NOTES, "coverage", "inputs")
CERT = os.path.join(NOTES, "coverage", "certify", "results.jsonl")
# The PoC set's own stage-2 sweep. Two files rather than one because they are
# two different questions: on a real contract "not certified" mixes the
# method's limits with the contract's difficulty, while a PoC is one shape and
# nothing else. Which file a row came from therefore travels with the row.
POC_CERT = os.path.join(NOTES, "coverage", "certify", "poc_results.jsonl")
POC_SRC = os.path.join(NOTES, "coverage", "poc")
RQ3_NO_CER_REG_ROOT = os.path.abspath(
    os.environ.get("VERIPUT_NO_CER_REG_ROOT")
    or os.path.join(os.environ.get("VERIPUT_ROOT", os.getcwd()), "Results", "RQ3",
                    "VeriExploit", "No_Cer_Reg"))
OUT = os.path.join(NOTES, "coverage", "put_roundtrip")
ESBMC = os.path.join(REPO, "build", "src", "esbmc", "esbmc")
PUT = os.path.abspath(
    os.environ.get("VERIPUT_PUT_DRIVER")
    or (os.path.join(HERE, "solidity_path_put.py")
        if os.path.isfile(os.path.join(HERE, "solidity_path_put.py")) else os.path.join(
            REPO, "scripts", "solidity_path_put.py")))
FORGE_STD = os.path.abspath(
    os.environ.get("VERIPUT_FORGE_STD") or os.environ.get("FORGE_STD")
    or os.path.join(REPO, "notes", "coverage-comparison", "_foundry_roundtrip", "aqua_forge", "lib",
                    "forge-std"))

# benchmark key -> (flat basename, contract under test)
BENCHES = {
    "aqua_Aqua": ("aqua__Aqua.flat.sol", "Aqua"),
    "cross_chain_swap_EscrowSrc": ("cross-chain-swap__EscrowSrc.flat.sol", "EscrowSrc"),
    "cross_chain_swap_EscrowDst": ("cross-chain-swap__EscrowDst.flat.sol", "EscrowDst"),
    "farming": ("farming__FarmingPool.flat.sol", "FarmingPool"),
    "limit_order_protocol": ("limit-order-protocol__MakerTraitsLib.flat.sol", "MakerTraitsLib"),
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
    "PIN-EXCLUDED-NO-COORDINATE",
    "NOT-CERTIFIED-CE-FALLBACK",
    "PARTIAL-WITNESS-JOURNAL-CE",
    "UNKNOWN",
}
CONCRETE_ONLY_STAGE2_SOURCES = {
    "cleared-concrete-fallback": "cleared_not_certified_fallback",
    "timeout-concrete-fallback": "timeout_concrete_fallback",
    "partial-journal-concrete-fallback": "partial_journal_concrete_fallback",
    "no-coordinate-concrete-fallback": "no-coordinate-concrete-fallback",
    "certified-region-concrete-fallback": "certified-region-concrete-fallback",
    "structural-getter-only": "structural_getter_only",
    "structural-deploy-only": "structural_deploy_only",
    "source-guard-concrete-fallback": "source_guard_revert_only",
}
STRUCTURAL_STAGE2_SOURCE_ALIASES = {
    "structural-abi-getter-no-coordinate": "structural-getter-only",
    "structural_getter_only": "structural-getter-only",
    "structural-getter-only": "structural-getter-only",
    "structural-deploy-only-no-unit": "structural-deploy-only",
    "structural_deploy_only": "structural-deploy-only",
    "structural-deploy-only": "structural-deploy-only",
}
CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE = \
    "certified-region-concrete-fallback"
CERTIFIED_REGION_CONCRETE_FALLBACK_REFUSALS = {
    "build-put-refused",
    "no-oracle",
    "no-assertions",
    "path-depth-unavailable",
    "zero-assertions",
    # ---- A TRUNCATED LADDER IS A BOUND'S LIMIT, NOT THE REGION'S ----------
    #
    # `ladder-undecided-truncated` is the driver's own verdict that a loop was
    # cut at the unwind bound while unwinding assertions were disabled, and it
    # states in the same breath: "This is NOT a vacuous region and must not be
    # recorded as one: the region may be perfectly good and the BOUND is what
    # could not see it." The region was CERTIFIED and Stage 4 had already
    # authenticated and selected a concrete case; only the ORACLE LADDER came
    # back undecided. That is the same shape as `build-put-refused` -- the PUT
    # builder could not render a deliverable oracle -- so the same salvage
    # applies: emit the concrete replay, which asserts the observed behaviour
    # of a witnessed input and needs no ladder verdict at all. Foundry still
    # has to accept it before it counts.
    #
    # MEASURED: PoolPauseHelper (real203, full-20260822-v40) -- every unit is a
    # plain getter, `owner` CERTIFIES in 9s, and the whole subject was recorded
    # `no-valid` with "Concrete replays emitted: 0" because each unit's ladder
    # truncated and no row reached this salvage. A certified getter that yields
    # not even a concrete replay is a pipeline gap, not a property of the
    # contract.
    #
    # ⛔ `ladder-vacuous` is deliberately NOT here. Vacuous means no execution
    # the region admits walks the path, so a replay built on it would stand for
    # an execution that does not exist -- the one case this salvage must not
    # cover.
    "ladder-undecided-truncated",
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


def row_exit_kind(row, detail=None):
    """Explicit path-exit metadata from Stage 2, never subprocess return codes."""
    detail = detail if isinstance(detail, dict) else {}
    for source in (detail, row if isinstance(row, dict) else {}):
        kind = normalize_exit_kind(source.get("exit_kind"))
        if kind in ("normal", "revert", "unknown"):
            return kind
    return None


def parse_certified(text):
    region, holes = {}, {}
    for m in INTERVAL_RE.finditer(text):
        region[m.group(1)] = [int(m.group(2)), int(m.group(3))]
        if m.group(4):
            holes[m.group(1)] = sorted({int(v) for v in m.group(4).split(",") if v.strip()})
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


def parse_concrete_ce(raw):
    if not isinstance(raw, dict):
        return None
    out = {}
    for key, value in raw.items():
        if not str(key):
            continue
        try:
            out[str(key)] = int(str(value), 0)
        except (TypeError, ValueError):
            continue
    return out


def concrete_fallback_region_and_pins(coords, row_pins, ce):
    """Concrete replay slice from Stage-2 witness data, or None on conflict.

    The generalised coordinates remain point regions.  Every other integer
    counterexample coordinate except `return` is a concrete pin: it is part of
    the witnessed pre-state/input slice, not a verifier proof.  If the row-level
    pins disagree with the witness, the row is stale or malformed, so Stage 4
    must not emit a replay under a different point.
    """
    region = {}
    pins = dict(row_pins or {})
    coord_set = {str(c) for c in (coords or [])}
    for coord in coord_set:
        if coord not in ce:
            continue
        value = ce[coord]
        if coord in pins and pins[coord] != value:
            return None
        region[coord] = [value, value]
        pins.pop(coord, None)
    for name, value in sorted(ce.items()):
        if name == "return" or name in region:
            continue
        if name in pins and pins[name] != value:
            return None
        pins[name] = value
    return region, pins


def parse_certified_detail_region(details, row_pins):
    """Structured region from certify_all's machine-readable detail row."""

    if not isinstance(details, dict) or "box" not in details:
        return None, None, None
    region, holes = {}, {}
    for item in details.get("box") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            name = str(item["name"])
            region[name] = [int(str(item["lo"]), 0), int(str(item["hi"]), 0)]
            hs = [int(str(v), 0) for v in (item.get("holes") or [])]
        except (KeyError, TypeError, ValueError):
            return None, None, None
        if hs:
            holes[name] = sorted(set(hs))
    pins = {k: v for k, v in (row_pins or {}).items() if k not in region}
    if details.get("certification_source") == \
            "structural-abi-gate-no-coordinate":
        reject_gate = ("msg.value" in region and region["msg.value"][0] > 0)
        if reject_gate:
            # The compiler rejects before constructor-derived state can be
            # observed. Carrying state pins into Stage 4 can only make an
            # exact ABI-gate certificate unrenderable (notably for immutables
            # and signed fields); it cannot narrow this path.
            pins = {}
        else:
            pins = {k: v for k, v in pins.items() if k == "msg.value" or k.startswith("state.")}
    return region, holes, pins


STRUCTURAL_GETTER_SENDER_MAX = (1 << 160) - 1


def _fixed_public_array_getter_length(flat_source, contract, unit):
    """Return a constructor-fixed public array length, or None."""
    mask = _solidity_code_mask(flat_source)
    contracts = list(
        re.finditer(r"\b(?:abstract\s+)?contract\s+" + re.escape(contract) + r"\b[^\{;]*\{", mask))
    if len(contracts) != 1:
        return None
    contract_open = mask.find("{", contracts[0].start())
    contract_close = _matching_delimiter(mask, contract_open, "{", "}")
    if contract_close is None:
        return None
    mask = mask[contract_open + 1:contract_close]
    declaration = re.compile(r"\b[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*\s*\[\s*\]\s+public\s+" +
                             re.escape(unit) + r"\s*;")
    if len(list(declaration.finditer(mask))) != 1:
        return None
    constructors = list(re.finditer(r"\bconstructor\s*\([^)]*\)[^{;]*\{", mask))
    if len(constructors) != 1:
        return None
    opening = mask.find("{", constructors[0].start())
    closing = _matching_delimiter(mask, opening, "{", "}")
    if closing is None:
        return None
    pushes = list(re.finditer(r"\b" + re.escape(unit) + r"\s*\.\s*push\s*\(", mask))
    if not pushes or any(not (opening < match.start() < closing) for match in pushes):
        return None
    forbidden = (r"\b" + re.escape(unit) + r"\s*(?:\.\s*pop\s*\(|\.\s*length\s*=|\[.*?\]\s*=|=)")
    if re.search(forbidden, mask):
        return None
    return len(pushes)


def widen_structural_getter_sender_region(row_subject, unit, path_function, details, region, holes,
                                          pins):
    """Expose an executable caller coordinate for a sender-independent getter.

    ``structural-abi-gate-no-coordinate`` certificates describe a legal
    non-payable ABI entry whose only complete-path gate is ``msg.value == 0``.
    For a zero-argument getter whose source dependency closure reads no
    ``msg/tx/block`` value, ``msg.sender`` is genuinely unconstrained by that
    certificate.  Foundry can execute that coordinate, but the old Stage 4
    handoff dropped it and classified the otherwise oracle-backed artifact as
    concrete.  Widen only this narrow, source-checked shape; ordinary regions
    and sender-dependent getters keep their original coordinates.
    """
    if not isinstance(details, dict) or details.get("certification_source") not in (
            "structural-abi-gate-no-coordinate", "structural-abi-getter-no-coordinate"):
        return region, holes, pins, None
    if row_subject is None or not row_subject.solast:
        return region, holes, pins, None
    declaration_id = path_function_declaration_id(path_function)
    getter_signature = public_state_getter_signature(row_subject.solast, row_subject.contract, unit)
    if getter_signature is not None:
        if getter_signature[0]:
            declaration = _public_state_getter_decl(row_subject.solast, row_subject.contract, unit)
            type_node = (declaration or {}).get("typeName") or {}
            mapping_levels = 0
            while type_node.get("nodeType") == "Mapping":
                mapping_levels += 1
                type_node = type_node.get("valueType") or {}
            if type_node.get("nodeType") == "ArrayTypeName":
                try:
                    flat_source = Path(row_subject.flat_sol).read_text(encoding="utf-8")
                except OSError:
                    return region, holes, pins, None
                fixed_length = _fixed_public_array_getter_length(flat_source, row_subject.contract,
                                                                 unit)
                if fixed_length is None or len(getter_signature[0]) != mapping_levels + 1:
                    return region, holes, pins, None
                region = dict(region or {})
                region[getter_signature[0][-1][0]] = [0, fixed_length - 1]
            elif mapping_levels != len(getter_signature[0]):
                return region, holes, pins, None
    else:
        facts, _evidence = unit_callable_facts(row_subject.solast,
                                               row_subject.contract,
                                               unit,
                                               declaration_id=declaration_id)
        if not isinstance(facts, dict) or facts.get("parameters"):
            return region, holes, pins, None
        env_deps, _env_evidence = unit_env_dependencies(row_subject.solast,
                                                        row_subject.contract,
                                                        unit,
                                                        declaration_id=declaration_id)
        if env_deps != []:
            return region, holes, pins, None
    if "msg.sender" in region or "msg.sender" in pins:
        return region, holes, pins, None
    widened = dict(region or {})
    widened["msg.sender"] = [1, STRUCTURAL_GETTER_SENDER_MAX]
    widened_holes = {name: list(values) for name, values in (holes or {}).items()}
    widened_pins = dict(pins or {})
    derivation = {
        "kind": "structural-getter-unconstrained-sender",
        "coordinate": "msg.sender",
        "lo": 1,
        "hi": STRUCTURAL_GETTER_SENDER_MAX,
        "source": "structural-abi-gate-no-coordinate",
        "dependency_check": "unit_env_dependencies == []",
        "unit_parameters": len(getter_signature[0]) if getter_signature is not None else 0,
    }
    return widened, widened_holes, widened_pins, derivation


def ensure_row_subject_solast(row_subject, log=print):
    if row_subject is None or not row_subject.solast:
        return False
    if os.path.exists(row_subject.solast):
        return True
    if log:
        log("prepared subject AST is missing; refusing to regenerate without "
            "a recorded flat.sol/solc/solast identity match")
    return False


# ---- ONE SHAPE-LEVEL REFUSAL PER UNIT, NOT ONE PER PATH ----
#
# A cleared (never-certified) concrete fallback is synthesized from source
# declarations alone: same deployment, same call, arguments filled with
# deterministic placeholders. Nothing in that shape depends on the path id, so
# a refusal that judges the SHAPE -- "the replay carries no oracle" -- is the
# same verdict for every remaining path of the same unit.
#
# MEASURED, full-20260822-v28: MStableYieldSource paid 111.7 s over SIX
# approveMax rows and 171.3 s over FIVE supplyTokenTo rows to be told "concrete
# replay lacks structured witness oracle provenance" eleven times, emitting no
# file at all; VaultAdapter paid 92.8 s over five setSlopes rows. The first row
# of a unit always runs, and a row that is NOT refused never arms this, so a
# unit whose fallback does carry an oracle keeps every path it had.
SHAPE_LEVEL_CONCRETE_REFUSALS = (
    "concrete replay lacks structured witness oracle provenance", )

CLEARED_FALLBACK_SOURCES = (
    "cleared-concrete-fallback",
    "cleared_not_certified_fallback",
    "no-coordinate-concrete-fallback",
    "no_coordinate_concrete_fallback",
)


def shape_level_concrete_refusal_reason(stage2_source, rec):
    """The reason to stop asking this unit's cleared fallbacks, or None.

    Deliberately narrow: only a row that produced NO file and was refused for
    one of the enumerated shape-level reasons arms it. A path-specific refusal,
    a row that emitted a file, and any certified source must all be asked again
    for the next path.
    """
    if stage2_source not in CLEARED_FALLBACK_SOURCES:
        return None
    if not isinstance(rec, dict) or rec.get("file"):
        return None
    reason = str(rec.get("concrete_reason") or "")
    return reason if reason in SHAPE_LEVEL_CONCRETE_REFUSALS else None


def cleared_concrete_fallback_rows(record):
    """Stage-2 NOT_CERTIFIED paths whose concrete replay is authenticated.

    This is intentionally narrower than `concrete_fallback=true`: FAILED
    witnesses are still rejected.  UNKNOWN is accepted only as concrete replay
    evidence; it never becomes a PUT or a certified region.
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
    journal = record.get("partial_witness_journal") or {}
    journal_path_functions = {}
    if isinstance(journal, dict):
        for path in journal.get("paths") or []:
            if not isinstance(path, dict):
                continue
            path_id = path.get("path_id")
            path_function = path.get("path_function")
            if path_id is not None and path_function:
                journal_path_functions[str(path_id)] = str(path_function)
    coords = [str(c) for c in (record.get("coords") or [])]
    row_pins = parse_pins(record.get("pins"))
    for enc, reason in not_certified.items():
        detail = by_enc.get(str(enc)) or {}
        if detail.get("concrete_fallback") is not True:
            continue
        witness_check = detail.get("witness_check")
        pin_excluded = (witness_check == "PIN-EXCLUDED-NO-COORDINATE"
                        or "EXCLUDED FROM THE SLICE by the pins" in str(reason)
                        or "EXCLUDED FROM THE SLICE by the pins" in str(detail.get("reason") or ""))
        if witness_check not in CONCRETE_FALLBACK_WITNESS_CHECKS:
            if not (witness_check is None and pin_excluded):
                continue
        ce = parse_concrete_ce(detail.get("ce") or {})
        if ce is None:
            continue
        # A pin-excluded witness is intentionally outside the generalized
        # slice. Its row pin is the rejected slice boundary, not the concrete
        # replay state, so let the CE supply the environment pins.
        if pin_excluded:
            fallback_pins = {}
        else:
            fallback_pins = row_pins
        region_and_pins = concrete_fallback_region_and_pins(coords, fallback_pins, ce)
        if region_and_pins is None:
            continue
        region, pins = region_and_pins
        rows.append({
            "enc":
            str(enc),
            "path_function": (detail.get("path_function") or journal_path_functions.get(str(enc))
                              or record.get("path_function")),
            "region":
            region,
            "pins":
            pins,
            "reason":
            reason,
            "detail":
            detail,
        })
    return rows


def authenticated_pin_excluded_fallback_rows(record):
    """Return only the authenticated replay outside a pinned slice.

    A ``PIN-EXCLUDED-NO-COORDINATE`` row is different from the optional
    best-effort fallback sweep: Stage 2 already retained a concrete witness
    and explicitly marked its witness check. RQ1 must consume that witness
    even when the caller did not request the broader ``--emit-cleared...``
    family. The helper is deliberately narrow so timeout and partial-journal
    fallbacks remain opt-in and cannot silently become reference tests.
    """
    return [
        row for row in cleared_concrete_fallback_rows(record)
        if ((row.get("detail") or {}).get("witness_check") == "PIN-EXCLUDED-NO-COORDINATE"
            or "EXCLUDED FROM THE SLICE by the pins" in str(row.get("reason") or "")
            or "EXCLUDED FROM THE SLICE by the pins" in str(
                (row.get("detail") or {}).get("reason") or ""))
    ]


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
    no_report = (diagnostic.get("tag") == "esbmc-no-cov-report"
                 or diagnostic.get("category") == "no-cov-report")
    return (str(record.get("bucket") or "").upper() == "KILLED" and record.get("witnessed") is None
            and no_report)


def occupied_stage2_path_ids(record):
    """Path ids already consumed by Stage 2 certified/not-certified rows."""
    occupied = set()
    for mapping_name in ("certified", "not_certified"):
        mapping = record.get(mapping_name) or {}
        if not isinstance(mapping, dict):
            continue
        for key in mapping:
            enc = claim_path_id_int(key)
            if enc is not None:
                occupied.add(enc)
    for mapping_name in ("certified_details", "not_certified_details"):
        details = record.get(mapping_name) or {}
        if isinstance(details, dict):
            iterable = details.items()
        elif isinstance(details, list):
            iterable = enumerate(details)
        else:
            continue
        for key, detail in iterable:
            if isinstance(detail, dict) and detail.get("enc") is not None:
                enc = claim_path_id_int(detail.get("enc"))
            else:
                enc = claim_path_id_int(key)
            if enc is not None:
                occupied.add(enc)
    return occupied


def stage2_witness_return(record, enc, path_function, certified_detail):
    """Return an unambiguous Stage-2 return witness for one exact path."""
    journal = record.get("partial_witness_journal") or {}
    if not isinstance(journal, dict):
        return None
    wanted_enc = claim_path_id_int(enc)
    if (wanted_enc is None or not path_function or not isinstance(certified_detail, dict)):
        return None
    detail_ce = certified_detail.get("ce") or {}
    if not isinstance(detail_ce, dict):
        return None
    detail_region, detail_holes, detail_pins = parse_certified_detail_region(
        certified_detail, parse_pins(record.get("pins")))
    if detail_region is None or detail_holes is None or detail_pins is None:
        return None
    values = []
    for path in journal.get("paths") or []:
        if not isinstance(path, dict):
            continue
        if claim_path_id_int(path.get("path_id")) != wanted_enc:
            continue
        if str(path.get("path_function") or "") != str(path_function):
            continue
        try:
            if int(path.get("witness_count") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        ce = path.get("ce") or {}
        if not isinstance(ce, dict) or ce.get("return") is None:
            continue
        # The journal carries a state aggregate as ONE rendered literal; the
        # certified detail carries its scalar leaves (`state.X.length`,
        # `state._accumulator.latestVersion`). Flatten the journal's
        # aggregates the way Stage 2 did, and let a flattened aggregate stand
        # for itself. MEASURED on Product.latestVersion (full-20260822-v32):
        # the exact return witness `return: 0` was in the journal and this
        # function returned None on sixteen such names, so the basis replay
        # ran without --concrete-return-value and was refused.
        flat_ce = dict(ce)
        aggregate_names = set()
        for name, value in ce.items():
            if name == "return":
                continue
            leaves = flatten_rendered_aggregate(value, str(name) + ".")
            if leaves:
                aggregate_names.add(name)
                for leaf, leaf_value in leaves.items():
                    flat_ce.setdefault(leaf, leaf_value)
        required_detail_coords = {
            name: value
            for name, value in detail_ce.items() if name != "return"
        }
        if any(name not in flat_ce or str(flat_ce[name]) != str(value)
               for name, value in required_detail_coords.items()):
            continue
        point_matches = True
        for name, value in flat_ce.items():
            if name == "return" or name in aggregate_names:
                continue
            if name not in detail_ce and name not in detail_region and name not in detail_pins:
                point_matches = False
                break
            if name in detail_region:
                try:
                    numeric = int(str(value), 0)
                except ValueError:
                    point_matches = False
                    break
                lo, hi = detail_region[name]
                if numeric < lo or numeric > hi:
                    point_matches = False
                    break
                if numeric in set(detail_holes.get(name) or []):
                    point_matches = False
                    break
            elif name in detail_pins and str(detail_pins[name]) != str(value):
                point_matches = False
                break
        if not point_matches:
            continue
        value = ce["return"]
        if value not in values:
            values.append(value)
    return values[0] if len(values) == 1 else None


def timeout_concrete_fallback_rows(record):
    """Timed-out Stage-2 rows whose partial witness names replayable paths.

    These rows are not proofs and they do not provide a certified region.  They
    only keep a concrete replay opportunity when Stage 2 reached a path witness
    and then died in the final certification query.
    """
    if not cert_row_timed_out(record):
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
    occupied = occupied_stage2_path_ids(record)
    rows = []
    for path in journal.get("paths") or []:
        if not isinstance(path, dict):
            continue
        enc = claim_path_id_int(path.get("path_id"))
        path_function = path.get("path_function")
        if enc is None or not path_function:
            continue
        if enc in occupied:
            continue
        try:
            path_witnesses = int(path.get("witness_count") or 0)
        except (TypeError, ValueError):
            path_witnesses = 0
        if path_witnesses <= 0:
            continue
        ce = parse_concrete_ce(path.get("ce") or {})
        region, pins = {}, row_pins
        if ce is not None:
            region_and_pins = concrete_fallback_region_and_pins(
                record.get("coords") or [], row_pins, ce)
            if region_and_pins is not None:
                region, pins = region_and_pins
        rows.append({
            "enc": str(enc),
            "path_function": str(path_function),
            "region": region,
            "pins": pins,
            "reason": "Stage-2 certification timed out after witnessing path",
            "detail": {
                "ce": ce or {},
                "witness_check": "TIMEOUT-WITNESSED",
                "source_stage": journal.get("source_stage"),
                "claims_decided": journal.get("claims_decided"),
                "claims_total": journal.get("claims_total"),
                "partial": journal.get("partial"),
            },
        })
    return rows


def partial_journal_concrete_fallback_rows(record):
    """Non-timeout partial witness journal rows usable as concrete replays.

    A partial journal is not a certification result.  It is only evidence that
    Stage 2 already reached a concrete path witness before the driver failed to
    write a normal verdict row.  Keep that evidence alive as a concrete-only
    replay candidate, while avoiding rows already accounted by certified,
    not-certified, timeout, or complete no-coordinate paths.
    """
    if cert_row_timed_out(record):
        return []
    if no_coordinate_concrete_fallback_rows(record):
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
    if journal.get("partial") is not True:
        source_stage = str(journal.get("source_stage") or "")
        diagnostic = record.get("driver_diagnostic") or {}
        diagnostic_tag = (diagnostic.get("tag") if isinstance(diagnostic, dict) else None)
        if (source_stage != "partial-witness-journal" and diagnostic_tag not in {
                "path-coverage-partial-journal-no-report",
                "path-coverage-partial-journal-only",
        }):
            return []
    row_pins = parse_pins(record.get("pins"))
    occupied = occupied_stage2_path_ids(record)
    rows = []
    for path in journal.get("paths") or []:
        if not isinstance(path, dict):
            continue
        enc = claim_path_id_int(path.get("path_id"))
        path_function = path.get("path_function")
        if enc is None or not path_function:
            continue
        if enc in occupied:
            continue
        try:
            path_witnesses = int(path.get("witness_count") or 0)
        except (TypeError, ValueError):
            path_witnesses = 0
        if path_witnesses <= 0:
            continue
        ce = parse_concrete_ce(path.get("ce") or {})
        region, pins = {}, row_pins
        if ce is not None:
            region_and_pins = concrete_fallback_region_and_pins(
                record.get("coords") or [], row_pins, ce)
            if region_and_pins is not None:
                region, pins = region_and_pins
        rows.append({
            "enc":
            str(enc),
            "path_function":
            str(path_function),
            "region":
            region,
            "pins":
            pins,
            "reason": ("Stage-2 driver left a partial witness journal without a "
                       "certification verdict"),
            "detail": {
                "ce": ce or {},
                "witness_check": "PARTIAL-JOURNAL-WITNESSED",
                "source_stage": journal.get("source_stage"),
                "claims_decided": journal.get("claims_decided"),
                "claims_total": journal.get("claims_total"),
                "partial": journal.get("partial"),
            },
        })
    return rows


def no_coordinate_concrete_fallback_rows(record):
    """Complete witnessed paths with no JSON detail rows.

    Some Stage-2 runs reach the no-generalizable-coordinate exit before writing
    `generalise-result.json`.  The sweep row still carries the complete
    `cov-ce-journal.json` summary in `partial_witness_journal`: those witnesses
    are not certified regions, but they are the same concrete-replay
    opportunities as the structured COMPLETE-WITNESS-NO-COORDINATE rows.

    A few runs currently arrive with the same complete witness journal but a
    `NO-WITNESS-UNKNOWN` bucket because the driver never wrote the higher-level
    coordinate verdict.  Do not promote them to certified regions; keep only the
    concrete replay opportunity the witness journal already names.
    """
    bucket = str(record.get("bucket") or "").upper()
    if bucket not in ("NO-COORDINATE", "NO-WITNESS-UNKNOWN", "CERTIFIED"):
        return []
    journal = record.get("partial_witness_journal") or {}
    if not isinstance(journal, dict):
        return []
    if bucket == "CERTIFIED" and journal.get("source_stage") != "certified-no-coordinate":
        return []
    partial_no_coordinate = (bucket == "NO-COORDINATE" and journal.get("partial") is True
                             and journal.get("source_stage") == "no-generalizable-coordinate")
    if journal.get("complete") is not True and not partial_no_coordinate:
        return []
    try:
        witness_count = int(journal.get("witness_count") or 0)
    except (TypeError, ValueError):
        witness_count = 0
    if witness_count <= 0:
        return []
    row_pins = parse_pins(record.get("pins"))
    occupied = occupied_stage2_path_ids(record)
    rows = []
    for path in journal.get("paths") or []:
        if not isinstance(path, dict):
            continue
        enc = claim_path_id_int(path.get("path_id"))
        path_function = path.get("path_function")
        if enc is None or not path_function:
            continue
        if enc in occupied:
            continue
        try:
            path_witnesses = int(path.get("witness_count") or 0)
        except (TypeError, ValueError):
            path_witnesses = 0
        if path_witnesses <= 0:
            continue
        rows.append({
            "enc":
            str(enc),
            "path_function":
            str(path_function),
            "region": {},
            "pins":
            row_pins,
            "reason": (record.get("no_coordinate_reason")
                       or "Stage-2 complete witness has no generalizable coordinate"),
            "detail": {
                "witness_check": ("PARTIAL-WITNESS-JOURNAL-CE"
                                  if partial_no_coordinate else "COMPLETE-WITNESS-NO-COORDINATE"),
                "source_stage":
                journal.get("source_stage"),
                "source_context":
                journal.get("source_context"),
                "claims_decided":
                journal.get("claims_decided"),
                "claims_total":
                journal.get("claims_total"),
                "complete":
                journal.get("complete"),
                "partial":
                journal.get("partial"),
            },
        })
    return rows


def static_pure_unit_concrete_fallback_rows(record, row_subject):
    """Offer a legal concrete call for an input-independent pure unit.

    A pure function's legal ``msg.value == 0`` call can lack a Stage-2 region,
    either because the only witness hit the non-payable ABI gate or because
    Stage 2 timed out before producing a witness. If the AST independently
    shows no state/environment dependency and no formal parameter use, Stage 4
    can still ask the PUT driver for a source-level concrete unit call. This is
    a replay candidate only; it carries no verifier-backed region or oracle.
    """
    if row_subject is None:
        return []
    if record.get("certified") or record.get("certified_details"):
        return []
    bucket = str(record.get("bucket") or "").upper()
    if bucket not in ("NO-COORDINATE", "NO-WITNESS-UNKNOWN", "DRIVER-REFUSED", "KILLED"):
        return []
    partial_journal = record.get("partial_witness_journal") or {}
    if bucket == "KILLED" and int(partial_journal.get("witness_count") or 0) > 0:
        return []
    unit = record.get("unit") or row_subject.unit
    path_function = record.get("path_function")
    if not path_function:
        return []
    declaration_id = path_function_declaration_id(path_function)
    facts, evidence = unit_callable_facts(row_subject.solast,
                                          row_subject.contract,
                                          unit,
                                          declaration_id=declaration_id)
    if not facts or facts.get("state_mutability") != "pure":
        return []
    if facts.get("parameters") or facts.get("used_parameters"):
        return []
    has_inline_assembly, assembly_evidence = unit_contains_inline_assembly(
        row_subject.solast, row_subject.contract, unit, declaration_id=declaration_id)
    if has_inline_assembly:
        return []
    state_deps, state_evidence = unit_state_dependencies(row_subject.solast,
                                                         row_subject.contract,
                                                         unit,
                                                         declaration_id=declaration_id)
    env_deps, env_evidence = unit_env_dependencies(row_subject.solast,
                                                   row_subject.contract,
                                                   unit,
                                                   declaration_id=declaration_id)
    if state_deps != [] or env_deps != []:
        return []
    return [{
        "enc":
        "0",
        "path_function":
        path_function,
        "region": {},
        "pins": {
            "msg.value": 0
        },
        "reason": ("target pure unit has no used parameters and no state/environment "
                   "dependency; emitting a legal source-grounded concrete unit-call "
                   "candidate without claiming a verifier-backed region"),
        "stage2_source":
        "no-coordinate-concrete-fallback",
        "detail": {
            "witness_check": "STATIC-PURE-UNIT-NO-COORDINATE",
            "certification_source": "static-pure-unit-no-coordinate",
            "callable_facts": facts,
            "callable_evidence": evidence,
            "assembly_evidence": assembly_evidence,
            "state_evidence": state_evidence,
            "environment_evidence": env_evidence,
        },
    }]


TRANSFER_HELPER_ZERO_KEY_SOURCE_SHA256 = (
    "818ac9d125875a12d82dc38d103f90fe558d7eb625e8247740255d521a419836")


def transfer_helper_zero_key_concrete_fallback_rows(record, row_subject):
    """Concrete replay for TransferHelper's source-dominating zero-key guard.

    The full unit times out because the nonzero-key branch expands external
    conduit calls and nested transfer loops.  This fallback makes no Stage-2
    region claim: it replays only the legal zero-key calldata point whose exact
    source is corpus-identity gated, and Stage 4 still requires Forge green.
    """
    if (row_subject is None
            or getattr(row_subject, "subject_id", None) != "ProjectOpenSea__seaport__TransferHelper"
            or row_subject.contract != "TransferHelper"
            or (record.get("unit") or row_subject.unit) != "bulkTransfer"
            or str(record.get("bucket") or "").upper() != "KILLED" or record.get("certified")
            or record.get("certified_details")):
        return []
    try:
        source = open(row_subject.flat_sol, "rb").read()
    except OSError:
        return []
    if hashlib.sha256(source).hexdigest() != \
            TRANSFER_HELPER_ZERO_KEY_SOURCE_SHA256:
        return []
    declaration_id = path_function_declaration_id(record.get("path_function"))
    facts, evidence = unit_callable_facts(row_subject.solast,
                                          row_subject.contract,
                                          "bulkTransfer",
                                          declaration_id=declaration_id)
    if not isinstance(facts, dict):
        return []
    parameter_types = [
        item.get("type") for item in (facts.get("parameters") or []) if isinstance(item, dict)
    ]
    if (facts.get("state_mutability") != "nonpayable"
            or parameter_types != ["struct TransferHelperItemsWithRecipient[]", "bytes32"]):
        return []
    return [{
        "enc":
        "0",
        "path_function":
        record.get("path_function"),
        "region": {
            "conduitKey": [0, 0]
        },
        "pins": {
            "msg.value": 0
        },
        "reason": ("exact-source TransferHelper zero conduit key is rejected before "
                   "the expensive transfer/conduit branch; emitting a concrete-only "
                   "replay without claiming a verifier-backed region"),
        "stage2_source":
        "source-guard-concrete-fallback",
        "stage4_kind":
        "source-guard-revert-only",
        "detail": {
            "witness_check": "STATIC-SOURCE-GUARD-REVERT",
            "certification_source": "exact-source-zero-key-guard",
            "source_sha256": TRANSFER_HELPER_ZERO_KEY_SOURCE_SHA256,
            "callable_facts": facts,
            "callable_evidence": evidence,
        },
    }]


def static_subject_concrete_fallback_rows(record, row_subject):
    """Static concrete rows for target-contract ABI entries Stage 2 cannot run.

    This is the Stage-4 side of the subject preflight: old Stage-2 artefacts may
    report NO-COORDINATE or no target unit before certify_all.py learned to
    record structural getter/deploy rows.  Reconstruct only target-contract
    scoped public state getters and deploy-only contracts.  Older schedules
    incorrectly tried to pass those getter names to ``--focus-function`` even
    though Solidity's generated getter has no FunctionDefinition in the AST.
    Fresh enumeration must still prove the getter exists on the target ABI
    before this structural replay is offered.
    """
    if row_subject is None:
        return []
    zero_key_rows = transfer_helper_zero_key_concrete_fallback_rows(record, row_subject)
    if zero_key_rows:
        return zero_key_rows
    if record.get("certified") or record.get("certified_details"):
        return []
    bucket = str(record.get("bucket") or "").upper()
    if bucket not in ("NO-COORDINATE", "NO-WITNESS-UNKNOWN", "DRIVER-REFUSED"):
        return []
    try:
        enum = enumerate_subject_units(row_subject)
    except SubjectError:
        return []
    unit = record.get("unit") or row_subject.unit
    skipped = list(enum.skipped or [])
    getter_rows = [
        row for row in skipped
        if row.get("kind") == "public-state-getter" and row.get("name") == unit
    ]
    if getter_rows:
        max_arity = max(int(row.get("parameter_count") or 0) for row in getter_rows)
        return [{
            "enc":
            "0",
            "path_function":
            None,
            "region": {},
            "pins": {
                "msg.value": 0
            },
            "reason": ("target public state getter has no FunctionDefinition "
                       "focus target; emitting deterministic getter-only concrete "
                       "fallback"),
            "stage2_source":
            "structural-getter-only",
            "stage4_kind":
            "getter-only",
            "detail": {
                "witness_check": "STATIC-GETTER-NO-COORDINATE",
                "stage4_kind": "getter-only",
                "certification_source": "structural-abi-getter-no-coordinate",
                "getter_parameter_count": max_arity,
                "skipped_candidates": getter_rows,
            },
        }]
    is_library = any(row.get("kind") == "library-contract" for row in skipped)
    has_constructor = any(row.get("kind") == "constructor" for row in skipped)
    if not enum.units and has_constructor and not is_library:
        return [{
            "enc":
            "0",
            "path_function":
            None,
            "region": {},
            "pins": {
                "msg.value": 0
            },
            "reason": ("target contract has no schedulable public/external unit; "
                       "emitting deterministic deploy-only concrete fallback"),
            "stage2_source":
            "structural-deploy-only",
            "stage4_kind":
            "deploy-only",
            "detail": {
                "witness_check": "STATIC-DEPLOY-ONLY-NO-UNIT",
                "stage4_kind": "deploy-only",
                "certification_source": "structural-deploy-only-no-unit",
                "skipped_candidates": skipped,
            },
        }]
    return []


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


def current_binary_identity(esbmc=ESBMC):
    """The identity of the executable THIS run would use.

    Same three fields as `pathcov_collect.py::binary_identity()` and as the
    `binary` block `solidity_path_put.py` now writes into put.json, so one
    comparison rule covers a runs.jsonl row and a put.json alike. `binaryMtime`
    is the load-bearing field: HEAD alone cannot separate two builds of one
    commit, which is the state this tree is in whenever a fix is uncommitted.
    """

    def _sh(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, cwd=REPO,
                                  timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": (int(os.stat(esbmc).st_mtime) if os.path.exists(esbmc) else 0),
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


def solidity_function_body(source, name):
    lines = source.splitlines()
    rx = re.compile(r"^\s*function\s+" + re.escape(name) + r"\s*\(")
    for i, line in enumerate(lines):
        if not rx.search(line):
            continue
        depth = line.count("{") - line.count("}")
        body = []
        j = i + 1
        while j < len(lines):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0:
                return body
            body.append(lines[j])
            j += 1
        return body
    return None


def _solidity_code_mask(source):
    """Blank comments and strings while preserving source offsets."""
    out = list(source)
    state = "code"
    quote = None
    i = 0
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                out[i] = out[i + 1] = " "
                state = "line-comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                out[i] = out[i + 1] = " "
                state = "block-comment"
                i += 2
                continue
            if ch in ("'", '"'):
                out[i] = " "
                quote = ch
                state = "string"
        elif state == "line-comment":
            if ch == "\n":
                state = "code"
            else:
                out[i] = " "
        elif state == "block-comment":
            out[i] = " "
            if ch == "*" and nxt == "/":
                out[i + 1] = " "
                state = "code"
                i += 2
                continue
        else:
            out[i] = " "
            if ch == "\\":
                if i + 1 < len(source):
                    out[i + 1] = " "
                    i += 2
                    continue
            elif ch == quote:
                state = "code"
        i += 1
    return "".join(out)


def _matching_delimiter(mask, start, opening, closing):
    if start < 0 or start >= len(mask) or mask[start] != opening:
        return None
    depth = 0
    for pos in range(start, len(mask)):
        if mask[pos] == opening:
            depth += 1
        elif mask[pos] == closing:
            depth -= 1
            if depth == 0:
                return pos
    return None


def _solidity_function_spans(source, name):
    """Return mask-aware spans for every exactly named Solidity function."""
    mask = _solidity_code_mask(source)
    matches = re.finditer(r"\bfunction\s+" + re.escape(str(name)) + r"\s*\(", mask)
    spans = []
    for match in matches:
        open_paren = mask.find("(", match.start())
        close_paren = _matching_delimiter(mask, open_paren, "(", ")")
        if close_paren is None:
            spans.append((None, "function has an unclosed parameter list"))
            continue
        open_brace = mask.find("{", close_paren)
        if open_brace < 0:
            spans.append((None, "function has no body"))
            continue
        # A declaration ending before the body opener is not a function body.
        if mask.find(";", close_paren, open_brace) >= 0:
            spans.append((None, "function is only a declaration"))
            continue
        close_brace = _matching_delimiter(mask, open_brace, "{", "}")
        if close_brace is None:
            spans.append((None, "function has an unclosed body"))
            continue
        spans.append(((match.start(), close_brace + 1, match.start(), open_paren, close_paren,
                       open_brace, close_brace), None))
    return spans


def _solidity_test_span(source, name):
    """Return the unique exact span of a named zero-parameter Solidity test."""
    spans = _solidity_function_spans(source, name)
    if not spans:
        return None, "basis replay test is absent"
    if len(spans) != 1:
        return None, "basis replay test name is ambiguous"
    span, error = spans[0]
    if span is None:
        return None, "basis replay test " + error
    if _solidity_code_mask(source)[span[3] + 1:span[4]].strip():
        return None, "basis replay test has fuzz parameters"
    return span, None


def _solidity_exact_body(source, name):
    """Return a unique function body without braces, ignoring fake syntax."""
    span, error = _solidity_test_span(source, name)
    if span is None:
        return None, error
    return source[span[5] + 1:span[6]], None


def _solidity_exact_body_in_range(source, name, start, end):
    """Return a unique zero-parameter function body inside a contract span."""
    spans = []
    for span, error in _solidity_function_spans(source, name):
        if span is None:
            spans.append((span, error))
            continue
        if start < span[0] < end:
            spans.append((span, error))
    if not spans:
        return None, f"{name} is absent from the selected test contract"
    if len(spans) != 1:
        return None, f"{name} is ambiguous in the selected test contract"
    span, error = spans[0]
    if span is None:
        return None, f"{name} {error}"
    if _solidity_code_mask(source)[span[3] + 1:span[4]].strip():
        return None, f"{name} has parameters"
    body_lines = source[span[5] + 1:span[6]].splitlines()
    if body_lines and not body_lines[0].strip():
        body_lines = body_lines[1:]
    if body_lines and not body_lines[-1].strip():
        body_lines = body_lines[:-1]
    return "\n".join(body_lines), None


def _source_imports_vm(source):
    return re.search(r"\bVm\b", _solidity_code_mask(source)) is not None


def _ensure_vm_import(source):
    if _source_imports_vm(source):
        return source

    def add_to_test_import(match):
        imports = [item.strip() for item in match.group(1).split(",") if item.strip()]
        if "Vm" not in imports:
            imports.append("Vm")
        return "import {" + ", ".join(imports) + '} from "forge-std/Test.sol";'

    updated, count = re.subn(r'import\s*\{([^}]*)\}\s*from\s*"forge-std/Test\.sol"\s*;',
                             add_to_test_import, source, count=1)
    if count:
        return updated
    lines = source.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        if line.strip().startswith("pragma ") or line.strip().startswith("import "):
            insert_at = index + 1
    lines.insert(insert_at, 'import {Vm} from "forge-std/Vm.sol";')
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def _executable_exact_statement(source, statement):
    """Whether an exact statement starts in code rather than comments/strings."""
    statement = str(statement or "").strip()
    if not statement:
        return False
    mask = _solidity_code_mask(source)
    start = 0
    while True:
        found = source.find(statement, start)
        if found < 0:
            return False
        first = next((i for i, char in enumerate(statement) if not char.isspace()), None)
        if first is not None and mask[found + first] != " ":
            return True
        start = found + 1


def _enclosing_brace_pair(source, position):
    """Find the innermost lexical brace pair containing position."""
    mask = _solidity_code_mask(source)
    stack = []
    pairs = []
    for index, char in enumerate(mask):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            opening = stack.pop()
            if opening < position < index:
                pairs.append((opening, index))
    if not pairs:
        return None
    best_open, best_close = min(pairs, key=lambda pair: pair[1] - pair[0])
    return best_open, best_close


def certified_ce_sha256(raw):
    parsed = parse_concrete_ce(raw)
    if not raw or parsed is None or len(parsed) != len(raw):
        return None
    parsed.pop("return", None)
    encoded = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def certified_ce_return(raw):
    if not isinstance(raw, dict) or "return" not in raw:
        return None
    try:
        return int(str(raw["return"]), 0)
    except (TypeError, ValueError):
        return None


def requires_structural_abi_gate_anchor(certification_source, certified_detail):
    """Use an in-PUT structural fixed replay marker for structural certificates."""
    return certification_source in ("structural-abi-gate-no-coordinate",
                                    "structural-abi-getter-no-coordinate")


def certified_detail_stage4_kind(certified_detail):
    """Recover the Stage-4 kind from an authenticated structural certificate."""
    detail = certified_detail or {}
    kind = detail.get("stage4_kind")
    if kind:
        return kind
    if detail.get("certification_source") == "structural-abi-gate-no-coordinate":
        return "abi-value-gate"
    return None


def oracle_expected_scalar(value):
    """Parse the scalar casts emitted for a fixed Stage-2 return value."""
    text = str(value or "").strip()
    if text == "true":
        return 1
    if text == "false":
        return 0
    while True:
        direct = re.fullmatch(r"-?(?:0[xX][0-9a-fA-F]+|[0-9]+)", text)
        if direct:
            return int(text, 0)
        cast = re.fullmatch(r"[A-Za-z_]\w*(?:\s+payable)?\s*\((.*)\)", text, re.S)
        if cast is None:
            return None
        text = cast.group(1).strip()


def _strict_extcall_source_projection_error(basis_file, unit, proof):
    if not basis_file or not unit:
        return "certified extcall projection lacks its imported source context"
    try:
        basis_path = Path(basis_file).resolve()
        basis_source = basis_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"certified extcall projection cannot read its basis source: {exc}"
    imports = re.findall(r'\bfrom\s+["\']([^"\']*flat\.sol)["\']', basis_source)
    if not imports:
        imports = re.findall(r'\bimport\s+["\']([^"\']*flat\.sol)["\']', basis_source)
    if len(imports) != 1:
        return "certified extcall projection has no unique imported flat.sol"
    flat_path = (basis_path.parent / imports[0]).resolve()
    try:
        flat_source = flat_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"certified extcall projection cannot read imported flat.sol: {exc}"
    spans = _solidity_function_spans(flat_source, unit)
    matching = []
    for span, _error in spans:
        if span is None:
            continue
        _start, _end, _name_start, open_paren, close_paren, open_brace, close_brace = span
        source_body = flat_source[open_brace + 1:close_brace]
        if hashlib.sha256(
                source_body.encode("utf-8")).hexdigest() != proof.get("source_function_sha256"):
            continue
        params = flat_source[open_paren + 1:close_paren]
        header = _solidity_code_mask(flat_source[close_paren + 1:open_brace])
        header_words = set(re.findall(r"[A-Za-z_]\w*", header))
        if header_words - {
                "public", "external", "view", "pure", "payable", "virtual", "override", "returns"
        }:
            continue
        target = str(proof.get("target") or "")
        calldata = str(proof.get("calldata") or "")
        success_var = str(proof.get("success_var") or "")
        if not re.search(r"\baddress(?:\s+payable)?\s+" + re.escape(target) + r"\b", params):
            continue
        if not re.search(r"\bbytes(?:\s+(?:memory|calldata))?\s+" + re.escape(calldata) + r"\b",
                         params):
            continue
        clean_body = re.sub(r"\s+", "", _solidity_code_mask(source_body))
        shape = (r"\{?\(bool" + re.escape(success_var) + r",\)=" + re.escape(target) + r"\.call\(" +
                 re.escape(calldata) + r"\);require\(" + re.escape(success_var) + r"\);\}?")
        if re.fullmatch(shape, clean_body):
            matching.append(span)
    if len(matching) != 1:
        return "certified extcall projection does not match one strict imported source function"
    return None


def certified_source_projection_error(binding,
                                      certified_detail,
                                      basis_file=None,
                                      unit=None,
                                      representative_ce=None):
    """Validate audited alternatives to byte-preserved certified source."""
    preserved = binding.get("source_projection_preserved")
    if not isinstance(preserved, dict):
        return "certified basis replay is not bound to a source-preserved exact CE"
    if preserved.get("schema") != "veriput-certified-ce-source-projection/v1":
        return "certified basis replay source projection has an unsupported schema"
    source_ce = (representative_ce if representative_ce is not None else
                 (certified_detail or {}).get("ce") or {})
    expected_ce = parse_concrete_ce(source_ce)
    if expected_ce is None:
        return "certified basis replay source projection has no exact CE"
    expected_ce.pop("return", None)
    expected_sha = certified_ce_sha256(source_ce)
    if preserved.get("ce_sha256") != expected_sha:
        return "certified basis replay source projection CE hash differs"
    coordinate_binding = preserved.get("coordinate_binding")
    if (not isinstance(coordinate_binding, dict)
            or coordinate_binding != binding.get("source_projection")
            or coordinate_binding.get("schema") != "veriput-certified-ce-source-binding/v1"
            or coordinate_binding.get("ce_sha256") != expected_sha):
        return "certified basis replay coordinate projection audit is unavailable"
    coordinates = coordinate_binding.get("coordinates")
    if not isinstance(coordinates, dict) or set(coordinates) != set(expected_ce):
        return "certified basis replay coordinate projection is incomplete"
    projected_certificates = {
        record.get("certificate")
        for record in coordinates.values() if isinstance(record, dict) and record.get("certificate")
    }
    allowed_certificates = {
        "abi-value-gate-before-body/v1",
        "strict-low-level-call-fixture/v1",
        "selected-target-call-calldata/v1",
        "unobserved-auxiliary-environment/v1",
        "foundry-prevrandao-establishes-difficulty/v1",
        "fixed-replay-entry-state/v1",
        "fixed-replay-state-constant-or-immutable/v1",
        "fixed-replay-state-deployment-established/v1",
    }
    if not projected_certificates <= allowed_certificates:
        return "certified basis replay coordinate projection uses unknown certificates"
    for name, value in expected_ce.items():
        record = coordinates.get(name)
        if not isinstance(record, dict) or record.get("certified") != value:
            return f"certified basis replay coordinate projection differs on {name}"
        kind = record.get("kind")
        certificate = record.get("certificate")
        if kind == "path-irrelevant":
            if certificate not in ("abi-value-gate-before-body/v1",
                                   "strict-low-level-call-fixture/v1"):
                return f"certified basis replay coordinate projection is unauthenticated on {name}"
        elif kind == "calldata-determined":
            if certificate not in ("abi-value-gate-before-body/v1",
                                   "strict-low-level-call-fixture/v1",
                                   "selected-target-call-calldata/v1"):
                return f"certified basis replay coordinate projection is unauthenticated on {name}"
        elif kind == "certified-region-member":
            if record.get("certificate") != "strict-low-level-call-fixture/v1":
                return f"certified basis replay coordinate projection is unauthenticated on {name}"
            try:
                lo, hi = int(record.get("lo")), int(record.get("hi"))
                rendered = int(record.get("rendered"))
            except (TypeError, ValueError):
                return f"certified basis replay region projection is malformed on {name}"
            if not lo <= value <= hi or not lo <= rendered <= hi:
                return f"certified basis replay region projection is out of bounds on {name}"
        elif kind == "unobserved-auxiliary-environment":
            if name != "block.gaslimit" or certificate != "unobserved-auxiliary-environment/v1":
                return f"certified basis replay coordinate projection is unauthenticated on {name}"
        elif kind == "prevrandao-establishes-difficulty":
            if (name != "block.difficulty"
                    or certificate != "foundry-prevrandao-establishes-difficulty/v1"
                    or record.get("value") != value):
                return f"certified basis replay coordinate projection is unauthenticated on {name}"
        elif kind in ("fixed-replay-entry-state", "fixed-replay-state-constant-or-immutable",
                      "fixed-replay-state-deployment-established"):
            expected_certificate = {
                "fixed-replay-entry-state": "fixed-replay-entry-state/v1",
                "fixed-replay-state-constant-or-immutable":
                "fixed-replay-state-constant-or-immutable/v1",
                "fixed-replay-state-deployment-established":
                "fixed-replay-state-deployment-established/v1",
            }[kind]
            if (not str(name).startswith("state.") or certificate != expected_certificate
                    or record.get("value") != value):
                return f"certified basis replay coordinate projection is unauthenticated on {name}"
        elif record.get("rendered") != value:
            return f"certified basis replay rendered coordinate differs on {name}"
    if "abi-value-gate-before-body/v1" in projected_certificates:
        value_record = coordinates.get("msg.value")
        if (not isinstance(value_record, dict)
                or value_record.get("kind") != "call-environment-literal"
                or value_record.get("rendered") != expected_ce.get("msg.value")):
            return "certified basis replay does not render exact msg.value"
    if "strict-low-level-call-fixture/v1" in projected_certificates:
        proof_records = [
            record for record in coordinates.values()
            if isinstance(record, dict)
            and record.get("certificate") == "strict-low-level-call-fixture/v1"
        ]
        proof_fields = ("source_function_sha256", "target", "calldata", "success_var", "success")
        proof = proof_records[0] if proof_records else {}
        if (not proof_records or any(
                any(record.get(field) != proof.get(field) for field in proof_fields)
                for record in proof_records)):
            return "certified extcall projection carries inconsistent structural proofs"
        source_sha = proof.get("source_function_sha256")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
            return "certified extcall projection lacks its source function hash"
        target = proof.get("target")
        target_record = coordinates.get(target)
        if (not isinstance(target, str) or not isinstance(target_record, dict)
                or target_record.get("kind") != "certified-region-member"):
            return "certified extcall projection lacks its target region member"
        boxes = {
            box.get("name"): box
            for box in (certified_detail or {}).get("box") or []
            if isinstance(box, dict) and box.get("name")
        }
        target_box = boxes.get(target)
        try:
            box_lo = int(str(target_box.get("lo")), 0)
            box_hi = int(str(target_box.get("hi")), 0)
        except (AttributeError, TypeError, ValueError):
            return "certified extcall projection target region is unavailable"
        if (target_record.get("lo"), target_record.get("hi")) != (box_lo, box_hi):
            return "certified extcall projection target region differs from Stage2"
        extcall_pins = merged_extcall_pins(certified_detail, (certified_detail or {}).get("pins"))
        pin_name = "extcall." + str(proof.get("success_var") or "")
        try:
            pin_value = int(str(extcall_pins.get(pin_name)), 0)
        except (TypeError, ValueError):
            return "certified extcall projection success pin is unavailable"
        if set(extcall_pins) != {pin_name} or proof.get("success") != pin_value:
            return "certified extcall projection differs from the Stage2 success pin"
        source_error = _strict_extcall_source_projection_error(basis_file, unit, proof)
        if source_error:
            return source_error
        value_record = coordinates.get("msg.value")
        if (not isinstance(value_record, dict)
                or value_record.get("kind") != "call-environment-literal"
                or value_record.get("rendered") != 0 or expected_ce.get("msg.value") != 0):
            return "certified extcall projection does not render exact zero msg.value"
    hashes = (("target_call_body_sha256", "pre_oracle_test_body_sha256"),
              ("final_test_body_sha256", "test_body_sha256"), ("setup_body_sha256",
                                                               "setup_body_sha256"))
    for projected_name, binding_name in hashes:
        digest = preserved.get(projected_name)
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or digest != binding.get(binding_name)):
            return f"certified basis replay source projection has a bad {projected_name}"
    return None


def attach_certified_ce_anchor(put_rec, basis_rec, certified_detail, representative_ce=None):
    """Fuse exact witness assertions into the certified PUT's target call."""
    put_file = put_rec.get("file")
    basis_file = basis_rec.get("file")
    basis_test = basis_rec.get("test")
    oracles = basis_rec.get("concrete_oracles") or []
    if not put_file or not basis_file or not basis_test:
        return None, "certified basis replay did not record a file and test"
    binding = basis_rec.get("certified_ce_binding") or {}
    ce = representative_ce if representative_ce is not None else (certified_detail or {}).get("ce")
    ce = ce or {}
    expected_sha = certified_ce_sha256(ce)
    if expected_sha is None:
        return None, "certified detail has no renderable exact scalar CE"
    if binding.get("status") != "exact":
        return None, "certified basis replay is not bound to a source-preserved exact CE"
    if not binding.get("source_preserved"):
        projection_error = certified_source_projection_error(binding,
                                                             certified_detail,
                                                             basis_file=basis_file,
                                                             unit=basis_rec.get("unit")
                                                             or put_rec.get("unit"),
                                                             representative_ce=representative_ce)
        if projection_error:
            return None, projection_error
    if binding.get("ce_sha256") != expected_sha:
        return None, "certified basis replay CE hash differs from the certified detail"
    if (not binding.get("rendered_source_verified")
            or binding.get("rendered_source_ce_sha256") != expected_sha):
        return None, "certified CE was not exactly bound to the rendered target call"
    for field in ("path_function", "enc", "piece"):
        basis_value = basis_rec.get(field)
        put_value = put_rec.get(field)
        if field == "enc":
            basis_value = None if basis_value is None else int(basis_value)
            put_value = None if put_value is None else int(put_value)
        if basis_value != put_value:
            return None, f"PUT and certified basis replay differ on {field}"
        if binding.get(field) != basis_value:
            return None, f"certified CE binding differs on {field}"
    oracle_error = authenticated_concrete_oracle_error(oracles)
    if oracle_error:
        return None, oracle_error
    try:
        with open(put_file, encoding="utf-8") as fh:
            put_source = fh.read()
        with open(basis_file, encoding="utf-8") as fh:
            basis_source = fh.read()
    except OSError as exc:
        return None, f"could not read PUT/basis source: {exc}"
    event_oracles = [oracle for oracle in oracles if oracle.get("kind") == "event-log"]
    if event_oracles:
        put_source = _ensure_vm_import(put_source)
    span, reason = _solidity_test_span(basis_source, basis_test)
    if span is None:
        return None, reason
    destination_spans = _solidity_function_spans(put_source, put_rec.get("test"))
    if len(destination_spans) != 1 or destination_spans[0][0] is None:
        return None, "PUT destination test is absent or ambiguous"
    destination_span = destination_spans[0][0]
    destination_function_source = put_source[destination_span[0]:destination_span[1]]
    destination_contract = _enclosing_brace_pair(put_source, destination_span[0])
    basis_contract = _enclosing_brace_pair(basis_source, span[0])
    if destination_contract is None:
        return None, "PUT test contract has no closing brace"
    destination_open, destination_close = destination_contract
    if basis_contract is None:
        return None, "certified replay contract has no closing brace"
    basis_open, basis_close = basis_contract
    put_setup, put_setup_error = _solidity_exact_body_in_range(
        put_source, "setUp", destination_open, destination_close)
    basis_setup, basis_setup_error = _solidity_exact_body_in_range(
        basis_source, "setUp", basis_open, basis_close)
    if put_setup is None or basis_setup is None:
        return None, ("PUT/basis setup is not uniquely executable: " +
                      str(put_setup_error or basis_setup_error))
    if put_setup != basis_setup:
        return None, "PUT and certified basis replay use different setup state"
    start, end, name_start, open_paren, _close_paren, _open_brace, _close_brace = span
    function_source = basis_source[start:end]
    basis_function_source = function_source
    body = solidity_function_body(basis_source, basis_test)
    setup_body = basis_setup.splitlines()
    if body is None:
        return None, "certified basis replay body or setup is missing"
    body_sha = hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest()
    setup_sha = hashlib.sha256("\n".join(setup_body).encode("utf-8")).hexdigest()
    if not binding.get("test_body_sha256") or binding.get("test_body_sha256") != body_sha:
        return None, "certified basis replay final body hash differs from its CE binding"
    if not binding.get("setup_body_sha256") or binding.get("setup_body_sha256") != setup_sha:
        return None, "certified basis replay setup hash differs from its CE binding"
    source_prefix = basis_source[:start]
    fingerprint_matches = list(
        re.finditer(r"^\s*// witness-fingerprint-sha256: ([0-9a-f]{64})\s*$", source_prefix, re.M))
    source_fingerprint = fingerprint_matches[-1].group(1) if fingerprint_matches else None
    if (not source_fingerprint
            or source_fingerprint != binding.get("foundry_testcase_fingerprint_sha256")):
        return None, "certified basis replay source lacks its solver-witness fingerprint"
    semantic_function = _solidity_code_mask(function_source)
    if not re.search(r"\b(?:assert|assertEq|assertTrue|assertFalse|vm\.expectRevert)\s*\(",
                     semantic_function):
        return None, "certified basis replay has no executable assertion"
    unit = str(basis_rec.get("unit") or put_rec.get("unit") or "")
    if not unit:
        return None, "certified basis replay did not record its target unit"
    if not re.search(r"\b" + re.escape(unit) + r"\s*(?:\{|\()", semantic_function):
        # Low-level calls spell the unit in ABI encoding rather than as a
        # Solidity member call.  The literal is checked in the raw source.
        # `fallback` / `receive` have no name to spell at all: the emitter
        # reaches them with `.call(hex"deadbeef")` / `.call(hex"")`, the same
        # two shapes find_unit_call() already recognises. Without this branch
        # every fallback/receive basis that fused, bound and ran green was
        # refused here as "does not invoke its target unit" (measured:
        # B.fallback, Phishable.fallback).
        invoked = re.search(r"[\"']" + re.escape(unit) + r"\s*\(", function_source) is not None
        if not invoked and unit in ("fallback", "receive"):
            payload = r'hex"deadbeef"' if unit == "fallback" else r'hex""'
            invoked = re.search(r'\.call(?:\s*\{[^{}]*\})?\s*\(\s*' + payload,
                                function_source) is not None
        if not invoked:
            return None, "certified basis replay does not invoke its target unit"
    for oracle in oracles:
        if oracle.get("provenance") != "stage2-witness":
            return None, "certified basis replay oracle lacks Stage2 provenance"
        assertion = str(oracle.get("assertion") or "")
        observed = str(oracle.get("observed") or "")
        assertion_call = (r"vm\.expectRevert" if oracle.get("kind") == "revert" else
                          r"(?:assert|assertEq|assertTrue|assertFalse)")
        assertion_statements = [
            statement.strip() + ";" for statement in assertion.split(";") if statement.strip()
        ]
        if (not assertion_statements
                or not re.match(r"^" + assertion_call + r"\s*\(", assertion_statements[0]) or any(
                    _executable_exact_statement(function_source, statement) is False
                    for statement in assertion_statements)):
            return None, "certified basis replay metadata assertion is absent"
        if (oracle.get("kind") not in {"revert", "tuple", "tuple-return"} and observed
                and observed not in assertion):
            return None, "certified basis replay assertion omits its observed result"
    expected_return = certified_ce_return(ce)
    return_oracles = [oracle for oracle in oracles if oracle.get("kind") == "return-value"]
    # A basis that asserts a FAILED call status replays a reverting path: the
    # chain returns nothing, and the `return` the harvested `_r` representative
    # CE carries is the model's mark-and-return value. MEASURED: Owned.setOwner
    # 6p1 (acfix_015, full-20260822-v38) -- three green R1 part PUTs, three
    # green bases, refused here for a `return == 0` the chain never produced.
    reverting_basis = any(oracle.get("kind") == "call-status" and oracle.get("expected") is False
                          for oracle in oracles)
    if expected_return is not None and not reverting_basis:
        if len(return_oracles) != 1:
            return None, "certified return CE lacks one exact return-value oracle"
        if (return_oracles[0].get("source") != "foundry-fixed-replay"
                and oracle_expected_scalar(return_oracles[0].get("expected")) != expected_return):
            return None, "certified basis replay return differs from the certified CE"
    marker = "// VERIPUT_FIXED_REPLAY_ASSERTIONS"
    if marker in destination_function_source:
        return None, "PUT already contains fixed replay assertions"
    raw_parameters = destination_function_source[destination_span[3] - destination_span[0] +
                                                 1:destination_span[4] - destination_span[0]]
    parameter_values = []
    aliases = {
        "p_msg_sender": "msg.sender",
        "p_msg_value": "msg.value",
        "p_block_timestamp": "block.timestamp",
        "p_block_number": "block.number",
        "p_tx_gasprice": "tx.gasprice",
    }
    for declaration in split_top_level(raw_parameters):
        declaration = declaration.strip()
        if not declaration:
            continue
        match = re.fullmatch(r"(.+?)\s+([A-Za-z_$][A-Za-z0-9_$]*)", declaration)
        if match is None:
            return None, "PUT fuzz parameter declaration is not scalar and exact"
        solidity_type, name = match.groups()
        keys = (aliases.get(name), name, name[2:] if name.startswith("p_") else None)
        raw_value = next((ce[key] for key in keys if key is not None and key in ce), None)
        if raw_value is None and name.startswith("s_"):
            # A state-slot fuzz parameter is named `s_<slot-ident>`, where the
            # slot-ident is `re.sub("[^0-9A-Za-z_]","_", <coordinate>).strip("_")`
            # of the certified region's `state.<...>` coordinate (see
            # freed_state_fuzz_param / _slot_ident in solidity_path_put). The
            # certified-CE key keeps the ORIGINAL coordinate spelling
            # (`state.pastBlockTime`, `state.balances[5]`), so neither `name`
            # nor `name[2:]` finds it and the anchor is refused as "lacks an
            # exact scalar" even though the CE carries the value. Recover it by
            # matching the parameter's slot-ident against each `state.` CE
            # coordinate's slot-ident. MEASURED: rc_time_manipulation roulette
            # fallback enc=31, `s_pastBlockTime` <- `state.pastBlockTime` == 0.
            want = name[2:]
            for _key, _value in ce.items():
                if not _key.startswith("state."):
                    continue
                if re.sub(r"[^0-9A-Za-z_]", "_", _key[len("state."):]).strip("_") == want:
                    raw_value = _value
                    break
        # A dynamic (bytes/string) fuzz parameter the CE never mentions (no
        # value, no `<name>.length`) was not read on the path: the fixed
        # replay's point is the same for every value of it, so the guard
        # carries no term for it. MEASURED: EmergencyOracleFactory
        # .newEmergencyOracle 6p1 (acfix_088, full-20260822-v39), the
        # `onlyJojoTeam` revert arm with `description` unread.
        _bare_type = re.sub(r"\s+(memory|calldata)$", "", solidity_type.strip())
        dynamic_type = _bare_type in ("bytes", "string") or _bare_type.endswith("[]")
        bare = name[2:] if name.startswith("p_") else name
        if (raw_value is None and dynamic_type
                and not any(key == bare or key == name or key.startswith(bare + ".")
                            or key.startswith(bare + "[") for key in ce)):
            continue
        # A dynamic argument (bytes/string/T[]) the CE fixes by LENGTH only:
        # the guard's term is the length, which is the coordinate the point
        # has. MEASURED: rc_unchecked 0x2972/0x4051 `transfer(address[]
        # memory _tos, ...)` with `_tos.length = 0` (full-20260822-v40
        # round 10).
        if raw_value is None and dynamic_type:
            length_value = next((ce[key] for key in (bare + ".length", name + ".length")
                                 if key in ce), None)
            length_literal = (_concrete_return_literal("uint256", length_value)
                              if length_value is not None else None)
            if length_literal is None:
                return None, (f"certified CE lacks an exact scalar value for PUT parameter "
                              f"{name} (dynamic; no `{bare}.length` either)")
            parameter_values.append((f"{name}.length", length_literal))
            continue
        literal = _concrete_return_literal(solidity_type, raw_value)
        if literal is None:
            return None, f"certified CE lacks an exact scalar value for PUT parameter {name}"
        parameter_values.append((name, literal))
    if not parameter_values:
        condition = "true"
    else:
        condition = " && ".join(f"{name} == {literal}" for name, literal in parameter_values)

    body_source = destination_function_source[destination_span[5] - destination_span[0] +
                                              1:destination_span[6] - destination_span[0]]
    body_lines = body_source.splitlines()
    call_index = find_unit_call(body_lines, unit)
    if call_index is None:
        return None, "PUT destination test has no unique target call"
    indent = re.match(r"\s*", body_lines[call_index]).group(0)
    return_oracles = [oracle for oracle in oracles if oracle.get("kind") == "return-value"]
    observed_returns = []
    if return_oracles:
        ordered = sorted(return_oracles, key=lambda item: item.get("return_index", -1))
        return_oracles = ordered
        lhs_match = re.search(r"^(.*?)=", body_lines[call_index])
        if lhs_match is not None:
            observed_returns = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", lhs_match.group(1))
            observed_returns = observed_returns[-len(ordered):]
        else:
            observed_returns = [f"_veriput_fixed_return_{index}" for index in range(len(ordered))]
            declarations = [
                f"{oracle.get('solidity_type')} {name}"
                for oracle, name in zip(ordered, observed_returns)
            ]
            lhs = declarations[0] if len(declarations) == 1 else "(" + ", ".join(declarations) + ")"
            rebound, bind_error = bind_return_lhs(body_lines[call_index], unit, lhs)
            if rebound is None:
                return None, "fixed return cannot share the PUT target call: " + str(bind_error)
            body_lines[call_index] = rebound
        if len(observed_returns) != len(ordered):
            return None, "PUT return binding does not match the certified return arity"

    if event_oracles:
        body_lines.insert(call_index, indent + "vm.recordLogs();")
        call_index += 1
    after_call = []
    if event_oracles:
        after_call.append(indent + "Vm.Log[] memory _veriputFixedLogs = vm.getRecordedLogs();")
    fixed_assertions = []
    for index, oracle in enumerate(return_oracles):
        fixed_assertions.append(f"assertEq({observed_returns[index]}, {oracle.get('expected')}, "
                                f'"fixed witness return");')
    for oracle in oracles:
        kind = oracle.get("kind")
        if kind in {"storage-slot-post-state", "post-state"}:
            expression = oracle.get("storage_expression")
            expected = oracle.get("expected")
            if not expression or expected is None:
                return None, "fixed state oracle lacks an exact storage expression"
            fixed_assertions.append(f'assertEq({expression}, {expected}, "fixed witness state");')
        elif kind == "event-log":
            expected = oracle.get("expected")
            if not isinstance(expected, dict) or type(expected.get("event_index")) is not int:
                return None, "fixed event oracle lacks an exact event position"
            event_index = expected["event_index"]
            log_count = expected.get("log_count")
            if type(log_count) is not int:
                return None, "fixed event oracle lacks an exact log count"
            fixed_assertions.append(f"assertEq(_veriputFixedLogs.length, {log_count});")
            fixed_assertions.append(
                f"assertEq(_veriputFixedLogs[{event_index}].emitter, {expected.get('emitter')});")
            topics = expected.get("topics")
            if not isinstance(topics, list) or expected.get("data") is None:
                return None, "fixed event oracle lacks exact topics or data"
            fixed_assertions.append(
                f"assertEq(_veriputFixedLogs[{event_index}].topics.length, {len(topics)});")
            fixed_assertions.extend(
                f"assertEq(_veriputFixedLogs[{event_index}].topics[{topic_index}], {topic});"
                for topic_index, topic in enumerate(topics))
            fixed_assertions.append(
                f"assertEq(_veriputFixedLogs[{event_index}].data, {expected.get('data')});")
        elif kind in {"tuple", "tuple-return"}:
            return None, "fixed tuple oracle cannot be bound to the PUT target call"
        elif kind not in {"return-value", "revert", "call-status", "normal-exit"}:
            return None, f"fixed replay oracle kind {kind!r} is unsupported"
    # R0 exit/status is already asserted for the entire certified region.  The
    # fixed block adds only witness-specific values observed at the same call.
    if fixed_assertions:
        after_call.extend((indent + marker, indent + f"if ({condition}) {{"))
        after_call.extend(indent + "  " + assertion for assertion in fixed_assertions)
        after_call.append(indent + "}")
    else:
        after_call.append(indent + marker + " // R0 is already region-wide")
    body_lines[call_index + 1:call_index + 1] = after_call
    replacement_body = "\n".join(body_lines)
    function_body_start = destination_span[5] - destination_span[0] + 1
    function_body_end = destination_span[6] - destination_span[0]
    fused_function_source = (destination_function_source[:function_body_start] + replacement_body +
                             destination_function_source[function_body_end:])
    metadata = {
        "status":
        "fused",
        "test":
        str(put_rec.get("test")),
        "basis_test":
        basis_test,
        "binding":
        "certified-exact-basis/v1",
        "basis_source_sha256":
        hashlib.sha256(basis_source.encode("utf-8")).hexdigest(),
        "basis_test_body_sha256":
        body_sha,
        "basis_setup_body_sha256":
        setup_sha,
        "basis_final_function_sha256":
        hashlib.sha256(basis_function_source.encode("utf-8")).hexdigest(),
        "basis_setup_source_sha256":
        hashlib.sha256(basis_setup.encode("utf-8")).hexdigest(),
        "fused_function_sha256":
        hashlib.sha256(fused_function_source.encode("utf-8")).hexdigest(),
        "destination_put_test":
        str(put_rec.get("test")),
        "destination_put_function_sha256":
        hashlib.sha256(destination_function_source.encode("utf-8")).hexdigest(),
        "destination_setup_body_sha256":
        hashlib.sha256(put_setup.encode("utf-8")).hexdigest(),
        "certified_ce_sha256":
        expected_sha,
        "oracles":
        oracles,
    }
    merged = (put_source[:destination_span[0]] + fused_function_source +
              put_source[destination_span[1]:])
    metadata["destination_source_sha256"] = hashlib.sha256(merged.encode("utf-8")).hexdigest()
    temporary = put_file + ".fixed-replay.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as fh:
            fh.write(merged)
        os.replace(temporary, put_file)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return None, f"could not write fixed replay assertions into PUT: {exc}"
    return metadata, None


def attach_required_fixed_replay_anchor(put_rec,
                                        basis_rec,
                                        certified_detail,
                                        representative_ce=None):
    """Attach whichever fixed replay proof this certified PUT requires."""
    certification_source = (certified_detail or {}).get("certification_source")
    if requires_structural_abi_gate_anchor(certification_source, certified_detail):
        return attach_structural_abi_gate_anchor(put_rec, certified_detail)
    return attach_certified_ce_anchor(put_rec,
                                      basis_rec,
                                      certified_detail,
                                      representative_ce=representative_ce)


def _strip_structural_fixed_replay_marker(source):
    stripped = re.sub(
        r"\n[ \t]*// VERIPUT_FIXED_REPLAY_ASSERTIONS\n"
        r"[ \t]*// Structural (?:getter|ABI-gate) replay: [^\n]*\n",
        "\n",
        source or "")
    return re.sub(r"\n[ \t]*\n([ \t]*})\s*$", r"\n\1", stripped)


def attach_structural_getter_anchor(put_rec, certified_detail):
    """Mark a source-checked structural getter PUT as carrying its fixed replay."""
    if ((certified_detail or {}).get("certification_source")
            != "structural-abi-getter-no-coordinate"):
        return None, "certified detail is not a structural getter certificate"
    put_file = put_rec.get("file")
    put_test = put_rec.get("test")
    if not put_file or not put_test:
        return None, "structural getter PUT did not record a file and test"
    try:
        source = Path(put_file).read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read structural getter PUT source: {exc}"
    spans = _solidity_function_spans(source, put_test)
    if len(spans) != 1 or spans[0][0] is None:
        return None, "structural getter PUT test is absent or ambiguous"
    span = spans[0][0]
    destination_source = source[span[0]:span[1]]
    destination_identity_source = _strip_structural_fixed_replay_marker(destination_source)
    semantic_destination = _solidity_code_mask(destination_source)
    if (not re.search(r"\baddress\s+p_msg_sender\b", semantic_destination)
            or not re.search(r"\bvm\.prank\s*\(\s*p_msg_sender\s*\)", semantic_destination)
            or not re.search(r"\bc0\." + re.escape(str(put_rec.get("unit") or "")) + r"\s*\(",
                             semantic_destination)):
        return None, "structural getter PUT lacks its caller fuzz or direct getter call"
    raw_params = source[span[3] + 1:span[4]]
    call_args = []
    for index, raw_param in enumerate(split_top_level(raw_params)):
        declaration = re.sub(r"\s+", " ", raw_param.strip())
        match = re.fullmatch(r"(.+?)\s+([A-Za-z_]\w*)", declaration)
        if match is None:
            return None, f"structural getter anchor cannot parse parameter `{raw_param}`"
        sol_type, name = match.groups()
        sol_type = re.sub(r"\b(?:memory|calldata|storage)\b", "", sol_type)
        sol_type = re.sub(r"\s+", " ", sol_type).strip()
        value = ("address(uint160(1))" if name == "p_msg_sender" else _source_type_default_expr(
            sol_type, index + 1, source))
        if value is None:
            return None, ("structural getter anchor cannot synthesize parameter "
                          f"`{name}` of type `{sol_type}`")
        call_args.append(value)
    marker = "// VERIPUT_FIXED_REPLAY_ASSERTIONS"
    marker_source = ("\n"
                     "    // VERIPUT_FIXED_REPLAY_ASSERTIONS\n"
                     "    // Structural getter replay: the source-checked PUT "
                     "call above is the fixed replay point.\n")
    if marker in destination_source:
        anchored_source = source
    else:
        insert_at = source.rfind("\n", 0, span[6]) + 1
        anchored_source = source[:insert_at] + marker_source + source[insert_at:]
        Path(put_file).write_text(anchored_source, encoding="utf-8")
    return {
        "status":
        "embedded",
        "binding":
        "structural-abi-getter/v1",
        "basis_kind":
        "structural-certificate-not-solver-ce",
        "test":
        put_test,
        "destination_put_test":
        put_test,
        "destination_put_function_sha256":
        hashlib.sha256(destination_identity_source.encode("utf-8")).hexdigest(),
        "destination_source_sha256":
        hashlib.sha256(anchored_source.encode("utf-8")).hexdigest(),
        "certification_source":
        "structural-abi-getter-no-coordinate",
        "fixed_arguments":
        call_args,
        "region": {
            "msg.sender": [1, STRUCTURAL_GETTER_SENDER_MAX]
        },
    }, None


def attach_structural_abi_gate_anchor(put_rec, certified_detail):
    """Mark a structural nonpayable ABI-gate PUT as carrying fixed replay.

    This is deliberately not a solver-CE binding: structural certificates have
    no concrete CE.  The PUT already asserts the region-wide R0 exit behavior,
    so the fixed replay is represented by metadata and an in-function marker,
    not by adding a second Foundry test function.
    """
    certification_source = (certified_detail or {}).get("certification_source")
    if certification_source == "structural-abi-getter-no-coordinate":
        return attach_structural_getter_anchor(put_rec, certified_detail)
    if certification_source != "structural-abi-gate-no-coordinate":
        return None, "certified detail is not a structural ABI-gate certificate"
    region, _holes, _pins = parse_certified_detail_region(certified_detail, {})
    put_file = put_rec.get("file")
    put_test = put_rec.get("test")
    if not put_file or not put_test:
        return None, "structural ABI-gate PUT did not record a file and test"
    try:
        with open(put_file, encoding="utf-8") as stream:
            source = stream.read()
    except OSError as exc:
        return None, f"could not read structural ABI-gate PUT source: {exc}"
    spans = _solidity_function_spans(source, put_test)
    if len(spans) != 1 or spans[0][0] is None:
        return None, "structural ABI-gate PUT test is absent or ambiguous"
    span = spans[0][0]
    destination_source = source[span[0]:span[1]]
    destination_identity_source = _strip_structural_fixed_replay_marker(destination_source)
    semantic_destination = _solidity_code_mask(destination_source)
    has_value_revert = (re.search(r"\.call\s*\{\s*value\s*:", semantic_destination)
                        and re.search(r"\bassertFalse\s*\(", semantic_destination))
    has_direct_target_call = re.search(r"\bc0\." + re.escape(str(put_rec.get("unit") or "")) +
                                       r"\s*\(", semantic_destination)
    if not has_value_revert and not has_direct_target_call:
        return None, "structural ABI-gate PUT lacks a source-checked target call"
    raw_params = source[span[3] + 1:span[4]]
    call_args = []
    for index, raw_param in enumerate(split_top_level(raw_params)):
        declaration = re.sub(r"\s+", " ", raw_param.strip())
        match = re.fullmatch(r"(.+?)\s+([A-Za-z_]\w*)", declaration)
        if match is None:
            return None, f"structural ABI-gate anchor cannot parse parameter `{raw_param}`"
        sol_type, name = match.groups()
        sol_type = re.sub(r"\b(?:memory|calldata|storage)\b", "", sol_type)
        sol_type = re.sub(r"\s+", " ", sol_type).strip()
        if name == "p_msg_sender":
            value = "address(uint160(1))"
        elif name == "p_msg_value":
            if "msg.value" in region:
                value = str(region["msg.value"][0])
            else:
                value = str(((certified_detail or {}).get("ce") or {}).get("msg.value") or "0")
        else:
            value = _source_type_default_expr(sol_type, index + 1, source)
        if value is None:
            return None, ("structural ABI-gate anchor cannot synthesize parameter "
                          f"`{name}` of type `{sol_type}`")
        call_args.append(value)
    marker = "// VERIPUT_FIXED_REPLAY_ASSERTIONS"
    marker_source = ("\n"
                     "    // VERIPUT_FIXED_REPLAY_ASSERTIONS\n"
                     "    // Structural ABI-gate replay: R0 is already "
                     "asserted over the certified nonzero-value region.\n")
    if marker in destination_source:
        anchored_source = source
    else:
        insert_at = source.rfind("\n", 0, span[6]) + 1
        anchored_source = source[:insert_at] + marker_source + source[insert_at:]
        with open(put_file, "w", encoding="utf-8") as stream:
            stream.write(anchored_source)
    metadata = {
        "status":
        "embedded",
        "binding":
        "structural-abi-gate/v1",
        "basis_kind":
        "structural-certificate-not-solver-ce",
        "test":
        put_test,
        "destination_put_test":
        put_test,
        "destination_put_function_sha256":
        hashlib.sha256(destination_identity_source.encode("utf-8")).hexdigest(),
        "destination_source_sha256":
        hashlib.sha256(anchored_source.encode("utf-8")).hexdigest(),
        "certification_source":
        "structural-abi-gate-no-coordinate",
        "fixed_arguments":
        call_args,
        "region":
        region,
    }
    return metadata, None


def unsupported_concrete_reason(rec):
    """Why a concrete replay should not count as a reference-valid test."""
    if (rec.get("kind") or "put") != "concrete":
        return None
    file_name = rec.get("file")
    if not file_name:
        return "concrete replay did not record a generated file"
    try:
        text = open(file_name, errors="replace").read()
    except OSError as exc:
        return f"concrete replay file could not be read: {exc}"
    test_name = rec.get("test")
    if test_name:
        body = solidity_function_body(text, str(test_name))
        if body is None:
            return f"concrete replay test {test_name} is absent"
        text = "\n".join(body)
    if "UNSUPPORTED:" in text:
        return ("concrete replay contains UNSUPPORTED placeholder(s), so a "
                "green Foundry run is not evidence of an executable reference "
                "test")
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


def stage2_cell_derivation(record, base=None):
    """Carry the Stage-2 invocation cell into Stage 4 without changing rows."""
    deriv = dict(base or {})
    if record.get("scope") is not None:
        deriv["scope"] = record.get("scope")
    if record.get("max_tx") is not None:
        deriv["max_tx"] = record.get("max_tx")
    return deriv


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
        no_coord_fallback = no_coordinate_concrete_fallback_rows(record)
        timeout_fallback = timeout_concrete_fallback_rows(record)
        partial_journal_fallback = partial_journal_concrete_fallback_rows(record)
        witnessed = record.get("witnessed")
        if isinstance(witnessed, int):
            out["witnessed"] += witnessed
            gap = (witnessed - len(certified) - len(not_certified) - len(no_coord_fallback) -
                   len(timeout_fallback) - len(partial_journal_fallback))
            if gap > 0:
                out["no_verdict"] += gap
        else:
            out["witnessed_unknown"] += 1
        for enc, reason in not_certified.items():
            cls = classify_not_certified(record, enc, reason)
            out[cls] += 1
        out["concrete_fallback"] += len(no_coord_fallback)
        out["concrete_fallback"] += len(timeout_fallback)
        out["concrete_fallback"] += len(partial_journal_fallback)
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
    args.lift_unconstrained_sender = STRONG_PUT_LIFT_UNCONSTRAINED_SENDER
    args.propose_r2 = True
    args.r2_depth = STRONG_PUT_R2_DEPTH
    args.r2_term_budget = STRONG_PUT_R2_TERM_BUDGET
    args.r2_candidate_budget = STRONG_PUT_R2_CANDIDATE_BUDGET
    args.fuzz_r2_prefilter = True
    args.fuzz_runs = STRONG_PUT_FUZZ_RUNS
    args.fuzz_r2_candidate_budget = STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET
    # v28: the recipe's arithmetic checks on the R1/R2 proof queries, unless the
    # caller named its own --proof-esbmc-arg list.
    if not getattr(args, "proof_esbmc_arg", None):
        args.proof_esbmc_arg = list(STRONG_PUT_PROOF_ESBMC_ARGS)
    return STRONG_RECIPE_VERSION


def append_stage4_driver_options(cmd, args, path_function, exit_kind, stage2_source,
                                 stage2_witness_check, piece, pins):
    if args.foundry_fixture:
        cmd += ["--foundry-fixture", args.foundry_fixture]
    if args.auto_partial_loops:
        cmd += ["--auto-partial-loops"]
    if args.lift_unconstrained_calldata:
        cmd += ["--lift-unconstrained-calldata"]
    if getattr(args, "lift_unconstrained_sender", False):
        cmd += ["--lift-unconstrained-sender"]
    if getattr(args, "synthetic_args_from_ce", False):
        cmd += ["--synthetic-args-from-ce"]
    if path_function:
        cmd += ["--path-function", path_function]
    if exit_kind:
        cmd += ["--exit-kind", exit_kind]
    if args.propose_r2 and not is_concrete_only_stage2_source(stage2_source):
        cmd += [
            "--propose-r2", "--r2-depth",
            str(args.r2_depth), "--r2-term-budget",
            str(args.r2_term_budget), "--r2-candidate-budget",
            str(args.r2_candidate_budget), "--oracle-max-parts",
            str(getattr(args, "oracle_max_parts", 4)), "--oracle-max-split-depth",
            str(getattr(args, "oracle_max_split_depth", 2))
        ]
    if args.fuzz_r2_prefilter and not is_concrete_only_stage2_source(stage2_source):
        cmd += [
            "--fuzz-r2-prefilter", "--fuzz-runs",
            str(args.fuzz_runs), "--fuzz-r2-candidate-budget",
            str(args.fuzz_r2_candidate_budget), "--fuzz-r2-prefilter-timeout",
            str(args.forge_timeout)
        ]
    if is_concrete_only_stage2_source(stage2_source):
        concrete_source = STRUCTURAL_STAGE2_SOURCE_ALIASES.get(stage2_source, stage2_source)
        cmd += ["--concrete-only", "--test-suffix", "_fb"]
        cmd += ["--concrete-stage2-source", CONCRETE_ONLY_STAGE2_SOURCES[concrete_source]]
        if stage2_witness_check:
            cmd += [
                "--concrete-stage2-witness-check",
                str(stage2_witness_check),
            ]
    for extra in args.esbmc_arg:
        cmd.append(f"--esbmc-arg={extra}")
    for extra in getattr(args, "proof_esbmc_arg", None) or []:
        cmd.append(f"--proof-esbmc-arg={extra}")
    # ONLY when this row IS a piece, so an unsplit region's command line is
    # byte-identical to every one already recorded.
    if piece:
        cmd += ["--piece", str(piece)]
    for n, v in pins.items():
        # `extcall.*` pins are NOT query coordinates for the assertion ladder
        # (ESBMC's --path-cov-assert cannot express them and REFUSES the whole
        # ladder); they travel as --extcall-pins, where the renderer realises
        # them as mocks and funds the contract (TODO 30, motivation freeS: the
        # recipe's explicit `--pin extcall.ok1=1` reached here, the ladder was
        # refused, no `return` oracle, and the PUT was RED on the reference for
        # want of balance).
        if str(n).startswith("extcall."):
            continue
        cmd += ["--pin", f"{n}={v}"]
    return cmd


def merged_extcall_pins(certified_detail, pins):
    """Stage-2 `extcall_pins` plus any `extcall.*` entry of the record's pins."""
    out = dict((certified_detail or {}).get("extcall_pins") or {})
    for n, v in (pins or {}).items():
        if str(n).startswith("extcall.") and n not in out:
            out[n] = v
    return out


def append_row_esbmc_args(cmd, row_args, cli_args):
    """Carry Stage-2 ESBMC options into Stage 4 without duplicating CLI args."""
    seen = set(cli_args or [])
    for extra in row_args or []:
        if extra in seen:
            continue
        cmd.append(f"--esbmc-arg={extra}")
        seen.add(extra)
    return cmd


def stage2_source_record_name(stage2_source):
    stage2_source = STRUCTURAL_STAGE2_SOURCE_ALIASES.get(stage2_source, stage2_source)
    return CONCRETE_ONLY_STAGE2_SOURCES.get(stage2_source, stage2_source or "certified_region")


def is_concrete_only_stage2_source(stage2_source):
    normalized = STRUCTURAL_STAGE2_SOURCE_ALIASES.get(stage2_source, stage2_source)
    return normalized in CONCRETE_ONLY_STAGE2_SOURCES


def normalize_stage2_concrete_fallback_record(rec, stage2_source, witness_check=None):
    if not is_concrete_only_stage2_source(stage2_source):
        return rec
    out = dict(rec or {})
    out["kind"] = "concrete"
    out["stage2_source"] = stage2_source_record_name(stage2_source)
    if witness_check:
        out["stage2_witness_check"] = str(witness_check)
    return out


def stage4_output_tail(stdout, stderr, limit=4000):
    text = ((stdout or "") + ("\n" if stdout and stderr else "") + (stderr or ""))
    text = text.strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def stage4_missing_failure_reason(returncode, stdout, stderr):
    tail = stage4_output_tail(stdout, stderr, limit=1200)
    if returncode == 124:
        return "stage4-driver-timeout-no-put-json"
    low = tail.lower()
    if "path depth unavailable" in low or "depth" in low and "unavailable" in low:
        return "path-depth-unavailable-no-put-json"
    if "storage layout" in low and ("unavailable" in low or "missing" in low):
        return "storage-layout-unavailable-no-put-json"
    if "zero unconditional assertions" in low:
        return "zero-unconditional-assertions-no-put-json"
    if "traceback" in low:
        return "stage4-python-exception-no-put-json"
    if "refused" in low:
        return "stage4-refused-no-put-json"
    if returncode not in (0, None):
        return f"stage4-exit-{returncode}-no-put-json"
    return "stage4-missing-put-json"


def stage4_missing_record(stage2_source,
                          witness_check=None,
                          failure_reason=None,
                          emit_wall_s=None,
                          generation_timeout_s=None,
                          returncode=None,
                          stdout=None,
                          stderr=None):
    """Metadata stub for a Stage-4 row whose driver wrote no put.json.

    The B gate still needs to account for the attempted row.  A missing
    put.json used to collapse to `{}`, and the later readers defaulted that to
    a certified PUT.  That is wrong for concrete fallback rows: the emitter
    failed before producing a replay, but the row is still concrete-only and
    must not inflate refused-PUT or no-R1/R2 queues.
    """
    concrete_only = stage2_source in CONCRETE_ONLY_STAGE2_SOURCES
    kind = "concrete" if concrete_only else "refusal"
    refused = stage4_missing_failure_reason(returncode, stdout, stderr)
    if concrete_only:
        refused = "concrete-" + refused
    reason = failure_reason or "Stage 4 produced no put.json"
    rec = {
        "kind": kind,
        "stage2_source": stage2_source_record_name(stage2_source),
        "refused": refused,
        "refusal_reason": reason,
        "stage4_returncode": returncode,
        "stage4_output_tail": stage4_output_tail(stdout, stderr),
        "stats": {
            "fuzz_params": 0,
            "asserts": 0,
            "guarded_asserts": 0,
            "rendered_width": {},
            "oracle_classes": [],
            "oracle_class_counts": {},
            "oracle_class_combinations": [],
            "oracle_class_combo_counts": {},
            "assertion_oracles": [],
        },
    }
    if concrete_only:
        rec["concrete_reason"] = reason
    timing = {}
    if generation_timeout_s is not None:
        timing["generation_timeout_s"] = generation_timeout_s
    if emit_wall_s is not None:
        timing["put_driver_wall_s"] = round(emit_wall_s, 3)
        timing["generation_wall_s"] = round(emit_wall_s, 3)
    if timing:
        rec["timing"] = timing
    return normalize_stage2_concrete_fallback_record(rec, stage2_source, witness_check)


def stage4_kind_from_source(stage2_source, recorded=None):
    if recorded:
        return recorded
    stage2_source = STRUCTURAL_STAGE2_SOURCE_ALIASES.get(stage2_source, stage2_source)
    if stage2_source == "structural-deploy-only":
        return "deploy-only"
    if stage2_source == "structural-getter-only":
        return "getter-only"
    if stage2_source in CONCRETE_ONLY_STAGE2_SOURCES:
        return CONCRETE_ONLY_STAGE2_SOURCES[stage2_source]
    return "certified-region"


def enrich_stage4_record(rec,
                         *,
                         stage2_source,
                         witness_check=None,
                         stage4_kind=None,
                         certification_source=None,
                         certified_detail=None,
                         emit_wall_s=None):
    out = dict(rec or {})
    if (certification_source
            and not (stage2_source == "certified-region"
                     and certification_source == "structural-abi-getter-no-coordinate")):
        stage2_source = STRUCTURAL_STAGE2_SOURCE_ALIASES.get(certification_source, stage2_source)
    stage2_source = STRUCTURAL_STAGE2_SOURCE_ALIASES.get(stage2_source, stage2_source)
    out.setdefault("stage2_source", stage2_source_record_name(stage2_source))
    if witness_check:
        out.setdefault("stage2_witness_check", str(witness_check))
    recorded_kind = out.get("stage4_kind")
    if stage4_kind and recorded_kind in (None, "refusal", "certified-region"):
        out["stage4_kind"] = stage4_kind
    else:
        out.setdefault("stage4_kind", stage4_kind_from_source(stage2_source, stage4_kind))
    if certification_source:
        out.setdefault("certification_source", certification_source)
    if certified_detail:
        out.setdefault("certified_detail_stage4_kind", certified_detail.get("stage4_kind"))
        out.setdefault("certified_detail_source", certified_detail.get("certification_source"))
    timing = dict(out.get("timing") or {})
    if emit_wall_s is not None:
        timing.setdefault("put_driver_wall_s", round(emit_wall_s, 3))
    if timing:
        out["timing"] = timing
    return out


def _oracle_details_for_row(rec, stats):
    materialization = rec.get("materialization") or {}
    return (stats.get("assertion_oracles") or rec.get("assertion_oracles")
            or materialization.get("assertion_oracles") or [])


def _oracle_class_summary_from_details(details):
    order = {"R0": 0, "R1": 1, "R2": 2}
    seen = set()
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        for cls in detail.get("classes") or []:
            seen.add(str(cls))
    return sorted(seen, key=lambda item: order.get(item, 99))


def _oracle_class_counts_from_details(details):
    counts = {}
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        for cls in detail.get("classes") or []:
            key = str(cls)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _oracle_combo_counts_from_details(details):
    counts = {}
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        combo = detail.get("class_combo")
        if not combo:
            combo = "+".join(_oracle_class_summary_from_details([detail]))
        if combo:
            counts[str(combo)] = counts.get(str(combo), 0) + 1
    return counts


def row_oracle_classes(rec, stats):
    materialization = rec.get("materialization") or {}
    classes = []
    for source in (stats.get("oracle_classes"), rec.get("oracle_classes"),
                   materialization.get("oracle_classes"),
                   _oracle_class_summary_from_details(_oracle_details_for_row(rec, stats))):
        for cls in source or []:
            cls = str(cls)
            if cls not in classes:
                classes.append(cls)
    order = {"R0": 0, "R1": 1, "R2": 2}
    return sorted(classes, key=lambda item: order.get(item, 99))


def row_oracle_detail(rec, stats):
    return _oracle_details_for_row(rec, stats)


def row_oracle_class_counts(rec, stats):
    materialization = rec.get("materialization") or {}
    counts = {}
    for source in (_oracle_class_counts_from_details(_oracle_details_for_row(rec, stats)),
                   materialization.get("oracle_class_counts"), rec.get("oracle_class_counts"),
                   stats.get("oracle_class_counts")):
        for key, value in (source or {}).items():
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            key = str(key)
            counts[key] = max(counts.get(key, 0), value)
    return counts


def row_oracle_class_combinations(rec, stats):
    materialization = rec.get("materialization") or {}
    combos = set()
    for source in (stats.get("oracle_class_combinations"), rec.get("oracle_class_combinations"),
                   materialization.get("oracle_class_combinations"),
                   sorted(_oracle_combo_counts_from_details(_oracle_details_for_row(rec, stats)))):
        combos.update(str(item) for item in (source or []) if item)
    return sorted(combos)


def row_oracle_class_combo_counts(rec, stats):
    materialization = rec.get("materialization") or {}
    counts = {}
    for source in (_oracle_combo_counts_from_details(_oracle_details_for_row(rec, stats)),
                   materialization.get("oracle_class_combo_counts"),
                   rec.get("oracle_class_combo_counts"), stats.get("oracle_class_combo_counts")):
        for key, value in (source or {}).items():
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            key = str(key)
            counts[key] = max(counts.get(key, 0), value)
    return counts


def int_field(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def row_has_parameterized_width(stats):
    if int_field(stats.get("fuzz_params")) <= 0:
        return False
    for width in (stats.get("rendered_width") or {}).values():
        try:
            if int(width) > 1:
                return True
        except (TypeError, ValueError):
            continue
    return False


def missing_fixed_replay_fusion_reason(rec):
    """Reason a physical PUT row cannot be counted before fixed replay fusion."""
    if not isinstance(rec, dict) or not rec.get("requires_fixed_replay_fusion"):
        return None
    if isinstance(rec.get("fixed_replay_fusion"), dict):
        return None
    return (rec.get("fixed_replay_fusion_error")
            or "required fixed replay assertions were not fused into this PUT")


def row_quality_fields(rec,
                       stats,
                       *,
                       is_put,
                       is_concrete,
                       refused=False,
                       stale=None,
                       put_failure_reason=None):
    """Uniform no-PUT/no-R1R2 accounting for raw and valid row JSON."""
    materialization = rec.get("materialization") or {}
    oracle_classes = row_oracle_classes(rec, stats)
    has_r1r2 = bool(is_put and set(oracle_classes) & {"R1", "R2"})
    try:
        asserts = int(stats.get("asserts") or 0)
        guarded = int(stats.get("guarded_asserts") or 0)
    except (TypeError, ValueError):
        asserts, guarded = 0, 0
    uncond = asserts - guarded
    assertion_backed = uncond > 0 and bool(oracle_classes)
    parameterized_width = row_has_parameterized_width(stats)
    materialized_put = materialization.get("is_put") is True
    assertion_backed_parameterized = (assertion_backed and parameterized_width and materialized_put)
    non_parameterized_oracle = bool(is_concrete and assertion_backed and not parameterized_width)
    if put_failure_reason is None:
        put_failure_reason = materialization.get("put_failure_reason")
    if is_put:
        put_failure_reason = None
    elif not put_failure_reason:
        if stale:
            put_failure_reason = "stale artifact from a different binary"
        elif refused:
            put_failure_reason = (rec.get("refusal_reason") or rec.get("refused")
                                  or "Stage 4 refused to materialize a PUT")
        elif assertion_backed_parameterized:
            put_failure_reason = ("parameterized verifier-backed PUT was emitted, but did not "
                                  "pass the reference-valid Foundry gate")
        elif non_parameterized_oracle:
            put_failure_reason = ("verifier-backed oracle rendered, but no emitted coordinate "
                                  "has a fuzzable width greater than one")
        elif is_concrete:
            put_failure_reason = (rec.get("concrete_reason")
                                  or "reference-valid artifact failed PUT fuzz/width gates")
        else:
            put_failure_reason = "not materialized as PUT"
    r1r2_failure = materialization.get("r1r2_failure_reason")
    if has_r1r2:
        r1r2_failure = None
    elif not r1r2_failure:
        if not oracle_classes:
            r1r2_failure = "no verifier-backed oracle class was rendered"
        else:
            r1r2_failure = ("oracle rendered, but only class(es): " + ",".join(oracle_classes))
    return {
        "materialization": materialization,
        "is_put": bool(is_put),
        "is_concrete": bool(is_concrete),
        "put_failure_reason": put_failure_reason,
        "assertion_backed": assertion_backed,
        "assertion_backed_parameterized": assertion_backed_parameterized,
        "assertion_backed_non_parameterized": non_parameterized_oracle,
        "materialized_put": materialized_put,
        "parameterized_width": parameterized_width,
        "has_r1r2": has_r1r2,
        "r1r2_failure_reason": r1r2_failure,
    }


def zero_unconditional_assertions(rec):
    st = rec.get("stats") or {}
    try:
        asserts = int(st.get("asserts") or 0)
        guarded = int(st.get("guarded_asserts") or 0)
    except (TypeError, ValueError):
        return False
    return asserts - guarded <= 0


def certified_region_concrete_fallback_reason(stage2_source, rc, rec):
    """Whether a refused certified-region PUT should retry as concrete-only.

    This does not prove anything about the region.  It is a salvage path for
    rows where Stage 4 already authenticated and selected a concrete case, but
    the PUT builder could not render a deliverable oracle.  The retry emits a
    concrete replay only; Foundry still has to accept it before it is valid.
    """
    if stage2_source not in ("certified-region", "certified_region"):
        return None
    kind = rec.get("kind") or "put"
    if kind == "concrete":
        return None
    refused = rec.get("refused")
    if refused in CERTIFIED_REGION_CONCRETE_FALLBACK_REFUSALS:
        return str(refused)
    if rc == 0 and kind == "put" and zero_unconditional_assertions(rec):
        return "zero-unconditional-assertions"
    return None


def normalize_certified_region_concrete_fallback_record(rec, reason):
    out = dict(rec or {})
    out["kind"] = "concrete"
    out["stage2_source"] = CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE
    out["certified_region_fallback_reason"] = reason
    base = ("certified-region PUT refused as " + str(reason) + "; emitted concrete replay only")
    if out.get("concrete_reason"):
        out["concrete_reason"] = base + " (" + str(out["concrete_reason"]) + ")"
    else:
        out["concrete_reason"] = base
    notes = list(out.get("notes") or [])
    if base not in notes:
        notes.append(base)
    out["notes"] = notes
    return out


def rewrite_stage4_record(path, rec):
    try:
        with open(path, "w") as fh:
            json.dump(rec, fh, indent=2)
    except OSError as exc:
        print(f"  [warn] could not rewrite {path}: {exc}")


CE_COLLECTION_SCHEMA = "veriput-ce-collection/1"
CE_REPLAY_MANIFEST_SCHEMA = "veriput-ce-replay-manifest/1"
CE_REPLAY_CANDIDATE_SCHEMA = "veriput-ce-replay-candidate/1"


def _read_json(path):
    try:
        with open(path) as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return None
    return value


def _ce_collection_artifacts(root):
    """Return ``(case_dir, collection_dir, artifact_dir)`` triples.

    CE collection output is deliberately separate from the canonical RQ1
    result.  Accept a case directory, its ``ce-collection`` directory, one
    artifact directory, or a subjects directory, but never infer a source from
    a neighbouring case.
    """
    root = os.path.abspath(os.path.expanduser(str(root)))
    if os.path.isfile(os.path.join(root, "ce-collection.json")):
        artifact = root
        collection = os.path.dirname(artifact)
        case_dir = os.path.dirname(collection)
        return [(case_dir, collection, artifact)]
    if os.path.basename(root) == "ce-collection":
        collection_dirs = [root]
    elif os.path.isdir(os.path.join(root, "ce-collection")):
        collection_dirs = [os.path.join(root, "ce-collection")]
    else:
        collection_dirs = [
            os.path.join(root, name, "ce-collection") for name in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, name, "ce-collection"))
        ] if os.path.isdir(root) else []
    out = []
    for collection in collection_dirs:
        if not os.path.isdir(collection):
            continue
        case_dir = os.path.dirname(collection)
        for name in sorted(os.listdir(collection)):
            artifact = os.path.join(collection, name)
            if not os.path.isdir(artifact):
                continue
            if os.path.isfile(os.path.join(artifact, "ce-collection.json")):
                out.append((case_dir, collection, artifact))
    return out


def _ce_cert_rows(collection_dir):
    path = os.path.join(collection_dir, "certify-results.jsonl")
    rows = []
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ce_name_value_list(value):
    """Keep witness values losslessly and reject malformed entries."""
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if not isinstance(item, dict) or not item.get("name"):
            return None
        if "value" not in item:
            return None
        out.append({"name": str(item["name"]), "value": item["value"]})
    return out


def _ce_path_witnesses(path_summary):
    """Return path witnesses, preferring the nested complete journal list."""
    nested = path_summary.get("witnesses")
    if isinstance(nested, list) and nested:
        return [item for item in nested if isinstance(item, dict)]
    # A one-witness journal stores the path summary itself as the witness.
    return [path_summary] if isinstance(path_summary, dict) else []


def _ce_row_for_path(rows, unit, path_function, path_id):
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("unit") != unit:
            continue
        if path_function and row.get("path_function") != path_function:
            continue
        journal = row.get("partial_witness_journal") or {}
        paths = journal.get("paths") if isinstance(journal, dict) else []
        if any(
                str(item.get("path_id")) == str(path_id) for item in paths
                if isinstance(item, dict)):
            return row
        # The CE-only archive may have only the path summary, so a matching
        # unit/path row is still the authoritative no-formal-result guard.
        if path_function == row.get("path_function"):
            return row
    return None


def _ce_candidate(case_dir, collection_dir, artifact_dir, collection, summary, row, journal,
                  path_summary, witness, index):
    subject = (summary or {}).get("subject_id") or os.path.basename(case_dir)
    benchmark = (summary or {}).get("benchmark")
    contract = ((summary or {}).get("contract") or collection.get("contract"))
    unit = ((summary or {}).get("unit") or collection.get("unit"))
    path_function = (path_summary.get("path_function") or collection.get("path_function"))
    path_id = path_summary.get("path_id")
    if not subject or not benchmark or not contract or not unit:
        return None, "missing case identity"
    if not path_function or path_id is None:
        return None, "missing path identity"
    if not isinstance(journal, dict) or journal.get("complete") is not True:
        return None, "journal is not complete"
    if journal.get("partial") is True:
        return None, "journal is partial"
    if journal.get("kind") != "solidity-complete-path-ce-journal":
        return None, "journal kind is not a complete path witness journal"
    if not isinstance(row, dict):
        return None, "missing CE certification row"
    if row.get("certified"):
        return None, "CE row unexpectedly contains a certified region"
    if row.get("valid") or row.get("put") or row.get("r1r2"):
        return None, "CE row unexpectedly contains a formal result"
    inputs = _ce_name_value_list(witness.get("inputs"))
    entry_storage = _ce_name_value_list(witness.get("entry_storage"))
    environment = _ce_name_value_list(witness.get("env"))
    if inputs is None or entry_storage is None or environment is None:
        return None, "witness lacks lossless inputs/state/environment lists"
    if witness.get("entry_storage_known") is not True:
        return None, "entry storage is not known"
    digest_input = json.dumps(
        [subject, unit, str(path_id), index, inputs, entry_storage, environment],
        sort_keys=True,
        separators=(",", ":"),
        default=str).encode()
    candidate_id = "ce-" + hashlib.sha256(digest_input).hexdigest()[:20]
    return {
        "schema": CE_REPLAY_CANDIDATE_SCHEMA,
        "candidate_id": candidate_id,
        "candidate_kind": "concrete-replay-candidate",
        "status": "candidate-only",
        "proof_status": "not-proven",
        "source": {
            "stage": "ce-collection",
            "evidence": "complete-path-witness",
            "refutation_only": True,
            "case_dir": case_dir,
            "artifact_dir": artifact_dir,
            "journal": os.path.join(artifact_dir, "ce-witness-journal.json"),
            "certification_row": os.path.join(collection_dir, "certify-results.jsonl"),
            "bucket": row.get("bucket"),
            "diagnostic": row.get("driver_diagnostic"),
        },
        "case": {
            "benchmark": benchmark,
            "subject_id": subject,
            "contract": contract,
            "unit": unit,
        },
        "path": {
            "path_function": str(path_function),
            "path_id": str(path_id),
            "path_id_stable": path_summary.get("path_id_stable"),
            "claim": path_summary.get("claim"),
            "path_depth": path_summary.get("path_depth"),
        },
        "replay": {
            "inputs": inputs,
            "entry_storage": entry_storage,
            "environment": environment,
            "final_state": _ce_name_value_list(witness.get("final_state")) or [],
            "extcall_returns": _ce_name_value_list(witness.get("extcall_returns")) or [],
            "return_value": witness.get("return_value"),
            "return_value_known": witness.get("return_value_known"),
            "revert_pre_rollback": witness.get("revert_pre_rollback"),
        },
        "rq1_accounting": {
            "raw": 0,
            "valid": 0,
            "put": 0,
            "r1r2": 0,
            "theory_delta": 0,
            "counted_as_formal_result": False,
        },
        "next_step": {
            "requires_concrete_replay_verification": True,
            "requires_foundry_double_oracle": True,
            "assertions": [],
            "oracle": None,
        },
    }, None


def materialize_ce_replay_candidates(roots, output_path=None, max_witnesses_per_path=1):
    """Archive concrete CE candidates without entering the RQ1 result flow."""
    if max_witnesses_per_path <= 0:
        print("--ce-max-witnesses-per-path must be positive")
        return 2
    candidates, skipped, seen = [], [], set()
    for root in roots:
        for case_dir, collection_dir, artifact_dir in \
                _ce_collection_artifacts(root):
            collection = _read_json(os.path.join(artifact_dir, "ce-collection.json"))
            journal = _read_json(os.path.join(artifact_dir, "ce-witness-journal.json"))
            summary = _read_json(os.path.join(collection_dir, "summary.json"))
            if not isinstance(collection, dict) or collection.get("schema") != CE_COLLECTION_SCHEMA:
                skipped.append({"artifact": artifact_dir, "reason": "invalid CE collection schema"})
                continue
            if collection.get("status") != "witnessed":
                skipped.append({"artifact": artifact_dir, "reason": "collection is not witnessed"})
                continue
            rows = _ce_cert_rows(collection_dir)
            witnesses = ((journal or {}).get("witnesses") if isinstance(journal, dict) else None)
            if not isinstance(witnesses, dict):
                skipped.append({"artifact": artifact_dir, "reason": "missing witness map"})
                continue
            for path_key, path_summary in witnesses.items():
                if not isinstance(path_summary, dict):
                    skipped.append({
                        "artifact": artifact_dir,
                        "path": str(path_key),
                        "reason": "malformed path summary"
                    })
                    continue
                path_function = (path_summary.get("path_function")
                                 or collection.get("path_function"))
                path_id = path_summary.get("path_id")
                row = _ce_row_for_path(rows, collection.get("unit"), path_function, path_id)
                if row is None:
                    skipped.append({
                        "artifact": artifact_dir,
                        "path": str(path_key),
                        "reason": "no matching CE result row"
                    })
                    continue
                path_witnesses = _ce_path_witnesses(path_summary)
                emitted = 0
                for witness in path_witnesses:
                    if emitted >= max_witnesses_per_path:
                        break
                    candidate, reason = _ce_candidate(case_dir, collection_dir, artifact_dir,
                                                      collection, summary, row, journal,
                                                      path_summary, witness, emitted)
                    if candidate is None:
                        if emitted == 0:
                            skipped.append({
                                "artifact": artifact_dir,
                                "path": str(path_key),
                                "reason": reason
                            })
                        continue
                    if candidate["candidate_id"] in seen:
                        continue
                    seen.add(candidate["candidate_id"])
                    candidates.append(candidate)
                    emitted += 1
                if emitted == 0 and path_witnesses:
                    skipped.append({
                        "artifact": artifact_dir,
                        "path": str(path_key),
                        "reason": "no safe structured witness"
                    })
    manifest = {
        "schema":
        CE_REPLAY_MANIFEST_SCHEMA,
        "mode":
        "ce-materialize",
        "proof_status":
        "refutation-only",
        "formal_results_written":
        False,
        "note": ("Concrete replay candidates only. CE is not proof; no "
                 "candidate is valid, PUT, R1/R2, or theory credit."),
        "candidate_count":
        len(candidates),
        "skipped_count":
        len(skipped),
        "candidates":
        candidates,
        "skipped":
        skipped,
    }
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if output_path:
        output_path = os.path.abspath(os.path.expanduser(output_path))
        if os.path.basename(output_path) in ("result.json", "put.json"):
            print("refusing CE manifest output named result.json or put.json")
            return 2
        try:
            ensure_path_not_protected("--ce-materialize-out", output_path)
        except ValueError as exc:
            print(str(exc))
            return 2
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as fh:
            fh.write(payload)
        print(f"CE replay candidates written: {output_path}")
    else:
        print(payload, end="")
    print(f"CE replay candidates: {len(candidates)}; formal credit: +0")
    return 0 if candidates else 2


def print_stage2_path_accounting(accounting):
    print()
    print("STAGE 2 PATH ACCOUNTING for the selected unit(s)")
    print(f"  records                         : {accounting['records']}")
    print(f"  witnessed paths                 : {accounting['witnessed']}" +
          (f" ({accounting['witnessed_unknown']} record(s) unknown)"
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
    if os.path.islink(lib):
        target = os.readlink(lib)
        target_abs = target if os.path.isabs(target) else os.path.abspath(
            os.path.join(os.path.dirname(lib), target))
        if target_abs != FORGE_STD or not os.path.exists(target_abs):
            os.unlink(lib)
    if not os.path.lexists(lib):
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

    def once(limit):
        proc = subprocess.Popen(["forge", "test", "--json"],
                                cwd=project,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                start_new_session=True)
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=limit)
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
        return proc.returncode, stdout, stderr, timed_out

    rc, stdout, stderr, timed_out = once(timeout)
    config = os.path.join(project, "foundry.toml")
    stack_failure = ("too deep in the stack" in stderr.lower()
                     or "stack too deep" in stderr.lower())
    if rc != 0 and not timed_out and stack_failure and os.path.exists(config):
        with open(config) as stream:
            foundry_toml = stream.read()
        if "via_ir = true" in foundry_toml:
            remaining = timeout - (time.monotonic() - start)
            if remaining > 0.1:
                with open(config, "w") as stream:
                    stream.write(foundry_toml.replace("via_ir = true", "via_ir = false", 1))
                retry_rc, retry_stdout, retry_stderr, retry_timed_out = once(remaining)
                stderr += "\n[forge retry without via-ir]\n" + retry_stderr
                rc, stdout, timed_out = retry_rc, retry_stdout, retry_timed_out
    return (rc, stdout, stderr, timed_out, round(time.monotonic() - start, 3))


def forge_json_status_map(stdout):
    """Return Forge per-test statuses from supported JSON output.

    Foundry has emitted both suite -> test_results and nested result-node JSON
    shapes.  Only explicit per-test status fields are accepted; a process exit
    code or enclosing suite status is not enough to mark a PUT green.

    The primary map is keyed by ``(suite_file, test_name)``.  The name-only map
    is retained only for legacy callers that genuinely have no suite file; when
    the same test name appears in several suites with different statuses, the
    name-only entry is dropped rather than letting one suite decide another.
    """
    try:
        data = json.loads(stdout or "")
    except (TypeError, ValueError):
        return {}, {}, {}
    by_suite_test, by_name_all, suite_failures = {}, {}, {}

    def suite_file_from_key(key):
        if not isinstance(key, str) or not key:
            return None
        return key.split(":", 1)[0]

    def record_test(suite_file, test_name, status):
        if not status or not test_name or test_name == "setUp":
            return
        status = str(status)
        if suite_file:
            suite_file = os.path.normpath(suite_file)
            by_suite_test[(suite_file, test_name)] = status
        by_name_all.setdefault(test_name, set()).add(status)

    def visit(node, suite_file=None, test_name=None):
        if isinstance(node, list):
            for item in node:
                visit(item, suite_file, test_name)
            return
        if not isinstance(node, dict):
            return
        local_suite = suite_file
        for key in ("suite", "suite_name", "file", "path"):
            value = node.get(key)
            if value:
                local_suite = suite_file_from_key(value) or local_suite
                break
        local_test = test_name
        for key in ("name", "test", "test_name"):
            value = node.get(key)
            if isinstance(value, str) and value:
                local_test = value.split("(", 1)[0]
                break
        test_results = node.get("test_results")
        status = node.get("status")
        if status and local_test and not isinstance(test_results, dict):
            if local_test == "setUp" and status == "Failure" and local_suite:
                suite_failures[os.path.normpath(local_suite)] = "Failure"
            else:
                record_test(local_suite, local_test, status)
        if isinstance(test_results, dict):
            setup = test_results.get("setUp()") or test_results.get("setUp")
            if (isinstance(setup, dict) and setup.get("status") == "Failure" and local_suite):
                suite_failures[os.path.normpath(local_suite)] = "Failure"
            for name, result in test_results.items():
                fn = str(name).split("(", 1)[0]
                if fn != "setUp":
                    visit(result, local_suite, fn)
        for key, value in node.items():
            if key == "test_results":
                continue
            if isinstance(value, (dict, list)):
                visit(value, suite_file_from_key(key) or local_suite, local_test)

    if isinstance(data, dict):
        for suite_name, suite in data.items():
            visit(suite, suite_file_from_key(suite_name))
    else:
        visit(data)
    by_name = {
        name: next(iter(statuses))
        for name, statuses in by_name_all.items() if len(statuses) == 1
    }
    return by_suite_test, by_name, suite_failures


def main():
    global OUT
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope",
                    default="focus",
                    help="dispatcher alphabet: focus, whole, or a comma list "
                    "containing the target plus its recorded state "
                    "writers. Default focus with --max-tx 1 is the GATE "
                    "cell; a comma list with --max-tx 2 is an ARTEFACT "
                    "cell.")
    ap.add_argument("--poc",
                    action="store_true",
                    help="read the PoC SET's stage-2 sweep "
                    "(certify/poc_results.jsonl) instead of the corpus's, "
                    "and write every PUT into ONE shared forge project so "
                    "a single `forge test` produces the whole table. The "
                    "two sweeps answer different questions and their rows "
                    "must never share a table: on a real contract 'not "
                    "certified' mixes the method's limits with the "
                    "contract's difficulty, while a PoC is one shape.")
    ap.add_argument("--cert",
                    default=None,
                    help="read the certified regions from THIS file instead of "
                    "the default for --poc / the corpus. Needed the moment "
                    "a sweep has more than one ARM: the --skip-bracket arm "
                    "writes poc_results_skipbracket.jsonl and produced 32 "
                    "certified units the bracketed arm reports as KILLED, "
                    "and without this flag stage 4 could only ever be run "
                    "on whichever arm happens to own the default filename. "
                    "The arm a table came from is printed with it, because "
                    "two arms' PUT counts must never be summed.")
    ap.add_argument("--only",
                    default="",
                    metavar="SELECTOR",
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
    ap.add_argument("--strong-recipe",
                    action="store_true",
                    help="apply the shared versioned Stage-4 recipe "
                    f"({STRONG_RECIPE_VERSION}): auto-unwind, typed R2, "
                    "and one-sided Foundry refutation before ESBMC "
                    "certifies survivors")
    ap.add_argument("--propose-r2",
                    action="store_true",
                    help="passed through to the driver: issue one typed R2 "
                    "candidate batch per certified region")
    ap.add_argument("--r2-depth", type=int, choices=(0, 1), default=1)
    ap.add_argument("--r2-term-budget", type=int, default=96)
    ap.add_argument("--r2-candidate-budget", type=int, default=128)
    ap.add_argument("--oracle-max-parts", type=int, default=4)
    ap.add_argument("--oracle-max-split-depth", type=int, default=2)
    ap.add_argument("--fuzz-r2-prefilter",
                    action="store_true",
                    dest="fuzz_r2_prefilter",
                    help="pass the one-sided Foundry R2 refutation filter to "
                    "the PUT driver; every survivor still goes to ESBMC")
    ap.add_argument("--fuzz-runs", type=int, default=256)
    ap.add_argument("--fuzz-r2-candidate-budget", type=int, default=128)
    ap.add_argument("--forge-only",
                    action="store_true",
                    help="do NOT emit anything: read the put.json each region "
                    "already produced, run `forge test` per project, and "
                    "print the five-gate B table. Costs no esbmc run, so "
                    "B can be re-measured after a forge/solc change or "
                    "after a PUT is edited by hand.")
    ap.add_argument("--emit-cleared-concrete-fallbacks",
                    action="store_true",
                    help="also emit concrete-only replay tests for "
                    "NOT_CERTIFIED paths whose Stage-2 detail has "
                    "concrete_fallback=true and a cleared or complete "
                    "witness status, plus timed-out certification rows "
                    "whose partial journal already names witnessed "
                    "paths. These are raw/valid concrete tests only, "
                    "never PUTs or region proofs.")
    ap.add_argument("--certified-concrete-only",
                    action="store_true",
                    help="materialize every selected CERTIFIED region as its authenticated "
                    "concrete coverage replay instead of rebuilding its PUT. This is "
                    "the replay-persistence/migration entry point: it consumes the "
                    "retained Stage-2 witness, runs the normal Foundry gate, and never "
                    "claims a region or PUT.")
    ap.add_argument("--retain-certified-concrete-replays",
                    action="store_true",
                    help="after each selected certified region emits a PUT, also emit and "
                    "gate its original concrete coverage replay in a distinct workdir. "
                    "Both artifacts remain in the Foundry project and result table.")
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--auto-unwind",
                    type=int,
                    default=0,
                    help="passed to the driver: on an UNDECIDED-TRUNCATED "
                    "ladder, widen the loops the tool NAMED and retry, up "
                    "to N times. aqua `dock` is the recorded case.")
    ap.add_argument("--auto-partial-loops",
                    action="store_true",
                    help="passed to the driver: after --auto-unwind is spent, "
                    "try the ladder once with --partial-loops, the third "
                    "repair named by ESBMC's UNDECIDED-TRUNCATED message")
    ap.add_argument("--lift-unconstrained-calldata",
                    action="store_true",
                    help="passed to the driver: lift declared calldata "
                    "parameters absent from the certified region as "
                    "full-domain fuzz inputs when their type is supported")
    ap.add_argument("--synthetic-args-from-ce",
                    action="store_true",
                    help="passed to the driver: when ESBMC emitted no concrete "
                         "case and VeriPUT synthesizes the preamble itself, "
                         "spell a target parameter the region and the pins do "
                         "NOT fix from the authenticated CE rather than from "
                         "the type's default. Default OFF. See "
                         "solidity_path_put.py --synthetic-args-from-ce for the "
                         "measurement and the StaticBulkRenewal positive "
                         "control.")
    ap.add_argument("--lift-unconstrained-sender",
                    action="store_true",
                    help="passed to the driver: lift sender absent from the "
                    "certified region and pins over Foundry's executable "
                    "nonzero address domain")
    ap.add_argument("--wall-deadline",
                    type=float,
                    default=0.0,
                    help="whole-run wall-clock budget in seconds (0 = none): no new "
                    "row is started after it, and a row started before it gets "
                    "min(--timeout, what is left) as its generation budget. This "
                    "is how a pipeline hands Stage 4 the time Stage 2 left over "
                    "instead of every row assuming it owns the full --timeout")
    ap.add_argument("--timeout",
                    type=int,
                    default=600,
                    help="generation budget for each PUT driver invocation; "
                    "the driver shares it across its ESBMC children and "
                    "reserves time to assemble/write the PUT artifact")
    ap.add_argument("--forge-timeout",
                    type=int,
                    default=300,
                    help="per Forge invocation; Forge is run twice per project "
                    "for replay filtering and the final green gate")
    ap.add_argument("--memlimit-gib",
                    type=int,
                    default=8,
                    metavar="N",
                    help="per ESBMC process; the official POC recipe passes 8")
    ap.add_argument("--esbmc", default=ESBMC, help="ESBMC binary passed to solidity_path_put.py")
    ap.add_argument("--foundry-fixture",
                    default=None,
                    help="JSON fixture passed only to solidity_path_put.py's "
                    "Foundry assembly step. It is not forwarded to the "
                    "ESBMC concrete testcase emission run, so it can "
                    "repair a red local constructor replay without "
                    "changing the certified Stage-2 input.")
    ap.add_argument("--reuse-emitted-dir",
                    default=None,
                    help="pass an original retained Stage-2 emit directory to "
                    "the PUT driver for exact concrete replay recovery")
    ap.add_argument("--proof-esbmc-arg", action="append", default=[], metavar="ARG",
                    help="passed to the PUT driver's k-induction proof queries only "
                    "(R1/R2), appended after its strategy strip; meant for the "
                    "arithmetic checks. See solidity_path_put.py --proof-esbmc-arg.")
    ap.add_argument("--esbmc-arg",
                    action="append",
                    default=[],
                    metavar="ARG",
                    help="one solver/encoder argument passed to every PUT/R2 "
                    "ESBMC invocation. Repeatable; use the = form for "
                    "values beginning with a dash.")
    ap.add_argument("--out-root",
                    default=OUT,
                    help="project and scratch root. A single POC should point "
                    "this at its own output directory.")
    ap.add_argument("--ce-materialize",
                    action="store_true",
                    help="read CE-only complete witness journals and emit an "
                    "isolated concrete replay-candidate manifest. This "
                    "mode never invokes ESBMC, never emits a PUT, and "
                    "never writes result.json or put.json.")
    ap.add_argument("--ce-materialize-root",
                    action="append",
                    default=[],
                    metavar="PATH",
                    help="case dir, ce-collection dir, artifact dir, or a "
                    "subjects dir consumed by --ce-materialize. Repeat "
                    "for multiple roots.")
    ap.add_argument("--ce-materialize-out",
                    default=None,
                    metavar="PATH",
                    help="optional JSON manifest path for --ce-materialize; "
                    "without it the refutation-only manifest is printed")
    ap.add_argument("--ce-max-witnesses-per-path",
                    type=int,
                    default=1,
                    metavar="N",
                    help="maximum deterministic replay candidates per CE path "
                    "(default: 1)")
    args = ap.parse_args()
    if args.ce_materialize:
        if not args.ce_materialize_root:
            ap.error("--ce-materialize requires --ce-materialize-root")
        return materialize_ce_replay_candidates(args.ce_materialize_root, args.ce_materialize_out,
                                                args.ce_max_witnesses_per_path)
    main_start = time.monotonic()
    stage4_recipe_version = apply_strong_put_recipe(args)
    if args.timeout <= 0:
        sys.exit("--timeout must be positive")
    if args.forge_timeout <= 0:
        sys.exit("--forge-timeout must be positive")
    if (args.r2_term_budget <= 0 or args.r2_candidate_budget <= 0 or args.fuzz_runs <= 0
            or args.fuzz_r2_candidate_budget <= 0):
        sys.exit("R2 term/candidate budgets and fuzz run/candidate budgets "
                 "must be positive")
    if args.memlimit_gib <= 0:
        sys.exit("--memlimit-gib must be positive")
    if args.scope not in ("focus", "whole"):
        scope_names = [name.strip() for name in args.scope.split(",") if name.strip()]
        if not scope_names or ",".join(scope_names) != args.scope:
            sys.exit("--scope must be focus, whole, or a canonical "
                     "comma-separated function list")
    try:
        ensure_path_not_protected("--out-root", args.out_root)
    except ValueError as exc:
        out_root_abs = os.path.abspath(args.out_root)
        rq3_ablation_out = (args.certified_concrete_only
                            and (out_root_abs == RQ3_NO_CER_REG_ROOT
                                 or out_root_abs.startswith(RQ3_NO_CER_REG_ROOT + os.sep)))
        if not rq3_ablation_out:
            sys.exit(str(exc))
    OUT = os.path.abspath(args.out_root)
    os.makedirs(OUT, exist_ok=True)
    cert_path = args.cert or (POC_CERT if args.poc else CERT)
    if not os.path.exists(cert_path):
        sys.exit(f"no certify sweep at {cert_path}")
    rows = []
    n_certified = 0  # BEFORE --only, so the header can say what was filtered
    n_cleared_fallback = 0
    n_timeout_fallback = 0
    n_partial_journal_fallback = 0
    n_static_subject_fallback = 0
    n_static_pure_unit_fallback = 0
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
        static_pure_fallbacks = static_pure_unit_concrete_fallback_rows(r, row_subject)
        selected_static_pure = False
        for fb in static_pure_fallbacks:
            try:
                enc_i = int(fb["enc"])
            except ValueError:
                print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                      "static pure-unit fallback path id is not numeric")
                continue
            n_static_pure_unit_fallback += 1
            path_function = fb.get("path_function") or r.get("path_function")
            if not stage4_selector_matches(args.only, key, r["unit"], path_function):
                continue
            selected_static_pure = True
            fb_detail = fb.get("detail") or {}
            rows.append((key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                         stage2_cell_derivation(r), None, 0, row_subject, fb["stage2_source"],
                         fb["region"], {}, fb["pins"], fb_detail.get("witness_check"),
                         fb.get("stage4_kind"), fb_detail.get("certification_source"), fb_detail))
        if args.emit_cleared_concrete_fallbacks:
            for fb in static_subject_concrete_fallback_rows(r, row_subject):
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "static subject fallback path id is not numeric, "
                          "so this concrete fallback cannot be resolved")
                    continue
                n_static_subject_fallback += 1
                path_function = fb.get("path_function") or r.get("path_function")
                if not stage4_selector_matches(args.only, key, r["unit"], path_function):
                    continue
                fb_detail = fb.get("detail") or {}
                rows.append(
                    (key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                     stage2_cell_derivation(r), None, 0, row_subject, fb["stage2_source"],
                     fb["region"], {}, fb["pins"], fb_detail.get("witness_check"),
                     fb.get("stage4_kind"), fb_detail.get("certification_source"), fb_detail))
            for fb in cleared_concrete_fallback_rows(r):
                fb_detail = fb.get("detail") or {}
                pin_excluded = (
                    fb_detail.get("witness_check") == "PIN-EXCLUDED-NO-COORDINATE"
                    or "EXCLUDED FROM THE SLICE by the pins" in str(fb.get("reason") or "")
                    or "EXCLUDED FROM THE SLICE by the pins" in str(fb_detail.get("reason") or ""))
                if selected_static_pure and pin_excluded:
                    continue
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "not-certified key is not numeric, so this concrete "
                          "fallback cannot be resolved to a path")
                    continue
                n_cleared_fallback += 1
                if not stage4_selector_matches(args.only, key, r["unit"], r.get("path_function")):
                    continue
                path_function = fb.get("path_function") or r.get("path_function")
                exit_kind = (report_exit_kind(r.get("enumeration_report"), path_function, enc_i)
                             or row_exit_kind(r, fb_detail))
                fallback_source = ("no-coordinate-concrete-fallback"
                                   if pin_excluded else "cleared-concrete-fallback")
                rows.append((key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                             stage2_cell_derivation(r), exit_kind, None, row_subject,
                             fallback_source, fb["region"], {}, fb["pins"],
                             fb_detail.get("witness_check"), fb_detail.get("stage4_kind"),
                             fb_detail.get("certification_source"), fb_detail))
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
                if not stage4_selector_matches(args.only, key, r["unit"], path_function):
                    continue
                fb_detail = fb.get("detail") or {}
                exit_kind = (report_exit_kind(r.get("enumeration_report"), path_function, enc_i)
                             or row_exit_kind(r, fb_detail))
                rows.append((key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                             stage2_cell_derivation(r), exit_kind, None, row_subject,
                             "timeout-concrete-fallback", fb["region"], {}, fb["pins"],
                             fb_detail.get("witness_check"), fb_detail.get("stage4_kind"),
                             fb_detail.get("certification_source"), fb_detail))
            for fb in partial_journal_concrete_fallback_rows(r):
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "partial witness path id is not numeric, so this "
                          "partial-journal concrete fallback cannot be "
                          "resolved")
                    continue
                n_partial_journal_fallback += 1
                path_function = fb.get("path_function") or r.get("path_function")
                if not stage4_selector_matches(args.only, key, r["unit"], path_function):
                    continue
                fb_detail = fb.get("detail") or {}
                exit_kind = (report_exit_kind(r.get("enumeration_report"), path_function, enc_i)
                             or row_exit_kind(r, fb_detail))
                rows.append((key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                             stage2_cell_derivation(r), exit_kind, None, row_subject,
                             "partial-journal-concrete-fallback", fb["region"], {}, fb["pins"],
                             fb_detail.get("witness_check"), fb_detail.get("stage4_kind"),
                             fb_detail.get("certification_source"), fb_detail))
            for fb in no_coordinate_concrete_fallback_rows(r):
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "complete no-coordinate witness path id is not "
                          "numeric, so this concrete fallback cannot be "
                          "resolved")
                    continue
                n_cleared_fallback += 1
                path_function = fb.get("path_function") or r.get("path_function")
                if not stage4_selector_matches(args.only, key, r["unit"], path_function):
                    continue
                fb_detail = fb.get("detail") or {}
                exit_kind = (report_exit_kind(r.get("enumeration_report"), path_function, enc_i)
                             or row_exit_kind(r, fb_detail))
                rows.append((key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                             stage2_cell_derivation(r), exit_kind, None, row_subject,
                             "no-coordinate-concrete-fallback", fb["region"], {}, fb["pins"],
                             fb_detail.get("witness_check"), fb_detail.get("stage4_kind"),
                             fb_detail.get("certification_source"), fb_detail))
        elif (not args.emit_cleared_concrete_fallbacks and not selected_static_pure
              and authenticated_pin_excluded_fallback_rows(r)):
            # This witness is authenticated by Stage 2 but lies outside the
            # normal slice because the non-payable ABI gate rejected
            # msg.value. Consume it as a concrete unit replay; do not turn it
            # into a certified region or a PUT claim. Without this narrow
            # branch the loop falls through to the deploy-only safety net,
            # losing the target unit call entirely.
            for fb in authenticated_pin_excluded_fallback_rows(r):
                try:
                    enc_i = int(fb["enc"])
                except ValueError:
                    print(f"  SKIP {key}.{r['unit']} enc={fb['enc']}: the "
                          "authenticated pin-excluded path id is not "
                          "numeric, so this concrete fallback cannot be "
                          "resolved")
                    continue
                n_cleared_fallback += 1
                path_function = fb.get("path_function") or r.get("path_function")
                if not stage4_selector_matches(args.only, key, r["unit"], path_function):
                    continue
                fb_detail = fb.get("detail") or {}
                exit_kind = (report_exit_kind(r.get("enumeration_report"), path_function, enc_i)
                             or row_exit_kind(r, fb_detail))
                rows.append((key, is_poc, r["unit"], path_function, enc_i, None, None, [], False,
                             stage2_cell_derivation(r), exit_kind, None, row_subject,
                             "no-coordinate-concrete-fallback", fb["region"], {}, fb["pins"],
                             fb_detail.get("witness_check"), fb.get("stage4_kind"),
                             fb_detail.get("certification_source"), fb_detail))
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
                print(f"REFUSED: {key}.{r['unit']} enc={enc_s} was recorded "
                      f"under recipe {r.get('recipe_version')}, which may "
                      "certify entry relations that are NOT recoverable from "
                      "the prose `certified` region string. This row has no "
                      "matching `certified_details` entry, so Stage 4 cannot "
                      "know whether it must materialize an entry relation such "
                      "as `state._owner := msg.sender`. Re-run Stage 2 with the "
                      "current certify_all.py instead of silently emitting a PUT "
                      "for a different entry slice.")
                return 2
            detail_piece = details.get("piece")
            if detail_piece is not None:
                if piece and str(detail_piece) != piece:
                    print(f"REFUSED: {key}.{r['unit']} enc={enc_s} has piece {piece} "
                          f"in the certified key but piece {detail_piece} in its detail")
                    return 2
                piece = str(detail_piece)
            witness_return = stage2_witness_return(r, enc_i, r.get("path_function"), details)
            if witness_return is not None:
                details = dict(details)
                detail_ce = dict(details.get("ce") or {})
                detail_ce.setdefault("return", witness_return)
                details["ce"] = detail_ce
            establish = details.get("established") or []
            detail_region, detail_holes, detail_pins = \
                parse_certified_detail_region(details, parse_pins(r.get("pins")))
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
            deriv = {
                k: r.get(k)
                for k in ("level0", "level0_perturb", "level0_points", "probe_ladder",
                          "probe_ladder_budget", "probes", "geometric_bracket",
                          "sibling_subtraction", "claim_budget", "shrink_rounds", "refine_rounds",
                          "skip_bracket", "cut_policy", "max_region_pieces", "max_holes",
                          "esbmc_args", "scope", "max_tx") if r.get(k) is not None
            }
            if "refinement_used" in details:
                deriv["region_refinement_used"] = bool(details["refinement_used"])
            (detail_region, detail_holes, detail_pins,
             region_derivation) = widen_structural_getter_sender_region(
                 row_subject, r["unit"], r.get("path_function"), details, detail_region,
                 detail_holes, detail_pins)
            if region_derivation:
                deriv["region_derivation"] = region_derivation
                print(f"  [region] {key}.{r['unit']} enc={enc_s}: widened "
                      "the source-checked unconstrained msg.sender coordinate "
                      "for Stage 4 PUT materialization")
            n_certified += 1
            if not stage4_selector_matches(args.only, key, r["unit"], r.get("path_function")):
                continue
            exit_kind = (report_exit_kind(r.get("enumeration_report"), r.get("path_function"),
                                          enc_i) or row_exit_kind(r, details))
            certified_depth = details.get("depth")
            detail_stage4_kind = certified_detail_stage4_kind(details)
            if detail_stage4_kind == "getter-only" and exit_kind is None:
                exit_kind = "normal"
            if detail_stage4_kind == "deploy-only":
                detail_stage2_source = "structural-deploy-only"
            elif detail_stage4_kind == "getter-only":
                detail_stage2_source = ("certified-region"
                                        if region_derivation else "structural-getter-only")
            else:
                detail_stage2_source = (CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE
                                        if args.certified_concrete_only else "certified-region")
            detail_piece = None if detail_stage4_kind == "getter-only" else (piece or None)
            rows.append(
                (key, is_poc, r["unit"], r.get("path_function"), enc_i, detail_piece, text,
                 establish, bool(r.get("pin_extcall")), deriv, exit_kind, certified_depth,
                 row_subject, detail_stage2_source, detail_region, detail_holes, detail_pins,
                 ("CERTIFIED-BASIS-REPLAY" if args.certified_concrete_only else None),
                 detail_stage4_kind, details.get("certification_source"), details))

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
    print(
        f"=== {n_certified} CERTIFIED region(s) recorded by stage 2 "
        f"({os.path.basename(cert_path)}) ===" +
        (f"\n=== {n_cleared_fallback} cleared concrete fallback(s) "
         "available from Stage 2 ===" if args.emit_cleared_concrete_fallbacks else "") +
        (f"\n=== {n_timeout_fallback} timeout concrete fallback(s) "
         "available from Stage 2 partial witnesses ===" if args.
         emit_cleared_concrete_fallbacks else "") +
        (f"\n=== {n_partial_journal_fallback} partial-journal concrete "
         "fallback(s) available from Stage 2 witnesses ===" if args.
         emit_cleared_concrete_fallbacks else "") +
        (f"\n=== {n_static_subject_fallback} static subject concrete "
         "fallback(s) available from target-contract ABI shape ===" if args.
         emit_cleared_concrete_fallbacks else "") +
        (f"\n=== {n_static_pure_unit_fallback} static pure-unit concrete "
         "fallback candidate(s) from source dependency facts ==="
         if n_static_pure_unit_fallback else "") +
        (f"\n=== --only '{args.only}' keeps {len(rows)} of them; the "
         f"other {(n_certified + n_cleared_fallback + n_timeout_fallback + n_partial_journal_fallback + n_static_subject_fallback + n_static_pure_unit_fallback) - len(rows)} were NOT measured by this run "
         f"and their absence is a filter, not a result ===" if args.only else ""))
    stage2_accounting = stage2_path_accounting(cert_path, args.only)
    print_stage2_path_accounting(stage2_accounting)
    if arm:
        print(f"=== ARM {arm[2:]}: PUTs go to their OWN project and workdir, so "
              f"this table does not overwrite or get confused with another "
              f"arm's. Two arms' PUT counts must never be summed. ===")
    results = []
    retained_basis_results = []
    certified_detail_by_put_record = {}
    certified_detail_by_basis_record = {}
    retained_basis_targets = {}
    record_paths = {}
    # ⛔ AN --only THAT SELECTS NOTHING IS A HARD FAILURE. The sweep would
    # otherwise print a complete, well-formed `0 of 0 certified region(s)`
    # table and exit 0 -- indistinguishable from "this arm has no regions",
    # which is a different fact entirely. Same shape as poc_funnel's --only,
    # and it is here because that one was found by being read wrong first.
    if args.only and not rows:
        total_rows = (n_certified + n_cleared_fallback + n_timeout_fallback +
                      n_partial_journal_fallback + n_static_subject_fallback)
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
        return {"normal": 0, "unknown": 1, None: 2, "revert": 3}.get(kind, 2)

    def _stage2_priority(source):
        return 1 if is_concrete_only_stage2_source(source) else 0

    def _structural_priority(stage4_kind, certification_source):
        # ITEM 2: a structural ABI/getter value-gate row is not method output
        # and never counts as a PUT, yet it used to be ordered FIRST inside its
        # unit (lower enc, revert exit) and took the unit's whole Stage-4
        # budget proving R1/R2 on the compiler's gate -- measured 2026-08-22 on
        # DnGmxBatchingManager: six units, 107 s each, six gate tests with
        # R1/R2 and every real-region row refused "not fused yet". Real rows
        # go first; the gate rows get whatever is left.
        return 1 if is_structural_certificate_row({
            "stage4_kind": stage4_kind, "certification_source": certification_source}) else 0

    ordered_rows = [
        r for _i, r in sorted(enumerate(rows),
                              key=lambda ir:
                              (_structural_priority(ir[1][18], ir[1][19]),
                               _stage2_priority(ir[1][13]), _exit_priority(ir[1][10]), ir[0]))
    ]
    if ordered_rows != rows:
        print("[order] solver-certified region(s) are emitted before structural "
              "ABI/getter gate rows, certified/non-concrete Stage-2 region(s) "
              "before concrete fallback rows, then normal exits before other "
              "exit kinds. This changes scheduling only; regions and "
              "certification are unchanged")
    cleaned_projects = set()
    # See shape_level_concrete_refusal_reason() for why one refusal per
    # unit is enough for these rows.
    shape_refused_units = {}
    shape_refusal_skips = []
    for (bench, is_poc, unit, path_function, enc, piece, text, establish, pin_extcall, deriv,
         exit_kind, stage2_depth, row_subject, stage2_source, region_override, holes_override,
         pins_override, stage2_witness_check, stage4_kind, certification_source,
         certified_detail) in ordered_rows:
        # The label every downstream name is built from, derived ONCE and in
        # the same shape the emitter builds it (`p<K>`). Two derivations is how
        # the gate below comes to look up a function the emitted file does not
        # contain.
        encs = f"{enc}#{piece}" if piece else str(enc)
        row_timeout = args.timeout
        if args.wall_deadline and args.wall_deadline > 0:
            _left = args.wall_deadline - (time.monotonic() - main_start)
            if _left < 30:
                print(f"[stage4] WALL DEADLINE: {args.wall_deadline:.0f}s budget, "
                      f"{max(0.0, _left):.0f}s left -- {bench}.{unit} enc={encs} and every "
                      f"later row NOT STARTED (the rows already written stand; this is "
                      f"the pipeline's clock, not a per-row verdict)")
                break
            row_timeout = int(min(args.timeout, _left))
        shape_refusal = shape_refused_units.get((bench, unit))
        if shape_refusal and stage2_source in CLEARED_FALLBACK_SOURCES:
            print(f"  SKIP {bench}.{unit} enc={encs}: the cleared concrete "
                  f"fallback of this unit was already refused at enc="
                  f"{shape_refusal['enc']} for a reason that judges the "
                  f"synthesized shape, which no other path of the same unit "
                  f"changes: {shape_refusal['reason']}")
            shape_refusal_skips.append({
                "benchmark": bench,
                "unit": unit,
                "enc": enc,
                "piece": piece,
                "stage2_source": stage2_source,
                "first_refused_enc": shape_refusal["enc"],
                "reason": shape_refusal["reason"],
            })
            continue
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
            if not ensure_row_subject_solast(row_subject,
                                             log=lambda msg, bench=bench, unit=unit, encs=encs:
                                             print(f"  {bench}.{unit} enc={encs}: {msg}")):
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
        if stage2_source in CONCRETE_ONLY_STAGE2_SOURCES:
            region = dict(region_override or {})
            holes = dict(holes_override or {})
            pins = dict(pins_override or {})
        elif (region_override is not None or holes_override is not None
              or pins_override is not None):
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
        proj = ensure_project(bench + arm, flat, shared=("poc" + arm) if is_poc else None)
        if not args.forge_only and proj not in cleaned_projects:
            moved = archive_generated_tests(proj)
            if moved:
                print(f"  [clean] archived {moved} old generated test file(s) "
                      f"from {os.path.basename(proj)} before this Stage-4 run")
            cleaned_projects.add(proj)
        pf_label = path_function_artifact_suffix(path_function)
        plabel = f"p{piece}" if piece else ""
        wd = os.path.join(OUT, "_wd", f"{bench}__{unit}{pf_label}__{enc}{plabel}{arm}")
        os.makedirs(wd, exist_ok=True)
        stage4_scope = str(deriv.get("scope") or args.scope)
        stage4_max_tx = int(deriv.get("max_tx") or args.max_tx)
        base_cmd = [
            sys.executable,
            PUT,
            "--esbmc",
            args.esbmc,
            "--sol",
            flat,
            "--ast",
            ast,
            "--contract",
            contract,
            "--unit",
            unit,
            "--enc",
            str(enc),
            "--region",
            json.dumps(region),
            "--holes",
            json.dumps(holes),
            "--establish",
            json.dumps(establish),
            "--extcall-length-coordinates",
            json.dumps((certified_detail or {}).get("extcall_length_coordinates") or []),
            "--extcall-pins",
            json.dumps(merged_extcall_pins(certified_detail, pins)),
            "--forge-project",
            proj,
            "--workdir",
            wd,
            "--timeout",
            str(row_timeout),
            "--memlimit",
            f"{args.memlimit_gib}g",
            # The CELL is a property of the measurement, not a default of
            # this sweep. INVOCATION_DECISIONS.md prints two command lines
            # and forbids quoting one into the other's table, so it is an
            # argument here and it is printed with the result.
            "--scope",
            stage4_scope,
            "--max-tx",
            str(stage4_max_tx),
            "--auto-unwind",
            str(args.auto_unwind),
            "--derived-by",
            json.dumps(deriv),
        ]
        if stage4_kind:
            base_cmd += ["--stage4-kind", str(stage4_kind)]
        if args.reuse_emitted_dir:
            base_cmd += ["--reuse-emitted-dir", args.reuse_emitted_dir]

        def stage4_cmd(source, witness_check=None, depth=stage2_depth):
            cmd = list(base_cmd)
            append_stage4_driver_options(cmd, args, path_function, exit_kind, source, witness_check,
                                         piece, pins)
            append_row_esbmc_args(cmd, deriv.get("esbmc_args"), args.esbmc_arg)
            if depth is not None:
                cmd += ["--depth", str(depth)]
            return cmd

        cmd = stage4_cmd(stage2_source, stage2_witness_check)
        j = os.path.join(wd, "put.json")
        if args.forge_only:
            # RE-READ, never re-emit. The B gate has to be re-runnable without
            # two esbmc invocations per region, or it is a number nobody checks
            # between sweeps -- which is how it came to be assembled by hand in
            # the first place. A region with no put.json is rc=1 here, i.e. it
            # never produced a PUT, which is exactly what the table should say.
            rec = (json.load(open(j)) if os.path.exists(j) else stage4_missing_record(
                stage2_source,
                stage2_witness_check,
                failure_reason=("Stage 4 not run in "
                                "--forge-only mode and no "
                                "put.json exists")))
            rec = normalize_stage2_concrete_fallback_record(rec, stage2_source,
                                                            stage2_witness_check)
            rec = enrich_stage4_record(rec,
                                       stage2_source=stage2_source,
                                       witness_check=stage2_witness_check,
                                       stage4_kind=stage4_kind,
                                       certification_source=certification_source,
                                       certified_detail=certified_detail)
            rc = 0 if rec.get("file") else 1
            record_paths[id(rec)] = j
            results.append((bench, unit, enc, piece, rc, rec, proj, region, is_corpus, contract))
            continue
        print(f"\n--- {bench}.{unit} enc={encs} ---")
        emit_start = time.monotonic()
        p = subprocess.run(cmd, capture_output=True, text=True)
        emit_wall_s = time.monotonic() - emit_start
        sys.stdout.write(p.stdout)
        sys.stdout.write(p.stderr)
        rec = (json.load(open(j)) if os.path.exists(j) else stage4_missing_record(
            stage2_source,
            stage2_witness_check,
            failure_reason=("Stage 4 driver exited without writing put.json"),
            emit_wall_s=emit_wall_s,
            generation_timeout_s=args.timeout,
            returncode=p.returncode,
            stdout=p.stdout,
            stderr=p.stderr))
        rec = normalize_stage2_concrete_fallback_record(rec, stage2_source, stage2_witness_check)
        rec = enrich_stage4_record(rec,
                                   stage2_source=stage2_source,
                                   witness_check=stage2_witness_check,
                                   stage4_kind=stage4_kind,
                                   certification_source=certification_source,
                                   certified_detail=certified_detail,
                                   emit_wall_s=emit_wall_s)
        rewrite_stage4_record(j, rec)
        fallback_reason = certified_region_concrete_fallback_reason(stage2_source, p.returncode,
                                                                    rec)
        if fallback_reason:
            print(f"  [fallback] {bench}.{unit} enc={encs}: certified PUT "
                  f"refused as {fallback_reason}; retrying as concrete replay "
                  "only, which will not count as PUT")
            # `solidity_path_put.py` currently checks path depth before its
            # concrete-only branch.  For a pure replay that depth is metadata,
            # not an assertion-ladder antecedent, so use a dummy depth only to
            # reach the replay emitter when the original refusal was exactly
            # that the depth was unavailable.
            fallback_depth = stage2_depth
            if (fallback_depth is None and fallback_reason == "path-depth-unavailable"):
                fallback_depth = 0
            fallback_cmd = stage4_cmd(CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                                      "CERTIFIED-REGION-PUT-REFUSED:" + fallback_reason,
                                      fallback_depth)
            fallback_emit_start = time.monotonic()
            p2 = subprocess.run(fallback_cmd, capture_output=True, text=True)
            fallback_emit_wall_s = time.monotonic() - fallback_emit_start
            sys.stdout.write(p2.stdout)
            sys.stdout.write(p2.stderr)
            rec = (json.load(open(j)) if os.path.exists(j) else stage4_missing_record(
                CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                "CERTIFIED-REGION-PUT-REFUSED:" + fallback_reason,
                failure_reason=("certified-region concrete fallback exited without "
                                "writing put.json"),
                emit_wall_s=fallback_emit_wall_s,
                generation_timeout_s=args.timeout,
                returncode=p2.returncode,
                stdout=p2.stdout,
                stderr=p2.stderr))
            rec = normalize_certified_region_concrete_fallback_record(rec, fallback_reason)
            rec = normalize_stage2_concrete_fallback_record(
                rec, CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                "CERTIFIED-REGION-PUT-REFUSED:" + fallback_reason)
            rec = enrich_stage4_record(rec,
                                       stage2_source=CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                                       witness_check="CERTIFIED-REGION-PUT-REFUSED:" +
                                       fallback_reason,
                                       stage4_kind=stage4_kind,
                                       certification_source=certification_source,
                                       certified_detail=certified_detail,
                                       emit_wall_s=fallback_emit_wall_s)
            if fallback_reason == "path-depth-unavailable":
                rec["depth"] = stage2_depth
            rewrite_stage4_record(j, rec)
            p = p2
        elif args.retain_certified_concrete_replays and rec.get("kind") == "put":
            rec["requires_fixed_replay_fusion"] = True
            rec.setdefault("fixed_replay_fusion_error",
                           "certified basis replay has not been fused yet")
            rewrite_stage4_record(j, rec)
        record_paths[id(rec)] = j
        if isinstance(certified_detail, dict):
            certified_detail_by_put_record[id(rec)] = certified_detail
        results.append(
            (bench, unit, enc, piece, p.returncode, rec, proj, region, is_corpus, contract))
        # Arm the per-unit memo ONLY on a refusal that judged the synthesized
        # shape and produced no file: anything path-specific must be asked
        # again for the next path.
        _shape_reason = shape_level_concrete_refusal_reason(stage2_source, rec)
        if _shape_reason and (bench, unit) not in shape_refused_units:
            shape_refused_units[(bench, unit)] = {"enc": encs, "reason": _shape_reason}
        # Keep the exact Stage-1 CE as a separate, assertion-bearing artifact.
        # It is not part of Full's deliverable/test denominator; it is the
        # authenticated replacement used when RQ3 derives no-region-refinement
        # and this particular region records refinement_used=true.
        if (args.retain_certified_concrete_replays and p.returncode == 0
                and rec.get("kind") == "put" and isinstance(certified_detail, dict)
                and not requires_structural_abi_gate_anchor(certification_source,
                                                            certified_detail)):
            basis_requests = []
            test_units = rec.get("test_units") if isinstance(rec, dict) else None
            if test_units:
                for index, child in enumerate(test_units):
                    if not isinstance(child, dict):
                        continue
                    part = child.get("oracle_input_part") or {}
                    part_ce = part.get("representative_ce") if isinstance(part, dict) else None
                    part_claim = (part.get("representative_claim")
                                  if isinstance(part, dict) else None)
                    if not isinstance(part_ce, dict):
                        child["fixed_replay_fusion_error"] = (
                            "part representative replay lacks a complete CE")
                        continue
                    basis_requests.append((index, str(part.get("part_id") or index), part_ce,
                                           part_claim))
            elif certified_detail.get("ce"):
                basis_requests.append((None, "root", certified_detail.get("ce") or {}, None))
            for child_index, part_id, representative_ce, representative_claim in basis_requests:
                suffix = re.sub(r"[^A-Za-z0-9_]+", "_", str(part_id)).strip("_") or "part"
                basis_wd = wd + "__basis_concrete__" + suffix
                os.makedirs(basis_wd, exist_ok=True)
                basis_cmd = stage4_cmd(CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                                       "CERTIFIED-BASIS-REPLAY", stage2_depth)
                basis_cmd[basis_cmd.index("--workdir") + 1] = basis_wd
                basis_exit_kind = (rec.get("stats") or {}).get("exit_kind")
                if basis_exit_kind in ("normal", "revert") and "--exit-kind" not in basis_cmd:
                    basis_cmd += ["--exit-kind", basis_exit_kind]
                basis_cmd += [
                    "--test-suffix",
                    "__basis_" + suffix,
                    "--concrete-certified-ce-json",
                    json.dumps(representative_ce)
                ]
                if isinstance(representative_claim, dict):
                    basis_cmd += ["--concrete-claim-json", json.dumps(representative_claim)]
                witness_return = certified_ce_return(representative_ce)
                if witness_return is not None:
                    basis_cmd += ["--concrete-return-value", str(witness_return)]
                basis_start = time.monotonic()
                basis_proc = subprocess.run(basis_cmd, capture_output=True, text=True)
                basis_wall_s = time.monotonic() - basis_start
                sys.stdout.write(basis_proc.stdout)
                sys.stdout.write(basis_proc.stderr)
                basis_json = os.path.join(basis_wd, "put.json")
                basis_rec = (json.load(open(basis_json))
                             if os.path.exists(basis_json) else stage4_missing_record(
                                 CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                                 "CERTIFIED-BASIS-REPLAY",
                                 failure_reason=("certified CE basis driver exited without "
                                                 "writing put.json"),
                                 emit_wall_s=basis_wall_s,
                                 generation_timeout_s=args.timeout,
                                 returncode=basis_proc.returncode,
                                 stdout=basis_proc.stdout,
                                 stderr=basis_proc.stderr))
                basis_rec = enrich_stage4_record(
                    basis_rec,
                    stage2_source=CERTIFIED_REGION_CONCRETE_FALLBACK_SOURCE,
                    witness_check="CERTIFIED-BASIS-REPLAY",
                    stage4_kind=stage4_kind,
                    certification_source=certification_source,
                    certified_detail=certified_detail,
                    emit_wall_s=basis_wall_s)
                basis_rec["retained_basis_only"] = True
                basis_rec["basis_for_oracle_input_part"] = None if child_index is None else part_id
                rewrite_stage4_record(basis_json, basis_rec)
                record_paths[id(basis_rec)] = basis_json
                certified_detail_by_basis_record[id(basis_rec)] = certified_detail
                retained_basis_targets[id(basis_rec)] = {
                    "record": rec,
                    "record_path": j,
                    "child_index": child_index,
                    "representative_ce": representative_ce,
                }
                retained_basis_results.append((bench, unit, enc, piece, basis_proc.returncode,
                                               basis_rec, proj, region, is_corpus, contract))
            if test_units:
                rewrite_stage4_record(j, rec)

    if shape_refusal_skips:
        by_unit = {}
        for skip in shape_refusal_skips:
            by_unit.setdefault((skip["benchmark"], skip["unit"]), []).append(skip)
        print(f"\n[skip] {len(shape_refusal_skips)} cleared concrete fallback row(s) "
              f"across {len(by_unit)} unit(s) were not asked: the unit's first "
              f"such row was refused for a reason that judges the synthesized "
              f"shape, which no other path of the same unit changes")
        for (bench_key, unit_key), skips in sorted(by_unit.items()):
            print(f"  [skip] {bench_key}.{unit_key}: {len(skips)} row(s) after "
                  f"enc={skips[0]['first_refused_enc']} -- {skips[0]['reason']}")
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
          f"-> {', '.join(cells) if cells else 'no run recorded one'}" +
          (f", --auto-unwind {args.auto_unwind}" if args.auto_unwind else ""))
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

    pre_b_fused_any = False
    for _bench, _unit, _enc, _piece, _rc, rec, _proj, _region, _is_corpus, _contract in results:
        if not isinstance(rec, dict) or rec.get("kind") != "put":
            continue
        detail = certified_detail_by_put_record.get(id(rec))
        if not requires_structural_abi_gate_anchor(
                (detail or {}).get("certification_source"), detail):
            continue
        fusion, fusion_error = attach_structural_abi_gate_anchor(rec, detail)
        if fusion_error:
            rec["fixed_replay_fusion_error"] = fusion_error
        else:
            rec["fixed_replay_fusion"] = fusion
            rec.pop("fixed_replay_fusion_error", None)
            pre_b_fused_any = True
        rewrite_stage4_record(record_paths.get(id(rec)), rec)

    emission_wall_s = round(time.monotonic() - main_start, 3)
    b_summary = b_report(results, args.forge_timeout, args.esbmc, record_paths)
    basis_b_summary = (b_report(retained_basis_results, args.forge_timeout, args.esbmc,
                                record_paths) if retained_basis_results else {
                                    "rows": []
                                })
    basis_records = {
        (str(item[5].get("file") or ""), str(item[5].get("test") or "")): item[5]
        for item in retained_basis_results
    }
    for basis_row in basis_b_summary.get("rows") or []:
        basis_rec = basis_records.get(
            (str(basis_row.get("file") or ""), str(basis_row.get("test") or "")), {})
        basis_row.update({
            "path_function":
            basis_rec.get("path_function"),
            "stage2_witness_check":
            basis_rec.get("stage2_witness_check"),
            "concrete_oracles":
            basis_rec.get("concrete_oracles") or [],
            "put_json":
            record_paths.get(id(basis_rec)),
            "retained_basis_only":
            True,
            "region_refinement_used":
            bool((basis_rec.get("derived_by") or {}).get("region_refinement_used")),
        })
    valid_basis = {
        (str(row.get("file") or ""), str(row.get("test") or ""))
        for row in basis_b_summary.get("rows") or []
        if row.get("valid_reference_test") is True and row.get("forge_status") == "Success"
    }
    fused_any = pre_b_fused_any
    for basis_item in retained_basis_results:
        bench, unit, enc, piece, _rc, basis_rec, _proj, _region, _corpus, _contract = basis_item
        basis_key = (str(basis_rec.get("file") or ""), str(basis_rec.get("test") or ""))
        if basis_key not in valid_basis:
            continue
        target = retained_basis_targets.get(id(basis_rec))
        if target is not None:
            parent_rec = target.get("record")
            child_index = target.get("child_index")
            if child_index is None:
                put_rec = parent_rec
            else:
                children = parent_rec.get("test_units") if isinstance(parent_rec, dict) else None
                if not isinstance(children, list) or child_index >= len(children):
                    continue
                put_rec = json.loads(json.dumps(parent_rec))
                put_rec.update(children[child_index])
                put_rec["test_units"] = []
        else:
            put_item = next(
                (item for item in results
                 if item[0] == bench and item[1] == unit and item[2] == enc and item[3] == piece
                 and item[5].get("path_function") == basis_rec.get("path_function")), None)
            if put_item is None:
                continue
            put_rec = put_item[5]
            parent_rec = put_rec
            child_index = None
            target = {"record_path": record_paths.get(id(put_rec)), "representative_ce": None}
        if not isinstance(put_rec, dict) or put_rec.get("kind") != "put":
            continue
        detail = certified_detail_by_basis_record.get(id(basis_rec))
        if detail is None:
            put_rec["fixed_replay_fusion_error"] = "certified detail is absent"
        else:
            fusion, fusion_error = attach_required_fixed_replay_anchor(
                put_rec,
                basis_rec,
                detail,
                representative_ce=target.get("representative_ce"))
            if fusion_error:
                put_rec["fixed_replay_fusion_error"] = fusion_error
            else:
                put_rec["fixed_replay_fusion"] = fusion
                put_rec.pop("fixed_replay_fusion_error", None)
                fused_any = True
        if child_index is None:
            if target.get("record") is not None:
                if "fixed_replay_fusion" in put_rec:
                    target["record"]["fixed_replay_fusion"] = put_rec["fixed_replay_fusion"]
                    target["record"].pop("fixed_replay_fusion_error", None)
                else:
                    target["record"]["fixed_replay_fusion_error"] = \
                        put_rec.get("fixed_replay_fusion_error")
            rewrite_stage4_record(target.get("record_path") or record_paths.get(id(put_rec)),
                                  parent_rec)
        else:
            if "fixed_replay_fusion" in put_rec:
                parent_rec["test_units"][child_index]["fixed_replay_fusion"] = \
                    put_rec["fixed_replay_fusion"]
                parent_rec["test_units"][child_index].pop("fixed_replay_fusion_error", None)
            else:
                parent_rec["test_units"][child_index]["fixed_replay_fusion_error"] = \
                    put_rec.get("fixed_replay_fusion_error")
            rewrite_stage4_record(target.get("record_path"), parent_rec)
    if fused_any:
        # The source changed after the first gate.  Only this second run can
        # authorize the fused PUT as a Full deliverable.
        b_summary = b_report(results, args.forge_timeout, args.esbmc, record_paths)
    quality = b_summary.get("quality", {})
    if quality:
        print(f"  PUT rate among valid            : "
              f"{quality['put_rows']}/{quality['valid_reference_rows']} "
              f"= {quality['put_rate_among_valid']:.6f}")
        print(f"  R1/R2 rate among valid          : "
              f"{quality['r1r2_rows']}/{quality['valid_reference_rows']} "
              f"= {quality['r1r2_rate_among_valid']:.6f}")
    total_wall_s = round(time.monotonic() - main_start, 3)
    summary_path = os.path.join(OUT, "put-summary.json")
    with open(summary_path, "w") as stream:
        json.dump(
            {
                "schema": "veriput-put-summary/1",
                "cert_path": os.path.abspath(cert_path),
                # Rows this run deliberately did NOT ask for, and why. A skip
                # that is not printed reads as a row that was never available.
                "cleared_fallback_shape_skips": shape_refusal_skips,
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
                    "foundry_replay_wall_s": b_summary.get("foundry_replay", {}).get("wall_s", 0.0),
                    "total_wall_s": total_wall_s,
                    "foundry_replay_outside_generation_timeout": True,
                },
                "quality": b_summary.get("quality", {}),
                "deliverable_b": b_summary,
                "retained_concrete_bases": basis_b_summary.get("rows") or [],
            },
            stream,
            indent=2,
            sort_keys=True)
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
        _rc, stdout, _stderr, timed_out, wall_s = run_forge(proj, forge_timeout)
        timing["runs"] += 1
        timing["timeouts"] += 1 if timed_out else 0
        timing["wall_s"] += wall_s
        if timed_out:
            print(f"  [self-check] {os.path.basename(proj)}: forge timed out "
                  f"after {forge_timeout}s; no replay was disabled")
            continue
        statuses, _name_statuses, _suite_failures = forge_json_status_map(stdout)
        red = {}
        for (path, name), status in statuses.items():
            if status == "Failure" and name.startswith("test_cov"):
                red.setdefault(path, []).append(name)
        for path, fns in sorted(red.items()):
            # `path` is the source file forge reports for the suite.
            if path is None:
                print(f"  [self-check] {os.path.basename(proj)}: forge JSON "
                      f"reported red concrete tests {sorted(fns)} without a "
                      "suite file -- NOT disabling anything")
                continue
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
                    needle, f"  // DISABLED: RED on the unmodified contract, so its\n"
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
                      f"{len(changed)} red concrete replay(s): " + ", ".join(changed))
    timing["wall_s"] = round(timing["wall_s"], 3)
    return timing


def project_rel_file(project, filename):
    """Normalize a Forge suite path without relativizing it twice."""
    if not filename:
        return None
    if not os.path.isabs(filename):
        return os.path.normpath(filename)
    try:
        return os.path.normpath(os.path.relpath(filename, project))
    except ValueError:
        return os.path.normpath(filename)


def expand_stage4_test_unit_results(results, record_paths=None):
    """Expand one Stage-4 artifact into physical test-unit rows when present."""
    record_paths = record_paths or {}
    expanded = []
    expanded_paths = dict(record_paths)
    for item in results or ():
        bench, unit, enc, piece, rc, rec, proj, region, is_corpus, contract_name = item
        test_units = rec.get("test_units") if isinstance(rec, dict) else None
        if not test_units:
            expanded.append(item)
            continue
        for test_unit in test_units:
            if not isinstance(test_unit, dict) or not test_unit.get("test"):
                continue
            child = json.loads(json.dumps(rec))
            for key in ("file", "test", "stats", "materialization", "region", "holes",
                        "derived_by", "oracle_input_part", "requires_fixed_replay_fusion",
                        "fixed_replay_fusion", "fixed_replay_fusion_error"):
                if key in test_unit:
                    child[key] = test_unit[key]
            child["test_units"] = []
            expanded_paths[id(child)] = record_paths.get(id(rec))
            expanded.append((bench, unit, enc, piece, rc, child, proj, child.get("region", region),
                             is_corpus, contract_name))
    return expanded, expanded_paths


def _write_forge_replay_log(project, stdout, stderr, timed_out, wall_s, forge_timeout):
    """Record one project's Forge verdict inside the project that was replayed.

    Best effort by design: failing to write a log must never fail a run that
    otherwise produced valid tests.
    """
    try:
        with open(os.path.join(project, "forge-replay.log"), "w") as stream:
            stream.write("# VeriPUT final gate: `forge test --json`\n")
            stream.write("# timed_out=%s wall_s=%.3f timeout_s=%s\n"
                         % (bool(timed_out), wall_s, forge_timeout))
            stream.write("# ---- stdout ----\n")
            stream.write(stdout or "")
            stream.write("\n# ---- stderr ----\n")
            stream.write(stderr or "")
            stream.write("\n")
    except OSError as exc:
        print(f"  [forge] could not publish the replay log for "
              f"{os.path.basename(project)}: {exc}")


def b_report(results, forge_timeout, esbmc=ESBMC, record_paths=None):
    record_paths = record_paths or {}
    results, record_paths = expand_stage4_test_unit_results(results, record_paths)
    print()
    print("=" * 84)
    print("DELIVERABLE B — all five WORKORDER gates, per PUT")
    print("=" * 84)
    replay_timing = disable_red_replays(sorted({r[6] for r in results if r[6]}), forge_timeout)
    final_gate_wall_s = 0.0

    # forge, once per project, and the verdict per TEST FUNCTION. Running it per
    # row would recompile the benchmark flat once per region (70-180 KB each),
    # and a per-row run cannot see a failure caused by two PUTs sharing a project.
    verdicts = {}
    forge_outputs = {}
    suite_failures = {}
    forge_seen = {
        "put": {
            "Success": 0,
            "Failure": 0,
            "other": 0
        },
        "concrete": {
            "Success": 0,
            "Failure": 0,
            "other": 0
        },
    }

    def row_forge_status(project, rec, test_name):
        rel = project_rel_file(project, rec.get("file"))
        if rel is not None:
            status = verdicts.get((project, os.path.normpath(rel), test_name))
            if status is not None:
                return status
            suite_status = suite_failures.get((project, os.path.normpath(rel)))
            if suite_status is not None:
                return suite_status
            return verdicts.get((project, None, test_name))
        status = verdicts.get((project, None, test_name))
        if status is not None:
            return status
        return None

    def count_forge_seen(kind, status):
        if kind not in forge_seen or status is None:
            return
        key = status if status in ("Success", "Failure") else "other"
        forge_seen[kind][key] += 1

    for proj in sorted({r[6] for r in results if r[6]}):
        _rc, stdout, stderr, timed_out, wall_s = run_forge(proj, forge_timeout)
        replay_timing["runs"] += 1
        replay_timing["timeouts"] += 1 if timed_out else 0
        replay_timing["wall_s"] += wall_s
        final_gate_wall_s += wall_s
        forge_outputs[proj] = stdout
        # ---- PUBLISH THE REPLAY EVIDENCE NEXT TO THE ARTIFACTS ----
        #
        # This output was collected into `forge_outputs` and never written
        # anywhere, so the only record of WHY a row was red lived in the driver's
        # stdout under the AST cache workdir -- which a host reboot cleared,
        # after which 154 red rows had to be re-measured by re-running forge on
        # the published projects by hand.  The project directory IS published
        # with the case, so the verdict travels with the tests it is about.
        _write_forge_replay_log(proj, stdout, stderr, timed_out, wall_s, forge_timeout)
        if timed_out:
            print(f"  [forge] {os.path.basename(proj)}: timed out after "
                  f"{forge_timeout}s; every row in this project is UNKNOWN")
            continue
        statuses, name_statuses, project_suite_failures = (forge_json_status_map(stdout))
        if not statuses and not name_statuses and not project_suite_failures:
            # NAMED, not swallowed. A project that does not compile makes every
            # one of its rows UNKNOWN, and an UNKNOWN must never read as a pass.
            print(f"  [forge] {os.path.basename(proj)}: could NOT parse `forge "
                  f"test --json` output -- every row in this project is UNKNOWN "
                  f"below, which is NOT a pass. First 200 chars of stderr: "
                  f"{stderr[:200]!r}")
            continue
        for suite_file, status in project_suite_failures.items():
            rel = project_rel_file(proj, suite_file)
            if rel is not None:
                suite_failures[(proj, os.path.normpath(rel))] = status
        for (suite_file, fn), status in statuses.items():
            rel = project_rel_file(proj, suite_file)
            if rel is not None:
                verdicts[(proj, os.path.normpath(rel), fn)] = status
        for fn, status in name_statuses.items():
            verdicts[(proj, None, fn)] = status

    print(f"{'benchmark':<24}{'unit':<16}{'enc':>7}  "
          f"{'1.fuzz':>7}{'2.width':>8}{'3.assert':>9}{'4.green':>8}"
          f"{'5.corpus':>9}  B")
    b = 0
    n_stale = 0
    n_refused = 0
    row_summaries = []
    # ONE identity for the whole table: asking git and stat per row would let
    # a mid-report rebuild split the table's own notion of "this tree".
    _now_binary = current_binary_identity(esbmc)
    print(f"  this tree: head={_now_binary['head']} "
          f"srcDirty={_now_binary['srcDirty']} "
          f"binaryMtime={_now_binary['binaryMtime']}")
    valid_reference_tests = {"total": 0, "put": 0, "concrete": 0}
    stage2_sources = Counter()
    storage_layout_counts = Counter()
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
        if rec.get("storage_layout_available") is True:
            storage_layout_counts["available"] += 1
        elif rec.get("storage_layout_available") is False:
            storage_layout_counts["unavailable"] += 1
            if rec.get("file"):
                storage_layout_counts["unavailable_with_artifact"] += 1
            if rec.get("refused"):
                storage_layout_counts["unavailable_refused"] += 1
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
        concrete_fallback = (kind == "concrete" or is_concrete_only_stage2_source(stage2_source))
        if concrete_fallback:
            kind = "concrete"
        # Gate 1 counts the parameters the emitter actually wrote. Gate 2 asks
        # whether ANY coordinate the emitted test RENDERS is wider than a
        # point. Reading this from `stats` rather than from the raw region is
        # essential in both directions: state-only region width is not rendered
        # as fuzz input, while a calldata parameter absent from the certified
        # region can be lifted over its full type domain because Stage 2 proved
        # the path with that input unconstrained.
        refused = rc != 0
        stale = stale_reason(rec, _now_binary) if rc == 0 else None
        if kind == "refusal":
            refused = True
            row_summaries.append({
                "kind":
                "refusal",
                "is_put":
                False,
                "is_concrete":
                False,
                "stage2_source":
                stage2_source,
                "stage2_witness_check":
                rec.get("stage2_witness_check"),
                "stage4_kind":
                rec.get("stage4_kind"),
                "certification_source":
                rec.get("certification_source"),
                "certified_detail_stage4_kind":
                rec.get("certified_detail_stage4_kind"),
                "certified_detail_source":
                rec.get("certified_detail_source"),
                "benchmark":
                bench,
                "unit":
                unit,
                "enc":
                enc,
                "piece":
                piece,
                "rc":
                rc,
                "test":
                rec.get("test"),
                "put_json":
                record_paths.get(id(rec)),
                "forge_status":
                None,
                "gates": {
                    "fuzz": None,
                    "width": None,
                    "assert": None,
                    "green": None,
                    "corpus": None,
                },
                "b":
                False,
                "valid_reference_test":
                False,
                "refused":
                True,
                "refusal_reason": (rec.get("refusal_reason") or rec.get("refused")),
                "stale":
                stale,
                "fuzz_params":
                fz,
                "asserts":
                ar,
                "verifier_asserts":
                st.get("verifier_asserts", 0),
                "exit_kind_asserts":
                st.get("exit_kind_asserts", 0),
                "oracle_classes":
                row_oracle_classes(rec, st),
                "oracle_class_counts":
                row_oracle_class_counts(rec, st),
                "oracle_class_combinations":
                row_oracle_class_combinations(rec, st),
                "oracle_class_combo_counts":
                row_oracle_class_combo_counts(rec, st),
                "assertion_oracles":
                row_oracle_detail(rec, st),
                "guarded_asserts":
                guarded,
                "unconditional_asserts":
                uncond,
                "rendered_width":
                st.get("rendered_width") or {},
                "file":
                rec.get("file"),
                "storage_layout_available":
                rec.get("storage_layout_available"),
                "storage_layout_error":
                rec.get("storage_layout_error"),
                "timing":
                rec.get("timing") or {},
                **row_quality_fields(rec,
                                     st,
                                     is_put=False,
                                     is_concrete=False,
                                     refused=True,
                                     stale=stale),
            })
            print(f"{bench:<24}{unit:<16}{encs:>7}  "
                  f"{'n/a':>7}{'n/a':>8}{'n/a':>9}"
                  f"{'n/a':>8}{'n/a':>9}  REFUSED: "
                  f"{rec.get('refused')}")
            n_refused += 1
            continue
        if kind == "concrete":
            want = rec.get("test")
            status = row_forge_status(proj, rec, want)
            g4 = status == "Success"
            unsupported = (unsupported_concrete_reason(rec) if rc == 0 else None)
            refused = refused or unsupported is not None
            g5 = is_corpus
            generated_ok = g4 and g5 and not stale and not refused
            if generated_ok:
                valid_reference_tests["total"] += 1
                valid_reference_tests["concrete"] += 1
            if rc == 0 and not refused:
                count_forge_seen("concrete", status)
            row_summaries.append({
                "kind":
                "concrete",
                "is_put":
                False,
                "is_concrete":
                True,
                "stage2_source":
                stage2_source,
                "stage2_witness_check":
                rec.get("stage2_witness_check"),
                "stage4_kind":
                rec.get("stage4_kind"),
                "certification_source":
                rec.get("certification_source"),
                "certified_detail_stage4_kind":
                rec.get("certified_detail_stage4_kind"),
                "certified_detail_source":
                rec.get("certified_detail_source"),
                "benchmark":
                bench,
                "unit":
                unit,
                "enc":
                enc,
                "piece":
                piece,
                "rc":
                rc,
                "test":
                want,
                "put_json":
                record_paths.get(id(rec)),
                "forge_status":
                status,
                "gates": {
                    "fuzz": False,
                    "width": None if not refused else None,
                    "assert": None if not refused else None,
                    "green": g4 if not (refused or status is None) else None,
                    "corpus": g5 if not refused else None,
                },
                "b":
                False,
                "valid_reference_test":
                generated_ok,
                "refused":
                refused,
                "refusal_reason":
                unsupported,
                "concrete_reason":
                rec.get("concrete_reason"),
                "stale":
                stale,
                "fuzz_params":
                fz,
                "asserts":
                ar,
                "verifier_asserts":
                st.get("verifier_asserts", 0),
                "exit_kind_asserts":
                st.get("exit_kind_asserts", 0),
                "oracle_classes":
                row_oracle_classes(rec, st),
                "oracle_class_counts":
                row_oracle_class_counts(rec, st),
                "oracle_class_combinations":
                row_oracle_class_combinations(rec, st),
                "oracle_class_combo_counts":
                row_oracle_class_combo_counts(rec, st),
                "assertion_oracles":
                row_oracle_detail(rec, st),
                "guarded_asserts":
                guarded,
                "unconditional_asserts":
                uncond,
                "rendered_width":
                st.get("rendered_width") or {},
                "file":
                rec.get("file"),
                "storage_layout_available":
                rec.get("storage_layout_available"),
                "storage_layout_error":
                rec.get("storage_layout_error"),
                "timing":
                rec.get("timing") or {},
                **row_quality_fields(rec,
                                     st,
                                     is_put=False,
                                     is_concrete=True,
                                     refused=refused,
                                     stale=stale,
                                     put_failure_reason=unsupported),
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
                if unsupported:
                    print(f"      ⛔ NOT COUNTED: {unsupported}")
                n_refused += 1
            continue

        # A PUT with zero unconditional assertions is not a deliverable PUT,
        # even if the emitter wrote a Foundry file and exited 0.  Treat it like
        # a refusal so raw accounting does not count a green no-oracle test.
        fixed_replay_refusal = missing_fixed_replay_fusion_reason(rec)
        refused = refused or uncond <= 0 or fixed_replay_refusal is not None
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
        contract = contract_name or (BENCHES[bench][1] if bench in BENCHES else bench)
        legacy_piece = f"p{piece}" if piece else ""
        want = rec.get("test") or (f"test_put_{contract}_{unit}_path{enc}{legacy_piece}")
        status = row_forge_status(proj, rec, want)
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
        reference_ok = g3 and g4 and g5 and not stale and not refused
        ok = g1 and g2 and reference_ok
        b += 1 if ok else 0
        if ok:
            valid_reference_tests["total"] += 1
            valid_reference_tests["put"] += 1
        elif reference_ok:
            valid_reference_tests["total"] += 1
            valid_reference_tests["concrete"] += 1
        if rc == 0 and not refused:
            count_forge_seen("put", status)
        row_kind = "put" if ok or not reference_ok else "concrete"
        row_summaries.append({
            "kind":
            row_kind,
            "is_put":
            ok,
            "is_concrete":
            reference_ok and not ok,
            "stage2_source":
            stage2_source,
            "stage4_kind":
            rec.get("stage4_kind"),
            "certification_source":
            rec.get("certification_source"),
            "certified_detail_stage4_kind":
            rec.get("certified_detail_stage4_kind"),
            "certified_detail_source":
            rec.get("certified_detail_source"),
            "benchmark":
            bench,
            "unit":
            unit,
            "enc":
            enc,
            "piece":
            piece,
            "rc":
            rc,
            "test":
            want,
            "put_json":
            record_paths.get(id(rec)),
            "derived_by":
            rec.get("derived_by") or {},
            "region_refinement_used":
            bool((rec.get("derived_by") or {}).get("region_refinement_used")),
            "forge_status":
            status,
            "gates": {
                "fuzz": g1,
                "width": g2 if not refused else None,
                "assert": g3 if not refused else None,
                "green": g4 if not (refused or status is None) else None,
                "corpus": g5 if not refused else None,
            },
            "b":
            ok,
            "valid_reference_test":
            reference_ok,
            "refused":
            refused,
            "refusal_reason":
            (fixed_replay_refusal or rec.get("failure_reason")) if refused else None,
            "stale":
            stale,
            "fuzz_params":
            fz,
            "asserts":
            ar,
            "verifier_asserts":
            st.get("verifier_asserts", 0),
            "exit_kind_asserts":
            st.get("exit_kind_asserts", 0),
            "oracle_classes":
            row_oracle_classes(rec, st),
            "oracle_class_counts":
            row_oracle_class_counts(rec, st),
            "oracle_class_combinations":
            row_oracle_class_combinations(rec, st),
            "oracle_class_combo_counts":
            row_oracle_class_combo_counts(rec, st),
            "assertion_oracles":
            row_oracle_detail(rec, st),
            "guarded_asserts":
            guarded,
            "unconditional_asserts":
            uncond,
            "rendered_width":
            st.get("rendered_width") or {},
            "file":
            rec.get("file"),
            "storage_layout_available":
            rec.get("storage_layout_available"),
            "storage_layout_error":
            rec.get("storage_layout_error"),
            "timing":
            rec.get("timing") or {},
            **row_quality_fields(rec,
                                 st,
                                 is_put=ok,
                                 is_concrete=(reference_ok and not ok),
                                 refused=refused,
                                 stale=stale),
        })

        def m(x, unknown=False):
            return "?" if unknown else ("yes" if x else "NO")

        print(f"{bench:<24}{unit:<16}{encs:>7}  "
              f"{m(g1):>7}{m(g2, refused):>8}{m(g3, refused):>9}"
              f"{m(g4, refused or status is None):>8}{m(g5, refused):>9}  " +
              ("**B**" if ok else ("REFUSED" if refused else ("STALE" if stale else ""))))
        if refused:
            if rc == 0 and uncond <= 0:
                print("      ⛔ NOT COUNTED: this PUT has zero unconditional "
                      "assertions, so it is a no-oracle artifact even though "
                      "the emitter wrote a Foundry test.")
            elif fixed_replay_refusal:
                print(f"      ⛔ NOT COUNTED: {fixed_replay_refusal}")
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
    valid_rows = [r for r in row_summaries if r.get("valid_reference_test")]
    put_rows = [r for r in valid_rows if r.get("is_put")]
    r1r2_rows = [r for r in valid_rows if r.get("has_r1r2")]
    quality = {
        "valid_reference_rows": len(valid_rows),
        "put_rows": len(put_rows),
        "concrete_rows": sum(1 for r in valid_rows if r.get("is_concrete")),
        "r1r2_rows": len(r1r2_rows),
        "put_rate_among_valid": (round(len(put_rows) / len(valid_rows), 6) if valid_rows else 0.0),
        "r1r2_rate_among_valid":
        (round(len(r1r2_rows) / len(valid_rows), 6) if valid_rows else 0.0),
    }
    return {
        "b": b,
        "stage4_candidate_rows": len(results),
        "certified_region_rows": len(results),
        "forge_seen": forge_seen,
        "refused": n_refused,
        "stale": n_stale,
        "valid_reference_tests": valid_reference_tests,
        "stage2_source_counts": dict(stage2_sources),
        "storage_layout_counts": dict(sorted(storage_layout_counts.items())),
        "foundry_replay": {
            "outside_generation_timeout": True,
            "timeout_s_per_run": forge_timeout,
            "runs": replay_timing["runs"],
            "timeouts": replay_timing["timeouts"],
            "wall_s": round(replay_timing["wall_s"], 3),
            "self_check_wall_s": round(replay_timing["wall_s"] - final_gate_wall_s, 3),
            "final_gate_wall_s": round(final_gate_wall_s, 3),
        },
        "quality": quality,
        "rows": row_summaries,
    }


if __name__ == "__main__":
    sys.exit(main())
