# Variant Parameter Impact Flow

```mermaid
flowchart TD
    A["Market Data"] --> A1["IBIT net flow history"]
    A --> A2["BTC price history"]

    A1 --> B["Feature Builder"]
    A2 --> B

    B --> B1["Regime-normalized flow features\nflow_z20, flow_z60, flow_rel60, flow_trend_5_20"]
    B --> B2["Price context features\nret_1d_lag1, ret_5d_lag1, ret_std20_lag1, flow_x_mom"]

    C1["Parameter: test_start_date"] --> D["Out-of-sample segment\n(what dates are scored)"]
    C2["Parameter: train_lookback_days"] --> E["Training window selection\n(all history vs recent regime)"]
    C3["Parameter: retrain_every"] --> F["How often model weights refresh"]

    B1 --> G["MLP Model\n(StandardScaler + MLP)"]
    B2 --> G
    E --> G
    F --> G

    G --> H1["expected_return_pct"]
    G --> H2["confidence\n(from expected vs residual std)"]

    C4["Parameter: confidence_threshold"] --> I1{"confidence >= threshold?"}
    H2 --> I1

    C5["Parameter: min_abs_return_pct"] --> I2{"abs(expected_return_pct) >= min?"}
    H1 --> I2

    I1 -- "no" --> J0["Signal = FLAT"]
    I2 -- "no" --> J0
    I1 -- "yes" --> I2
    I2 -- "yes" --> J1["Directional signal\n(sign of expected_return_pct)"]

    C6["Parameter: max_position"] --> K["Position sizing\nintensity=min(abs(expected)/1,1)"]
    J1 --> K
    K --> L["target_position_btc"]
    J0 --> L

    C7["Parameter: tx_cost_bps"] --> M["Net return adjustment\n(turnover * tx_cost)"]
    L --> M
    D --> M

    M --> N["Portfolio path from $100"]
    N --> O["Metrics\nfinal value, drawdown, trade count"]
```

## Parameter-to-decision mapping

- `confidence_threshold` and `min_abs_return_pct` directly gate whether the strategy is flat or active.
- `max_position` changes trade size, not direction.
- `train_lookback_days` and `retrain_every` influence model outputs (`expected_return_pct`, `confidence`) by changing what data the model learns from and how quickly it adapts.
- `tx_cost_bps` does not change signal direction, but it changes net PnL and can flip a profitable gross strategy to unprofitable net.
- `test_start_date` defines the out-of-sample evaluation regime.
