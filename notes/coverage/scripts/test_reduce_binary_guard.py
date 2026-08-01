#!/usr/bin/env python3
"""Prove `reduce_to_poc._assert_same_binary` FIRES when the binary changes.

A guard that has never been seen to fire is indistinguishable from one that is
not installed -- this project has shipped both an always-true reader and a
never-firing detector, so a new defence is not accepted on the grounds that it
compiles.

The real ESBMC binary is never touched: a reduction may be running, and mutating
its tool mid-run is the exact defect this guard exists to stop. The module's
`ESBMC` path is pointed at a temporary file instead, which is also the only way
to exercise the CHANGE branch deterministically.

Both directions are checked. A guard that always raised would pass a
fires-on-change test and break every ordinary run.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location(
    "reduce_to_poc", HERE / "reduce_to_poc.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = 0
with tempfile.TemporaryDirectory() as d:
    fake = Path(d) / "esbmc"
    fake.write_bytes(b"binary-v1")
    mod.ESBMC = fake
    mod._BIN_FP = None

    # 1. first call records the fingerprint and must NOT raise
    try:
        mod._assert_same_binary()
        print("  ok    first call records the fingerprint, does not raise")
    except SystemExit as e:
        fails += 1
        print(f"  ** FAIL ** first call raised: {e}")

    # 2. an UNCHANGED binary must still not raise -- otherwise every candidate
    #    after the first would die and the guard would be worse than the defect
    try:
        mod._assert_same_binary()
        print("  ok    unchanged binary does not raise")
    except SystemExit as e:
        fails += 1
        print(f"  ** FAIL ** unchanged binary raised: {e}")

    # 3. a REBUILT binary must raise. Size and mtime both change here, which is
    #    what a real rebuild does; the fingerprint uses both so a same-size
    #    rebuild is still caught.
    fake.write_bytes(b"binary-v2-rebuilt-longer")
    try:
        mod._assert_same_binary()
        fails += 1
        print("  ** FAIL ** rebuilt binary did NOT raise -- the guard is dead")
    except SystemExit as e:
        msg = str(e)
        if "REBUILT MID-REDUCTION" in msg and "must not be used" in msg:
            print("  ok    rebuilt binary raises, and the message names the "
                  "hazard and the remedy")
        else:
            fails += 1
            print(f"  ** FAIL ** raised but the message is unusable: {msg[:120]}")

print(f"\n{3 - fails}/3 behave as stated" + ("" if not fails else
                                             f"   ** {fails} WRONG **"))
sys.exit(1 if fails else 0)
