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
    p = argparse.ArgumentParser(description="Top up perp collateral from spot USDC for strategy accounts.")
    p.add_argument("--config", default="config/testnet_strategies.json")
    p.add_argument("--base-url", default=TESTNET_URL)
    p.add_argument("--target-usd", type=float, default=100.0, help="Desired perp account value per strategy account.")
    p.add_argument("--min-transfer", type=float, default=1.0, help="Skip transfers smaller than this amount.")
    p.add_argument("--live", action="store_true", help="Execute transfers. Default is dry-run preview.")
    return p.parse_args()


def load_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def spot_usdc_balance(info: Info, address: str) -> float:
    spot = info.spot_user_state(address)
    for b in spot.get("balances", []):
        if b.get("coin") == "USDC":
            return safe_float(b.get("total"))
    return 0.0


def perp_account_value(info: Info, address: str) -> float:
    state = info.user_state(address)
    return safe_float(state.get("marginSummary", {}).get("accountValue"))


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    cfg = load_config(args.config)
    strategies = [s for s in cfg.get("strategies", []) if s.get("enabled", True)]
    info = Info(args.base_url, skip_ws=True)

    results: list[dict[str, Any]] = []
    for s in strategies:
        name = str(s.get("name", "unnamed"))
        account = str(s.get("account_address", ""))
        vault = str(s.get("vault_address", account))
        secret_env = str(s.get("secret_key_env", ""))
        key = os.getenv(secret_env, "").strip()

        perp_usd = perp_account_value(info, account)
        spot_usdc = spot_usdc_balance(info, account)
        need = max(0.0, args.target_usd - perp_usd)
        transfer = min(need, spot_usdc)

        record: dict[str, Any] = {
            "strategy": name,
            "account": account,
            "perp_account_value_usd": round(perp_usd, 6),
            "spot_usdc": round(spot_usdc, 6),
            "target_usd": args.target_usd,
            "needed_usd": round(need, 6),
            "planned_transfer_usd": round(transfer, 6),
            "mode": "live" if args.live else "dry-run",
            "status": "skipped",
            "response": None,
            "error": "",
        }

        if transfer < args.min_transfer:
            record["status"] = "no_transfer_needed_or_insufficient_spot"
            results.append(record)
            continue

        if not key:
            record["status"] = "error"
            record["error"] = f"Missing env var: {secret_env}"
            results.append(record)
            continue

        if not key.startswith("0x") or len(key) != 66:
            record["status"] = "error"
            record["error"] = f"{secret_env} must be 66 chars total (0x + 64 hex chars)"
            results.append(record)
            continue

        try:
            wallet = Account.from_key(key)
            record["signer_wallet"] = wallet.address
        except Exception as exc:
            record["status"] = "error"
            record["error"] = f"Invalid key in {secret_env}: {exc}"
            results.append(record)
            continue

        if not args.live:
            record["status"] = "ready_to_transfer"
            results.append(record)
            continue

        try:
            exchange = Exchange(
                wallet,
                args.base_url,
                vault_address=vault,
                account_address=account,
            )
            resp = exchange.usd_class_transfer(amount=transfer, to_perp=True)
            record["response"] = resp
            if isinstance(resp, dict) and resp.get("status") == "ok":
                record["status"] = "transfer_submitted"
            else:
                record["status"] = "error"
                record["error"] = str(resp)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)

        results.append(record)

    print(json.dumps({"mode": "live" if args.live else "dry-run", "target_usd": args.target_usd, "results": results}, indent=2))


if __name__ == "__main__":
    main()
