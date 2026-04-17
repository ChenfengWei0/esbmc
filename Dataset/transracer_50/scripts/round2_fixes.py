#!/usr/bin/env python3
"""Round-2 targeted fixes for specific error patterns seen after the
initial patch loop + emit-in-event cleanup.

Each function takes the source text and the last solc error line, and
returns patched source.  Applied iteratively until no change or cap.
"""
from __future__ import annotations
import re, subprocess, sys, json, os
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/Dataset/transracer_50")
SOURCES = ROOT / "sources"


def run_solc(sol_dir: Path) -> tuple[int, str]:
    r = subprocess.run(
        ["solc", "--bin", "contract.sol"],
        cwd=sol_dir, capture_output=True, text=True, timeout=60,
    )
    return r.returncode, r.stderr


# Regex to match a specific error with contract name inside
ABSTRACT_RE = re.compile(r'Contract "(\w+)" should be marked as abstract')
OVERRIDE_RE = re.compile(r'Overriding function is missing "override" specifier\.\s*\n\s*-->\s*contract\.sol:(\d+):')
NO_VIS_RE = re.compile(r'No visibility specified.*\n\s*-->\s*contract\.sol:(\d+):')
ADDR_EQ0_RE = re.compile(r'Built-in binary operator [!=]= cannot be applied to types address(?:\s*payable)? and int_const 0\.\s*\n\s*-->\s*contract\.sol:(\d+):')
MEM_LOC_RE = re.compile(r'Data location must be "(?:storage"|"memory"|memory"|calldata").*\n\s*-->\s*contract\.sol:(\d+):(\d+):')
CONTRACT2ADDR_RE = re.compile(r'Invalid implicit conversion from contract \w+ to address requested.*\n\s*-->\s*contract\.sol:(\d+):')
SHADOW_RE = re.compile(r'Identifier already declared\.\s*\n\s*-->\s*contract\.sol:(\d+):')


def mark_contract_abstract(src: str, cname: str) -> str:
    # `contract X is Y { ... }` or `contract X { ... }` -> `abstract contract X ...`
    return re.sub(rf"(?<!abstract\s)\bcontract\s+{re.escape(cname)}\b", f"abstract contract {cname}", src, count=1)


def add_override_on_line(src: str, lineno: int) -> str:
    lines = src.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return src
    idx = lineno - 1
    line = lines[idx]
    # common patterns:
    #   function f(...) <visibility> [view|pure|payable] [returns (...)] { ... }
    # insert virtual override before `returns` or before `{`
    if "override" in line:
        return src
    # Case 1: has returns
    m = re.match(r"(\s*function\s+\w+\s*\([^)]*\)\s+(?:public|external|internal|private)(?:\s+(?:view|pure|payable))?)\s+(returns\s*\([^)]*\))(.*)", line)
    if m:
        lines[idx] = f"{m.group(1)} virtual override {m.group(2)}{m.group(3)}"
    else:
        # Case 2: no returns, before `{`
        m = re.match(r"(\s*function\s+\w+\s*\([^)]*\)\s+(?:public|external|internal|private)(?:\s+(?:view|pure|payable))?)\s*(\{|;)(.*)", line)
        if m:
            lines[idx] = f"{m.group(1)} virtual override {m.group(2)}{m.group(3)}"
    return "".join(lines)


def add_public_on_line(src: str, lineno: int) -> str:
    lines = src.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return src
    idx = lineno - 1
    line = lines[idx]
    if re.search(r"\b(public|external|internal|private)\b", line):
        return src
    # insert `public` after the param list
    new = re.sub(r"(function\s+\w+\s*\([^)]*\))", r"\1 public", line, count=1)
    if new != line:
        lines[idx] = new
    return "".join(lines)


def fix_addr_eq0_on_line(src: str, lineno: int) -> str:
    lines = src.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return src
    idx = lineno - 1
    line = lines[idx]
    # replace `x != 0` or `x == 0` with `x != address(0)` — narrow to this line
    new = re.sub(r"([a-zA-Z_][\w\.]*)\s*!=\s*0\b(?!\s*[\)\w])", r"\1 != address(0)", line)
    new = re.sub(r"([a-zA-Z_][\w\.]*)\s*==\s*0\b(?!\s*[\)\w])", r"\1 == address(0)", new)
    lines[idx] = new
    return "".join(lines)


