# Historical review workflow

## What is already confirmed

- The user's latest capture metadata reports all seven tickers as `OK`, each with 1,254 rows from 2021-10-20 through 2026-10-19.
- The `NEEDS_LABEL` status in the regression runner is expected because the manifest has no manually-reviewed dates/expected outputs yet.
- A successful download does not validate exact price adjustment basis or pattern accuracy.

## Next actions

1. Run OHLCV audit and inspect `ohlcv_audit.csv`. Investigate `FAIL`; review warnings and adjustment basis before numerical pivot comparisons.
2. Run the rebase review worksheet. It lists dates where the close first falls at least 10% below the rolling 20-bar high and compares unconstrained vs rebase-gated pattern observations.
3. Compare rows around known incidents against the old dashboard screenshots, trade journal, and chart. Record exact as-of date and prior pivot values from historical evidence, not by copying V2's observed output into expected values.
4. Only then fill manifest labels.
5. Run `tools/run_historical_regression.py`. A test is `PASS` only if its explicitly supplied expected condition matches observed output.

## Required manual evidence for known cases

- AMAT / MU / SOXX: as-of date where an old pattern incorrectly survived the drawdown, plus whether a valid post-rebase pattern existed.
- META: breakout-event date, preserved historical Breakout Pivot, and candidate pivot as of the comparison date.
- GOOG: as-of date and the separate candidate/breakout pivot values from the old dashboard/journal.
- STX: EOD date, active Breakout Pivot, and completed daily Close (intraday prices do not count as official failure).
- LRCX: entry date, entry fill price, ATR(14) available at that time, original saved Initial Risk/stop, and any later recomputed value if available.

These cases cover distinct invariants. Pattern regression does not substitute for breakout event persistence or trade-risk immutability tests.

## Phase 3.3: targeted review worksheets

After the Phase 3.2 audit and rebase snapshot builder:

```powershell
.\.venv\Scripts\python.exe tools/review_ohlcv_large_moves.py
.\.venv\Scripts\python.exe tools/summarize_rebase_review.py
```

- `data/regression/audit/large_close_move_review.csv` lists the date and nearby OHLCV bars for each >20% absolute close-to-close move.
- `data/regression/rebase_review_summary.csv` provides one row per RB-01 proxy cycle and identifies the first sampled valid gated candidate, if any.

These outputs guide manual review only. The detected pattern must be compared to independent evidence; neither worksheet fills expected labels automatically.
