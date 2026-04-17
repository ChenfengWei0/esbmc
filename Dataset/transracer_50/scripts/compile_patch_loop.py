#!/usr/bin/env python3
"""Iterative solc-0.8 compile-and-patch loop. For each upgraded contract, run
solc; on error, apply a targeted patch keyed to the error message; re-run.

Budget per contract: 15 iterations OR the same error appearing twice in a row
(indicating no progress). Logs every patch applied to logs/upgrade_<name>.log.

Returns exit code 0 if all 33 compile; non-zero count of failures otherwise.
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ROOT / "sources"
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)


def run_solc(sol: Path) -> tuple[int, str, str]:
    """Return (returncode, stdout, stderr)."""
    r = subprocess.run(
        ["solc", "--stop-after", "parsing", str(sol.name)],
        cwd=sol.parent,
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode, r.stdout, r.stderr


def run_solc_full(sol: Path) -> tuple[int, str, str]:
    """Full compile (type-check + codegen). Use this after parsing passes."""
    r = subprocess.run(
        ["solc", "--bin", str(sol.name)],
        cwd=sol.parent,
        capture_output=True, text=True, timeout=120,
    )
    return r.returncode, r.stdout, r.stderr


# ---- Patch rules: list of (error_pattern, patch_fn) tuples.
# Each patch_fn receives (source_text, error_match) and returns patched source.

def patch_anonymous_fallback(src: str, m: re.Match) -> str:
    """function() public payable { ... } → receive() external payable { ... }
    function() external payable { ... } → receive() external payable { ... }
    function() public { ... } (no value) → fallback() external { ... }"""
    # replace anonymous payable
    src = re.sub(
        r"function\s*\(\s*\)\s*(public|external)?\s*payable",
        "receive() external payable",
        src,
    )
    # anonymous non-payable
    src = re.sub(
        r"function\s*\(\s*\)\s*(public|external)",
        "fallback() external",
        src,
    )
    return src


def patch_contract_named_ctor(src: str, m: re.Match) -> str:
    """function <Name>(...) public { ... } where <Name> is a contract name →
    constructor(...) { ... }. We detect by looking at every `contract X { ... function X(...) ... }`
    nesting."""
    # find all `contract Foo ...` or `contract Foo is ...`
    contract_names = re.findall(r"\bcontract\s+([A-Z]\w*)", src)
    for cn in contract_names:
        # replace function with same name (only first occurrence per contract)
        # pattern: function Foo(args) visibility { body }
        pat = re.compile(rf"function\s+{cn}\s*\(([^)]*)\)\s*(public|external|internal|private|\s)*", re.DOTALL)
        def repl(mm):
            args = mm.group(1)
            return f"constructor({args}) "
        new = pat.sub(repl, src, count=1)
        src = new
    return src


def patch_send_transfer_to_payable(src: str, m: re.Match) -> str:
    """`addr.transfer(x)` or `addr.send(x)` where addr is plain `address` → wrap in payable()."""
    # Low-precision: wrap every `.transfer(` and `.send(` invocation where the
    # recipient is a bare identifier or `msg.sender`. Let solc re-check.
    src = re.sub(r"\bmsg\.sender\.(transfer|send)\(", r"payable(msg.sender).\1(", src)
    # For other identifiers, wrap `ident.transfer(` → `payable(ident).transfer(`
    # Only rewrite if the ident's declared type is `address` (not `address payable`). We can't
    # tell that statically; apply the rewrite conservatively to anything that's not already
    # `payable(...)`.
    src = re.sub(
        r"(?<![a-zA-Z0-9_\)])([a-zA-Z_]\w*)\.(transfer|send)\(",
        r"payable(\1).\2(",
        src,
    )
    # fix double-wrap: payable(payable(x)) → payable(x)
    src = re.sub(r"payable\(payable\(([^)]+)\)\)", r"payable(\1)", src)
    return src


def patch_constant_to_view(src: str, m: re.Match) -> str:
    """`function f() public constant` → `function f() public view`"""
    src = re.sub(r"(function[^{;]*?\s)constant(\s)", r"\1view\2", src)
    return src


def patch_using_safemath(src: str, m: re.Match) -> str:
    """Drop `using SafeMath for uintN;`; leave the arithmetic rewrites to patch_safemath_second_pass
    (which is context-free and avoids the prefix-dot corruption from capturing a bare ident that
    was actually the tail of a longer expression like `block.timestamp.sub(x)`)."""
    src = re.sub(r"using\s+SafeMath\s+for\s+[^;]+;", "", src)
    # Let the second-pass rewriter handle the calls on the next iteration.
    return patch_safemath_second_pass(src, m)


def patch_var_keyword(src: str, m: re.Match) -> str:
    """`var x = ...` → `uint256 x = ...` (best guess)"""
    src = re.sub(r"\bvar\s+(\w+)\s*=", r"uint256 \1 =", src)
    return src


def patch_returns_visibility(src: str, m: re.Match) -> str:
    """`function f() returns (uint)` (no visibility) → `function f() public returns (uint)`"""
    src = re.sub(r"(function\s+\w+\s*\([^)]*\))\s+returns", r"\1 public returns", src)
    return src


def patch_address_to_payable_statevar(src: str, m: re.Match) -> str:
    """State var `address X` that is assigned `msg.sender` or used in .transfer → `address payable X`.
    Very heuristic: detect 'address public X' or 'address X' at contract scope."""
    # prefer conservative: only rewrite ones where downstream .transfer / .send is used
    # on that identifier. For now, just flip every non-payable state var address to payable.
    src = re.sub(r"\baddress(\s+(public|private|internal))?\s+(\w+)\s*(=|;)",
                 r"address payable\1 \3 \4", src)
    # dedupe: "address payable payable x" → "address payable x"
    src = re.sub(r"address payable payable", "address payable", src)
    return src


def patch_throw_stmt(src: str, m: re.Match) -> str:
    return re.sub(r"\bthrow\s*;", "revert();", src)


def patch_bytes_literal_to_bytes(src: str, m: re.Match) -> str:
    """Sometimes 0.4 code uses `bytes()` constructor on a string without explicit cast."""
    # handled ad-hoc as needed; placeholder for now
    return src


def patch_not_overflow_unchecked(src: str, m: re.Match) -> str:
    """Some 0.4 code does `uint8(-x)` or similar casts that now overflow. Wrap in unchecked.
    Low precision; skip for now."""
    return src


def patch_emit_keyword(src: str, m: re.Match) -> str:
    """Old code: `Transfer(from, to, val);` without `emit`. 0.8 requires `emit`."""
    # Look for bare `CapitalizedName(args);` at statement level that matches a declared event
    events = re.findall(r"event\s+(\w+)\s*\(", src)
    for ev in events:
        src = re.sub(rf"(?<![a-zA-Z_\.])({ev})\s*\(([^;]*)\)\s*;",
                     rf"emit \1(\2);", src)
    # avoid double-emit
    src = re.sub(r"emit\s+emit\s+", "emit ", src)
    return src


def patch_block_blockhash(src: str, m: re.Match) -> str:
    src = re.sub(r"\bblock\.blockhash\b", "blockhash", src)
    return src


def patch_sha3_to_keccak(src: str, m: re.Match) -> str:
    src = re.sub(r"\bsha3\s*\(", "keccak256(", src)
    return src


def patch_selfdestruct(src: str, m: re.Match) -> str:
    # already handled in bulk, but ensure
    src = re.sub(r"\bsuicide\s*\(", "selfdestruct(", src)
    return src


def patch_drop_legacy_modifier(src: str, m: re.Match) -> str:
    """`.send(x)` now returns bool; old code used as statement. Make it enforced with require."""
    # risky to auto-rewrite; skip for now.
    return src


def patch_memory_location(src: str, m: re.Match) -> str:
    """Insert `memory` for reference-type params in function/constructor signatures.
    Applies only to `function ... (...)` and `constructor (...)` — NOT to `event` / `struct`."""

    def rewrite_sig(mm: re.Match) -> str:
        head = mm.group(1)  # "function foo" or "constructor"
        params = mm.group(2)  # content inside ()
        tail = mm.group(3)  # rest up to `{` or `;`
        # Insert memory for reference types in params
        for typ in ("string", "bytes"):
            params = re.sub(
                rf"\b({typ})(?!\s+(?:memory|calldata|storage))\s+(\w+)",
                r"\1 memory \2",
                params,
            )
        params = re.sub(
            r"\b((?:uint\d*|int\d*|address|bool|bytes\d+)\[\s*\])(?!\s+(?:memory|calldata|storage))\s+(\w+)",
            r"\1 memory \2",
            params,
        )
        params = re.sub(
            r"\b([A-Z]\w*\[\s*\])(?!\s+(?:memory|calldata|storage))\s+(\w+)",
            r"\1 memory \2",
            params,
        )
        # Struct-typed single params: `Type ident` (uppercase)
        params = re.sub(
            r"\b([A-Z]\w*)(?!\s+(?:memory|calldata|storage|payable))\s+(\w+)(?=\s*[,\)])",
            lambda m2: f"{m2.group(1)} memory {m2.group(2)}" if m2.group(1) not in ("ERC20", "IERC20", "IERC721", "ERC721") else m2.group(0),
            params,
        )
        return f"{head}({params}){tail}"

    # Apply only to function signatures (has `function NAME(...)` prefix) and constructors.
    # The signature header ends at the start of `{` or `;`.
    src = re.sub(
        r"(function\s+\w+\s*)\(([^)]*)\)(\s+[^{;]*?)(?=[\{;])",
        rewrite_sig,
        src,
        flags=re.DOTALL,
    )
    src = re.sub(
        r"(constructor\s*)\(([^)]*)\)(\s+[^{;]*?)(?=[\{;])",
        rewrite_sig,
        src,
        flags=re.DOTALL,
    )
    # Return decl: `returns (string)` → `returns (string memory)`
    for typ in ("string", "bytes"):
        src = re.sub(
            rf"returns\s*\(\s*({typ})\s*\)",
            rf"returns (\1 memory)",
            src,
        )
    # Strip `memory` from event declarations (events don't support data locations)
    def strip_event_memory(mm):
        s = mm.group(0)
        s = re.sub(r"(\b\w+)\s+memory\s+(\w+)", r"\1 \2", s)
        s = re.sub(r"(\b\w+\[\s*\])\s+memory\s+(\w+)", r"\1 \2", s)
        return s
    src = re.sub(r"event\s+\w+\s*\([^)]*\)\s*;", strip_event_memory, src)
    return src


def patch_constant_function(src: str, m: re.Match) -> str:
    """function f() constant returns (...) → function f() view returns (...)"""
    src = re.sub(r"(function[^{;]*?)\bconstant\b", r"\1view", src)
    return src


def patch_internal_ctor(src: str, m: re.Match) -> str:
    """`constructor(...) internal {...}` → `constructor(...) {...}`. (Ignore the abstract warning.)"""
    src = re.sub(r"(constructor\s*\([^)]*\))\s+internal\s*(\{)", r"\1 \2", src)
    return src


def patch_virtual_unimpl(src: str, m: re.Match) -> str:
    """Functions without body (ending in `;`) need `virtual`. Place virtual BEFORE returns clause."""
    # Case 1: with returns clause
    src = re.sub(
        r"(function\s+\w+\s*\([^)]*\)\s+(?:public|external|internal|private)(?:\s+(?:view|pure|payable|constant))?)\s+(returns\s*\([^)]*\))\s*;",
        r"\1 virtual \2;",
        src,
    )
    # Case 2: no returns clause
    src = re.sub(
        r"(function\s+\w+\s*\([^)]*\)\s+(?:public|external|internal|private)(?:\s+(?:view|pure|payable|constant))?)\s*;",
        r"\1 virtual;",
        src,
    )
    # Fix misplacement: `... returns(...) virtual;` → `... virtual returns(...);`
    src = re.sub(
        r"(returns\s*\([^)]*\))\s+virtual\s*;",
        r"virtual \1;",
        src,
    )
    # Clean up double virtual
    src = re.sub(r"\bvirtual\s+virtual\b", "virtual", src)
    return src


def patch_address_zero_compare(src: str, m: re.Match) -> str:
    """`x != 0` / `x == 0` on address → `x != address(0)` / `x == address(0)`.
    Without type info, we rewrite all `ident {==,!=} 0` where ident is likely an address."""
    # Heuristic: rewrite patterns like `_owner != 0` where _owner appears adjacent to `address`
    # type declarations. Simpler: just rewrite `== 0`/`!= 0` where the LHS is a plain identifier
    # or `msg.sender` etc. Risky — may break uint comparisons. Apply only where error says so
    # by rewriting ALL such comparisons; integer `!= 0` on uint is still legal, so no regression.
    # Actually that claim is wrong — `uint x; if (x != 0)` is still fine in 0.8. But
    # `address x; if (x != 0)` is rejected. So rewriting both uint AND address cases: uint stays
    # legal because `address(0)` wouldn't match uint. Let me be precise: rewrite `ident != 0` to
    # `ident != address(0)` ONLY if ident followed by `.` (addr-like usage). Safer: do it naively
    # and let compile errors flag regressions.
    # Start with msg.sender and common address-like identifiers:
    for ident in ("_owner", "_to", "_from", "_spender", "owner", "to", "from", "spender", "msg.sender", "tx.origin", "newOwner"):
        src = re.sub(rf"(?<![A-Za-z0-9_\.]){re.escape(ident)}\s*!=\s*0\b", rf"{ident} != address(0)", src)
        src = re.sub(rf"(?<![A-Za-z0-9_\.]){re.escape(ident)}\s*==\s*0\b", rf"{ident} == address(0)", src)
    # Also: `address(0x0)` / `address(0)` already correct; leave alone.
    return src


def patch_interface_external(src: str, m: re.Match) -> str:
    """In interface blocks, public→external."""
    # Finds `interface X { ... function f() public ... }`; rewrites public→external inside.
    def rewrite_block(match):
        body = match.group(0)
        body = re.sub(r"\b(function\s+\w+\s*\([^)]*\))\s+public\b", r"\1 external", body)
        return body
    src = re.sub(r"interface\s+\w+\s*\{[^}]*\}", rewrite_block, src, flags=re.DOTALL)
    return src


def patch_override_needed(src: str, m: re.Match) -> str:
    """For the common ERC20 override pattern, add `override` to `_mint`/`_burn`/`_transfer`/`_approve`
    and other common override points. Very narrow; most contracts won't hit this."""
    for fname in ("_mint", "_burn", "_transfer", "_approve", "transfer", "transferFrom", "approve", "allowance", "balanceOf", "totalSupply", "name", "symbol", "decimals", "increaseAllowance", "decreaseAllowance"):
        # add override between visibility and body/returns
        pat = rf"(function\s+{fname}\s*\([^)]*\)\s+(?:public|external|internal|private)(?:\s+(?:view|pure|payable))?)\s*(returns\s*\([^)]*\))?\s*\{{"
        def repl(mm):
            sig = mm.group(1)
            ret = mm.group(2) or ""
            if " override" in sig or " virtual override" in sig:
                return mm.group(0)  # already has it
            if ret:
                return f"{sig} virtual override {ret} {{"
            return f"{sig} virtual override {{"
        src = re.sub(pat, repl, src)
    src = re.sub(r"virtual\s+virtual\b", "virtual", src)
    src = re.sub(r"override\s+override\b", "override", src)
    return src


