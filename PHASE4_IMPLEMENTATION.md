# Phase 4 — Momentum Monitor / Pattern Scanner integration

## Implemented

- Replaced the placeholder Momentum Monitor with a real scan table driven by completed daily OHLCV.
- Connected Pattern Scanner to the same canonical analysis output; it filters the same one-primary-pattern/stage rows rather than implementing its own pivot/stage logic.
- Added a shared `dashboard_v2.services.screening_service.analyze_symbol` service.
- Displays price/day move, primary pattern, Candidate Pivot and distance, historical Breakout Pivot and distance, 20D Reference, EOD Stage, rebase state/reason, RVOL, 1/3/6/12-month returns, and 3-month relative performance vs SPY when benchmark data is available.
- Added selected-symbol close/SMA chart and a diagnostic expander with state provenance, breakout events/failures, and PP signal reasons.
- Added an explicit data-source choice: no data by default, Yahoo Finance EOD under the approved data policy, or bundled historical regression data. Historical fixture data is clearly labelled as stale/historical.
- Added snapshot resume support to chronological replay. For historical fixture mode, state resumes from the bundled engine snapshot only when the close on that date matches within 0.5%. Yahoo EOD uses the approved separate price basis and intentionally uses a rolling 300-bar replay rather than mixing price-basis-incompatible seed state.
- Symbols without a compatible snapshot use a rolling 300-bar replay and the UI discloses that older breakout history may not be reconstructed.
- Corrected official stage precedence so an active rebase suppresses `BREAKOUT` hold display; an EOD close below the active Breakout Pivot still has priority as `BREAKOUT FAILURE`.
- Added Streamlit Cloud `requirements.txt` and deployment/run documentation.

## Verification performed

- `pytest -q`: **96 passed**.
- `python -m py_compile app.py dashboard_v2/services/screening_service.py dashboard_v2/services/chronological_replay.py dashboard_v2/engines/stage_engine.py`: passed.
- Ran the shared service against all seven bundled OHLCV fixtures. It returned current-as-of rows through 2026-10-19 and preserved Candidate Pivot vs Breakout Pivot separation.
- Confirmed AMAT/MU/SOXX/META/STX/LRCX show `BASE FORMING / NO VALID PATTERN`, blank Candidate Pivot, and preserved historical Breakout Pivot while their bundled snapshot says rebase is active. GOOG retains distinct Candidate Pivot and Breakout Pivot.

## Explicit limitations

- Streamlit is not installed in the current build container, so the browser/UI has not been launched for a visual smoke test. `requirements.txt` installs Streamlit for local or Streamlit Community Cloud execution.
- Yahoo Finance EOD is selectable under `DATA_SOURCE_POLICY.md`: `auto_adjust=False` technical OHLC, separate `Adj Close` returns/RS, 16:15 ET finalization buffer, expected XNYS session freshness validation, and fail-closed handling. The app is read-only; no automatic trading is authorized.
- The bundled replay snapshot is generated engine state, not independently labelled ground truth. Historical chart accuracy remains `OBSERVED_NOT_VALIDATED` until expected outcomes are manually labelled.
- Supabase/portfolio positions/transactions and the rest of the legacy screens are not connected yet. No DB writes, order submission, or automatic trading was added.
