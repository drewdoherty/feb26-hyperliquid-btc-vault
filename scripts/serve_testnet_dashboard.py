#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import http.server
import json
import os
import socketserver
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Serve local testnet dashboard (server-rendered + embedded charts).")
    p.add_argument("--monitor-dir", default="reports/testnet_monitor")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--with-monitor", action="store_true", help="Also run monitor in watch mode in a subprocess.")
    p.add_argument("--poll-seconds", type=int, default=15, help="Used only with --with-monitor.")
    p.add_argument("--config", default="config/testnet_strategies.json", help="Used only with --with-monitor.")
    return p.parse_args()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def fnum(value: Any, ndigits: int = 6) -> float:
    try:
        return round(float(value), ndigits)
    except Exception:
        return 0.0


def fmt_num(value: Any, ndigits: int = 2) -> str:
    try:
        return f"{float(value):.{ndigits}f}"
    except Exception:
        return "-"


def _prepare_chart_payload(
    strategy_rows: list[dict[str, str]],
    market_rows: list[dict[str, str]],
    benchmark_tokens: list[str],
) -> dict[str, Any]:
    recent_strat = strategy_rows[-2500:]
    labels = sorted({r.get("snapshot_time_utc", "") for r in recent_strat if r.get("snapshot_time_utc")})[-220:]

    strat_names = sorted({r.get("strategy", "unknown") for r in recent_strat})
    equity_usd_map: dict[str, dict[str, float]] = {s: {} for s in strat_names}
    for r in recent_strat:
        ts = r.get("snapshot_time_utc", "")
        s = r.get("strategy", "unknown")
        if ts in labels and s in equity_usd_map:
            equity_usd_map[s][ts] = fnum(r.get("account_value_usd", 0), 6)

    recent_market = market_rows[-5000:]
    token_price_map: dict[str, dict[str, float]] = {t: {} for t in benchmark_tokens}
    for r in recent_market:
        ts = r.get("snapshot_time_utc", "")
        token = r.get("token", "")
        if ts in labels and token in token_price_map:
            px = fnum(r.get("real_price_usd", 0), 10)
            if px > 0:
                token_price_map[token][ts] = px

    equity_usd_series = {
        s: [equity_usd_map[s].get(ts, None) for ts in labels] for s in strat_names
    }

    equity_by_token: dict[str, dict[str, list[float | None]]] = {}
    for token in benchmark_tokens:
        equity_by_token[token] = {}
        for s in strat_names:
            points: list[float | None] = []
            for ts in labels:
                eq = equity_usd_map[s].get(ts)
                px = token_price_map[token].get(ts)
                if eq is None or px is None or px <= 0:
                    points.append(None)
                else:
                    points.append(round(eq / px, 8))
            equity_by_token[token][s] = points

    benchmark_index: dict[str, list[float | None]] = {}
    for token in benchmark_tokens:
        series = [token_price_map[token].get(ts, None) for ts in labels]
        first = next((x for x in series if x is not None and x > 0), None)
        base = first if first else None
        if base is None:
            benchmark_index[token] = [None for _ in labels]
        else:
            benchmark_index[token] = [round((x / base) * 100.0, 4) if x else None for x in series]

    return {
        "labels": labels,
        "strategies": strat_names,
        "equity_usd": equity_usd_series,
        "equity_by_token": equity_by_token,
        "benchmark_index": benchmark_index,
    }


