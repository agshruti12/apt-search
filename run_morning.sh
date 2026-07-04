#!/bin/bash
# Morning pipeline: scrape → export → build → deploy to GitHub Pages
# Triggered by launchd at 9am (or on next wake if Mac was asleep).

PROJECT="/Users/agshruti/Downloads/apt agent design"
PYTHON="/Users/agshruti/anaconda3/bin/python3"
NPM="/Users/agshruti/.nvm/versions/node/v22.3.0/bin/npm"
LOG="$PROJECT/logs/morning_run.log"

mkdir -p "$PROJECT/logs"
exec >> "$LOG" 2>&1

echo "=============================="
echo "Morning run: $(date)"
echo "=============================="

# 1. Run all scrapers
echo "[1/4] Running scrapers..."
cd "$PROJECT"
"$PYTHON" agent.py
if [ $? -ne 0 ]; then
  echo "ERROR: scraper failed. Aborting."
  exit 1
fi

# 2. Export DB → JSON
echo "[2/4] Exporting listings..."
"$PYTHON" export_listings.py
if [ $? -ne 0 ]; then
  echo "ERROR: export failed. Aborting."
  exit 1
fi

# 3. Build the frontend
echo "[3/4] Building frontend..."
cd "$PROJECT/web"
"$NPM" run build
if [ $? -ne 0 ]; then
  echo "ERROR: build failed. Aborting."
  exit 1
fi

# 4. Deploy to GitHub Pages
echo "[4/4] Deploying to GitHub Pages..."
"$NPM" run deploy
if [ $? -ne 0 ]; then
  echo "ERROR: deploy failed."
  exit 1
fi

echo "Done! Site updated at https://agshruti12.github.io/apt-search/"
echo ""
