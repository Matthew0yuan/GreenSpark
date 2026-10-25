"""Offline Codex check. This is not a Claude verification result."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import csv
import json
import math
from dataclasses import replace
from vpp.policy import policy_simulator as ps
from vpp.policy import policy_tests
from agents.check import check_numbers

OUT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports" / "policy"
def read_csv(name):
    with (REPORTS / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

saved = read_csv("policy_backtest.csv")
refinement = {r["scenario"]: r for r in read_csv("policy_refinement.csv")}
period = "样本内合同年 2025-07-01 至 2026-06-30（365 天）"
expected_period = "跨季构造年度期望：2023-24、2024-25、2025-26 热季；电能部分为样本内合同年2025-26"
numbers, summaries = [], []
def add(name, value, unit, when, source, formula="", status="推算", note=""):
    numbers.append(dict(name=name, value=value, unit=unit, formula=formula, period=when,
                        source=source, status=status, check="", note=note))

for sc in ("S2b", "S3"):
    r = next(r for r in saved if r["scenario"] == sc and r["case"] == "C optimised" and r["period"] == "in-sample")
    revenue_keys = ["revenue_wholesale", "revenue_capacity", "revenue_fcess"]
    cost_keys = ["compensation_pay_energy", "compensation_pay_event", "compensation_annual_fee",
                 "compensation_offset", "costs_ops_event", "costs_ops_customer", "costs_fixed"]
    cash = sum(float(r[k]) for k in revenue_keys) - sum(float(r[k]) for k in cost_keys)
    assert abs(cash - float(r["zenith_profit"])) < .01
    pol = ps.Policy(name="C local verification", comp_type=r["policy_comp_type"],
                    pay_per_kwh=float(r["policy_pay_per_kwh"]), pay_per_event=float(r["policy_pay_per_event"]),
                    annual_fee=float(r["policy_annual_fee"]), max_events=int(r["policy_max_events"]),
                    event_h=float(r["policy_event_h"]), standby_h=float(r["policy_standby_h"]),
                    reserve_soc=float(r["policy_reserve_soc"]))
    obl = ps.Obligations(name=sc, capacity="net", energy_to_zenith=(sc == "S2b"),
                         capacity_share=1.0 if sc == "S2b" else .5)
    fit = ps.fit_rule(pol, obl, ps.Participation())
    e = ps.evaluate_policy(pol, market_obligations=obl, fit=fit)
    assert abs(e.zenith_profit - float(r["zenith_profit"])) < .01
    assert abs(e.enrolled - float(r["enrolled"])) < 1e-6
    assert abs(e.customer_benefit - float(r["customer_benefit"])) < 1e-6
    exp_profit, risk_profit, enrolled = ps.fleet(fit, ps.Participation(), obl)
    assert abs(exp_profit - float(refinement[sc]["grid_profit"])) < .01
    assert abs(fit.zen - float(r["fit_zen"])) < 1e-6
    stressed_profit = exp_profit - 100000
    min_enrolled_raw = 100000 / fit.zen
    min_enrolled = math.ceil(min_enrolled_raw)
    src = "reports/policy/policy_backtest.csv，C optimised，in-sample"
    for k in revenue_keys + cost_keys:
        add(f"{sc}_{k}", float(r[k]), "A$", period, src, status="回测")
    expression = " + ".join("{" + sc + "_" + k + "}" for k in revenue_keys)
    expression += " - " + " - ".join("{" + sc + "_" + k + "}" for k in cost_keys)
    add(sc + "_FY_profit", cash, "A$", period, src, expression)
    add(sc + "_enrolled", enrolled, "户[报名]", expected_period, "固定中央参与曲线；原模型复算", status="假设")
    add(sc + "_expected_unit_profit", fit.zen, "A$/(户[报名]*年)", expected_period, "原模型fit_rule复现", status="回测")
    add(sc + "_expected_profit", exp_profit, "A$/年", expected_period, "原模型fleet复现",
        "{" + sc + "_enrolled} * {" + sc + "_expected_unit_profit}")
    add(sc + "_FY_unit_profit", e.zenith_per_customer, "A$/户[报名]", period, src,
        "{" + sc + "_FY_profit} / {" + sc + "_enrolled}")
    add(sc + "_FY_customer_benefit", e.customer_benefit, "A$/户[报名]", period, src, status="回测")
    add(sc + "_fixed_cost", 100000, "A$/年", expected_period, "额外固定成本情景", status="假设")
    add(sc + "_after_fixed", stressed_profit, "A$/年", expected_period, "保持策略、参与人数和单位利润不变",
        "{" + sc + "_expected_profit} - {" + sc + "_fixed_cost}")
    add(sc + "_expected_risk_profit", risk_profit, "A$/年", expected_period, "原模型fleet返回的下行情景值", status="回测")
    add(sc + "_risk_after_fixed", risk_profit - 100000, "A$/年", expected_period, "同一人数和策略，额外固定成本",
        "{" + sc + "_expected_risk_profit} - {" + sc + "_fixed_cost}")
    add(sc + "_minimum_enrolled_raw", min_enrolled_raw, "户[报名]", expected_period, "仅覆盖假设固定成本",
        "{" + sc + "_fixed_cost} / {" + sc + "_expected_unit_profit}")
    add(sc + "_minimum_enrolled_ceiling", min_enrolled, "户[报名]", expected_period, "向上取整minimum_enrolled_raw", note="单位利润固定；不构成获客预测")
    test = ps.evaluate_policy(pol, market_obligations=obl, fit=fit, period="test")
    assert test.activations == 0 and test.days == 114
    add(sc + "_test_profit", test.zenith_profit, "A$", "测试期2026-07-01至2026-10-22（114天），无热季", "原模型evaluate_policy复现", status="回测")
    summary = dict(scenario=sc, enrolled=enrolled, expected_profit=exp_profit, expected_unit_profit=fit.zen,
                   expected_risk_profit=risk_profit, FY_profit=cash, FY_unit_profit=e.zenith_per_customer,
                   FY_customer_benefit=e.customer_benefit, expected_customer_benefit=fit.cust,
                   after_fixed=stressed_profit, minimum_enrolled=min_enrolled, test_profit=test.zenith_profit,
                   rule=dict(theta=str(fit.rule.theta), ircr_q=fit.rule.ircr_q), accounting_difference=cash-float(r["zenith_profit"]))
    summaries.append(summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)

with (OUT / "NUMBERS.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
    w.writeheader()
    w.writerows(numbers)
(OUT / "LOCAL_RESULTS.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
checks = check_numbers(OUT / "NUMBERS.csv")
for level, message in checks:
    print(level, message, flush=True)
assert not any(level == "FAIL" for level, _ in checks), checks
test_exit = policy_tests.main()
assert test_exit == 0
print("LOCAL VERIFICATION COMPLETE; Claude verification remains blocked.", flush=True)
