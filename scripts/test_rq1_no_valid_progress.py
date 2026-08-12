#!/usr/bin/env python3

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROGRESS = REPO / "notes" / "coverage" / "scripts" / "rq1_no_valid_progress.py"

sys.path.insert(0, str(PROGRESS.parent))

spec = importlib.util.spec_from_file_location("rq1_no_valid_progress", PROGRESS)
progress = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = progress
spec.loader.exec_module(progress)


def check(name, got, want):
    if got == want:
        print(f"ok - {name}")
        return 0
    print(f"not ok - {name}: got {got!r}, want {want!r}")
    return 1


def main():
    bad = 0
    bad += check(
        "invalid-detailed-test-overrides-row-scalar",
        progress._strength_counts({
            "valid":
            1,
            "put_valid":
            1,
            "valid_put_with_R1_or_R2":
            1,
            "valid_tests": [{
                "kind": "put",
                "valid_reference_test": False,
                "oracle_classes": ["R1"],
            }],
        }),
        (0, 0, 0),
    )
    bad += check(
        "deploy-only-detailed-test-overrides-put-scalar",
        progress._strength_counts({}, {
            "valid":
            1,
            "put_valid":
            1,
            "valid_put_with_R1_or_R2":
            1,
            "valid_tests": [{
                "kind": "put",
                "valid_reference_test": True,
                "stage4_kind": "deploy-only",
                "oracle_classes": ["R1"],
            }],
        }),
        (0, 0, 0),
    )
    for source in ("structural_deploy_only", "structural-deploy-only"):
        bad += check(
            f"{source}-detailed-test-is-not-valid",
            progress._strength_counts({
                "valid_tests": [{
                    "kind": "concrete",
                    "valid_reference_test": True,
                    "stage2_source": source,
                }],
            }),
            (0, 0, 0),
        )
    for kind in ("creation_code_only", "creation-code-only", "deploy_only"):
        bad += check(
            f"{kind}-detailed-test-is-not-valid",
            progress._strength_counts({
                "valid_tests": [{
                    "kind": "concrete",
                    "valid_reference_test": True,
                    "stage4_kind": kind,
                }],
            }),
            (0, 0, 0),
        )
    bad += check(
        "legacy-scalars-still-count-without-details",
        progress._strength_counts({
            "valid": 1,
            "put_valid": 0,
            "valid_put_with_R1_or_R2": 0,
        }),
        (1, 0, 0),
    )
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
