"""Peak-day probability for the IRCR Hot Season, with strict 09:00 information cut-offs (PLAN.md, frozen first).

For every Hot Season trading day D (1 Dec - 31 Mar) the methods give a same-day output (decided at 09:00 D) and a
day-ahead output (decided at 09:00 D-1):
  group 1  M1 the existing rule (vpp.site_bill.dynamic_flags, tolerance from earlier seasons only) and M1p, its
           probability analogue (share of earlier seasons' forecast errors that would beat the known 4th highest)
  group 2  M2 AEMO's forecast with a ridge bias correction; P_beat = P(day beats the season's known 4th highest),
           M2-flag = P_beat >= 0.10
  group 3  M3 P_final = P(day ends in the season's final top 4), from 2,000 paths: known days fixed, today and
           tomorrow from the corrected forecasts plus paired out-of-sample residuals, the rest of the season from
           7-day blocks of earlier seasons at the same season position, scaled by a level ratio from known days
  baselines  S0 = 4 / season days; S1 = P(day beats a fixed seasonal threshold from earlier seasons)
Online policies: first come first served for flags; a dynamic-programming threshold (expected peak days covered less
a call cost) for probabilities. Labels: AEMO's published peak days (2025-26 only) and the operational-demand proxy.

Production code (vpp/) is imported read-only.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from vpp import site_bill as sb  # noqa: E402
from vpp import wa_calendar as wc  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

H = pd.Timedelta(hours=8)
FIRST, LAST = pd.Timestamp("2023-10-01"), pd.Timestamp("2026-10-22")
SEASONS = (2023, 2024, 2025)
WARM = (11, 12, 1, 2, 3)                 # months the bias model trains on and draws residuals from
EVENT_TIMES = ("17:30", "18:00", "18:30", "19:00")          # interval starts inside the fixed 17:30-19:30 call
FACTORY_TIMES = ("17:00", "17:30", "18:00", "18:30", "19:00", "19:30")  # vpp.site_bill.IRCR_SLOTS, for reference
BUDGETS = (15, 30, 45)
COST = 0.03                               # call cost in units of one covered peak day [PLAN 5, rough]
COST_SWEEP = (0.0, 0.01, 0.03, 0.1, 0.2)
PROB_METHODS = ("S0", "S1", "M1p", "M2", "M3")
FLAG_METHODS = ("M1_union", "M1_same", "M2_flag")


@dataclass(frozen=True)
class Config:
    lag: int = 1                # at 09:00 D the actual daily maxima are known through D - lag [assumed: lag 1]
    alpha: float = 1.0          # ridge penalty on standardised features
    min_train: int = 60         # fewer training days: forecast + mean bias
    knn: int = 150              # residual pool: the past days whose prediction is nearest today's
    min_pool: int = 30          # fewer out-of-sample residuals: AEMO's raw errors (conservative baseline)
    features: str = "main"      # main | lin | wx
    n_paths: int = 2000
    block: int = 7              # days per historical block
    window: int = 10            # block start within +- this many days of the same season position
    level_days: int = 60        # level ratio from this many latest known days
    level: bool = True
    seed: int = 20261024
    flag_p: float = 0.10        # M2-flag: P_beat at least this (the 90% tolerance of M1)
    q_tol: float = 0.9          # M1 tolerance quantile (vpp.site_bill.FLAG_QUANTILE)


# --------------------------------------------------------------------------------------------------- the data
def season_position(days: pd.DatetimeIndex) -> np.ndarray:
    """Days since 1 December of the Hot Season the contract year (from 1 July) leads into; negative before it."""
    days = pd.DatetimeIndex(days)
    start = np.where(days.month >= 7, days.year, days.year - 1)
    dec1 = pd.to_datetime([f"{y}-12-01" for y in start])
    return np.asarray((days - dec1).days, int)


@lru_cache(maxsize=1)
def raw_daily() -> pd.DataFrame:
    """One row per trading day: actual daily maxima, AEMO's forecasts public at 09:00, weather forecasts, calendar."""
    df = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw", "req_energy_mw"])
    days = pd.date_range(FIRST, LAST)
    d = pd.DataFrame(index=days)
    d["y"] = sb.daily_max(df["demand_mw"]).reindex(days)          # operational demand: the label quantity (proxy)
    d["er"] = sb.daily_max(df["req_energy_mw"]).reindex(days)     # actual energy requirement: what M1 compares with
    d["f0"] = sb.peak_day_predictor(0).reindex(days)              # 08:00 run of D, public by 09:00 D
    d["f1"] = sb.peak_day_predictor(1).reindex(days)              # 08:00 run of D-1, public by 09:00 D-1
    t = pd.read_parquet(PROCESSED / "spike_table.parquet",
                        columns=["target_date", "target_time", "days_ahead", "wx_tmax_day", "wx_temp", "pd_age_h"])
    t0 = t[t["days_ahead"] == 0]
    d["f0_age_h"] = t0.groupby("target_date")["pd_age_h"].max().reindex(days)
    d["wx_tmax0"] = t0.groupby("target_date")["wx_tmax_day"].max().reindex(days)
    at18 = t0[(t0["target_time"].dt.hour == 18) & (t0["target_time"].dt.minute == 0)]
    d["wx_t18"] = at18.groupby("target_date")["wx_temp"].first().reindex(days)
    d["nonbiz"] = (~wc.business_day(days)).astype(float)
    d["season"] = sb.hot_season(days)
    d["pos"] = season_position(days)
    return d


