-- Portfolio Dashboard V2: additive PP signal and trade journal.
-- Run this only in the NEW V2 Supabase project after v2_watchlist_schema.sql.
-- No legacy table is read, migrated, updated, or deleted.

create table if not exists public.v2_pp_signals (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    ticker text not null check (ticker = upper(btrim(ticker))),
    signal_date date not null,
    signal_price numeric(20, 6) not null check (signal_price > 0),
    signal_volume numeric(24, 2) not null check (signal_volume > 0),
    max_prior_down_day_volume numeric(24, 2) not null check (max_prior_down_day_volume >= 0),
    reference_ma text check (reference_ma is null or reference_ma in ('MA10', 'MA50')),
    pattern_name text not null default 'BASE FORMING / NO VALID PATTERN' check (char_length(pattern_name) <= 100),
    entry_stage text not null default 'BASE' check (entry_stage in ('BASE', 'BASE + PP', 'PIVOT APPROACH', 'BREAKOUT', 'BREAKOUT CONFIRMED', 'BREAKOUT FAILURE')),
    candidate_pivot numeric(20, 6) check (candidate_pivot is null or candidate_pivot > 0),
    breakout_pivot numeric(20, 6) check (breakout_pivot is null or breakout_pivot > 0),
    context_valid boolean not null default true check (context_valid),
    reason_codes text[] not null default array['POCKET_PIVOT_VALID']::text[],
    created_at timestamptz not null default now(),
    constraint v2_pp_signals_ticker_format check (ticker ~ '^[A-Z0-9^][A-Z0-9.^=-]{0,14}$'),
    constraint v2_pp_signals_user_ticker_date_unique unique (user_id, ticker, signal_date),
    constraint v2_pp_signals_id_user_unique unique (id, user_id)
);

create index if not exists v2_pp_signals_user_date_idx
    on public.v2_pp_signals (user_id, signal_date desc, ticker);

create table if not exists public.v2_pp_trades (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    ticker text not null check (ticker = upper(btrim(ticker))),
    pp_signal_id uuid null,
    initial_entry_date date not null,
    initial_entry_price numeric(20, 6) not null check (initial_entry_price > 0),
    initial_shares numeric(24, 8) not null check (initial_shares > 0),
    initial_weight_pct numeric(8, 4) check (initial_weight_pct is null or initial_weight_pct between 0 and 100),
    initial_entry_stage text not null check (initial_entry_stage in ('BASE', 'BASE + PP', 'PIVOT APPROACH', 'BREAKOUT', 'BREAKOUT CONFIRMED', 'BREAKOUT FAILURE')),
    initial_stop_price numeric(20, 6) check (initial_stop_price is null or (initial_stop_price > 0 and initial_stop_price < initial_entry_price)),
    notes text not null default '' check (char_length(notes) <= 1500),
    created_at timestamptz not null default now(),
    constraint v2_pp_trades_ticker_format check (ticker ~ '^[A-Z0-9^][A-Z0-9.^=-]{0,14}$'),
    constraint v2_pp_trades_id_user_unique unique (id, user_id),
    constraint v2_pp_trades_signal_owner_fk foreign key (pp_signal_id, user_id)
        references public.v2_pp_signals (id, user_id) on delete restrict
);

create index if not exists v2_pp_trades_user_date_idx
    on public.v2_pp_trades (user_id, initial_entry_date desc, ticker);
create index if not exists v2_pp_trades_user_signal_idx
    on public.v2_pp_trades (user_id, pp_signal_id);

create table if not exists public.v2_pp_trade_adds (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    trade_id uuid not null,
    entry_date date not null,
    entry_price numeric(20, 6) not null check (entry_price > 0),
    shares numeric(24, 8) not null check (shares > 0),
    portfolio_weight_pct numeric(8, 4) check (portfolio_weight_pct is null or portfolio_weight_pct between 0 and 100),
    entry_stage text not null check (entry_stage in ('BASE', 'BASE + PP', 'PIVOT APPROACH', 'BREAKOUT', 'BREAKOUT CONFIRMED', 'BREAKOUT FAILURE')),
    initial_stop_price numeric(20, 6) check (initial_stop_price is null or (initial_stop_price > 0 and initial_stop_price < entry_price)),
    note text not null default '' check (char_length(note) <= 1000),
    created_at timestamptz not null default now(),
    constraint v2_pp_trade_adds_trade_owner_fk foreign key (trade_id, user_id)
        references public.v2_pp_trades (id, user_id) on delete cascade
);

create index if not exists v2_pp_trade_adds_user_trade_date_idx
    on public.v2_pp_trade_adds (user_id, trade_id, entry_date, created_at);

create table if not exists public.v2_pp_trade_exits (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    trade_id uuid not null,
    exit_date date not null,
    exit_price numeric(20, 6) not null check (exit_price > 0),
    shares numeric(24, 8) not null check (shares > 0),
    reason_code text not null default 'MANUAL'
        check (reason_code in ('BREAKOUT_FAILURE', 'INITIAL_STOP', 'TRAILING_STOP', 'TAKE_PROFIT', 'MANUAL', 'OTHER')),
    note text not null default '' check (char_length(note) <= 1000),
    created_at timestamptz not null default now(),
    constraint v2_pp_trade_exits_trade_owner_fk foreign key (trade_id, user_id)
        references public.v2_pp_trades (id, user_id) on delete cascade
);

create index if not exists v2_pp_trade_exits_user_trade_date_idx
    on public.v2_pp_trade_exits (user_id, trade_id, exit_date, created_at);