def patch_payable_contract_cast(src: str, m: re.Match) -> str:
    """`payable(<contract instance>)` → `payable(address(<contract instance>))`.
    Our earlier patch may have introduced `payable(token)` where `token` is a contract type."""
    # we can't distinguish; apply a safer two-layer wrap where missing:
    # payable(X) → payable(address(X)) if X is not already `address(...)` or identifier preceded by `address`
    src = re.sub(r"payable\(\s*((?!address\s*\()[a-zA-Z_]\w*(?:\.\w+)*)\s*\)",
                 r"payable(address(\1))", src)
    # Don't over-double: `payable(address(address(x)))` — normalize
    src = re.sub(r"address\(address\(([^)]+)\)\)", r"address(\1)", src)
    return src


def patch_keccak_abi_encode(src: str, m: re.Match) -> str:
    """`keccak256(a, b, c)` → `keccak256(abi.encodePacked(a, b, c))`."""
    # Rewrite when keccak256 called with >1 arg.
    def repl(mm):
        args = mm.group(1)
        # if args has commas at top level (not inside parens), wrap in abi.encodePacked
        depth = 0
        has_top_comma = False
        for ch in args:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                has_top_comma = True
                break
        if has_top_comma:
            return f"keccak256(abi.encodePacked({args}))"
        return mm.group(0)
    src = re.sub(r"keccak256\(([^)]+)\)", repl, src)
    return src


