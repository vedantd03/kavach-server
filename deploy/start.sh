#!/bin/sh
set -e
# Phase 3 will start the portal here, e.g.:
# streamlit run portal/app.py --server.port "${PORTAL_PORT:-8501}" --server.address 0.0.0.0 &
exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"
