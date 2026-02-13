#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Explain data -> signal -> decision for each V2 variant")
    p.add_argument("--variants-dir", default="reports/v2_variants_sep2025_fast")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    return p.parse_args()


def build_feature_dataset(flow_csv: str, price_csv: str) -> pd.DataFrame:
    f = pd.read_csv(flow_csv)[["date", "net_flow_usd"]].copy()
    p = pd.read_csv(price_csv)[["date", "close"]].copy()

    f["date"] = pd.to_datetime(f["date"]).dt.date
    p["date"] = pd.to_datetime(p["date"]).dt.date

    df = pd.merge(f, p, on="date", how="inner").sort_values("date")
    df["flow_b"] = df["net_flow_usd"] / 1_000_000_000
    df["flow_lag1"] = df["flow_b"].shift(1)

    eps = 1e-9
    df["flow_mean20"] = df["flow_lag1"].rolling(20).mean()
    df["flow_std20"] = df["flow_lag1"].rolling(20).std()
    df["flow_mean60"] = df["flow_lag1"].rolling(60).mean()
    df["flow_std60"] = df["flow_lag1"].rolling(60).std()
    df["flow_abs_med60"] = df["flow_lag1"].abs().rolling(60).median()

    df["flow_z20"] = (df["flow_lag1"] - df["flow_mean20"]) / (df["flow_std20"] + eps)
    df["flow_z60"] = (df["flow_lag1"] - df["flow_mean60"]) / (df["flow_std60"] + eps)
    df["flow_rel60"] = df["flow_lag1"] / (df["flow_abs_med60"] + eps)
    df["flow_trend_5_20"] = (
        df["flow_lag1"].rolling(5).mean() - df["flow_lag1"].rolling(20).mean()
    ) / (df["flow_std60"] + eps)

    df["ret_1d_pct"] = df["close"].pct_change() * 100.0
    df["ret_1d_lag1"] = df["ret_1d_pct"].shift(1)
    df["ret_5d_lag1"] = ((df["close"] / df["close"].shift(5)) - 1.0).shift(1) * 100.0
    df["ret_std20_lag1"] = df["ret_1d_pct"].rolling(20).std().shift(1)
    df["flow_x_mom"] = df["flow_z60"] * np.sign(df["ret_5d_lag1"].fillna(0.0))

    df = df.dropna().copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    # Daily feature changes to show what changed in data before decision.
    for c in ["flow_z20", "flow_z60", "flow_rel60", "flow_trend_5_20", "ret_5d_lag1", "ret_std20_lag1"]:
        df[f"d_{c}"] = df[c].diff()

    return df


def classify_event(prev_pos: float, pos: float) -> str:
    if abs(pos - prev_pos) < 1e-12:
        return "HOLD"
    if prev_pos == 0.0 and pos != 0.0:
        return "ENTER"
    if prev_pos != 0.0 and pos == 0.0:
        return "EXIT"
    if prev_pos * pos < 0.0:
        return "FLIP"
    return "RESIZE"


def expected_target(expected_return_pct: float, confidence: float, conf_th: float, min_ret: float, max_pos: float) -> tuple[float, str]:
    if confidence < conf_th:
        return 0.0, "confidence below threshold"
    if abs(expected_return_pct) < min_ret:
        return 0.0, "expected return too small"

    intensity = min(abs(expected_return_pct) / 1.0, 1.0)
    target = max_pos * intensity
    if expected_return_pct > 0:
        return target, "positive expected return"
    return -target, "negative expected return"


