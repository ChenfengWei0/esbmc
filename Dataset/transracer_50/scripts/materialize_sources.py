#!/usr/bin/env python3
"""Materialize contract.sol under sources/<paper_name>/ for each of the 33 selected
(TransRacer Table 2 TRBD>=1) contracts, with a minimal bulk pragma upgrade pass.

Fuzzy-matches the paper name (from selected.json) against the Etherscan primary
contract name captured in address_to_name.json. Records the picked address and
Etherscan name in sources/<paper_name>/meta.json.
"""
from __future__ import annotations
import json, re, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
META = ROOT / "etherscan_meta"
SOURCES = ROOT / "sources"
SOURCES.mkdir(parents=True, exist_ok=True)
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(parents=True, exist_ok=True)

# explicit mappings for names that differ between the paper Table 2 and Etherscan primary
FUZZY_OVERRIDE = {
    "Xpense": "XPS",
    "Freedom": "FreedomStreaming",
    "HubrisOne": "HUBRIS",
    "Viewly": "ViewlyMainSale",
    "Dentacoin": "DentacoinToken",
    "Yihaa": "Yiha",
    # MediBloc ↔ MedXToken is genuinely ambiguous; verify by manual inspection.
    # Tentative: Table 2 MediBloc has 19 funcs / 3237 insts; MedXToken addr is 0xfd1e80..., compiler 0.4.24.
    # Table 1 says MediBloc — we'll use MedXToken and record the override in meta.json so the user can review.
    "MediBloc": "MedXToken",
}


def load_selected() -> list[str]:
    return json.loads((ROOT / "selected.json").read_text())


def load_name_to_addr() -> dict:
    return json.loads((ROOT / "name_to_address.json").read_text())


def load_meta(addr: str) -> dict:
    return json.loads((META / addr / "sourcify.json").read_text())


def pick_main_sol(files: list[dict], contract_name: str) -> str | None:
    """Return concatenation of all .sol files (we'll flatten by simple concat)."""
    sols = [f for f in files if f.get("name", "").endswith(".sol")]
    if not sols:
        return None
    # We'll flatten by concat with separator comments. This is crude but works
    # for most single-file-flattened Etherscan uploads.
    parts = []
    for f in sols:
        parts.append(f"// ===== {f['name']} =====")
        parts.append(f["content"])
    return "\n".join(parts)


PRAGMA_RE = re.compile(r"pragma\s+solidity\s+[^;]+;", re.IGNORECASE)


def bulk_upgrade(src: str) -> str:
    """Minimal 0.8 upgrade pass. Iterative compile-and-patch loop handles the rest."""
    # Collapse all pragma lines to a single >=0.8.0 at top of each file unit.
    # Easier: replace every pragma statement with a clean one.
    src = PRAGMA_RE.sub("pragma solidity >=0.8.0;", src)
    # If no pragma existed anywhere, prepend one
    if "pragma solidity" not in src:
        src = "pragma solidity >=0.8.0;\n" + src
    # throw → revert()
    src = re.sub(r"\bthrow\s*;", "revert();", src)
    # suicide → selfdestruct (renamed in 0.5)
    src = re.sub(r"\bsuicide\s*\(", "selfdestruct(", src)
    # `now` → block.timestamp (removed in 0.7)
    src = re.sub(r"(?<![A-Za-z0-9_])now(?![A-Za-z0-9_])", "block.timestamp", src)
    # SafeMath: drop `using SafeMath for uint256;` lines — 0.8 has built-in overflow checks;
    # but DON'T remove the library itself yet because .add/.sub calls remain. We'll handle
    # those in the compile-and-patch loop.
    return src


def main():
    selected = load_selected()
    name2addr = load_name_to_addr()
    log = open(LOGDIR / "materialize_log.txt", "w")

    picked_map = {}
    unresolved = []
    for paper_name in selected:
        ether_name = FUZZY_OVERRIDE.get(paper_name, paper_name)
        addr = name2addr.get(ether_name)
        if addr is None:
            unresolved.append(paper_name)
            log.write(f"UNRESOLVED {paper_name} (tried {ether_name})\n")
            continue
        picked_map[paper_name] = {"etherscan_name": ether_name, "address": addr}
        resp = load_meta(addr)
        files = resp.get("files", [])
        sol = pick_main_sol(files, ether_name)
        if not sol:
            log.write(f"NO_SOL {paper_name} addr={addr}\n")
            continue
        upgraded = bulk_upgrade(sol)

        outdir = SOURCES / paper_name
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "contract.sol").write_text(upgraded)
        (outdir / "contract.sol.orig").write_text(sol)
        meta = {
            "paper_name": paper_name,
            "etherscan_name": ether_name,
            "address": addr,
            "sourcify_status": resp.get("status"),
            "file_count": len(files),
        }
        (outdir / "meta.json").write_text(json.dumps(meta, indent=2))
        log.write(f"OK {paper_name} → {ether_name}@{addr[:12]}...\n")
        print(f"[{paper_name}] upgraded ({len(upgraded)} bytes)")

    (ROOT / "picked_map.json").write_text(json.dumps(picked_map, indent=2))
    log.write(f"\nresolved={len(picked_map)}/33 unresolved={unresolved}\n")
    log.close()
    print(f"\nResolved {len(picked_map)}/33. Unresolved: {unresolved}")


if __name__ == "__main__":
    main()
