"""Plain-Chinese account of one decision (reports/v2_plan.md, step 7; FR-10).

Every number in the text is formatted through Facts, which keeps what each one is and where it came from (the
decision's record and the outputs computed from it), so check_numbers can confirm the text holds no number that was
not computed: no reason is written that the calculation did not give.
"""

import re

import numpy as np

from vpp.v2 import outputs as op

KIND = {"morning": "早上的过渡计划（预测公布之前）", "reveal": "预测公布后的重排", "scheduled": "定时重排",
        "trigger": "挂单成交后的重排"}
NUMBER = re.compile(r"[-−+]?\d[\d,]*(?:\.\d+)?%?")
TIME = re.compile(r"\d{1,2}:\d{2}")


class Facts:
    """The numbers an explanation uses, each formatted once: text -> [(what it is, value)]."""

    def __init__(self):
        self.used: dict[str, list] = {}

    def __call__(self, name: str, value: float, fmt: str = "{:,.0f}") -> str:
        text = fmt.format(value)
        self.used.setdefault(text.replace("−", "-"), []).append((name, float(value)))
        return text


def check_numbers(lines: list[str], facts: Facts) -> list[str]:
    """Numbers in the text that no fact produced (empty when every number is accounted for)."""
    bad = []
    for line in lines:
        for token in NUMBER.findall(TIME.sub(" ", line)):
            if token.replace("−", "-") not in facts.used:
                bad.append(f"{token!r} in: {line}")
    return bad