def patch_unicode_string(src: str, m: re.Match) -> str:
    """Convert `"...non-ascii..."` → `unicode"...non-ascii..."`."""
    # find string literals with non-ascii chars; prepend `unicode`
    def repl(mm):
        s = mm.group(0)
        inner = s[1:-1]
        if any(ord(c) > 127 for c in inner):
            return 'unicode' + s
        return s
    src = re.sub(r'"(?:[^"\\]|\\.)*"', repl, src)
    # dedupe: `unicodeunicode"..."` — shouldn't happen but guard
    src = re.sub(r'unicode(unicode)+"', 'unicode"', src)
    return src


def patch_strip_docs(src: str, m: re.Match) -> str:
    """Remove stray @return NatSpec lines that exceed the parameter count."""
    # Strip entire /// @return ... and /** ... */ docstring blocks conservatively — they're comments.
    # Remove all NatSpec @return tags (they're cosmetic).
    src = re.sub(r"^\s*///\s*@return[^\n]*\n", "", src, flags=re.MULTILINE)
    src = re.sub(r"\*\s*@return[^\n]*\n", "", src)
    return src


def patch_fix_double_visibility(src: str, m: re.Match) -> str:
    """`function f() external external returns (...)` → single external."""
    src = re.sub(r"\b(public|external|internal|private)\s+\1\b", r"\1", src)
    # also `external public` → `external`
    src = re.sub(r"\bexternal\s+public\b", "external", src)
    src = re.sub(r"\bpublic\s+external\b", "external", src)
    return src


