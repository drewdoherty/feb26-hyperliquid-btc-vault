#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare multiple flow-based strategies vs buy-and-hold from $100")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--out-dir", default="reports/strategy_comparison")
    p.add_argument("--start-value", type=float, default=100.0)
    return p.parse_args()


def max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min())


def annualized_return(equity: pd.Series, periods_per_year: int = 365) -> float:
    n = len(equity)
    if n < 2:
        return 0.0
    total = float(equity.iloc[-1] / equity.iloc[0])
    years = n / periods_per_year
    if years <= 0:
        return 0.0
    return total ** (1 / years) - 1


def build_dataset(flow_csv: str, price_csv: str) -> pd.DataFrame:
    f = pd.read_csv(flow_csv)[["date", "net_flow_usd"]].copy()
    p = pd.read_csv(price_csv)[["date", "close"]].copy()

    f["date"] = pd.to_datetime(f["date"]).dt.date
    p["date"] = pd.to_datetime(p["date"]).dt.date

    df = pd.merge(f, p, on="date", how="inner").sort_values("date")
    df["flow_b"] = df["net_flow_usd"] / 1_000_000_000

    # Lag all signal inputs by one day to avoid lookahead.
    df["flow_lag1"] = df["flow_b"].shift(1)
    df["flow_ma3_lag"] = df["flow_b"].rolling(3).mean().shift(1)
    df["flow_ma7_lag"] = df["flow_b"].rolling(7).mean().shift(1)
    df["flow_ma20_lag"] = df["flow_b"].rolling(20).mean().shift(1)
    df["flow_std20_lag"] = df["flow_b"].rolling(20).std().shift(1)

    df["ret_1d"] = df["close"].pct_change()
    df["ret_5d_lag"] = (df["close"] / df["close"].shift(5) - 1.0).shift(1)
    df["ret_std20_lag"] = df["ret_1d"].rolling(20).std().shift(1)

    # Return earned from t to t+1 after taking signal at t.
    df["next_day_ret"] = df["close"].shift(-1) / df["close"] - 1.0

    df = df.dropna().reset_index(drop=True)
    return df


def strategy_positions(df: pd.DataFrame) -> dict[str, pd.Series]:
    eps = 1e-12
    z = (df["flow_lag1"] - df["flow_ma20_lag"]) / (df["flow_std20_lag"] + eps)
    mom = df["ret_5d_lag"] / (df["ret_std20_lag"] + eps)

    positions: dict[str, pd.Series] = {}
    positions["buy_hold"] = pd.Series(1.0, index=df.index)
    positions["flow_sign"] = np.sign(df["flow_lag1"]).astype(float)
    positions["flow_long_only"] = (df["flow_lag1"] > 0).astype(float)
    positions["flow_ma_cross"] = np.sign(df["flow_ma3_lag"] - df["flow_ma7_lag"]).astype(float)
    positions["flow_zscore_threshold"] = pd.Series(np.where(np.abs(z) > 0.5, np.sign(z), 0.0), index=df.index)
    positions["flow_strength_scaled"] = pd.Series(np.clip(df["flow_lag1"] / 0.20, -1.0, 1.0), index=df.index)
    positions["flow_momentum_combo"] = pd.Series(np.clip(np.tanh((0.7 * z + 0.3 * mom) / 2.0), -1.0, 1.0), index=df.index)

    return positions


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = build_dataset(args.flow_csv, args.price_csv)
    pos_map = strategy_positions(df)

    paths = pd.DataFrame({"date": pd.to_datetime(df["date"])})
    summary_rows: list[dict[str, float | int | str]] = []

    for name, pos in pos_map.items():
        ret = pos * df["next_day_ret"]
        equity = args.start_value * (1.0 + ret).cumprod()

        paths[name] = equity

        trades = int((pos.diff().abs().fillna(0) > 1e-9).sum())
        summary_rows.append(
            {
                "strategy": name,
                "final_value": float(equity.iloc[-1]),
                "annualized_return": annualized_return(equity),
                "max_drawdown": max_drawdown(equity),
                "trades": trades,
                "time_in_market_pct": float((pos.abs() > 0).mean()),
            }
        )

    summary = pd.DataFrame(summary_rows).sort_values("final_value", ascending=False)

    paths.to_csv(out_dir / "portfolio_paths_100usd.csv", index=False)
    summary.to_csv(out_dir / "strategy_summary.csv", index=False)

    # Plot portfolio evolution from $100.
    fig, ax = plt.subplots(figsize=(13, 7))
    for col in paths.columns:
        if col == "date":
            continue
        lw = 2.8 if col == "buy_hold" else 1.8
        alpha = 1.0 if col == "buy_hold" else 0.9
        ax.plot(paths["date"], paths[col], label=col, linewidth=lw, alpha=alpha)

    ax.set_title("Portfolio Evolution From $100: Strategy Variations vs Buy-and-Hold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Portfolio Value ($)")
    ax.grid(alpha=0.2)
    ax.legend(loc="upper left", ncol=2, fontsize=9)

    plt.tight_layout()
    plt.savefig(out_dir / "portfolio_paths_100usd.png", dpi=170)
    plt.close(fig)

    print(summary.to_string(index=False))
    print(f"\nSaved: {out_dir / 'portfolio_paths_100usd.csv'}")
    print(f"Saved: {out_dir / 'strategy_summary.csv'}")
    print(f"Saved: {out_dir / 'portfolio_paths_100usd.png'}")


if __name__ == "__main__":
    main()
