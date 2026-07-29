#!/usr/bin/env python3
"""Generate tests for a corpus and RUN each suite against the unmodified contract.

This is the validity gate. Section 5.3's only vetoing criterion is that a
generated test passes on the contract it was generated from, and until now that
was checked by hand, once, on whichever contract was in front of me. Checking it
by hand is how a suite that reverts on the unmodified contract ships -- which is
the exact thing this project criticises SolTG for, so emitting one would be a
liability rather than a bug.

It also produces a number the evaluation needs and has never had: the fraction
of generated tests that pass on the unmodified contract. A suite measured by
enumeration rather than by running is the failure mode of "symbolically reached
but no executable test", again the criticism we make of others.

Every case is reported: generated / compiled / passed / failed, with the failure
text. A case that produced no test at all is reported too -- "0 failures" and
"0 tests" print the same way otherwise.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HARNESS = os.path.dirname(os.path.abspath(__file__))
REG = os.path.abspath(os.path.join(HARNESS, "..", "esbmc-solidity"))
TOOL = os.path.abspath(
    os.path.join(HARNESS, "..", "..", "build", "src", "esbmc", "esbmc"))
WORK = os.path.join(HARNESS, ".verify")

# Every solc the corpus pins, resolved through solc-select. The harness pins one
# version for its own sources, but benchmark contracts pin their own -- aqua
# requires exactly 0.8.30 and forge refuses to compile it under 0.8.34. The
# freeze is therefore per contract, not global, and that has to be recorded.
SOLC_DIR = os.path.expanduser("~/.solc-select/artifacts")


def pragma_version(src):
    """The exact version a source pins, if it pins one, else None."""
    m = re.search(r"pragma\s+solidity\s+=\s*([0-9]+\.[0-9]+\.[0-9]+)", src)
    return m.group(1) if m else None


def contract_name(desc_path, default=None):
    try:
        txt = open(desc_path, errors="replace").read()
    except OSError:
        return default
    m = re.search(r"--contract\s+(\S+)", txt)
    return m.group(1) if m else default


def run(cmd, cwd=None, env=None, timeout=900):
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return -1, "<timeout>"


def verify(case_dir, name):
    """Generate for one case and run the suite. Returns a result dict."""
    src = os.path.join(case_dir, "contract.sol")
    desc = os.path.join(case_dir, "test.desc")
    if not os.path.isfile(src):
        return {"case": name, "status": "no-contract.sol"}
    # Many cases do not pass --contract at all and let ESBMC infer it. Skipping
    # those was a limitation of THIS script, not a property of the tool, and it
    # silently shrank the corpus from 40 to 23 -- a rate over a corpus chosen by
    # a script's parsing convenience is not a rate.
    contract = contract_name(desc)

    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    shutil.copy(src, os.path.join(WORK, "contract.sol"))

    text = open(src, errors="replace").read()
    pin = pragma_version(text)
    env = dict(os.environ)
    if pin:
        env["SOLC_VERSION"] = pin

    cmd = [TOOL, "--solidity-path-coverage"]
    if contract:
        cmd += ["--contract", contract]
    cmd += ["--solidity-max-tx", "1", "--generate-foundry-testcase",
            "--memlimit", "20g", "--result-only", "contract.sol"]
    rc, out = run(cmd, cwd=WORK, env=env)
    tests = [f for f in os.listdir(WORK) if f.endswith(".cov.t.sol")]
    if not tests:
        return {"case": name, "contract": contract, "status": "no-test-emitted",
                "esbmc_rc": rc}

    # Match the EMITTED comment lines exactly. Matching the bare tag counted the
    # file header too -- it contains "[revert-tolerant]" in prose -- which
    # inflated the tolerant count by one per file and made the asserted share
    # look worse than it is. A ratio is only as good as its denominator.
    body = "".join(
        open(os.path.join(WORK, t), errors="replace").read() for t in tests)
    bare = len(re.findall(
        r"^\s*// \[asserted\] path exits normally", body, re.MULTILINE))
    tolerant = len(re.findall(
        r"^\s*// \[revert-tolerant\] outcome not asserted", body, re.MULTILINE))

    for f in os.listdir(os.path.join(HARNESS, "test")):
        if f.endswith(".sol"):
            os.remove(os.path.join(HARNESS, "test", f))
    shutil.copy(os.path.join(WORK, "contract.sol"),
                os.path.join(HARNESS, "test", "contract.sol"))
    for t in tests:
        shutil.copy(os.path.join(WORK, t), os.path.join(HARNESS, "test", t))

    cmd = ["forge", "test", "--match-path", "test/*.cov.t.sol"]
    if pin:
        solc_bin = os.path.join(SOLC_DIR, "solc-" + pin, "solc-" + pin)
        if os.path.isfile(solc_bin):
            cmd += ["--use", solc_bin]
    frc, fout = run(cmd, cwd=HARNESS, timeout=600)

    passed = len(re.findall(r"^\[PASS\]", fout, re.MULTILINE))
    failed = len(re.findall(r"^\[FAIL", fout, re.MULTILINE))
    # The residual bucket has to be explicit and it has to be SPLIT. "Nothing
    # ran" conflates two things that mean opposite things about the tool: a
    # generated test that will not compile is an emitter defect and the one
    # outcome the emitter promises never to produce, whereas a CONTRACT that
    # will not compile under the frozen profile is a limit of the profile. The
    # first version of this script called both "no-run", which is the same
    # bottom-of-the-whitelist mistake this project keeps recording.
    compile_err = "Compiler run failed" in fout or "Compiler error" in fout
    if frc == 0 and failed == 0 and passed > 0:
        status = "green"
    elif failed:
        status = "red"
    elif compile_err and re.search(r"--> test/[^\n]*\.cov\.t\.sol", fout):
        status = "test-uncompilable"   # emitter defect
    elif compile_err:
        status = "contract-uncompilable"  # profile limit, e.g. optimizer off
    else:
        status = "no-run-unclassified"
    fails = re.findall(r"^\[FAIL[^\n]*", fout, re.MULTILINE)
    for f in os.listdir(os.path.join(HARNESS, "test")):
        if f.endswith(".sol"):
            os.remove(os.path.join(HARNESS, "test", f))
    return {"case": name, "contract": contract or "<inferred>",
            "status": status,
            "solc": pin or "default", "asserted_calls": bare,
            "tolerant_calls": tolerant, "passed": passed, "failed": failed,
            "failures": fails[:5],
            "forge_head": "" if status == "green" else fout[:600]}


def main():
    names = sorted(d for d in os.listdir(REG)
                   if d.startswith("solidity_path_cov")
                   and os.path.isdir(os.path.join(REG, d)))
    if len(sys.argv) > 1:
        names = [n for n in names if any(a in n for a in sys.argv[1:])]
    print("corpus: %d case(s)" % len(names))
    if not names:
        sys.exit("empty corpus -- refusing to report a rate over nothing")

    results = []
    for n in names:
        r = verify(os.path.join(REG, n), n)
        results.append(r)
        print("  %-58s %-16s asserted=%s tolerant=%s pass=%s fail=%s" % (
            n, r.get("status"), r.get("asserted_calls", "-"),
            r.get("tolerant_calls", "-"), r.get("passed", "-"),
            r.get("failed", "-")), flush=True)
        for f in r.get("failures", []):
            print("        %s" % f)

    buckets = {}
    for r in results:
        buckets.setdefault(r.get("status"), []).append(r)
    emitted = [r for r in results
               if r.get("status") not in ("no-contract.sol", "no-test-emitted")]
    green = buckets.get("green", [])
    red = buckets.get("red", [])
    print()
    print("cases in corpus            : %d" % len(results))
    print("by status (every case lands in exactly one):")
    for k in sorted(buckets):
        print("   %-24s %d" % (k, len(buckets[k])))
    print("cases that emitted a suite : %d" % len(emitted))
    print("suites GREEN on unmodified : %d" % len(green))
    print("suites RED  on unmodified  : %d  <- emitter defect if nonzero" % len(red))
    print("tests passed / failed      : %d / %d" % (
        sum(r.get("passed", 0) for r in emitted),
        sum(r.get("failed", 0) for r in emitted)))
    print("calls asserted / tolerant  : %d / %d" % (
        sum(r.get("asserted_calls", 0) for r in emitted),
        sum(r.get("tolerant_calls", 0) for r in emitted)))
    with open(os.path.join(HARNESS, "verify-report.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("report: verify-report.json")


if __name__ == "__main__":
    main()