def daily() -> pd.DataFrame:
    return raw_daily().copy()


@lru_cache(maxsize=1)
def demand_30() -> pd.Series:
    return pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw"])["demand_mw"]


def season_index(d: pd.DataFrame, season: int) -> np.ndarray:
    return np.flatnonzero(d["season"].to_numpy() == season)


@lru_cache(maxsize=1)
def labels() -> dict:
    """Per season: proxy peak days on operational demand and on actual energy requirement, AEMO's published days
    where known, the primary label (official if published, else the operational-demand proxy) and its 12 intervals."""
    d = raw_daily()
    out = {}
    for y in SEASONS:
        s = d[d["season"] == y]
        od = pd.DatetimeIndex(sorted(s["y"].nlargest(4).index))
        er = pd.DatetimeIndex(sorted(s["er"].nlargest(4).index))
        iv = sb.ircr_intervals(demand_30(), y)                     # official for 2025-26, else on demand_mw
        official = None
        if y in sb.OFFICIAL_PEAK_INTERVALS:
            official = pd.DatetimeIndex(sorted({(t - H).normalize() for t in pd.DatetimeIndex(sb.OFFICIAL_PEAK_INTERVALS[y])}))
        out[y] = {"proxy_od": od, "proxy_er": er, "official": official,
                  "primary": official if official is not None else od,
                  "primary_kind": "official" if official is not None else "proxy_od", "intervals": pd.DatetimeIndex(iv)}
    return out


# ---------------------------------------------------------------------------------------------- group 2: bias model
def design(d: pd.DataFrame, cfg: Config, ahead: int) -> tuple[pd.DataFrame, pd.Series]:
    """Features of the same-day (ahead 0) or day-ahead (ahead 1) model, row t = target day t, all public at the
    decision (09:00 t - ahead). The forecast: same day f0 (missing: the day before's f1, still public); day ahead f1."""
    k = cfg.lag + ahead                                   # the latest actual known at the decision is day t - k
    if ahead == 0:
        f = d["f0"].fillna(d["f1"])
        cols = {"f": f, "rev": (d["f0"] - d["f1"]).fillna(0.0)}
    else:
        f = d["f1"]
        cols = {"f": f}
    cols["y_known"] = d["y"].shift(k)
    cols["err_known"] = (d["y"] - d["f0"]).shift(k)
    cols["nonbiz"] = d["nonbiz"]
    if cfg.features == "lin":
        cols = {"f": f}
    elif cfg.features == "wx" and ahead == 0:
        cols["wx_tmax0"] = d["wx_tmax0"]
        cols["wx_t18"] = d["wx_t18"]
    return pd.DataFrame(cols, index=d.index), f


