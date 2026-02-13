from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from .types import DailyFlow


class IbitFlowRepository:
    def __init__(self, csv_path: str) -> None:
        self.csv_path = Path(csv_path)

    def latest(self) -> DailyFlow:
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"Flow file not found at {self.csv_path}. Expected CSV columns: date,net_flow_usd"
            )

        last_row: dict[str, str] | None = None
        with self.csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date") and row.get("net_flow_usd"):
                    last_row = row

        if not last_row:
            raise ValueError(f"No valid rows found in {self.csv_path}")

        dt = datetime.strptime(last_row["date"], "%Y-%m-%d").date()
        return DailyFlow(dt=dt, net_flow_usd=float(last_row["net_flow_usd"]))

    def latest_date(self) -> date:
        return self.latest().dt
