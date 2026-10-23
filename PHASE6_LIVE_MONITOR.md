# Phase 6 — Live Monitor (read-only intraday layer)

## Scope

- Adds a separate `Live Monitor` page after `Momentum Monitor`.
- Uses only enabled symbols from the authenticated V2 `v2_watchlist`.
- The first release is scoped to US equity/index symbols using XNYS regular-session hours. Futures (`=F`), FX (`=X`), and 24-hour crypto (`-USD`) symbols fail closed until instrument-specific trading sessions/volume profiles exist.
- Fetches provisional Yahoo Finance 5-minute regular-session bars via `YFinanceIntradayProvider`.
- Gets the official pattern, Candidate Pivot, Breakout Pivot, and ENTRY STAGE from the existing completed-daily-bar `screening_service` output. The intraday module does not re-run or update the EOD engine.
- Displays last observed intraday price, change from the previous completed daily close before that intraday session, cumulative session volume, same-time cumulative intraday relative volume, pivot distances, data status, and the official EOD baseline/date.
- Provides a bounded concurrent fetch (up to four symbols at a time), a 60-second intraday cache, a manual cache-clear refresh button, and a 5-minute chart with EOD pivot reference lines.

## Safety / interpretation rules

- `LIVE STATE` is provisional and never records an official `BREAKOUT`, `BREAKOUT CONFIRMED`, or `BREAKOUT FAILURE` event.
- Only the official EOD engine controls official pattern/pivot/stage history. A live cross is a provisional observation; EOD confirmation still requires the actual completed daily close to cross the pivot and the official EOD RVOL condition.
- Yahoo intraday data is not guaranteed exchange-grade real-time data. The UI labels it provisional and read-only.
- When regular trading is open, no current-session bar or a last bar more than 15 minutes old returns `DATA STALE` and disables live-state interpretation. Invalid/missing bars or a missing EOD baseline produce `DATA INCOMPLETE`.
- Outside regular market hours the page shows `MARKET CLOSED` and labels any derived state as a last-session observation. It does not present that state as an active live signal.
- `LIVE RVOL` is current-session cumulative 5-minute volume divided by the average cumulative volume through the same intraday time over up to five previous sessions. At least three reference sessions are required. It is a separate diagnostic, never substituted for official daily RVOL or the 1.30× EOD confirmation threshold.
- No orders are submitted, and Live Monitor writes no state or journal rows to Supabase.

## Deliberately not included in this phase

- `LIVE ENTRY QUALITY` score/label is not fabricated from undocumented component thresholds. The specification provides weights and quality boundaries but the exact per-component scoring rules need their own tested calculation contract before displaying a GOOD/CAUTION/WAIT score.
- PP signal persistence, PP Journal, entry/exit fills, alerts, broker integration, and automated trading remain out of scope.

## Validation performed in the build workspace

- `python -m py_compile app.py dashboard_v2/services/live_monitor_service.py tests/test_live_monitor_service.py`: passed.
- `tests/test_live_monitor_service.py`: 11 passed.
- Selected connected engine/service regression group: 113 passed, including the new Live Monitor tests.
- `tests/test_screening_service.py tests/test_live_monitor_service.py tests/test_watchlist_repository.py`: 37 passed.
- A full-suite local run was not certified: the reconstructed build workspace had a pre-existing `test_rebase_lifecycle_regression.py::test_stx_candidate_breakout_failure_and_rebase_are_distinct_events` mismatch against the bundled regression state; another full run reached the execution timeout. No rebase/official EOD engine modules are included in this patch. GitHub Actions on the user's actual repository remains the integrated gate.
