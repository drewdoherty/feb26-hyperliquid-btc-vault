#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

HEADERS = {"User-Agent": "hv-btc-vault/0.1"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch daily BTC close prices from public APIs")
    p.add_argument("--days", type=int, default=730, help="Days of history to fetch")
    p.add_argument("--out", default="data/btc_prices.csv", help="Output CSV path")
    return p.parse_args()


def fetch_from_coingecko(days: int) -> list[dict[str, float | str]]:
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    rows = []
    for ts_ms, px in payload.get("prices", []):
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        rows.append({"date": dt, "close": float(px)})
    return rows


def fetch_from_cryptocompare(days: int) -> list[dict[str, float | str]]:
    limit = min(max(days, 30), 2000)
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {"fsym": "BTC", "tsym": "USD", "limit": limit}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    data = payload.get("Data", {}).get("Data", [])
    rows = []
    for x in data:
        ts = int(x.get("time", 0))
        close = x.get("close")
        if not ts or close is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        rows.append({"date": dt, "close": float(close)})
    return rows


def fetch_from_yahoo(days: int) -> list[dict[str, float | str]]:
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days + 2)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"
    params = {"period1": int(start.timestamp()), "period2": int(end.timestamp()), "interval": "1d"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    result = data.get("chart", {}).get("result", [])
    if not result:
        return []

    timestamps = result[0].get("timestamp", [])
    closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
    rows = []
    for ts, px in zip(timestamps, closes):
        if px is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        rows.append({"date": dt, "close": float(px)})
    return rows


def main() -> None:
    args = parse_args()

    errors: list[str] = []
    rows: list[dict[str, float | str]] = []
    for name, fn in [
        ("coingecko", fetch_from_coingecko),
        ("cryptocompare", fetch_from_cryptocompare),
        ("yahoo", fetch_from_yahoo),
    ]:
        try:
            rows = fn(args.days)
            if rows:
                print(f"source={name}")
                break
            errors.append(f"{name}: empty")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if not rows:
        raise RuntimeError("Failed to fetch BTC prices: " + " | ".join(errors))

    df = pd.DataFrame(rows).drop_duplicates("date", keep="last").sort_values("date")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"Wrote {len(df)} rows to {out}")


if __name__ == "__main__":
    main()