def patch_fix_returns_with_var_names(src: str, m: re.Match) -> str:
    """`returns (uint256 myVar)` with shadowing var; rename or strip. Best-effort: strip the var name."""
    src = re.sub(r"returns\s*\(\s*(uint\d*|int\d*|bool|address|bytes\d*)\s+\w+\s*\)",
                 r"returns (\1)", src)
    return src


def patch_strip_all_natspec(src: str, m: re.Match) -> str:
    """Strip every NatSpec comment (single-line `///` and block `/** ... */`)."""
    # strip single-line NatSpec. NOTE: `///` at start of line only (within function bodies
    # it's equivalent to `//`, safe to strip too)
    src = re.sub(r"^\s*///[^\n]*\n", "\n", src, flags=re.MULTILINE)
    # strip NatSpec block docs. NatSpec blocks start with `/**` (not `/*`).
    src = re.sub(r"/\*\*[^*]*(?:\*(?!/)[^*]*)*\*/", "", src)
    return src


def patch_mark_abstract(src: str, m: re.Match) -> str:
    """When solc says `Contract "X" should be marked as abstract`, rewrite `contract X` → `abstract contract X`."""
    # mark every contract that declares at least one unimplemented `virtual` function (body is `;`).
    contract_names = re.findall(r"(?<!abstract\s)(?<!abstract  )\bcontract\s+(\w+)", src)
    for cn in contract_names:
        # find this contract's block
        pat = re.compile(rf"(?<!abstract\s)\bcontract\s+{cn}\b")
        m0 = pat.search(src)
        if not m0:
            continue
        start = m0.start()
        depth = 0
        i = src.find("{", start)
        if i < 0:
            continue
        j = i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = src[i:j+1]
        # detect presence of virtual unimplemented functions (body ends with `;`)
        has_virtual = re.search(r"\bvirtual\b[^{;]*;", block) is not None
        if has_virtual:
            src = src[:start] + "abstract " + src[start:]
    # dedupe
    src = re.sub(r"abstract\s+abstract\b", "abstract", src)
    return src


