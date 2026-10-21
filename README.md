# Portfolio Dashboard V2 — Phase 4: Core Screen Integration

This is a clean-room rebuild, not a refactor of the legacy monolithic `app.py`. The legacy app is used only to inventory data requirements and preserve regression examples. The current milestone connects the shared chronological engine to Momentum Monitor and Pattern Scanner; it does not claim that every legacy screen or live DB integration is complete.

## Current state

Implemented and unit-tested:
- Canonical OHLCV normalization and data validation.
- Pivot distance, PIVOT APPROACH, EOD Stage transitions, Entry Quality normalization/extension gate.
- Initial Risk and monotonic ATR trailing stop with EOD-only exit-signal creation.
- Pocket Pivot core volume and moving-average context rules.
- Nine initial OHLCV pattern detectors plus one primary-pattern selector.
- Synthetic-fixture tests for detector geometry and rebase boundary handling.
- Optional yfinance daily-bars adapter with explicit settings and US-session cutoff tests; the UI requires the user to select this provisional source explicitly.
- Momentum Monitor and Pattern Scanner connected to one shared analysis service with candidate/breakout pivot distances, 20D reference, official EOD stage, rebase state, RVOL, returns, and optional relative strength vs SPY.
- Incremental chronological replay from the seven bundled state snapshots when the historical close matches; unseeded symbols use a clearly labelled rolling 260-bar replay.
- Local historical OHLCV capture utility and a regression runner that differentiates observed output from chart-labelled PASS/FAIL.
- Static Supabase call-site inventory from legacy source; live schema/RLS not inspected.

## Still unverified / disconnected

- Actual historical chart regressions for AMAT/MU/SOXX/META/GOOG/STX/LRCX. Dates and expected outcomes are intentionally not guessed.
- Final market-data provider, dividend/split adjustment basis, and completed-bar policy. The yfinance adapter is a candidate for parity testing only.
- Actual Supabase table columns, primary/unique keys, foreign keys, types, views, RPCs, RLS, and triggers.
- Production approval of the price source/adjustment/completed-bar policy and a cloud browser smoke test.
- Actual Supabase read-only connection and full screen-by-screen migration.

Pattern thresholds are initial automation heuristics from Functional Spec v0.12, not universal chart-pattern truths. V2 is not a production trade-signal system yet.

## Safety boundaries

- No Supabase writes or schema migrations.
- Supabase access remains disabled by default; do not turn it on based solely on the static inventory.
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

In the sidebar, choose a data source explicitly. `Yahoo Finance (provisional)` fetches adjusted daily bars and uses the current candidate completion policy; this policy is not yet approved for production trading. `Bundled regression data (historical)` is a fixed historical fixture ending 2026-10-19 and must not be interpreted as live market data.

### Streamlit Community Cloud

Deploy this repository/project folder with `app.py` as the entry point. Cloud installation uses `requirements.txt`. No Supabase secrets are required for this phase because both core screens are read-only and use market-data bars. Supabase/portfolio records are not yet connected. Before using the Yahoo provider as a production signal source, approve the price-adjustment and completed-bar policy and complete price-parity checks.

### Historical state note

For the seven bundled regression symbols, the app can seed state from the latest bundled chronological replay snapshot and incrementally process newer bars if the source close on the snapshot date matches within 0.5%. The snapshot is an engine-generated starting state, not an independently labelled ground truth. For other symbols, the screen uses a rolling 260-bar replay and labels that provenance; it cannot claim to reconstruct older breakout history beyond that window.
