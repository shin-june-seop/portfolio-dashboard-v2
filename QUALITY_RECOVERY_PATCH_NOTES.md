# Quality Recovery patch notes

## Files in this patch

- `app.py` — routes the Quality Recovery page to the new scanner and records feature status in App Monitor.
- `dashboard_v2/engines/quality_recovery_engine.py` — pure, read-only moving-average recovery calculations.
- `tests/test_quality_recovery_engine.py` — unit tests for moving averages, cross-day events, setup rules, bad/missing data, and separation from official pivot/Stage fields.
- `PHASE8_QUALITY_RECOVERY.md` — definitions and limitations.

## Apply

1. Replace the root `app.py` with the patch version.
2. Add `dashboard_v2/engines/quality_recovery_engine.py` at the exact path.
3. Add `tests/test_quality_recovery_engine.py` under `tests/`.
4. Add the two phase documents at repository root if you want the implementation notes retained.
5. Commit and confirm GitHub Actions.

No new Python dependencies, Supabase tables, SQL, or Streamlit secrets are required.

## Validation

- Quality Recovery unit tests: **9 passed**.
- Related current feature/engine tests in the local reconstructed tree: **144 passed**.
- Full local suite had one stale assertion in the reconstructed `tests/test_screening_service.py` expecting `ROLLING_260_BAR_REPLAY` even though the current service contract is `ROLLING_300_BAR_REPLAY`. That stale test was not modified or included in this patch; use the current assertion in the deployed repository and let GitHub Actions be the integrated gate.

## Known limitation

Fundamental quality is not assessed yet. The page explicitly shows `미평가`; technical recovery conditions must not be interpreted as EPS/sales growth qualification or as a buy instruction.
