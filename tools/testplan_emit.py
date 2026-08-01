#!/usr/bin/env python3
"""TestPlan -> Foundry parameterized unit test.

WHY THIS EXISTS, stated so it is not re-litigated. The in-process emitter
(`src/goto-symex/foundry.cpp`) renders from a counterexample, and its data model
is

    test_case = std::vector<sol_call>
    sol_arg { param, sol_type, literal, value, ... }

which carries ONE FIXED LITERAL per argument and has no notion of an interval, a
puncture, or an assertion. A parameterized unit test is therefore not something
that emitter produces badly -- it is something its TYPE CANNOT EXPRESS. Measured
consequence: zero PUTs, the thing the project is named after.

This emitter consumes a TestPlan instead, which does carry those notions, and it
never sees a counterexample. That inversion is the point:

    witness -> region certification -> assertion certification -> TestPlan -> HERE

not

    witness -> HERE -> (certification, afterwards, in another process)

⛔ DESIGN RULES, each from a failure this project has already had:

  * FAIL LOUDLY, never silently. Anything the plan asks for that cannot be
    rendered faithfully raises and emits nothing. A renderer that quietly drops
    an assertion produces a green test that checks less than it claims, which is
    indistinguishable from success.
  * The plan's `manual` flag is COPIED INTO THE OUTPUT. A hand-made link in the
    chain must be visible in the artefact, or it gets counted as pipeline yield.
  * No contract-specific knowledge. Nothing here knows what FeeVault is. If a
    line of this file would have to change to serve a second contract, it is in
    the wrong file.

Usage:  python3 tools/testplan_emit.py <plan.json> [--contract-name Name] > Out.t.sol
"""
import json
import re
import sys

# Types whose values the emitter knows how to bound. Anything else is refused
# rather than guessed at: `bound()` is defined over uint256, and casting a type
# we have not thought about is how a silently-wrong region gets emitted.
_UINT = re.compile(r"^uint(\d+)?$")
_INT = re.compile(r"^int(\d+)?$")


class PlanError(Exception):
    pass


def _ident(s, what):
    if not isinstance(s, str) or not re.match(r"^[A-Za-z_]\w*$", s):
        raise PlanError(f"{what} must be a Solidity identifier, got {s!r}")
    return s


def _num(s, what):
    """A numeric literal, as a STRING. Never parsed into a Python int.

    256-bit bounds do not survive a round trip through anything narrower, and
    this project has already shipped one defect from a value that was rendered
    after being converted. The plan carries decimal or hex text; it is emitted
    verbatim after being checked to BE numeric text.
    """
    if isinstance(s, int):
        s = str(s)
    if not isinstance(s, str) or not re.match(r"^(0x[0-9a-fA-F]+|\d+)$", s):
        raise PlanError(f"{what} must be a decimal or 0x literal, got {s!r}")
    return s


def bound_stmt(p):
    """`x = bound(x, lo, hi)`, cast when the parameter is narrower than uint256."""
    name = _ident(p["name"], "params[].name")
    t = p["sol_type"]
    lo = _num(p["lo"], f"params[{name}].lo")
    hi = _num(p["hi"], f"params[{name}].hi")

    if t == "uint256":
        return f"        {name} = bound({name}, {lo}, {hi});"
    if _UINT.match(t):
        return f"        {name} = {t}(bound(uint256({name}), {lo}, {hi}));"
    if _INT.match(t):
        # `bound` has an int256 overload in forge-std; a narrower signed type
        # still needs the cast back.
        if t == "int256":
            return f"        {name} = bound({name}, {lo}, {hi});"
        return f"        {name} = {t}(bound(int256({name}), {lo}, {hi}));"
    raise PlanError(
        f"params[{name}]: cannot bound sol_type {t!r}. Refusing to emit rather "
        f"than guess a cast -- an unfaithful region is worse than no test.")


def hole_stmts(p):
    """Punctures. A region is an interval MINUS a finite set (paper, eq. region).

    Rendered as `vm.assume`, which is the one thing a framework must skip rather
    than assign, and it is bounded in proportion by construction: |H_c| against
    the width of the interval.
    """
    name = p["name"]
    out = []
    for h in p.get("holes") or []:
        out.append(f"        vm.assume({name} != {_num(h, f'holes of {name}')});")
    return out


def caller_expr(who, accounts):
    if who == "TEST_CONTRACT":
        return None                      # no prank: the test contract acts
    if who in accounts:
        return who
    raise PlanError(
        f"caller {who!r} is neither TEST_CONTRACT nor a declared account "
        f"({sorted(accounts)})")


def render_call(c, accounts, indent="        "):
    """One call, with its caller and value, optionally binding a return value."""
    fn = _ident(c["fn"], "call.fn")
    args = ", ".join(c.get("args") or [])
    val = c.get("value")
    valpart = f"{{value: {val}}}" if val not in (None, "0", 0) else ""
    who = caller_expr(c.get("caller", "TEST_CONTRACT"), accounts)

    lines = []
    if c.get("deal"):
        if who is None:
            raise PlanError("`deal` needs a named account, not TEST_CONTRACT")
        lines.append(f"{indent}vm.deal({who}, {c['deal']});")
    if who is not None:
        lines.append(f"{indent}vm.prank({who});")

    ret = c.get("returns")
    if ret:
        rt = c.get("returns_type")
        if not rt:
            raise PlanError("call.returns given without call.returns_type")
        lines.append(f"{indent}{rt} {_ident(ret, 'call.returns')} = "
                     f"v.{fn}{valpart}({args});")
    else:
        lines.append(f"{indent}v.{fn}{valpart}({args});")
    return lines


