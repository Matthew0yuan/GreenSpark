"""③ what stored energy is worth, following the price level (reports/v2_plan.md, S3).

v1's value table (vpp.storage_value) fixed its prices at the level of the 365 days before the month, so when prices
rose after 2026-06-30 it fell behind. Here the table is of prices relative to a level known each morning:
  level   L_d: the median (or mean) of every half-hour's RTP over the 28 calendar days before trading day d,
          final at 08:00 on d (closed on the left)
  states  each half-hour's price less its day's level, binned as v1 (same quantiles, transitions, window of 365
          days before the month and never after TRAIN_END)
  solve   on a $5 grid of levels L: prices L + bin means; the CMD still limits charging, but the shortfall penalty
          is off (② holds what tomorrow's factory needs, vpp.v2.reserves); FCESS, IRCR and the cycle limit left out
  day     the curve and the offer values of the grid levels either side of L_d, interpolated (still non-increasing)
Per trading day it keeps L_d, the end-of-day curve ($ per MWh of each 1 MWh segment at the next 08:00), the offer
values (slot x price state x segment) and the relative bin edges, as float32 in an npz the workers load.

hurdle (round 4, reports/v2_plan.md section 11): the table charges every MWh sold its wear plus this hurdle ($/MWh),
as the plans do (bt.HURDLE, the default, is the table rounds 1-3 used). B1 builds tables at 0, 25 and 50: the trades
the table prices are made at prices already seen, so the forecast-error hurdle may be counted twice there.

state (round 4, B4, section 11.6): the values also by the day's market state, a high-risk day or not by the
day-before 09:00 call at the recent threshold (vpp.v2.risk). The transitions between price states are counted on each
state's days of the same window and shrunk to the pooled ones with REGIME_PRIOR pseudo-transitions; from the pooled
value at the next 08:00 one trading day is solved back under each state's transitions, giving today's offer values
when today is in that state, and the 08:00 curve that ends today when tomorrow is in it.

regime (round 5, c5, section 12.4): today's offer values also by whether the day has already spiked (a settled
half-hour >= SPIKE). Inside the day the regime is part of the state: once spiked it stays for the day (it resets at
the next 08:00); a day not yet spiked spikes in a slot with the share of that slot's pooled half-hours in its price
state that reach SPIKE at the table's level. The transitions split by regime are the state's plus the regime's
shrunk deviation (regime_split). The plans' end value is not split: the regime resets at the next 08:00.

battery and site (TableParams, 2026-10-26): every table is solved for one battery (power, energy, round trip, wear)
behind one site (CMD, import limit, the synthetic factory load times a scale); the defaults are the 10 MW / 40 MWh
battery behind the factory with m1's CMD, and their tables keep their names. Other parameters add _p<8 hex digits>
to the name (a hash of the parameters) and a .json of the same name records them; vpp.v2.harness finds the table by
the battery and site the dispatcher sees and refuses one solved for others (check_table).

Run:  python -m vpp.v2.value [--stat mean] [--energy 36] [--hurdle 0] [--state] [--regime]   builds the per-day arrays
          [--power 8] [--round-trip 0.88] [--wear 40] [--cmd 10] [--import-limit 12] [--load-scale 10] [--rebuild]
      python -m vpp.v2.value --b1                                         round 4, B1: builds and checks the tables
"""

import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from vpp import aemo_correction as ac
from vpp import backtest as bt
from vpp import config as cf
from vpp import dispatch as dp
from vpp import site as st
from vpp import storage_value as sv
from vpp.data import PROCESSED
from vpp.evaluate import CUTOFF, TRAIN_END

LEVEL_DAYS = 28   # days of past prices in the level [fixed]
GRID_STEP = 5.0   # $/MWh between solved levels [fixed]
REGIME_PRIOR = 20.0  # pseudo-transitions shrinking each market state's transitions to the pooled ones [fixed, 11.6]
POOL = 2          # round 5, c5: slots either side whose transitions are counted with a slot's [fixed, 12.4]
SPIKE = 150.0     # $/MWh: a settled half-hour at least this has spiked (as vpp.v2.risk.SPIKE) [fixed, 12.4]
FIRST, LAST = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-10-31")  # the backtest's days, with the forward days


