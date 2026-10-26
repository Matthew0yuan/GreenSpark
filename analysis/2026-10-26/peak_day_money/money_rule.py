"""Money-driven Hot Season calls (PLAN.md section 3). The peak-day study (analysis/2026-10-24/peak_day_probability)
and vpp/ are imported read-only.

  V(k)     capacity value of covering k of the season's 4 peak days, from the training seasons' peak intervals
           (every size-k subset, the policy simulator's ircr_value), at the evaluated season's RCP
  c$       Zenith's cost of one call per enrolled home (payments, offset credits, ops, less wholesale value),
           the mean over the training seasons' Hot Season days, window 17:30-19:30
  dVbar    a peak day's expected marginal value when the other three are each covered with probability q
  R2       the study's DP with the call cost c' = c$ / dVbar instead of the assumed 0.03
  R3       call when dV_i - c$ >= lambda(b, r): dV_i from M3's 2,000 paths at 09:00 on day i, given the days already
           called; lambda = dVbar x max(theta(b, r) - c', 0) from R2's thresholds
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from itertools import combinations  # noqa: E402
from math import comb  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STUDY = ROOT / "analysis" / "2026-10-24" / "peak_day_probability"
for p in (ROOT, STUDY):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import peakprob as pp  # noqa: E402  (read only)
import run_experiment as rex  # noqa: E402  (read only)
from vpp import contracts as ct  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

Q = 0.75                       # [assumed] chance each later peak day ends up covered (PLAN 3); sensitivity 0.5, 0.9
Q_SENS = (0.5, 0.9)
AVAIL = 0.9                    # response rate, as the study's money()
LEDGERS = {"L1": "L1 现行规则（净出口记 0）", "L2": "L2 净出口记负（2027 情景，假设组合下限不起作用）"}


# --------------------------------------------------------------------------------------- the study's set-up
def setup():
    """The objects the study's money() builds (same arguments), for V(k) and c$."""
    m = ps.load_market()
    fam = ps.family(2.0, 2.0, 0.2, ps.Household(), (15.0, 21.0), str(ps.LAST_DAY.date()))
    j = fam.index(ps.ircr_start(2.0))
    pol = ps.Policy(name="synergy-like reference", pay_per_kwh=0.70, event_h=2.0, standby_h=2.0, reserve_soc=0.20,
                    window=(15.0, 21.0))
    obls = {"L1": ps.Obligations("L1", energy_to_zenith=True, capacity="floor", ops_per_customer=0.0, ops_per_event=0.05),
            "L2": ps.Obligations("L2", energy_to_zenith=True, capacity="net", ops_per_customer=0.0, ops_per_event=0.05)}
    part = ps.Participation(availability=AVAIL)
    return m, fam, j, pol, obls, part


# --------------------------------------------------------------------------------------------- V(k) and c$
def v_kw(train: tuple, obl, peaks: dict | None = None) -> np.ndarray:
    """Mean IRCR reduction (kW per responding home) of covering k = 0..4 of each training season's peak days."""
    m, fam, j, _, _, _ = setup()
    peaks = ps.peak_intervals() if peaks is None else peaks
    out = np.zeros(5)
    for k in range(1, 5):
        vals = []
        for t in train:
            days = sorted(set(peaks[t][0].tolist()))
            for sub in combinations(days, k):
                calls = ps.Calls(np.array(sub, int), np.full(k, j, int), np.full(k, "ircr", object))
                vals.append(ps.ircr_value(calls, fam, obl, m, t, peaks) / (ct.RCP[t + 1] / 1000))
        out[k] = float(np.mean(vals))
    return out


def v_dollars(kw: np.ndarray, season: int, obl) -> np.ndarray:
    """A$ per enrolled home: kW x the evaluated season's RCP [assumed known before the season] x response rate."""
    return kw / 1000 * ct.RCP[season + 1] * obl.rcp_ratio * AVAIL * obl.capacity_share


def call_cost(train: tuple, obl) -> float:
    """c$: Zenith's mean cost of one call per enrolled home over the training seasons' Hot Season days."""
    m, fam, j, pol, _, _ = setup()
    season = sb.hot_season(m.days)
    d = np.flatnonzero(np.isin(season, train))
    zen = fam.w_actual[j, d] - pol.pay_per_kwh * fam.paid_kwh[j, d] - pol.pay_per_event \
        - pol.offset_rate * fam.standby_kwh[j, d] - obl.ops_per_event
    return float(-AVAIL * zen.mean())


def dvbar(v: np.ndarray, q: float) -> float:
    return float(sum(comb(3, k) * q ** k * (1 - q) ** (3 - k) * (v[k + 1] - v[k]) for k in range(4)))


def marginal_table(v: np.ndarray, q: float) -> np.ndarray:
    """D[K, m] = E_F[V(K + 1 + F) - V(K + F)], F ~ Binomial(m, q): today's marginal value in a path where today is
    a peak day, K peak days are already called and m peak days are still to come."""
    D = np.zeros((5, 5))
    for K in range(5):
        for mm in range(5):
            D[K, mm] = sum(comb(mm, f) * q ** f * (1 - q) ** (mm - f) * (v[min(K + 1 + f, 4)] - v[min(K + f, 4)])
                           for f in range(mm + 1))
    return D


# --------------------------------------------------------------------------------------------- M3's paths
def simulate_top(i, n, y_known, mu0, mu1, pairs, single, hist, ratios, cfg, rng) -> np.ndarray:
    """pp.simulate unchanged (same draws in the same order), returning the paths' peak-day matrix (P x n)."""
    P = cfg.n_paths
    sims = np.empty((P, n))
    u0 = max(0, i - cfg.lag + 1)
    sims[:, :u0] = y_known[:u0]
    if np.isnan(y_known[:u0]).any():
        raise ValueError("a public actual is missing")
    for j in range(u0, min(i, n)):
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
    sims += rng.random(sims.shape) * 1e-6
    fourth = np.partition(sims, n - 4, axis=1)[:, n - 4]
    return sims >= fourth[:, None]


