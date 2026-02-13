from __future__ import annotations

from datetime import datetime
from io import StringIO

import pandas as pd
import requests

FARSIDE_ALL_DATA_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"


def _to_iso_date(value: str) -> str:
    return datetime.strptime(value.strip(), "%d %b %Y").date().isoformat()


def fetch_ibit_flows_usd(url: str = FARSIDE_ALL_DATA_URL) -> pd.DataFrame:
    resp = requests.get(url, timeout=30, headers={"User-Agent": "hv-btc-vault/0.1"})
    resp.raise_for_status()

    tables = pd.read_html(StringIO(resp.text))
    if not tables:
        raise RuntimeError("No tables found on Farside all-data page")

    df = None
    for candidate in tables:
        lower_map = {str(c).strip().lower(): c for c in candidate.columns}
        if "date" in lower_map and "ibit" in lower_map:
            df = candidate.copy()
            break
    if df is None:
        raise RuntimeError("Could not find table with Date and IBIT columns on Farside page")

    lower_map = {str(c).strip().lower(): c for c in df.columns}
    date_col = lower_map["date"]
    ibit_col = lower_map["ibit"]

    out = df[[date_col, ibit_col]].rename(columns={date_col: "date", ibit_col: "ibit_musd"})
    out["date"] = out["date"].astype(str).str.strip()
    out = out[out["date"].str.match(r"^\d{1,2}\s[A-Za-z]{3}\s\d{4}$", na=False)].copy()

    def parse_flow(v: object) -> float | None:
        s = str(v).strip().replace(",", "")
        if s in {"-", "nan", "None", ""}:
            return None
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        return float(s)

    out["ibit_musd"] = out["ibit_musd"].apply(parse_flow)
    out = out.dropna(subset=["ibit_musd"])
    out["date"] = out["date"].map(_to_iso_date)
    out["net_flow_usd"] = out["ibit_musd"] * 1_000_000.0

    out = out[["date", "net_flow_usd"]].drop_duplicates("date", keep="last").sort_values("date")
    return out.reset_index(drop=True)
