# Phase 3.2 — Data audit and historical-event review aids

## What changed

1. `tools/audit_historical_ohlcv.py`
   - Validates file presence, required OHLCV structure, positive prices, non-negative volume, duplicate dates, capture-metadata row/date-range agreement, unusual >20% close-to-close moves, and calendar gaps >5 days.
   - Large moves, zero volume, gaps, and adjustment-policy uncertainty are warnings to review; they are not automatically treated as bad data.
   - Writes `data/regression/audit/ohlcv_audit.csv` and `ohlcv_audit_summary.json`.

2. `tools/build_rebase_review.py`
   - Finds entries into the provisional RB-01 proxy: completed close <= 90% of the rolling 20-bar highest High.
   - Samples candidate observations at bars 0, 19, 24, 34, 44, 59, and 79 after each trigger when they occur before the next trigger.
   - For each snapshot, compares pattern output without rebase gating with output gated by the proxy trigger date, including pivot and pattern start date.
   - Writes `data/regression/rebase_review_candidates.csv`.

## Important limits

- The EOD price policy is now defined in `DATA_SOURCE_POLICY.md`: `auto_adjust=False` technical OHLC plus separate `Adj Close` for total-return metrics. Captured data still needs independent chart labels before it can establish pattern accuracy.
- The rebase review only approximates RB-01 using price data. OHLCV alone cannot reconstruct historic RB-02 (prior active candidate pivot) or RB-03 (preserved breakout pivot) without the prior dashboard/journal state.
- The review CSV is not an oracle and cannot produce PASS/FAIL by itself. Human review of historical screenshots/journal/trade records is still required before setting `manifest.csv` expected labels.
- No Supabase connection, schema mutation, order, or dashboard live-signal activation is introduced.

## Tests

Run the full suite from the project root with Python 3.13:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Commands

Audit captured files:

```powershell
.\.venv\Scripts\python.exe tools/audit_historical_ohlcv.py
```

Build the provisional rebase review worksheet:

```powershell
.\.venv\Scripts\python.exe tools/build_rebase_review.py
```

Then provide `data/regression/audit/ohlcv_audit.csv`, `data/regression/audit/ohlcv_audit_summary.json`, and `data/regression/rebase_review_candidates.csv` for review. Do not share API secrets. Public OHLCV CSVs are optional if the reports are sufficient.
