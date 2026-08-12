#!/usr/bin/env python3
"""Audit canonical RQ1 valid evidence for tuple-frontend false success."""

import argparse
import json
import re
from pathlib import Path


SCHEMA = "veriput-rq1-tuple-frontend-pollution-audit/v1"
TUPLE_ERRORS = (
    "Unexpected tuple",
    "expecting struct type for tuple RHS",
    "unexpected address member access, got Tuple",
    "Tuple AST mismatch",
    "tuple assignment: cannot resolve RHS tuple function",
)
FUNCTION_POINTER_TUPLE_DECL = re.compile(
    r"function\s*\([^)]*\)\s*(?:internal|external)\b[^;={}]*"
    r"returns\s*\([^)]*,[^)]*\)\s+[A-Za-z_$][A-Za-z0-9_$]*",
    re.DOTALL,
)


def walk_json(value, ancestors=()):
    """Yield every JSON object together with its containing objects."""
    if isinstance(value, dict):
        yield value, ancestors
        nested_ancestors = ancestors + (value, )
        for child in value.values():
            yield from walk_json(child, nested_ancestors)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child, ancestors)


def schedule_subject(case_dir):
    """Return the prepared subject retained by a canonical unit schedule."""
    schedule = case_dir / "unit-schedule.json"
    if not schedule.is_file():
        return {}
    data = json.loads(schedule.read_text(encoding="utf-8"))
    for job in data.get("jobs", []):
        subject = job.get("subject")
        if isinstance(subject, dict):
            return subject
    return {}


def function_pointer_tuple_calls(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        ast_path, target_contract_name, valid_units):
    """Find tuple-returning calls through function-typed variables."""
    with ast_path.open(encoding="utf-8") as ast_file:
        ast_line = next((line for line in ast_file if line.lstrip().startswith("{")),
                        None)
    if ast_line is None:
        raise json.JSONDecodeError("compact AST contains no JSON object", "", 0)
    ast = json.loads(ast_line)
    nodes = list(walk_json(ast))
    by_id = {
        node["id"]: node
        for node, _ in nodes
        if isinstance(node.get("id"), int)
    }
    calls = []
    call_edges = {}
    function_owners = {}
    contracts = {
        node["id"]: node
        for node, _ in nodes
        if node.get("nodeType") == "ContractDefinition" and
        isinstance(node.get("id"), int)
    }
    for node, ancestors in nodes:
        if node.get("nodeType") != "FunctionDefinition":
            continue
        owner = next((item for item in reversed(ancestors)
                      if item.get("nodeType") == "ContractDefinition"), None)
        if owner is not None:
            function_owners[node["id"]] = owner["id"]
        call_edges.setdefault(node["id"], set())

    for node, ancestors in nodes:
        if node.get("nodeType") != "FunctionCall":
            continue
        function = next((item for item in reversed(ancestors)
                         if item.get("nodeType") == "FunctionDefinition"), {})
        caller_id = function.get("id")
        type_desc = node.get("typeDescriptions") or {}
        type_id = str(type_desc.get("typeIdentifier", ""))
        type_string = str(type_desc.get("typeString", ""))
        if not (type_id.startswith("t_tuple") or
                type_string.startswith("tuple")):
            continue

        callee = node.get("expression") or {}
        while isinstance(callee, dict) and callee.get("nodeType") in (
                "FunctionCall", "FunctionCallOptions"):
            callee = callee.get("expression") or {}
        callee_id = callee.get("referencedDeclaration")
        if caller_id in call_edges and callee_id in function_owners:
            call_edges[caller_id].add(callee_id)
        ref = by_id.get(callee_id, {})
        ref_type = str((ref.get("typeDescriptions") or {}).get(
            "typeIdentifier", ""))
        type_name = ref.get("typeName") or {}
        if not (ref.get("nodeType") == "VariableDeclaration" and
                (type_name.get("nodeType") == "FunctionTypeName" or
                 ref_type.startswith("t_function"))):
            continue

        contract = next((item for item in reversed(ancestors)
                         if item.get("nodeType") == "ContractDefinition"), {})
        calls.append({
            "contract": contract.get("name"),
            "caller": function.get("name") or "<constructor>",
            "caller_id": function.get("id"),
            "callee_variable": ref.get("name"),
            "callee_variable_id": ref.get("id"),
            "source_range": node.get("src"),
            "tuple_type": type_string,
        })

    target_contract = next((contract for contract in contracts.values()
                            if contract.get("name") == target_contract_name), {})
    target_contract_ids = set(target_contract.get("linearizedBaseContracts", []))
    entrypoints = []
    for function_id, contract_id in function_owners.items():
        function = by_id[function_id]
        if contract_id not in target_contract_ids:
            continue
        if function.get("kind") == "constructor" or function.get(
                "name") in valid_units:
            entrypoints.append(function_id)
    reachable = set(entrypoints)
    pending = list(entrypoints)
    while pending:
        for callee_id in call_edges.get(pending.pop(), set()):
            if callee_id not in reachable:
                reachable.add(callee_id)
                pending.append(callee_id)
    entrypoint_names = sorted({
        (by_id[function_id].get("name") or "<constructor>")
        for function_id in entrypoints
    })
    for call in calls:
        call["valid_evidence_entrypoints"] = entrypoint_names
        call["valid_unit_or_constructor_reachable"] = (
            call["caller_id"] in reachable)
    return calls


