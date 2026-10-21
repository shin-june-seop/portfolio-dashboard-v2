# V2 Architecture — Phase 3

## 1. Design objective

The functional specification is `Dashboard_V2_Functional_Spec_v0.12.md`. This project is deliberately clean-room: the legacy app is scanned only to learn existing UI surface, data persistence needs, and regression examples. Legacy functions are not imported into this package.

## 2. Data flow

```text
Price provider adapter (not selected yet)
          ↓  completed daily bars + explicit as_of_date
MarketDataService → normalize_ohlcv / data-quality validation
          ↓
Pure domain engines (pattern → rebase → pivot → stage; quality; risk/exit; PP)
          ↓
Typed domain results (models/schemas.py)
          ↓
Snapshot/journal services (not yet connected)
          ↓
Read-only repositories (schema mapping pending inventory)
          ↓
Streamlit pages (display only; no inline trading rules)
```

`Pattern`, `Candidate Pivot`, historical/active `Breakout Pivot`, `Next Pivot`, `20D Reference`, `ENTRY STAGE`, `LIVE STATE`, `LIVE ENTRY QUALITY`, and stop states remain independent fields. A screen may format a value but cannot recompute its rule.

## 3. Module boundaries

- `models/schemas.py`: shared types and enums. Cross-screen state fields belong here instead of local ad-hoc dictionaries.
- `config/settings.py`: tunable thresholds adopted from the spec. Rule changes require spec + config + tests to change together.
- `engines/data_validation.py`: canonical OHLCV contract. It does not guess the final bar is incomplete and never drops it silently.
- `engines/pattern_engine.py`: shared detector-output contract and single primary-pattern choice (VCP first, then spec priority).
- `engines/pattern_detectors.py`: initial pure OHLCV detectors for nine pattern/setup types. Thresholds are centralized in `config/settings.py`; synthetic fixtures test their intended geometry.
- `engines/pivot_engine.py`: pivot distance, approach gate, rolling 20-bar high reference.
- `engines/stage_engine.py`: official EOD state/event rules only. It takes one completed bar plus prior state; it does not perform database writes.
- `engines/rebase_engine.py`: RB-01/02/03 gates, same-cycle start-date preservation, and low tracking. The caller passes yesterday's candidate explicitly.
- `engines/entry_quality_engine.py`: normalized component weighting and separate price-extension action gate. Initial Risk is intentionally absent.
- `engines/risk_engine.py`: per-fill initial stop, monotonic trailing-stop updates, high-watermark reconstruction support, and EOD-only signal output. No order or position mutation.
- `engines/pocket_pivot_engine.py`: strict prior-10-down-day volume comparison, MA support/reclaim, explicit wider context gate.
- `repositories/supabase_repository.py`: optional read-only boundary. There are no insert/update/delete/upsert/RPC methods. It will not connect unless explicitly enabled.
- `services/market_data_service.py`: canonical provider boundary; no provider is wired in by default.
- `services/yfinance_provider.py`: optional candidate adapter that mirrors the main legacy daily-bar path for parity tests; provider/adjustment/bar-finalization policy is not approved for production.
- `app.py`: presentation only. No trading state is calculated inside Streamlit pages.

## 4. Decisions intentionally not guessed yet

1. Final provider, split/dividend-adjustment policy, exchange calendar, and definition of a completed bar. The yfinance adapter is a candidate only.
2. Live Supabase tables, fields, unique constraints, views, RPC functions, RLS policies, triggers, and exact write paths; static source inventory is available but is not live-schema validation.
3. Actual widget/action inventory for each retained legacy page.
4. Historical chart-pattern recognition/false-positive validation: synthetic fixtures exist and a regression harness is implemented, but real AMAT/MU/SOXX/META/GOOG/STX dates and chart-labelled expected results are still pending.
5. Core/Satellite storage scope and any schema migration.

## 5. Failure and idempotency rules

- Missing/invalid OHLCV is explicit; it cannot become a normal `BASE` by silent fallback.
- Pure calculations are side-effect free.
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
