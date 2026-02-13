# Strategy Process Diagram (Detailed)

```mermaid
flowchart TD
    A["Daily Scheduler (cron)"] --> B["Data Update Layer"]
    B --> B1["IBIT flows (Farside)\nupdate_ibit_flows.py"]
    B --> B2["BTC price history\nfetch_btc_prices.py"]
    B1 --> C["Model Layer"]
    B2 --> C
    C --> C1["Feature engineering\nflow lags, moving averages,\nflow volatility, BTC return"]
    C1 --> C2["MLP forecast (48h)\nexpected_return_pct + confidence"]
    C2 --> D["Decision Layer"]

    D --> D1{"confidence < CONFIDENCE_THRESHOLD?"}
    D1 -- "yes" --> E0["Signal = FLAT\nTarget = 0 BTC"]
    D1 -- "no" --> D2{"abs(expected_return_pct) < 0.10%?"}
    D2 -- "yes" --> E0
    D2 -- "no" --> D3["Position sizing:\nintensity = min(abs(expected_return_pct)/1.0, 1.0)\ntarget = MAX_ABS_POSITION_BTC * intensity\nside = sign(expected_return_pct)"]
    D3 --> E1["Signal = LONG/SHORT\nTarget in [-MAX_ABS_POSITION_BTC, +MAX_ABS_POSITION_BTC]"]

    E0 --> F["Portfolio & Risk Layer"]
    E1 --> F
    F --> F1["Clamp target to hard limits\nclamp_target(...)"]
    F1 --> F2["Read current position from HL account"]
    F2 --> F3["delta = target - current"]
    F3 --> F4{"Trade notional >= MIN_TRADE_NOTIONAL_USD?"}
    F4 -- "no" --> G0["Skip rebalance\navoid dust/fee churn"]
    F4 -- "yes" --> F5{"DRY_RUN?"}
    F5 -- "yes" --> G1["Log intended order only"]
    F5 -- "no" --> G2["Execution:\nset leverage=1\nmarket_open(BTC perp)"]

    G0 --> H["Run report + logs"]
    G1 --> H
    G2 --> H
    H --> H1["run_daily JSON output"]
    H --> H2["logs/run_YYYY-MM-DD.log"]
    H --> H3["backtest reports (offline):\nbacktest_summary.json,\nbacktest_timeseries.csv,\nequity_curve.png,\nprediction_scatter.png"]
```

## Decision Logic Reference

- Forecast inputs: `expected_return_pct`, `confidence`.
- Trade decision:
  - `flat` if confidence is below threshold.
  - `flat` if absolute expected return is too small.
  - otherwise long/short by sign of expected return.
- Position sizing:
  - scales with forecast magnitude up to max position cap.
- Portfolio management:
  - hard cap on absolute BTC exposure,
  - minimum notional gate to avoid low-value churn,
  - 1x leverage intent for low-risk directional exposure.
