# GreenSpark: battery trading and customer-battery calls in the WA electricity market

A personal project built for the GreenSpark Hackathon 2026 (Challenge 02, Western Australia's Wholesale Electricity
Market).

## The problem

Zenith is an electricity retailer with a 10 MW / 40 MWh battery behind a factory's meter. It also wants to pay its
customers to use their batteries. Every morning at 09:00 it has to decide two things:

1. **What its own battery does today.** Grid-service income has collapsed, so the money is now in rare price spikes,
   and no forecast can tell which half-hour a spike will hit.
2. **Whether to call customer batteries this evening.** Every call costs a payment to the customers, and calls are
   limited.

## What it does

- Forecasts today's half-hourly prices at 09:00, using only data public by then.
- Prices each stored MWh by what it is worth to keep, and offers the spare energy at that price. If a spike comes, the
  offer sells; if not, the energy stays. The day is re-planned every 30 minutes.
- Calls customer batteries only on likely system-peak days, the days that set the capacity charge, within a budget of
  45 calls a year. Trading the energy alone never covers the customers' payment, so it never calls on price.
- Explains every decision in plain English and replays what actually happened, in a web app.

## Results

| What | Result | Period |
|---|---|---|
| Price forecast error, same day, issued at 09:00 (mean absolute error) | A$19.2/MWh, against A$28.0 for "same as yesterday": 31% lower | Test: 1 Jul – 22 Oct 2026, 114 days |
| Battery market income, as a share of a plan made with the day's actual prices | 68% (first version 39%, the 09:00 plan alone 7%) | Test: 114 days |
| Profit per day, current dispatcher minus first version, at the factory site | +A$440 (95% interval +A$247 to +A$626) | Training: 1 Apr 2025 – 30 Jun 2026, 456 days |
| Customer-battery calls | 24 calls; 3 of the season's 4 official peak days caught | Summer 2025-26 (training) |

Everything was built and chosen on the training period; the test period is reported only. The plan made with actual
prices is a reference, not a ceiling: on some spike days the current dispatcher earns more.

## Models

| Part | Method |
|---|---|
| Price forecast | LightGBM on the error of AEMO's own pre-dispatch forecast, with ECMWF weather, averaged with a LightGBM model that does not use AEMO's forecast; monthly walk-forward refits |
| Spike risk | LightGBM classifier: the chance of a price of A$200/MWh or more in each half-hour |
| Value of stored energy | Dynamic programming over a year of half-hourly price movements, scaled to the last 28 days' price level |
| Day plan | Mixed-integer linear program (SciPy `milp`, HiGHS): power and energy limits, round-trip losses, battery wear, the factory's demand cap, grid services |
| Sell offers | Layered offers at the value of keeping each MWh, plus wear and a safety margin |
| Customer-battery calls | Call when AEMO's 09:00 demand forecast tops the season's 4th-highest daily peak so far, less a margin learned from past forecast errors |

## Stack

- **Models:** Python 3.13, pandas, NumPy, PyArrow, LightGBM, SciPy (HiGHS), scikit-learn, Matplotlib; uv
- **Backend:** FastAPI and Uvicorn, with the models in worker processes
- **Frontend:** React 19, TypeScript, Vite, TanStack Query, hand-drawn SVG charts
- **Tests:** pytest, Playwright
- **Deploy:** Docker, Cloudflare Tunnel
- **Data:** AEMO's public WEM data (5-minute prices, pre-dispatch, demand, grid-service prices), Open-Meteo (ECMWF)

## Run it

With [uv](https://docs.astral.sh/uv/) and Node 22:

```
uv sync --extra app
cd app/frontend && npm ci && npm run build && cd ../..
uv run python -m app.backend
```

Then open http://127.0.0.1:8000. The models take about 30 seconds to load. On a fresh clone the first start also builds
the storage-value tables, which are not in git, so it takes longer.

## Limits

- The factory's load is synthetic.
- The customer-battery money uses a household battery model; Zenith's real customers are businesses.
- Customer exports reduce the capacity charge under an assumed 2027 rule.
- The test period has no summer, so the capacity-charge value is checked on past summers only.

## Layout

`app/` the web app · `vpp/` the models · `scripts/` data downloads · `reports/` result tables · `analysis/` dated
experiments: scripts and their outputs
