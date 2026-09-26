# Kavach Console (Phase 3)

Streamlit console over the server's HTTP API (never the database).

    pip install -r portal/requirements.txt
    streamlit run portal/app.py

Env: `PORTAL_API_URL` (default `http://127.0.0.1:8000`), `ADMIN_TOKEN` (sent as `X-Admin-Token`),
`PORTAL_PASSWORD` (optional sign-in), `PORTAL_REFRESH_SEC` (Overview refresh, default 3).

Pages: Overview (live), Findings (masked values; AI tier vs final code tier), Approvals (Phase 1B),
Audit trail (CSV evidence pack), Accuracy (from `/admin/eval`).

Deployed next to the API by `deploy/start.sh` on `${PORTAL_PORT:-8501}`.
