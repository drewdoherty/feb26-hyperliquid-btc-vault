#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info


TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
MAINNET_URL = "https://api.hyperliquid.xyz"


@dataclass
class StrategyRuntime:
    name: str
    asset: str
    account: str
    vault: str
    key_env: str
    exchange: Exchange | None
    sz_decimals: int
    only_isolated: bool
    order_size: float
    max_abs_position: float
    gamma: float
    kappa: float
    horizon_seconds: float
    min_spread_bps: float
    max_spread_bps: float
    target_fill_seconds: float
    history: deque[float]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Stoikov inventory-based market making on Hyperliquid testnet.")
    p.add_argument("--config", default="config/testnet_strategies.json")
    p.add_argument("--base-url", default=TESTNET_URL)
    p.add_argument("--real-price-url", default=MAINNET_URL)
    p.add_argument("--poll-seconds", type=int, default=10)
    p.add_argument("--history-length", type=int, default=120, help="Number of mid-price samples kept for volatility estimate.")
    p.add_argument("--report-dir", default="reports/testnet_mm")
    p.add_argument("--watch", action="store_true", help="Run continuously.")
    p.add_argument("--max-cycles", type=int, default=0, help="Stop after N cycles (0 = unlimited).")
    p.add_argument("--live", action="store_true", help="Send real testnet orders. Default is dry-run.")
    return p.parse_args()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ts_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def count_decimals_from_str(px: str) -> int:
    if "." not in px:
        return 0
    return len(px.split(".", 1)[1].rstrip("0"))