def add_memory_on_line(src: str, lineno: int, col: int) -> str:
    lines = src.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return src
    idx = lineno - 1
    line = lines[idx]
    # Look for type at col and append `memory` if it's string/bytes/array without it
    # col is 1-based; err column points at the problem location
    # Simpler: regex-rewrite any `string <ident>` / `bytes <ident>` / `<T>[] <ident>` on this line
    for typ in ("string", "bytes"):
        new = re.sub(
            rf"\b({typ})(?!\s+(?:memory|calldata|storage))\s+(\w+)",
            r"\1 memory \2",
            line,
        )
        if new != line:
            line = new
    new = re.sub(
        r"\b((?:uint\d*|int\d*|address|bool|bytes\d+|[A-Z]\w*)\[\s*\])(?!\s+(?:memory|calldata|storage))\s+(\w+)",
        r"\1 memory \2",
        line,
    )
    if new != line:
        line = new
    # return parameter: `returns (string)` / `returns (Foo[])`
    for typ in ("string", "bytes"):
        line = re.sub(rf"returns\s*\(\s*({typ})\s*\)", rf"returns (\1 memory)", line)
    lines[idx] = line
    return "".join(lines)


def fix_contract_to_addr(src: str, lineno: int) -> str:
    lines = src.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return src
    idx = lineno - 1
    line = lines[idx]
    # heuristic: wrap any bare call arg that's a contract-like identifier in `address(...)`
    # specifically where the arg is passed to `.transfer`, `.call`, `payable(...)`, etc.
    # safer approach: target `payable(X)` where X is a bare word and replace with `payable(address(X))`
    line = re.sub(r"payable\(\s*((?!address\s*\()[A-Za-z_]\w*(?:\.\w+)*)\s*\)", r"payable(address(\1))", line)
    # Also any arg like `f(this, ...)` where this is the contract type → address(this)
    # not safe; skip.
    lines[idx] = line
    return "".join(lines)


def strip_natspec_docblock(src: str) -> str:
    # strip /// lines & /** ... */ blocks (preserve // lines)
    src = re.sub(r"^\s*///[^\n]*\n", "\n", src, flags=re.MULTILINE)
    src = re.sub(r"/\*\*[\s\S]*?\*/", "", src)
    return src


def dedupe_external_public(src: str) -> str:
    src = re.sub(r"\b(public|external|internal|private)\s+\1\b", r"\1", src)
    src = re.sub(r"\bexternal\s+public\b", "external", src)
    src = re.sub(r"\bpublic\s+external\b", "external", src)
    src = re.sub(r"\bvirtual\s+virtual\b", "virtual", src)
    src = re.sub(r"\boverride\s+override\b", "override", src)
    return src


def attempt_fix(sol_dir: Path, max_iters: int = 20, log: list = None) -> str:
    """Try to make sol_dir/contract.sol compile. Return final status string."""
    sol = sol_dir / "contract.sol"
    for i in range(max_iters):
        rc, stderr = run_solc(sol_dir)
        if rc == 0 and "Error:" not in stderr:
            return "ok"
        src = sol.read_text()
        orig = src
        # try pattern-specific fixes
        m = ABSTRACT_RE.search(stderr)
        if m:
            src = mark_contract_abstract(src, m.group(1))
        elif m := OVERRIDE_RE.search(stderr):
            src = add_override_on_line(src, int(m.group(1)))
        elif m := NO_VIS_RE.search(stderr):
            src = add_public_on_line(src, int(m.group(1)))
        elif m := ADDR_EQ0_RE.search(stderr):
            src = fix_addr_eq0_on_line(src, int(m.group(1)))
        elif m := MEM_LOC_RE.search(stderr):
            src = add_memory_on_line(src, int(m.group(1)), int(m.group(2)))
        elif m := CONTRACT2ADDR_RE.search(stderr):
            src = fix_contract_to_addr(src, int(m.group(1)))
        elif "Documentation tag" in stderr:
            src = strip_natspec_docblock(src)
        elif "Visibility already specified" in stderr or "virtual virtual" in stderr:
            src = dedupe_external_public(src)
        else:
            return f"unhandled: {stderr.splitlines()[0] if stderr else '?'}"
        if src == orig:
            return f"no_progress: {stderr.splitlines()[0] if stderr else '?'}"
        sol.write_text(src)
        if log is not None:
            log.append({"iter": i, "err": stderr.splitlines()[0] if stderr else "", "applied": "yes"})
    return "budget_exhausted"


def main():
    selected = json.loads((ROOT / "selected.json").read_text())
    summary = {}
    for name in selected:
        sol_dir = SOURCES / name
        if not (sol_dir / "contract.sol").exists():
            continue
        rc, stderr = run_solc(sol_dir)
        if rc == 0 and "Error:" not in stderr:
            summary[name] = "ok"
            continue
        summary[name] = attempt_fix(sol_dir)
        print(f"[{name}] {summary[name]}")
    (ROOT / "round2_status.json").write_text(json.dumps(summary, indent=2))
    ok = sum(1 for v in summary.values() if v == "ok")
    print(f"\n{ok}/{len(summary)} compile after round 2")


if __name__ == "__main__":
    main()
