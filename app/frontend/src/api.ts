/* The backend's /api/v1 (app/backend/main.py). Missing values are null with a reason in `gaps`; never 0. */
export type Num = number | null;
export type Period = { kind: string; label: string; start: string; end: string; days: number; used_for_selection: boolean };
export type Check = { id: string; label: string; status: "pass" | "fail" | "not_checked"; worst: string | null; detail: string; slots: number[] };
export type Gap = { field: string; reason: string };

export type Plan = {
  day: string; weekday_label: string; basis: "plan"; period: Period; hot_season: string | null;
  dispatcher: { name: string; short: string }; asset: { power_mw: number; energy_mwh: number; round_trip: number; wear_aud_per_mwh: number };
  settings: { fcess: string[]; start_mwh: Num; export_mw: number; import_mw: number }; unvalidated: boolean;
  start_state: { stored_mwh: Num; source: string };
  plan: {
    first_interval: number; stored_at_0900_mwh: Num; battery_mw: Num[]; soc_mwh: Num[];
    fcess_mw: Record<"reg_raise" | "cont_raise" | "reg_lower" | "cont_lower", Num[]>; reserve_floor_mwh: Num[];
    action: string[]; value_at_forecast_aud: { energy: Num; regulation_energy: Num; fcess: Num; wear: Num; stored_end: Num; net: Num };
  };
  price: { forecast_p50: Num[]; forecast_p10: Num[] | null; forecast_p90: Num[] | null; spike_prob_ge_200: Num[] | null; actual_before_0900: Num[] };
  offers_0900: { rule: "value"; floor_aud_per_mwh: number; alpha: number; spare_mw: Num[]; raise_enabled_mw: Num[] }
    | { rule: "ladder"; by_halfhour: ({ mw: number; price_aud_per_mwh: number | "MAX" }[] | null)[]; ceiling_aud_per_mwh: number } | null;
  value_now: { stored_mwh: Num; top_mwh_aud_per_mwh: Num } | null;
  checks: Check[]; state: "PASS" | "BLOCKED"; blocked_halfhours: number[]; gaps: Gap[];
};

export type Outcome = {
  day: string; basis: "outcome"; unvalidated: boolean; battery_mw: Num[]; soc_mwh: Num[];
  fcess_mw: Record<string, Num[]>; offer_cleared_mw: Num[]; offer_sold_aud: Num[]; offer_price_live: Num[];
  price_actual: Num[]; price_actual_max_5min: Num[];
  money_aud: { energy: Num; fcess: Num; wear: Num; net: Num };
  baseline_same_day: { name: string; net_aud: Num } | null; baselines?: { name: string; net_aud: Num }[];
};

export type Uplift = { mean: Num; lo95: Num; hi95: Num; pct?: Num; days_better: number };
export type Evidence = {
  adopted: string; candidate: string; selection_rule: string;
  periods: { period: Period; per_day_aud: Record<string, Num>; uplift_vs_plan_only?: Uplift; candidate_minus_adopted: Uplift;
    share_of_prices_known?: Num; adopted_parts_aud: { energy: Num; fcess: Num; wear: Num } }[];
  forecast: { period: Period; mae_all_day: Num; mae_all_day_yesterday: Num; mae_17_21: Num; mae_17_21_yesterday: Num; change_pct: Num }[];
  limits: { period: Period; halfhours: number; over_power: number; soc_outside: number }[];
};

export type Window = { start: string | null; end: string | null; standby_from: string | null };
export type CallDay = {
  day: string; weekday_label: string; offer: "ref" | "custom"; offer_name: string; contract_year: string; hot_season: string | null; in_sample: boolean;
  forecast: { peak_mw: Num; threshold_mw: Num; dayahead_mw: Num; dayahead_threshold_mw: Num; fourth_highest_mw: Num; margin_mw: Num;
    flagged: boolean; routes: string[]; first_days_auto: boolean } | null;
  demand_forecast_mw: Num[] | null;
  model: { call: boolean; kind: string | null; reason: string } & Window;
  operator: { call: boolean; start: string | null } | null;
  decision: { call: boolean; source: "model" | "operator" } & Window;
  calls: { used_before: number; used_with_today: number; cap: number; hot_season_days_left: number };
  payment: { fleet_aud: Num; per_customer_aud: Num; billed_kwh_per_customer: Num; basis: string };
  enrolled: Num; responding: Num; start_options: string[]; event_h: number; standby_h: number; reserve_pct: Num;
  terms: { pay_c_per_kwh: Num; pay_aud_per_event: Num; max_calls: number; status: string };
  hindsight?: { actual_peak_mw: Num; demand_actual_mw: Num[]; official_peak_day: boolean; peak_intervals: string[];
    payment_settled: { fleet_aud: Num } | null };
};

