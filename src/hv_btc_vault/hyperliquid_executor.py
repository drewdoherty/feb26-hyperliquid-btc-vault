from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from .settings import Settings
from .risk import trade_passes_min_notional


@dataclass
class RebalanceResult:
    asset: str
    mark_price: float
    current_position_btc: float
    target_position_btc: float
    delta_btc: float
    dry_run: bool
    exchange_response: dict[str, Any] | None


class HyperliquidExecutor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.info = Info(settings.hl_base_url, skip_ws=True)
        self.exchange: Exchange | None = None

        if settings.dry_run:
            return

        key = settings.hl_secret_key.strip()
        if not key.startswith("0x"):
            raise ValueError("HL_SECRET_KEY must start with 0x")
        if len(key) != 66:
            raise ValueError("HL_SECRET_KEY must be 66 chars total (0x + 64 hex chars)")
        try:
            wallet = Account.from_key(key)
        except Exception as exc:
            raise ValueError("HL_SECRET_KEY is not valid hex. Remove quotes/spaces and use raw 0x... value.") from exc

        self.exchange = Exchange(
            wallet,
            settings.hl_base_url,
            vault_address=settings.hl_vault_address,
            account_address=settings.hl_account_address,
        )

    def mark_price(self, asset: str) -> float:
        mids = self.info.all_mids()
        if asset not in mids:
            raise KeyError(f"Asset {asset} not in mids. Available example: {list(mids.keys())[:10]}")
        return float(mids[asset])

    def asset_sz_decimals(self, asset: str) -> int:
        meta = self.info.meta()
        for u in meta.get("universe", []):
            if u.get("name") == asset:
                return int(u.get("szDecimals", 0))
        return 3

    def current_position_btc(self, asset: str) -> float:
        if not self.settings.hl_account_address.startswith("0x") or len(self.settings.hl_account_address) != 42:
            return 0.0

        state = self.info.user_state(self.settings.hl_account_address)
        positions = state.get("assetPositions", [])

        for p in positions:
            pos = p.get("position", {})
            if pos.get("coin") == asset:
                try:
                    return float(pos.get("szi", 0.0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def rebalance_to_target(self, asset: str, target_btc: float, min_trade_notional_usd: float) -> RebalanceResult:
        mark = self.mark_price(asset)
        current = self.current_position_btc(asset)
        delta = target_btc - current

        if abs(delta) < 1e-8:
            return RebalanceResult(asset, mark, current, target_btc, 0.0, self.settings.dry_run, None)

        if not trade_passes_min_notional(delta, mark, min_trade_notional_usd):
            return RebalanceResult(asset, mark, current, target_btc, delta, self.settings.dry_run, None)

        if self.settings.dry_run:
            return RebalanceResult(asset, mark, current, target_btc, delta, True, None)

        if self.exchange is None:
            raise RuntimeError("Live mode requires a valid Exchange client. Check HL_SECRET_KEY and DRY_RUN settings.")

        sz_decimals = self.asset_sz_decimals(asset)
        rounded_sz = round(abs(delta), sz_decimals)
        if rounded_sz <= 0:
            return RebalanceResult(asset, mark, current, target_btc, 0.0, False, None)

        self.exchange.update_leverage(1, asset, is_cross=True)
        response = self.exchange.market_open(
            name=asset,
            is_buy=delta > 0,
            sz=rounded_sz,
            slippage=self.settings.hl_default_slippage,
        )
        return RebalanceResult(asset, mark, current, target_btc, delta, False, response)
