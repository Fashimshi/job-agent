#!/bin/bash
# Job agent scheduled run — Monday & Friday at 4:30 PM ET
# Auto-applies to qualified jobs on trusted platforms (Greenhouse, Lever)

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

# LIVE mode — actually submits applications on trusted ATS platforms
python -m src.cli run --no-dry-run 2>&1 | tee -a data/scheduled_run.log

echo "--- Run completed at $(date) ---" >> data/scheduled_run.log
