#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from hv_btc_vault.ibit_fetcher import fetch_ibit_flows_usd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Update IBIT flow CSV from Farside")
    p.add_argument("--out", default="data/ibit_flows.csv", help="Output CSV path")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fetched = fetch_ibit_flows_usd()

    if out_path.exists():
        existing = pd.read_csv(out_path)
        if {"date", "net_flow_usd"}.issubset(existing.columns):
            merged = pd.concat([existing[["date", "net_flow_usd"]], fetched], ignore_index=True)
        else:
            merged = fetched
    else:
        merged = fetched

    merged["date"] = merged["date"].astype(str)
    merged["net_flow_usd"] = merged["net_flow_usd"].astype(float)
    merged = merged.drop_duplicates("date", keep="last").sort_values("date")

    merged.to_csv(out_path, index=False)
    print(f"Wrote {len(merged)} rows to {out_path}")


if __name__ == "__main__":
    main()
