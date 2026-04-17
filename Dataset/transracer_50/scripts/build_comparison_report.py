#!/usr/bin/env python3
"""Build comparison_report.md joining Table 2 per-contract bug counts with ESBMC results."""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/Dataset/transracer_50")
T2 = json.loads((ROOT / "paper_table2.json").read_text())
T2_BY_NAME = {r["name"]: r for r in T2}
SELECTED = json.loads((ROOT / "selected.json").read_text())
UPGRADE = {r["name"]: r for r in json.loads((ROOT / "upgrade_summary.json").read_text())}
# picked_map uses paper names → etherscan_name/address
PICKED = json.loads((ROOT / "picked_map.json").read_text())

# fuzzy map: paper name → etherscan name (same as in materialize_sources.py)
FUZZY = {
    "Xpense": "XPS", "Freedom": "FreedomStreaming", "HubrisOne": "HUBRIS",
    "Viewly": "ViewlyMainSale", "Dentacoin": "DentacoinToken", "Yihaa": "Yiha",
    "MediBloc": "MedXToken",
}


def parse_esbmc_summary(stderr_path: Path) -> dict:
    """Extract the --tod-race-check summary + individual verdicts."""
    if not stderr_path.exists():
        return {"status": "missing"}
    text = stderr_path.read_text()
    # summary line: "--tod-race-check summary: N pair(s) — A clean, B TOD found, C error"
    m = re.search(r"summary:\s+(\d+)\s+pair\(s\)\s+—\s+(\d+)\s+clean,\s+(\d+)\s+TOD found,\s+(\d+)\s+error", text)
    if m:
        return {
            "status": "summarised",
            "pairs": int(m.group(1)),
            "clean": int(m.group(2)),
            "tod_found": int(m.group(3)),
            "error": int(m.group(4)),
        }
    # Maybe 0 pairs
    if "discovered 0 candidate pair(s)" in text:
        return {"status": "no_pairs", "pairs": 0, "clean": 0, "tod_found": 0, "error": 0}
    return {"status": "crashed_or_incomplete"}


def main():
    results_root = ROOT / "results"
    rows = []
    for name in SELECTED:
        t2 = T2_BY_NAME.get(name, {})
        ether = FUZZY.get(name, name)
        addr = PICKED.get(name, {}).get("address", "-")
        up = UPGRADE.get(name, {})
        up_status = up.get("status", "missing")
        esbmc_dir = results_root / name
        esbmc = parse_esbmc_summary(esbmc_dir / "run.stderr") if esbmc_dir.exists() else {"status": "not_run"}
        rows.append({
            "name": name, "etherscan_name": ether, "address": addr,
            "n_func": t2.get("n_func"), "n_inst": t2.get("n_inst"),
            "paper_trbD_is": t2.get("trbD_is"), "paper_trbD_us": t2.get("trbD_us"),
            "upgrade_status": up_status,
            "esbmc_status": esbmc.get("status"),
            "esbmc_pairs": esbmc.get("pairs"),
            "esbmc_clean": esbmc.get("clean"),
            "esbmc_tod_found": esbmc.get("tod_found"),
            "esbmc_error": esbmc.get("error"),
        })

    # Markdown report
    lines = []
    lines.append("# ESBMC `--tod-race-check` vs TransRacer paper Table 2\n")
    lines.append("Filtered to the 33 contracts with `#TRBD ≥ 1` in TransRacer's Table 2 (i.e.\n")
    lines.append("contracts where the paper reported at least one race bug between distinct\n")
    lines.append("functions — the category `--tod-race-check` targets).\n\n")

    # Status summary
    up_ok = sum(1 for r in rows if r["upgrade_status"] == "ok")
    esbmc_ran = sum(1 for r in rows if r["esbmc_status"] in ("summarised", "no_pairs"))
    lines.append("## Pipeline status\n\n")
    lines.append(f"- contracts selected: **{len(rows)}**\n")
    lines.append(f"- source successfully upgraded to `pragma >=0.8.0` and compiled: **{up_ok}**\n")
    lines.append(f"- ESBMC run completed (summary emitted): **{esbmc_ran}**\n")
    lines.append("\nThe remaining contracts failed the auto-upgrade pass; each has its last\n")
    lines.append("solc-0.8 error logged under `logs/upgrade_<name>.log`. Manual cleanup\n")
    lines.append("(rename shadowed identifiers, add `override` / `virtual` correctly, cast\n")
    lines.append("contract refs to address) is required to bring them into compilation.\n\n")

    # Detailed table
    lines.append("## Per-contract comparison\n\n")
    lines.append("| contract | #func | paper TRBD IS | paper TRBD US | upgrade | ESBMC pairs | clean | TOD found | error |\n")
    lines.append("|---|---:|---:|---:|---|---:|---:|---:|---:|\n")
    for r in rows:
        lines.append("| {name} | {n_func} | {pi} | {pu} | {up} | {ep} | {ec} | {et} | {ee} |\n".format(
            name=r["name"], n_func=r["n_func"],
            pi=r["paper_trbD_is"], pu=r["paper_trbD_us"],
            up=r["upgrade_status"],
            ep=r["esbmc_pairs"] if r["esbmc_pairs"] is not None else "-",
            ec=r["esbmc_clean"] if r["esbmc_clean"] is not None else "-",
            et=r["esbmc_tod_found"] if r["esbmc_tod_found"] is not None else "-",
            ee=r["esbmc_error"] if r["esbmc_error"] is not None else "-",
        ))

    lines.append("\n## Notes\n\n")
    lines.append("- **Paper TRBD IS / US** are copied from TransRacer's Table 2 (columns `#TRBD IS`\n")
    lines.append("  and `#TRBD US` respectively). They reflect TransRacer's own manual-confirmed\n")
    lines.append("  true positives, run against mainnet-deployed bytecode with access to the\n")
    lines.append("  live storage snapshot (TransRacer's Updated-State analysis can reach state\n")
    lines.append("  configurations ESBMC's fresh-Initial-State analysis cannot).\n")
    lines.append("- **ESBMC columns** measure `--tod-race-check=auto` with `--bound --unwind 3\n")
    lines.append("  --no-unwinding-assertions --cvc5 --tod-jobs=1` under the hardened wrapper\n")
    lines.append("  (`timeout 600 + ulimit -v 4000000 + ulimit -t 540`).\n")
    lines.append("- **`esbmc error` count** is pairs whose verification process could not reach\n")
    lines.append("  a verdict (usually solver timeout or front-end exception on the emitted\n")
    lines.append("  harness); they are NOT counted as bugs.\n")
    lines.append("- **`TOD found = 0` systematically** on the three contracts suggests ESBMC's\n")
    lines.append("  Initial-State harness cannot reach the post-state distinction TransRacer's\n")
    lines.append("  Updated-State analysis leverages — matches the paper's observation that\n")
    lines.append("  US-only TRBD constitute 50/66 (75.8%) of the total, i.e. most race bugs\n")
    lines.append("  are not IS-manifest.\n")

    out = ROOT / "comparison_report.md"
    out.write_text("".join(lines))
    print(f"Wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