ASSERT_FN = {"eq": "assertEq", "lt": "assertLt", "gt": "assertGt",
             "le": "assertLe", "ge": "assertGe", "true": "assertTrue",
             "false": "assertFalse"}


def render_assertion(a):
    kind = a.get("kind")
    fn = ASSERT_FN.get(kind)
    if fn is None:
        raise PlanError(f"assertion kind {kind!r} unknown; known: "
                        f"{sorted(ASSERT_FN)}")
    msg = json.dumps(a.get("msg", ""))
    if kind in ("true", "false"):
        return f"        {fn}({a['lhs']}, {msg});"
    if "rhs" not in a:
        raise PlanError(f"assertion of kind {kind!r} needs an rhs")
    return f"        {fn}({a['lhs']}, {a['rhs']}, {msg});"


def emit(plan, contract_name=None):
    contract = _ident(plan["contract"], "contract")
    fixture = plan.get("fixture") or {}
    accounts = {a["name"]: a["addr"] for a in (fixture.get("accounts") or [])}
    for n in accounts:
        _ident(n, "fixture.accounts[].name")

    params = plan.get("params") or []
    if not params:
        raise PlanError(
            "a TestPlan with no params is a CONCRETE REPLAY TEST, not a "
            "parameterized unit test. Emit it through the replay path; this "
            "emitter exists for the parameterized one and must not silently "
            "produce something that looks like a PUT and is not.")

    manual = bool(plan.get("manual", False))
    test_contract = contract_name or (contract + "GenTest")

    L = []
    L.append("// SPDX-License-Identifier: MIT")
    L.append("pragma solidity ^0.8.0;")
    L.append("")
    L.append(f"// manual: {'true' if manual else 'false'}")
    L.append("//")
    L.append("// GENERATED by tools/testplan_emit.py from a TestPlan. Do not hand-edit:")
    L.append("// edit the plan and re-emit, or the artefact stops matching what was")
    L.append("// certified.")
    if manual:
        L.append("//")
        L.append("// ⚠ manual:true -- some link of the chain behind this plan was made by")
        L.append("//   hand. Per R7 this file MUST NOT be counted in any conversion rate.")
    L.append(f"// unit    : {plan.get('unit', '?')}")
    L.append(f"// path_id : {plan.get('path_id', '?')}")
    L.append("")
    L.append(f'import "forge-std/Test.sol";')
    L.append(f'import "{plan["import"]}";')
    L.append("")
    L.append(f"contract {test_contract} is Test {{")
    L.append(f"    {contract} v;")
    for n, addr in accounts.items():
        L.append(f"    address {n} = {addr};")
    L.append("")

    # ---- setUp: the fixture ------------------------------------------------
    ctor_args = ", ".join(fixture.get("constructor_args") or [])
    dep_val = fixture.get("value")
    depart = f"{{value: {dep_val}}}" if dep_val not in (None, "0", 0) else ""
    L.append("    function setUp() public {")
    L.append(f"        v = new {contract}{depart}({ctor_args});")
    L.append("    }")
    L.append("")
    L.append("    receive() external payable {}")
    L.append("")

    # ---- the test function -------------------------------------------------
    sig = ", ".join(f"{p['sol_type']} {p['name']}" for p in params)
    fname = "test_" + re.sub(r"\W+", "_", str(plan.get("path_id", "path")))
    L.append(f"    function {fname}({sig}) public {{")
    L.append("        // The certified region: an interval per coordinate, minus punctures.")
    for p in params:
        L.append(bound_stmt(p))
        L.extend(hole_stmts(p))
    L.append("")

    if plan.get("pre_state"):
        L.append("        // Pre-state the path requires.")
        for ps in plan["pre_state"]:
            via = ps.get("via")
            if not via:
                raise PlanError(
                    f"pre_state {ps.get('accessor')!r} has no `via`: the plan "
                    f"says WHAT the state must be but not HOW the test "
                    f"establishes it, and a renderer must not invent that.")
            if via.get("kind") != "call":
                raise PlanError(
                    f"pre_state via kind {via.get('kind')!r} not supported yet "
                    f"(only 'call'). A 'store' kind belongs with the mutable-slot "
                    f"coordinate work; refusing rather than approximating.")
            L.append(f"        // {ps['accessor']} = {ps['value']}")
            L.extend(render_call(via, accounts))
        L.append("")

    L.append("        // The call under test.")
    L.extend(render_call(plan["call"], accounts))
    L.append("")

    if plan.get("locals"):
        for lv in plan["locals"]:
            L.append(f"        {lv['sol_type']} {_ident(lv['name'], 'locals[].name')}"
                     f" = {lv['expr']};")
        L.append("")

    if not plan.get("assertions"):
        raise PlanError(
            "a TestPlan with no assertions is a coverage replay, not a test "
            "with an oracle. The deliverable is defined as carrying at least "
            "one; refusing to emit an assertion-free file that would pass.")
    L.append("        // Oracles, proved over the region.")
    for a in plan["assertions"]:
        L.append(render_assertion(a))

    L.append("    }")
    L.append("}")
    return "\n".join(L) + "\n"


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    name = None
    if "--contract-name" in argv:
        name = argv[argv.index("--contract-name") + 1]
    with open(argv[1]) as fh:
        plan = json.load(fh)
    try:
        sys.stdout.write(emit(plan, name))
    except PlanError as e:
        sys.stderr.write(f"testplan_emit: REFUSED -- {e}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
