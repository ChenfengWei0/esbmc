"""Byte-range source editing driven by solc's AST `src` field.

The AST's `src` field is `"<offset>:<length>:<srcIdx>"` — offsets into
the original source bytes. The surgery layer deletes declarations by
splicing out their byte range (plus trailing delimiter), and changes
function visibility by locating the visibility keyword within the
function signature and replacing it.

Invariant: every edit is applied to the original source string; edits
are ordered by descending byte offset so earlier offsets remain valid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class SourceEdit:
    start: int       # byte offset in original source
    end: int         # exclusive
    replacement: str


@dataclass(frozen=True)
class SrcRange:
    offset: int
    length: int
    src_idx: int

    @classmethod
    def parse(cls, s: str) -> "SrcRange":
        parts = s.split(":")
        return cls(int(parts[0]), int(parts[1]), int(parts[2]))

    @property
    def end(self) -> int:
        return self.offset + self.length


def parse_src(node: dict) -> Optional[SrcRange]:
    """Extract the AST node's `src` range, if present."""

    s = node.get("src")
    if not isinstance(s, str):
        return None
    try:
        return SrcRange.parse(s)
    except (ValueError, IndexError):
        return None


def apply_edits(source: str, edits: Iterable[SourceEdit]) -> str:
    """Apply edits to the original source in descending-offset order."""

    ordered = sorted(edits, key=lambda e: e.start, reverse=True)
    chars = list(source)
    out_chars = chars
    for e in ordered:
        if e.start < 0 or e.end > len(out_chars) or e.end < e.start:
            raise ValueError(
                f"SourceEdit out of bounds: {e.start}:{e.end} on source of "
                f"len {len(source)}"
            )
        out_chars[e.start : e.end] = list(e.replacement)
    return "".join(out_chars)


# ---------------------------------------------------------------------------
# Deletion — covers function-, struct-, enum-, event-, error-, and import
# declarations. We trim trailing semicolons / commas / newlines so the
# resulting source stays syntactically clean.
# ---------------------------------------------------------------------------

_TRAILING_WS = re.compile(r"[ \t]*\n?")


def delete_range(source: str, rng: SrcRange) -> SourceEdit:
    """Delete a byte range plus its immediate trailing whitespace + newline."""

    end = rng.end
    # Extend to swallow trailing newline if the next byte is whitespace +
    # newline (common for declaration ranges that don't include their own
    # newline terminator).
    match = _TRAILING_WS.match(source, end)
    if match:
        end = match.end()
    return SourceEdit(start=rng.offset, end=end, replacement="")


# ---------------------------------------------------------------------------
# Visibility rewrite. For a function signature like
#   function f(...) public pure returns (bool)
# the AST node has `visibility: "public"` and the keyword literal lives
# in the signature range but not directly surfaced as a separate node.
# We locate the keyword by scanning the signature prefix (everything up
# to the first `{` or `;` for abstract/virtual functions).
# ---------------------------------------------------------------------------

_VIS_KEYWORDS = ("public", "external", "internal", "private")

# Word boundary so we don't match `public` inside a comment or string.
_KW_PATTERN = re.compile(r"\b(" + "|".join(_VIS_KEYWORDS) + r")\b")


def _strip_comments_and_strings(chunk: str) -> str:
    """Replace comments and string literals with spaces so keyword
    matching cannot be fooled by text inside them."""

    out = []
    i = 0
    n = len(chunk)
    while i < n:
        ch = chunk[i]
        # Line comment
        if ch == "/" and i + 1 < n and chunk[i + 1] == "/":
            while i < n and chunk[i] != "\n":
                out.append(" ")
                i += 1
            continue
        # Block comment
        if ch == "/" and i + 1 < n and chunk[i + 1] == "*":
            out.append(" ")
            out.append(" ")
            i += 2
            while i < n and not (chunk[i] == "*" and i + 1 < n and chunk[i + 1] == "/"):
                out.append(" " if chunk[i] != "\n" else "\n")
                i += 1
            out.append(" ")
            out.append(" ")
            i += 2
            continue
        # String literal (", ', or hex/unicode prefixed — cover the common cases)
        if ch in ("'", '"'):
            quote = ch
            out.append(" ")
            i += 1
            while i < n and chunk[i] != quote:
                if chunk[i] == "\\" and i + 1 < n:
                    out.append(" ")
                    out.append(" ")
                    i += 2
                    continue
                out.append(" " if chunk[i] != "\n" else "\n")
                i += 1
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def change_visibility_to_internal(
    source: str, sig_range: SrcRange
) -> Optional[SourceEdit]:
    """Locate the first visibility keyword inside `sig_range` and replace
    it with `internal`. Returns None if the function already has no
    visibility keyword or is already internal/private."""

    chunk = source[sig_range.offset : sig_range.end]
    # Stop scanning at the body's opening brace or semicolon to avoid
    # matching keywords inside the body (can't happen in valid Solidity
    # signatures, but better safe).
    brace = chunk.find("{")
    semi = chunk.find(";")
    stop_candidates = [x for x in (brace, semi) if x != -1]
    stop = min(stop_candidates) if stop_candidates else len(chunk)
    signature_prefix = chunk[:stop]

    masked = _strip_comments_and_strings(signature_prefix)
    m = _KW_PATTERN.search(masked)
    if not m:
        return None
    vis_word = m.group(1)
    if vis_word in ("internal", "private"):
        return None  # already restricted
    start = sig_range.offset + m.start()
    end = sig_range.offset + m.end()
    return SourceEdit(start=start, end=end, replacement="internal")