def explain(s: op.Snapshot, prev: op.Snapshot | None = None) -> tuple[list[str], Facts]:
    f = Facts()
    rec, eta = s.rec, s.bat.eta
    lines = [f"{op.clock(s.t0)} {KIND.get(rec['kind'], rec['kind'])}：从 {f('stored now', rec['stored'], '{:.1f}')} MWh "
             f"出发，排到次日 08:00。"]
    one = f("one MWh", 1, "{:.0f}")
    vn = op.value_now(s)
    if vn is not None:
        note = f"表里每卖 {one} MWh 已扣损耗和安全边际"
        how = (f"，其中存电价值表给 ${f('table value now', vn['table'])}（{note}），守约和工厂这部分电每 MWh 再加 "
               f"${f('protected value', vn['extra'])}（少存会多付的网络费或容量费）" if vn["extra"] > 0
               else f"（按存电价值表，{note}）")
        lines.append(f"{vn['time']} 时电池里最上面这 {one} MWh 属于 {vn['layer']}：留着不卖，它值 "
                     f"${f('top MWh worth now', vn['worth'])}/MWh{how}，"
                     f"折合每 kWh 约 {f('top kWh worth now (c)', vn['c_per_kwh'], '{:.1f}')} 分。")
    ch = op.charging_plan(s)
    if len(ch):
        grid, stored_in, cost = ch["grid_mwh"].sum(), ch["stored_mwh"].sum(), ch["cost"].sum()
        lines.append(f"充电：在 {ch['time'].iloc[0]}–{op.clock(int(ch['slot'].iloc[-1]) + 1)} 之间，计划从电网买 "
                     f"{f('grid MWh', grid, '{:.1f}')} MWh，存进 {f('stored MWh', stored_in, '{:.1f}')} MWh，预计花 "
                     f"${f('charging cost', cost)}，平均 ${f('average price', cost / grid)}/MWh。")
        parts = ch.groupby("layer")["stored_mwh"].sum()
        lines.append("这些电填的是：" + "，".join(f"{k} {f('layer ' + k, v, '{:.1f}')} MWh" for k, v in parts.items()) + "。")
        opp = ch[ch["layer"] == "③ 机会电"]
        if len(opp):
            last = opp.iloc[-1]
            worth = s.curve_value(last["stored_after"])
            cost_per = last["price"] / eta
            why = "，所以不再多充" if worth <= cost_per else ""
            lines.append(f"③ 机会电充到 {f('stored after', last['stored_after'], '{:.1f}')} MWh 为止：再多存 {one} MWh，存到明早约值 "
                         f"${f('next MWh worth', worth)}/MWh；{last['time']} 的电价 ${f('price', last['price'])}/MWh，存进 {one} MWh "
                         f"的成本是 ${f('cost per stored MWh', cost_per)}（电价 ÷ {f('efficiency', eta, '{:.3f}')}）{why}。")
    else:
        lines.append("充电：这份计划不充电。")
    rp = op.reserve_plan(s)
    end = rp[rp["time"] == "次日 08:00"]
    if len(end):
        e = end.iloc[0]
        lines.append(f"留电：明早 08:00 计划存 {f('stored at 08:00', e['stored'], '{:.1f}')} MWh。其中强制预留 "
                     f"{f('mandatory', e['mandatory'], '{:.1f}')} MWh，工厂备电 {f('site reserve', e['site'], '{:.1f}')} MWh，"
                     f"机会电 {f('opportunity', e['opportunity'], '{:.1f}')} MWh；再多存 {one} MWh 值 "
                     f"${f('next MWh at 08:00', e['next_mwh_worth'])}/MWh，折合每 kWh 约 "
                     f"{f('next kWh at 08:00 (c)', e['next_mwh_worth'] / 10.0, '{:.1f}')} 分。")
    if (rec.get("site_total_mwh") or 0) > 0:
        ircr = rec.get("site_ircr_mwh") or 0.0
        lines.append(f"工厂备电是给明天的：明天负荷超过 CMD 的部分{'，加上可能的全网高峰时段' if ircr > 0 else ''}，共要明早存好 "
                     f"{f('site need', rec['site_total_mwh'], '{:.1f}')} MWh"
                     + (f"（其中高峰时段 {f('site IRCR part', ircr, '{:.1f}')} MWh）" if ircr > 0 else "")
                     + f"。少存 {one} MWh，明天大约多交 ${f('site value', rec['site_cmd_value'])} 网络费，所以几乎不卖。")
    bp = op.bidding_plan(s)
    if len(bp):
        b = bp.iloc[0]
        line = (f"报价（{op.clock(int(b['slot']))} 起）：最上面 {one} MWh 挂 ${f('first MWh offer', b['first_mwh'])}/MWh")
        if np.isfinite(b["for_5_mwh"]):
            line += f"，卖到第 {f('5 MWh', 5, '{:.0f}')} MWh 要 ${f('offer for 5 MWh', b['for_5_mwh'])}/MWh"
        if b["protected_mwh"] > 1e-6:
            line += (f"；工厂要用的 {f('protected MWh', b['protected_mwh'], '{:.1f}')} MWh 门槛不低于 "
                     f"${f('protected offer', b['protected_from'])}")
        if np.isfinite(b["first_fill_freq"]):
            line += f"。过去一年这个半小时价格到过第一档的频率约 {f('fill frequency', b['first_fill_freq'], '{:.0%}')}（未校准，只作参考）"
        lines.append(line + "。电越少，每 MWh 挂得越高。")
    elif not s.ladder and rec["kind"] != "morning":
        lines.append("报价：挂单用 value 规则，价格第一次超过最低挂单价时才现算卖价，所以不能提前列出。")
    m = op.money(s)
    lines.append(f"这份计划自己估的钱（到明早）：电能买卖净额 {'−' if m['energy'] < 0 else ''}${f('energy', abs(m['energy']))}"
                 f"{'（买电多于卖电）' if m['energy'] < 0 else ''}，FCESS ${f('fcess', m['fcess'])}，损耗 "
                 f"${f('wear', m['wear'])}，剩下的电按存电价值算 ${f('stored value', m['stored'])}。")
    rk = op.risk(s)
    if rk is not None:
        lines.append(f"风险：把这份计划原样放进 {f('scenarios', rk['scenarios'], '{:.0f}')} 种价格走势，平均 ${f('risk mean', rk['mean'])}，"
                     f"最差的 {f('worst share', 0.05, '{:.0%}')} 是 ${f('risk worst', rk['worst_5pct'])}（不含实时挂单，只看计划本身）。")
    if prev is not None:
        d_end = s.arr("soc")[-1] - prev.arr("soc")[-1]
        before = op.charging_plan(prev)
        d_ch = (ch["grid_mwh"].sum() if len(ch) else 0.0) - (before["grid_mwh"].sum() if len(before) else 0.0)
        lines.append(f"和上一份计划（{op.clock(prev.t0)}）比：明早计划存电变化 {f('change at 08:00', d_end, '{:+.1f}')} MWh，"
                     f"计划充电变化 {f('change in charging', d_ch, '{:+.1f}')} MWh。")
    return lines, f
