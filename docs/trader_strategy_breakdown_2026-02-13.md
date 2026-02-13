# Trader Breakdown: Live Testnet Market-Making Stack (2026-02-13)

## What all 3 strategies are doing (same core logic)

Each strategy is a Stoikov-style inventory market maker. Every cycle it does this:

1. Pull top-of-book (best bid/ask) and mid price.
2. Read your current position for that coin.
3. Estimate short-term volatility from recent log returns.
4. Compute a reservation price (inventory-adjusted fair value):
   reservation = mid - position * gamma * sigma2_h
5. Compute spread width from risk + liquidity:
   raw_spread = gamma * sigma2_h + (2/gamma) * ln(1 + gamma/kappa)
   then clamp to min/max spread bps in config.
6. Quote both sides around reservation:
   bid = reservation - half_spread
   ask = reservation + half_spread
7. Inventory skew on size:
   - long inventory -> smaller bid, bigger ask (lean to reduce long)
   - short inventory -> bigger bid, smaller ask (lean to reduce short)
8. Position cap logic:
   - if at +max position, stop placing new bids
   - if at -max position, stop placing new asks
9. Fill-pressure mode for stale fills:
   - if no fills for target time, tighten spread and switch tif to Gtc
   - can cross the book to force inventory turnover
10. In live mode: cancel existing asset orders, then post fresh bid/ask.

## How parameters translate into trading behavior

- gamma: inventory aversion. Higher gamma = stronger inventory push, typically wider/more defensive quoting.
- kappa: assumed order-book liquidity. Lower kappa usually means wider model spread.
- min_spread_bps / max_spread_bps: hard floor/ceiling for quoted width.
- mm_order_size: baseline quote size per side before inventory skew.
- mm_max_abs_position: hard inventory cap per strategy.
- mm_target_fill_seconds: how quickly strategy tries to get at least one fill before switching to aggressive fill-pressure mode.

## Strategy-by-strategy (current config)

### Strategy 1: mm_eth_perp
- Coin: ETH perp
- Base size: 0.01 ETH
- Max abs position: 0.20 ETH
- gamma/kappa: 0.12 / 1.5
- Spread bounds: 5 to 90 bps
- Personality: moderate inventory control, medium-tight quoting, faster turnover than very wide MM settings.

### Strategy 2: mm_btc_perp
- Coin: BTC perp
- Base size: 0.001 BTC
- Max abs position: 0.004 BTC
- gamma/kappa: 0.15 / 1.8
- Spread bounds: 4 to 80 bps
- Personality: tighter base spread with slightly stronger inventory sensitivity than ETH setup.

### Strategy 3: mm_hype_perp
- Coin: HYPE perp
- Base size: 1.0 HYPE
- Max abs position: 12 HYPE
- gamma/kappa: 0.20 / 1.2
- Spread bounds: 12 to 250 bps
- Personality: most conservative/wide regime (higher gamma + wider min/max spread) to handle thinner/noisier market microstructure.

## Why fills may still be sparse sometimes

Even with a 30s target fill setting, the strategy only *tries* to force more activity after fills are stale. Actual fills still depend on:
- whether your key/account is valid and authorized on testnet,
- whether the coin/account has active margin and can place orders,
- immediate order-book depth and volatility,
- whether your quotes are still inside a tradable range after rounding/tick constraints.

So 30s is a target behavior control, not a hard guarantee.

## Current operational caveat to check first

If one strategy shows no orders/fills while others work, the first suspect is key/account mismatch for that strategy's env var and configured account.
