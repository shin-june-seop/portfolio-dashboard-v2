# V2 EOD Market-Data Policy

**Status: approved for V2 end-of-day analysis on 2026-10-22.** This is not approval for automatic trading; the app is read-only and Yahoo Finance remains an external source whose history can be revised or be temporarily unavailable.

## 1. Scope: completed daily bars only

- Momentum Monitor and Pattern Scanner consume completed daily OHLCV bars only.
- Live/current-session bars are never used by the EOD pattern, pivot, stage, or RVOL engines. Intraday changes belong in a future, separate Live Monitor.
- Yahoo Finance daily data for US-listed stocks/ETFs is considered eligible only after **16:15 America/New_York**, giving a 15-minute finalization buffer after the regular 16:00 close. Before then, today's bar is excluded.
- `exchange-calendars` (`XNYS`) determines the latest expected completed trading session, including weekends and exchange holidays. If Yahoo does not return the expected completed session, the provider raises a data error and that symbol is not analyzed; it does not silently use stale data.
- An explicitly supplied `as_of_date` is a replay cutoff. The latest expected XNYS session on/before that date must be present. If the cutoff is today and it is earlier than 16:15 ET, today remains excluded.

## 2. Separate price series

- Provider: Yahoo Finance via `yfinance`, daily interval, `prepost=False`, `auto_adjust=False`, `actions=False`.
- Pattern, base, pivot, stage and price-distance calculations use the provider's OHLC `Open/High/Low/Close` series requested with `auto_adjust=False`. The client does not apply the `Adj Close / Close` dividend-adjustment factor to technical OHLC; Yahoo's own corporate-action/split handling remains part of its upstream data convention.
- Holding-period returns (1M/3M/6M/12M) and relative strength versus SPY use the separately supplied `Adj Close`, adjusted by Yahoo for stock splits and cash distributions.
- The table's `일간 %` is a traded-price Close-to-Close change in the technical OHLC basis; multi-period returns and RS use `Adj Close` total-return values.
- RVOL uses today's completed Volume divided by the arithmetic mean of the **prior 20 completed bars' Volume**, excluding today's volume. The breakout confirmation threshold remains EOD RVOL >= 1.30x.
- `Adj Close` must be present, positive, finite, and aligned by session date. Missing OHLCV, non-finite OHLCV, zero volume on the latest bar, missing adjusted-close values, or a stale/missing expected session causes the provider to fail closed. The app displays a data/analysis error for that symbol rather than generating a signal.

## 3. Seed and historical fixture compatibility

- The bundled OHLCV CSVs and chronological replay snapshot were captured with the earlier `auto_adjust=True` total-return-adjusted OHLC basis. They remain available for the explicit “Bundled regression data (historical)” mode; there `Close` is already adjusted and is used as the return series when no separate `Adj Close` column exists.
- Yahoo's new split-normalized OHLC plus separate `Adj Close` basis is not mixed with the old seed state. When separate `Adj Close` is present, analysis intentionally avoids resuming the legacy basis snapshot and uses a transparent rolling 300-bar replay. The UI warns that events older than this window may not be reconstructed. This avoids silently comparing pivots from different price bases.
- The current close can still match a legacy seed within tolerance while historical pivots differ due to dividend adjustment; close-only compatibility is therefore insufficient to establish basis compatibility.

## 4. Failure behavior and operational boundary

- No synthetic/fallback prices or signals are created if required live data is absent or stale.
- A failed Yahoo request or validation check omits the affected symbol from the analysis results and exposes a data/analysis error in the UI.
- Yahoo is a third-party source without an application-specific freshness guarantee. Past bars can be corrected. The app is read-only: no Supabase writes, orders, or automatic trading.
- This is an explicit V2 EOD policy. A future Live Monitor must use a separate intraday data path and must not alter EOD historical state.

## References

- yfinance `PriceHistory.history` documents `auto_adjust` and the exclusive `end` boundary: https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html
- Yahoo Finance explains that adjusted close reflects stock splits and dividend distributions: https://in.help.yahoo.com/kb/adjusted-close-sln28256.html
