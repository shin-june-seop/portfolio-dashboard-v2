# Legacy Supabase Static Operation Inventory

Source: `app.py`; call sites found: **56**.

This is code-level static discovery only. It does not inspect the live Supabase schema, column types, keys, constraints, RLS policies, triggers, views, or stored procedures.

| Table/RPC | Operations found | Functions found |
|---|---|---|
| `<dynamic:table>` | select | pp_table_rows_cached |
| `<dynamic:table_name>` | select | v31_pp_table_exists |
| `daily_directions` | upsert | v16_upsert_daily_direction |
| `market_regime_daily_snapshots` | select, upsert | v25_load_market_history, v25_save_market_snapshot |
| `momentum_daily_snapshots` | select, update, upsert | _latest_content, _latest_stored_dates, _update, _v21_load_snapshots_raw, commit_backfill_rows, update_snapshot_by_market_date, v21_backfill_missing_snapshot_dates, v21_save_scan_results, v50_repair_known_meta_history, v50_repair_latest_snapshot_metadata |
| `momentum_watchlist` | delete, select, update, upsert | v21_add_watchlist_ticker, v21_load_watchlist, v21_remove_watchlist_tickers, v271_apply_default_sectors, v271_update_watchlist_sector, v273_apply_exact_current_watchlist_sectors |
| `portfolio_snapshots` | select, upsert | <module>, v17_load_snapshots, v17_save_daily_snapshot |
| `portfolio_values` | upsert | v16_upsert_snapshot |
| `pp_signals` | insert, select, update | _write, pp_entry_context, v31_capture_current_pp_signals, v31_load_pp_signals |
| `pp_trade_adds` | insert, select | v51_load_pp_adds, v51_record_pp_add |
| `pp_trade_entries` | insert, select, update | v31_add_trade_entry, v31_create_pp_trade, v31_load_pp_entries, v45_save_trailing_stop |
| `pp_trade_exits` | insert, select | v35_load_pp_exits, v35_record_exit |
| `pp_trades` | delete, insert, select, update | _v31_load_pp_trades_raw, record_trim_alert_once, v31_create_pp_trade, v31_sync_trade_basis_after_entry, v31_update_trade_metrics, v35_record_exit, v38_delete_pp_trade, v38_repair_trade_entry_stages |
| `transactions` | delete, insert, select | v15_load_transactions, v15_save_transactions |

## Review priorities

1. Obtain a schema-only export or user-reviewed table/column screenshots from Supabase; do not enable V2 adapter based on this report alone.
2. Map every write-capable legacy path, especially transaction replacement, watchlist edits, PP trades/entries/exits/adds, and snapshot upserts.
3. Record primary/unique keys, foreign keys, data types, nullability, RLS policies, and trigger behavior before defining any V2 mappings.
4. V2 must remain read-only and disabled until exact read allowlists and projections are approved.