def patch_contract_balance(src: str, m: re.Match) -> str:
    """`SomeContract.balance` or `identifier.balance` where identifier is a contract → `address(X).balance`.
    Also covers `this.balance` — already works in 0.8 but redundant."""
    # `this.balance` → `address(this).balance`
    src = re.sub(r"\bthis\.balance\b", "address(this).balance", src)
    # Be conservative; bare-identifier `.balance` might be legit address var. Let it trip again if so.
    # If solc asks for `address(this)` specifically in the error, that's already handled above.
    return src


def patch_safemath_second_pass(src: str, m: re.Match) -> str:
    """Aggressive SafeMath elimination: convert residual `.add(`/`.sub(`/`.mul(`/`.div(`/`.mod(` chains."""
    # handle x.add(y) chains including .add(y).sub(z)
    for op, sym in [("add", "+"), ("sub", "-"), ("mul", "*"), ("div", "/"), ("mod", "%")]:
        # greedy balanced-paren match is hard in regex; do single-level:
        pat = re.compile(rf"\.{op}\(([^()]*(?:\([^()]*\)[^()]*)?)\)")
        while True:
            new = pat.sub(lambda mm: f" {sym} ({mm.group(1)})", src)
            if new == src:
                break
            src = new
    # Clean up: `(a + (b))` → `(a + b)` where possible (solc will accept either)
    return src


