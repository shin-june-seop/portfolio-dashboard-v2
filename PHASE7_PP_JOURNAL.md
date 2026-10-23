# Phase 7 — PP Journal / Trade Journal

## Scope implemented

- A standalone `PP Journal` page uses only the authenticated V2 user's `v2_watchlist` symbols that are enabled.
- The scan reuses the same completed-daily-bar `analyze_symbol` and `detect_pocket_pivot` rules; intraday 5-minute bars are not used.
- Valid PP events are explicitly saved by the user. The persistent key `(user_id, ticker, signal_date)` prevents same-day duplicate records. Re-scans never overwrite a saved signal snapshot.
- Signal snapshots store signal date/price/volume, prior 10-bar max down-day volume, support MA, pattern, official entry stage, Candidate Pivot and historical Breakout Pivot as they were at scan time.
- Trades are a separate record. The initial entry captures the original entry stage and optional initial stop. Adds and exits are immutable append-only events.
- Trade states are derived from shares, not manually toggled: `OPEN` if no shares have been exited; `PARTIAL` if any shares were exited but a balance remains; `CLOSED` only when the remaining quantity reaches zero within a numeric tolerance.
- Exit entry is checked against shares acquired by that exit date; both the repository and database trigger reject an over-exit. The SQL also checks add chronology and linked signal/ticker ownership.
- Realized profit and return are computed FIFO per share lot. Risk multiple is computed only when the closed trade's exited lots all have valid recorded initial stop levels. Stats use only closed trades for win rate, average win/loss, profit factor and expectancy; OPEN/PARTIAL positions remain visible but are excluded from closed-trade stats.
- Optional signal follow-up uses completed EOD highs/lows after the signal date to calculate candidate-pivot high touch, +10%/+20% reach, MFE and MAE. These are price excursion metrics only, not official breakout events.
- User data access is isolated with RLS, all rows are owned by `auth.uid()`, and app writes never use service-role credentials. Journal records grant SELECT/INSERT only (append-only); no edit/delete UI or automated orders.

## Explicitly deferred rather than guessed

- PP → official Pivot / official Breakout / Breakout Failure rates are not computed from a static line touch. Those require durable, date-by-date official Stage/Breakout event history linked to each signal. A future analytics step should create that link and validate it against replay tests before displaying official conversion rates.
- Market-regime snapshot is not stored because the V2 market-regime calculation is not yet implemented.
- Stop levels are optional manual snapshots in this phase. If entered, they are immutable for the fill; the UI does not retroactively fabricate a stop for missing historic trade data.
- Unrealized P&L, MFE/MAE of trade fills, broker reconciliation, corrections/deletions, taxes, FX, and order execution are not part of this first journal release.

## Supabase deployment

The user has already run `sql/v2_watchlist_schema.sql`. Run this **additional additive migration** once in the same V2 project's SQL Editor:

- `sql/v2_pp_journal_schema.sql`

Do not replace the Watchlist schema or create a new project for this step. This script creates only the new `v2_pp_*` tables/functions/triggers. It does not query, import, or modify legacy tables.

## Tests

- `tests/test_pp_journal.py`: validation, deduplication, partial/complete status, FIFO realized P&L, closed-trade statistics, forward EOD outcome boundaries, over-exit guard, and SQL/RLS expectations.
- Targeted connected tests (PP Journal + Watchlist + Live Monitor + screening + Pocket Pivot) are run in the implementation workspace before release.
- The complete repository suite and GitHub Actions remain the integrated deployment gate; don't infer the full suite has passed from targeted tests.
