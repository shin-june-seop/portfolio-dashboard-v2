# Phase 11 Patch Notes

## Files

- `app.py` — Overview cards integrated; separate `Market Regime` sidebar entry removed. Market Score / Market Regime remains on Momentum Monitor.
- `dashboard_v2/services/overview_market_service.py` — completed-session index data, validation, ETF-proxy RVOL, and responsive compact candlestick-card rendering.
- `tests/test_overview_market_service.py` — panel, stale-data, proxy-volume, HTML, and navigation regression tests.
- `PHASE11_OVERVIEW_INDEX_PANELS.md` — design and policy notes.
- `README.md`, `ARCHITECTURE.md` — updated screen ownership and market-data boundaries.

## Supabase / secrets

No SQL, Supabase schema, or Secrets changes are required.

## Apply

Extract the patch ZIP preserving paths. Replace `app.py` and the two documentation files; add the service, test file and this phase note. Commit and inspect GitHub Actions, then open Overview and Momentum Monitor in Streamlit.
