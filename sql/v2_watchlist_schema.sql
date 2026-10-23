-- Portfolio Dashboard V2: clean, user-owned watchlist only.
-- Run this in a NEW Supabase project. It does not copy or modify legacy data.

create table if not exists public.v2_watchlist (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    ticker text not null check (ticker = upper(btrim(ticker))),
    sector text not null default '미분류' check (char_length(sector) <= 80),
    classification text not null default 'WATCH'
        check (classification in ('WATCH', 'CORE', 'SATELLITE')),
    note text not null default '' check (char_length(note) <= 500),
    enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint v2_watchlist_ticker_format
        check (ticker ~ '^[A-Z0-9^][A-Z0-9.^=-]{0,14}$'),
    constraint v2_watchlist_user_ticker_unique unique (user_id, ticker)
);

create index if not exists v2_watchlist_user_enabled_idx
    on public.v2_watchlist (user_id, enabled, ticker);

alter table public.v2_watchlist enable row level security;

drop policy if exists v2_watchlist_select_own on public.v2_watchlist;
create policy v2_watchlist_select_own
    on public.v2_watchlist for select to authenticated
    using (auth.uid() = user_id);

drop policy if exists v2_watchlist_insert_own on public.v2_watchlist;
create policy v2_watchlist_insert_own
    on public.v2_watchlist for insert to authenticated
    with check (auth.uid() = user_id);

drop policy if exists v2_watchlist_update_own on public.v2_watchlist;
create policy v2_watchlist_update_own
    on public.v2_watchlist for update to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists v2_watchlist_delete_own on public.v2_watchlist;
create policy v2_watchlist_delete_own
    on public.v2_watchlist for delete to authenticated
    using (auth.uid() = user_id);

grant select, insert, update, delete on public.v2_watchlist to authenticated;

create or replace function public.v2_watchlist_set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists v2_watchlist_updated_at on public.v2_watchlist;
create trigger v2_watchlist_updated_at
before update on public.v2_watchlist
for each row execute function public.v2_watchlist_set_updated_at();
