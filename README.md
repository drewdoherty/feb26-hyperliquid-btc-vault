# Hyperliquid BTC Vault Runner

End-to-end BTC vault system: IBIT flow ingestion, local neural-net forecast, strategy execution, simulation, visualization, and Hyperliquid testnet deployment checks.

## Important trading detail

If you need both long and short without leverage, use **BTC perpetual at 1x leverage**.
Pure spot cannot natively short.

This code is configured for `HL_ASSET=BTC` (perp), with `update_leverage(1, "BTC")` before trading.

## Setup

```bash
cd "/Users/andrewdoherty/Desktop/Coding/Crypto Trading/feb26"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

## Daily pipeline

```bash
./scripts/daily_pipeline.sh
```

Pipeline steps:

1. Update IBIT flows from Farside (`scripts/update_ibit_flows.py`)
2. Update BTC prices (`scripts/fetch_btc_prices.py`)
3. Generate forecast (`scripts/generate_forecast.py`)
4. Rebalance strategy (`scripts/run_daily.py`)
5. Append logs (`logs/run_YYYY-MM-DD.log`)

## Simulation + visualizations

```bash
PYTHONPATH=src ./scripts/simulate_strategy.py --flow-csv data/ibit_flows.csv --price-csv data/btc_prices.csv --out-dir reports
```

Outputs:

- `reports/backtest_timeseries.csv`
- `reports/backtest_summary.json`
- `reports/equity_curve.png`
- `reports/prediction_scatter.png`
- `reports/trade_blotter.csv`

### Multi-variant sweep (for trade frequency and parameter exploration)

```bash
PYTHONPATH=src ./scripts/simulate_variants.py --flow-csv data/ibit_flows.csv --price-csv data/btc_prices.csv --out-dir reports/variants_fast --confidence-thresholds 0.48,0.50,0.52 --min-abs-return-pcts 0.00,0.02,0.05 --max-positions 1.0 --retrain-every-options 3,7
```

Outputs:

- `reports/variants_fast/variant_results.csv`
- `reports/variants_fast/leaderboard_top20.csv`

## V2 Regime-Adaptive OOS Simulation

Run from a fixed out-of-sample start date (all prior data treated as training):

```bash
PYTHONPATH=src ./scripts/simulate_strategy_v2.py --flow-csv data/ibit_flows.csv --price-csv data/btc_prices.csv --out-dir reports/v2_sep2025 --test-start-date 2025-09-01 --confidence-threshold 0.52 --min-abs-return-pct 0.02 --retrain-every 3 --train-lookback-days 365 --tx-cost-bps 2
```

Key outputs:

- `reports/v2_sep2025/v2_summary.json`
- `reports/v2_sep2025/v2_timeseries.csv`
- `reports/v2_sep2025/v2_trade_blotter.csv`
- `reports/v2_sep2025/v2_portfolio_100usd.png`

Variant sweep:

```bash
PYTHONPATH=src ./scripts/simulate_strategy_v2_variants.py --flow-csv data/ibit_flows.csv --price-csv data/btc_prices.csv --out-dir reports/v2_variants_sep2025_fast --test-start-date 2025-09-01 --confidence-thresholds 0.50,0.52,0.55 --min-abs-return-pcts 0.00,0.02 --retrain-every-options 3,7 --train-lookback-days-options 0,365 --tx-cost-bps-options 2
```

## Decision Explainability (Per Variant)

Generate data->signal->decision traces for each V2 variant:

```bash
python ./scripts/explain_v2_variants.py --variants-dir reports/v2_variants_sep2025_fast --flow-csv data/ibit_flows.csv --price-csv data/btc_prices.csv
python ./scripts/cleanup_reports.py
```

Key inspection files:

- `reports/INDEX.md`
- `reports/v2_variants_sep2025_fast/v2_decision_index.md`
- `reports/v2_variants_sep2025_fast/v2_variant_decision_summary.csv`
- `reports/v2_variants_sep2025_fast/v2_variant_signal_drivers.csv`
- `reports/v2_variants_sep2025_fast/<variant>/v2_decision_trace.csv`

## Process diagram

- Markdown diagram: `docs/process_diagram.md`
- Parameter impact diagram: `docs/variant_parameter_flow.md`

## Testnet deployment

Dry-run deployment:

```bash
./scripts/deploy_testnet.sh
```

Live testnet deployment (submits real testnet orders):

```bash
./scripts/deploy_testnet.sh --live
```

Guardrails:

- Requires `HL_BASE_URL=https://api.hyperliquid-testnet.xyz`
- Runs preflight test (`scripts/testnet_smoke.py`) before pipeline/execution

## Automation schedule

Current cron schedule is daily at 09:15 local time.

Check schedule:

```bash
crontab -l
```

Change schedule:

```bash
CRON_HOUR=8 CRON_MINUTE=30 ./scripts/install_cron.sh
```

## Going live checklist

1. Keep `DRY_RUN=true` for several days and inspect logs.
2. Validate testnet with `./scripts/deploy_testnet.sh`.
3. Set valid `HL_ACCOUNT_ADDRESS`, `HL_VAULT_ADDRESS`, and `HL_SECRET_KEY` in `.env`.
4. Run `./scripts/deploy_testnet.sh --live` only after smoke test passes.
5. For mainnet later, switch `HL_BASE_URL=https://api.hyperliquid.xyz` and keep strict position/risk limits.