def season_paths(season: int, pools: tuple, cfg, d, mo, last_dec: int | None = None) -> list[np.ndarray]:
    """Per decision day 0 .. n-1 (or last_dec) of the season: the peak-day matrix of M3's paths at 09:00 that day,
    built exactly as pp.run_season builds M3."""
    gi = pp.season_index(d, season)
    n = len(gi)
    y_s = d["y"].to_numpy()[gi]
    mu0, mu1 = mo.p0["mu"].to_numpy()[gi], mo.p1["mu"].to_numpy()[gi]
    hist = pp.history(d, pools)
    out = []
    for dec in range(0, n if last_dec is None else last_dec + 1):
        g = gi[0] + dec
        n_known = dec - cfg.lag + 1
        y_known = np.where(np.arange(n) < max(n_known, 0), y_s, np.nan)
        ratios = np.array([pp.level_ratio(d, g, p, cfg) for p in pools])
        last_pub = g - cfg.lag
        pairs = pp.paired_pool(d, mo, g, mu0[dec], cfg)
        single = {j: pp.residual_pool(d, mo.p0, last_pub, mu0[j], cfg) for j in range(max(0, n_known), max(dec, 0))}
        rng = np.random.default_rng(pp.seed_for(cfg, season, dec, pools))
        out.append(simulate_top(dec, n, y_known, mu0, mu1, pairs, single, hist, ratios, cfg, rng))
    return out


# --------------------------------------------------------------------------------------------- the rules
def r3_calls(tops: list[np.ndarray], days_left: np.ndarray, theta: np.ndarray, budget: int, v: np.ndarray,
             q: float, cost: float) -> tuple[np.ndarray, pd.DataFrame]:
    """R3 in date order. Returns the calls and a per-day log (dV_i, lambda, decision)."""
    dvb = dvbar(v, q)
    n = len(tops)
    called = np.zeros(n, bool)
    log = []
    if dvb <= 0:
        return called, pd.DataFrame({"dV": np.zeros(n), "lam": np.full(n, np.inf), "call": called})
    c_unit = cost / dvb
    D = marginal_table(v, q)
    left = budget
    for i in range(n):
        top = tops[i]
        if left <= 0:
            log.append((np.nan, np.inf, False))
            continue
        K = top[:, called[:i].nonzero()[0]].sum(axis=1) if called[:i].any() else np.zeros(top.shape[0], int)
        mm = top[:, i + 1:].sum(axis=1)
        dv = float((top[:, i] * D[K, mm]).mean())
        r = int(min(days_left[i], theta.shape[1] - 1))
        lam = dvb * max(theta[left, r] - c_unit, 0.0)
        go = dv - cost >= lam
        if go:
            called[i] = True
            left -= 1
        log.append((dv, lam, go))
    return called, pd.DataFrame(log, columns=["dV", "lam", "call"])


# --------------------------------------------------------------------------------------------- money
def er_peaks(season: int) -> dict:
    """The season's 12 intervals chosen on the actual energy requirement (the study's proxy_er), as (day, slot)."""
    m = ps.load_market()
    er = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])["req_energy_mw"]
    ts = sb.ircr_intervals(er, season)
    day = (ts - ps.H).normalize()
    idx = m.days.get_indexer(day)
    slots = np.asarray((ts - day - ps.H) / pd.Timedelta(minutes=30), int)
    if (idx < 0).any() or (slots < ps.PEAK[0]).any() or (slots >= ps.PEAK[1]).any():
        raise ValueError("energy-requirement peak intervals outside the kept slots")
    return {**ps.peak_intervals(), season: (idx, slots)}


def money(calls_wide: dict, peaks: dict, oracle_days: dict) -> pd.DataFrame:
    """The study's money() with the peak intervals and the perfect-foresight days as arguments (same accounts)."""
    m, fam, j, pol, obls, part = setup()
    d = pp.daily()
    rows = []
    keys = sorted({k[1] for k in calls_wide})
    for y in sorted({k[0] for k in calls_wide}):
        dates = d.index[pp.season_index(d, y)]
        cases = {k: calls_wide[(y, k)] for k in keys if (y, k) in calls_wide}
        cases["none|-|0"] = np.zeros(len(dates), bool)
        cases["oracle|perfect foresight|4"] = dates.isin(oracle_days[y])
        for name, called in cases.items():
            days = m.days.get_indexer(dates[called])
            if (days < 0).any():
                raise ValueError("call outside the market days")
            calls = ps.Calls(np.asarray(days, int), np.full(len(days), j, int), np.full(len(days), "ircr", object))
            meth, pol_name, B = name.split("|")
            for lk, obl in obls.items():
                if len(days):
                    acc = ps.account(calls, fam, pol, obl, part, m, y, peaks)
                else:
                    acc = {"zenith_calls": 0.0, "capacity": 0.0, "pay_energy": 0.0, "offset": 0.0, "wholesale": 0.0,
                           "ops_event": 0.0, "customer_calls": 0.0, "violations": 0.0}
                rows.append({"season": y, "ledger": LEDGERS[lk], "method": meth, "policy": pol_name, "budget": int(B),
                             "calls": int(called.sum()), "zenith_net": acc["zenith_calls"], "ircr_value": acc["capacity"],
                             "pay_energy": acc["pay_energy"], "offset": acc["offset"], "wholesale": acc["wholesale"],
                             "ops": acc["ops_event"], "customer_net": acc["customer_calls"], "violations": acc["violations"]})
    return pd.DataFrame(rows)
