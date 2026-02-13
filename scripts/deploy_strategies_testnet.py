#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy multiple strategy variants to Hyperliquid testnet")
    p.add_argument("--config", default="config/testnet_strategies.json")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--forecast-json", default="data/forecast.json")
    p.add_argument("--allow-heuristic", action="store_true")
    p.add_argument("--live", action="store_true", help="Submit real testnet orders. Default is dry-run.")
    p.add_argument("--report-dir", default="reports/testnet_deploy")
    return p.parse_args()


def load_config(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def strategy_env(base_env: dict[str, str], strategy: dict[str, Any], global_cfg: dict[str, Any], live: bool) -> dict[str, str]:
    env = dict(base_env)

    account = strategy["account_address"]
    vault = strategy["vault_address"]
    key_env = strategy["secret_key_env"]
    key_val = base_env.get(key_env, "")
    if not key_val:
        raise RuntimeError(f"Missing env var for secret key: {key_env}")

    env["HL_BASE_URL"] = TESTNET_URL
    env["HL_ACCOUNT_ADDRESS"] = account
    env["HL_VAULT_ADDRESS"] = vault
    env["HL_SECRET_KEY"] = key_val
    env["HL_ASSET"] = str(strategy.get("asset", global_cfg.get("asset", "BTC")))

    env["MAX_ABS_POSITION_BTC"] = str(strategy.get("max_abs_position_btc", global_cfg.get("max_abs_position_btc", 1.0)))
    env["MIN_TRADE_NOTIONAL_USD"] = str(strategy.get("min_trade_notional_usd", global_cfg.get("min_trade_notional_usd", 25)))
    env["CONFIDENCE_THRESHOLD"] = str(strategy.get("confidence_threshold", global_cfg.get("confidence_threshold", 0.55)))
    env["HL_DEFAULT_SLIPPAGE"] = str(strategy.get("hl_default_slippage", global_cfg.get("hl_default_slippage", 0.01)))

    env["DRY_RUN"] = "false" if live else "true"
    return env


def run_one(root: Path, env: dict[str, str], args: argparse.Namespace, strategy: dict[str, Any]) -> dict[str, Any]:
    cmd = [
        "./scripts/run_daily.py",
        "--flow-csv",
        args.flow_csv,
        "--forecast-json",
        args.forecast_json,
        "--min-abs-return-pct",
        str(strategy.get("min_abs_return_pct", 0.10)),
    ]
    if args.allow_heuristic:
        cmd.append("--allow-heuristic")

    proc = subprocess.run(
        cmd,
        cwd=root,
        env={**env, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
    )

    result: dict[str, Any] = {
        "strategy": strategy["name"],
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip(),
        "stdout": proc.stdout.strip(),
    }

    if proc.returncode == 0 and proc.stdout.strip():
        try:
            result["output"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result["output_parse_error"] = "stdout was not valid JSON"

    return result


def validate_no_live_collisions(strategies: list[dict[str, Any]], global_cfg: dict[str, Any]) -> None:
    seen: set[tuple[str, str]] = set()
    for s in strategies:
        if not s.get("enabled", True):
            continue
        account = s["account_address"]
        asset = str(s.get("asset", global_cfg.get("asset", "BTC")))
        key = (account.lower(), asset)
        if key in seen:
            raise RuntimeError(
                f"Live collision: multiple strategies share account {account} and asset {asset}. "
                "Use separate testnet accounts/vaults per strategy for parallel deployment."
            )
        seen.add(key)


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent

    load_dotenv(root / ".env")

    cfg = load_config(args.config)
    global_cfg = cfg.get("global", {})
    strategies = cfg.get("strategies", [])
    if not strategies:
        raise RuntimeError("No strategies found in config")

    if args.live:
        validate_no_live_collisions(strategies, global_cfg)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = root / args.report_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    base_env = dict(os.environ)
    runs: list[dict[str, Any]] = []

    for s in strategies:
        if not s.get("enabled", True):
            continue

        run_record: dict[str, Any] = {
            "strategy": s.get("name", "unnamed"),
            "mode": "live" if args.live else "dry-run",
        }
        try:
            env = strategy_env(base_env, s, global_cfg, live=args.live)
            res = run_one(root, env, args, s)
            run_record.update(res)
        except Exception as exc:
            run_record["returncode"] = -1
            run_record["stderr"] = str(exc)

        runs.append(run_record)

        name = run_record["strategy"].replace("/", "_").replace(" ", "_")
        (out_dir / f"{name}.json").write_text(json.dumps(run_record, indent=2), encoding="utf-8")

    # Compact summary for quick inspection.
    summary = []
    for r in runs:
        out = r.get("output", {})
        signal = out.get("signal", {}) if isinstance(out, dict) else {}
        execution = out.get("execution", {}) if isinstance(out, dict) else {}
        summary.append(
            {
                "strategy": r.get("strategy"),
                "mode": r.get("mode"),
                "returncode": r.get("returncode"),
                "signal_side": signal.get("side"),
                "target_position_btc": signal.get("target_position_btc"),
                "signal_reason": signal.get("reason"),
                "delta_btc": execution.get("delta_btc"),
                "dry_run": execution.get("dry_run"),
                "error": r.get("stderr"),
            }
        )

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps({"report_dir": str(out_dir), "n_strategies": len(summary), "mode": "live" if args.live else "dry-run"}, indent=2))


if __name__ == "__main__":
    main()
