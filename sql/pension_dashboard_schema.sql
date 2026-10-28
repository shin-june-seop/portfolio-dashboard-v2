-- Run once in the dedicated V2 Supabase project's SQL Editor.
-- Creates only the pension_* tables; it does not drop or alter existing V2 tables.
create table if not exists public.pension_holdings (
  user_id uuid not null references auth.users(id) on delete cascade,
  ticker text not null,
  asset_group text not null default '',
  name text not null,
  target_weight double precision not null default 0,
  monthly_investment double precision not null default 0,
  avg_cost double precision not null default 0,
  quantity integer not null default 0,
  last_known_price double precision,
  sort_order integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (user_id, ticker)
);

create table if not exists public.pension_monthly_history (
  user_id uuid not null references auth.users(id) on delete cascade,
  record_month date not null,
  invested_amount double precision not null default 0,
  total_balance double precision not null default 0,
  profit double precision not null default 0,
  return_pct double precision,
  return_change double precision,
  updated_at timestamptz not null default now(),
  primary key (user_id, record_month)
);

create table if not exists public.pension_weekly_returns (
  user_id uuid not null references auth.users(id) on delete cascade,
  record_date date not null,
  semi_return double precision,
  dow_return double precision,
  sp500_return double precision,
  nasdaq_return double precision,
  semi_change double precision,
  dow_change double precision,
  sp500_change double precision,
  nasdaq_change double precision,
  updated_at timestamptz not null default now(),
  primary key (user_id, record_date)
);

alter table public.pension_holdings enable row level security;
alter table public.pension_monthly_history enable row level security;
alter table public.pension_weekly_returns enable row level security;

drop policy if exists "pension holdings owned rows" on public.pension_holdings;
create policy "pension holdings owned rows" on public.pension_holdings
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "pension monthly owned rows" on public.pension_monthly_history;
create policy "pension monthly owned rows" on public.pension_monthly_history
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "pension weekly owned rows" on public.pension_weekly_returns;
create policy "pension weekly owned rows" on public.pension_weekly_returns
  for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);

create index if not exists pension_monthly_user_month_idx on public.pension_monthly_history (user_id, record_month);
create index if not exists pension_weekly_user_date_idx on public.pension_weekly_returns (user_id, record_date);


grant select, insert, update, delete on public.pension_holdings to authenticated;
grant select, insert, update, delete on public.pension_monthly_history to authenticated;
grant select, insert, update, delete on public.pension_weekly_returns to authenticated;
