from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def test_scan_downloads_and_analyzes_with_bounded_thread_pools_and_stable_order():
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _run_scan(")
    end = app.index("def _load_market_regime_bars(", start)
    scan = app[start:end]

    assert 'ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v2-eod-fetch")' in scan
    assert 'ThreadPoolExecutor(max_workers=min(4, len(raw)), thread_name_prefix="v2-eod-engine")' in scan
    assert 'as_completed(futures)' in scan
    assert 'progress_callback: Callable[[str, int, int, str], None] | None = None' in scan
    assert 'rows = [rows_by_symbol[ticker] for ticker in requested if ticker in rows_by_symbol]' in scan
    assert 'notify("download", 0' in scan
    assert 'notify("analysis", 0' in scan


def test_broad_momentum_universe_uses_parallel_bounded_chunks_and_preserves_rank_history():
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _load_broad_breadth_closes(")
    end = app.index("def _compute_market_regime()", start)
    loader = app[start:end]

    assert 'pd.Timedelta(days=450)' in loader
    assert 'range(0, len(tickers), 40)' in loader
    assert 'ThreadPoolExecutor(max_workers=min(4, len(chunks))' in loader
    assert 'timeout=15' in loader
    assert 'len(out) < 253' in loader
    assert 'pd.Timestamp(out.index[-1]).date() != anchor' in loader


def test_eod_cache_changes_bucket_after_regular_session_close_and_is_shared():
    app = APP.read_text(encoding="utf-8")
    assert 'def _current_eod_cache_bucket()' in app
    assert 'PRE_CLOSE' in app and 'POST_CLOSE' in app
    assert '@st.cache_data(ttl=60 * 60, show_spinner=False)\ndef _load_yfinance_bars_cached' in app
    assert 'return _load_yfinance_bars(symbol)' in app
    assert '성능 진단 · 일봉 다운로드' in app
