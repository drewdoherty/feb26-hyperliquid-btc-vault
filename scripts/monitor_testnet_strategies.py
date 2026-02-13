#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hyperliquid.info import Info


TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
MAINNET_URL = "https://api.hyperliquid.xyz"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Monitor testnet strategies in real time (fills, open orders, positions, real-price benchmarks)."
    )
    p.add_argument("--config", default="config/testnet_strategies.json")
    p.add_argument("--base-url", default=TESTNET_URL)
    p.add_argument("--real-price-url", default=MAINNET_URL)
    p.add_argument("--report-dir", default="reports/testnet_monitor")
    p.add_argument("--poll-seconds", type=int, default=20, help="Polling interval for watch mode.")
    p.add_argument("--lookback-hours", type=int, default=24, help="Initial fill lookback when starting monitor.")
    p.add_argument("--watch", action="store_true", help="Run continuously. Default is single snapshot.")
    p.add_argument("--include-non-asset-fills", action="store_true", help="Keep fills for all coins, not just strategy asset.")
    return p.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ensure_csv_header(path: Path, headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()


def append_csv_rows(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ensure_csv_header(path, headers)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        for row in rows:
            writer.writerow(row)


def load_config(path: str) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def extract_position_for_asset(state: dict[str, Any], asset: str) -> dict[str, Any]:
    for p in state.get("assetPositions", []):
        pos = p.get("position", {})
        if pos.get("coin") == asset:
            return pos
    return {}


def fmt_num(value: float, ndigits: int = 4) -> str:
    return f"{value:.{ndigits}f}"


def short_time(ms: int | None) -> str:
    if not ms:
        return "-"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


def pick_order_asset(order: dict[str, Any]) -> str | None:
    for key in ("coin", "name", "asset"):
        value = order.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def fill_key(account: str, fill: dict[str, Any]) -> str:
    return "|".join(
        [
            account.lower(),
            str(fill.get("oid", "")),
            str(fill.get("tid", "")),
            str(fill.get("time", "")),
            str(fill.get("coin", "")),
            str(fill.get("px", "")),
            str(fill.get("sz", "")),
            str(fill.get("side", "")),
        ]
    )


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    global_cfg = cfg.get("global", {})
    strategies = [s for s in cfg.get("strategies", []) if s.get("enabled", True)]
    if not strategies:
        raise RuntimeError("No enabled strategies in config")

    benchmark_tokens = list(global_cfg.get("benchmark_tokens", []))
    for s in strategies:
        token = str(s.get("asset", global_cfg.get("asset", "BTC")))
        if token not in benchmark_tokens:
            benchmark_tokens.append(token)

    root = Path(__file__).resolve().parent.parent
    out_root = root / args.report_dir
    snapshots_dir = out_root / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    latest_snapshot_path = out_root / "latest_snapshot.json"
    state_path = out_root / "monitor_state.json"
    summary_path = out_root / "latest_summary.md"

    fills_csv = out_root / "fills.csv"
    orders_csv = out_root / "open_orders.csv"
    strategy_csv = out_root / "strategy_snapshots.csv"
    market_prices_csv = out_root / "market_prices.csv"

    fill_headers = [
        "snapshot_time_utc",
        "strategy",
        "account",
        "asset",
        "fill_time_utc",
        "fill_time_ms",
        "oid",
        "tid",
        "side",
        "dir",
        "coin",
        "px",
        "sz",
        "closed_pnl",
        "fee",
        "fee_token",
        "hash",
    ]
    order_headers = [
        "snapshot_time_utc",
        "strategy",
        "account",
        "asset",
        "oid",
        "coin",
        "side",
        "is_buy",
        "px",
        "sz",
        "reduce_only",
        "timestamp",
    ]
    strategy_headers = [
        "snapshot_time_utc",
        "strategy",
        "account",
        "asset",
        "mark_price_testnet",
        "real_price_usd",
        "position_asset",
        "position_btc",
        "entry_px",
        "position_notional_testnet_usd",
        "position_notional_real_usd",
        "unrealized_pnl_usd",
        "account_value_usd",
        "equity_asset_units",
        "withdrawable_usd",
        "open_orders_asset",
        "open_orders_total",
        "new_fills",
        "last_fill_time_ms",
    ]
    market_headers = ["snapshot_time_utc", "token", "real_price_usd", "testnet_mid"]

    info_test = Info(args.base_url, skip_ws=True)
    info_real = Info(args.real_price_url, skip_ws=True)

    ensure_csv_header(strategy_csv, strategy_headers)
    ensure_csv_header(orders_csv, order_headers)
    ensure_csv_header(fills_csv, fill_headers)
    ensure_csv_header(market_prices_csv, market_headers)

    state = read_json(state_path, {})
    seen_fill_keys = set(state.get("seen_fill_keys", []))
    last_fetch_ms = int(state.get("last_fetch_ms", 0))

    initial_start_ms = int((utc_now().timestamp() - args.lookback_hours * 3600) * 1000)
    if last_fetch_ms <= 0:
        last_fetch_ms = initial_start_ms

    while True:
        now = utc_now()
        now_iso = ts_iso(now)
        now_ms = int(now.timestamp() * 1000)

        mids_test = info_test.all_mids()
        try:
            mids_real = info_real.all_mids()
        except Exception:
            mids_real = {}

        strategy_rows: list[dict[str, Any]] = []
        order_rows: list[dict[str, Any]] = []
        new_fill_rows: list[dict[str, Any]] = []
        snapshot_entries: list[dict[str, Any]] = []
        market_rows: list[dict[str, Any]] = []

        for t in benchmark_tokens:
            market_rows.append(
                {
                    "snapshot_time_utc": now_iso,
                    "token": t,
                    "real_price_usd": safe_float(mids_real.get(t), 0.0),
                    "testnet_mid": safe_float(mids_test.get(t), 0.0),
                }
            )

        for s in strategies:
            strategy_name = str(s.get("name", "unnamed"))
            account = str(s.get("account_address", ""))
            asset = str(s.get("asset", global_cfg.get("asset", "BTC")))

            entry: dict[str, Any] = {
                "strategy": strategy_name,
                "account": account,
                "asset": asset,
                "snapshot_time_utc": now_iso,
            }

            try:
                user_state = info_test.user_state(account)
                try:
                    open_orders = info_test.frontend_open_orders(account)
                except Exception:
                    open_orders = info_test.open_orders(account)
                fills = info_test.user_fills_by_time(account, start_time=last_fetch_ms, end_time=now_ms, aggregate_by_time=False)
            except Exception as exc:
                entry["error"] = str(exc)
                snapshot_entries.append(entry)
                strategy_rows.append(
                    {
                        "snapshot_time_utc": now_iso,
                        "strategy": strategy_name,
                        "account": account,
                        "asset": asset,
                        "mark_price_testnet": "",
                        "real_price_usd": "",
                        "position_asset": "",
                        "position_btc": "",
                        "entry_px": "",
                        "position_notional_testnet_usd": "",
                        "position_notional_real_usd": "",
                        "unrealized_pnl_usd": "",
                        "account_value_usd": "",
                        "equity_asset_units": "",
                        "withdrawable_usd": "",
                        "open_orders_asset": "",
                        "open_orders_total": "",
                        "new_fills": "",
                        "last_fill_time_ms": "",
                    }
                )
                continue

            position = extract_position_for_asset(user_state, asset)
            mark_test = safe_float(mids_test.get(asset), 0.0)
            mark_real = safe_float(mids_real.get(asset), mark_test)
            szi = safe_float(position.get("szi"), 0.0)
            entry_px = safe_float(position.get("entryPx"), 0.0)
            unrealized = safe_float(position.get("unrealizedPnl"), 0.0)
            pos_notional_test = abs(szi) * mark_test
            pos_notional_real = abs(szi) * mark_real
            account_value = safe_float(user_state.get("marginSummary", {}).get("accountValue"), 0.0)
            equity_asset_units = account_value / mark_real if mark_real > 0 else 0.0
            withdrawable = safe_float(user_state.get("withdrawable"), 0.0)

            asset_orders = [o for o in open_orders if pick_order_asset(o) == asset]
            latest_fill_ms: int | None = None
            new_fills_count = 0

            for o in asset_orders:
                order_rows.append(
                    {
                        "snapshot_time_utc": now_iso,
                        "strategy": strategy_name,
                        "account": account,
                        "asset": asset,
                        "oid": o.get("oid"),
                        "coin": pick_order_asset(o) or "",
                        "side": o.get("side", ""),
                        "is_buy": o.get("isBuy", ""),
                        "px": o.get("limitPx", o.get("px", "")),
                        "sz": o.get("sz", ""),
                        "reduce_only": o.get("reduceOnly", ""),
                        "timestamp": o.get("timestamp", ""),
                    }
                )

            for f in fills:
                if not args.include_non_asset_fills and f.get("coin") != asset:
                    continue

                fk = fill_key(account, f)
                if fk in seen_fill_keys:
                    continue
                seen_fill_keys.add(fk)

                fill_ms = int(f.get("time", 0) or 0)
                if latest_fill_ms is None or fill_ms > latest_fill_ms:
                    latest_fill_ms = fill_ms
                new_fills_count += 1

                fill_time_iso = ""
                if fill_ms:
                    fill_time_iso = datetime.fromtimestamp(fill_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")

                new_fill_rows.append(
                    {
                        "snapshot_time_utc": now_iso,
                        "strategy": strategy_name,
                        "account": account,
                        "asset": asset,
                        "fill_time_utc": fill_time_iso,
                        "fill_time_ms": fill_ms,
                        "oid": f.get("oid"),
                        "tid": f.get("tid"),
                        "side": f.get("side", ""),
                        "dir": f.get("dir", ""),
                        "coin": f.get("coin", ""),
                        "px": f.get("px", ""),
                        "sz": f.get("sz", ""),
                        "closed_pnl": f.get("closedPnl", ""),
                        "fee": f.get("fee", ""),
                        "fee_token": f.get("feeToken", ""),
                        "hash": f.get("hash", ""),
                    }
                )

            strategy_row = {
                "snapshot_time_utc": now_iso,
                "strategy": strategy_name,
                "account": account,
                "asset": asset,
                "mark_price_testnet": mark_test,
                "real_price_usd": mark_real,
                "position_asset": szi,
                "position_btc": szi,
                "entry_px": entry_px,
                "position_notional_testnet_usd": pos_notional_test,
                "position_notional_real_usd": pos_notional_real,
                "unrealized_pnl_usd": unrealized,
                "account_value_usd": account_value,
                "equity_asset_units": equity_asset_units,
                "withdrawable_usd": withdrawable,
                "open_orders_asset": len(asset_orders),
                "open_orders_total": len(open_orders),
                "new_fills": new_fills_count,
                "last_fill_time_ms": latest_fill_ms or "",
            }
            strategy_rows.append(strategy_row)

            entry.update(
                {
                    "mark_price_testnet": mark_test,
                    "real_price_usd": mark_real,
                    "position_asset": szi,
                    "entry_px": entry_px,
                    "position_notional_testnet_usd": pos_notional_test,
                    "position_notional_real_usd": pos_notional_real,
                    "unrealized_pnl_usd": unrealized,
                    "account_value_usd": account_value,
                    "equity_asset_units": equity_asset_units,
                    "withdrawable_usd": withdrawable,
                    "open_orders_asset": len(asset_orders),
                    "open_orders_total": len(open_orders),
                    "new_fills": new_fills_count,
                    "last_fill_time_ms": latest_fill_ms,
                    "fills_polled": len(fills),
                }
            )
            snapshot_entries.append(entry)

        snapshot_payload = {
            "snapshot_time_utc": now_iso,
            "base_url": args.base_url,
            "real_price_url": args.real_price_url,
            "n_strategies": len(strategies),
            "entries": snapshot_entries,
            "benchmark_tokens": benchmark_tokens,
        }

        snap_file = snapshots_dir / f"{now_iso.replace(':', '').replace('-', '')}.json"
        write_json(snap_file, snapshot_payload)
        write_json(latest_snapshot_path, snapshot_payload)

        append_csv_rows(strategy_csv, strategy_headers, strategy_rows)
        append_csv_rows(orders_csv, order_headers, order_rows)
        append_csv_rows(fills_csv, fill_headers, new_fill_rows)
        append_csv_rows(market_prices_csv, market_headers, market_rows)

        state = {"last_fetch_ms": now_ms + 1, "seen_fill_keys": sorted(seen_fill_keys)}
        write_json(state_path, state)

        lines = [
            f"# Testnet Monitor ({now_iso})",
            "",
            "| strategy | asset | mark_test | mark_real | pos_asset | pos_real_usd | acct_usd | open_orders | new_fills | last_fill_utc |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in strategy_rows:
            last_fill_ms = row["last_fill_time_ms"] if row["last_fill_time_ms"] != "" else None
            last_fill = "-"
            if isinstance(last_fill_ms, int):
                last_fill = datetime.fromtimestamp(last_fill_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            lines.append(
                "| "
                + f"{row['strategy']} | {row['asset']} | {fmt_num(safe_float(row['mark_price_testnet']), 2)} | "
                + f"{fmt_num(safe_float(row['real_price_usd']), 2)} | {fmt_num(safe_float(row['position_asset']), 5)} | "
                + f"{fmt_num(safe_float(row['position_notional_real_usd']), 2)} | {fmt_num(safe_float(row['account_value_usd']), 2)} | "
                + f"{row['open_orders_asset']} | {row['new_fills']} | {last_fill} |"
            )
        lines.append("")
        lines.append(f"- New fills this poll: {len(new_fill_rows)}")
        lines.append(f"- Snapshot: `{snap_file}`")
        lines.append(f"- Ledgers: `{strategy_csv}`, `{orders_csv}`, `{fills_csv}`, `{market_prices_csv}`")
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        print(f"[{now_iso}] strategies={len(strategies)} new_fills={len(new_fill_rows)}")
        for row in strategy_rows:
            print(
                "  "
                + f"{row['strategy']}: pos={fmt_num(safe_float(row['position_asset']), 5)} {row['asset']} "
                + f"acct=${fmt_num(safe_float(row['account_value_usd']), 2)} "
                + f"open={row['open_orders_asset']} new_fills={row['new_fills']} "
                + f"last_fill={short_time(row['last_fill_time_ms'] if isinstance(row['last_fill_time_ms'], int) else None)}"
            )

        if not args.watch:
            break
        time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    main()
