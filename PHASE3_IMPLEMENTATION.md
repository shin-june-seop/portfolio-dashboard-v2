# Phase 3 Implementation — Historical Regression and Data Boundaries

**Spec baseline:** `Dashboard_V2_Functional_Spec_v0.12.md`  
**Status:** local regression harness implemented; V2 EOD data policy approved on 2026-10-22. Broad independent chart ground truth remains incomplete.

## Delivered

1. **Yahoo Finance EOD adapter** (`dashboard_v2/services/yfinance_provider.py`). It requests `auto_adjust=False`, preserves separate `Adj Close`, waits until 16:15 ET, checks the expected XNYS session, and fails closed on missing/stale/incomplete daily data.
2. **OHLCV capture utility** (`tools/capture_historical_ohlcv.py`). It can save current daily-bar history for AMAT, MU, SOXX, META, GOOG, STX and LRCX as local CSVs and creates `capture_metadata.json` with dates/policy. It never touches Supabase.
3. **Historical regression runner** (`tools/run_historical_regression.py`). It runs the V2 detector on a given ticker/as-of-date and writes the observed pattern, pivot, pattern start, candidate diagnostics, and status.
4. **Ground-truth manifest** (`data/regression/manifest.csv`). It contains the known issue scenarios without inventing dates or expected prices. Blank date/expected-label cases are marked pending, not passed.
5. **Legacy database call-site inventory** (`docs/legacy_db_inventory/`). A static AST scan found 56 query-operation call sites across 14 literal/dynamic table-or-RPC targets in `/mnt/data/app.py`. This is source inventory only, not a live schema/RLS inspection.
6. App Monitor now separates “test harness implemented” from “real historical regression unverified.”

## Regression status meanings

- `NEEDS_LABEL`: no explicit as-of date in manifest; no date is inferred.
- `MISSING_OHLCV`: the corresponding local ticker CSV is absent.
- `INVALID_OHLCV`: canonical data contract failed.
- `OBSERVED_NOT_VALIDATED`: engine output was produced, but no expected result was supplied; this is not a pass.
- `PASS`: a manually entered expected pattern and/or pivot range matched.
- `FAIL`: one or more supplied expected values did not match.

## Run locally

Install test and provider extras:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,market-data]"
```

Capture five years of candidate daily history (network access required):

```powershell
.\.venv\Scripts\python.exe tools/capture_historical_ohlcv.py
```

Review and fill `data/regression/manifest.csv` with exact historical `as_of_date`, `rebase_started_at`, known historical pivot values, and chart-reviewed expected pattern/pivot ranges. Do not fill values based on what the engine currently returns; expectations should come from chart review, old records, or transaction/journal evidence.

Run regression:

```powershell
.\.venv\Scripts\python.exe tools/run_historical_regression.py
```

Review `data/regression/results.csv`. All expected results must be sourced/labeled before a case can count as PASS.

## Still not done

- Representative cases have been chart-reviewed, but broad historical chart-labelled ground truth and statistical pattern accuracy are still not complete.
- No live Supabase schema/columns/keys/RLS were inspected. `docs/legacy_db_inventory/LEGACY_DB_INVENTORY.md` is a static call-site list only.
- Yahoo Finance is connected to Momentum Monitor/Pattern Scanner for read-only EOD analysis. It remains an external source whose historical bars may be revised.
- Breakout Pivot/event persistence and trade-entry-time Initial Risk still need integration-level historical acceptance tests after canonical dates/records are assembled.

## Verification performed on the package

- Python compileall completed.
- Unit tests before Phase 3.1: 77 passed.
- Provider tests use mocked historical bars; no external market-data call is made during unit tests.
- Regression runner tests use generated test fixtures; these are implementation tests, not real-world chart validation.