def ridge(Xtr: np.ndarray, ztr: np.ndarray, xte: np.ndarray, alpha: float) -> float:
    m, s = Xtr.mean(axis=0), Xtr.std(axis=0)
    s[s == 0] = 1.0
    Z = (Xtr - m) / s
    zc = ztr - ztr.mean()
    beta = np.linalg.solve(Z.T @ Z + alpha * np.eye(Z.shape[1]), Z.T @ zc)
    return float(ztr.mean() + ((xte - m) / s) @ beta)


def predictions(d: pd.DataFrame, cfg: Config, ahead: int) -> pd.DataFrame:
    """Out-of-sample prediction of each warm-month day's maximum operational demand, made at its decision time
    (09:00 t - ahead) from a fit on warm days whose actual is known then; columns mu, resid, train_end, n_train."""
    X, f = design(d, cfg, ahead)
    y = d["y"].to_numpy()
    fa = f.to_numpy()
    Xa = X.to_numpy()
    warm = d.index.month.isin(WARM)
    complete = ~np.isnan(Xa).any(axis=1) & ~np.isnan(fa) & ~np.isnan(y)
    n = len(d)
    mu = np.full(n, np.nan)
    train_end = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    n_train = np.zeros(n, int)
    z = y - fa
    for t in range(n):
        if not warm[t] or np.isnan(fa[t]):
            continue
        last = t - ahead - cfg.lag                         # last day whose actual is public at the decision
        if last < 0:
            mu[t] = fa[t]
            continue
        tr = np.flatnonzero(warm[:last + 1] & complete[:last + 1])
        n_train[t] = len(tr)
        if len(tr):
            train_end[t] = d.index[tr[-1]].to_datetime64()
        if len(tr) >= cfg.min_train and not np.isnan(Xa[t]).any():
            mu[t] = fa[t] + ridge(Xa[tr], z[tr], Xa[t], cfg.alpha)
        elif len(tr):
            mu[t] = fa[t] + float(z[tr].mean())
        else:
            mu[t] = fa[t]
    out = pd.DataFrame({"mu": mu, "resid": y - mu, "train_end": train_end, "n_train": n_train}, index=d.index)
    return out


@dataclass
class Models:
    """The bias model's out-of-sample predictions, same day and day ahead (and, for the wx variant, the main ones
    it falls back to)."""
    p0: pd.DataFrame
    p1: pd.DataFrame


def models(d: pd.DataFrame, cfg: Config) -> Models:
    p0 = predictions(d, cfg, 0)
    p1 = predictions(d, cfg, 1)
    if cfg.features == "wx":
        base = Config(**{**cfg.__dict__, "features": "main"})
        b0 = predictions(d, base, 0)
        miss = p0["mu"].isna() & b0["mu"].notna()
        p0.loc[miss, ["mu", "train_end", "n_train"]] = b0.loc[miss, ["mu", "train_end", "n_train"]]
        p0["resid"] = d["y"] - p0["mu"]
        p1 = predictions(d, base, 1)
    return Models(p0, p1)


def residual_pool(d: pd.DataFrame, pr: pd.DataFrame, upto: int, mu_now: float, cfg: Config) -> np.ndarray:
    """Out-of-sample residuals of warm days with index <= upto (actual public), the knn whose prediction is nearest
    mu_now; with fewer than min_pool, AEMO's raw same-day errors (actual - f0) of those days."""
    if upto < 0:
        return np.array([0.0])
    warm = d.index.month.isin(WARM)[:upto + 1]
    r = pr["resid"].to_numpy()[:upto + 1]
    m = pr["mu"].to_numpy()[:upto + 1]
    ok = warm & ~np.isnan(r)
    if ok.sum() < cfg.min_pool:
        raw = (d["y"] - d["f0"]).to_numpy()[:upto + 1]
        okr = ~np.isnan(raw) & warm
        if okr.sum() < cfg.min_pool:
            okr = ~np.isnan(raw)
        return raw[okr] if okr.any() else np.array([0.0])
    rr, mm = r[ok], m[ok]
    if np.isnan(mu_now) or len(rr) <= cfg.knn:
        return rr
    near = np.argsort(np.abs(mm - mu_now), kind="stable")[:cfg.knn]
    return rr[near]


