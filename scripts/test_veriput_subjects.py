#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

from veriput_subjects import (SubjectError, resolve_subject,  # noqa: E402
                              subject_from_record)


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def make_subject(root, sid="repo__C", **meta_overrides):
    d = Path(root) / sid
    d.mkdir(parents=True)
    (d / "flat.sol").write_text("contract C { function f() public {} }\n")
    (d / "flat.sol.solast").write_text("{}\n")
    meta = {
        "subject_id": sid,
        "benchmark": "stress243",
        "contract": "C",
        "status": "ok",
        "solc_bin": "/bin/false",
    }
    meta.update(meta_overrides)
    (d / "meta.json").write_text(json.dumps(meta) + "\n")
    return d


def test_resolve_subject_from_root_and_unit():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, "repo__C")
        subject = resolve_subject("repo__C", root=td, unit="f")
    bad = 0
    bad += check(subject.benchmark == "stress243",
                 f"benchmark is read from meta.json: {subject.benchmark}")
    bad += check(subject.subject_id == "repo__C",
                 f"subject id is stable: {subject.subject_id}")
    bad += check(subject.contract == "C" and subject.unit == "f",
                 f"contract/unit resolved: {subject.contract}.{subject.unit}")
    bad += check(subject.flat_sol.endswith("/repo__C/flat.sol"),
                 f"flat path points at prepared source: {subject.flat_sol}")
    bad += check(subject.solast.endswith("/repo__C/flat.sol.solast"),
                 f"AST path uses prepared flat naming: {subject.solast}")
    return bad


def test_resolve_subject_requires_explicit_unit():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td)
        try:
            resolve_subject("repo__C", root=td)
        except SubjectError as exc:
            return check("explicit --unit" in str(exc),
                         f"missing unit fails closed: {exc}")
    print("FAIL: missing unit was accepted")
    return 1


def test_subject_from_cert_record_round_trips():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td)
        subject = resolve_subject("repo__C", root=td, unit="f")
        row = {"subject": subject.to_record()}
        restored = subject_from_record(row)
    bad = 0
    bad += check(restored is not None, "subject block is recognized")
    bad += check(restored.to_record() == subject.to_record(),
                 "cert row subject block round-trips")
    return bad


def test_bad_status_is_not_usable():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, status="compile-failed")
        try:
            resolve_subject("repo__C", root=td, unit="f")
        except SubjectError as exc:
            return check("status='compile-failed'" in str(exc),
                         f"bad prepared subject status refused: {exc}")
    print("FAIL: compile-failed subject was accepted")
    return 1


def main():
    tests = [
        test_resolve_subject_from_root_and_unit,
        test_resolve_subject_requires_explicit_unit,
        test_subject_from_cert_record_round_trips,
        test_bad_status_is_not_usable,
    ]
    bad = 0
    for test in tests:
        print("---", test.__name__)
        bad += test()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
