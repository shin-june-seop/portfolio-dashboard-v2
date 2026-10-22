# V2 EOD Data Policy Patch

## User-approved policy

- EOD-only: current-session bars are withheld until 16:15 America/New_York, a 15-minute buffer after the standard 16:00 close.
- `exchange-calendars` / `XNYS` determines the latest expected trading session. Missing or stale expected sessions fail closed rather than using yesterday's data.
- Technical analysis uses Yahoo OHLC with `auto_adjust=False`; returns and relative strength use the separate `Adj Close` series adjusted for splits/distributions.
- RVOL is the latest completed bar's volume divided by the prior 20 completed bars' average volume (today excluded); breakout confirmation remains >= 1.30x.
- Missing/non-finite OHLCV, missing/invalid `Adj Close`, a zero-volume latest bar, or a stale expected session prevents a symbol from being analyzed.
- Day `%` is Close-to-Close price movement in the technical OHLC basis. Multi-period performance and SPY-relative strength use `Adj Close`.
- The future Live Monitor must be separate and must not alter EOD state.

## Compatibility behavior

The bundled snapshots were created with `auto_adjust=True`. Yahoo EOD data now includes a separate `Adj Close`, so `analyze_symbol` does not resume the legacy adjusted-OHLC state snapshot. It uses an explicit 260-bar rolling replay to avoid mixing historical pivots from different price bases. The app warns that events older than those 260 bars may not be reconstructed. Bundled historical mode keeps the legacy basis and snapshot behavior.

## Files included (22)

- `app.py`, `requirements.txt`, `pyproject.toml`
- `dashboard_v2/engines/data_validation.py`
- `dashboard_v2/services/yfinance_provider.py`
- `dashboard_v2/services/market_data_service.py`
- `dashboard_v2/services/screening_service.py`
- `tests/test_yfinance_provider.py`
- `tests/test_screening_service.py`
- `tests/test_data_and_pivots.py`
- `tests/test_market_data_service.py` (new)
- `tools/capture_historical_ohlcv.py`
- `tools/audit_historical_ohlcv.py`
- `DATA_SOURCE_POLICY.md`, `README.md`, `ARCHITECTURE.md`
- `PHASE3_IMPLEMENTATION.md`, `PHASE3_2_IMPLEMENTATION.md`, `PHASE3_3_IMPLEMENTATION.md`, `PHASE4_IMPLEMENTATION.md`
- `data/regression/README.md`, `docs/HISTORICAL_REVIEW_WORKFLOW.md`

## Verification

- `python -m compileall -q app.py dashboard_v2 tools tests`: passed.
- `python -m pytest -q`: **119 passed**.
- Tests use fake/mocked market-data frames; no live Yahoo request was part of this verification.

This patch has not been committed to the user's GitHub repository. Apply it via GitHub and wait for Actions to pass before rebooting the Streamlit app.
