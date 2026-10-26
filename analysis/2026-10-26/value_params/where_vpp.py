"""Prints where vpp, vpp.storage_value and vpp.v2.value are imported from (they must be the worktree's).

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/where_vpp.py
"""

import _common  # noqa: F401  (puts the worktree first and checks it)
import vpp
from vpp import data, storage_value
from vpp.v2 import value

print("worktree root:", _common.ROOT)
for m in (vpp, storage_value, value):
    print(f"{m.__name__}: {m.__file__}")
print("PROCESSED:", data.PROCESSED)
