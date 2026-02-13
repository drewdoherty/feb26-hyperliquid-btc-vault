#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_MINUTE="${CRON_MINUTE:-15}"
CRON_HOUR="${CRON_HOUR:-9}"

# Daily at CRON_HOUR:CRON_MINUTE local machine time.
CRON_CMD="cd \"$ROOT_DIR\" && /bin/zsh -lc './scripts/daily_pipeline.sh'"
CRON_LINE="$CRON_MINUTE $CRON_HOUR * * * $CRON_CMD"

EXISTING="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "$EXISTING" | grep -v "scripts/daily_pipeline.sh" || true)"

{
  printf '%s\n' "$FILTERED"
  printf '%s\n' "$CRON_LINE"
} | sed '/^$/d' | crontab -

echo "Installed cron job: $CRON_LINE"