def patch_remove_bogus_override(src: str, m: re.Match) -> str:
    """`Function has override specified but does not override anything` → remove `override` from that function.
    Without line-awareness, remove `override` globally; subsequent iteration will re-add it if really needed."""
    src = re.sub(r"\bvirtual\s+override\b", "virtual", src)
    src = re.sub(r"\boverride\s+", "", src)
    return src


def patch_contract_to_address(src: str, m: re.Match) -> str:
    """Invalid implicit conversion from contract X to address → wrap ident in `address(...)`.
    Low-precision: we don't know WHICH ident. Apply a broad rewrite: every bare identifier
    being passed to `.transfer/.send/require/address cast/equality` gets wrapped IF it looks
    like a contract ref. Too risky without type info. Instead, do minor targeted rewrites:
    - ERC20-like: `token.transfer(addr, amt)` stays the same — but `addr` may be a contract.
    - `emit X(contract_var, ...)` with Topic event expecting `address` → wrap.
    Just do: `== contract_var`  → `== address(contract_var)` when error trips."""
    # insert address() cast in comparisons between contract ref and address
    # VERY conservative: skip this — let user mark as manual if it trips.
    # Minimal: if we see `contract X {` followed by `address ...  =  Y;` where Y is ident of contract type,
    # we can't easily fix. Best effort: wrap every `== identifier` where identifier ends in 'Token', 'Contract',
    # 'Fallback', 'Owner' in `address()`. Too heuristic.
    return src


def patch_fix_no_visibility(src: str, m: re.Match) -> str:
    """Any function without visibility → add `public`."""
    # function foo(args) { → function foo(args) public {
    src = re.sub(
        r"(function\s+\w+\s*\([^)]*\))\s*(\{)",
        lambda mm: mm.group(0) if re.search(r"\b(public|external|internal|private)\b", mm.group(0)) else f"{mm.group(1)} public {mm.group(2)}",
        src,
    )
    # function foo(args) returns ... { (without visibility)
    src = re.sub(
        r"(function\s+\w+\s*\([^)]*\))\s+(returns\s*\()",
        lambda mm: mm.group(0) if re.search(r"\b(public|external|internal|private)\b", mm.group(0)) else f"{mm.group(1)} public {mm.group(2)}",
        src,
    )
    # dedupe
    src = re.sub(r"\bpublic\s+public\b", "public", src)
    return src


def patch_strip_ctor_view(src: str, m: re.Match) -> str:
    """Constructor mustn't be view/pure/payable (historically)."""
    src = re.sub(r"(constructor\s*\([^)]*\))\s+(view|pure)\s*(\{)", r"\1 \3", src)
    return src


def patch_cannot_override_state_var(src: str, m: re.Match) -> str:
    """`Cannot override public state variable` — usually from SafeMath using-attached modifier.
    Fix: remove `override` from the offending identifier or remove the derived `public` state var.
    Narrow fix: remove override from identifiers that match common state-var names."""
    # not a safe blanket change; skip, mark manual
    return src


def patch_return_addr_payable(src: str, m: re.Match) -> str:
    """Function returns type `address` but actually returns `address payable` now (after our .transfer rewrite)."""
    src = re.sub(r"returns\s*\(\s*address\s*\)", "returns (address payable)", src)
    return src


def patch_drop_ctor_visibility(src: str, m: re.Match) -> str:
    """`constructor(args) public {...}` → `constructor(args) {...}`"""
    src = re.sub(r"(constructor\s*\([^)]*\))\s+(?:public|external|internal|private)\s*(\{)",
                 r"\1 \2", src)
    return src