def paired_pool(d: pd.DataFrame, mo: Models, decision: int, mu_now: float, cfg: Config) -> np.ndarray:
    """(same-day residual of issue day t, day-ahead residual of t+1) from the same 09:00 issue, both actuals public
    at the decision; the knn issues whose same-day prediction is nearest mu_now. Shape (k, 2)."""
    last = decision - cfg.lag                             # last day with a public actual
    if last < 1:
        return np.zeros((1, 2))
    warm = d.index.month.isin(WARM)
    r0 = mo.p0["resid"].to_numpy()
    r1 = mo.p1["resid"].to_numpy()
    m0 = mo.p0["mu"].to_numpy()
    t = np.arange(0, last)                                # issue days whose next day is <= last
    ok = warm[t] & warm[t + 1] & ~np.isnan(r0[t]) & ~np.isnan(r1[t + 1])
    t = t[ok]
    if len(t) < cfg.min_pool:
        single = residual_pool(d, mo.p0, last, mu_now, cfg)
        return np.column_stack([single, single])
    pairs = np.column_stack([r0[t], r1[t + 1]])
    if np.isnan(mu_now) or len(t) <= cfg.knn:
        return pairs
    near = np.argsort(np.abs(m0[t] - mu_now), kind="stable")[:cfg.knn]
    return pairs[near]


# --------------------------------------------------------------------------------------------- shared helpers
def kth_known(values: np.ndarray, n_known: int, k: int = 4) -> float:
    """k-th highest of the first n_known values of a season (-inf with fewer than k known)."""
    v = values[:max(0, n_known)]
    v = v[~np.isnan(v)]
    return float(np.sort(v)[-k]) if len(v) >= k else -np.inf


def level_ratio(d: pd.DataFrame, decision: int, pool_season: int, cfg: Config) -> float:
    """Mean daily maximum of the level_days latest known days over the mean on the same season positions of the
    pool season (known data only). 1 when switched off or not computable."""
    if not cfg.level:
        return 1.0
    last = decision - cfg.lag
    if last < 0:
        return 1.0
    idx = np.arange(max(0, last - cfg.level_days + 1), last + 1)
    pos = d["pos"].to_numpy()[idx]
    cur = d["y"].to_numpy()[idx]
    dec1 = pd.Timestamp(f"{pool_season}-12-01")
    pdates = dec1 + pd.to_timedelta(pos, unit="D")
    pidx = d.index.get_indexer(pdates)
    okp = pidx >= 0
    past = np.full(len(idx), np.nan)
    past[okp] = d["y"].to_numpy()[pidx[okp]]
    ok = ~np.isnan(cur) & ~np.isnan(past)
    if ok.sum() < 10:
        return 1.0
    return float(cur[ok].mean() / past[ok].mean())


def seasonal_threshold(d: pd.DataFrame, decision: int, pools: tuple, cfg: Config) -> float:
    """S1: level ratio x (4th + 5th highest daily maximum) / 2 of each pool season, averaged."""
    vals = []
    for p in pools:
        s = np.sort(d["y"].to_numpy()[season_index(d, p)])
        vals.append(level_ratio(d, decision, p, cfg) * (s[-4] + s[-5]) / 2)
    return float(np.mean(vals))


def history(d: pd.DataFrame, pools: tuple) -> tuple[np.ndarray, np.ndarray]:
    """Pool seasons' daily maxima by season position, NaN-padded: (n_pool, longest) and lengths."""
    seqs = [d["y"].to_numpy()[season_index(d, p)] for p in pools]
    L = max(len(s) for s in seqs)
    Hm = np.full((len(seqs), L), np.nan)
    for j, s in enumerate(seqs):
        Hm[j, :len(s)] = s
    return Hm, np.array([len(s) for s in seqs])


def seed_for(cfg: Config, season: int, decision_pos: int, pools: tuple) -> np.random.SeedSequence:
    return np.random.SeedSequence([cfg.seed, season, decision_pos + 2, sum(1 << (p - 2020) for p in pools)])


