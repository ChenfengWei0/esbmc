#!/usr/bin/env python3
"""Versioned VeriPUT recipe fragments shared by POC and benchmark runners."""

STRONG_RECIPE_VERSION = "veriput-strong/7"
STRONG_PROBE_WITNESSES = 8

STRONG_CERTIFY_ARGS = [
    "--recipe-version", STRONG_RECIPE_VERSION,
    "--jobs", "1",
    "--probes", "8",
    "--refine-rounds", "2",
    "--shrink-rounds", "4",
    "--claim-budget", "0",
    "--level0",
    "--level0-perturb",
    "--probe-witnesses", str(STRONG_PROBE_WITNESSES),
    "--probe-ladder",
    "--probe-ladder-budget", "4",
    "--skip-bracket",
    "--no-auto-pin-value",
    "--env-coord-disagreed",
    "--pin-agreed-state",
    "--max-holes", "1",
    "--max-region-pieces", "1",
    "--cut-policy", "spec",
    "--state-struct-fields",
    "--slot-coords", "8",
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
        "--auto-unwind", "1",
        "--propose-r2",
        "--r2-depth", "1",
        "--r2-term-budget", "96",
        "--r2-candidate-budget", "128",
        "--fuzz-r2-prefilter",
        "--fuzz-runs", "256",
        "--fuzz-r2-candidate-budget", "128",
    ]