def log_tuple_errors(path):
    """Return tuple diagnostics and whether the same log reports success."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], False
    errors = [error for error in TUPLE_ERRORS if error.lower() in text.lower()]
    return errors, "VERIFICATION SUCCESSFUL" in text


def evidence_logs(test):
    """Locate verifier logs belonging to one retained valid test."""
    put_json = test.get("put_json")
    if not put_json:
        return []
    root = Path(put_json).parent
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.log"))


def fallback_source_scan(case_dir):
    """Conservatively scan a case whose retained schedule has no AST."""
    candidates = list(case_dir.glob("put/**/src/flat.sol"))
    if not candidates:
        candidates = list(case_dir.parent.glob(
            f"{case_dir.name}.*/put/**/src/flat.sol"))
    if not candidates:
        return {"source": None, "function_pointer_tuple_declarations": []}
    source = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    text = source.read_text(encoding="utf-8", errors="replace")
    declarations = []
    for match in FUNCTION_POINTER_TUPLE_DECL.finditer(text):
        declarations.append({
            "offset": match.start(),
            "text": " ".join(match.group(0).split()),
        })
    return {
        "source": str(source),
        "function_pointer_tuple_declarations": declarations,
    }


def audit(state_path):  # pylint: disable=too-many-locals
    """Audit exactly the fixed canonical inventory in ``state_path``."""
    state = json.loads(state_path.read_text(encoding="utf-8"))
    cases = state.get("cases", {})
    if len(cases) != 205:
        raise SystemExit(
            f"refusing non-canonical inventory: expected 205 cases, got {len(cases)}")

    ast_audited = 0
    ast_missing = []
    ast_invalid = []
    fallback_source_scans = []
    pointer_calls = []
    affected_tests = []
    all_valid_tests = 0
    for key, case in sorted(cases.items()):
        case_dir = Path(case["last_result_subject_dir"])
        subject = schedule_subject(case_dir)
        ast_path_text = subject.get("solast")
        ast_path = Path(ast_path_text) if ast_path_text else None
        result = json.loads(
            Path(case["last_result_json"]).read_text(encoding="utf-8"))
        row = result.get("row", {})
        valid_units = {
            test.get("unit")
            for test in row.get("valid_tests", [])
            if test.get("unit")
        }
        if ast_path and ast_path.is_file():
            try:
                calls = function_pointer_tuple_calls(
                    ast_path, subject.get("contract"), valid_units)
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                ast_invalid.append({
                    "case": key,
                    "ast": str(ast_path),
                    "error": f"{type(error).__name__}: {error}",
                })
                calls = None
            if calls is not None:
                ast_audited += 1
            for call in calls or []:
                pointer_calls.append({
                    "case": key,
                    "ast": str(ast_path),
                    **call,
                })
        else:
            ast_missing.append(key)
            fallback_source_scans.append({
                "case": key,
                **fallback_source_scan(case_dir),
            })

        for test in row.get("valid_tests", []):
            all_valid_tests += 1
            matching_logs = []
            error_names = set()
            for log in evidence_logs(test):
                errors, has_success = log_tuple_errors(log)
                if errors and has_success:
                    matching_logs.append(str(log))
                    error_names.update(errors)
            if matching_logs:
                affected_tests.append({
                    "case": key,
                    "state": case.get("state"),
                    "kind": test.get("kind"),
                    "unit": test.get("unit"),
                    "test": test.get("test"),
                    "test_file": test.get("file") or test.get("path"),
                    "put_json": test.get("put_json"),
                    "tuple_errors": sorted(error_names),
                    "error_success_logs": matching_logs,
                })

    affected_cases = sorted({test["case"] for test in affected_tests})
    return {
        "schema": SCHEMA,
        "inventory": {
            "state": str(state_path.resolve()),
            "cases": len(cases),
            "valid_tests_examined": all_valid_tests,
        },
        "function_pointer_tuple_call_audit": {
            "retained_asts_examined": ast_audited,
            "cases_without_retained_ast": ast_missing,
            "invalid_retained_asts": ast_invalid,
            "fallback_source_scans": fallback_source_scans,
            "fallback_sources_examined": sum(
                bool(scan["source"]) for scan in fallback_source_scans),
            "fallback_declaration_match_count": sum(
                len(scan["function_pointer_tuple_declarations"])
                for scan in fallback_source_scans),
            "matching_calls": pointer_calls,
            "matching_call_count": len(pointer_calls),
            "affected_case_count": len({call["case"] for call in pointer_calls}),
        },
        "error_then_success_evidence_audit": {
            "rule": (
                "A valid test is affected only when its own put_json work directory "
                "contains a log with both a known tuple frontend error and "
                "VERIFICATION SUCCESSFUL."
            ),
            "affected_cases": affected_cases,
            "affected_case_count": len(affected_cases),
            "affected_tests": affected_tests,
            "affected_test_count": len(affected_tests),
            "affected_put_test_count": sum(
                test["kind"] == "put" for test in affected_tests),
            "affected_concrete_test_count": sum(
                test["kind"] == "concrete" for test in affected_tests),
        },
    }


def main():
    """Render the audit as stable, machine-readable JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("notes/coverage/rq1_case_state.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.state)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
