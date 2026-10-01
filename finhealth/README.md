# Fiscal Fit - Financial Health Checker

Flask app: landing (name) -> finance form -> animated score, charts, tips -> PDF report.

## Run locally
pip install -r requirements.txt
python app.py   # http://127.0.0.1:5000

## Deploy on Render (free)
1. Push this folder's contents to a GitHub repo (app.py at the repo root).
2. Render -> New -> Blueprint (uses render.yaml), or New -> Web Service:
   - Build: pip install -r requirements.txt
   - Start: gunicorn app:app
   - Plan: Free