def main() -> None:
    args = parse_args()
    root = Path(args.variants_dir)
    if not root.exists():
        raise FileNotFoundError(f"Variants dir not found: {root}")

    base_features = build_feature_dataset(args.flow_csv, args.price_csv)

    summary_rows: list[dict[str, float | int | str]] = []
    driver_rows: list[dict[str, float | str]] = []

    variant_dirs = sorted([p for p in root.iterdir() if p.is_dir()])
    for vd in variant_dirs:
        s_path = vd / "v2_summary.json"
        t_path = vd / "v2_timeseries.csv"
        if not s_path.exists() or not t_path.exists():
            continue

        summary = json.loads(s_path.read_text(encoding="utf-8"))
        ts = pd.read_csv(t_path)
        if ts.empty:
            continue

        ts["date"] = pd.to_datetime(ts["date"]).dt.strftime("%Y-%m-%d")
        merged = ts.merge(base_features, on="date", how="left")

        conf_th = float(summary["confidence_threshold"])
        min_ret = float(summary["min_abs_return_pct"])
        max_pos = float(summary["max_position"])

        exp_targets = merged.apply(
            lambda r: expected_target(
                expected_return_pct=float(r["expected_return_pct"]),
                confidence=float(r["confidence"]),
                conf_th=conf_th,
                min_ret=min_ret,
                max_pos=max_pos,
            ),
            axis=1,
        )
        merged["rule_target_btc"] = [x[0] for x in exp_targets]
        merged["rule_reason"] = [x[1] for x in exp_targets]
        merged["gate_conf_pass"] = merged["confidence"] >= conf_th
        merged["gate_return_pass"] = merged["expected_return_pct"].abs() >= min_ret
        merged["rule_match_actual_position"] = (merged["rule_target_btc"] - merged["position_btc"]).abs() < 1e-8

        # Event labels from realized position transitions.
        prev = 0.0
        events = []
        for pos in merged["position_btc"].astype(float).tolist():
            events.append(classify_event(prev, pos))
            prev = pos
        merged["decision_event"] = events

        merged["variant"] = vd.name

        trace_cols = [
            "date",
            "variant",
            "flow_lag1",
            "flow_z20",
            "flow_z60",
            "flow_rel60",
            "flow_trend_5_20",
            "ret_5d_lag1",
            "ret_std20_lag1",
            "d_flow_z20",
            "d_flow_z60",
            "d_flow_rel60",
            "d_flow_trend_5_20",
            "expected_return_pct",
            "confidence",
            "gate_conf_pass",
            "gate_return_pass",
            "rule_reason",
            "signal_side",
            "position_btc",
            "rule_target_btc",
            "rule_match_actual_position",
            "turnover",
            "decision_event",
            "strategy_ret",
            "benchmark_ret",
        ]
        merged[trace_cols].to_csv(vd / "v2_decision_trace.csv", index=False)

        long_days = int((merged["signal_side"] == "long").sum())
        short_days = int((merged["signal_side"] == "short").sum())
        flat_days = int((merged["signal_side"] == "flat").sum())

        summary_rows.append(
            {
                "variant": vd.name,
                "rows": int(len(merged)),
                "final_value": float(summary["strategy_final_value"]),
                "benchmark_final": float(summary["benchmark_final_value"]),
                "n_trade_events": int(summary["n_trade_events"]),
                "long_days": long_days,
                "short_days": short_days,
                "flat_days": flat_days,
                "conf_fail_days": int((~merged["gate_conf_pass"]).sum()),
                "ret_fail_days": int((merged["gate_conf_pass"] & ~merged["gate_return_pass"]).sum()),
                "rule_match_ratio": float(merged["rule_match_actual_position"].mean()),
                "avg_abs_flow_z60_when_long": float(merged.loc[merged["signal_side"] == "long", "flow_z60"].abs().mean()),
                "avg_abs_flow_z60_when_short": float(
                    merged.loc[merged["signal_side"] == "short", "flow_z60"].abs().mean()
                ),
                "avg_abs_flow_z60_when_flat": float(merged.loc[merged["signal_side"] == "flat", "flow_z60"].abs().mean()),
            }
        )

        for side in ["long", "short", "flat"]:
            part = merged[merged["signal_side"] == side]
            if part.empty:
                continue
            driver_rows.append(
                {
                    "variant": vd.name,
                    "side": side,
                    "n_days": int(len(part)),
                    "mean_flow_z20": float(part["flow_z20"].mean()),
                    "mean_flow_z60": float(part["flow_z60"].mean()),
                    "mean_flow_rel60": float(part["flow_rel60"].mean()),
                    "mean_flow_trend_5_20": float(part["flow_trend_5_20"].mean()),
                    "mean_ret_5d_lag1": float(part["ret_5d_lag1"].mean()),
                    "mean_expected_return_pct": float(part["expected_return_pct"].mean()),
                    "mean_confidence": float(part["confidence"].mean()),
                }
            )

    summary_df = pd.DataFrame(summary_rows).sort_values("final_value", ascending=False)
    drivers_df = pd.DataFrame(driver_rows)

    summary_path = root / "v2_variant_decision_summary.csv"
    drivers_path = root / "v2_variant_signal_drivers.csv"
    summary_df.to_csv(summary_path, index=False)
    drivers_df.to_csv(drivers_path, index=False)

    # Human-readable overview file.
    lines = [
        "# V2 Variant Decision Mapping",
        "",
        "This report maps data changes -> model signal -> trading decision for each variant.",
        "",
        "## Files",
        "",
        "- Per-variant trace: `<variant>/v2_decision_trace.csv`",
        "- Variant summary: `v2_variant_decision_summary.csv`",
        "- Feature-by-side drivers: `v2_variant_signal_drivers.csv`",
        "",
        "## How to interpret trace",
        "",
        "- `d_flow_*` columns show what changed in data before the decision date.",
        "- `expected_return_pct` + `confidence` are model outputs.",
        "- `gate_conf_pass` and `gate_return_pass` show threshold filtering.",
        "- `rule_reason` explains why final signal is long/short/flat.",
        "- `decision_event` shows portfolio transition (ENTER/EXIT/FLIP/RESIZE/HOLD).",
        "",
        "## Top variants by final value",
        "",
    ]
    top = summary_df.head(10)
    if not top.empty:
        view = top[["variant", "final_value", "benchmark_final", "n_trade_events", "conf_fail_days", "ret_fail_days"]]
        lines.append(",".join(view.columns.tolist()))
        for _, r in view.iterrows():
            lines.append(
                ",".join(
                    [
                        str(r["variant"]),
                        f'{float(r["final_value"]):.6f}',
                        f'{float(r["benchmark_final"]):.6f}',
                        str(int(r["n_trade_events"])),
                        str(int(r["conf_fail_days"])),
                        str(int(r["ret_fail_days"])),
                    ]
                )
            )
    else:
        lines.append("No variants found.")

    (root / "v2_decision_index.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {summary_path}")
    print(f"Wrote {drivers_path}")
    print(f"Wrote {root / 'v2_decision_index.md'}")


if __name__ == "__main__":
    main()