# ----------------------------------------------------------------------------------------- group 3: simulation
def simulate(i: int, n: int, y_known: np.ndarray, mu0: np.ndarray, mu1: np.ndarray, pairs: np.ndarray,
             single: dict, hist: tuple, ratios: np.ndarray, cfg: Config, rng: np.random.Generator) -> dict:
    """Paths of the season's daily maxima at 09:00 on season day i (i = -1: the day before the season starts).
    y_known: the season's actuals, NaN where not public at the decision. mu0, mu1: same-day / day-ahead predictions
    of each season day (NaN where none). single: day -> residual pool for days before today not yet public."""
    P = cfg.n_paths
    sims = np.empty((P, n))
    u0 = max(0, i - cfg.lag + 1)                          # first season day without a public actual
    sims[:, :u0] = y_known[:u0]
    if np.isnan(y_known[:u0]).any():
        raise ValueError("a public actual is missing")
    for j in range(u0, min(i, n)):                         # only with lag >= 2: earlier days not yet published
        r = single[j]
        sims[:, j] = mu0[j] + r[rng.integers(len(r), size=P)]
    j0 = max(u0, i)
    k = rng.integers(len(pairs), size=P)
    if 0 <= i < n:
        if not np.isnan(mu0[i]):
            sims[:, i] = mu0[i] + pairs[k, 0]
            j0 = i + 1
        if j0 == i + 1 and i + 1 < n and not np.isnan(mu1[i + 1]):
            sims[:, i + 1] = mu1[i + 1] + pairs[k, 1]
            j0 = i + 2
    elif i == -1 and not np.isnan(mu1[0]):
        sims[:, 0] = mu1[0] + pairs[k, 1]
        j0 = 1
    Hm, lens = hist
    pos = j0
    while pos < n:
        L = min(cfg.block, n - pos)
        ps_ = rng.integers(len(lens), size=P)
        s = pos + rng.integers(-cfg.window, cfg.window + 1, size=P)
        s = np.clip(s, 0, lens[ps_] - L)
        idx = s[:, None] + np.arange(L)[None, :]
        sims[:, pos:pos + L] = Hm[ps_[:, None], idx] * ratios[ps_][:, None]
        pos += L
    if np.isnan(sims).any():
        raise ValueError("NaN in simulated paths")
    # overlapping blocks can repeat one historical day in a path: break ties at random (1e-6 MW), so that every
    # path has exactly 4 peak days
    sims += rng.random(sims.shape) * 1e-6
    fourth = np.partition(sims, n - 4, axis=1)[:, n - 4]
    top = sims >= fourth[:, None]
    out = {"p_today": float(top[:, i].mean()) if 0 <= i < n else np.nan,
           "p_next": float(top[:, i + 1].mean()) if i + 1 < n else np.nan,
           "top_per_path_min": int(top.sum(axis=1).min()), "top_per_path_max": int(top.sum(axis=1).max()),
           "exp_peaks_ahead": float(top[:, max(i + 1, 0):].sum(axis=1).mean())}
    if 0 <= i < n:
        k4 = kth_known(y_known, u0)
        out["p_beat_sim"] = float((sims[:, i] >= k4).mean())
    return out


