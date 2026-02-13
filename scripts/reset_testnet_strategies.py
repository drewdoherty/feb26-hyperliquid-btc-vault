#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info


TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reset strategy state: cancel open orders + flatten positions.")
    p.add_argument("--config", default="config/testnet_strategies.json")
    p.add_argument("--base-url", default=TESTNET_URL)
    p.add_argument("--slippage", type=float, default=0.01)
    p.add_argument(
        "--all-assets",
        action="store_true",
        help="Flatten all non-zero perp positions on each strategy account (not only configured asset).",
    )
    p.add_argument("--live", action="store_true", help="Execute actions. Default is dry-run.")
    return p.parse_args()


def read_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def pick_order_asset(order: dict[str, Any]) -> str | None:
    for key in ("coin", "name", "asset"):
        value = order.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def current_position_btc(info: Info, address: str, asset: str) -> float:
    state = info.user_state(address)
    for p in state.get("assetPositions", []):
        pos = p.get("position", {})
        if pos.get("coin") == asset:
            return safe_float(pos.get("szi"))
    return 0.0


def non_zero_positions(info: Info, address: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    state = info.user_state(address)
    for p in state.get("assetPositions", []):
        pos = p.get("position", {})
        coin = str(pos.get("coin", ""))
        szi = safe_float(pos.get("szi"))
        if coin and abs(szi) > 1e-8:
            out.append((coin, szi))
    return out


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    cfg = read_config(args.config)
    global_asset = str(cfg.get("global", {}).get("asset", "BTC"))
    strategies = [s for s in cfg.get("strategies", []) if s.get("enabled", True)]

    info = Info(args.base_url, skip_ws=True)
    out: list[dict[str, Any]] = []

    for s in strategies:
        name = str(s.get("name", "unnamed"))
        account = str(s.get("account_address", ""))
        vault = str(s.get("vault_address", account))
        use_vault = bool(s.get("use_vault", False))
        asset = str(s.get("asset", global_asset))
        key_env = str(s.get("secret_key_env", ""))
        key = os.getenv(key_env, "").strip()

        orders = info.open_orders(account)
        asset_orders = [o for o in orders if pick_order_asset(o) == asset]
        position = current_position_btc(info, account, asset)

        positions_to_flatten: list[tuple[str, float]]
        if args.all_assets:
            positions_to_flatten = non_zero_positions(info, account)
        else:
            positions_to_flatten = [(asset, position)] if abs(position) > 1e-8 else []

        rec: dict[str, Any] = {
            "strategy": name,
            "account": account,
            "asset": asset,
            "mode": "live" if args.live else "dry-run",
            "open_orders_total": len(orders),
            "open_orders_asset": len(asset_orders),
            "position_btc": position,
            "positions_to_flatten": [{"asset": c, "position": p} for c, p in positions_to_flatten],
            "planned_cancel_oids": [o.get("oid") for o in asset_orders if o.get("oid") is not None],
            "planned_flatten": len(positions_to_flatten) > 0,
            "cancel_results": [],
            "close_results": [],
            "error": "",
        }

        if not args.live:
            out.append(rec)
            continue

        if not key:
            rec["error"] = f"Missing env var: {key_env}"
            out.append(rec)
            continue
        if not key.startswith("0x") or len(key) != 66:
            rec["error"] = f"{key_env} must be 66 chars total (0x + 64 hex chars)"
            out.append(rec)
            continue

        try:
            wallet = Account.from_key(key)
            exchange = Exchange(
                wallet,
                args.base_url,
                vault_address=vault if use_vault else None,
                account_address=account,
            )
        except Exception as exc:
            rec["error"] = f"Exchange init failed: {exc}"
            out.append(rec)
            continue

        for o in asset_orders:
            oid = o.get("oid")
            if oid is None:
                continue
            try:
                c = exchange.cancel(asset, int(oid))
                rec["cancel_results"].append({"oid": oid, "response": c})
            except Exception as exc:
                rec["cancel_results"].append({"oid": oid, "error": str(exc)})

        for coin, szi in positions_to_flatten:
            try:
                rec["close_results"].append(
                    {
                        "asset": coin,
                        "response": exchange.market_close(coin, sz=abs(szi), slippage=args.slippage),
                    }
                )
            except Exception as exc:
                rec["close_results"].append({"asset": coin, "error": str(exc)})

        # Refresh current state after actions.
        rec["position_btc_after"] = current_position_btc(info, account, asset)
        rec["open_orders_asset_after"] = len([o for o in info.open_orders(account) if pick_order_asset(o) == asset])
        rec["remaining_positions_after"] = [{"asset": c, "position": p} for c, p in non_zero_positions(info, account)]
        out.append(rec)

    print(json.dumps({"mode": "live" if args.live else "dry-run", "results": out}, indent=2))


if __name__ == "__main__":
    main()
