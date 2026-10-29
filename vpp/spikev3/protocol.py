"""spikev3's fixed settings (analysis/2026-10-28/spikev3/PLAN.md sections 1 and 2.3).

In-sample decides; the test is reported once, in two parts (the days already looked at, the 7 that were not).
"""

import numpy as np
import pandas as pd

IN_START, IN_END = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-06-30")
SEGMENTS = {
    "in-sample 2025-04-01..2026-06-30": (IN_START, IN_END),
    "test 2026-07-01..10-15": (pd.Timestamp("2026-07-01"), pd.Timestamp("2026-10-15")),
    "test 2026-10-16..10-22": (pd.Timestamp("2026-10-16"), pd.Timestamp("2026-10-22")),
}
SPIKE = 200.0         # $/MWh: a spike half-hour (30-min RTP), the spike models' label
FIRST_SLOT = 2        # 09:00, the decision time: half-hours from here on are what the 09:00 forecast covers
GATE = 50.0           # $/day: the least headroom over e5 worth a realistic follow-up
CONTROL_SHARE = 0.5   # a shuffled control must earn less than this share of its oracle's headroom
SEED = 0


def segment(days) -> np.ndarray:
    """The SEGMENTS label of each trading day ('' outside them)."""
    d = pd.DatetimeIndex(days)
    out = np.full(len(d), "", dtype=object)
    for name, (lo, hi) in SEGMENTS.items():
        out[(d >= lo) & (d <= hi)] = name
    return out
