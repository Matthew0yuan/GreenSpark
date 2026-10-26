"""The checks of PLAN.md on run_offers.py's output: V0b, V1 (with planted errors), V2 (with planted errors), V3 (replays
with scrambled prices), V4 and the other counts. Writes out/checks.json and prints a summary.

Run (repo root, after run_offers.py):  .venv/bin/python analysis/2026-10-26/market_offer/check_offers.py [--no-v3]
"""

import copy
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import offers as of  # noqa: E402

OUT = Path(__file__).parent / "out"
SCHEMA = ROOT / "data/raw/aemo_rtms/rtms_openapi.json"
TOL = 0.0015  # MW: three tranche edges rounded to 0.001
PERIODS = {"样本内": ("2025-04-01", "2026-06-30"), "测试期A": ("2026-07-01", "2026-10-15"),
           "测试期B": ("2026-10-16", "2026-10-22")}
LOCK = "rw50+state t5+roll+lock"
V3_SAMPLES, V3_SEED = 20, 0


def period(day: pd.Series) -> pd.Series:
    out = pd.Series("", index=day.index)
    for name, (a, b) in PERIODS.items():
        out[(day >= a) & (day <= b)] = name
    return out


def v0b() -> dict:
    ours = pd.read_csv(OUT / "daily.csv", parse_dates=["day"]).set_index("day")
    ref = pd.read_csv(ROOT / "reports/v2_g5_daily.csv", parse_dates=["day"])
    ref = ref[ref["strategy"] == LOCK].set_index("day")
    both = ours.index.intersection(ref.index)
    diff = {k: float((ours.loc[both, k] - ref.loc[both, k]).abs().max()) for k in ("total", "energy", "fcess", "soc_end")}
    return {"days": len(both), "from": str(both.min().date()), "to": str(both.max().date()), "max_abs_diff": diff,
            "pass": len(both) > 0 and max(diff.values()) <= 1e-6}


def v1() -> dict:
    v = pd.read_csv(OUT / "validation.csv")
    out = {r: {"days": int((v["request"] == r).sum()), "requests_with_errors": int(((v["request"] == r) & (v["errors"] > 0)).sum()),
               "errors": int(v.loc[v["request"] == r, "errors"].sum()),
               "dis": int(v.loc[v["request"] == r, "dis"].sum())} for r in ("gate", "0900")}
    out["pass"] = all(out[r]["errors"] == 0 for r in ("gate", "0900"))
    return out


