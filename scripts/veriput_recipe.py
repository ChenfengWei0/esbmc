#!/usr/bin/env python3
"""Versioned VeriPUT recipe fragments shared by POC and benchmark runners."""

STRONG_RECIPE_VERSION = "veriput-strong/30-free-state-coords"
STRONG_PROBE_WITNESSES = 16
STRONG_PROBE_LADDER_BUDGET = 8
STRONG_MAX_HOLES = 3
STRONG_MAX_REGION_PIECES = 4
STRONG_SLOT_COORDS = 20
STRONG_PUT_AUTO_UNWIND = 2
STRONG_PUT_AUTO_PARTIAL_LOOPS = True
STRONG_PUT_LIFT_UNCONSTRAINED_CALLDATA = True
STRONG_PUT_LIFT_UNCONSTRAINED_SENDER = True
STRONG_PUT_R2_DEPTH = 1
STRONG_PUT_R2_TERM_BUDGET = 256
STRONG_PUT_R2_CANDIDATE_BUDGET = 512
STRONG_PUT_FUZZ_RUNS = 64
STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET = 96
STRONG_PUT_FUZZ_R2_PREFILTER_TIMEOUT = 60
STRONG_PUT_MIN_R2_ESBMC_BUDGET = 150

STRONG_CERTIFY_ARGS = [
    "--recipe-version", STRONG_RECIPE_VERSION,
    "--jobs", "1",
    "--probes", "8",
    "--refine-rounds", "2",
    "--shrink-rounds", "4",
    "--safety-retreat-after-tiny-cuts", "2",
    "--claim-budget", "0",
    "--level0",
    "--level0-perturb",
    "--probe-witnesses", str(STRONG_PROBE_WITNESSES),
    "--probe-ladder",
    "--probe-ladder-budget", str(STRONG_PROBE_LADDER_BUDGET),
    "--skip-bracket",
    "--env-coord-disagreed",
    "--pin-agreed-establishable-env",
    # v30: under --free-entry-state (always on in the batch driver) this flag
    # no longer pins STATE coordinates -- the query frees them at the entry
    # and the PUT establishes them, so a state variable the unit reads is a
    # real input dimension (MEASURED, TODO 45: Product.latestVersion returns
    # state._accumulator.latestVersion; pinned, the body path was a
    # no-coordinate structural point and the case had no method PUT; free,
    # the certificate is `return == state` over [0, 2^256-1]). The flag is
    # kept so a run WITHOUT --free-entry-state keeps the old behaviour.
    "--pin-agreed-state",
    "--max-holes", str(STRONG_MAX_HOLES),
    "--max-region-pieces", str(STRONG_MAX_REGION_PIECES),
    "--cut-policy", "spec",
    "--state-struct-fields",
    "--slot-coords", str(STRONG_SLOT_COORDS),
    "--static-uncontrolled-inseparable",
    "--esbmc-arg=--overflow-check",
    "--esbmc-arg=--div-by-zero-check",
    "--esbmc-arg=--path-cov-arith-resolve",
    "--esbmc-arg=--unwindsetname",
    "--esbmc-arg=_ESBMC_alloc_nested_2d:0:16,nondet_string:0:33",
    # v29: external calls are modelled as a nondet return value of the call's
    # own type, not as a re-entrant dispatch into the contract (TODO 20, the
    # motivation runs' recipe, and what Stage 4's extcall pin materialisation
    # already assumes). Under the default re-entry model every unit that makes
    # an external call is an unbounded recursion: the certification query's
    # forward condition can never close and its base case grows with k until
    # the unit budget kills it (MEASURED, TODO 42: MStable.balanceOfToken
    # k=12 at 57 s and still climbing; every MStable unit KILLED in v29), and
    # the enumeration's path set is re-entry combinations (approveMax 514
    # claims). Reentrancy is therefore NOT modelled by any certificate or PUT
    # of this recipe; that is the stated limit. The flag is not in the
    # k-induction strip lists, so it reaches enumeration, certification and
    # (through the Stage-2 row's esbmc_args) the Stage-4 proofs alike.
    "--esbmc-arg=--extcall-nondet",
    # v28: the arithmetic checks reach the CERTIFICATION queries too. The
    # driver's k-induction proof strategy strips --overflow-check /
    # --div-by-zero-check / --path-cov-arith-resolve from --esbmc-arg (they are
    # the enumeration's bounded flags), so until v27 every certificate was
    # about unchecked arithmetic (TODO 30 #9, measured on the motivation: the
    # `discountBps > feeBps` refuter is a checked revert and was invisible).
    "--certify-esbmc-arg=--overflow-check",
    "--certify-esbmc-arg=--div-by-zero-check",
    "--certify-esbmc-arg=--path-cov-arith-resolve",
]
# v28: same three checks on the Stage-4 R1/R2 proof queries (put_all passes
# them to solidity_path_put.py as --proof-esbmc-arg, appended after its strip).
STRONG_PUT_PROOF_ESBMC_ARGS = [
    "--overflow-check",
    "--div-by-zero-check",
    "--path-cov-arith-resolve",
]


