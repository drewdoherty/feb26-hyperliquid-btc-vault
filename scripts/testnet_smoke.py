#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from hv_btc_vault.settings import settings
from hyperliquid.info import Info


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hyperliquid testnet connectivity/account smoke test")
    p.add_argument("--base-url", default="https://api.hyperliquid-testnet.xyz")
    p.add_argument("--account", default=None)
    p.add_argument("--asset", default="BTC")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    account = args.account or settings.hl_account_address

    info = Info(args.base_url, skip_ws=True)
    mids = info.all_mids()
    state = {}
    account_error = None
    is_valid_account = isinstance(account, str) and account.startswith("0x") and len(account) == 42
    if is_valid_account:
        try:
            state = info.user_state(account)
        except Exception as exc:
            account_error = str(exc)
    else:
        account_error = "account address missing or invalid format (expected 0x + 40 hex chars)"

    out = {
        "base_url": args.base_url,
        "account": account,
        "account_valid_format": is_valid_account,
        "account_error": account_error,
        "asset": args.asset,
        "asset_in_mids": args.asset in mids,
        "asset_mark": mids.get(args.asset),
        "margin_summary": state.get("marginSummary", {}),
        "n_positions": len(state.get("assetPositions", [])),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