def planted(root: dict, P: float) -> dict:
    """V1c: each planted error must be caught by the schema or the rules."""
    base = json.loads((OUT / "rtms_2026-10-20_gate.json").read_text())
    clean = of.schema_errors(base, root["createSubmissionJsonRequest"], root) + of.rule_errors(base, P)
    td = lambda r: r["submission"]["variation"]["tradingDays"][0]  # noqa: E731
    ivs = lambda r: td(r)["energy"]["facilities"][0]["dispatchIntervals"]  # noqa: E731
    both = next(k for k, it in enumerate(ivs(base)) if any(x["quantity"] < 0 for x in it["tranches"])
                and any(x["quantity"] > 0 for x in it["tranches"]))
    many = next(k for k, it in enumerate(ivs(base)) if len(it["tranches"]) >= 3)

    def numeric(it):  # the index of a tranche with a numeric price after one
        return next(k for k in range(1, len(it["tranches"])) if not isinstance(it["tranches"][k]["price"], str))

    def m_eleven(r):
        it = ivs(r)[many]
        last = it["tranches"][-1]
        it["tranches"] = it["tranches"][:-1] + [dict(last, tranche=len(it["tranches"]) + k) for k in range(11)]

    def m_swap(r):
        tr = ivs(r)[many]["tranches"]
        k = numeric(ivs(r)[many])
        tr[k]["price"] = -1000.0 if isinstance(tr[k - 1]["price"], str) else tr[k - 1]["price"] - 1.0

    def m_dup(r):
        tr = ivs(r)[many]["tranches"]
        k = numeric(ivs(r)[many])
        tr[k]["price"] = -1000.0 if tr[k - 1]["price"] == "MIN" else tr[k - 1]["price"]

    def m_cents(r):
        it = ivs(r)[many]
        it["tranches"][numeric(it)]["price"] += 0.005

    def m_ceiling(r):
        it = ivs(r)[many]
        it["tranches"][-1]["price"] = of.ceiling("2026-10-20") + 1.0

    def m_min(r):
        ivs(r)[many]["tranches"][1]["price"] = "MIN"

    def m_side(r):  # a withdrawal tranche priced above an injection one, prices still rising
        tr = ivs(r)[both]["tranches"]
        k = max(k for k, x in enumerate(tr) if x["quantity"] < 0)
        tr[k]["quantity"], tr[k + 1]["quantity"] = tr[k + 1]["quantity"], tr[k]["quantity"]

    def m_sum(r):
        ivs(r)[many]["maxInjectionCapacity"] = round(ivs(r)[many]["maxInjectionCapacity"] + 0.5, 3)

    def m_zero(r):
        it = ivs(r)[many]
        it["tranches"].insert(1, {"tranche": 0, "quantity": 0.0, "price": -999.0, "capacityType": "IN-SERVICE"})
        for k, x in enumerate(it["tranches"]):
            x["tranche"] = k + 1

    def m_trapezium(r):
        it = td(r)["regulationRaise"]["facilities"][0]["dispatchIntervals"][100]
        it["lowBreakpoint"] = it["highBreakpoint"] + 1.0

    def m_fcess_price(r):
        it = td(r)["regulationRaise"]["facilities"][0]["dispatchIntervals"][100]
        it["tranches"][0]["price"] = of.fcess_ceiling("2026-10-20")["reg_raise"] + 1.0

    def m_missing(r):
        del ivs(r)[many]["inflexibleFlag"]

    def m_step(r):
        ivs(r)[many]["tranches"][0]["quantity"] += 0.0005

    plants = {"11 段": m_eleven, "价格不递增": m_swap, "价格重复": m_dup, "价格不是整分": m_cents,
              "价格超过上限": m_ceiling, "MIN 不在第一段": m_min, "充电价不低于放电价": m_side,
              "数量之和不等于能力": m_sum, "0 MW 段": m_zero, "FCESS 梯形顺序错": m_trapezium,
              "FCESS 价格超过上限": m_fcess_price, "缺必填字段": m_missing, "数量不是 0.001 的倍数": m_step}
    caught = {}
    for name, f in plants.items():
        r = copy.deepcopy(base)
        f(r)
        errs = of.schema_errors(r, root["createSubmissionJsonRequest"], root) + of.rule_errors(r, P)
        caught[name] = {"caught": bool(errs), "first": errs[0] if errs else ""}
    return {"clean_errors": len(clean), "plants": caught, "pass": not clean and all(c["caught"] for c in caught.values())}


def v2(st: pd.DataFrame) -> dict:
    strict = (st["price"] < st["ceiling"]) & ~st["charge_over"]
    diff = (st["exec_mw"] - st["want"]).abs()
    bad = strict & (diff > TOL)
    sold = st["want"] > st["plan_exec"].clip(lower=0) + 1e-9
    out = {"steps": len(st), "strict": int(strict.sum()), "mismatch": int(bad.sum()),
           "share_ok": float(1 - bad.sum() / strict.sum()), "max_diff_strict": float(diff[strict].max()),
           "outside": {"价格到了上限": int((st["price"] >= st["ceiling"]).sum()),
                       "计划充电超过充电能力": int(st["charge_over"].sum())},
           "outside_mismatch": int((~strict & (diff > TOL)).sum()),
           "ladder_sold_steps": {p: int((sold & (st["period"] == p)).sum()) for p in PERIODS},
           "non_cent_prices": int(((st["price"] * 100 - (st["price"] * 100).round()).abs() > 1e-6).sum()),
           "planted_price_mismatch": int((strict & ((st["bad_price_mw"] - st["want"]).abs() > TOL)).sum()),
           "planted_mw_mismatch": int((strict & ((st["bad_mw_mw"] - st["want"]).abs() > TOL)).sum()),
           "examples": st.loc[bad, ["day", "step", "price", "want", "exec_mw"]].head(10).astype(str).to_dict("records")}
    out["pass"] = out["mismatch"] == 0 and out["planted_price_mismatch"] > 0 and out["planted_mw_mismatch"] > 0
    return out


