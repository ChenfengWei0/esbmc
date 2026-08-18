# No-Cer-Reg Experiment Runbook

This document is the handoff contract for deriving the RQ3 `no-cer-reg` arm.
The arm is derived from a completed Full VeriPUT corpus. It does not rerun
ESBMC and it must not consume the legacy `run_rq3_no_cer_reg.py` corpus.

## Repositories

Clone both repositories and check out the revisions named by the experiment
owner:

```sh
git clone git@github.com:ChenfengWei0/E-Solidity.git esbmc
git clone git@github.com:ChenfengWei0/VeriPUT.git VeriPUT
```

Set portable roots. No command below depends on a machine-specific absolute
path:

```sh
export ESBMC_REPO="$(cd esbmc && pwd)"
export VERIPUT_ROOT="$(cd VeriPUT && pwd)"
export WORKER_ID="worker-name"
```

The derivation tool is packaged at
`$VERIPUT_ROOT/Tools/VeriPUT/rq3_derive_from_full.py`. The identical development
copy is at `$ESBMC_REPO/notes/coverage/scripts/rq3_derive_from_full.py`.

`no-cer-reg` does not invoke the ESBMC binary, so no ESBMC build is required for
this derivation. For experiments that do invoke ESBMC (Full VeriPUT and
`no-selection-strategy`), use a `Release` build. `Debug`/`DebugOpt` builds are
for diagnosis only and must not be used for reported experiment timings.

## Input Contract

`FULL_ROOT` must be a completed, audited Full VeriPUT result root. It must
contain, for every selected strict-valid Full row:

- `result.json` with `valid_reference_test=true`;
- the referenced `put.json` and generated Foundry test;
- `<subject>/concrete-replays/manifest.json`;
- exactly one retained concrete basis with the same
  `(path_function, unit, enc, piece)` identity;
- matching test, `flat.sol`, and Forge-log SHA-256 values; and
- `forge_status=Success` for that concrete basis.

The derivation fails closed if any required basis is missing, duplicated, red,
or identity/hash mismatched. Do not use `--no-forge` for reported results.

## Preflight

```sh
cd "$ESBMC_REPO"
python3 notes/coverage/scripts/test_rq3_derive_from_full.py
python3 -m py_compile "$VERIPUT_ROOT/Tools/VeriPUT/rq3_derive_from_full.py"
cmp notes/coverage/scripts/rq3_derive_from_full.py \
  "$VERIPUT_ROOT/Tools/VeriPUT/rq3_derive_from_full.py"
forge --version
```

All commands must succeed before running the arm.

## Run

Each collaborator owns one output directory and one Git branch. Never write
directly into the canonical `Results/RQ3/VeriExploit/No_Cer_Reg` directory.

```sh
export FULL_ROOT="$VERIPUT_ROOT/Results/RQ1/VeriPUT"
export OUTPUT_ROOT="$VERIPUT_ROOT/Results/RQ3/No_Cer_Reg_workers/$WORKER_ID"

python3 "$VERIPUT_ROOT/Tools/VeriPUT/rq3_derive_from_full.py" \
  --full-root "$FULL_ROOT" \
  --out-root "$OUTPUT_ROOT" \
  --mode no-cer-reg \
  --forge-timeout 600
```

The tool replays each selected fixed test on the copied original `flat.sol`
with 10,000 Foundry runs and seed `0x56657269505554`, the ASCII encoding of
`VeriPUT`. It writes transactionally: an incomplete staging directory is not a
published result.

## Acceptance Checks

The output is acceptable only when all of the following hold:

```sh
python3 - "$OUTPUT_ROOT/manifest.json" <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text())
entries = manifest.get("entries") or []
assert manifest.get("mode") == "no-cer-reg"
assert manifest.get("test_units") == len(entries)
assert entries
assert all(row.get("forge", {}).get("status") == "Success" for row in entries)
assert all(row.get("origin", {}).get("replacement") == "retained-certified-ce"
           or row.get("origin", {}).get("kind") == "concrete" for row in entries)
print({"test_units": len(entries), "forge_success": len(entries)})
PY

rg -n "function test_put_" "$OUTPUT_ROOT/entries" && exit 1 || true
rg -n "function test_(cov|concrete|replay)" "$OUTPUT_ROOT/entries" >/dev/null
```

`no-cer-reg` contains fixed-input concrete replay tests and therefore must
contain zero PUT test units. Its assertions are witness-specific observations,
not region-wide k-induction claims. ESBMC supplies the Stage-2 witness/path
identity; Forge validates the fixed return/state/event/exit assertions against
the original target source.

## Conflict-Free Publication

Use a result-only branch. Do not edit tool code, Full inputs, or the canonical
No-Cer-Reg directory on this branch.

```sh
cd "$VERIPUT_ROOT"
git switch -c "data/no-cer-reg-$WORKER_ID"
git add "Results/RQ3/No_Cer_Reg_workers/$WORKER_ID"
git commit -m "[rq3] add no-cer-reg results for $WORKER_ID"
git push -u origin "data/no-cer-reg-$WORKER_ID"
```

Send the branch name and commit SHA to the experiment owner. The owner should
fetch and cherry-pick that commit, or restore only the worker directory from
the commit. Do not merge two workers into the same output directory. The final
canonical directory is assembled only after manifests, hashes, counts, and
Forge logs have been audited centrally.

## Portability Note

The scripts contain no hard-coded `/home/...` workspace path. Runtime manifests
may record resolved absolute input paths as provenance. Those fields do not
control execution after the self-contained Foundry project has been copied;
portable execution uses the relative `test_file`, `flat_source`, and log paths
plus their SHA-256 values.