-- Enforce per-user access with RLS on every new journal table.
alter table public.v2_pp_signals enable row level security;
alter table public.v2_pp_trades enable row level security;
alter table public.v2_pp_trade_adds enable row level security;
alter table public.v2_pp_trade_exits enable row level security;

drop policy if exists v2_pp_signals_select_own on public.v2_pp_signals;
create policy v2_pp_signals_select_own on public.v2_pp_signals
    for select to authenticated using (auth.uid() = user_id);
drop policy if exists v2_pp_signals_insert_own on public.v2_pp_signals;
create policy v2_pp_signals_insert_own on public.v2_pp_signals
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists v2_pp_trades_select_own on public.v2_pp_trades;
create policy v2_pp_trades_select_own on public.v2_pp_trades
    for select to authenticated using (auth.uid() = user_id);
drop policy if exists v2_pp_trades_insert_own on public.v2_pp_trades;
create policy v2_pp_trades_insert_own on public.v2_pp_trades
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists v2_pp_trade_adds_select_own on public.v2_pp_trade_adds;
create policy v2_pp_trade_adds_select_own on public.v2_pp_trade_adds
    for select to authenticated using (auth.uid() = user_id);
drop policy if exists v2_pp_trade_adds_insert_own on public.v2_pp_trade_adds;
create policy v2_pp_trade_adds_insert_own on public.v2_pp_trade_adds
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists v2_pp_trade_exits_select_own on public.v2_pp_trade_exits;
create policy v2_pp_trade_exits_select_own on public.v2_pp_trade_exits
    for select to authenticated using (auth.uid() = user_id);
drop policy if exists v2_pp_trade_exits_insert_own on public.v2_pp_trade_exits;
create policy v2_pp_trade_exits_insert_own on public.v2_pp_trade_exits
    for insert to authenticated with check (auth.uid() = user_id);

-- Append-only journal: no updates/deletes are granted. Each fill/event remains auditable.
revoke all on table public.v2_pp_signals, public.v2_pp_trades,
    public.v2_pp_trade_adds, public.v2_pp_trade_exits from public, anon, authenticated;
grant select, insert on table public.v2_pp_signals, public.v2_pp_trades,
    public.v2_pp_trade_adds, public.v2_pp_trade_exits to authenticated;

-- Database-level integrity checks also protect writes made outside the UI.
create or replace function public.v2_pp_trade_add_validate()
returns trigger
language plpgsql
as $$
declare
    trade_entry_date date;
begin
    select initial_entry_date into trade_entry_date
      from public.v2_pp_trades
     where id = new.trade_id and user_id = new.user_id;
    if trade_entry_date is null then
        raise exception 'PP trade not found for this user';
    end if;
    if new.entry_date < trade_entry_date then
        raise exception 'Add entry date cannot be before the initial entry date';
    end if;
    return new;
end;
$$;

drop trigger if exists v2_pp_trade_add_validate_before_insert on public.v2_pp_trade_adds;
create trigger v2_pp_trade_add_validate_before_insert
before insert on public.v2_pp_trade_adds
for each row execute function public.v2_pp_trade_add_validate();

create or replace function public.v2_pp_trade_exit_validate()
returns trigger
language plpgsql
as $$
declare
    initial_entry_date date;
    initial_shares numeric;
    added_shares numeric;
    already_exited numeric;
    available_shares numeric;
    tolerance numeric;
begin
    select t.initial_entry_date, t.initial_shares
      into initial_entry_date, initial_shares
      from public.v2_pp_trades t
     where t.id = new.trade_id and t.user_id = new.user_id;
    if initial_entry_date is null then
        raise exception 'PP trade not found for this user';
    end if;
    if new.exit_date < initial_entry_date then
        raise exception 'Exit date cannot be before the initial entry date';
    end if;

    select coalesce(sum(a.shares), 0) into added_shares
      from public.v2_pp_trade_adds a
     where a.trade_id = new.trade_id and a.user_id = new.user_id
       and a.entry_date <= new.exit_date;
    select coalesce(sum(e.shares), 0) into already_exited
      from public.v2_pp_trade_exits e
     where e.trade_id = new.trade_id and e.user_id = new.user_id
       and e.exit_date <= new.exit_date;

    available_shares := initial_shares
        + added_shares
        - already_exited;
    tolerance := greatest(0.00000001, (initial_shares + added_shares) * 0.00000001);
    if new.shares > available_shares + tolerance then
        raise exception 'Exit quantity exceeds shares available by exit date';
    end if;
    return new;
end;
$$;

drop trigger if exists v2_pp_trade_exit_validate_before_insert on public.v2_pp_trade_exits;
create trigger v2_pp_trade_exit_validate_before_insert
before insert on public.v2_pp_trade_exits
for each row execute function public.v2_pp_trade_exit_validate();

create or replace function public.v2_pp_trade_signal_ticker_validate()
returns trigger
language plpgsql
as $$
declare
    signal_ticker text;
begin
    if new.pp_signal_id is null then
        return new;
    end if;
    select ticker into signal_ticker
      from public.v2_pp_signals
     where id = new.pp_signal_id and user_id = new.user_id;
    if signal_ticker is null then
        raise exception 'Linked PP signal is not owned by this user';
    end if;
    if signal_ticker <> new.ticker then
        raise exception 'Trade ticker must match linked PP signal ticker';
    end if;
    return new;
end;
$$;

drop trigger if exists v2_pp_trade_signal_ticker_validate_before_insert on public.v2_pp_trades;
create trigger v2_pp_trade_signal_ticker_validate_before_insert
before insert on public.v2_pp_trades
for each row execute function public.v2_pp_trade_signal_ticker_validate();
