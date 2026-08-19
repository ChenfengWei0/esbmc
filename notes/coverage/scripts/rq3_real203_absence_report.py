#!/usr/bin/env python3
"""Produce a read-only absence report for selected RQ3 identities."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


RQ3_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg")
TARGETS = [
    ("real203/ensdomains__ens-contracts__PublicResolver", "supportsInterface", "sol:@C@PublicResolver@F@supportsInterface#2289"),
    ("real203/balancer__balancer-v3-monorepo__ClaimSignatureRegistry", "signatures", "sol:ClaimSignatureRegistry.signatures#0"),
    ("real203/compound-finance__comet__OnChainLiquidator", "poolConfigs", "sol:OnChainLiquidator.poolConfigs#0"),
    ("real203/ERC-3643__ERC-3643__Token", "increaseAllowance", "sol:@C@Token@F@increaseAllowance#999"),
    ("real203/ProjectOpenSea__seaport__SeaportNavigator", "helpers", "sol:SeaportNavigator.helpers#0"),
    ("real203/euler-xyz__euler-vault-kit__ESynth", "minters", "sol:ESynth.minters#0"),
    ("real203/ensdomains__ens-contracts__DefaultReverseRegistrar", "renounceOwnership", "sol:@C@DefaultReverseRegistrar@F@renounceOwnership#144"),
    ("real203/ERC-3643__ERC-3643__IdentityRegistry", "deleteIdentity", "sol:@C@IdentityRegistry@F@deleteIdentity#775"),
]


def main():
    # Hash every regular file, including hidden/archive/shard trees, so an
    # absence result is tied to the exact RQ3 snapshot that was scanned.
    tree_hash = hashlib.sha256()
    tree_file_count = 0
    tree_t_sol_count = 0
    for root, dirs, files in os.walk(RQ3_ROOT.parent.parent):
        dirs.sort()
        for name in sorted(files):
            path = Path(root) / name
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, IsADirectoryError):
                continue
            rel = path.relative_to(RQ3_ROOT.parent.parent).as_posix()
            tree_hash.update(rel.encode())
            tree_hash.update(b"\0")
            tree_hash.update(digest.encode())
            tree_hash.update(b"\n")
            tree_file_count += 1
            if name.endswith(".t.sol"):
                tree_t_sol_count += 1
    rows = []
    for subject, unit, path_function in TARGETS:
        subject_dir = RQ3_ROOT / subject.split("/", 1)[0] / "subjects" / subject.split("/", 1)[1]
        unit_dirs = []
        source_files = []
        schedule_files = []
        if subject_dir.exists():
            for root, dirs, files in os.walk(subject_dir):
                root_path = Path(root)
                if "put" in root_path.parts and unit.lower() in root_path.name.lower():
                    unit_dirs.append(str(root_path))
                for name in files:
                    path = root_path / name
                    if name.endswith(".t.sol") and unit.lower() in name.lower():
                        source_files.append(str(path))
                    if name == "unit-schedule.json":
                        schedule_files.append(str(path))
        rows.append({
            "subject": subject,
            "unit": unit,
            "path_function": path_function,
            "subject_exists": subject_dir.exists(),
            "unit_put_directory_count": len(set(unit_dirs)),
            "matching_t_sol_count": len(source_files),
            "unit_schedule_count": len(schedule_files),
            "unit_schedule_paths": sorted(schedule_files),
            "status": "absent" if not unit_dirs and not source_files else "artifact-present",
        })
    report = {
        "schema": "rq3-real203-durable-absence/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "executed_esbmc": False,
        "executed_forge": False,
        "rq3_root": str(RQ3_ROOT),
        "scan_scope": ["all recursive files under RQ3 root", "subject put directories", "all recursive *.t.sol", "unit-schedule.json"],
        "tree_snapshot": {
            "root": str(RQ3_ROOT.parent.parent),
            "recursive_regular_file_count": tree_file_count,
            "recursive_t_sol_count": tree_t_sol_count,
            "path_content_sha256": tree_hash.hexdigest(),
        },
        "target_count": len(rows),
        "rows": rows,
    }
    encoded = json.dumps(report, sort_keys=True, indent=2).encode()
    report["report_sha256"] = hashlib.sha256(encoded).hexdigest()
    out = Path("/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_NoPUT600/adoption-bundles/rq3-mechanical-match-20260815/rq3-real203-absence-v1.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(out)
    print(report["report_sha256"])


if __name__ == "__main__":
    main()
