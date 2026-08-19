#!/usr/bin/env python3
"""Materialize the four bugfix124 RQ3-closure replays from retained RQ1 artifacts.

RQ3 has the frozen identities but no concrete source files for these subjects.
The retained RQ1 replay and flat source are copied as explicit reconstructed
evidence; this script never adds certification or PUT credit.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
OUT = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_NoPUT600/"
    "adoption-bundles/rq3-mechanical-match-20260815"
)

CASES = [
    ("rcx_unchecked_low_level_calls__0xa46edd6a9a93feec36576ee5048146870ea2c3ae__TIPS", "76", "EBU"),
    ("rcx_unchecked_low_level_calls__0xa46edd6a9a93feec36576ee5048146870ea2c3ae__sGuard", "63", "EBU"),
    ("rcx_unchecked_low_level_calls__0xd5967fed03e85d1cce44cab284695b41bc675b5c__TIPS", "73", "demo"),
    ("rcx_unchecked_low_level_calls__0xd5967fed03e85d1cce44cab284695b41bc675b5c__sGuard", "60", "demo"),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(subject: str, fn: str, unit: str) -> list[str]:
    return [f"bugfix124/{subject}", f"sol:@C@{unit}@F@transfer#{fn}", "transfer", "1", ""]


def main() -> None:
    report = []
    for subject, fn, contract in CASES:
        subject_root = ROOT / "bugfix124" / "subjects" / subject
        source = next(subject_root.glob(
            f"put/*__transfer__pf{fn}/*__certify-results/test/*{contract}_transfer_concrete1_fb.t.sol"
        ))
        flat = source.parents[1] / "src" / "flat.sol"
        assert flat.is_file(), flat
        ident = identity(subject, fn, contract)
        claim_sha = hashlib.sha256(json.dumps(ident, separators=(",", ":")).encode()).hexdigest()
        key = hashlib.sha256("\0".join(ident).encode()).hexdigest()[:24]
        destination = subject_root / "put" / "transfer" / "rq3-mechanical" / "reconstructed-from-rq3" / key
        dest_test = destination / "test" / source.name
        dest_flat = destination / "src" / "flat.sol"
        dest_test.parent.mkdir(parents=True, exist_ok=True)
        dest_flat.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest_test)
        shutil.copy2(flat, dest_flat)
        result_path = subject_root / "result.json"
        result = json.loads(result_path.read_text())
        entries = [x for x in result.get("rq3_mechanical_closure", []) if x.get("frozen_identity") != ident]
        entries.append({
            "schema": "veriput-rq3-mechanical-closure-entry/v2",
            "frozen_identity": ident,
            "rq3_identity": ident,
            "binding_status": "reconstructed-from-rq3",
            "binding_tier": "retained-rq1-source-rq3-claim",
            "status": "source-backed-reconstructed-from-rq3",
            "source": str(dest_test),
            "source_sha256": sha(dest_test),
            "flat_source": str(dest_flat),
            "flat_source_sha256": sha(dest_flat),
            "claim_sha256": claim_sha,
            "claim_basis": "frozen-rq3-identity",
            "source_provenance": str(source),
            "flat_provenance": str(flat),
            "reconstructed_from_rq3": True,
            "forge_run": False,
            "put_credit": False,
            "identity_rewrite": False,
            "test": source.name,
        })
        result["rq3_mechanical_closure"] = entries
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        report.append({
            "identity": ident,
            "status": "source-backed-reconstructed-from-rq3",
            "source": str(dest_test),
            "source_sha256": sha(dest_test),
            "flat_source": str(dest_flat),
            "flat_source_sha256": sha(dest_flat),
            "claim_sha256": claim_sha,
            "source_provenance": str(source),
            "flat_provenance": str(flat),
            "forge_run": False,
            "put_credit": False,
        })
    out = OUT / "bugfix14-reconstruction-v1.json"
    out.write_text(json.dumps({"schema": "rq3-bugfix14-reconstruction/v1", "rows": report}, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(report), "report": str(out)}))


if __name__ == "__main__":
    main()
