-- Additive PP Tracking snapshot metrics for Portfolio Dashboard V2.
-- Run once in the SAME V2 Supabase project after v2_pp_journal_schema.sql.
-- Existing journal rows are kept; their historical values remain NULL rather than being backfilled from today's data.

alter table public.v2_pp_signals
    add column if not exists rs_rating numeric,
    add column if not exists momentum_score numeric,
    add column if not exists pp_quality text,
    add column if not exists market_regime text,
    add column if not exists pivot_dist_pct numeric;

comment on column public.v2_pp_signals.rs_rating is 'Signal-date relative-strength rating snapshot; NULL means unavailable at save time.';
comment on column public.v2_pp_signals.momentum_score is 'Signal-date momentum percentile snapshot; NULL means unavailable at save time.';
comment on column public.v2_pp_signals.pp_quality is 'Legacy-compatible PP quality grade A/B/C calculated from available signal-date metrics.';
comment on column public.v2_pp_signals.market_regime is 'Market regime label only when its EOD date matches the PP signal date.';
comment on column public.v2_pp_signals.pivot_dist_pct is 'Signal price distance to saved reference pivot, in percent.';
