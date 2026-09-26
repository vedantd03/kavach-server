#!/bin/sh
set -e
# SERVICE_ROLE=console -> Kavach Console only (reads the API over HTTP at PORTAL_API_URL).
# default              -> API only; RUN_PORTAL=1 also starts the console on PORTAL_PORT (single box).
if [ "${SERVICE_ROLE:-api}" = "console" ]; then
  exec streamlit run portal/app.py --server.port "${PORT:-8501}" --server.address 0.0.0.0
fi
if [ "${RUN_PORTAL:-0}" = "1" ]; then
  export PORTAL_API_URL="${PORTAL_API_URL:-http://127.0.0.1:${PORT:-8000}}"
  streamlit run portal/app.py --server.port "${PORTAL_PORT:-8501}" --server.address 0.0.0.0 &
fi
exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"
