#!/usr/bin/env python3
"""Pure-function regression for salvaging path coverage CE journals."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes/coverage/scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

from pathcov_collect import report_from_ce_journal  # noqa: E402
from solidity_path_generalise import claim_unit, coord_values  # noqa: E402


def main():
    journal = {
        "kind": "solidity-complete-path-ce-journal",
        "claims_decided": 3,
        "claims_total": 5,
        "witnesses": {
            "p12": {
                "claim":
                "sol:@C@C@F@setDistributor#5926:path:12",
                "compact_trace":
                False,
                "dropped_internal":
                7,
                "entry_storage": [
                    {
                        "name": "_owner",
                        "value": "1"
                    },
                    {
                        "name": "_farm",
                        "value": "{ .finished = 0 }"
                    },
                ],
                "env": [
                    {
                        "name": "msg_sender",
                        "value": "0x2"
                    },
                    {
                        "name": "block_timestamp",
                        "value": "3"
                    },
                ],
                "extcall_returns": [{
                    "name": "return_value$__msgSender$2",
                    "value": "0x2"
                }],
                "final_state": [{
                    "name": "_owner",
                    "value": "1"
                }],
                "inputs": [{
                    "name": "distributor_",
                    "value": "9"
                }],
                "payload_symbols_protected":
                True,
                "return_value_known":
                False,
                "revert_pre_rollback":
                False,
                "scoped_to_claim":
                True,
                "sliced":
                True,
                "state_written_unrendered": ["_farm"],
                "witness_count":
                2,
                "witnesses": [{
                    "entry_storage": [{
                        "name": "_owner",
                        "value": "1"
                    }],
                    "env": [{
                        "name": "msg_sender",
                        "value": "0x3"
                    }],
                    "inputs": [{
                        "name": "distributor_",
                        "value": "10"
                    }],
                    "return_value_known": False,
                }],
            },
            "p2": {
                "claim": "sol:@C@C@F@setDistributor#5926:path:2",
                "entry_storage": [{
                    "name": "_owner",
                    "value": "1"
                }],
                "env": [{
                    "name": "msg_value",
                    "value": "1"
                }],
                "inputs": [{
                    "name": "distributor_",
                    "value": "0"
                }],
                "path_depth": 1,
                "return_value_known": False,
            },
        },
    }

    report = report_from_ce_journal(journal, "")
    assert report is not None
    assert report["partial"] is True
    assert report["summary"]["F_feasible_with_ce"] == 2
    assert report["summary"]["U_undecided"] == 3
    assert report["veriput_salvage"]["claims_total"] == 5

    claims = sorted(report["claims"], key=lambda c: int(c["path_id"]))
    assert [c["path_depth"] for c in claims] == [1, 3]
    assert {claim_unit(c) for c in claims} == {"setDistributor"}

    ce, refused = coord_values(claims[1], state_structs=True)
    assert refused == ["state._farm (aggregate; 1 scalar field(s) used instead: finished)"]
    assert ce["msg.sender"] == 2
    assert ce["block.timestamp"] == 3
    assert ce["distributor_"] == 9
    assert ce["state._owner"] == 1
    assert ce["state._farm.finished"] == 0
    assert claims[1]["extcall_returns"][0]["symbol"] == \
        "return_value$__msgSender$2"
    assert claims[1]["witnesses"][0]["env"]["msg.sender"] == "0x3"


if __name__ == "__main__":
    main()