export type Season = { season: string; days: { day: string; forecast_mw: Num; threshold_mw: Num; flagged: boolean; model_call: boolean;
  operator: { call: boolean } | null; called: boolean; actual_peak_mw: Num; official_peak_day: boolean | null; known: boolean }[];
  official_peak_days: string[] | null };

export type ValueRow = { offer: string; offer_name: string; contract_year: string; period: Period & { kind: string }; calls: number;
  operator_days: number; customer_limit_breaches: number; peak_days_covered: number | null; peak_days: number | null; enrolled: Num; responding: Num;
  parties_aud: { zenith: Num; customers: Num; retailer: Num; value_created: Num }; transfers_aud: Num;
  per_customer_aud: { zenith: Num; customers: Num };
  ledger: { item: string; label: string; kind: string; payer: string; receiver: string | null; aud: Num }[] };
export type ValueSummary = { scenario: string; difference_custom_minus_ref_aud: Num;
  offers: Record<"ref" | "custom", { name: string; status: string; status_note: string;
    terms: { pay_c_per_kwh: Num; pay_aud_per_event: Num; annual_fee_aud: Num; max_calls: number; event_h: number; reserve_pct: Num };
    model_calls: ValueRow; your_calls: ValueRow | null;
    expected_year: { zenith_aud: Num; customers_aud: Num; zenith_risk_adjusted_aud: Num; basis: string };
    holdouts: { held_out: string; chronological: boolean; held_out_profit: number; covered_held_out: number }[] }> };
export type Job = { id: number; state: "queued" | "running" | "done" | "failed" | "cancelled"; progress: number; message: string | null;
  result: { periods: { period: Period; per_day_aud: Num; default_per_day_aud: Num; difference_aud: Num }[] } | null };
export type Meta = { engines: Record<string, { state: string; error: string | null }>;
  storage: { first_day: string; last_day: string; days: number; dispatcher: { name: string; short: string } } | null };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch("/api/v1" + path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not JSON */ }
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

export type StorageSettings = { fcess: string[]; startMwh: number | null; exportMw: number | null; importMw: number | null };

const qs = (o: Record<string, string | number | null | undefined>) => {
  const p = Object.entries(o).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return p.length ? "?" + p.join("&") : "";
};
const ALL = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"];
const fcessQ = (s: StorageSettings) => (s.fcess.length === ALL.length && ALL.every((x) => s.fcess.includes(x)) ? undefined : s.fcess.join(","));

export const api = {
  meta: () => req<Meta>("/meta"),
  plan: (day: string, s: StorageSettings) =>
    req<Plan>(`/storage/days/${day}/plan` + qs({ fcess: fcessQ(s), start_mwh: s.startMwh, export_mw: s.exportMw, import_mw: s.importMw })),
  outcome: (day: string, s: StorageSettings) => req<Outcome>(`/storage/days/${day}/outcome` + qs({ fcess: fcessQ(s), start_mwh: s.startMwh })),
  evidence: () => req<Evidence>("/storage/evidence"),
  call: (day: string, offer: string, hindsight: boolean) => req<CallDay>(`/fleet/days/${day}/call` + qs({ offer, hindsight: hindsight ? "true" : undefined })),
  decide: (day: string, offer: string, call: boolean, start: string | null) =>
    req<CallDay>(`/fleet/days/${day}/decision`, { method: "PUT", body: JSON.stringify({ offer, call, start }) }),
  undo: (day: string, offer: string) => req<CallDay>(`/fleet/days/${day}/decision?offer=${offer}`, { method: "DELETE" }),
  resetDecisions: (offer: string) => req<{ cleared: string }>(`/fleet/decisions?offer=${offer}`, { method: "DELETE" }),
  season: (offer: string, through: string, hindsight: boolean) => req<Season>(`/fleet/season` + qs({ offer, through, hindsight: hindsight ? "true" : undefined })),
  value: () => req<ValueSummary>("/fleet/value"),
  evaluate: (body: Record<string, number | string | null>) => req<{ parties_aud: Record<string, Num>; calls: number; enrolled: Num; feasible: boolean;
    peak_days_covered: Record<string, number> }>("/fleet/offers/evaluate", { method: "POST", body: JSON.stringify(body) }),
  startBacktest: (fcess: string[]) => req<{ id: number }>("/jobs/storage-backtest", { method: "POST", body: JSON.stringify({ fcess }) }),
  job: (id: number) => req<Job>(`/jobs/${id}`),
  cancelJob: (id: number) => req<{ cancelled: boolean }>(`/jobs/${id}`, { method: "DELETE" }),
};
