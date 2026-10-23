# V2 Watchlist — Patch Notes

## Scope

- Adds a clean V2-only `public.v2_watchlist` table schema.
- Adds Supabase email/password login to the Streamlit app.
- Restricts data rows to the authenticated Supabase user using RLS (`auth.uid() = user_id`).
- Adds Watchlist CRUD: add, edit sector/classification/note/enabled status, and delete.
- Uses only enabled V2 watchlist tickers for Momentum Monitor and Pattern Scanner.
- Adds `supabase` to deployed `requirements.txt`.

## Explicit non-goals

- Does not import legacy watchlist, holdings, transaction, snapshot, or PP data.
- Does not connect to or mutate legacy Supabase tables.
- Does not implement Live Monitor or PP Journal in this patch.
- Does not change the EOD pattern, pivot, rebase, stage, RVOL or historical-breakout engines.
- Does not submit broker orders or perform automatic trading.

## Deployment prerequisites

1. Create a new, empty Supabase project.
2. Run `sql/v2_watchlist_schema.sql` in that project.
3. Manually create the intended app user under Supabase Authentication → Users. Disable public sign-ups for a single-user deployment if desired.
4. Add `SUPABASE_URL` and `SUPABASE_ANON_KEY` to Streamlit Community Cloud → app Settings → Secrets.
5. Never expose or use the Supabase `service_role` key.

## Validation performed

- `python -m py_compile app.py dashboard_v2/services/watchlist_repository.py`: passed.
- `pytest -q tests/test_watchlist_repository.py`: 17 passed.
- Related EOD/pattern/pivot/rebase/stage/integration group: 112 passed in a split test run.
- The whole legacy suite was not completed in one local run because the local command exceeded the execution time limit. GitHub Actions remains the final integrated gate.
