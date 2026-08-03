"""Both directions of the retreat guard, exercised against the REAL function.

⛔ A guard is not done because it was written. Three cases, and the two that
must still behave exactly as before are the point -- a guard that refuses
everything looks identical to one that refuses the right thing.
"""
import sys
sys.path.insert(0, "/home/samson/workspace/esbmc/scripts")
from solidity_path_generalise import refutation_response

MAX = (1 << 256) - 1

# (A) THE DEFECT. A split piece whose own interval is [1, 3] and whose x_pi
#     value is ~1.16e77, which is the shape farming/deposit's pieces 2-4 have in
#     all three recorded runs. BEFORE the guard this returned ("pin",
#     {"amount": 1157...}) and the caller wrote that point into the piece.
box_a = {"amount": (1, 3), "msg.sender": (1, 100)}
ce_a = {"amount": 115792089237316195423570985008687907853269984565640564039457584007913129639934,
        "msg.sender": 1}
wit_a = {"amount": 5, "msg.sender": 1}
print("A defect-shape :", refutation_response(box_a, {}, ce_a, wit_a, {}))

# (B) MUST STILL PIN. Same first trigger -- no cut, because the WITNESS is
#     outside the interval -- but x_pi IS inside it, so the retreat is exactly
#     what §Certification prescribes and the guard must not touch it.
box_b = {"amount": (1, 100)}
ce_b = {"amount": 50}
wit_b = {"amount": 500}
print("B legit pin    :", refutation_response(box_b, {}, ce_b, wit_b, {}))

# (C) MUST STILL PIN, second trigger: a cut is available but would leave the
#     coordinate one value, so the method says pin instead of spending a round.
box_c = {"amount": (50, 51)}
ce_c = {"amount": 50}
wit_c = {"amount": 51}
print("C one-value pin:", refutation_response(box_c, {}, ce_c, wit_c, {}))

# (D) MUST STILL CUT. Nothing about the ordinary path may move.
box_d = {"amount": (1, 100)}
ce_d = {"amount": 10}
wit_d = {"amount": 60}
print("D ordinary cut :", refutation_response(box_d, {}, ce_d, wit_d, {}))