# ----------------------------------------------------------------------------------------------- one season
def run_season(season: int, pools: tuple, cfg: Config = Config(), d: pd.DataFrame | None = None,
               mo: Models | None = None, sim: bool = True) -> pd.DataFrame:
    """Every method's same-day and day-ahead outputs for each day of one Hot Season, with the pool seasons (earlier
    seasons in the forward evaluation) as the history for tolerances, thresholds and blocks."""
    d = daily() if d is None else d
    mo = models(d, cfg) if mo is None else mo
    gi = season_index(d, season)
    n = len(gi)
    y_s, er_s = d["y"].to_numpy()[gi], d["er"].to_numpy()[gi]
    f0_s, f1_s = d["f0"].to_numpy()[gi], d["f1"].to_numpy()[gi]
    mu0 = mo.p0["mu"].to_numpy()[gi]
    mu1 = mo.p1["mu"].to_numpy()[gi]
    # group 1: earlier seasons' errors of AEMO's forecast against the actual energy requirement
    hs = np.isin(d["season"].to_numpy(), pools)
    e0 = (d["er"] - d["f0"]).to_numpy()[hs]
    e1 = (d["er"] - d["f1"]).to_numpy()[hs]
    e0, e1 = e0[~np.isnan(e0)], e1[~np.isnan(e1)]
    hist = history(d, pools)
    rows = {k: np.full(n, np.nan) for k in (
        "K4_od", "K4_er", "M1p", "M2", "S1", "M3", "M3_beat_sim", "M1p_da", "M2_da", "S1_da", "M3_da", "T_s1",
        "ratio", "pool_n", "exp_peaks_ahead", "top_min", "top_max", "mu0_train_end_ok", "mu1_train_end_ok")}
    for dec in range(-1, n):                               # decision at 09:00 on season day dec
        g = gi[0] + dec                                    # global index of the decision day
        n_known = dec - cfg.lag + 1                        # season days with a public actual
        y_known = np.where(np.arange(n) < max(n_known, 0), y_s, np.nan)
        er_known = np.where(np.arange(n) < max(n_known, 0), er_s, np.nan)
        k4_od = kth_known(y_s, n_known)
        k4_er = kth_known(er_s, n_known)
        T = seasonal_threshold(d, g, pools, cfg)
        ratios = np.array([level_ratio(d, g, p, cfg) for p in pools])
        last_pub = g - cfg.lag
        # same day (season day dec)
        if dec >= 0:
            rows["K4_od"][dec], rows["K4_er"][dec], rows["T_s1"][dec], rows["ratio"][dec] = k4_od, k4_er, T, ratios.mean()
            pool0 = residual_pool(d, mo.p0, last_pub, mu0[dec], cfg)
            rows["pool_n"][dec] = len(pool0)
            if np.isnan(mu0[dec]):                         # no forecast at all: never silently low risk
                rows["M2"][dec] = rows["S1"][dec] = 1.0
            else:
                rows["M2"][dec] = 1.0 if not np.isfinite(k4_od) else float((mu0[dec] + pool0 >= k4_od).mean())
                rows["S1"][dec] = float((mu0[dec] + pool0 >= T).mean())
            f = f0_s[dec] if not np.isnan(f0_s[dec]) else f1_s[dec]
            if not np.isfinite(k4_er) or np.isnan(f):
                rows["M1p"][dec] = 1.0
            else:
                rows["M1p"][dec] = float((f + e0 >= k4_er).mean())
            te = mo.p0["train_end"].to_numpy()[gi[dec]]
            rows["mu0_train_end_ok"][dec] = float(pd.isna(te) or te <= d.index[last_pub].to_datetime64())
        # day ahead (season day dec + 1)
        if dec + 1 < n:
            j = dec + 1
            pool1 = residual_pool(d, mo.p1, last_pub, mu1[j], cfg)
            if np.isnan(mu1[j]):
                rows["M2_da"][j] = rows["S1_da"][j] = 1.0
            else:
                rows["M2_da"][j] = 1.0 if not np.isfinite(k4_od) else float((mu1[j] + pool1 >= k4_od).mean())
                rows["S1_da"][j] = float((mu1[j] + pool1 >= T).mean())
            if not np.isfinite(k4_er) or np.isnan(f1_s[j]):
                rows["M1p_da"][j] = 1.0
            else:
                rows["M1p_da"][j] = float((f1_s[j] + e1 >= k4_er).mean())
            te = mo.p1["train_end"].to_numpy()[gi[j]]
            rows["mu1_train_end_ok"][j] = float(pd.isna(te) or te <= d.index[last_pub].to_datetime64())
        if sim:
            pairs = paired_pool(d, mo, g, mu0[dec] if dec >= 0 else np.nan, cfg)
            single = {j: residual_pool(d, mo.p0, last_pub, mu0[j], cfg) for j in range(max(0, n_known), max(dec, 0))}
            rng = np.random.default_rng(seed_for(cfg, season, dec, pools))
            out = simulate(dec, n, y_known, mu0, mu1, pairs, single, hist, ratios, cfg, rng)
            if dec >= 0:
                rows["M3"][dec] = out["p_today"]
                rows["M3_beat_sim"][dec] = out["p_beat_sim"]
                rows["exp_peaks_ahead"][dec] = out["exp_peaks_ahead"]
                rows["top_min"][dec], rows["top_max"][dec] = out["top_per_path_min"], out["top_per_path_max"]
            if dec + 1 < n:
                rows["M3_da"][dec + 1] = out["p_next"]
    out = pd.DataFrame(rows, index=d.index[gi])
    out["S0"] = 4.0 / n
    out["S0_da"] = 4.0 / n
    out.insert(0, "season", season)
    out.insert(1, "pool", "+".join(map(str, pools)))
    out["season_day"] = np.arange(n)
    out["days_left"] = n - np.arange(n)
    out["y"], out["er"], out["f0"], out["f1"], out["mu0"], out["mu1"] = y_s, er_s, f0_s, f1_s, mu0, mu1
    out["f0_age_h"] = d["f0_age_h"].to_numpy()[gi]
    # group 1 flags, the existing function unchanged, tolerance from the pool seasons only
    act = d["er"].dropna()
    p0s, p1s = d["f0"].dropna(), d["f1"].dropna()
    t0 = sb.forecast_tolerance(p0s, act, list(pools), cfg.q_tol)
    t1 = sb.forecast_tolerance(p1s, act, list(pools), cfg.q_tol)
    f_same = sb.dynamic_flags(p0s, act, t0, cfg.lag)
    f_da = sb.dynamic_flags(p1s, act, t1, cfg.lag + 1)
    out["M1_same"] = out.index.isin(f_same).astype(float)
    out["M1_da"] = out.index.isin(f_da).astype(float)
    out["M1_union"] = ((out["M1_same"] > 0) | (out["M1_da"] > 0)).astype(float)
    out["tol_m1_same"], out["tol_m1_da"] = t0, t1
    out["M2_flag"] = (out["M2"] >= cfg.flag_p).astype(float)
    lab = labels()[season]
    for k in ("primary", "proxy_od", "proxy_er"):
        out[f"lab_{k}"] = out.index.isin(lab[k]).astype(float)
    out["lab_official"] = out.index.isin(lab["official"]).astype(float) if lab["official"] is not None else np.nan
    return out