# ---------------------------------------------------------------------------
# High-level collection: walk an AST and build a map
#   qualified_name -> (FunctionDefinition node, SrcRange)
# for every function/modifier in the program, so phases can pick
# candidates by name.
# ---------------------------------------------------------------------------


@dataclass
class FunctionEntry:
    contract: str
    name: str           # bare function name
    kind: str           # "function" / "constructor" / "fallback" / "receive" /
                        # "modifier"
    visibility: str     # "public" / "external" / "internal" / "private" /
                        # "" if not applicable (modifier, constructor before 0.6)
    src: SrcRange
    ast_id: int
    state_mutability: str = ""
    is_virtual: bool = False
    overrides: bool = False
    source_file: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.contract}.{self.name}"


def collect_function_entries(ast: Dict[str, dict]) -> List[FunctionEntry]:
    """ast is `{filename: solc_ast_node_for_source_unit}`."""

    out: List[FunctionEntry] = []
    for filename, unit in ast.items():
        nodes = unit.get("nodes", [])
        for node in nodes:
            if node.get("nodeType") != "ContractDefinition":
                continue
            contract = node.get("name", "")
            for member in node.get("nodes", []):
                ntype = member.get("nodeType")
                if ntype not in ("FunctionDefinition", "ModifierDefinition"):
                    continue
                src = parse_src(member)
                if src is None:
                    continue
                kind = member.get("kind", "function")
                if ntype == "ModifierDefinition":
                    kind = "modifier"
                name = member.get("name") or kind
                out.append(
                    FunctionEntry(
                        contract=contract,
                        name=name,
                        kind=kind,
                        visibility=member.get("visibility", ""),
                        src=src,
                        ast_id=int(member.get("id", 0)),
                        state_mutability=member.get("stateMutability", ""),
                        is_virtual=bool(member.get("virtual", False)),
                        overrides=bool(member.get("overrides")) if member.get("overrides") else False,
                        source_file=filename,
                    )
                )
    return out


def collect_call_graph(ast: Dict[str, dict]) -> Dict[str, List[str]]:
    """Very cheap call graph: for every function F, a list of fully
    qualified names G whose identifier appears inside F's body.

    Overcounts (name-only; no overload resolution), but Phase 2 uses this
    only for the weight heuristic ("does f have any caller in the
    retained set?"), where overcounting biases toward keeping more
    functions (sound). Phase 1 uses compile-time feedback for the strict
    dependency edge, not this analysis.
    """

    entries = collect_function_entries(ast)
    name_index: Dict[str, List[FunctionEntry]] = {}
    for e in entries:
        name_index.setdefault(e.name, []).append(e)

    # Collect call-like AST nodes under each FunctionDefinition.
    def _walk_for_callees(node: dict, acc: set) -> None:
        if isinstance(node, dict):
            if node.get("nodeType") == "FunctionCall":
                expr = node.get("expression", {})
                # expression can be Identifier, MemberAccess, etc.
                if expr.get("nodeType") == "Identifier":
                    acc.add(expr.get("name", ""))
                elif expr.get("nodeType") == "MemberAccess":
                    acc.add(expr.get("memberName", ""))
            for v in node.values():
                _walk_for_callees(v, acc)
        elif isinstance(node, list):
            for v in node:
                _walk_for_callees(v, acc)

    graph: Dict[str, List[str]] = {}
    for filename, unit in ast.items():
        for top in unit.get("nodes", []):
            if top.get("nodeType") != "ContractDefinition":
                continue
            cname = top.get("name", "")
            for member in top.get("nodes", []):
                if member.get("nodeType") not in ("FunctionDefinition", "ModifierDefinition"):
                    continue
                mname = member.get("name") or member.get("kind", "function")
                callees: set = set()
                _walk_for_callees(member.get("body") or {}, callees)
                resolved: List[str] = []
                for c in callees:
                    for candidate in name_index.get(c, []):
                        resolved.append(candidate.qualified)
                graph[f"{cname}.{mname}"] = sorted(set(resolved))
    return graph