@dataclass(frozen=True)
class TableParams:
    """The battery and site a table is solved for. Defaults: the 10 MW / 40 MWh battery (bt.Q2B_ASSET) behind the
    factory (st.FACTORY) with m1's CMD (sv.CMD). The table sees the import limit only as the charging room's
    min(import limit, CMD), so key() puts that in its place: every import limit at or above the CMD shares a table."""
    power_mw: float = bt.Q2B_ASSET.battery.power_mw
    energy_mwh: float = bt.Q2B_ASSET.battery.energy_mwh
    round_trip: float = bt.Q2B_ASSET.battery.round_trip
    wear_per_mwh: float = bt.Q2B_ASSET.wear_per_mwh
    cmd_mw: float = sv.CMD
    import_limit_mw: float = st.FACTORY.import_limit_mw
    load_scale: float = st.FACTORY.load_scale

    def __post_init__(self):
        for f in fields(self):
            object.__setattr__(self, f.name, float(getattr(self, f.name)))
        ok = {"power_mw": self.power_mw > 0, "energy_mwh": self.energy_mwh >= sv.GRID,
              "round_trip": 0 < self.round_trip <= 1, "wear_per_mwh": self.wear_per_mwh >= 0, "cmd_mw": self.cmd_mw > 0,
              "import_limit_mw": self.import_limit_mw > 0, "load_scale": self.load_scale >= 0}
        bad = [k for k, v in ok.items() if not v]
        if bad:
            raise ValueError(f"value table parameters out of range: {', '.join(f'{k}={getattr(self, k)}' for k in bad)}")
        if abs(self.energy_mwh / sv.GRID - round(self.energy_mwh / sv.GRID)) > 1e-9:
            raise ValueError(f"the value table's stored energy is in {sv.GRID:g} MWh segments: {self.energy_mwh:g} MWh "
                             "is not a whole number of them")

    def key(self) -> "TableParams":
        """The parameters as the table sees them (the import limit as min(import limit, CMD))."""
        return replace(self, import_limit_mw=min(self.import_limit_mw, self.cmd_mw))

    def kwargs(self) -> dict:
        """As path and build take them."""
        return asdict(self)

    def only_energy(self) -> bool:
        """The defaults but perhaps the energy (whose tables were always named by it: _<MWh>mwh)."""
        return replace(self.key(), energy_mwh=DEFAULT_PARAMS.energy_mwh) == DEFAULT_PARAMS.key()

    def is_default(self) -> bool:
        return self.key() == DEFAULT_PARAMS.key()

    def digest(self) -> str:
        """8 hex digits of the sha256 of the parameters as the table sees them."""
        return hashlib.sha256(json.dumps(asdict(self.key()), sort_keys=True).encode()).hexdigest()[:8]

    def asset(self) -> cf.Asset:
        base = bt.Q2B_ASSET
        return replace(base, battery=replace(base.battery, power_mw=self.power_mw, energy_mwh=self.energy_mwh,
                                             round_trip=self.round_trip), wear_per_mwh=self.wear_per_mwh)

    def site(self) -> st.Site:
        return replace(st.FACTORY, import_limit_mw=self.import_limit_mw, load_scale=self.load_scale)

    def describe(self) -> str:
        k = self.key()
        return (f"{k.power_mw:g} MW / {k.energy_mwh:g} MWh, round trip {k.round_trip:g}, wear ${k.wear_per_mwh:g}/MWh, "
                f"CMD {k.cmd_mw:g} MW, charging room min(import limit, CMD) {k.import_limit_mw:g} MW, "
                f"load x{k.load_scale:g}")


DEFAULT_PARAMS = TableParams()
_D = DEFAULT_PARAMS
CLI = {"--power": "power_mw", "--energy": "energy_mwh", "--round-trip": "round_trip", "--wear": "wear_per_mwh",
       "--cmd": "cmd_mw", "--import-limit": "import_limit_mw", "--load-scale": "load_scale"}


def path(stat: str = "median", energy_mwh: float = 40.0, hurdle: float = bt.HURDLE, state: bool = False,
         regime: bool = False, *, power_mw: float = _D.power_mw, round_trip: float = _D.round_trip,
         wear_per_mwh: float = _D.wear_per_mwh, cmd_mw: float = _D.cmd_mw, import_limit_mw: float = _D.import_limit_mw,
         load_scale: float = _D.load_scale) -> Path:
    """Where the table for these options and this battery and site is kept: the name of the defaults is as it always
    was (the energy as _<MWh>mwh); any other battery or site parameter adds _p<digest>."""
    p = TableParams(power_mw, energy_mwh, round_trip, wear_per_mwh, cmd_mw, import_limit_mw, load_scale)
    return PROCESSED / (f"v2_value_{stat}{'' if energy_mwh == 40.0 else f'_{energy_mwh:g}mwh'}"
                        f"{'' if hurdle == bt.HURDLE else f'_h{hurdle:g}'}{'_state' if state else ''}"
                        f"{'_regime' if regime else ''}{'' if p.only_energy() else f'_p{p.digest()}'}.npz")


def command(params: TableParams, stat: str = "median", hurdle: float = bt.HURDLE, state: bool = False,
            regime: bool = False) -> str:
    """The command line that builds the table for these options, battery and site."""
    k = params.key()
    plain = replace(_D, cmd_mw=k.cmd_mw).key()  # left out, the import limit is the default's (then min'd with the CMD)
    args = [f"--stat {stat}"] if stat != "median" else []
    args += [f"{flag} {getattr(k, name):g}" for flag, name in CLI.items()
             if getattr(k, name) != (plain.import_limit_mw if name == "import_limit_mw" else getattr(_D, name))]
    args += ([f"--hurdle {hurdle:g}"] if hurdle != bt.HURDLE else []) + (["--state"] if state else []) + \
            (["--regime"] if regime else [])
    return " ".join(["python -m vpp.v2.value", *args])


