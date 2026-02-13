#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reset monitor ledgers/snapshots so dashboard charts start fresh.")
    p.add_argument("--report-dir", default="reports/testnet_monitor")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    out = root / args.report_dir
    out.mkdir(parents=True, exist_ok=True)

    to_delete = [
        out / "latest_snapshot.json",
        out / "latest_summary.md",
        out / "monitor_state.json",
        out / "fills.csv",
        out / "open_orders.csv",
        out / "strategy_snapshots.csv",
        out / "market_prices.csv",
    ]

    for p in to_delete:
        if p.exists():
            p.unlink()

    snapshots = out / "snapshots"
    if snapshots.exists():
        shutil.rmtree(snapshots)
    snapshots.mkdir(parents=True, exist_ok=True)

    # Start fresh from "now" so the next monitor run doesn't backfill older fills.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    (out / "monitor_state.json").write_text(
        json.dumps({"last_fetch_ms": now_ms, "seen_fill_keys": []}, indent=2),
        encoding="utf-8",
    )

    print(f"Reset complete: {out}")


if __name__ == "__main__":
    main()