# --------------------------------------------------------------------------------------------- calling policies
def dp_thresholds(train: list[pd.DataFrame], col: str, budget: int, cost: float, halfwidth: int = 7,
                  r_max: int = 122) -> np.ndarray:
    """theta[n, r]: call with n calls left and r season days left (today included) when p >= theta. From the
    recursion V(n, r) = E[max(p - cost + V(n-1, r-1), V(n, r-1))], p drawn from the training seasons' probabilities
    of this method at r +- halfwidth days left."""
    pr = pd.concat([t[[col, "days_left"]] for t in train]).dropna()
    p_all, r_all = pr[col].to_numpy(), pr["days_left"].to_numpy()
    V = np.zeros((budget + 1, r_max + 1))
    for r in range(1, r_max + 1):
        p = p_all[np.abs(r_all - r) <= halfwidth]
        if len(p) == 0:
            p = p_all
        for nn in range(1, budget + 1):
            V[nn, r] = np.maximum(p - cost + V[nn - 1, r - 1], V[nn, r - 1]).mean()
    theta = np.full((budget + 1, r_max + 1), np.inf)
    for r in range(1, r_max + 1):
        for nn in range(1, budget + 1):
            theta[nn, r] = V[nn, r - 1] - V[nn - 1, r - 1] + cost
    return theta


def dp_calls(p: np.ndarray, days_left: np.ndarray, theta: np.ndarray, budget: int) -> np.ndarray:
    called = np.zeros(len(p), bool)
    left = budget
    for i in range(len(p)):
        if left <= 0:
            break
        r = int(min(days_left[i], theta.shape[1] - 1))
        if p[i] >= theta[left, r]:
            called[i] = True
            left -= 1
    return called


def fcfs_calls(flags: np.ndarray, budget: int) -> np.ndarray:
    called = np.zeros(len(flags), bool)
    left = budget
    for i in range(len(flags)):
        if left <= 0:
            break
        if flags[i] > 0:
            called[i] = True
            left -= 1
    return called


