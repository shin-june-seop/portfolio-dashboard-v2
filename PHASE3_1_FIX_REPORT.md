# Phase 3.1 Fix Report — Date index/column ambiguity

## What the uploaded run showed

The supplied `phase3_regression_data.zip` contains no per-ticker OHLCV CSV files. All seven symbols (AMAT, MU, SOXX, META, GOOG, STX, LRCX) failed with the same error:

`ValueError: 'Date' is both an index level and a column label, which is ambiguous.`

Therefore, this capture attempt did **not** obtain market data, and the regression runner correctly reported `NEEDS_LABEL` with `bar_count=0`. This is not evidence of a pattern-engine pass or fail. The manifest dates and chart-ground-truth values remain blank, as intended.

## Root cause and fix

Yahoo/yfinance can return a daily DataFrame whose DatetimeIndex is named `Date`. The provider adds a canonical `Date` column, then normalization sorts on that column while the index level has the same name. Pandas rejects this ambiguous operation.

`dashboard_v2/engines/data_validation.py` now clears index level names at the start of normalization, so the canonical `Date` column is unambiguous. A regression test was added that creates a yfinance-like DataFrame with `index.name = "Date"` and verifies successful normalization, unique dates, and canonical column order.

## Verification

- Unit tests: 78 passed.
- The new test specifically reproduces the reported failure mode.
- No live Yahoo request was made as part of unit testing. Internet access, provider availability, and actual ticker data capture must be checked by rerunning the capture script on the user's machine.

## Next action

Use this Phase 3.1 package in a new folder (or replace the patched source files in the existing local project), install the market-data extra using its Python 3.13 virtual environment, run the capture script again, and inspect `data/regression/ohlcv/capture_metadata.json`. Continue to chart labelling only after the metadata lists successful downloads and ticker CSV files have been created.