def pick_order_asset(order: dict[str, Any]) -> str | None:
    for key in ("coin", "name", "asset"):
        value = order.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def read_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_csv_header(path: Path, headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()


def append_csv(path: Path, headers: list[str], row: dict[str, Any]) -> None:
    ensure_csv_header(path, headers)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writerow(row)


def build_strategy_runtimes(
    cfg: dict[str, Any],
    info: Info,
    base_url: str,
    history_length: int,
    live: bool,
) -> list[StrategyRuntime]:
    global_cfg = cfg.get("global", {})
    strategies = [s for s in cfg.get("strategies", []) if s.get("enabled", True)]
    if not strategies:
        raise RuntimeError("No enabled strategies in config.")

    meta = info.meta()
    universe = {u.get("name"): u for u in meta.get("universe", [])}
    runtimes: list[StrategyRuntime] = []

    for s in strategies:
        name = str(s.get("name", "unnamed"))
        asset = str(s.get("asset", global_cfg.get("asset", "BTC")))
        account = str(s.get("account_address", ""))
        vault = str(s.get("vault_address", account))
        key_env = str(s.get("secret_key_env", ""))
        key = os.getenv(key_env, "").strip()

        if asset not in universe:
            raise RuntimeError(f"{name}: asset {asset} not found in testnet meta universe.")
        sz_decimals = int(universe[asset].get("szDecimals", 3))
        only_isolated = bool(universe[asset].get("onlyIsolated", False))

        exchange: Exchange | None = None
        if live:
            if not key or not key.startswith("0x") or len(key) != 66:
                raise RuntimeError(f"{name}: invalid/missing key in env var {key_env}.")
            wallet = Account.from_key(key)
            exchange = Exchange(
                wallet,
                base_url,
                vault_address=vault,
                account_address=account,
            )
            try:
                exchange.update_leverage(1, asset, is_cross=not only_isolated)
            except Exception:
                # Non-fatal: some assets/accounts may already be configured.
                pass

        runtimes.append(
            StrategyRuntime(
                name=name,
                asset=asset,
                account=account,
                vault=vault,
                key_env=key_env,
                exchange=exchange,
                sz_decimals=sz_decimals,
                only_isolated=only_isolated,
                order_size=safe_float(s.get("mm_order_size", 0.01), 0.01),
                max_abs_position=safe_float(s.get("mm_max_abs_position", 0.2), 0.2),
                gamma=safe_float(s.get("mm_gamma", 0.1), 0.1),
                kappa=safe_float(s.get("mm_kappa", 1.5), 1.5),
                horizon_seconds=safe_float(s.get("mm_horizon_seconds", 30.0), 30.0),
                min_spread_bps=safe_float(s.get("mm_min_spread_bps", 4.0), 4.0),
                max_spread_bps=safe_float(s.get("mm_max_spread_bps", 60.0), 60.0),
                target_fill_seconds=safe_float(s.get("mm_target_fill_seconds", 30.0), 30.0),
                history=deque(maxlen=max(10, history_length)),
            )
        )

    return runtimes


def current_position_asset(info: Info, account: str, asset: str) -> float:
    state = info.user_state(account)
    for p in state.get("assetPositions", []):
        pos = p.get("position", {})
        if pos.get("coin") == asset:
            return safe_float(pos.get("szi"), 0.0)
    return 0.0


def top_book(info: Info, asset: str) -> tuple[float, float, int]:
    l2 = info.l2_snapshot(asset)
    levels = l2.get("levels", [[], []])
    bid = safe_float(levels[0][0].get("px")) if levels and levels[0] else 0.0
    ask = safe_float(levels[1][0].get("px")) if len(levels) > 1 and levels[1] else 0.0
    if bid <= 0 or ask <= 0:
        raise RuntimeError(f"{asset}: invalid L2 top of book.")
    px_decimals = max(count_decimals_from_str(str(levels[0][0].get("px", "0"))), count_decimals_from_str(str(levels[1][0].get("px", "0"))))
    return bid, ask, max(0, px_decimals)


def latest_fill_age_seconds(info: Info, account: str, asset: str, now_ms: int) -> float:
    fills = info.user_fills(account)
    for f in fills:
        if f.get("coin") == asset:
            t = int(f.get("time", 0) or 0)
            if t > 0:
                return max(0.0, (now_ms - t) / 1000.0)
    return 1e9


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def estimate_sigma_step(history: deque[float]) -> float:
    if len(history) < 3:
        return 0.0005
    rets: list[float] = []
    prev = history[0]
    for px in list(history)[1:]:
        if prev > 0 and px > 0:
            rets.append(math.log(px / prev))
        prev = px
    if len(rets) < 2:
        return 0.0005
    return max(1e-6, pstdev(rets))


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    cfg = read_config(args.config)
    info_test = Info(args.base_url, skip_ws=True)
    info_real = Info(args.real_price_url, skip_ws=True)
    runtimes = build_strategy_runtimes(cfg, info_test, args.base_url, args.history_length, live=args.live)

    out_dir = root / args.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    events_csv = out_dir / "mm_events.csv"
    event_headers = [
        "ts_utc",
        "strategy",
        "asset",
        "mode",
        "mid_testnet",
        "best_bid",
        "best_ask",
        "mid_real",
        "position_asset",
        "reservation_px",
        "spread_bps",
        "quote_bid_px",
        "quote_ask_px",
        "quote_bid_sz",
        "quote_ask_sz",
        "tif",
        "last_fill_age_sec",
        "cancelled_orders",
        "responses_json",
        "error",
    ]

    cycle = 0
    while True:
        cycle += 1
        t0 = now_utc()
        t0_iso = ts_iso(t0)
        now_ms = int(t0.timestamp() * 1000)
        mids_test = info_test.all_mids()
        mids_real = info_real.all_mids()

        print(f"[{t0_iso}] cycle={cycle} mode={'live' if args.live else 'dry-run'}")

        for rt in runtimes:
            err = ""
            responses: list[Any] = []
            cancelled = 0
            tif = "Alo"
            bid_px = 0.0
            ask_px = 0.0
            bid_sz = 0.0
            ask_sz = 0.0
            reservation = 0.0
            spread_bps = 0.0
            position = 0.0
            mid_test = safe_float(mids_test.get(rt.asset), 0.0)
            mid_real = safe_float(mids_real.get(rt.asset), mid_test)
            best_bid = 0.0
            best_ask = 0.0
            fill_age = 1e9

            try:
                best_bid, best_ask, px_decimals = top_book(info_test, rt.asset)
                if mid_test <= 0:
                    mid_test = (best_bid + best_ask) / 2.0
                rt.history.append(mid_test)
                sigma_step = estimate_sigma_step(rt.history)
                position = current_position_asset(info_test, rt.account, rt.asset)
                fill_age = latest_fill_age_seconds(info_test, rt.account, rt.asset, now_ms)

                dt = max(1.0, float(args.poll_seconds))
                var_per_sec = (sigma_step * sigma_step) / dt
                sigma2_h = (mid_test * mid_test) * var_per_sec * max(1.0, rt.horizon_seconds)
                inv_ratio = clamp(position / max(rt.max_abs_position, 1e-9), -1.0, 1.0)

                reservation = mid_test - position * rt.gamma * sigma2_h
                raw_spread_px = rt.gamma * sigma2_h + (2.0 / max(rt.gamma, 1e-6)) * math.log(
                    1.0 + max(rt.gamma, 1e-6) / max(rt.kappa, 1e-6)
                )
                spread_bps = clamp((raw_spread_px / max(mid_test, 1e-9)) * 10_000.0, rt.min_spread_bps, rt.max_spread_bps)
                half_spread_px = mid_test * (spread_bps / 10_000.0) / 2.0

                # Fill-pressure mode: tighten and allow crossing if fills are stale.
                if fill_age > rt.target_fill_seconds:
                    urgency = clamp(fill_age / max(rt.target_fill_seconds, 1.0), 1.0, 5.0)
                    half_spread_px = max(mid_test * (rt.min_spread_bps / 10_000.0) / 8.0, half_spread_px / urgency)
                    tif = "Gtc"

                bid_px = reservation - half_spread_px
                ask_px = reservation + half_spread_px
                tick = 10 ** (-px_decimals)
                if tif == "Alo":
                    bid_px = min(bid_px, best_ask - tick)
                    ask_px = max(ask_px, best_bid + tick)
                else:
                    if position > 0:
                        ask_px = min(ask_px, best_bid)
                    elif position < 0:
                        bid_px = max(bid_px, best_ask)

                bid_px = round(max(tick, bid_px), px_decimals)
                ask_px = round(max(bid_px + tick, ask_px), px_decimals)

                bid_mult = max(0.0, 1.0 - inv_ratio)
                ask_mult = max(0.0, 1.0 + inv_ratio)
                bid_sz = round(rt.order_size * max(0.15, bid_mult), rt.sz_decimals)
                ask_sz = round(rt.order_size * max(0.15, ask_mult), rt.sz_decimals)

                if position >= rt.max_abs_position:
                    bid_sz = 0.0
                if position <= -rt.max_abs_position:
                    ask_sz = 0.0

                open_orders = info_test.open_orders(rt.account)
                asset_orders = [o for o in open_orders if pick_order_asset(o) == rt.asset]

                if args.live:
                    if rt.exchange is None:
                        raise RuntimeError("Exchange client missing in live mode.")

                    for o in asset_orders:
                        oid = o.get("oid")
                        if oid is None:
                            continue
                        try:
                            rt.exchange.cancel(rt.asset, int(oid))
                            cancelled += 1
                        except Exception as cancel_exc:
                            responses.append({"cancel_oid": oid, "error": str(cancel_exc)})

                    order_type = {"limit": {"tif": tif}}
                    if bid_sz > 0:
                        responses.append(
                            rt.exchange.order(
                                name=rt.asset,
                                is_buy=True,
                                sz=bid_sz,
                                limit_px=bid_px,
                                order_type=order_type,
                                reduce_only=False,
                            )
                        )
                    if ask_sz > 0:
                        responses.append(
                            rt.exchange.order(
                                name=rt.asset,
                                is_buy=False,
                                sz=ask_sz,
                                limit_px=ask_px,
                                order_type=order_type,
                                reduce_only=False,
                            )
                        )
                else:
                    cancelled = len(asset_orders)
            except Exception as exc:
                err = str(exc)

            append_csv(
                events_csv,
                event_headers,
                {
                    "ts_utc": t0_iso,
                    "strategy": rt.name,
                    "asset": rt.asset,
                    "mode": "live" if args.live else "dry-run",
                    "mid_testnet": round(mid_test, 8),
                    "best_bid": round(best_bid, 8),
                    "best_ask": round(best_ask, 8),
                    "mid_real": round(mid_real, 8),
                    "position_asset": round(position, 10),
                    "reservation_px": round(reservation, 8),
                    "spread_bps": round(spread_bps, 6),
                    "quote_bid_px": bid_px,
                    "quote_ask_px": ask_px,
                    "quote_bid_sz": bid_sz,
                    "quote_ask_sz": ask_sz,
                    "tif": tif,
                    "last_fill_age_sec": round(fill_age, 3),
                    "cancelled_orders": cancelled,
                    "responses_json": json.dumps(responses, separators=(",", ":")),
                    "error": err,
                },
            )

            print(
                f"  {rt.name} {rt.asset}: pos={position:.6f} bid={bid_px} ask={ask_px} "
                f"sz=({bid_sz},{ask_sz}) fill_age={fill_age:.1f}s tif={tif} cancelled={cancelled}"
                + (f" err={err}" if err else "")
            )

        if not args.watch:
            break
        if args.max_cycles > 0 and cycle >= args.max_cycles:
            break
        time.sleep(max(1, args.poll_seconds))


if __name__ == "__main__":
    main()