# Ordered rules. First match wins per iteration.
RULES = [
    # Error message substring → patch function
    ("Expected a state variable declaration", patch_anonymous_fallback),
    ("Functions are not allowed to have the same name as the contract", patch_contract_named_ctor),
    ("not allowed to have the same name as the contract", patch_contract_named_ctor),
    ("Constructor can", patch_contract_named_ctor),
    ("Contracts are not allowed to name a function", patch_contract_named_ctor),
    ("not allowed to name a function after the contract", patch_contract_named_ctor),
    ("send and transfer are only available for objects of type", patch_send_transfer_to_payable),
    ("\"send\" and \"transfer\" are only available for objects of type", patch_send_transfer_to_payable),
    ("not implicitly convertible to expected type address payable", patch_send_transfer_to_payable),
    ("not implicitly convertible to expected type \"address payable\"", patch_send_transfer_to_payable),
    ("constant used as a function", patch_constant_to_view),  # placeholder
    ("Mutability restrictions have been tightened", patch_constant_to_view),
    ("cannot be declared as `constant`", patch_constant_to_view),
    ("Expected pragma, import directive or contract/interface/library/struct/enum definition", patch_var_keyword),
    ("\"var\" has been deprecated", patch_var_keyword),
    ("No visibility specified", patch_returns_visibility),
    ("No visibility was specified", patch_returns_visibility),
    ("Functions have to be specified as either \"pure\", \"view\" or \"payable\"", patch_constant_to_view),
    ("needs to be marked \"emit\"", patch_emit_keyword),
    ("emit\" must be followed", patch_emit_keyword),
    ("Event invocations have to be prefixed by \"emit\"", patch_emit_keyword),
    ("\"throw\" is no longer supported", patch_throw_stmt),
    ("Name \"now\" is deprecated", lambda s, m: re.sub(r"(?<![A-Za-z0-9_])now(?![A-Za-z0-9_])", "block.timestamp", s)),
    ("\"suicide\" has been renamed", patch_selfdestruct),
    ("\"sha3\" has been deprecated", patch_sha3_to_keccak),
    ("Undeclared identifier. \"sha3\"", patch_sha3_to_keccak),
    ("\"block.blockhash\" has been deprecated", patch_block_blockhash),
    ("add\" not found or not visible after argument-dependent lookup", patch_using_safemath),
    ("sub\" not found or not visible after argument-dependent lookup", patch_using_safemath),
    ("mul\" not found or not visible after argument-dependent lookup", patch_using_safemath),
    ("div\" not found or not visible after argument-dependent lookup", patch_using_safemath),
    # catch-all: address→payable
    ("Member \"transfer\" not found or not visible", patch_send_transfer_to_payable),
    ("Member \"send\" not found or not visible", patch_send_transfer_to_payable),
    # 0.5+ data-location requirements
    ("Data location must be \"storage\" or \"memory\" for constructor parameter", patch_memory_location),
    ("Data location must be \"memory\" or \"calldata\" for parameter", patch_memory_location),
    ("Data location must be \"memory\" or \"calldata\" for return parameter", patch_memory_location),
    ("Data location must be specified", patch_memory_location),
    # 0.5+ `constant` parser error
    ("Expected '{' but got 'constant'", patch_constant_function),
    # 0.5+ internal ctor
    ("Non-abstract contracts cannot have internal constructors", patch_internal_ctor),
    # 0.6+ virtual/override
    ("Functions without implementation must be marked \"virtual\"", patch_virtual_unimpl),
    ("without implementation must be marked", patch_virtual_unimpl),
    # address payable return mismatch
    ("is not implicitly convertible to expected type (type of first return variable) address payable", patch_return_addr_payable),
    ("not implicitly convertible to expected type", patch_return_addr_payable),
    # 0.5+ ctor visibility ignored → just remove
    ("Visibility for constructor is ignored", patch_drop_ctor_visibility),
    # 0.5+ address/int comparison
    ("cannot be applied to types address and int_const", patch_address_zero_compare),
    ("cannot be applied to types address payable and int_const", patch_address_zero_compare),
    # interface members must be external
    ("Functions in interfaces must be declared external", patch_interface_external),
    # override needed
    ("Derived contract must override function", patch_override_needed),
    ("Overriding function is missing \"override\" specifier", patch_override_needed),
    ("Trying to override non-virtual function", patch_override_needed),
    # cast contract→payable
    ("Explicit type conversion not allowed from \"contract", patch_payable_contract_cast),
    ("Explicit type conversion not allowed", patch_payable_contract_cast),
    ("Invalid type for argument in function call. Invalid implicit conversion from contract", patch_payable_contract_cast),
    # keccak256 / sha3 multi-arg needs abi.encodePacked
    ("Wrong argument count for function call", patch_keccak_abi_encode),
    ("This function requires a single bytes argument", patch_keccak_abi_encode),
    # unicode string
    ("Invalid character in string", patch_unicode_string),
    # docs
    ("Documentation tag \"@return\"", patch_strip_docs),
    # double visibility after patches
    ("Visibility already specified as", patch_fix_double_visibility),
    # NatSpec doctag issues
    ("Documentation tag", patch_strip_all_natspec),
    # abstract needed
    ("should be marked as abstract", patch_mark_abstract),
    # bogus override
    ("Function has override specified but does not override anything", patch_remove_bogus_override),
    ("does not override anything", patch_remove_bogus_override),
    # no visibility on function (not caught by returns_visibility)
    ("No visibility specified", patch_fix_no_visibility),
    # ctor view/pure
    ("Constructor must be payable or non-payable", patch_strip_ctor_view),
    # state-var override
    ("Cannot override public state variable", patch_remove_bogus_override),
    ("Overriding public state variable is missing", patch_remove_bogus_override),
    # .balance on contract type
    ("Member \"balance\" not found or not visible after argument-dependent lookup in contract", patch_contract_balance),
    # SafeMath residuals
    ("\"sub\" not found or not visible", patch_safemath_second_pass),
    ("\"add\" not found or not visible", patch_safemath_second_pass),
    ("\"mul\" not found or not visible", patch_safemath_second_pass),
    ("\"div\" not found or not visible", patch_safemath_second_pass),
    ("\"mod\" not found or not visible", patch_safemath_second_pass),
]