def topn_calls(p: np.ndarray, budget: int) -> np.ndarray:
    """Offline reference (hindsight over the whole season): the budget days of highest probability."""
    order = np.argsort(-p, kind="stable")[:budget]
    called = np.zeros(len(p), bool)
    called[order] = True
    return called


# -------------------------------------------------------------------------------------------------- metrics
def average_precision(p: np.ndarray, lab: np.ndarray) -> float:
    """Average precision with tied scores taken together (as sklearn's average_precision_score)."""
    order = np.argsort(-p, kind="stable")
    ps, ls = p[order], lab[order]
    tp = np.cumsum(ls)
    k = np.arange(1, len(ps) + 1)
    last_of_tie = np.r_[ps[1:] != ps[:-1], True]
    tp_t, k_t = tp[last_of_tie], k[last_of_tie]
    rec = tp_t / max(ls.sum(), 1)
    prec = tp_t / k_t
    return float(np.sum(np.diff(np.r_[0.0, rec]) * prec))


def prob_metrics(p: np.ndarray, lab: np.ndarray, base: np.ndarray | None = None) -> dict:
    """base: the S0 probabilities the skill score is measured against (default 4 / days, one season)."""
    n = len(p)
    pc = np.clip(p, 1e-4, 1 - 1e-4)
    brier = float(np.mean((p - lab) ** 2))
    base = np.full(n, 4.0 / n) if base is None else np.asarray(base, float)
    brier0 = float(np.mean((base - lab) ** 2))
    ranks = pd.Series(p).rank(ascending=False, method="average").to_numpy()
    return {"brier": brier, "bss_vs_S0": 1 - brier / brier0, "logloss": float(-np.mean(lab * np.log(pc) + (1 - lab) * np.log(1 - pc))),
            "sum_p": float(p.sum()), "mean_p_peak": float(p[lab > 0].mean()), "mean_p_other": float(p[lab == 0].mean()),
            "peak_ranks": " ".join(f"{r:g}" for r in ranks[lab > 0]), "median_peak_rank": float(np.median(ranks[lab > 0])),
            "avg_precision": average_precision(p, lab)}


def reliability(p: np.ndarray, lab: np.ndarray, edges=(0, 0.01, 0.03, 0.1, 0.3, 1.0000001)) -> pd.DataFrame:
    b = np.digitize(p, edges[1:-1], right=False)
    rows = []
    for k in range(len(edges) - 1):
        s = b == k
        rows.append({"bin": f"[{edges[k]:g}, {min(edges[k + 1], 1):g}{']' if k == len(edges) - 2 else ')'}", "days": int(s.sum()),
                     "mean_p": float(p[s].mean()) if s.any() else np.nan, "peak_days": int(lab[s].sum()),
                     "peak_share": float(lab[s].mean()) if s.any() else np.nan, "expected_peaks": float(p[s].sum())})
    return pd.DataFrame(rows)


def call_metrics(called: np.ndarray, dates: pd.DatetimeIndex, lab: np.ndarray, intervals: pd.DatetimeIndex | None,
                 budget: int) -> dict:
    cum = np.cumsum(called)
    exhaust = dates[np.argmax(cum >= budget)] if cum[-1] >= budget else pd.NaT
    out = {"calls": int(called.sum()), "covered_days": int((called & (lab > 0)).sum()), "false_calls": int((called & (lab == 0)).sum()),
           "missed_days": int(((~called) & (lab > 0)).sum()), "dec_calls": int(called[dates.month == 12].sum()),
           "exhausted_on": "" if pd.isna(exhaust) else str(exhaust.date()), "left_at_end": int(budget - called.sum())}
    if intervals is not None:
        day_of = (intervals - H).normalize()
        on = np.isin(day_of, dates[called])
        hhmm = intervals.strftime("%H:%M")
        in_ev = np.isin(hhmm, EVENT_TIMES)
        in_fac = np.isin(hhmm, FACTORY_TIMES)
        out.update({"covered_intervals": int((on & in_ev).sum()), "covered_intervals_17_20": int((on & in_fac).sum()),
                    "median_cut": int((on & in_ev).sum() >= 7),
                    "right_day_interval_outside_window": int((on & ~in_ev).sum())})
    return out