def strong_certify_args(probe_witnesses=STRONG_PROBE_WITNESSES):
    args = list(STRONG_CERTIFY_ARGS)
    idx = args.index("--probe-witnesses")
    args[idx + 1] = str(probe_witnesses)
    if probe_witnesses > 0:
        return args
    out = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--probe-ladder":
            continue
        if arg == "--probe-ladder-budget":
            skip_next = True
            continue
        out.append(arg)
    return out


def strong_put_args():
    return [
        "--auto-unwind", str(STRONG_PUT_AUTO_UNWIND),
        "--auto-partial-loops",
        "--lift-unconstrained-calldata",
        "--lift-unconstrained-sender",
        "--propose-r2",
        "--r2-depth", str(STRONG_PUT_R2_DEPTH),
        "--r2-term-budget", str(STRONG_PUT_R2_TERM_BUDGET),
        "--r2-candidate-budget", str(STRONG_PUT_R2_CANDIDATE_BUDGET),
        "--fuzz-r2-prefilter",
        "--fuzz-runs", str(STRONG_PUT_FUZZ_RUNS),
        "--fuzz-r2-candidate-budget", str(STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET),
        "--fuzz-r2-prefilter-timeout", str(STRONG_PUT_FUZZ_R2_PREFILTER_TIMEOUT),
        "--min-r2-esbmc-budget", str(STRONG_PUT_MIN_R2_ESBMC_BUDGET),
    ] + [f"--proof-esbmc-arg={x}" for x in STRONG_PUT_PROOF_ESBMC_ARGS]


# ---- ITEM 2: the hollow ABI tests are not method output ----
#
# `structural-abi-gate-no-coordinate` and `structural-abi-getter-no-coordinate`
# are the COMPILER's own value gate, not a region this method computed: a
# non-payable entry rejects every nonzero `msg.value` before the body runs, and
# the EVM restores storage across that revert by definition. There is no
# counterexample behind them and no coordinate in them, which is also why they
# never retain a concrete basis.
#
# They are not invalid tests -- their R0 exit assertion does discriminate a
# mutant that stops reverting -- so they stay emitted and stay counted, under
# their own name. What they must not do is enter the PUT counts that the method
# is judged by. Measured on the Full corpus they were 4573 of 5710 certified
# regions (80.1%) and 2607 of 3270 valid PUTs (79.7%): counting them is the
# difference between reporting 3270 PUTs and reporting 641.
STRUCTURAL_CERTIFICATION_SOURCES = (
    "structural-abi-gate-no-coordinate",
    "structural-abi-getter-no-coordinate",
)
STRUCTURAL_STAGE4_KINDS = ("abi-value-gate", "getter-value-gate")


def is_structural_certificate_row(row, record=None):
    """True when this row rests on a structural certificate, not a solver CE.

    Reads whichever of the two provenance fields the caller has: the emitted
    summary row carries `certification_source`/`stage4_kind`, the put.json
    record carries the same plus `certified_detail_stage4_kind`. Both are
    accepted so the one predicate serves the runner, the statistics and the
    RQ3 derivation instead of three copies drifting apart.
    """
    row = row if isinstance(row, dict) else {}
    record = record if isinstance(record, dict) else {}
    for source in (row.get("certification_source"), record.get("certification_source"),
                   row.get("certified_detail_source"), record.get("certified_detail_source")):
        if source in STRUCTURAL_CERTIFICATION_SOURCES:
            return True
    for kind in (row.get("stage4_kind"), record.get("stage4_kind"),
                 row.get("certified_detail_stage4_kind"),
                 record.get("certified_detail_stage4_kind")):
        if kind in STRUCTURAL_STAGE4_KINDS:
            return True
    return False
