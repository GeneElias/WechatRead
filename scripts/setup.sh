#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null && [ -z "${WEREAD_CHROMIUM:-}" ]; then
  PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$PWD/.data/browsers}" .venv/bin/python -m playwright install chromium
fi
.venv/bin/python -m pytest -q
