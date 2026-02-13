#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="dry"
if [[ "${1:-}" == "--live" ]]; then
  MODE="live"
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo ".env not found. Create it from .env.example first."
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
set +a

if [[ "${HL_BASE_URL:-}" != "https://api.hyperliquid-testnet.xyz" ]]; then
  echo "HL_BASE_URL must be https://api.hyperliquid-testnet.xyz for testnet deploy."
  exit 1
fi

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

echo "[1/3] Testnet smoke check"
PYTHONPATH=src ./scripts/testnet_smoke.py --base-url "$HL_BASE_URL" --asset "${HL_ASSET:-BTC}"

echo "[2/3] Data/model pipeline"
./scripts/daily_pipeline.sh

if [[ "$MODE" == "dry" ]]; then
  echo "[3/3] Dry-run rebalance"
  DRY_RUN=true PYTHONPATH=src ./scripts/run_daily.py --flow-csv "${FLOW_CSV_PATH:-data/ibit_flows.csv}" --forecast-json data/forecast.json
  echo "Testnet deployment dry-run complete. Use --live for actual testnet orders."
else
  echo "[3/3] Live rebalance on testnet"
  DRY_RUN=false PYTHONPATH=src ./scripts/run_daily.py --flow-csv "${FLOW_CSV_PATH:-data/ibit_flows.csv}" --forecast-json data/forecast.json
  echo "Testnet live rebalance submitted."
fi