def _record(params: TableParams, stat: str, hurdle: float, state: bool, regime: bool) -> dict:
    """What the .json beside a table records (the options and the parameters as the table sees them)."""
    return {"stat": stat, "hurdle": float(hurdle), "state": bool(state), "regime": bool(regime),
            "params": asdict(params.key()), "digest": params.digest()}


def recorded(p: Path) -> dict | None:
    """The record beside the table at p, or None without one."""
    rec = p.with_suffix(".json")
    return json.loads(rec.read_text()) if rec.exists() else None


def check_table(p: Path, inputs: dict, params: TableParams, stat: str = "median", hurdle: float = bt.HURDLE,
                state: bool = False, regime: bool = False) -> None:
    """Raise ValueError unless the table at p (loaded: inputs) was solved for these options and parameters: its
    record must say so; without a record only a table of the defaults (but perhaps the energy) is taken; and its
    segments must be the battery's MWh."""
    rec = recorded(p)
    if rec is not None:
        got = TableParams(**rec["params"])
        if got.key() != params.key() or (rec["stat"], rec["hurdle"], rec["state"], rec["regime"]) != \
                (stat, float(hurdle), bool(state), bool(regime)):
            raise ValueError(f"{p.name} was solved for {got.describe()} ({rec['stat']}, hurdle {rec['hurdle']:g}, "
                             f"state {rec['state']}, regime {rec['regime']}); the dispatcher sees {params.describe()} "
                             f"({stat}, hurdle {hurdle:g}, state {state}, regime {regime}): {command(params, stat, hurdle, state, regime)}")
    elif not params.only_energy():
        raise ValueError(f"{p.name} has no record of its parameters ({p.with_suffix('.json').name}); the dispatcher "
                         f"sees {params.describe()}: rebuild it ({command(params, stat, hurdle, state, regime)} --rebuild)")
    segments = inputs["curve"].shape[1]
    if segments != round(params.energy_mwh / sv.GRID):
        raise ValueError(f"{p.name} has {segments} segments of {sv.GRID:g} MWh; the dispatcher's battery stores "
                         f"{params.energy_mwh:g} MWh")