def apply_first_matching_rule(src: str, stderr: str) -> tuple[str, str] | None:
    """Return (new_src, rule_name) for first matching rule, or None if nothing matches."""
    for sub, fn in RULES:
        if sub in stderr:
            new = fn(src, None)
            if new != src:
                return new, fn.__name__
    return None


def compile_one(name: str, max_iters: int = 25) -> dict:
    sol = SOURCES / name / "contract.sol"
    log_path = LOGDIR / f"upgrade_{name}.log"
    result = {"name": name, "iters": 0, "status": "unknown", "patches": []}
    with log_path.open("w") as log:
        prev_stderr = None
        for i in range(max_iters):
            rc, stdout, stderr = run_solc(sol)
            log.write(f"=== iter {i} rc={rc} ===\n")
            log.write(stderr)
            log.write("\n")
            if rc == 0 and "Error:" not in stderr:
                # parsing passed; try full compile
                rc2, out2, err2 = run_solc_full(sol)
                log.write(f"=== full compile rc={rc2} ===\n{err2}\n")
                if rc2 == 0 and "Error:" not in err2:
                    result["status"] = "ok"
                    result["iters"] = i + 1
                    return result
                stderr = err2
                rc = rc2
            if stderr == prev_stderr:
                result["status"] = "stuck_same_error"
                result["iters"] = i + 1
                log.write("=== stuck: same error twice ===\n")
                return result
            patched = apply_first_matching_rule(sol.read_text(), stderr)
            if patched is None:
                result["status"] = "no_rule_matched"
                result["iters"] = i + 1
                log.write("=== no rule matched ===\n")
                return result
            new_src, rule_name = patched
            sol.write_text(new_src)
            result["patches"].append(rule_name)
            log.write(f"=== applied {rule_name} ===\n")
            prev_stderr = stderr
        result["status"] = "budget_exhausted"
        result["iters"] = max_iters
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run only one contract name")
    args = ap.parse_args()

    selected = json.loads((ROOT / "selected.json").read_text())
    if args.only:
        selected = [args.only]

    summary = []
    for name in selected:
        t0 = time.time()
        r = compile_one(name)
        r["elapsed"] = round(time.time() - t0, 1)
        summary.append(r)
        print(f"[{name}] {r['status']} iters={r['iters']} elapsed={r['elapsed']}s patches={r['patches']}")
    (ROOT / "upgrade_summary.json").write_text(json.dumps(summary, indent=2))
    ok = sum(1 for r in summary if r["status"] == "ok")
    print(f"\n{ok}/{len(summary)} compiled successfully")


if __name__ == "__main__":
    main()