def _intervals(col: of.Collector, day, upto: int | None = None, after: int | None = None, leaked: bool = False):
    """{DI: JSON of its energy (and FCESS) offer} for DIs at most upto (or above after)."""
    out = {}
    src = col.leaked[day] if leaked else col.submitted[day]
    for di, v in src.items():
        if (upto is not None and di > upto) or (after is not None and di <= after):
            continue
        o, f = (v, None) if leaked else v
        out[di] = json.dumps([of.energy_interval(di, o)] + ([of.ess_interval(di, f[k]) for k in of.SERVICES] if f else []))
    return out


def v3() -> dict:
    """Replay sampled days with the prices from Gate Closure on scrambled: the Gate Closure offers up to the DI must not
    change (and later ones should; a version priced from the DI's own price must)."""
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    with open(OUT / "start_state.pkl", "rb") as f:
        start = pickle.load(f)
    sc, cfg = vb.m1(), vb.G5B
    bat = hs.scenario(sc, cfg).asset.battery
    rng = np.random.default_rng(V3_SEED)
    picks = [(int(k), int(i)) for k, i in zip(rng.integers(0, len(days), V3_SAMPLES),
                                              rng.integers(of.FORWARD_AT + of.GATE, dp.N * dp.STEPS, V3_SAMPLES))]
    daily = pd.read_csv(OUT / "daily.csv", parse_dates=["day"]).set_index("day")
    rows = []
    for n, (k, i) in enumerate(picks):
        day = days[k]["day"]
        soc0, mtd0 = start[day]

        def scramble(d, kw, i=i, n=n):
            g = np.random.default_rng(1000 + n)
            at, slot = i - of.GATE, (i - of.GATE) // dp.STEPS
            out = {}
            for key, cut in (("p5", at), ("rtp", slot)):
                x = np.array(kw[key], float)
                x[cut:] = g.permutation(x[cut:])
                out[key] = x
            for key, cut in (("p5_f", at), ("act_f", slot)):
                if kw.get(key) is not None:
                    out[key] = {p: np.r_[np.asarray(v, float)[:cut], g.permutation(np.asarray(v, float)[cut:])]
                                for p, v in kw[key].items()}
            return out

        runs = {}
        for name, decide in (("base", None), ("scrambled", scramble)):
            bt._init_worker(days[k:k + 3], aux)
            col = of.Collector(bat, leak=True)
            res = list(hs.route_days(sc, cfg, soc0, mtd0, until=day, decide=decide, on_step=col))
            runs[name] = (col, res[0][2])
        (b, sb), (s, _) = runs["base"], runs["scrambled"]
        di = i + 1
        same = _intervals(b, day, upto=di) == _intervals(s, day, upto=di)
        later = _intervals(b, day, after=di + of.GATE) != _intervals(s, day, after=di + of.GATE)
        leak = _intervals(b, day, upto=di, leaked=True) != _intervals(s, day, upto=di, leaked=True)
        rows.append({"day": str(day.date()), "di": di, "same_up_to_di": same, "later_changed": later,
                     "leak_caught": leak, "replay_equals_run": bool(abs(sb["total"] - daily.loc[day, "total"]) < 1e-9
                                                                    and abs(sb["soc_end"] - daily.loc[day, "soc_end"]) < 1e-9)})
    r = pd.DataFrame(rows)
    out = {"samples": rows, "same": int(r["same_up_to_di"].sum()), "later_changed": int(r["later_changed"].sum()),
           "leak_caught": int(r["leak_caught"].sum()), "replay_equals_run": int(r["replay_equals_run"].sum())}
    out["pass"] = out["same"] == V3_SAMPLES and out["later_changed"] >= 15 and out["leak_caught"] >= 1
    return out