def render_dashboard(monitor_dir: Path) -> str:
    snapshot = read_json(monitor_dir / "latest_snapshot.json", {"entries": []})
    fills = read_csv_rows(monitor_dir / "fills.csv")
    strategy_rows = read_csv_rows(monitor_dir / "strategy_snapshots.csv")
    open_orders_rows = read_csv_rows(monitor_dir / "open_orders.csv")
    market_rows = read_csv_rows(monitor_dir / "market_prices.csv")

    rows = snapshot.get("entries", []) if isinstance(snapshot, dict) else []
    snapshot_time = html.escape(str(snapshot.get("snapshot_time_utc", "-"))) if isinstance(snapshot, dict) else "-"
    benchmark_tokens = list(snapshot.get("benchmark_tokens", ["BTC", "ETH", "HYPE"])) if isinstance(snapshot, dict) else ["BTC", "ETH", "HYPE"]

    total_acct = sum(float(r.get("account_value_usd", 0) or 0) for r in rows)
    total_pos = sum(float(r.get("position_notional_real_usd", 0) or 0) for r in rows)
    total_open = sum(int(r.get("open_orders_asset", 0) or 0) for r in rows)

    snapshot_rows_html = ""
    for r in rows:
        last_fill = r.get("last_fill_time_ms")
        if last_fill:
            try:
                from datetime import datetime, timezone

                last_fill_txt = datetime.fromtimestamp(int(last_fill) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception:
                last_fill_txt = "-"
        else:
            last_fill_txt = "-"

        snapshot_rows_html += (
            "<tr>"
            f"<td>{html.escape(str(r.get('strategy', '')))}</td>"
            f"<td class='mono'>{html.escape(str(r.get('account', '')))}</td>"
            f"<td>{html.escape(str(r.get('asset', '')))}</td>"
            f"<td>{fmt_num(r.get('mark_price_testnet'), 2)}</td>"
            f"<td>{fmt_num(r.get('real_price_usd'), 2)}</td>"
            f"<td>{fmt_num(r.get('position_asset'), 5)}</td>"
            f"<td>{fmt_num(r.get('position_notional_real_usd'), 2)}</td>"
            f"<td>{fmt_num(r.get('account_value_usd'), 2)}</td>"
            f"<td>{html.escape(str(r.get('open_orders_asset', 0)))}</td>"
            f"<td>{html.escape(str(r.get('new_fills', 0)))}</td>"
            f"<td class='mono'>{html.escape(last_fill_txt)}</td>"
            "</tr>"
        )

    fills_html = ""
    for f in reversed(fills[-60:]):
        fills_html += (
            "<tr>"
            f"<td class='mono'>{html.escape(f.get('fill_time_utc', '-') or '-')}</td>"
            f"<td>{html.escape(f.get('strategy', ''))}</td>"
            f"<td>{html.escape(f.get('coin', ''))}</td>"
            f"<td>{html.escape(f.get('dir', ''))}</td>"
            f"<td>{html.escape(f.get('side', ''))}</td>"
            f"<td>{html.escape(f.get('px', ''))}</td>"
            f"<td>{html.escape(f.get('sz', ''))}</td>"
            f"<td class='mono'>{html.escape(f.get('oid', ''))}</td>"
            "</tr>"
        )

    latest_open_orders = [r for r in open_orders_rows if r.get("snapshot_time_utc") == snapshot.get("snapshot_time_utc")]
    open_orders_html = ""
    for o in latest_open_orders:
        open_orders_html += (
            "<tr>"
            f"<td>{html.escape(o.get('strategy', ''))}</td>"
            f"<td class='mono'>{html.escape(o.get('account', ''))}</td>"
            f"<td>{html.escape(o.get('asset', ''))}</td>"
            f"<td>{html.escape(o.get('coin', ''))}</td>"
            f"<td>{html.escape(str(o.get('side', o.get('is_buy', ''))))}</td>"
            f"<td>{html.escape(o.get('px', ''))}</td>"
            f"<td>{html.escape(o.get('sz', ''))}</td>"
            f"<td class='mono'>{html.escape(o.get('oid', ''))}</td>"
            "</tr>"
        )

    if not snapshot_rows_html:
        snapshot_rows_html = "<tr><td colspan='11'>No snapshot yet. Start monitor with --with-monitor.</td></tr>"
    if not fills_html:
        fills_html = "<tr><td colspan='8'>No fills yet.</td></tr>"
    if not open_orders_html:
        open_orders_html = "<tr><td colspan='8'>No open orders at current snapshot.</td></tr>"

    chart_payload = _prepare_chart_payload(strategy_rows, market_rows, benchmark_tokens)
    chart_json = json.dumps(chart_payload)

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <meta http-equiv=\"refresh\" content=\"10\" />
  <title>Testnet Strategy Dashboard</title>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>
  <style>
    :root {{ --bg:#0b1020; --panel:#111a33; --text:#e8eeff; --muted:#8da0d1; --border:#25355f; }}
    body {{ margin:0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: radial-gradient(1000px 700px at 10% -10%, #1a2a54, var(--bg)); color:var(--text); }}
    .wrap {{ max-width:1400px; margin:0 auto; padding:16px; }}
    .sub {{ color:var(--muted); margin-bottom:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin-bottom:12px; }}
    .card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:10px; }}
    .k {{ color:var(--muted); font-size:12px; }}
    .v {{ font-size:22px; font-weight:700; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    .panel {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:10px; margin-bottom:12px; overflow:auto; }}
    .chart-wrap {{ height: 280px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ text-align:left; border-bottom:1px solid #2a3a63; padding:7px 6px; }}
    th {{ color:var(--muted); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    a {{ color:#8ec5ff; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>Hyperliquid Testnet Strategy Dashboard</h1>
    <div class=\"sub\">Auto-refresh every 10s | Snapshot: <span class=\"mono\">{snapshot_time}</span> | <a href=\"/dashboard\">reload now</a></div>

    <div class=\"grid\">
      <div class=\"card\"><div class=\"k\">Strategies</div><div class=\"v\">{len(rows)}</div></div>
      <div class=\"card\"><div class=\"k\">Portfolio Value (USD, real benchmark)</div><div class=\"v\">{total_acct:.2f}</div></div>
      <div class=\"card\"><div class=\"k\">Total Position Notional (USD, real prices)</div><div class=\"v\">{total_pos:.2f}</div></div>
      <div class=\"card\"><div class=\"k\">Open Orders</div><div class=\"v\">{total_open}</div></div>
    </div>

    <div class=\"panel\">
      <h3>Portfolio Equity Comparison in USD (Real)</h3>
      <div class=\"chart-wrap\"><canvas id=\"chartUsd\"></canvas></div>
    </div>

    <div class=\"panel\">
      <h3>Portfolio Equity Comparison in BTC Units</h3>
      <div class=\"chart-wrap\"><canvas id=\"chartBtc\"></canvas></div>
    </div>

    <div class=\"panel\">
      <h3>Portfolio Equity Comparison in ETH Units</h3>
      <div class=\"chart-wrap\"><canvas id=\"chartEth\"></canvas></div>
    </div>

    <div class=\"panel\">
      <h3>Portfolio Equity Comparison in HYPE Units</h3>
      <div class=\"chart-wrap\"><canvas id=\"chartHype\"></canvas></div>
    </div>

    <div class=\"panel\">
      <h3>Real Price Benchmark Indices (Base = 100)</h3>
      <div class=\"chart-wrap\"><canvas id=\"chartBench\"></canvas></div>
    </div>

    <div class=\"panel\">
      <h3>Current Snapshot</h3>
      <table>
        <thead>
          <tr><th>Strategy</th><th>Account</th><th>Asset</th><th>Mark (Testnet)</th><th>Price (Real)</th><th>Pos Asset</th><th>Pos USD (Real)</th><th>Acct USD</th><th>Open Orders</th><th>New Fills</th><th>Last Fill</th></tr>
        </thead>
        <tbody>{snapshot_rows_html}</tbody>
      </table>
    </div>

    <div class=\"panel\">
      <h3>Open Orders (Current Snapshot)</h3>
      <table>
        <thead>
          <tr><th>Strategy</th><th>Account</th><th>Asset</th><th>Coin</th><th>Side</th><th>Px</th><th>Sz</th><th>OID</th></tr>
        </thead>
        <tbody>{open_orders_html}</tbody>
      </table>
    </div>

    <div class=\"panel\">
      <h3>Recent Fills</h3>
      <table>
        <thead>
          <tr><th>Fill Time (UTC)</th><th>Strategy</th><th>Coin</th><th>Dir</th><th>Side</th><th>Px</th><th>Sz</th><th>OID</th></tr>
        </thead>
        <tbody>{fills_html}</tbody>
      </table>
    </div>
  </div>

  <script>
    const payload = {chart_json};
    const labels = payload.labels || [];
    const strategies = payload.strategies || [];
    const palette = ['#4ecdc4','#ffb703','#8ec5ff','#f28482','#b388eb','#84a59d','#ffd166'];

    function datasetsFromSeries(seriesObj) {{
      return strategies.map((s, i) => ({{
        label: s,
        data: (seriesObj && seriesObj[s]) ? seriesObj[s] : [],
        borderColor: palette[i % palette.length],
        borderWidth: 2,
        tension: 0.15,
        pointRadius: 0,
      }}));
    }}

    function makeLineChart(canvasId, datasets, yLabel) {{
      new Chart(document.getElementById(canvasId).getContext('2d'), {{
        type: 'line',
        data: {{ labels, datasets }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          scales: {{
            x: {{ ticks: {{ maxTicksLimit: 10 }} }},
            y: {{ title: {{ display: true, text: yLabel }} }}
          }}
        }}
      }});
    }}

    makeLineChart('chartUsd', datasetsFromSeries(payload.equity_usd), 'USD');
    makeLineChart('chartBtc', datasetsFromSeries((payload.equity_by_token || {{}}).BTC || {{}}), 'BTC units');
    makeLineChart('chartEth', datasetsFromSeries((payload.equity_by_token || {{}}).ETH || {{}}), 'ETH units');
    makeLineChart('chartHype', datasetsFromSeries((payload.equity_by_token || {{}}).HYPE || {{}}), 'HYPE units');

    const benchSeries = payload.benchmark_index || {{}};
    const benchTokens = Object.keys(benchSeries);
    const benchDatasets = benchTokens.map((t, i) => ({{
      label: `${{t}} real index`,
      data: benchSeries[t],
      borderColor: palette[(i + 3) % palette.length],
      borderWidth: 2,
      borderDash: [6, 6],
      tension: 0.15,
      pointRadius: 0,
    }}));
    makeLineChart('chartBench', benchDatasets, 'Index (base=100)');
  </script>
</body>
</html>"""


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    monitor_dir = root / args.monitor_dir

    monitor_proc = None
    if args.with_monitor:
        cmd = [
            sys.executable,
            str(root / "scripts" / "monitor_testnet_strategies.py"),
            "--config",
            args.config,
            "--watch",
            "--poll-seconds",
            str(args.poll_seconds),
        ]
        monitor_proc = subprocess.Popen(cmd, cwd=root, env={**os.environ, "PYTHONPATH": "src"})
        print(f"Started monitor subprocess pid={monitor_proc.pid}")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/dashboard") or self.path == "/":
                body = render_dashboard(monitor_dir).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            rel = self.path.lstrip("/").split("?", 1)[0]
            target = monitor_dir / rel
            if target.exists() and target.is_file():
                data = target.read_bytes()
                ctype = "application/json" if target.suffix == ".json" else "text/plain; charset=utf-8"
                if target.suffix == ".csv":
                    ctype = "text/csv; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), Handler) as httpd:
        url = f"http://{args.host}:{args.port}/dashboard"
        print(f"Serving: {url}")
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if monitor_proc is not None:
                monitor_proc.terminate()


if __name__ == "__main__":
    main()
