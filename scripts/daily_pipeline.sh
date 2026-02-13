#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
FORECAST_PATH="$ROOT_DIR/data/forecast.json"
FLOW_PATH="${FLOW_CSV_PATH:-$ROOT_DIR/data/ibit_flows.csv}"
PRICE_PATH="${PRICE_CSV_PATH:-$ROOT_DIR/data/btc_prices.csv}"
MODEL_PATH="${MODEL_PATH:-$ROOT_DIR/models/nn_model.joblib}"
RUN_DATE="$(date +%F)"
LOG_FILE="$LOG_DIR/run_${RUN_DATE}.log"

mkdir -p "$LOG_DIR"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting daily pipeline"
  echo "Root: $ROOT_DIR"

  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env"
    set +a
  fi

  if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.venv/bin/activate"
  fi

  if [[ "${UPDATE_IBIT_DATA:-true}" == "true" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Updating IBIT flows"
    PYTHONPATH=src ./scripts/update_ibit_flows.py --out "$FLOW_PATH" || \
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] IBIT update failed; continuing with existing flow CSV"
  fi

  if [[ -n "${MODEL_FORECAST_CMD:-}" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running model forecast command"
    TMP_FORECAST="$(mktemp "$ROOT_DIR/data/forecast.XXXXXX.json")"
    trap 'rm -f "$TMP_FORECAST"' EXIT

    eval "$MODEL_FORECAST_CMD" > "$TMP_FORECAST"

    python -m json.tool "$TMP_FORECAST" > /dev/null
    mv "$TMP_FORECAST" "$FORECAST_PATH"
    trap - EXIT

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Forecast updated at $FORECAST_PATH"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] MODEL_FORECAST_CMD not set; using built-in model pipeline"
    TMP_FORECAST="$(mktemp "$ROOT_DIR/data/forecast.XXXXXX.json")"
    trap 'rm -f "$TMP_FORECAST"' EXIT

    if [[ "${UPDATE_PRICE_DATA:-true}" == "true" ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Refreshing BTC price history"
      PYTHONPATH=src ./scripts/fetch_btc_prices.py --out "$PRICE_PATH" --days "${PRICE_LOOKBACK_DAYS:-730}"
    fi

    PYTHONPATH=src ./scripts/generate_forecast.py \
      --flow-csv "$FLOW_PATH" \
      --price-csv "$PRICE_PATH" \
      --model-path "$MODEL_PATH" \
      --horizon-days "${MODEL_HORIZON_DAYS:-2}" \
      --train-if-missing \
      --allow-heuristic > "$TMP_FORECAST"

    python -m json.tool "$TMP_FORECAST" > /dev/null
    mv "$TMP_FORECAST" "$FORECAST_PATH"
    trap - EXIT
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Forecast updated at $FORECAST_PATH"
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running strategy"
  PYTHONPATH=src ./scripts/run_daily.py --flow-csv "$FLOW_PATH" --forecast-json "$FORECAST_PATH"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pipeline complete"
} >> "$LOG_FILE" 2>&1