def v4(st: pd.DataFrame) -> dict:
    """The Gate Closure offers at the actual price against what the run did, by cause (the first that applies)."""
    diff = st["sub_mw"] - st["pw"]
    off = diff.abs() > TOL
    at_limit = (st["price"] >= st["ceiling"]) | (st["price"] <= of.FLOOR)
    plan = (st["plan_sub"] - st["plan_exec"]).abs() > 1e-3
    room = (st["sellable_sub"] - st["sellable_exec"]).abs() > 1e-3
    joint = st["sold"].notna() & (st["sold"] > st["pw_offer"] + 1e-9)
    caps = (st["pw"] - st["pw_offer"]).abs() > 1e-9
    cause = np.select([at_limit, plan, room, joint, caps],
                      ["价格到了上下限", "截止后计划改了", "可卖量不同（存电、预算、负荷）", "FCESS 联合检查没让卖",
                       "合规留电、电表上限或连接点"], "其他")
    st = st.assign(diff=diff, off=off, cause=cause, mwh=diff * dp.DT5, money=diff * dp.DT5 * st["price"])
    out = {}
    for p in PERIODS:
        g = st[(st["period"] == p) & st["off"]]
        n_days = int(st.loc[st["period"] == p, "day"].nunique())
        out[p] = {"days": n_days, "steps": int((st["period"] == p).sum()), "steps_off": len(g),
                  "abs_mwh": float(g["mwh"].abs().sum()), "net_mwh": float(g["mwh"].sum()),
                  "money_aud": float(g["money"].sum()), "money_aud_per_day": float(g["money"].sum() / max(n_days, 1)),
                  "by_cause": {c: {"steps": int(len(x)), "abs_mwh": float(x["mwh"].abs().sum()),
                                   "money_aud": float(x["money"].sum())} for c, x in g.groupby("cause")}}
    return out


def others(st: pd.DataFrame) -> dict:
    neg = (st["price"] < 0) & (st["pw"] > 1e-9) & (st["plan_exec"] > 1e-9)
    out = {"max_tranches": int(st["sub_n"].max()), "merged": int(st["sub_merged"].sum()),
           "tranches_hist": {int(k): int(v) for k, v in st["sub_n"].value_counts().sort_index().items()}}
    for p in PERIODS:
        g = st["period"] == p
        out[p] = {"neg_price_plan_discharge_steps": int((neg & g).sum()),
                  "neg_price_plan_discharge_mwh": float((st.loc[neg & g, "pw"] * dp.DT5).sum()),
                  "raise_trapezium_out": int((st["raise_out"] & g).sum()),
                  "lower_trapezium_out": int((st["lower_out"] & g).sum()),
                  "steps_with_raise": int(((st["raise_q"] > 0) & g).sum()),
                  "steps_with_lower": int(((st["lower_q"] > 0) & g).sum())}
    return out


def main(argv: list[str]) -> None:
    root = json.loads(SCHEMA.read_text())["components"]["schemas"]
    P = bt.Q2B_ASSET.battery.power_mw
    st = pd.read_parquet(OUT / "steps.parquet")
    st["day"] = pd.to_datetime(st["day"])
    st["period"] = period(st["day"])
    res = {"V0b": v0b(), "V1": v1(), "V1c": planted(root, P), "V2": v2(st), "V4": v4(st), "others": others(st)}
    if "--no-v3" not in argv:
        res["V3"] = v3()
    (OUT / "checks.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    for k in ("V0b", "V1", "V1c", "V2", "V3"):
        if k in res:
            print(f"{'PASS' if res[k]['pass'] else 'FAIL'}  {k}")
    print(json.dumps({k: v for k, v in res.items() if k not in ("V1c",)}, indent=1, ensure_ascii=False, default=str)[:6000])


if __name__ == "__main__":
    main(sys.argv[1:])
