#!/usr/bin/env python3
"""Materialize explicitly-labelled RQ3 reconstructions for missing closure rows.

This does not claim a Forge replay or PUT result.  It only preserves the RQ3
subject flat source and records a source-backed reconstruction in the RQ1
closure, so absence is not silently turned into an exact match.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RQ1 = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
RQ3 = Path("/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/real203/subjects")

ROWS = [
    ("ERC-3643__ERC-3643__IdentityRegistry", "deleteIdentity", "14"),
    ("ERC-3643__ERC-3643__Token", "increaseAllowance", "15"),
    ("ProjectOpenSea__seaport__SeaportNavigator", "helpers", "1"),
    ("balancer__balancer-v3-monorepo__ClaimSignatureRegistry", "signatures", "1"),
    ("compound-finance__comet__OnChainLiquidator", "poolConfigs", "1"),
    ("ensdomains__ens-contracts__DefaultReverseRegistrar", "renounceOwnership", "7"),
    ("ensdomains__ens-contracts__PublicResolver", "supportsInterface", "2"),
    ("euler-xyz__euler-vault-kit__ESynth", "minters", "1"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def existing_wrapper(subject: str, function: str) -> Path | None:
    base = RQ1 / "real203" / "subjects" / subject
    hits = sorted(p for p in base.rglob("*.t.sol")
                  if p.is_file() and "rq3-mechanical" not in str(p)
                  and function in p.name)
    return hits[0] if hits else None


def main() -> None:
    report = []
    for subject, function, path_no in ROWS:
        src_subject = RQ3 / subject
        flat = next(p for p in src_subject.rglob("flat.sol") if p.is_file())
        # Keep the source identity visible in the generated test.  The test is
        # deliberately not presented as an executed concrete replay.
        flat_text = flat.read_text(errors="replace")
        contract = subject.split("__")[-1]
        test_name = f"{contract}CovTest_{contract}_{function}_reconstructed_from_rq3.t.sol"
        wrapper = existing_wrapper(subject, function)
        if wrapper:
            test_text = ("// RECONSTRUCTED-FROM-RQ3-FLAT-TEMPLATE: target wrapper copied "
                         "from existing RQ1 artifact; no new Forge run.\n" +
                         wrapper.read_text(errors="replace"))
        else:
            test_text = f"""// SPDX-License-Identifier: MIT
// Reconstructed from RQ3 flat source; no ESBMC/Forge run was performed.
// Frozen claim: sol:@C@{contract}@F@{function}#0:path:{path_no}
// This artifact is source materialization only and must not receive PUT credit.
pragma solidity >=0.8.0;

import {{ {contract} }} from \"../src/flat.sol\";

contract {contract}CovTest_{contract}_{function}_reconstructed_from_rq3 {{
  // The RQ3 flat source is retained verbatim under src/flat.sol.  The
  // original RQ3 concrete test was absent, so this row is not exact.
  function test_reconstructed_from_rq3() public {{
    // Claim target: {function}; replay intentionally not asserted/executed.
  }}
}}
"""
        digest = hashlib.sha256((flat_text + test_text).encode()).hexdigest()[:32]
        dst = RQ1 / "real203" / "subjects" / subject / "put" / function / "rq3-mechanical" / digest
        (dst / "src").mkdir(parents=True, exist_ok=True)
        (dst / "test").mkdir(parents=True, exist_ok=True)
        (dst / "src" / "flat.sol").write_text(flat_text)
        test_path = dst / "test" / test_name
        test_path.write_text(test_text)
        result_path = dst.parent.parent.parent.parent / "result.json"
        # dst = subject/put/function/rq3-mechanical/digest
        result_path = RQ1 / "real203" / "subjects" / subject / "result.json"
        result = json.loads(result_path.read_text())
        frozen = [f"real203/{subject}", f"sol:@C@{contract}@F@{function}#{'2289' if function == 'supportsInterface' else '0'}", function, path_no, ""]
        # Match the existing frozen identity by function and path; preserve its
        # exact selector number when this is a storage/function claim.
        entry = next(e for e in result.get("rq3_mechanical_closure", [])
                     if e.get("frozen_identity", [None, None, None, None])[2:4] == [function, path_no])
        entry.update({
            "binding_status": "reconstructed",
            "binding_tier": "reconstructed-from-rq3-flat-template",
            "candidate_count": 0,
            "forge_run": False,
            "put_credit": False,
            "source": str(test_path),
            "source_sha256": sha(test_path),
            "rq3_identity": None,
            "status": "reconstructed-from-RQ3",
            "test": "test_reconstructed_from_rq3",
            "reconstruction_flat_source": str(dst / "src" / "flat.sol"),
            "reconstruction_flat_sha256": sha(dst / "src" / "flat.sol"),
        })
        if wrapper:
            entry["template_source"] = str(wrapper)
            entry["template_source_sha256"] = sha(wrapper)
        result_path.write_text(json.dumps(result, indent=2) + "\n")
        report.append({"subject": subject, "function": function, "path": path_no,
                       "test": str(test_path), "flat": str(dst / "src" / "flat.sol")})
    out = Path("/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_NoPUT600/adoption-bundles/rq3-mechanical-match-20260815/real203-reconstructed-from-rq3-v1.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "rq3-reconstructed-source/v1", "rows": report}, indent=2) + "\n")
    print(json.dumps({"rows": len(report), "report": str(out)}))


if __name__ == "__main__":
    main()
