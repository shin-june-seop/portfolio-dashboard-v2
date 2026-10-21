# Historical Chart Regression Data

`manifest.csv` lists the known regression scenarios. Dates and expected pattern/pivot values are intentionally blank until reviewed against the actual chart, prior dashboard snapshot, and/or journal record. Do not infer a date or use the detector's current output as its own ground truth.

Run capture (optional; requires network access and the optional yfinance package):

Run these commands from the project root (`portfolio_dashboard_v2`):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,market-data]"
.\.venv\Scripts\python.exe tools/capture_historical_ohlcv.py
```

This creates one file per ticker in `ohlcv/` and a `capture_metadata.json`. The provider/price-adjustment policy is a candidate only. Verify it before comparing exact numeric levels. Then complete the manifest and run:

```powershell
.\.venv\Scripts\python.exe tools/run_historical_regression.py
```

The runner writes `results.csv`. `OBSERVED_NOT_VALIDATED` is not a pass. Only manually labelled expected pattern/pivot fields can yield `PASS` or `FAIL` for those checks.
