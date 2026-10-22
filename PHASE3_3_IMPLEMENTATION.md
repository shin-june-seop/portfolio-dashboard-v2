# Phase 3.3 — Inspectable anomaly dates and rebase triage

## Why this phase exists

The user's Phase 3.2 reports were structurally consistent: all seven files were present, rows and date ranges matched capture metadata, no duplicate dates / zero-volume rows / >5-calendar-day gaps were flagged, and all rows were classified REVIEW because `auto_adjust=true` remains unapproved for numeric pivot comparison. META had four absolute close-to-close moves above 20%; Phase 3.2 counted them but did not list their dates.

## Added tools

### 1. Large-move context worksheet

Run:

```powershell
.\.venv\Scripts\python.exe tools/review_ohlcv_large_moves.py
```

Output:

`data/regression/audit/large_close_move_review.csv`

For each absolute close-to-close move over 20%, the report shows event date, previous close/date, event close and change percent, plus three bars of context before/after. It does not infer whether a move reflects a genuine market event, corporate action, or price-adjustment artifact.

### 2. Cycle-level rebase review summary

Run after the existing snapshot builder:

```powershell
.\.venv\Scripts\python.exe tools/summarize_rebase_review.py
```

Input: `data/regression/rebase_review_candidates.csv`

Output: `data/regression/rebase_review_summary.csv`

The summary groups the 767-style snapshot worksheet by RB-01 proxy cycle and identifies (a) whether the ungated pattern at trigger predates the trigger and (b) the first sampled post-rebase gated candidate, if one appears. “First sampled” means first among the configured sample offsets, not the exact day a pattern first became valid. All rows stay `REVIEW_REQUIRED`; expected labels stay blank.

## Tests

Added tests verify that the large-move report identifies an event and context bars, and that the cycle summary flags a pre-rebase pattern while preserving manual-review status. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Safety and validation boundary

- Data is read locally; no internet calls or database writes are made by these review tools.
- Captured Yahoo OHLCV follows the approved V2 policy. Numeric pivots must not be compared with the legacy `auto_adjust=True` snapshot without reconciling price basis; the historical tool outputs remain manual-review evidence, not ground truth.
- No row is labeled PASS from V2-generated output alone. Manual labels require independent historical evidence, such as saved dashboard screenshots, journal records, or an explicitly reviewed chart/date.
