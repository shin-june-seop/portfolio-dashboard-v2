# Phase 11 — Overview Index Panels

## Scope

- Add three compact Overview cards for S&P 500 (`^GSPC`), Nasdaq Composite (`^IXIC`), and Dow Jones Industrial Average (`^DJI`).
- Match the legacy mini-chart style: latest completed date, prior-session point/percentage change, last two daily candlesticks, current-close marker, and relative-volume bars where available.
- Remove the separate `Market Regime` sidebar page. The existing Market Score / Market Regime calculator remains rendered at the top of Momentum Monitor.

## Data policy

- Index candles are fetched as daily OHLC with `auto_adjust=False`.
- The most recent eligible session is derived from the XNYS calendar and the 16:15 America/New_York finalization cutoff. A missing expected session fails closed and shows `DATA UNAVAILABLE`; the UI never relabels an older close as today's value.
- Index symbols are not assumed to have meaningful reported volume. Blue relative-volume bars use a clearly disclosed ETF proxy (SPY for S&P 500, QQQ for Nasdaq, DIA for Dow) compared with the previous 20 completed ETF-volume bars. This proxy is presentation-only and is not mixed into index price calculations, the Market Score, per-stock RVOL, formal stages, pivots, or pattern detection.
- Each index's price panel is independent. If its ETF volume proxy is unavailable, candles remain visible and RVOL is shown unavailable; if index OHLC is stale or invalid, its card is unavailable.
- Results are cached for 15 minutes and loaded concurrently.

## Validation

- Normalization rejects missing, duplicate, stale, and malformed OHLC data.
- Unit tests cover US-session cutoff, change calculation, ETF proxy RVOL, unavailable proxy behavior, stale data fail-closed handling, HTML cards, Overview integration, and sidebar removal.
