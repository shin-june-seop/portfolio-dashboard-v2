# V2 Architecture — Phase 3

## 1. Design objective

The functional specification is `Dashboard_V2_Functional_Spec_v0.12.md`. This project is deliberately clean-room: the legacy app is scanned only to learn existing UI surface, data persistence needs, and regression examples. Legacy functions are not imported into this package.

## 2. Data flow

```text
Authenticated user (Supabase Auth)
          ↓  RLS-scoped V2 watchlist CRUD only
v2_watchlist (new, empty Supabase project; no legacy import)
          ↓  enabled tickers
Yahoo Finance EOD adapter (explicitly selected by user)
          ↓  completed daily bars + explicit as_of_date
MarketDataService → normalize_ohlcv / data-quality validation
          ↓
Pure domain engines (pattern → rebase → pivot → stage; quality; risk/exit; PP)
          ↓
Typed domain results (models/schemas.py)
          ↓
Streamlit pages (display only; no inline trading rules)
```

`Pattern`, `Candidate Pivot`, historical/active `Breakout Pivot`, `Next Pivot`, `20D Reference`, `ENTRY STAGE`, `LIVE STATE`, `LIVE ENTRY QUALITY`, and stop states remain independent fields. A screen may format a value but cannot recompute its rule.

## 3. Module boundaries

- `models/schemas.py`: shared types and enums. Cross-screen state fields belong here instead of local ad-hoc dictionaries.
- `config/settings.py`: tunable thresholds adopted from the spec. Rule changes require spec + config + tests to change together.
- `engines/data_validation.py`: canonical OHLCV contract, including finite-value checks. The provider verifies the latest expected XNYS session before any signal is calculated.
- `engines/pattern_engine.py`: shared detector-output contract and single primary-pattern choice (VCP first, then spec priority).
- `engines/pattern_detectors.py`: initial pure OHLCV detectors for nine pattern/setup types. Thresholds are centralized in `config/settings.py`; synthetic fixtures test their intended geometry.
- `engines/pivot_engine.py`: pivot distance, approach gate, rolling 20-bar high reference.
- `engines/stage_engine.py`: official EOD state/event rules only. It takes one completed bar plus prior state; it does not perform database writes.
- `engines/rebase_engine.py`: RB-01/02/03 gates, same-cycle start-date preservation, and low tracking. The caller passes yesterday's candidate explicitly.
- `engines/entry_quality_engine.py`: normalized component weighting and separate price-extension action gate. Initial Risk is intentionally absent.
- `engines/risk_engine.py`: per-fill initial stop, monotonic trailing-stop updates, high-watermark reconstruction support, and EOD-only signal output. No order or position mutation.
- `engines/pocket_pivot_engine.py`: strict prior-10-down-day volume comparison, MA support/reclaim, explicit wider context gate.
- `repositories/supabase_repository.py`: legacy-table read-only boundary, unused by the app.
- `services/watchlist_repository.py`: the sole initial persistence feature; user-scoped CRUD against `public.v2_watchlist` only. It uses the public anon/publishable key, authenticated sessions, and RLS; it never reads or writes legacy tables.
- `services/market_data_service.py`: canonical provider boundary; preserves a separate `Adj Close` series when supplied.
- `services/yfinance_provider.py`: Yahoo EOD adapter using `auto_adjust=False` OHLC for technical analysis, separate `Adj Close` for total-return metrics, XNYS expected-session checking, and a 16:15 ET finalization cutoff.

- `services/quality_recovery_service.py`: compatibility port of the legacy V30 Quality Recovery Radar, including annual Yahoo income-statement growth gate, close-based 120/200MA event lifecycle, correction, and V30.5 RS versus QQQ. It has no database writes and does not touch official EOD stages, pivots, PP signals, or trades. The old SEC provider is unused by `app.py`.
- `services/market_regime_service.py`: pure calculation layer for the legacy V25 broad-market Market Score/Regime. It stays separate from individual-stock Entry Stage, pattern/pivot, Pocket Pivot, and Live Monitor state. `app.py` caches the EOD market-data acquisition and only renders the result.
- `app.py`: presentation only. No trading state is calculated inside Streamlit pages.