def prices() -> pd.DataFrame:
    """(trading day x slot) 30-min RTP."""
    return ac._by_day_slot(pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy"])["energy"])


def levels(table: pd.DataFrame, stat: str = "median", days: int = LEVEL_DAYS) -> pd.Series:
    """L_d for every trading day d: stat of all half-hours of the trading days in [d - days, d)."""
    idx = table.index
    x = table.to_numpy()
    out = np.full(len(idx), np.nan)
    for i, d in enumerate(idx):
        lo = idx.searchsorted(d - pd.Timedelta(days=days))
        block = x[lo:i].ravel()
        block = block[np.isfinite(block)]
        if len(block):
            out[i] = np.median(block) if stat == "median" else block.mean()
    return pd.Series(out, index=idx)


def regimes() -> pd.Series:
    """Each trading day's market state: 1 if the day-before 09:00 call at the recent threshold made it a high-risk
    day, 0 if not, NaN without the call (vpp.v2.risk; public at 09:00 on the day before)."""
    from vpp.v2 import risk as rk
    return rk.recent_flags(rk.day_risk(rk.history(days_ahead=1)))


def regime_trans(table: pd.DataFrame, month: pd.Period, level: pd.Series, flags: pd.Series, ps: dict) -> np.ndarray:
    """(state, slot, b, c) transitions between price states on the normal (0) and high-risk (1) days of month's
    window, counted as vpp.storage_value.price_states counts them, each row shrunk to the pooled one."""
    end = min(month.start_time, CUTOFF)
    x = table[(table.index >= end - sv.WINDOW) & (table.index < end) & (table.index <= TRAIN_END)].dropna()
    x = x.sub(level.reindex(x.index), axis=0).dropna()
    X, days = x.to_numpy(), x.index
    bins = np.stack([np.searchsorted(ps["edges"][t, 1:-1], X[:, t], side="right") for t in range(dp.N)], axis=1)
    state = flags.reindex(days).to_numpy()
    nxt = np.flatnonzero((days[1:] - days[:-1]) == pd.Timedelta(days=1))  # day i followed by day i + 1
    out = np.empty((2, dp.N, sv.B, sv.B))
    for k in (0, 1):
        rows = state == k
        counts = np.zeros((dp.N, sv.B, sv.B))
        for t in range(dp.N - 1):
            np.add.at(counts[t], (bins[rows, t], bins[rows, t + 1]), 1)
        last = nxt[rows[nxt]]
        np.add.at(counts[dp.N - 1], (bins[last, dp.N - 1], bins[last + 1, 0]), 1)
        out[k] = (counts + REGIME_PRIOR * ps["trans"]) / (counts.sum(axis=2, keepdims=True) + REGIME_PRIOR)
    return out


def regime_split(table: pd.DataFrame, month: pd.Period, level: pd.Series, flags: pd.Series, ps: dict,
                 rtrans: np.ndarray) -> dict:
    """Round 5, c5 (reports/v2_plan.md section 12.4): the transitions between price states split again by whether the
    day has already spiked (a settled half-hour >= SPIKE before the slot), for each market state of the day (0, 1)
    and for a day without a call (2: all days). Each is the base's transitions (B4's for the state, the pooled ones
    for 2) plus, row by row, lam x (the transitions counted on that regime's rows - those counted on both regimes'
    rows), the counts pooled over the POOL slots either side and lam = n / (n + REGIME_PRIOR), n the distinct days in
    the row; rows clipped at 0 and renormalised. Without a real difference it is the base. The last slot (to the next
    day's first, where the regime resets) keeps the base's. Same window as the table (before the month, not after
    TRAIN_END).
    Returns trans (variant, regime, slot, b, c); cells (variant, regime, slot, [distinct days, mean lam weighted by
    the rows' days, largest change of a transition]); pool_rel[slot][b], the sorted relative prices of the slot's
    pooled half-hours in price state b (for the chance such a half-hour spikes at a level)."""
    end = min(month.start_time, CUTOFF)
    x = table[(table.index >= end - sv.WINDOW) & (table.index < end) & (table.index <= TRAIN_END)].dropna()
    rel = x.sub(level.reindex(x.index), axis=0).dropna()
    if len(rel) and not (rel.index.max() < month.start_time and rel.index.max() <= TRAIN_END):
        raise ValueError(f"{month}: the window reaches past the month or the training end")
    X, days = rel.to_numpy(), rel.index
    spiked = x.loc[days].to_numpy() >= SPIKE
    regime = np.c_[np.zeros(len(days), bool), np.cumsum(spiked, axis=1)[:, :-1] > 0]  # a spike before the slot
    bins = np.stack([np.searchsorted(ps["edges"][t, 1:-1], X[:, t], side="right") for t in range(dp.N)], axis=1)
    state = flags.reindex(days).to_numpy()
    trans = np.empty((3, 2, dp.N, sv.B, sv.B))
    cells = np.zeros((3, 2, dp.N, 3))
    for v in (0, 1, 2):
        rows = (state == v) if v < 2 else np.ones(len(days), bool)
        base = rtrans[v] if v < 2 else ps["trans"]
        trans[v, :, dp.N - 1] = base[dp.N - 1]
        for t in range(dp.N - 1):
            both, by = np.zeros((sv.B, sv.B)), np.zeros((2, sv.B, sv.B))
            seen = np.zeros((2, len(days), sv.B), bool)
            for u in range(max(0, t - POOL), min(dp.N - 2, t + POOL) + 1):
                np.add.at(both, (bins[rows, u], bins[rows, u + 1]), 1)
                for k in (0, 1):
                    m = rows & (regime[:, u] == bool(k))
                    np.add.at(by[k], (bins[m, u], bins[m, u + 1]), 1)
                    seen[k, np.flatnonzero(m), bins[m, u]] = True
            p_both = both / np.maximum(both.sum(axis=1, keepdims=True), 1.0)
            for k in (0, 1):
                n = seen[k].sum(axis=0)
                lam = n / (n + REGIME_PRIOR)
                counted = by[k].sum(axis=1, keepdims=True)
                dev = np.where(counted > 0, by[k] / np.maximum(counted, 1.0) - p_both, 0.0)
                tr = np.clip(base[t] + lam[:, None] * dev, 0.0, None)
                trans[v, k, t] = tr / tr.sum(axis=1, keepdims=True)
                cells[v, k, t] = (seen[k].any(axis=1).sum(), (n * lam).sum() / max(n.sum(), 1),
                                  np.abs(trans[v, k, t] - base[t]).max())
    pool_rel = [[np.sort(np.concatenate([X[bins[:, u] == b, u] for u in range(max(0, t - POOL), min(dp.N - 1, t + POOL) + 1)]))
                 for b in range(sv.B)] for t in range(dp.N)]
    return {"trans": trans, "cells": cells, "pool_rel": pool_rel}


def spike_chance(pool_rel: list, means: np.ndarray, level: float) -> np.ndarray:
    """(slot, b): the share of the slot's pooled half-hours in price state b whose price at this level is >= SPIKE
    (smooth in the level; a state without half-hours: whether its mean price spikes)."""
    out = np.empty((dp.N, sv.B))
    for t in range(dp.N):
        for b in range(sv.B):
            x = pool_rel[t][b]
            out[t, b] = ((len(x) - np.searchsorted(x, SPIKE - level, side="left")) / len(x) if len(x)
                         else float(means[t, b] + level >= SPIKE))
    return out


def backward_regime(end: np.ndarray, acts: dict, dow: int, trans: np.ndarray, chance: np.ndarray, E: float) -> np.ndarray:
    """ev[slot, b, regime, point]: the expected value at the end of each slot of a trading day on weekday dow, given
    the slot's price state b and whether the day has spiked before it, solved back from the value end (point x b) at
    the day's end, where the regime resets. Price states move by trans[regime] (regime, slot, b, c); a day not yet
    spiked has spiked after slot t with chance[t, b]. With the same transitions for both regimes it is
    vpp.storage_value.backward's."""
    grid = np.arange(0.0, E + 1e-9, sv.GRID)
    W = np.empty((dp.N + 1, len(grid), sv.B, 2))
    W[dp.N] = end[:, :, None]
    ev = np.empty((dp.N, sv.B, 2, len(grid)))
    for t in reversed(range(dp.N)):
        x, ok, reward = acts[dow, t]
        xc = np.clip(x, 0.0, E)
        for r in (0, 1):
            for b in range(sv.B):
                rho = 1.0 if r else chance[t, b]
                e = ((1.0 - rho) * W[t + 1, :, :, 0] + rho * W[t + 1, :, :, 1]) @ trans[r][t][b]
                ev[t, b, r] = e
                tot = np.where(ok, reward[b][None, :] + np.interp(xc, grid, e), -np.inf)
                W[t, :, b, r] = tot.max(axis=1)
    return ev


def _by_regime(V: np.ndarray, acts: dict, split: dict, chance: np.ndarray, E: float) -> np.ndarray:
    """(variant, dow, slot, b, regime, segment): the offer values of a day on weekday dow in each market state (and
    without a call), by whether the day has spiked: one trading day solved back from the pooled value at its end."""
    marg = np.empty((3, 7, dp.N, sv.B, 2, V.shape[2] - 1))
    for v in range(3):
        for dow in range(7):
            ev = backward_regime(V[dow, dp.N], acts, dow, split["trans"][v], chance, E)
            marg[v, dow] = np.clip(np.apply_along_axis(sv.decreasing, 3, np.diff(ev, axis=3) / sv.GRID), 0.0, None)
    return marg


def _marginals(V: np.ndarray, ps: dict) -> tuple[np.ndarray, np.ndarray]:
    """The end-of-day curve (dow x segment) and the offer values (dow x slot x state x segment) of a solved table,
    as v1 derives them (vpp.storage_value.build)."""
    ev0 = V[:, dp.N] @ ps["pi0"]
    curve = np.clip(np.array([sv.decreasing(r) for r in np.diff(ev0, axis=1) / sv.GRID]), 0.0, None)
    ev = np.einsum("dtpc,tbc->dtbp", V[:, 1:], ps["trans"])
    marg = np.clip(np.apply_along_axis(sv.decreasing, 3, np.diff(ev, axis=3) / sv.GRID), 0.0, None)
    return curve, marg


def _by_state(V: np.ndarray, ps: dict, acts: dict, rtrans: np.ndarray, E: float) -> tuple[np.ndarray, np.ndarray]:
    """(state, dow, segment) the 08:00 curve that ends a day on weekday dow when the next day is in that state, and
    (state, dow, slot, b, segment) the offer values of a day on weekday dow in that state: one trading day solved
    back from the pooled value at its end under the state's transitions."""
    curve = np.empty((2, 7, V.shape[2] - 1))
    marg = np.empty((2, 7, dp.N, sv.B, V.shape[2] - 1))
    for k in (0, 1):
        for dow in range(7):
            today = np.empty_like(V[dow])
            today[dp.N] = V[dow, dp.N]
            sv.backward(today, acts, dow, rtrans[k], E)
            ev = np.einsum("tpc,tbc->tbp", today[1:], rtrans[k])
            marg[k, dow] = np.clip(np.apply_along_axis(sv.decreasing, 2, np.diff(ev, axis=2) / sv.GRID), 0.0, None)
            nxt = (dow + 1) % 7
            tomorrow = np.empty_like(V[nxt])
            tomorrow[dp.N] = V[nxt, dp.N]
            sv.backward(tomorrow, acts, nxt, rtrans[k], E)
            curve[k, dow] = np.clip(sv.decreasing(np.diff(tomorrow[0] @ ps["pi0"]) / sv.GRID), 0.0, None)
    return curve, marg


def _solve(task: tuple) -> tuple:
    month, level, ps, params, hurdle, rtrans, split = task
    asset, site = params.asset(), params.site()
    prefs = bt.PREFS if hurdle == bt.HURDLE else replace(bt.PREFS, hurdle_per_mwh=hurdle)
    at_level = {**ps, "means": level + ps["means"]}
    load = sv.load_profiles(month, site)
    V, weeks = sv.solve(at_level, load, asset=asset, prefs=prefs, site=site, short_penalty=0.0, cmd_mw=params.cmd_mw)
    curve, marg = _marginals(V, ps)
    out = [month, level, curve.astype(np.float32), marg.astype(np.float32), weeks]
    if rtrans is not None:
        acts = sv.actions(at_level, load, asset, prefs, site, short_penalty=0.0, cmd_mw=params.cmd_mw)
        curve_s, marg_s = _by_state(V, ps, acts, rtrans, asset.battery.energy_mwh)
        out += [curve_s.astype(np.float32), marg_s.astype(np.float32)]
        if split is not None:  # round 5, c5: also by whether the day has spiked
            chance = spike_chance(split["pool_rel"], ps["means"], level)
            out.append(_by_regime(V, acts, split, chance, asset.battery.energy_mwh).astype(np.float32))
    return tuple(out)


def build(stat: str = "median", energy_mwh: float = 40.0, rebuild: bool = False, hurdle: float = bt.HURDLE,
          out: Path | None = None, state: bool = False, regime: bool = False, *, power_mw: float = _D.power_mw,
          round_trip: float = _D.round_trip, wear_per_mwh: float = _D.wear_per_mwh, cmd_mw: float = _D.cmd_mw,
          import_limit_mw: float = _D.import_limit_mw, load_scale: float = _D.load_scale) -> Path:
    """Per-day arrays for every backtest day (cached at path(stat, energy_mwh, hurdle, state, regime, ...), or written
    to out); with state, also each day's values by market state and the states of the day and the next (B4); with
    regime (round 5, c5; needs state), also the day's offer values for its own state (pooled without a call) by
    whether the day has spiked, and reports/v2_c5_cells.csv (the split's days, shrinkage and largest change per month
    and slot; _p<digest> added for other than the default battery and site).
    The battery and site (power_mw ... load_scale, with energy_mwh: TableParams) are what the table is solved for;
    other than the defaults, a .json beside the table records them (vpp.v2.harness checks it)."""
    if regime and not state:
        raise ValueError("values by regime are split from the values by market state")
    params = TableParams(power_mw, energy_mwh, round_trip, wear_per_mwh, cmd_mw, import_limit_mw, load_scale)
    record = _record(params, stat, hurdle, state, regime)
    if out is None:
        out = path(stat, energy_mwh, hurdle, state, regime, **{k: v for k, v in params.kwargs().items()
                                                              if k != "energy_mwh"})
        # a cache that stops before the backtest's last day (made before data were added), or whose record is of
        # other parameters, is built again
        rec = recorded(out)
        same = (rec is None and params.only_energy()) or (rec is not None and all(rec.get(k) == v for k, v in record.items()))
        if out.exists() and not rebuild and same and \
                pd.to_datetime(np.load(out)["day"]).max() >= pd.Timestamp(bt.LAST_DAY):
            return out
    table = prices()
    lv = levels(table, stat)
    flags = regimes() if state else None
    days = lv.index[(lv.index >= FIRST) & (lv.index <= LAST)]
    tasks, states, cells = [], {}, []
    for month in sv.MONTHS:
        in_month = days[(days >= month.start_time) & (days <= month.end_time)]
        if not len(in_month):
            continue
        ps = sv.price_states(table, month, level=lv)
        states[month] = ps
        rtrans = regime_trans(table, month, lv, flags, ps) if state else None
        split = regime_split(table, month, lv, flags, ps, rtrans) if regime else None
        if split is not None:
            for v in range(3):
                for k in (0, 1):
                    for t in range(dp.N):
                        n, lam, change = split["cells"][v, k, t]
                        cells.append({"month": str(month), "market_state": ("normal", "high-risk", "no call")[v],
                                      "spiked_today": k, "slot": t, "days": int(n), "lam": lam, "largest_change": change})
        lo, hi = lv[in_month].min(), lv[in_month].max()
        grid = np.arange(np.floor(lo / GRID_STEP) * GRID_STEP, np.ceil(hi / GRID_STEP) * GRID_STEP + 1e-9, GRID_STEP)
        if len(grid) == 1:
            grid = np.r_[grid, grid + GRID_STEP]
        tasks += [(month, float(g), ps, params.key(), hurdle, rtrans, split) for g in grid]
    if cells:
        pd.DataFrame(cells).to_csv(bt.REPORTS / f"v2_c5_cells{'' if params.is_default() else '_p' + params.digest()}.csv",
                                   index=False)
    with ProcessPoolExecutor() as pool:
        solved = {(r[0], r[1]): r[2:] for r in pool.map(_solve, tasks)}
    rows = {"day": [], "level": [], "curve": [], "marg": [], "edges": [], "curve_state": [], "marg_state": [],
            "marg_regime": []}
    for d in days:
        month = d.to_period("M")
        grid = sorted(g for m, g in solved if m == month)
        L = float(lv[d])
        k = min(max(np.searchsorted(grid, L, side="right") - 1, 0), len(grid) - 2)
        g0, g1 = grid[k], grid[k + 1]
        w = min(max((L - g0) / (g1 - g0), 0.0), 1.0)
        a, b = solved[month, g0], solved[month, g1]
        dow = d.dayofweek
        rows["day"].append(str(d.date()))
        rows["level"].append(L)
        rows["curve"].append((1 - w) * a[0][dow] + w * b[0][dow])
        rows["marg"].append((1 - w) * a[1][dow] + w * b[1][dow])
        rows["edges"].append(states[month]["edges"])
        if state:
            rows["curve_state"].append((1 - w) * a[3][:, dow] + w * b[3][:, dow])
            rows["marg_state"].append((1 - w) * a[4][:, dow] + w * b[4][:, dow])
        if regime:
            v = int(flags.get(d, np.nan)) if np.isfinite(flags.get(d, np.nan)) else 2  # today's state, or no call
            rows["marg_regime"].append((1 - w) * a[5][v, dow] + w * b[5][v, dow])
    keys = sorted(solved)
    extra = {}
    if state:
        idx = pd.DatetimeIndex(rows["day"])
        extra = {"curve_state": np.array(rows["curve_state"], np.float32),
                 "marg_state": np.array(rows["marg_state"], np.float32),
                 "state_today": flags.reindex(idx).to_numpy(float),
                 "state_tomorrow": flags.reindex(idx + pd.Timedelta(days=1)).to_numpy(float)}
    if regime:
        extra["marg_regime"] = np.array(rows["marg_regime"], np.float32)
    np.savez_compressed(out, day=np.array(rows["day"]), level=np.array(rows["level"]),
                        curve=np.array(rows["curve"], np.float32), marg=np.array(rows["marg"], np.float32),
                        edges=np.array(rows["edges"], np.float32),
                        weeks=np.array([v[2] for v in solved.values()]), n_tables=len(solved),
                        grid_month=np.array([str(m) for m, _ in keys]), grid_level=np.array([g for _, g in keys]),
                        grid_curve=np.array([solved[k][0] for k in keys], np.float32), **extra)
    rec_path = out.with_suffix(".json")
    # the defaults write no record, unless one left at this name by another table would misdescribe this one
    if not params.is_default() or rec_path.exists():
        rec_path.write_text(json.dumps({**record, "file": out.name, "asked": asdict(params),
                                        "command": command(params, stat, hurdle, state, regime),
                                        "days": len(rows["day"]), "first": rows["day"][0], "last": rows["day"][-1],
                                        "built": datetime.now().isoformat(timespec="seconds")}, indent=1) + "\n")
    return out


def load(p) -> dict:
    """The per-day arrays, keyed by trading day, and each month's end-of-day curves on its level grid."""
    z = np.load(p)
    days = pd.to_datetime(z["day"].astype(str))
    grid: dict = {}
    for m, g, c in zip(z["grid_month"].astype(str), z["grid_level"], z["grid_curve"]):
        grid.setdefault(m, []).append((float(g), c))
    out = {"index": {d: i for i, d in enumerate(days)}, "level": z["level"], "curve": z["curve"], "marg": z["marg"],
           "edges": z["edges"], "grid": {m: sorted(v, key=lambda x: x[0]) for m, v in grid.items()}}
    for k in ("curve_state", "marg_state", "state_today", "state_tomorrow", "marg_regime"):
        if k in z.files:
            out[k] = z[k]
    return out


def curve_at(inputs: dict, month: str, dow: int, level: float) -> np.ndarray:
    """The end-of-day curve of a trading day starting on weekday dow, from month's table at a price level
    (interpolated on the month's grid, the nearest end beyond it)."""
    g = inputs["grid"][month]
    levels = np.array([x for x, _ in g])
    k = int(np.clip(np.searchsorted(levels, level, side="right") - 1, 0, len(levels) - 2))
    w = float(np.clip((level - levels[k]) / (levels[k + 1] - levels[k]), 0.0, 1.0))
    return (1 - w) * g[k][1][dow].astype(float) + w * g[k + 1][1][dow].astype(float)


def shifted(c: np.ndarray, q_mwh: float, shift: bool = True) -> np.ndarray:
    """A curve shifted past the q MWh already needed (the first q segments take the first segment's value)."""
    q = int(np.clip(round(q_mwh), 0, len(c))) if shift else 0
    return np.r_[np.full(q, c[0]), c[:len(c) - q]]


def terminal(inputs: dict, day: pd.Timestamp, q_mwh: float, shift: bool = True) -> np.ndarray:
    """The plan's end-of-day value: the day's curve, shifted past the q MWh ① and ② already need at 08:00."""
    return shifted(inputs["curve"][inputs["index"][pd.Timestamp(day).normalize()]].astype(float), q_mwh, shift)


B1_HURDLES = (0.0, 25.0, 50.0)  # round 4, B1 [fixed beforehand, reports/v2_plan.md section 11.3]


def b1() -> bool:
    """Round 4, B1 (no decision changes): the tables at hurdles 0, 25 and 50, checked (the default hurdle rebuilt
    bit for bit; every curve and offer value non-increasing in stored energy and not below 0; training windows before
    the month and not after TRAIN_END) and reported: the mean 08:00 curve at 5/10/20/30 MWh before and after the
    cutoff, with the buy limit 0.922 x value / 1.2 - h and the sell threshold max(150, 1.2 x value / 0.922 + 30 + h)."""
    from vpp import evaluate as ev
    ok = True
    check = PROCESSED / "v2_value_median_b1check.npz"
    build("median", 40.0, hurdle=bt.HURDLE, out=check)
    a, b = np.load(path()), np.load(check)
    diff = [k for k in a.files if not np.array_equal(a[k], b[k])]
    worst = max((float(np.max(np.abs(a[k].astype(float) - b[k].astype(float)))) for k in diff
                 if a[k].dtype.kind == "f" and a[k].shape == b[k].shape), default=0.0)
    print(f"{'PASS' if not diff else 'FAIL'}  hurdle {bt.HURDLE:g} rebuilt bit for bit: "
          f"{'all ' + str(len(a.files)) + ' arrays equal' if not diff else f'differ {diff}, largest {worst:.3g}'}")
    ok &= not diff
    a.close(), b.close()
    check.unlink()
    table = prices()
    for month in sv.MONTHS:
        end = min(month.start_time, ev.CUTOFF)
        used = table.index[(table.index >= end - sv.WINDOW) & (table.index < end) & (table.index <= ev.TRAIN_END)]
        ok &= bool(used.max() < month.start_time and used.max() <= ev.TRAIN_END)
    print(f"{'PASS' if ok else 'FAIL'}  training windows before each month and not after {ev.TRAIN_END.date()}")
    rows = []
    for h in (*B1_HURDLES, bt.HURDLE):
        z = load(build("median", 40.0, hurdle=h))
        flat = (np.diff(z["curve"], axis=1) <= 1e-6).all() and (np.diff(z["marg"], axis=3) <= 1e-6).all()
        pos = (z["curve"] >= 0).all() and (z["marg"] >= 0).all()
        print(f"{'PASS' if flat and pos else 'FAIL'}  hurdle {h:g}: curves and offer values non-increasing and >= 0")
        ok &= bool(flat and pos)
        days = pd.DatetimeIndex(list(z["index"]))
        for name, m in (("before cutoff", days < ev.CUTOFF), ("after cutoff", days >= ev.CUTOFF)):
            c = z["curve"][m].astype(float).mean(axis=0)
            for s in (5, 10, 20, 30):
                rows.append({"hurdle": h, "period": name, "stored_mwh": s, "value": c[s],
                             "buy_limit": 0.922 * c[s] / 1.2 - h, "sell_threshold": max(150.0, 1.2 * c[s] / 0.922 + 30 + h)})
    rep = pd.DataFrame(rows)
    rep.to_csv(bt.REPORTS / "v2_b1_values.csv", index=False)
    pd.set_option("display.width", 200)
    print(rep.pivot_table(index=["period", "stored_mwh"], columns="hurdle",
                          values=["value", "buy_limit", "sell_threshold"]).round(0).to_string())
    return ok


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--b1" in argv:
        sys.exit(0 if b1() else 1)
    stat = argv[argv.index("--stat") + 1] if "--stat" in argv else "median"
    hurdle = float(argv[argv.index("--hurdle") + 1]) if "--hurdle" in argv else bt.HURDLE
    params = TableParams(**{name: float(argv[argv.index(flag) + 1]) for flag, name in CLI.items() if flag in argv})
    p = build(stat, params.energy_mwh, "--rebuild" in argv, hurdle, state="--state" in argv or "--regime" in argv,
              regime="--regime" in argv, **{k: v for k, v in params.kwargs().items() if k != "energy_mwh"})
    z = load(p)
    lv = pd.Series(z["level"], index=list(z["index"]))
    print(f"{p.name}: {len(lv)} days; level ${lv.min():.0f}-{lv.max():.0f}/MWh (median {lv.median():.0f}); "
          f"solved for {params.describe()}")
    for d in ("2025-07-02", "2026-01-07", "2026-08-05"):
        i = z["index"][pd.Timestamp(d)]
        c = z["curve"][i]
        print(f"  {d} (L ${z['level'][i]:.0f}): 08:00 value $/MWh at 0/5/10/20/30/{len(c) - 1} MWh: "
              + " / ".join(f"{c[s]:.0f}" for s in (0, 5, 10, 20, 30, len(c) - 1)))


if __name__ == "__main__":
    main()
