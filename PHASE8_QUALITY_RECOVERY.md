# Phase 8 — Quality Recovery (technical first release)

## Purpose

Connect the existing `Quality Recovery` navigation item to a read-only scanner that uses only the active V2 Watchlist. It is independent of the Pattern/Pivot/Breakout/PP engines and cannot write to Supabase or mutate official Stage/pivot state.

## Data policy

- Inputs are completed daily bars selected through the existing app data-source control.
- Yahoo Finance uses the existing EOD provider and its completion/freshness rules.
- SMA calculations use the raw technical `Close` from the `auto_adjust=False` daily bars, not dividend-adjusted `Adj Close`.
- At least 201 distinct daily bars are required, so both current and prior SMA200 values exist.
- Duplicate dates, date-time/intraday observations, missing/non-positive closes, or insufficient history fail closed for that ticker.
- No SQL changes or new persistent tables are needed.

## Definitions

- **Recovery Setup**: latest completed close > SMA20; SMA20 > SMA50; SMA20 is higher than it was five completed sessions ago; and SMA50 < SMA200. This is a technical recovery context, not a buy signal.
- **Recovery Cross**: SMA20 crosses from less-than-or-equal to SMA50 to above SMA50 on the latest completed bar.
- **Golden Cross**: SMA50 crosses from less-than-or-equal to SMA200 to above SMA200 on the latest completed bar.
- **120D Reclaim / 200D Reclaim**: latest completed close crosses from at-or-below the corresponding SMA to above it.
- **State**: descriptive current price/MA relationship; it is not an official entry Stage.

Crosses are event-day flags; an MA stack that remains bullish on a later date is not repeatedly labelled a new cross.

## Fundamental quality gate

The fundamental gate is explicitly `NOT_ASSESSED` in this release. The current V2 data layer has no validated fundamental provider/contract, and the formal functional spec does not define a complete automatic-damage rule. The page warns that technical recovery is not a positive fundamental-quality verdict. Do not infer EPS growth, sales growth, or business health from technical price data.

## UI

- Adds metrics and a table for Recovery Setup, Recovery Cross, Golden Cross, MA levels, distances, and 120D/200D reclaim events.
- Adds a chart with Close and SMA20/50/120/200.
- Displays per-symbol data errors rather than substituting partial or invented values.
- Default source remains disconnected; the user must explicitly choose Yahoo EOD or bundled historical data.

## Non-goals

- No Candidate Pivot or Breakout Pivot generation.
- No official Stage change, EOD Breakout confirmation/failure, PP signal, order, or transaction journal write.
- No automatic fundamental gate pass/fail.
