import csv, sys, glob, datetime as dt
from collections import Counter
for d, step in [("market-clearing-prices", 5), ("reference-trading-price", 30), ("operational-demand", 5), ("estimated-dpv", 5), ("market-requirements", 5)]:
    ts = []
    for f in sorted(glob.glob(f"{sys.argv[1]}/{d}/*.csv")):
        with open(f, newline="") as fh:
            r = csv.reader(fh); next(r)
            for row in r:
                if row and row[0]:
                    ts.append(dt.datetime.strptime(row[0], "%d/%m/%Y %H:%M:%S"))
    c = Counter(ts); dup = sum(1 for v in c.values() if v > 1)
    u = sorted(c)
    gaps = [(a, b) for a, b in zip(u, u[1:]) if (b - a) != dt.timedelta(minutes=step)]
    expected = int((u[-1] - u[0]) / dt.timedelta(minutes=step)) + 1
    print(f"{d:26s} {u[0]} -> {u[-1]}  rows={len(ts)} unique={len(u)} expected={expected} dup_ts={dup} gaps={len(gaps)} {gaps[:3]}")
