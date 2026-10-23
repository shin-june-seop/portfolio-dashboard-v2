# V2 Watchlist Setup (clean-room)

This setup uses a **new, empty Supabase project**. It does not import the old dashboard's watchlist, holdings, trades, snapshots, or PP data.

## 1. Create the new Supabase project

Keep it separate from the legacy database. Run `sql/v2_watchlist_schema.sql` in the new project's SQL Editor. The only business table created by this first stage is `public.v2_watchlist`.

## 2. Create one login user

In Supabase Dashboard → Authentication → Users, add your own user with email and password. Disable public sign-ups in Auth settings if you want this to remain a single-user app. The application does not provide a sign-up form.

## 3. Configure Streamlit Cloud secrets

In Streamlit Community Cloud → app → Settings → Secrets, add:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_ANON_KEY = "YOUR_PUBLIC_ANON_OR_PUBLISHABLE_KEY"
```

Use the project URL and public anon/publishable key only. **Do not use or expose the `service_role` key.** The public app requires login and the `v2_watchlist` table has row-level security keyed to `auth.uid()`.

## 4. Use the Watchlist page

- Add one ticker per item; examples: `NVDA`, `GOOG`, `BRK-B`, `BRK.B`, `^GSPC`.
- Choose sector, `WATCH`/`CORE`/`SATELLITE`, enabled status, and optional note.
- Edit, disable, or delete items.
- Only enabled tickers feed the EOD Momentum Monitor and Pattern Scanner. The same list is the intended source for the later Live Monitor and PP Journal.

## Privacy and safety

- V2 never queries or imports the old tables.
- RLS limits rows to the authenticated Supabase user.
- The browser only submits UI events to the Streamlit server; the Supabase key stays in server-side secrets.
- No portfolio holdings or trade journal tables are created in this stage.
- Do not add real secrets to GitHub. The `.streamlit/secrets.example.toml` file is only a template.
