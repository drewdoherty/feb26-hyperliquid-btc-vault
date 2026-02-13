#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUT = REPORTS / "visuals"
OUT.mkdir(parents=True, exist_ok=True)


def load_backtest() -> pd.DataFrame:
    p = REPORTS / "backtest_timeseries.csv"
    if not p.exists():
        raise FileNotFoundError(f"Missing {p}")
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"])
    return df


def plot_equity_drawdown(df: pd.DataFrame) -> Path:
    d = df.copy()
    d["roll_max"] = d["strategy_equity"].cummax()
    d["drawdown"] = d["strategy_equity"] / d["roll_max"] - 1.0

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax1.plot(d["date"], d["strategy_equity"], label="Strategy", linewidth=2)
    ax1.plot(d["date"], d["benchmark_equity"], label="Buy & Hold", linewidth=1.5, alpha=0.8)
    ax1.set_title("Equity Curve")
    ax1.set_ylabel("Growth of $1")
    ax1.legend()
    ax1.grid(alpha=0.2)

    ax2.fill_between(d["date"], d["drawdown"], 0, color="#d62828", alpha=0.35)
    ax2.set_title("Strategy Drawdown")
    ax2.set_ylabel("Drawdown")
    ax2.set_xlabel("Date")
    ax2.grid(alpha=0.2)

    out = OUT / "equity_and_drawdown.png"
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close(fig)
    return out


def plot_risk_regime(df: pd.DataFrame) -> Path:
    d = df.copy()
    window = 30
    d["strat_vol_30d"] = d["strategy_ret"].rolling(window).std() * np.sqrt(365)
    d["bench_vol_30d"] = d["benchmark_ret"].rolling(window).std() * np.sqrt(365)
    d["strat_sharpe_30d"] = (d["strategy_ret"].rolling(window).mean() / d["strategy_ret"].rolling(window).std()) * np.sqrt(365)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax1.plot(d["date"], d["strat_vol_30d"], label="Strategy vol", linewidth=2)
    ax1.plot(d["date"], d["bench_vol_30d"], label="Buy & Hold vol", linewidth=1.5)
    ax1.set_title("30D Annualized Volatility")
    ax1.set_ylabel("Vol")
    ax1.legend()
    ax1.grid(alpha=0.2)

    ax2.plot(d["date"], d["strat_sharpe_30d"], color="#2a9d8f", linewidth=2)
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_title("Strategy 30D Rolling Sharpe")
    ax2.set_ylabel("Sharpe")
    ax2.set_xlabel("Date")
    ax2.grid(alpha=0.2)

    out = OUT / "risk_regime.png"
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close(fig)
    return out


def plot_trade_timeline(df: pd.DataFrame) -> Path:
    d = df.copy()
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(d["date"], d["position_btc"], color="#264653", linewidth=1.8)

    longs = d[d["signal_side"] == "long"]
    shorts = d[d["signal_side"] == "short"]
    flats = d[d["signal_side"] == "flat"]

    ax.scatter(longs["date"], longs["position_btc"], s=14, color="#2a9d8f", label="Long")
    ax.scatter(shorts["date"], shorts["position_btc"], s=14, color="#e76f51", label="Short")
    ax.scatter(flats["date"], flats["position_btc"], s=8, color="#adb5bd", alpha=0.5, label="Flat")

    ax.set_title("Position Timeline and Signal States")
    ax.set_ylabel("Position (BTC)")
    ax.set_xlabel("Date")
    ax.legend(ncol=3, fontsize=9)
    ax.grid(alpha=0.2)

    out = OUT / "trade_timeline.png"
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close(fig)
    return out


def plot_variant_heatmap() -> Path | None:
    p = REPORTS / "variants_fast" / "variant_results.csv"
    if not p.exists():
        return None

    df = pd.read_csv(p)
    if "error" in df.columns:
        df = df[df["error"].isna()].copy()
    if df.empty:
        return None

    # Focus on one max_position to make a readable matrix.
    one = df[df["max_position"] == df["max_position"].max()].copy()
    if one.empty:
        return None

    pivot = one.pivot_table(
        index="confidence_threshold",
        columns="retrain_every",
        values="strategy_final",
        aggfunc="mean",
    ).sort_index()

    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(pivot.values, cmap="viridis", aspect="auto")

    ax.set_title("Variant Heatmap: Strategy Final Equity")
    ax.set_xlabel("retrain_every")
    ax.set_ylabel("confidence_threshold")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([str(c) for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{idx:.2f}" for idx in pivot.index])

    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            ax.text(j, i, f"{pivot.iloc[i, j]:.2f}", ha="center", va="center", color="white", fontsize=9)

    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label("Final Equity")

    out = OUT / "variant_heatmap.png"
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main() -> None:
    df = load_backtest()
    outputs = [
        plot_equity_drawdown(df),
        plot_risk_regime(df),
        plot_trade_timeline(df),
    ]
    heat = plot_variant_heatmap()
    if heat is not None:
        outputs.append(heat)

    for p in outputs:
        print(p)


if __name__ == "__main__":
    main()