## 4. Decisions intentionally not guessed yet

1. Yahoo is approved for V2 EOD analysis under `DATA_SOURCE_POLICY.md`; historical pattern-label validation remains incremental. The source can revise data and must fail closed on missing/stale bars.
2. Additional V2 storage for trades/PP Journal and portfolio holdings; these are not part of this first watchlist stage.
3. Actual widget/action inventory for each retained secondary page.
4. Historical chart-pattern recognition/false-positive validation is ongoing; representative AMAT/MU/SOXX/META/GOOG/STX cases have been reviewed, but the full universe does not have independent chart-labelled ground truth.
5. Intraday provider and Live Monitor behavior; it must remain separate from the completed EOD bars.

## 5. Failure and idempotency rules

- Missing/invalid OHLCV is explicit; it cannot become a normal `BASE` by silent fallback.
- Pure calculations are side-effect free. Watchlist CRUD is the only active DB write path and is isolated to `public.v2_watchlist` with `auth.uid() = user_id` RLS.
- Official event persistence must use a deterministic key such as `(symbol, event_type, as_of_date, pivot)` or a stable event identifier so Streamlit reruns cannot duplicate events. The persistence service is not implemented yet.
- Transactions, fills, partial exits, and historical breakout events are append-preserving records; re-computation must not overwrite them.
- Stop signal creation never sends an order or closes a trade.

## 6. Test coverage and current milestone acceptance

`pytest` verifies boundary behavior for completed bars, pivot distance, stage/event boundaries, score normalization, extension gates, fallback initial risk, stop ratcheting, EOD-only exit signals, Pocket Pivot volume reference, and rebase state preservation. These tests are unit-level acceptance only; they do not replace historical AMAT/MU/SOXX/META/GOOG/STX fixtures or side-by-side comparison with live legacy output.


## 7. Phase 3 regression preparation

- `tools/capture_historical_ohlcv.py` optionally writes local candidate daily history to `data/regression/ohlcv/`; it never accesses Supabase.
- `data/regression/manifest.csv` stores known problem scenarios. Dates and expected values intentionally remain blank until chart-reviewed.
- `tools/run_historical_regression.py` emits `NEEDS_LABEL`, `MISSING_OHLCV`, `INVALID_OHLCV`, `OBSERVED_NOT_VALIDATED`, `PASS`, or `FAIL`. Only user-labelled expected values can yield PASS/FAIL.
- `docs/legacy_db_inventory/` records 56 static query-operation call sites across 14 literal/dynamic table-or-RPC targets from the reviewed legacy source. Live schema, permissions, and row-level security remain unknown.


## Live Monitor boundary

`dashboard_v2/services/live_monitor_service.py` validates provisional Yahoo Finance 5-minute OHLCV, checks the XNYS session and timestamp freshness, and computes same-time cumulative intraday relative volume. It is isolated from the official daily-bar engine: Live Monitor reads official pattern/candidate pivot/breakout pivot/stage from `screening_service.analyze_symbol` using completed daily bars, then compares the latest intraday observation without writing official state or events. During regular hours a missing current-session bar or a bar older than 15 minutes disables live signal interpretation. Outside regular hours the page labels observations `MARKET CLOSED`. Intraday relative volume is explicitly not official EOD RVOL and cannot confirm a breakout.

## Overview index cards (Phase 11)

`dashboard_v2/services/overview_market_service.py` feeds only the Overview's three market cards. It independently validates completed daily OHLC for `^GSPC`, `^IXIC`, and `^DJI`, with the same XNYS 16:15 ET finalization boundary. Stale or invalid latest sessions fail closed. Since index-volume data is not consistently meaningful, SPY/QQQ/DIA 20-session-relative volume is an explicitly labelled visual proxy only. It never affects formal Market Score, single-stock RVOL, EOD stages, patterns, pivots, Live Monitor, or PP Journal. Market Score / Regime continues to be computed by `market_regime_service.py` and shown on Momentum Monitor; the separate sidebar page is removed.
