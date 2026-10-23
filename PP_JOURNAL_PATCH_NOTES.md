# PP Journal Patch Notes — Phase 7

## Upload

Unzip and preserve the paths. Copy/replace these files in the GitHub repository:

- `app.py` (replace the existing app file)
- `dashboard_v2/services/pp_journal_repository.py` (new)
- `dashboard_v2/services/pp_journal_analytics.py` (new)
- `sql/v2_pp_journal_schema.sql` (new additive Supabase migration)
- `tests/test_pp_journal.py` (new)
- `PHASE7_PP_JOURNAL.md` (new feature notes)

No new Python package was added. Existing `requirements.txt`, `pyproject.toml`, Watchlist SQL, Live Monitor service, EOD replay, and pattern/pivot engines are not changed by this patch.

## Supabase setup

The Watchlist project/table should already exist. In that **same V2 Supabase project**, open SQL Editor, create a new query, paste the complete `sql/v2_pp_journal_schema.sql` file, and run it once. This is additive; do not replace or delete `sql/v2_watchlist_schema.sql`, and do not create a second project for PP Journal.

The migration creates only `v2_pp_signals`, `v2_pp_trades`, `v2_pp_trade_adds`, and `v2_pp_trade_exits`, with user-scoped RLS. It grants SELECT/INSERT only (append-only journal) and adds database-side checks for trade/signal ticker ownership, add chronology, and over-exiting shares.

## Validation performed in the build workspace

- Python compilation: passed.
- Targeted connected group: **119 passed** across PP Journal, Watchlist, Live Monitor, Pocket Pivot, screening, EOD replay, risk/stage engines, data validation and related tests.
- Full suite: not certified. It stops at the pre-existing `tests/test_rebase_lifecycle_regression.py::test_stx_candidate_breakout_failure_and_rebase_are_distinct_events` mismatch (`rebase_active` was `True`, expected `False`) before completing. The PP Journal patch does not modify the EOD/rebase engine or that fixture. Run GitHub Actions on the actual repository as the final integrated check and treat any failure on that test separately from the new journal code.
- SQL was statically checked by tests but was not executed against the user's live Supabase project in this workspace.

## Deliberate limits

- No order submission, automated execution, signal-based automatic trade creation, or automatic trade closure.
- Official PP→Breakout / Breakout Failure conversion statistics are deferred until the official daily event history is persistently linkable to signals. A saved Candidate Pivot high touch is labeled as a price touch, not an official breakout.
- Market-regime snapshots, trade-level MFE/MAE and unrealized P&L are not fabricated in this phase.
