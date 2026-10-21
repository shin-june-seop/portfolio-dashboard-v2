# Phase 3.4 — Chronological Replay with Captured OHLCV

## What was run

- Input: seven uploaded daily OHLCV files: AMAT, MU, SOXX, META, GOOG, STX, LRCX.
- Range: 2021-10-20 through 2026-10-19; 1,254 completed daily bars per symbol.
- Replay: one bar at a time; candidate pivot, historical Breakout Pivot, breakout-cycle state, and rebase state carried forward.
- Runtime environment: Python 3.13.5.
- Automated test suite: **90 passed**.

## Bugs found and corrected during the real-data run

1. **Base-on-Base detector could crash** when a candidate formation end was earlier than the known breakout index. It attempted `np.max()` on an empty slice. The detector now rejects those ends before slicing; a regression test covers it.
2. **Rebase cycle could be trapped open** because RB-01 (close 10% below rolling 20-day high) often remains true throughout the original drawdown. That persistent condition was being treated as a new trigger on the day a valid new pattern was found. A valid pattern can now close the active cycle unless a same-day official breakout failure or active-candidate drawdown has precedence.
3. **Replay runtime** was reduced by limiting the detector scan window to 120 bars (enough for the configured longest base plus pre-base/pole context) and carrying the rebase low forward incrementally instead of rescanning the full cycle on every date.

## Mechanical invariants on the seven captured files

All seven symbols had 1,254 replay rows. The following mechanical checks reported zero violations for every symbol:

- While `rebase_active=True`, candidate pivot is blank and the pattern is `BASE FORMING / NO VALID PATTERN`.
- Historical Breakout Pivot does not change on a day without a recorded breakout event.
- Every recorded breakout-failure event has a completed close below the prior day's active Breakout Pivot.

| Symbol | Rebase days | Candidate-pivot days | Breakout events | EOD failure events | Rebase-reset violations | Pivot changed without breakout event | Failure logic violations |
|---|---:|---:|---:|---:|---:|---:|---:|
| AMAT | 906 | 307 | 10 | 8 | 0 | 0 | 0 |
| MU | 942 | 249 | 3 | 2 | 0 | 0 | 0 |
| SOXX | 874 | 314 | 11 | 8 | 0 | 0 | 0 |
| META | 755 | 459 | 8 | 3 | 0 | 0 | 0 |
| GOOG | 602 | 550 | 9 | 5 | 0 | 0 | 0 |
| STX | 873 | 314 | 10 | 8 | 0 | 0 | 0 |
| LRCX | 915 | 291 | 8 | 6 | 0 | 0 | 0 |

## Focused observations

- **AMAT / MU / SOXX:** active rebase always suppresses the previous pattern and candidate pivot. On the latest bar (2026-10-19), all three were still in `BASE FORMING / NO VALID PATTERN`; no new valid pattern had yet been accepted by this detector.
- **META:** candidate and historical Breakout Pivot remain separate values. The uploaded adjusted OHLCV replay does not reproduce the legacy dashboard's reported `$718.45` value exactly; this is not called a pass/fail until the expected date, price-adjustment basis, and source snapshot are confirmed.
- **GOOG:** on 2026-10-15, the replay detected `Base-on-Base` with Candidate Pivot about `$359.98`, while historical Breakout Pivot remained about `$329.89`. This demonstrates separation of the two fields in the replay, but does not independently prove the legacy snapshot's expected values.
- **STX:** replay recorded a breakout at about `$921.04` on 2026-09-23, then an EOD breakout failure on 2026-09-24 when close was about `$905.92`, below the prior active pivot. The legacy reference level `$924.70` differs, so exact-level validation remains open pending source-basis/snapshot review.
- **LRCX:** OHLCV alone cannot validate that an individual trade's Initial Risk remains fixed at entry. That requires a known entry/fill record and the saved Initial Risk value.

## Important interpretation

These are **mechanics checks**, not independent chart-label validation. The daily output rows are marked `OBSERVED_NOT_VALIDATED`. No historical pattern/pivot is marked `PASS` solely because the current detector produced it. Exact historical levels require the prior dashboard/journal snapshot and a confirmed price-adjustment policy.

## Output files

- `data/regression/chronological_replay.csv`: all 8,778 daily state rows.
- `data/regression/chronological_replay_invariant_summary.csv`: per-symbol mechanical checks.
- `data/regression/rebase_cycle_review.csv`: each observed rebase cycle and its post-cycle pattern.
- `data/regression/target_case_review.csv`: compact event/boundary review for the seven symbols.
- `data/regression/ohlcv/`: the seven uploaded source CSVs.

Run tests from the project root (Windows PowerShell):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\run_chronological_replay.py
```
