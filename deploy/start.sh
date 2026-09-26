#!/bin/sh
set -e
# Kavach Console (Phase 3): Streamlit next to the API. It reads the API over HTTP only.
export PORTAL_API_URL="${PORTAL_API_URL:-http://127.0.0.1:${PORT:-8000}}"
streamlit run portal/app.py --server.port "${PORTAL_PORT:-8501}" --server.address 0.0.0.0 &
exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"
