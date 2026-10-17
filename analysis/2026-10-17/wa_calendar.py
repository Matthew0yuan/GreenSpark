"""Western Australian public holidays and public-school terms, 2023-09 .. 2026-12.

Sources, checked 2026-10-17:
  public holidays  https://www.wa.gov.au/service/employment/workplace-agreements/public-holidays-western-australia
                   (main table 2026-27, "previous years" table 2021-25). Regional King's Birthday dates for Karratha
                   and Port Hedland are left out: those towns are not on the SWIS grid this market covers.
  school terms     https://www.education.wa.edu.au/past-term-dates (2023-25), .../future-term-dates (2026)
A school-holiday day is any day between two terms; weekends inside a term count as term time.
"""

import numpy as np
import pandas as pd

PUBLIC_HOLIDAYS = pd.to_datetime([
    "2023-09-25",                                                     # King's Birthday
    "2023-12-25", "2023-12-26",                                       # Christmas, Boxing Day
    "2024-01-01", "2024-01-26", "2024-03-04", "2024-03-29", "2024-03-31", "2024-04-01", "2024-04-25", "2024-06-03",
    "2024-09-23", "2024-12-25", "2024-12-26",
    "2025-01-01", "2025-01-27", "2025-03-03", "2025-04-18", "2025-04-20", "2025-04-21", "2025-04-25", "2025-06-02",
    "2025-09-29", "2025-12-25", "2025-12-26",
    "2026-01-01", "2026-01-26", "2026-03-02", "2026-04-03", "2026-04-05", "2026-04-06", "2026-04-25", "2026-04-27",
    "2026-06-01", "2026-09-28", "2026-12-25", "2026-12-26", "2026-12-28",
])

SCHOOL_TERMS = [(pd.Timestamp(a), pd.Timestamp(b)) for a, b in [
    ("2023-07-17", "2023-09-22"), ("2023-10-09", "2023-12-14"),
    ("2024-01-31", "2024-03-28"), ("2024-04-15", "2024-06-28"), ("2024-07-15", "2024-09-20"), ("2024-10-07", "2024-12-12"),
    ("2025-02-05", "2025-04-11"), ("2025-04-28", "2025-07-04"), ("2025-07-21", "2025-09-26"), ("2025-10-13", "2025-12-18"),
    ("2026-02-02", "2026-04-02"), ("2026-04-20", "2026-07-03"), ("2026-07-20", "2026-09-25"), ("2026-10-12", "2026-12-17"),
]]

# day types, mutually exclusive, in this order of priority
DAY_TYPES = ["公众假期", "周末", "假期前后的工作日", "学校假期的工作日", "上学期间的工作日"]


def day_table(first: str, last: str) -> pd.DataFrame:
    """One row per calendar day: day type and the flags behind it."""
    d = pd.DataFrame(index=pd.date_range(first, last, name="date"))
    ph = d.index.isin(PUBLIC_HOLIDAYS)
    weekend = d.index.dayofweek >= 5
    near_ph = (d.index - pd.Timedelta(days=1)).isin(PUBLIC_HOLIDAYS) | (d.index + pd.Timedelta(days=1)).isin(PUBLIC_HOLIDAYS)
    in_term = pd.Series(False, index=d.index)
    for a, b in SCHOOL_TERMS:
        in_term[(d.index >= a) & (d.index <= b)] = True
    d["public_holiday"] = ph
    d["weekend"] = weekend
    d["bridge"] = near_ph & ~ph & ~weekend           # a working day right before or after a public holiday
    d["school_holiday"] = ~in_term.to_numpy()
    md = d.index.strftime("%m-%d")
    d["xmas_newyear"] = (md >= "12-24") | (md <= "01-01")
    d["day_type"] = DAY_TYPES[4]
    d.loc[d["school_holiday"], "day_type"] = DAY_TYPES[3]
    d.loc[d["bridge"], "day_type"] = DAY_TYPES[2]
    d.loc[d["weekend"], "day_type"] = DAY_TYPES[1]
    d.loc[d["public_holiday"], "day_type"] = DAY_TYPES[0]
    ph_days = PUBLIC_HOLIDAYS.to_numpy()
    d["days_to_holiday"] = [abs((x - ph_days) / np.timedelta64(1, "D")).min() for x in d.index.to_numpy()]
    return d
