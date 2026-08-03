"""Both directions of --state-struct-fields, against the REAL functions.

The OFF direction is the one that matters most here: this flag changes the
coordinate set, so if the default moved, every recorded arm would silently
become a statement about a different input space.
"""
import sys
sys.path.insert(0, "/home/samson/workspace/esbmc/scripts")
from solidity_path_generalise import struct_fields, coord_values

# The two real renderings, copied from artefacts on disk.
ESCROW = "{ .orderHash={ .data=nil }, .taker=0, .amount=0 }"   # a PARAMETER
FARM = "{ .farmInfo = { .finished = 0 } }"                     # entry_storage,
#   cov-ce-journal.json of /tmp/certify_all/results_spec_ctrl_deposit

print("1 depth-1, escrow  :", struct_fields(ESCROW))
print("2 depth-1, farm    :", struct_fields(FARM))
print("3 nested , escrow  :", struct_fields(ESCROW, nested=True))
print("4 nested , farm    :", struct_fields(FARM, nested=True))

# A claim shaped like farming/deposit's: one aggregate state variable plus the
# scalars that already worked.
claim = {"env": {"msg.sender": "1", "msg.value": "0"},
         "inputs": {"amount": "7"},
         "entry_storage": {"_farm": FARM, "_owner": "1", "_totalSupply": "0"}}

ce_off, ref_off = coord_values(claim)
print("5 OFF ce           :", dict(sorted(ce_off.items())))
print("6 OFF refused      :", ref_off)

ce_on, ref_on = coord_values(claim, state_structs=True)
print("7 ON  ce           :", dict(sorted(ce_on.items())))
print("8 ON  refused      :", ref_on)

# The default must be byte-identical to what it was before the flag existed.
assert ce_off == {"msg.sender": 1, "msg.value": 0, "amount": 7,
                  "state._owner": 1, "state._totalSupply": 0}, ce_off
assert ref_off == ["state._farm"], ref_off
print("9 DEFAULT UNCHANGED: ok")
