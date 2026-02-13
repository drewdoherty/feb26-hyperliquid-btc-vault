from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class TrainingSummary:
    n_samples: int
    feature_columns: list[str]
    target_std: float
    train_r2: float


def _load_flow_df(flow_csv: str) -> pd.DataFrame:
    df = pd.read_csv(flow_csv)
    required = {"date", "net_flow_usd"}
    if not required.issubset(df.columns):
        raise ValueError(f"Flow CSV must contain columns {required}")
    df = df[["date", "net_flow_usd"]].copy()
    df["date"] = pd.to_datetime(df["date"], utc=False).dt.date
    df["net_flow_usd"] = df["net_flow_usd"].astype(float)
    return df.sort_values("date").drop_duplicates("date", keep="last")


def _load_price_df(price_csv: str) -> pd.DataFrame:
    df = pd.read_csv(price_csv)
    required = {"date", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"Price CSV must contain columns {required}")
    df = df[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"], utc=False).dt.date
    df["close"] = df["close"].astype(float)
    return df.sort_values("date").drop_duplicates("date", keep="last")


def _make_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["flow_b"] = out["net_flow_usd"] / 1_000_000_000
    out["flow_lag1"] = out["flow_b"].shift(1)
    out["flow_lag2"] = out["flow_b"].shift(2)
    out["flow_ma3"] = out["flow_b"].rolling(3).mean()
    out["flow_ma7"] = out["flow_b"].rolling(7).mean()
    out["flow_std7"] = out["flow_b"].rolling(7).std()
    out["ret_1d"] = out["close"].pct_change() * 100.0
    return out


def _build_dataset(flow_csv: str, price_csv: str, horizon_days: int) -> tuple[pd.DataFrame, list[str]]:
    flows = _load_flow_df(flow_csv)
    prices = _load_price_df(price_csv)
    merged = pd.merge(flows, prices, on="date", how="inner")

    if merged.empty:
        raise ValueError("No overlapping dates between flow and price data")

    ds = _make_features(merged)
    ds["target_return_pct"] = (ds["close"].shift(-horizon_days) / ds["close"] - 1.0) * 100.0

    feature_cols = ["flow_b", "flow_lag1", "flow_lag2", "flow_ma3", "flow_ma7", "flow_std7", "ret_1d"]
    ds = ds.dropna(subset=feature_cols + ["target_return_pct"]).copy()
    return ds, feature_cols


def train_and_save(flow_csv: str, price_csv: str, model_path: str, horizon_days: int = 2) -> TrainingSummary:
    ds, feature_cols = _build_dataset(flow_csv, price_csv, horizon_days=horizon_days)

    if len(ds) < 90:
        raise ValueError(
            f"Not enough training rows ({len(ds)}). Need at least 90 daily observations for a useful baseline model."
        )

    x = ds[feature_cols].to_numpy(dtype=float)
    y = ds["target_return_pct"].to_numpy(dtype=float)

    model = Pipeline(
        steps=[
            ("scale", StandardScaler()),
            (
                "mlp",
                MLPRegressor(
                    hidden_layer_sizes=(32, 16),
                    activation="relu",
                    random_state=42,
                    max_iter=2000,
                    early_stopping=True,
                    validation_fraction=0.2,
                ),
            ),
        ]
    )
    model.fit(x, y)

    pred = model.predict(x)
    residual_std = float(np.std(y - pred))
    payload: dict[str, Any] = {
        "model": model,
        "feature_cols": feature_cols,
        "target_residual_std": max(residual_std, 0.05),
        "horizon_hours": horizon_days * 24,
    }

    p = Path(model_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, p)

    return TrainingSummary(
        n_samples=len(ds),
        feature_columns=feature_cols,
        target_std=float(np.std(y)),
        train_r2=float(model.score(x, y)),
    )


def forecast_from_model(flow_csv: str, price_csv: str, model_path: str, horizon_days: int = 2) -> dict[str, float | int]:
    payload: dict[str, Any] = joblib.load(model_path)
    model: Pipeline = payload["model"]
    feature_cols: list[str] = payload["feature_cols"]
    residual_std: float = float(payload["target_residual_std"])
    horizon_hours: int = int(payload.get("horizon_hours", horizon_days * 24))

    ds, _ = _build_dataset(flow_csv, price_csv, horizon_days=horizon_days)
    latest = ds.iloc[-1]
    x = latest[feature_cols].to_numpy(dtype=float).reshape(1, -1)
    expected_return_pct = float(model.predict(x)[0])

    z = abs(expected_return_pct) / max(residual_std, 0.05)
    confidence = min(0.5 + 0.1 * z, 0.95)

    return {
        "horizon_hours": horizon_hours,
        "expected_return_pct": expected_return_pct,
        "confidence": confidence,
    }
