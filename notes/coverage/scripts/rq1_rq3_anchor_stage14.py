#!/usr/bin/env python3
"""Stage anchors for the fourteen RQ3-reconstructed RQ1 sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def span(source: str, name: str) -> tuple[int, int] | None:
    found = list(re.finditer(r"\bfunction\s+" + re.escape(name) + r"\s*\(", source))
    if len(found) != 1:
        return None
    start, opening = found[0].start(), source.find("{", found[0].end())
    if opening < 0:
        return None
    depth, quote, escaped, line_comment, block_comment = 0, None, False, False, False
    for i in range(opening, len(source)):
        c = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if line_comment:
            if c == "\n":
                line_comment = False
            continue
        if block_comment:
            if c == "*" and nxt == "/":
                block_comment = False
            continue
        if quote is None and c == "/" and nxt == "/":
            line_comment = True
            continue
        if quote is None and c == "/" and nxt == "*":
            block_comment = True
            continue
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    return None


def function_name(source: str) -> str | None:
    names = re.findall(r"\bfunction\s+(test[A-Za-z0-9_]*)\s*\(", source)
    return names[0] if len(names) == 1 else None


def fixed_function(function: str, name: str, anchor: str) -> tuple[str, list[str]]:
    """Turn bound parameters into their mechanically certified lower bounds."""
    signature = re.search(r"function\s+" + re.escape(name) + r"\s*\(([^)]*)\)", function)
    if not signature:
        raise ValueError("anchor signature is absent")
    params = []
    for raw in signature.group(1).split(","):
        bits = raw.strip().split()
        if len(bits) < 2:
            continue
        params.append((bits[-2], bits[-1]))
    declarations = []
    for typ, param in params:
        direct = re.search(r"\b" + re.escape(param) +
                           r"\s*=\s*bound\([^,]+,\s*(\d+),", function)
        address = re.search(r"\b" + re.escape(param) +
                            r"\s*=\s*address\(uint160\(bound\([^,]+,\s*(\d+),",
                            function)
        lower = (address or direct)
        if lower is None:
            raise ValueError(f"no bound lower bound for {param}")
        value = lower.group(1)
        if typ == "address":
            declarations.append(f"    address {param} = address(uint160({value}));")
        else:
            declarations.append(f"    {typ} {param} = {value};")
    tail = re.sub(r"^\s*public\s*\{", "", function[signature.end():], count=1)
    body = "\n".join(line for line in tail.splitlines()
                         if not any(re.search(r"\b" + re.escape(param) + r"\s*=", line)
                                    for _typ, param in params))
    head = f"function {anchor}() public {{\n" + "\n".join(declarations)
    return head + body, declarations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--staging", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    bundle = args.bundle
    bugfix = json.loads((bundle / "bugfix14-reconstruction-v1.json").read_text())
    peer = json.loads((bundle / "peer182-two-repair-v1.json").read_text())
    real = json.loads((bundle / "real203-reconstructed-from-rq3-v1.json").read_text())
    rows = []
    for item in bugfix["rows"] + peer["rows"]:
        rows.append({"identity": item["identity"], "source": item["source"],
                     "provenance": item.get("source_provenance") or item.get("source_origin")})
    real_ids = {
        ("ERC-3643__ERC-3643__IdentityRegistry", "deleteIdentity"): ["14"],
        ("ERC-3643__ERC-3643__Token", "increaseAllowance"): ["15"],
        ("ProjectOpenSea__seaport__SeaportNavigator", "helpers"): ["1"],
        ("balancer__balancer-v3-monorepo__ClaimSignatureRegistry", "signatures"): ["1"],
        ("compound-finance__comet__OnChainLiquidator", "poolConfigs"): ["1"],
        ("ensdomains__ens-contracts__DefaultReverseRegistrar", "renounceOwnership"): ["7"],
        ("ensdomains__ens-contracts__PublicResolver", "supportsInterface"): ["2"],
        ("euler-xyz__euler-vault-kit__ESynth", "minters"): ["1"],
    }
    for item in real["rows"]:
        subject, unit = item["subject"], item["function"]
        contract = subject.split("__")[-1]
        path = real_ids[(subject, unit)][0]
        claim = (f"sol:@C@{contract}@F@{unit}#{ {'deleteIdentity':'775','increaseAllowance':'999','renounceOwnership':'144','supportsInterface':'2289'}.get(unit, '0') }"
                 if unit in {"deleteIdentity", "increaseAllowance", "renounceOwnership", "supportsInterface"}
                 else f"sol:{contract}.{unit}#0")
        # Contract-scope storage identities use sol:Contract.field#0.
        if unit in {"helpers", "signatures", "poolConfigs", "minters"}:
            claim = f"sol:{contract}.{unit}#0"
        rows.append({"identity": ["real203/" + subject, claim, unit, path, ""],
                     "source": item["test"], "provenance": "reconstructed-from-rq3"})
    if len(rows) != 14:
        raise SystemExit(f"expected 14 rows, got {len(rows)}")
    if args.staging.exists():
        shutil.rmtree(args.staging)
    report_rows = []
    for row in rows:
        identity, source_path = row["identity"], Path(row["source"])
        out = {"identity": identity, "source": str(source_path),
               "provenance": row["provenance"]}
        if not source_path.is_file():
            out.update(status="refused", reason="source absent")
            report_rows.append(out); continue
        original = source_path.read_text(encoding="utf-8")
        name = function_name(original)
        location = span(original, name) if name else None
        if location is None:
            out.update(status="refused", reason="test function absent or ambiguous")
            report_rows.append(out); continue
        anchor = "test_ce_anchor_rq3_" + sha("\0".join(identity))[:16]
        fn = original[location[0]:location[1]]
        renamed = re.sub(r"(\bfunction\s+)" + re.escape(name) + r"(\s*\()",
                         r"\1" + anchor + r"\2", fn, count=1)
        parameterized = bool(re.search(r"function\s+[^\(]+\([^)]*\b(?:address|uint)\s+p?_?[A-Za-z]",
                                       fn)) or "function test_put" in fn
        if parameterized:
            try:
                renamed, declarations = fixed_function(fn, name, anchor)
            except ValueError as exc:
                out.update(status="refused", reason=str(exc))
                report_rows.append(out); continue
        else:
            declarations = []
        relative = Path(*source_path.parts[source_path.parts.index("VeriPUT") + 1:])
        target = args.staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = original[:location[1]] + "\n\n  // RQ1/RQ3 mechanical anchor.\n  " + renamed + original[location[1]:]
        target.write_text(staged, encoding="utf-8")
        out.update(status="staged", test=name, anchor_test=anchor,
                   source_sha256=sha(original), anchor_body_sha256=sha(renamed),
                   anchor_body=renamed, fixed_parameters=declarations,
                   staged_source=str(target),
                   staged_sha256=sha(staged), binding="rq3-identical-test/v1",
                   forge_run=False, put_credit=False)
        # Locate a same-flat, non-mechanical Foundry project for later isolated
        # PUT/anchor validation.  This is metadata only; no project is changed.
        flat = source_path.parent.parent / "src" / "flat.sol"
        if flat.is_file():
            flat_hash = sha(flat.read_text(encoding="utf-8"))
            marker = source_path.parts.index("subjects")
            subject_root = Path(*source_path.parts[:marker + 2])
            projects = []
            for candidate in subject_root.rglob("flat.sol"):
                if not candidate.is_file():
                    continue
                if candidate == flat or sha(candidate.read_text(encoding="utf-8")) != flat_hash:
                    continue
                project = candidate.parent.parent
                if (project / "foundry.toml").is_file() and "rq3-mechanical" not in str(project):
                    projects.append(str(project))
            out["project_candidates"] = sorted(set(projects))
            preferred = [p for p in out["project_candidates"]
                         if (f"/projects/{identity[2]}-" in p and
                             (Path(p) / "lib" / "forge-std" / "src" / "Test.sol").is_file() and
                             "Vm" in (Path(p) / "lib" / "forge-std" / "src" / "Test.sol").read_text(errors="ignore"))]
            preferred += [p for p in out["project_candidates"]
                          if f"/put/{identity[2]}/" in p and "certify" in p]
            out["project_selected"] = (preferred or out["project_candidates"] or [None])[0]
        report_rows.append(out)
    report = {"schema": "rq1-rq3-anchor-staging14/v1", "rows": report_rows,
              "counts": {"total": 14,
                         "staged": sum(x["status"] == "staged" for x in report_rows),
                         "refused": sum(x["status"] != "staged" for x in report_rows)},
              "policy": "staging only; no canonical writes, Forge, or PUT credit"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if report["counts"]["refused"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
