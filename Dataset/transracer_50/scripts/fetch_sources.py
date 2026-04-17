#!/usr/bin/env python3
"""Fetch mainnet source code for all 50 addresses via Sourcify (no API key required).

Writes one dir per address under Dataset/transracer_50/etherscan_meta/<addr>/
with the raw Sourcify response stored as files. Also builds:
  - address_to_name.json — maps checksum address → primary contract name (from metadata)
  - name_to_address.json — reverse map
  - fetch_log.txt — per-address status (ok/partial/missing/error)
"""
from __future__ import annotations
import json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
META = ROOT / "etherscan_meta"
META.mkdir(parents=True, exist_ok=True)
ADDR_LIST = Path("/home/samson/workspace/esbmc/Dataset/contracts_50.txt")
LOG = ROOT / "logs" / "fetch_log.txt"
(ROOT / "logs").mkdir(parents=True, exist_ok=True)


def load_addresses() -> list[str]:
    addrs = []
    for line in ADDR_LIST.read_text().splitlines():
        line = line.strip()
        if line.startswith("0x") and len(line) == 42:
            addrs.append(line)
    return addrs


def sourcify_fetch(addr: str) -> tuple[str, dict]:
    """Return (status, response_json). status ∈ {full, partial, missing, error}."""
    url = f"https://sourcify.dev/server/files/any/1/{addr}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "missing", {}
        return "error", {"http_code": e.code, "reason": str(e)}
    except Exception as e:
        return "error", {"reason": str(e)}
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        return "error", {"reason": "non-json response", "body": body[:200]}
    return j.get("status", "error"), j


def extract_contract_name(files: list[dict]) -> str | None:
    """Find the primary contract name. metadata.json has 'settings.compilationTarget' mapping."""
    for f in files:
        if f.get("name") == "metadata.json":
            try:
                m = json.loads(f["content"])
                tgt = m.get("settings", {}).get("compilationTarget", {})
                if tgt:
                    # pick first (usually only) entry's contract name
                    return next(iter(tgt.values()))
            except Exception:
                pass
    # fallback: grab names from .sol files, exclude non-primary libs
    for f in files:
        nm = f.get("name", "")
        if nm.endswith(".sol"):
            return nm[:-4]
    return None


def extract_compiler_version(files: list[dict]) -> str | None:
    for f in files:
        if f.get("name") == "metadata.json":
            try:
                m = json.loads(f["content"])
                return m.get("compiler", {}).get("version")
            except Exception:
                pass
    return None


def main():
    addrs = load_addresses()
    print(f"Loaded {len(addrs)} addresses from {ADDR_LIST}")
    addr_to_name = {}
    summary = []
    with LOG.open("w") as lf:
        for i, addr in enumerate(addrs, 1):
            outdir = META / addr
            outdir.mkdir(exist_ok=True)
            # skip re-fetch if already cached
            resp_path = outdir / "sourcify.json"
            if resp_path.exists():
                resp = json.loads(resp_path.read_text())
                status = resp.get("status", "error")
            else:
                status, resp = sourcify_fetch(addr)
                resp_path.write_text(json.dumps(resp, indent=2))
                time.sleep(0.3)  # be nice
            name = extract_contract_name(resp.get("files", []))
            cver = extract_compiler_version(resp.get("files", []))
            if name:
                addr_to_name[addr] = {"name": name, "compiler": cver, "sourcify_status": status}
            line = f"[{i:02d}/50] {addr} status={status} name={name} compiler={cver}"
            print(line)
            lf.write(line + "\n")
            summary.append({"addr": addr, "name": name, "status": status, "compiler": cver})

    (ROOT / "address_to_name.json").write_text(json.dumps(addr_to_name, indent=2))
    # reverse map (some names may collide, prefer full-match over partial)
    name_to_addr = {}
    for addr, info in addr_to_name.items():
        n = info["name"]
        prev = name_to_addr.get(n)
        if prev is None or addr_to_name[prev]["sourcify_status"] != "full":
            name_to_addr[n] = addr
    (ROOT / "name_to_address.json").write_text(json.dumps(name_to_addr, indent=2))
    (ROOT / "fetch_summary.json").write_text(json.dumps(summary, indent=2))
    missing = sum(1 for s in summary if s["status"] not in ("full", "partial"))
    print(f"\nDone. {len(addr_to_name)} resolved, {missing} missing/error.")


if __name__ == "__main__":
    main()
