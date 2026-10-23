# Portfolio Dashboard V2 — Phase 4: Core Screen Integration

This is a clean-room rebuild, not a refactor of the legacy monolithic `app.py`. The legacy app is used only to inventory data requirements and preserve regression examples. The current milestone connects the shared chronological engine to Momentum Monitor and Pattern Scanner; it does not claim that every legacy screen or live DB integration is complete.

## Current state

- V2-only authenticated watchlist CRUD (`public.v2_watchlist`) is the first persistent user-data feature. It uses a clean, separate Supabase project, email/password sign-in, row-level security, and no legacy-data import. See `WATCHLIST_SETUP.md`.
- Enabled watchlist symbols are the shared source for Momentum Monitor and Pattern Scanner. The next planned stage is a separate intraday Live Monitor; PP Journal follows after that.

Implemented and unit-tested:
- Canonical OHLCV normalization and data validation.
- Pivot distance, PIVOT APPROACH, EOD Stage transitions, Entry Quality normalization/extension gate.
- Initial Risk and monotonic ATR trailing stop with EOD-only exit-signal creation.
- Pocket Pivot core volume and moving-average context rules.
- Nine initial OHLCV pattern detectors plus one primary-pattern selector.
- Synthetic-fixture tests for detector geometry and rebase boundary handling.
- Yahoo Finance daily-bar adapter with a 16:15 ET finalization buffer, XNYS session freshness checks, split-normalized technical OHLC, and separate dividend/split-adjusted Adj Close for holding-period returns.
- Momentum Monitor and Pattern Scanner connected to one shared analysis service with candidate/breakout pivot distances, 20D reference, official EOD stage, rebase state, RVOL, returns, and optional relative strength vs SPY.
- Incremental chronological replay from the seven bundled state snapshots when the historical close matches; unseeded symbols use a clearly labelled rolling 300-bar replay.
- Local historical OHLCV capture utility and a regression runner that differentiates observed output from chart-labelled PASS/FAIL.
- Static Supabase call-site inventory from legacy source; live schema/RLS not inspected.

## Still unverified / disconnected

- Actual historical chart regressions for AMAT/MU/SOXX/META/GOOG/STX/LRCX. Dates and expected outcomes are intentionally not guessed.
- Yahoo Finance is approved for V2 EOD analysis under the explicit policy in `DATA_SOURCE_POLICY.md`; upstream values may be revised and this is not an automatic-trading approval.
- Broad historical pattern-label validation and a cloud browser smoke test for each deployed revision.
- Watchlist persistence requires the user to create a new empty Supabase project and configure Streamlit Cloud secrets.
- Live Monitor and PP Journal persistence are not implemented yet; they are the next planned phases.

Pattern thresholds are initial automation heuristics from Functional Spec v0.12, not universal chart-pattern truths. V2 is not a production trade-signal system yet.

## Watchlist deployment

1. Create a new empty Supabase project.
2. Run `sql/v2_watchlist_schema.sql` in that project.
3. Create your login user in Supabase Authentication → Users; disable public sign-ups if desired.
4. Add `SUPABASE_URL` and `SUPABASE_ANON_KEY` to Streamlit Cloud app Secrets. Never use the `service_role` key.
5. See `WATCHLIST_SETUP.md` for full steps.

## Safety boundaries

- V2 writes only user-owned rows to the new `v2_watchlist` table after login and RLS checks. It does not access or modify legacy tables.
- No order submission, broker connection, automatic position closure, or invented account data.
- Existing dashboard remains untouched until V2 acceptance checks pass.

## Run locally (Windows PowerShell)

From the project folder, the most robust workflow avoids PowerShell activation entirely:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Optional market-data capture requires network access and the yfinance extra:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,market-data]"
.\.venv\Scripts\python.exe tools/capture_historical_ohlcv.py
.\.venv\Scripts\python.exe tools/run_historical_regression.py
```

The capture writes only local files under `data/regression/ohlcv/`. Fill `data/regression/manifest.csv` with exact chart-reviewed dates and expected results before treating any case as validated. `OBSERVED_NOT_VALIDATED` is not a passing regression.

## Supabase inventory

- `docs/legacy_db_inventory/LEGACY_DB_INVENTORY.md`: static summary.
- `docs/legacy_db_inventory/legacy_db_operations.csv`: call-site detail.
- `tools/inventory_legacy_db.py`: rerunnable static scanner.

These documents do not replace inspection of the actual live Supabase schema and security policies.

## Source of truth

`Dashboard_V2_Functional_Spec_v0.12.md` is the functional source of truth. `ARCHITECTURE.md` documents module boundaries, and `PHASE3_IMPLEMENTATION.md` records the phase deliverables; `PHASE3_1_FIX_REPORT.md` documents the Date-index bug and fix. Rule changes should update specification/config/tests together.

## Phase 3.2: audit and historical review

See `PHASE3_2_IMPLEMENTATION.md` and `docs/HISTORICAL_REVIEW_WORKFLOW.md`. With the captured CSVs copied into `data/regression/ohlcv`, run:

```powershell
.\.venv\Scripts\python.exe tools/audit_historical_ohlcv.py
.\.venv\Scripts\python.exe tools/build_rebase_review.py
.\.venv\Scripts\python.exe -m pytest
```

The first two commands are local, read-only review tools. They do not access the internet or Supabase.


## Phase 3.3 review tools

After auditing OHLCV and building the rebase snapshot worksheet, use these local-only tools:

```powershell
.\.venv\Scripts\python.exe tools/review_ohlcv_large_moves.py
.\.venv\Scripts\python.exe tools/summarize_rebase_review.py
```

These create an event-date worksheet for large close moves and a cycle-level summary of sampled post-rebase patterns. They do not label any historical case as ground truth. See `PHASE3_3_IMPLEMENTATION.md`.

## Momentum Monitor / Pattern Scanner (Phase 4)

The two core screens now consume the same `dashboard_v2.services.screening_service.analyze_symbol` output. The screens do not independently recalculate candidate pivots, breakout pivots, rebase state, or official stage.

### Run locally for development

Use Python 3.13:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

In the sidebar, choose a data source explicitly. `Yahoo Finance (EOD)` fetches split-normalized OHLC plus a separate `Adj Close` series. It withholds the current US session until 16:15 ET, validates the expected latest XNYS session, and fails closed on missing/stale required data. Multi-period returns/RS use `Adj Close`; technical pivots use OHLC. `Bundled regression data (historical)` is a fixed historical fixture ending 2026-10-19 and must not be interpreted as live market data.

### Streamlit Community Cloud

Deploy this repository/project folder with `app.py` as the entry point. Cloud installation uses `requirements.txt`. No Supabase secrets are required for this phase because both core screens are read-only and use market-data bars. Supabase/portfolio records are not yet connected. Yahoo Finance remains an external source whose values may be revised. Price-source and completed-bar policies are documented in `DATA_SOURCE_POLICY.md`; the app stays read-only and no automatic orders are submitted.

### Historical state note

The historical fixture mode can use its matching bundled replay snapshot. Yahoo EOD bars use a separate split-normalized OHLC basis, so they intentionally do not resume the legacy auto-adjusted seed; they use a transparent rolling 300-bar replay. For those symbols, older breakout history beyond the window may not be reconstructed. All bundled states remain engine-generated starting states, not independently labelled ground truth.
