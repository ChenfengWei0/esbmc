#!/usr/bin/env python3
"""Versioned VeriPUT recipe fragments shared by POC and benchmark runners."""

STRONG_RECIPE_VERSION = "veriput-strong/27-proof-budgeted-r2"
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
    ]
