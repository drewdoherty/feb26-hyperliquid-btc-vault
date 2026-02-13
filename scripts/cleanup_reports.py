#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)

    removed = 0
    for p in REPORTS.rglob(".DS_Store"):
        p.unlink(missing_ok=True)
        removed += 1

    key_paths = [
        "strategy_comparison/strategy_summary.csv",
        "strategy_comparison/portfolio_paths_100usd.png",
        "v2_sep2025/v2_summary.json",
        "v2_sep2025/v2_portfolio_100usd.png",
        "v2_variants_sep2025_fast/v2_leaderboard.csv",
        "v2_variants_sep2025_fast/v2_variant_decision_summary.csv",
        "v2_variants_sep2025_fast/v2_variant_signal_drivers.csv",
        "v2_variants_sep2025_fast/v2_decision_index.md",
    ]

    lines = [
        "# Reports Index",
        "",
        "Canonical inspection outputs:",
        "",
    ]
    for rel in key_paths:
        p = REPORTS / rel
        status = "exists" if p.exists() else "missing"
        lines.append(f"- `{rel}` ({status})")

    lines += [
        "",
        "Variant trace location:",
        "",
        "- `reports/v2_variants_sep2025_fast/<variant>/v2_decision_trace.csv`",
        "",
        f"Cleanup removed {removed} .DS_Store file(s).",
    ]

    out = REPORTS / "INDEX.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
