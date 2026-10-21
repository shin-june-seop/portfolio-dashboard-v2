# Phase 2 Implementation Notes — Pattern Detector Foundation

**Specification:** `Dashboard_V2_Functional_Spec_v0.12.md`  
**Status:** initial detector code plus synthetic fixtures; real-market validation pending.

## Implemented detector entry points

`dashboard_v2/engines/pattern_detectors.py` exposes:

- `detect_pattern_candidates(...)`: runs each detector and returns valid candidates for diagnostics/debugging.
- `detect_primary_pattern(...)`: selects exactly one pattern and its Candidate Pivot through the shared `select_primary_pattern` function.
- Individual detectors for Double Bottom, Cup & Handle, Cup Without Handle, Flat Base, Saucer Base, Ascending Base, High Tight Flag, Base-on-Base, and VCP.

The detector module is pure: it does not change the Breakout Pivot, write historical events, persist state, or create a retroactive breakout. The pattern candidate and historical/active Breakout Pivot remain separate concerns. Base-on-Base currently detects a flat-style second base; richer second-base subtype recognition is a follow-up refinement.

## Guardrails

- Detectors consume the canonical OHLCV contract and require valid bars; they do not decide whether an intraday bar is complete.
- Rebase-aware detectors can require the new base structure to start on/after `rebase_started_at`. High Tight Flag allows a preceding pole to predate rebase but requires the flag segment itself to start after rebase and revalidates the pole/flag measurements.
- The detector searches the latest completed bar plus a short trailing formation-end window to recognize a structure that a breakout bar may have distorted. This is not a 3/5-day confirmation gate; a current valid pattern can be emitted immediately.
- Thresholds are centralized in `dashboard_v2/config/settings.py` as initial automation values corresponding to spec v0.12. They need chart-by-chart validation and should not be treated as immutable universal definitions.
- NumPy diagnostic scalars are normalized to native Python types to keep diagnostics serializable.
- The shared selector chooses VCP when valid, otherwise follows the classic-pattern priority. A bad/missing VCP cannot mask a valid classic candidate.

## Verification performed

- `python -m compileall -q .`
- `pytest -q`: **67 passed** at the time of this milestone package build.
- Synthetic fixture coverage includes Double Bottom, Cup & Handle, Cup Without Handle, Flat Base, Saucer Base, Ascending Base, High Tight Flag, Base-on-Base, VCP priority, short-history behavior, and rebase-aware invalidation.
- A sample 75-bar synthetic scan took about 0.05–0.06 seconds in the current local test environment. This is only a single-sample microbenchmark, not a performance promise for a full universe scan.

## Remaining before any real trade use

1. Build fixed historical OHLCV fixtures for AMAT, MU, SOXX, META, GOOG, and STX around known pattern/rebase/breakout dates.
2. Compare detector output and pivots to chart visuals and legacy dashboard history without allowing the old implementation to overwrite new logic.
3. Calibrate false positives and missing detections, particularly VCP contraction segmentation, cup/handle U-shape and rim recognition, and Base-on-Base subtype coverage.
4. Approve the actual price source, split/dividend adjustment basis, exchange calendar and EOD completeness rule.
5. Inventory Supabase tables/fields/constraints/RPCs before any read-only adapter is enabled.

**This phase is not a production signal release.** No real provider or Supabase account is connected, and the tests are synthetic unit tests rather than real-market acceptance tests.
