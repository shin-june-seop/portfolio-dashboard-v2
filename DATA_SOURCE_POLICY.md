# Data Source and Completed-Bar Policy — Phase 3 Candidate

## Status

**Not approved for production; adapter remains opt-in and is not connected to the Streamlit app.** The purpose of this phase is to enable chart-parity testing without creating a hidden assumption that the final provider or price basis has been selected.

## Legacy-source observations

A static review of the current legacy `app.py` found multiple `yfinance` paths. The main EOD helper `v171_fetch_ohlcv` uses `Ticker.history(..., auto_adjust=True, actions=False)` and excludes the current New York exchange-local date before 16:00 ET. Other fetch paths also use `yfinance`, but request windows and some options differ, so this is not a claim that every legacy page uses one uniform policy.

Current upstream yfinance documentation says `auto_adjust` adjusts OHLC automatically and documents `history(end=...)` as exclusive. V2 passes these parameters explicitly to avoid relying on defaults. References:

- https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html
- https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py

## Implemented candidate adapter

`dashboard_v2/services/yfinance_provider.py`:

- Optional dependency: `yfinance` (install with `pip install -e ".[market-data]"`).
- Daily bars only; `prepost=False`; `actions=False`.
- Candidate price basis: `auto_adjust=True`, matching the main legacy EOD helper.
- Normalizes to `Date, Open, High, Low, Close, Volume` and rejects ambiguous MultiIndex or missing required fields.
- For live requests, excludes the current New York exchange date before 16:00 ET; after 16:00 ET it can include that day's bar, matching the current legacy helper's broad rule.
- Explicit historical `as_of_date` requests include that historical date (if returned) and exclude later rows.
- No network request is made during app startup; the service is not injected into the app by default.

## Decisions still required before real signal usage

1. Approve/replace provider: Yahoo/yfinance is the current candidate, not a guaranteed service-level data contract.
2. Approve price basis: split-adjusted only vs split-and-dividend-adjusted OHLC. This materially changes long-history price levels and can alter pivot comparisons when mixed with unadjusted data.
3. Approve daily-bar completion rule: 16:00 ET boundary vs a post-close finalization buffer / exchange-calendar-aware readiness check.
4. Confirm treatment of corrections, missing rows, vendor outage, stock splits, symbol changes, ETFs, and different exchanges.
5. Compare fetched values and selected pivots to the source/legacy dashboard around labelled regression dates.

Until those items are approved, the adapter is only a controlled data-capture and parity-testing aid. Live data and official signal state remain disconnected.
