"""Portfolio Dashboard V2 — shared-engine UI milestone.

This app is intentionally read-only. Momentum Monitor and Pattern Scanner consume
one common analysis service; neither screen computes pattern/pivot/stage locally.
"""
from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime
from typing import Any, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from dashboard_v2.services.screening_service import analyze_symbol, load_seed_snapshots
from dashboard_v2.services.quality_recovery_service import (
    analyze_legacy_quality_recovery,
    fetch_yfinance_fundamentals,
    fundamentals_from_income_statement,
    quality_recovery_table,
)
from dashboard_v2.services.live_monitor_service import (
    NY as LIVE_NY,
    YFinanceIntradayProvider,
    assess_live_data_status,
    classify_live_state,
    crossed_pivot_this_session,
    live_session_metrics,
)
from dashboard_v2.services.pp_journal_repository import (
    ENTRY_STAGES, EXIT_REASONS, PPJournalValidationError,
    add_pp_trade_add, add_pp_trade_exit, create_pp_trade,
    list_pp_signals, list_pp_trades, list_trade_adds, list_trade_exits, save_pp_signals,
)
from dashboard_v2.services.pp_journal_analytics import (
    analyze_pp_signal_outcome, summarize_trade, summarize_trades,
)
from dashboard_v2.services.watchlist_repository import (
    active_tickers,
    add_watchlist_item,
    create_supabase_client,
    delete_watchlist_item,
    list_watchlist,
    update_watchlist_item,
    WatchlistValidationError,
)

ROOT = Path(__file__).resolve().parent
PAGES = [
    "Watchlist", "Momentum Monitor", "Live Monitor", "Quality Recovery", "Pattern Scanner", "PP Journal",
    "Overview", "Holdings", "Market Regime", "App Monitor", "ADD Monitor",
    "Performance", "Allocation", "Chart", "Leaders",
]

st.set_page_config(page_title="Portfolio Dashboard V2", page_icon="📊", layout="wide")


@st.cache_data(ttl=900, show_spinner=False)
def _load_yfinance_bars(symbol: str) -> pd.DataFrame:
    from dashboard_v2.services.yfinance_provider import YFinanceDailyProvider
    return YFinanceDailyProvider(history_days=5 * 365).daily_bars(symbol)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _load_adjusted_close_history(symbol: str) -> pd.DataFrame:
    """Load dividend/split-adjusted daily closes for legacy V30.5 RS only.

    These values are never passed into the technical moving-average or pivot
    calculations. The same completed-US-session cutoff as the V2 EOD layer is
    applied before returning them.
    """
    import yfinance as yf

    ticker = yf.Ticker(str(symbol).upper().strip())
    raw = ticker.history(period="2y", interval="1d", auto_adjust=True, actions=False, timeout=15)
    if raw is None or raw.empty or "Close" not in raw.columns:
        return pd.DataFrame(columns=["Date", "Close"])
    idx = pd.DatetimeIndex(pd.to_datetime(raw.index))
    idx_et = idx.tz_convert("America/New_York") if idx.tz is not None else idx
    now_et = pd.Timestamp.now(tz="America/New_York")
    row_dates = [stamp.date() for stamp in idx_et]
    cutoff_time = pd.Timestamp("16:15").time()
    if now_et.time().replace(tzinfo=None) < cutoff_time:
        keep = [day < now_et.date() for day in row_dates]
    else:
        keep = [day <= now_et.date() for day in row_dates]
    values = pd.to_numeric(raw["Close"], errors="coerce")
    out = pd.DataFrame({"Date": pd.to_datetime(row_dates), "Close": values.to_numpy()})
    out = out.loc[keep].dropna(subset=["Date", "Close"]).copy()
    out = out.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)
    return out


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _load_quality_fundamentals(symbol: str) -> dict[str, Any]:
    """Legacy-compatible annual Yahoo income-statement gate; no SEC dependency."""
    return fetch_yfinance_fundamentals(symbol)


@st.cache_data(ttl=60, show_spinner=False)
def _load_live_intraday_bars(symbol: str) -> pd.DataFrame:
    """Provisional 5-minute bars for Live Monitor only; never pass these to EOD engines."""
    return YFinanceIntradayProvider().intraday_bars(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _load_regression_bars(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "regression" / "ohlcv" / f"{symbol.upper()}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No bundled regression OHLCV for {symbol}")
    return pd.read_csv(path)


@st.cache_data(ttl=900, show_spinner=False)
def _load_seed_state() -> dict:
    return load_seed_snapshots()


@st.cache_data(ttl=900, show_spinner=False)
def _analyze_cached(symbol: str, bars: pd.DataFrame, seed_snapshot: dict | None, benchmark: pd.DataFrame | None) -> dict:
    return analyze_symbol(symbol, bars, seed_snapshot=seed_snapshot, benchmark_bars=benchmark)


def _load_bars(symbol: str, source: str) -> pd.DataFrame:
    if source == "Yahoo Finance (EOD)":
        return _load_yfinance_bars(symbol)
    if source == "Bundled regression data (historical)":
        return _load_regression_bars(symbol)
    raise RuntimeError("Select a data source to load market data")


def _run_scan(symbols: list[str], source: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[tuple[str, str]], float]:
    start = time.perf_counter()
    seed = _load_seed_state()
    raw: dict[str, pd.DataFrame] = {}
    errors: list[tuple[str, str]] = []
    for symbol in symbols:
        try:
            raw[symbol] = _load_bars(symbol, source)
        except Exception as exc:
            errors.append((symbol, f"{type(exc).__name__}: {exc}"))

    benchmark = None
    if source == "Yahoo Finance (EOD)":
        try:
            benchmark = _load_yfinance_bars("SPY")
        except Exception as exc:
            errors.append(("SPY benchmark", f"{type(exc).__name__}: {exc}"))

    rows = []
    for symbol, bars in raw.items():
        try:
            rows.append(_analyze_cached(symbol, bars, seed.get(symbol), benchmark))
        except Exception as exc:
            errors.append((symbol, f"Analysis {type(exc).__name__}: {exc}"))
    elapsed = time.perf_counter() - start
    return pd.DataFrame(rows), raw, errors, elapsed


def _fmt_num(value, digits: int = 2):
    return "—" if value is None or pd.isna(value) else f"{float(value):,.{digits}f}"


def _render_scan_table(frame: pd.DataFrame, *, pattern_mode: bool = False):
    if frame.empty:
        st.info("표시할 종목 분석 결과가 없습니다.")
        return
    view = frame.copy()
    view["재베이스"] = view["rebase_active"].map({True: "진행 중", False: "아님"})
    view["가격"] = view["close"].map(lambda x: _fmt_num(x))
    view["일간 %"] = view["day_change_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["후보 피봇"] = view["candidate_pivot"].map(_fmt_num)
    view["후보 거리"] = view["candidate_distance_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["돌파 피봇"] = view["breakout_pivot"].map(_fmt_num)
    view["돌파 거리"] = view["breakout_distance_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["20D Ref"] = view["twenty_day_reference"].map(_fmt_num)
    view["RVOL"] = view["rvol"].map(lambda x: "—" if pd.isna(x) else f"{x:.2f}×")
    view["3개월 수익률"] = view["return_3m_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["SPY 대비 RS(3M)"] = view["rs_3m_vs_spy_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%p")
    view["패턴"] = view["pattern"]
    view["Stage"] = view["stage"]
    view["기준일"] = view["as_of_date"]
    view["상태 출처"] = view["replay_provenance"].map({
        "BUNDLED_REPLAY_SNAPSHOT": "번들 스냅샷",
        "INCREMENTAL_FROM_BUNDLED_SNAPSHOT": "스냅샷 이후 증분 재생",
        "ROLLING_300_BAR_REPLAY": "최근 300봉 재생",
    }).fillna(view["replay_provenance"])
    cols = ["ticker", "기준일", "가격", "일간 %", "패턴", "후보 피봇", "후보 거리", "돌파 피봇", "돌파 거리", "20D Ref", "Stage", "재베이스", "RVOL", "3개월 수익률", "SPY 대비 RS(3M)"]
    if pattern_mode:
        cols = ["ticker", "기준일", "패턴", "후보 피봇", "후보 거리", "돌파 피봇", "돌파 거리", "Stage", "재베이스", "RVOL", "3개월 수익률", "SPY 대비 RS(3M)"]
    st.dataframe(view[cols].rename(columns={"ticker": "종목"}), use_container_width=True, hide_index=True)


def _render_symbol_detail(frame: pd.DataFrame, raw: dict[str, pd.DataFrame]):
    if frame.empty:
        return
    st.markdown("#### 종목 상세")
    symbol = st.selectbox("차트 및 엔진 상세", frame["ticker"].tolist(), key="detail_symbol")
    row = frame.loc[frame["ticker"] == symbol].iloc[0]
    bars = raw.get(symbol)
    a, b, c, d = st.columns(4)
    a.metric("종가", f"${row['close']:,.2f}", None if pd.isna(row["day_change_pct"]) else f"{row['day_change_pct']:+.2f}%")
    b.metric("Stage", str(row["stage"]))
    c.metric("패턴", str(row["pattern"]))
    d.metric("재베이스", "진행 중" if row["rebase_active"] else "아님")
    if bars is not None and not bars.empty:
        chart = bars.copy()
        chart["Date"] = pd.to_datetime(chart["Date"])
        chart = chart.set_index("Date")
        chart = chart[["Close"]].copy()
        chart["SMA20"] = bars["Close"].rolling(20).mean().to_numpy()
        chart["SMA50"] = bars["Close"].rolling(50).mean().to_numpy()
        st.line_chart(chart.tail(180), height=280)
    with st.expander("엔진 상태 및 데이터 출처"):
        st.json({
            "as_of_date": row["as_of_date"],
            "pattern_start_date": row["pattern_start_date"],
            "candidate_pivot": _num_or_none(row["candidate_pivot"]),
            "breakout_pivot": _num_or_none(row["breakout_pivot"]),
            "twenty_day_reference": _num_or_none(row["twenty_day_reference"]),
            "rebase_active": bool(row["rebase_active"]),
            "rebase_started_at": row["rebase_started_at"],
            "rebase_reason": row["rebase_reason"],
            "rebase_low": _num_or_none(row["rebase_low"]),
            "rebase_low_date": row["rebase_low_date"],
            "rebase_secondary_reasons": row["rebase_secondary_reasons"],
            "breakout_date": row["breakout_date"],
            "breakout_cycle_active": bool(row["breakout_cycle_active"]),
            "breakout_event_today": bool(row["breakout_event"]),
            "breakout_failure_today": bool(row["breakout_failure"]),
            "historical_breakout_recovered": bool(row.get("historical_breakout_recovered", False)),
            "historical_breakout_recovery_pattern": row.get("historical_breakout_recovery_pattern"),
            "historical_breakout_recovery_event_date": row.get("historical_breakout_recovery_event_date"),
            "historical_breakout_recovery_reason": row.get("historical_breakout_recovery_reason"),
            "historical_breakout_recovery_seed_pattern": row.get("historical_breakout_recovery_seed_pattern"),
            "historical_breakout_recovery_seed_pattern_matches_current": row.get("historical_breakout_recovery_seed_pattern_matches_current"),
            "historical_breakout_recovery_candidate_pivot": _num_or_none(row.get("historical_breakout_recovery_candidate_pivot")),
            "historical_breakout_recovery_previous_close": _num_or_none(row.get("historical_breakout_recovery_previous_close")),
            "historical_breakout_recovery_event_close": _num_or_none(row.get("historical_breakout_recovery_event_close")),
            "historical_breakout_recovery_attempted_event_date": row.get("historical_breakout_recovery_attempted_event_date"),
            "pocket_pivot_today": bool(row["pocket_pivot_today"]),
            "pocket_pivot_reason": row["pocket_pivot_reason"],
            "detected_pattern_diagnostic": row["detected_pattern_diagnostic"],
            "replay_provenance": row["replay_provenance"],
            "validation_status": row["validation_status"],
            "technical_price_basis": row["technical_price_basis"],
            "return_price_basis": row["return_price_basis"],
        })


def _num_or_none(value):
    return None if value is None or pd.isna(value) else float(value)


def _pivot_distance(price: float | None, pivot: float | None):
    if price is None or pivot is None or pd.isna(price) or pd.isna(pivot):
        return None
    try:
        price_value, pivot_value = float(price), float(pivot)
    except (TypeError, ValueError):
        return None
    if price_value <= 0 or pivot_value <= 0:
        return None
    return (price_value / pivot_value - 1.0) * 100.0


def _previous_completed_close_before_session(daily_bars: pd.DataFrame | None, session_date):
    """Return the last completed daily close strictly before the intraday session date."""
    if daily_bars is None or daily_bars.empty or "Date" not in daily_bars or "Close" not in daily_bars:
        return None
    dates = pd.to_datetime(daily_bars["Date"], errors="coerce")
    frame = daily_bars.loc[dates.dt.date < session_date].copy()
    if frame.empty:
        return None
    try:
        value = float(frame.iloc[-1]["Close"])
    except (TypeError, ValueError):
        return None
    return value if value > 0 and pd.notna(value) else None


def _live_source_status(status_code: str) -> str:
    return {
        "LIVE": "LIVE · 5분 데이터 정상",
        "DATA_STALE": "DATA STALE · 신호 비활성",
        "DATA_INCOMPLETE": "DATA INCOMPLETE · 신호 비활성",
        "MARKET_CLOSED": "MARKET CLOSED · 마지막 세션 데이터",
    }.get(status_code, f"{status_code} · 신호 비활성")


def _render_live_monitor_page(symbols: list[str]) -> None:
    st.subheader("Live Monitor")
    st.caption("장중 관찰 전용 화면입니다. 실시간 가격/누적 거래량은 공식 EOD 패턴·피봇·Stage 기록을 변경하지 않습니다.")
    st.warning(
        "데이터 공급원: Yahoo Finance 5분봉(잠정). 실시간·거래소 원천 데이터가 보장되지 않으며 지연/누락될 수 있습니다. "
        "LIVE 표시도 실제 주문 신호가 아니며 주문은 제출하지 않습니다."
    )
    with st.expander("계산 기준 및 안전장치", expanded=False):
        st.markdown(
            "- **공식 ENTRY STAGE / Pattern / Candidate Pivot / Breakout Pivot:** 기존 완료 일봉 공통 엔진의 결과만 사용합니다.\n"
            "- **LIVE STATE:** 마지막 장중 5분봉과 공식 EOD 피봇을 비교한 임시 상태입니다. 장중 이탈은 공식 `BREAKOUT FAILURE`로 기록하지 않습니다.\n"
            "- **장중 RVOL:** 현재 세션 누적 5분 거래량 ÷ 직전 최대 5개 세션의 같은 시각까지 누적 거래량 평균입니다. 최소 3개 기준 세션이 없으면 `—`로 둡니다.\n"
            "- 장중 RVOL은 공식 EOD RVOL과 다른 지표이며 `BREAKOUT CONFIRMED` 판정에 사용하지 않습니다.\n"
            "- 정규장 중 최신 봉이 15분보다 오래됐거나 당일 봉이 없으면 `DATA STALE`로 표시하고 LIVE 신호를 비활성화합니다. 정규장 종료 후에는 `MARKET CLOSED`로 표시합니다."
        )

    if not symbols:
        st.info("활성 관심종목이 없습니다. Watchlist에서 종목을 추가하거나 활성화해 주세요.")
        return

    refresh_col, info_col = st.columns([1, 3])
    if refresh_col.button("장중 데이터 새로고침", type="primary", key="v2_live_refresh"):
        _load_live_intraday_bars.clear()
        st.rerun()
    info_col.caption("장중 데이터 캐시: 최대 60초 · 공식 완료 일봉 기준 데이터 캐시: 최대 15분")

    with st.spinner(f"{len(symbols)}개 종목의 완료 일봉 기준선과 장중 데이터를 확인 중…"):
        eod_result, daily_raw, eod_errors, elapsed = _run_scan(symbols, "Yahoo Finance (EOD)")

    eod_by_symbol: dict[str, dict] = {}
    if not eod_result.empty:
        eod_by_symbol = {
            str(row["ticker"]): row.to_dict()
            for _, row in eod_result.iterrows()
        }

    now = datetime.now(tz=LIVE_NY)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    live_rows: list[dict] = []
    live_errors: list[tuple[str, str]] = []

    def load_intraday_one(symbol: str):
        try:
            return symbol, _load_live_intraday_bars(symbol), None
        except Exception as exc:
            return symbol, None, f"{type(exc).__name__}: {exc}"

    # A small bounded pool avoids a long serial wait for larger watchlists.
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(symbols)))) as pool:
        futures = {pool.submit(load_intraday_one, symbol): symbol for symbol in symbols}
        intraday_results = [future.result() for future in as_completed(futures)]

    for symbol, intraday_bars, fetch_error in intraday_results:
        baseline = eod_by_symbol.get(symbol)
        if fetch_error or intraday_bars is None or intraday_bars.empty:
            live_errors.append((symbol, fetch_error or "LiveMarketDataError: no intraday bars"))
            live_rows.append({
                "ticker": symbol, "live_price": None, "change_pct": None, "session_volume": None,
                "live_rvol": None, "candidate_pivot": None, "candidate_distance_pct": None,
                "breakout_pivot": None, "breakout_distance_pct": None, "live_state": "DATA INCOMPLETE",
                "data_status": "DATA INCOMPLETE · 신호 비활성", "last_bar_at": None, "bar_age_min": None,
                "eod_pattern": (baseline or {}).get("pattern", "—"),
                "eod_stage": (baseline or {}).get("stage", "—"), "eod_as_of_date": (baseline or {}).get("as_of_date", "—"),
                "source": "오류",
            })
            continue

        bars_by_symbol[symbol] = intraday_bars
        try:
            status = assess_live_data_status(intraday_bars, now=now)
            metrics = live_session_metrics(intraday_bars)
            latest_stamp = pd.Timestamp(intraday_bars["Date"].iloc[-1]).tz_convert(LIVE_NY)
            session_date = latest_stamp.date()
            live_price = metrics.get("last_price")
            daily_bars = daily_raw.get(symbol)
            previous_close = _previous_completed_close_before_session(daily_bars, session_date)
            change_pct = _pivot_distance(live_price, previous_close)
            candidate_pivot = (baseline or {}).get("candidate_pivot")
            breakout_pivot = (baseline or {}).get("breakout_pivot")
            active_cycle = bool((baseline or {}).get("breakout_cycle_active", False))
            eod_baseline_ok = baseline is not None and previous_close is not None
            if not eod_baseline_ok:
                state = "DATA INCOMPLETE · EOD 기준선 없음"
                display_status = "DATA INCOMPLETE · 신호 비활성"
            elif status.code == "DATA_STALE":
                state = "DATA STALE · LIVE 신호 비활성"
                display_status = _live_source_status(status.code)
            elif status.code == "DATA_INCOMPLETE":
                state = "DATA INCOMPLETE · LIVE 신호 비활성"
                display_status = _live_source_status(status.code)
            else:
                crossed = crossed_pivot_this_session(intraday_bars, candidate_pivot, now=now)
                calculated_state = classify_live_state(
                    live_price=live_price,
                    candidate_pivot=candidate_pivot,
                    breakout_pivot=breakout_pivot,
                    breakout_cycle_active=active_cycle,
                    candidate_crossed_today=crossed,
                )
                state = f"마지막 세션: {calculated_state}" if status.code == "MARKET_CLOSED" else calculated_state
                display_status = _live_source_status(status.code)

            candidate_distance = _pivot_distance(live_price, candidate_pivot)
            breakout_distance = _pivot_distance(live_price, breakout_pivot)
            rvol = metrics.get("live_rvol")
            rvol_display = None if rvol is None else float(rvol)
            live_rows.append({
                "ticker": symbol,
                "live_price": live_price,
                "change_pct": change_pct,
                "session_volume": metrics.get("session_volume"),
                "live_rvol": rvol_display,
                "live_rvol_reference_sessions": metrics.get("reference_sessions", 0),
                "candidate_pivot": candidate_pivot,
                "candidate_distance_pct": candidate_distance,
                "breakout_pivot": breakout_pivot,
                "breakout_distance_pct": breakout_distance,
                "live_state": state,
                "data_status": display_status,
                "last_bar_at": latest_stamp.strftime("%Y-%m-%d %H:%M ET"),
                "bar_age_min": status.age_minutes,
                "eod_pattern": (baseline or {}).get("pattern", "—"),
                "eod_stage": (baseline or {}).get("stage", "—"),
                "eod_as_of_date": (baseline or {}).get("as_of_date", "—"),
                "source": "Yahoo Finance 5m",
                "_status_code": status.code,
            })
        except Exception as exc:
            live_errors.append((symbol, f"Live analysis {type(exc).__name__}: {exc}"))
            live_rows.append({
                "ticker": symbol, "live_price": None, "change_pct": None, "session_volume": None,
                "live_rvol": None, "candidate_pivot": None, "candidate_distance_pct": None,
                "breakout_pivot": None, "breakout_distance_pct": None, "live_state": "DATA INCOMPLETE",
                "data_status": "DATA INCOMPLETE · 신호 비활성", "last_bar_at": None, "bar_age_min": None,
                "eod_pattern": (baseline or {}).get("pattern", "—"),
                "eod_stage": (baseline or {}).get("stage", "—"), "eod_as_of_date": (baseline or {}).get("as_of_date", "—"),
                "source": "오류",
            })

    frame = pd.DataFrame(live_rows)
    if frame.empty:
        st.error("Live Monitor 결과가 없습니다.")
        return

    fresh_n = int(frame.get("_status_code", pd.Series(dtype=str)).eq("LIVE").sum()) if "_status_code" in frame else 0
    limited_symbols = set(frame.loc[
        frame["data_status"].astype(str).str.startswith(("DATA STALE", "DATA INCOMPLETE")), "ticker"
    ].astype(str))
    limited_symbols.update(symbol for symbol, _ in live_errors)
    stale_n = len(limited_symbols)
    market_closed = any("MARKET CLOSED" in str(value) for value in frame["data_status"])
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("관심종목", len(symbols))
    k2.metric("LIVE 데이터 정상", fresh_n)
    k3.metric("데이터 제한/오류", stale_n)
    k4.metric("EOD 계산", f"{elapsed:.1f}s")
    if market_closed and fresh_n == 0:
        st.info("미국 정규장이 닫혀 있습니다. 마지막 장중 데이터와 공식 EOD 상태만 참고용으로 표시합니다. 실시간 신호는 비활성입니다.")
    if eod_errors:
        with st.expander(f"완료 일봉 기준선 오류 ({len(eod_errors)})", expanded=False):
            st.dataframe(pd.DataFrame(eod_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)

    display = frame.copy()
    display["현재/마지막 가격"] = display["live_price"].map(_fmt_num)
    display["직전 완료 일봉 대비"] = display["change_pct"].map(lambda x: "—" if x is None or pd.isna(x) else f"{x:+.2f}%")
    display["장중 누적 거래량"] = display["session_volume"].map(lambda x: "—" if x is None or pd.isna(x) else f"{int(x):,}")
    display["LIVE RVOL"] = display.apply(
        lambda row: "—" if pd.isna(row.get("live_rvol")) else f"{float(row['live_rvol']):.2f}× (n={int(row.get('live_rvol_reference_sessions', 0))})",
        axis=1,
    )
    display["Candidate Pivot"] = display["candidate_pivot"].map(_fmt_num)
    display["후보 피봇 거리"] = display["candidate_distance_pct"].map(lambda x: "—" if x is None or pd.isna(x) else f"{x:+.2f}%")
    display["Breakout Pivot"] = display["breakout_pivot"].map(_fmt_num)
    display["돌파 피봇 거리"] = display["breakout_distance_pct"].map(lambda x: "—" if x is None or pd.isna(x) else f"{x:+.2f}%")
    display["공식 EOD Pattern"] = display.get("eod_pattern", pd.Series("—", index=display.index)).fillna("—")
    display["공식 EOD Stage"] = display["eod_stage"]
    display["EOD 기준일"] = display["eod_as_of_date"].fillna("—")
    display["데이터 상태"] = display["data_status"]
    display["마지막 5분봉"] = display["last_bar_at"].fillna("—")
    columns = [
        "ticker", "현재/마지막 가격", "직전 완료 일봉 대비", "장중 누적 거래량", "LIVE RVOL",
        "공식 EOD Pattern", "Candidate Pivot", "후보 피봇 거리", "Breakout Pivot", "돌파 피봇 거리",
        "live_state", "공식 EOD Stage", "EOD 기준일", "데이터 상태", "마지막 5분봉",
    ]
    st.dataframe(
        display[columns].rename(columns={"ticker": "종목", "live_state": "LIVE STATE"}),
        use_container_width=True, hide_index=True,
    )

    available = [symbol for symbol in symbols if symbol in bars_by_symbol]
    if available:
        st.markdown("#### 장중 차트 및 기준선")
        selected = st.selectbox("종목", available, key="v2_live_detail_symbol")
        live_bars = bars_by_symbol[selected].copy()
        live_bars["Date"] = pd.to_datetime(live_bars["Date"])
        live_bars = live_bars.set_index("Date")
        eod_row = eod_by_symbol.get(selected, {})
        candidate = eod_row.get("candidate_pivot")
        breakout = eod_row.get("breakout_pivot")
        live_chart = live_bars[["Close"]].rename(columns={"Close": "5분봉 종가"}).tail(120).copy()
        if candidate is not None and pd.notna(candidate):
            live_chart["Candidate Pivot · EOD"] = float(candidate)
        if breakout is not None and pd.notna(breakout):
            live_chart["Breakout Pivot · EOD"] = float(breakout)
        st.line_chart(live_chart, height=300)
        st.caption("피봇선은 완료 일봉 엔진에서 가져온 고정 기준선입니다. 장중 차트로 패턴·피봇을 재계산하지 않습니다.")

    if live_errors:
        with st.expander(f"장중 데이터/계산 오류 ({len(live_errors)})", expanded=bool(live_errors)):
            st.dataframe(pd.DataFrame(live_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)


def _save_auth_session(session) -> None:
    if session is None:
        return
    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)
    if access_token and refresh_token:
        st.session_state["_v2_supabase_access_token"] = access_token
        st.session_state["_v2_supabase_refresh_token"] = refresh_token


def _clear_auth_session() -> None:
    st.session_state.pop("_v2_supabase_access_token", None)
    st.session_state.pop("_v2_supabase_refresh_token", None)
    st.session_state.pop("_v2_supabase_user_email", None)


def _user_from_auth_response(response):
    return getattr(response, "user", None)


def _require_v2_login():
    """Protect the public Streamlit app and only expose each user's own V2 rows."""
    try:
        supabase_url = str(st.secrets.get("SUPABASE_URL", "")).strip()
        supabase_anon_key = str(st.secrets.get("SUPABASE_ANON_KEY", "")).strip()
    except Exception:
        supabase_url, supabase_anon_key = "", ""

    if not supabase_url or not supabase_anon_key:
        st.title("Portfolio Dashboard V2 · 최초 설정")
        st.warning("V2 전용 Supabase 프로젝트 연결이 아직 설정되지 않았습니다.")
        st.markdown("""
이 대시보드는 공개 URL로 배포되므로, 관심종목을 보호하기 위해 로그인과 사용자별 RLS를 사용합니다.

1. **새로운 빈 Supabase 프로젝트**를 만듭니다. 기존 프로젝트와 기존 데이터는 연결하지 않습니다.
2. 저장소의 `sql/v2_watchlist_schema.sql`을 Supabase SQL Editor에서 실행합니다.
3. Supabase **Authentication → Users**에서 본인 계정을 직접 생성하고, 공개 회원가입은 비활성화합니다.
4. Streamlit Cloud의 앱 설정 → **Secrets**에 아래 두 값만 추가합니다. 키는 GitHub에 올리지 마세요.
""")
        st.code('SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"\nSUPABASE_ANON_KEY = "YOUR_PUBLIC_ANON_OR_PUBLISHABLE_KEY"', language="toml")
        st.caption("service_role 키는 사용하지 않습니다. 로그인 후에는 사용자별 Row Level Security가 데이터 접근을 제한합니다.")
        st.stop()

    try:
        client = create_supabase_client(supabase_url, supabase_anon_key)
    except Exception as exc:
        st.title("Portfolio Dashboard V2 · 연결 설정 확인")
        st.error(f"Supabase 클라이언트를 만들지 못했습니다: {type(exc).__name__}: {exc}")
        st.stop()

    user = None
    access = st.session_state.get("_v2_supabase_access_token")
    refresh = st.session_state.get("_v2_supabase_refresh_token")
    if access and refresh:
        try:
            restored = client.auth.set_session(access, refresh)
            _save_auth_session(getattr(restored, "session", None) or restored)
            user_response = client.auth.get_user()
            user = getattr(user_response, "user", None)
        except Exception:
            _clear_auth_session()
            user = None

    if user is None:
        st.title("Portfolio Dashboard V2 · 로그인")
        st.caption("새 V2 전용 Supabase 계정으로 로그인하세요. 기존 대시보드 데이터는 가져오지 않습니다.")
        with st.form("v2_login_form", clear_on_submit=False):
            email = st.text_input("이메일", key="v2_login_email")
            password = st.text_input("비밀번호", type="password", key="v2_login_password")
            submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if submitted:
            try:
                response = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
                session = getattr(response, "session", None)
                user = _user_from_auth_response(response)
                if session is None or user is None:
                    raise RuntimeError("로그인 세션을 만들지 못했습니다.")
                _save_auth_session(session)
                st.session_state["_v2_supabase_user_email"] = getattr(user, "email", email.strip())
                st.rerun()
            except Exception as exc:
                st.error(f"로그인에 실패했습니다. 이메일/비밀번호와 Supabase Auth 사용자를 확인하세요. ({type(exc).__name__})")
        st.info("계정은 새 Supabase 프로젝트의 Authentication → Users에서 직접 생성합니다. 앱 안에는 공개 회원가입을 제공하지 않습니다.")
        st.stop()

    # The access token may have been refreshed by the SDK; keep the newest values in session state.
    try:
        current = client.auth.get_session()
        _save_auth_session(current)
    except Exception:
        pass
    st.session_state["_v2_supabase_user_email"] = getattr(user, "email", "") or st.session_state.get("_v2_supabase_user_email", "")
    return client, user


def _render_watchlist_page(client, user_id: str, rows: list[dict]) -> None:
    st.subheader("관심종목 관리")
    st.caption("V2 전용 신규 목록입니다. 기존 관심종목이나 거래 기록은 가져오지 않았습니다. 활성 종목만 Momentum Monitor/Live Monitor/Pattern Scanner의 분석 대상에 포함됩니다.")

    with st.expander("➕ 관심종목 추가", expanded=not rows):
        with st.form("v2_watchlist_add_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            ticker = c1.text_input("티커", placeholder="예: NVDA, GOOG, BRK-B")
            sector = c2.text_input("섹터", placeholder="예: AI 반도체")
            c3, c4 = st.columns(2)
            classification_label = c3.selectbox("분류", ["일반 관찰", "Core", "Satellite"])
            enabled = c4.checkbox("활성화 (분석 대상에 포함)", value=True)
            note = st.text_area("메모 (선택)", max_chars=500, placeholder="관찰 이유, 관심 테마 등")
            add_submitted = st.form_submit_button("관심종목 저장", type="primary", use_container_width=True)
        if add_submitted:
            classification = {"일반 관찰": "WATCH", "Core": "CORE", "Satellite": "SATELLITE"}[classification_label]
            try:
                add_watchlist_item(client, user_id, ticker=ticker, sector=sector, classification=classification, note=note, enabled=enabled)
                st.success(f"{ticker.strip().upper()} 저장 완료")
                st.rerun()
            except WatchlistValidationError as exc:
                st.error(str(exc))
            except Exception as exc:
                if "v2_watchlist_user_ticker_unique" in str(exc) or "duplicate key" in str(exc).lower():
                    st.error("이미 등록된 티커입니다. 아래 목록에서 수정해 주세요.")
                else:
                    st.error(f"저장 실패: {type(exc).__name__}: {exc}")

    if not rows:
        st.info("관심종목이 없습니다. 위에서 첫 종목을 추가해 주세요.")
        return

    role_labels = {"WATCH": "일반 관찰", "CORE": "Core", "SATELLITE": "Satellite"}
    display_rows = [{
        "티커": r["ticker"], "섹터": r.get("sector") or "미분류",
        "분류": role_labels.get(str(r.get("classification", "WATCH")), str(r.get("classification", "WATCH"))),
        "활성": bool(r.get("enabled", True)), "메모": r.get("note") or "",
    } for r in rows]
    n_active = sum(bool(r.get("enabled", True)) for r in rows)
    a, b, c = st.columns(3)
    a.metric("전체 관심종목", len(rows))
    b.metric("분석 활성", n_active)
    c.metric("비활성", len(rows) - n_active)
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

    st.markdown("#### 종목 수정 또는 삭제")
    tickers = [str(r["ticker"]) for r in rows]
    selected_ticker = st.selectbox("수정할 티커", tickers, key="v2_watchlist_edit_ticker")
    current = next(r for r in rows if str(r["ticker"]) == selected_ticker)
    current_class = str(current.get("classification", "WATCH"))
    class_to_label = {"WATCH": "일반 관찰", "CORE": "Core", "SATELLITE": "Satellite"}
    label_to_class = {v: k for k, v in class_to_label.items()}
    with st.form(f"v2_watchlist_edit_form_{selected_ticker}"):
        e1, e2 = st.columns(2)
        edit_sector = e1.text_input("섹터", value=str(current.get("sector") or ""), key=f"v2_sector_{selected_ticker}")
        edit_class_label = e2.selectbox("분류", ["일반 관찰", "Core", "Satellite"], index=["일반 관찰", "Core", "Satellite"].index(class_to_label.get(current_class, "일반 관찰")), key=f"v2_class_{selected_ticker}")
        edit_enabled = st.checkbox("활성화 (분석 대상에 포함)", value=bool(current.get("enabled", True)), key=f"v2_enabled_{selected_ticker}")
        edit_note = st.text_area("메모", value=str(current.get("note") or ""), max_chars=500, key=f"v2_note_{selected_ticker}")
        save_edit = st.form_submit_button("변경 저장", type="primary", use_container_width=True)
    if save_edit:
        try:
            update_watchlist_item(client, user_id, selected_ticker, sector=edit_sector, classification=label_to_class[edit_class_label], note=edit_note, enabled=edit_enabled)
            st.success(f"{selected_ticker} 변경 완료")
            st.rerun()
        except Exception as exc:
            st.error(f"수정 실패: {type(exc).__name__}: {exc}")

    with st.expander("🗑️ 관심종목 삭제", expanded=False):
        confirm_delete = st.checkbox(f"{selected_ticker} 삭제를 확인합니다", key=f"v2_confirm_delete_{selected_ticker}")
        if st.button("선택 종목 삭제", disabled=not confirm_delete, key=f"v2_delete_{selected_ticker}"):
            try:
                delete_watchlist_item(client, user_id, selected_ticker)
                st.success(f"{selected_ticker} 삭제 완료")
                st.rerun()
            except Exception as exc:
                st.error(f"삭제 실패: {type(exc).__name__}: {exc}")


def _pp_constructive_context(row: Mapping[str, Any]) -> bool:
    if bool(row.get("detected_pattern_diagnostic")):
        return True
    close = _num_or_none(row.get("close"))
    sma50 = _num_or_none(row.get("sma50"))
    sma200 = _num_or_none(row.get("sma200"))
    return close is not None and sma50 is not None and sma200 is not None and close > sma50 and sma50 >= sma200


def _scan_pp_for_symbols(symbols: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]], float]:
    if not symbols:
        return [], [], [], 0.0
    scan_frame, raw_by_symbol, errors, elapsed = _run_scan(symbols, "Yahoo Finance (EOD)")
    valid_signals: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    if scan_frame.empty:
        return valid_signals, diagnostics, errors, elapsed
    from dashboard_v2.engines.pocket_pivot_engine import detect_pocket_pivot

    for _, series in scan_frame.iterrows():
        row = series.to_dict()
        symbol = str(row.get("ticker", "")).upper()
        bars = raw_by_symbol.get(symbol)
        if bars is None or bars.empty:
            diagnostics.append({"종목": symbol, "기준일": row.get("as_of_date"), "결과": "DATA INCOMPLETE"})
            continue
        try:
            pp = detect_pocket_pivot(
                bars,
                constructive_context=_pp_constructive_context(row),
                as_of_date=row.get("as_of_date"),
            )
        except Exception as exc:
            diagnostics.append({"종목": symbol, "기준일": row.get("as_of_date"), "결과": f"ERROR: {type(exc).__name__}"})
            continue
        if not pp.valid_signal:
            diagnostics.append({
                "종목": symbol,
                "기준일": row.get("as_of_date"),
                "결과": " | ".join(pp.reason_codes),
            })
            continue
        valid_signals.append({
            "ticker": symbol,
            "signal_date": (pp.as_of_date.isoformat() if pp.as_of_date else str(row.get("as_of_date", ""))[:10]),
            "signal_price": _num_or_none(row.get("close")),
            "signal_volume": pp.signal_volume,
            "max_prior_down_day_volume": pp.max_prior_down_day_volume,
            "reference_ma": pp.reference_ma,
            "pattern_name": row.get("pattern") or "BASE FORMING / NO VALID PATTERN",
            "entry_stage": row.get("stage") or "BASE",
            "candidate_pivot": _num_or_none(row.get("candidate_pivot")),
            "breakout_pivot": _num_or_none(row.get("breakout_pivot")),
            "context_valid": bool(pp.context_valid),
            "reason_codes": list(pp.reason_codes),
            "valid_signal": bool(pp.valid_signal),
        })
    return valid_signals, diagnostics, errors, elapsed


def _render_pp_journal_page(client, user_id: str, symbols: list[str]) -> None:
    from datetime import date
    from dashboard_v2.services.pp_journal_repository import (
        ENTRY_STAGES, EXIT_REASONS, PPJournalValidationError,
        add_pp_trade_add, add_pp_trade_exit, create_pp_trade,
        list_pp_signals, list_pp_trades, list_trade_adds, list_trade_exits,
        save_pp_signals,
    )
    from dashboard_v2.services.pp_journal_analytics import (
        analyze_pp_signal_outcome, summarize_trade, summarize_trades,
    )

    st.subheader("PP Journal")
    st.caption("Pocket Pivot 신호와 실제 거래를 분리해 기록합니다. 거래 입력은 수동 기록이며 주문 제출·자동 청산은 하지 않습니다.")
    with st.expander("저장·계산 기준", expanded=False):
        st.markdown(
            "- **PP Signal:** 완료 일봉 공통 엔진의 Pocket Pivot 조건을 통과한 이벤트만 저장합니다. 사용자·티커·신호일 조합으로 중복을 막고 당시 가격/거래량/피봇/Stage를 보존합니다.\n"
            "- **Trade:** 최초 진입, 추가매수, 부분청산, 전량청산은 별도 행으로 추가됩니다. 최초 Stage와 진입 시점의 초기 손절선은 나중의 Stage 변화로 덮어쓰지 않습니다.\n"
            "- **Position 상태:** OPEN은 청산 기록 없음, PARTIAL은 일부 청산 후 잔여 수량이 있음, CLOSED는 잔여 수량이 0인 경우에만 표시합니다. 예를 들어 90% 청산 후 10%가 남으면 PARTIAL입니다.\n"
            "- **손익:** 청산 손익은 진입 건을 시간순으로 적용하는 FIFO 기준 참고치입니다. 세무/브로커 명세서와 별개의 저널 분석값입니다.\n"
            "- **신호 후속 결과:** +10%/+20%, MFE/MAE, 저장 당시 Candidate Pivot의 고가 도달만 완료 일봉의 가격 움직임으로 측정합니다. 이는 공식 BREAKOUT/FAILURE 이벤트와 같지 않으며 그 이벤트 통계는 공식 날짜별 Stage 이벤트 원장과 연결된 뒤에만 표시합니다."
        )

    try:
        saved_signals = list_pp_signals(client, user_id)
        trades = list_pp_trades(client, user_id)
        trade_ids = [str(row.get("id")) for row in trades]
        all_adds = list_trade_adds(client, user_id, trade_ids)
        all_exits = list_trade_exits(client, user_id, trade_ids)
    except Exception as exc:
        st.error(f"PP Journal 테이블을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
        st.info("새 V2 Supabase 프로젝트의 SQL Editor에서 `sql/v2_pp_journal_schema.sql`을 실행했는지 확인하세요. 기존 Watchlist SQL을 다시 실행할 필요는 없습니다.")
        return

    tab_signal, tab_trade, tab_analytics = st.tabs(["PP Signals", "실제 거래 기록", "성과 분석"])

    with tab_signal:
        st.markdown("#### 활성 관심종목에서 PP 신호 탐색")
        if not symbols:
            st.info("활성 관심종목이 없습니다. Watchlist에서 종목을 활성화해 주세요. 이미 저장된 신호와 거래 기록은 아래에 계속 보존됩니다.")
        c1, c2 = st.columns([1, 3])
        if c1.button("완료 일봉 PP 재스캔", type="primary", key="v2_pp_scan"):
            if not symbols:
                st.session_state["v2_pp_scan_results"] = []
                st.session_state["v2_pp_scan_diagnostics"] = []
                st.session_state["v2_pp_scan_errors"] = []
            else:
                with st.spinner(f"{len(symbols)}개 활성 관심종목의 완료 일봉 PP 조건을 확인 중…"):
                    valid, diagnostics, errors, elapsed = _scan_pp_for_symbols(symbols)
                st.session_state["v2_pp_scan_results"] = valid
                st.session_state["v2_pp_scan_diagnostics"] = diagnostics
                st.session_state["v2_pp_scan_errors"] = errors
                st.session_state["v2_pp_scan_elapsed"] = elapsed
            st.rerun()
        c2.caption("현재 완료 일봉을 다시 계산합니다. 장중 5분봉은 신호 탐지/저장에 사용하지 않습니다.")

        scan_results = st.session_state.get("v2_pp_scan_results", [])
        if scan_results:
            scan_view = pd.DataFrame(scan_results)
            for col in ("signal_price", "candidate_pivot", "breakout_pivot"):
                if col in scan_view:
                    scan_view[col] = scan_view[col].map(lambda value: _fmt_num(value))
            scan_view = scan_view.rename(columns={
                "ticker": "종목", "signal_date": "신호일", "signal_price": "신호 가격",
                "signal_volume": "신호일 거래량", "max_prior_down_day_volume": "직전 하락일 최대 거래량",
                "reference_ma": "참조 MA", "pattern_name": "패턴", "entry_stage": "Stage",
                "candidate_pivot": "저장 당시 Candidate Pivot", "breakout_pivot": "저장 당시 Breakout Pivot",
            })
            show_cols = [col for col in ["종목", "신호일", "신호 가격", "신호일 거래량", "직전 하락일 최대 거래량", "참조 MA", "패턴", "Stage", "저장 당시 Candidate Pivot", "저장 당시 Breakout Pivot"] if col in scan_view]
            st.dataframe(scan_view[show_cols], use_container_width=True, hide_index=True)
            if st.button("유효 PP 신호 저장", type="primary", key="v2_pp_save_signals"):
                try:
                    saved = save_pp_signals(client, user_id, scan_results)
                    st.success(f"신규 저장 {saved['inserted']}건 · 중복으로 건너뜀 {saved['duplicates']}건")
                    st.rerun()
                except Exception as exc:
                    st.error(f"PP 신호 저장 실패: {type(exc).__name__}: {exc}")
        elif "v2_pp_scan_results" in st.session_state:
            st.info("현재 완료 일봉 기준으로 유효 PP 신호가 없습니다. 임의로 신호를 만들거나 이전 날짜 신호로 대체하지 않았습니다.")

        scan_errors = st.session_state.get("v2_pp_scan_errors", [])
        if scan_errors:
            with st.expander(f"스캔 오류 ({len(scan_errors)})", expanded=False):
                st.dataframe(pd.DataFrame(scan_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
        rejected = st.session_state.get("v2_pp_scan_diagnostics", [])
        if rejected:
            with st.expander(f"PP 조건 미충족/진단 ({len(rejected)}종목)", expanded=False):
                st.dataframe(pd.DataFrame(rejected), use_container_width=True, hide_index=True)

        st.markdown("#### 저장된 PP Signal")
        if saved_signals:
            signal_view = pd.DataFrame(saved_signals)
            signal_view = signal_view.rename(columns={
                "ticker": "종목", "signal_date": "신호일", "signal_price": "신호 가격",
                "signal_volume": "신호일 거래량", "max_prior_down_day_volume": "직전 하락일 최대 하락일 거래량",
                "reference_ma": "참조 MA", "pattern_name": "당시 패턴", "entry_stage": "당시 Stage",
                "candidate_pivot": "당시 Candidate Pivot", "breakout_pivot": "당시 Breakout Pivot",
            })
            cols = ["종목", "신호일", "신호 가격", "신호일 거래량", "직전 하락일 최대 하락일 거래량", "참조 MA", "당시 패턴", "당시 Stage", "당시 Candidate Pivot", "당시 Breakout Pivot"]
            signal_view = signal_view[[c for c in cols if c in signal_view.columns]].copy()
            for col in ("신호 가격", "당시 Candidate Pivot", "당시 Breakout Pivot"):
                if col in signal_view:
                    signal_view[col] = signal_view[col].map(lambda v: _fmt_num(v))
            st.dataframe(signal_view, use_container_width=True, hide_index=True)
        else:
            st.caption("아직 저장된 PP 신호가 없습니다.")

    with tab_trade:
        st.markdown("#### 최초 진입 기록")
        signal_options: dict[str, dict[str, Any] | None] = {"연결 안 함": None}
        for sig in saved_signals:
            label = f"{str(sig.get('signal_date',''))[:10]} · {sig.get('ticker')} · ${float(sig.get('signal_price') or 0):,.2f} · {str(sig.get('id',''))[:8]}"
            signal_options[label] = sig
        selected_signal_label = st.selectbox("연결할 PP Signal (선택)", list(signal_options.keys()), key="v2_pp_trade_signal_link")
        selected_signal = signal_options[selected_signal_label]
        ticker_options = list(dict.fromkeys(symbols + [str(s.get("ticker", "")) for s in saved_signals if s.get("ticker")]))
        default_ticker = str((selected_signal or {}).get("ticker") or (ticker_options[0] if ticker_options else ""))
        ticker = st.text_input("거래 티커", value=default_ticker, key=f"v2_pp_trade_ticker_{(selected_signal or {}).get('id','manual')}", help="PP Signal을 연결하면 해당 신호의 티커를 사용합니다.")
        with st.form("v2_pp_create_trade_form", clear_on_submit=False):
            a, b, c = st.columns(3)
            default_price = float((selected_signal or {}).get("signal_price") or 1.0)
            entry_price = a.number_input("최초 진입 가격", min_value=0.000001, value=max(0.000001, default_price), step=0.5, format="%.4f")
            shares = b.number_input("최초 진입 수량 (주)", min_value=0.000001, value=1.0, step=1.0, format="%.6f")
            weight = c.number_input("최초 진입 포트폴리오 비중 (%) · 선택", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.4f")
            d, e, f = st.columns(3)
            entry_date = d.date_input("최초 진입일", value=datetime.now(tz=LIVE_NY).date())
            stage_default = str((selected_signal or {}).get("entry_stage") or "BASE")
            stage_index = ENTRY_STAGES.index(stage_default) if stage_default in ENTRY_STAGES else 0
            entry_stage = e.selectbox("최초 진입 Stage (고정 기록)", ENTRY_STAGES, index=stage_index)
            initial_stop = f.number_input("최초 진입 당시 초기 손절선 · 선택", min_value=0.0, value=0.0, step=0.5, format="%.4f")
            notes = st.text_area("거래 메모", max_chars=1500, placeholder="진입 이유, 셋업, 계획 등")
            create_submitted = st.form_submit_button("최초 진입 기록 저장", type="primary", use_container_width=True)
        if create_submitted:
            trade_ticker = str((selected_signal or {}).get("ticker") or ticker).strip()
            try:
                created = create_pp_trade(
                    client, user_id, ticker=trade_ticker,
                    pp_signal_id=(selected_signal or {}).get("id"),
                    initial_entry_date=entry_date, initial_entry_price=entry_price,
                    initial_shares=shares, initial_weight_pct=(weight if weight > 0 else None),
                    initial_entry_stage=entry_stage,
                    initial_stop_price=(initial_stop if initial_stop > 0 else None), notes=notes,
                )
                st.success(f"{created['ticker']} 최초 진입을 기록했습니다. ID: {str(created['id'])[:8]}")
                st.rerun()
            except Exception as exc:
                st.error(f"거래 기록 저장 실패: {type(exc).__name__}: {exc}")

        trade_summaries = []
        for trade in trades:
            tid = str(trade.get("id"))
            trade_summaries.append(summarize_trade(
                trade,
                [row for row in all_adds if str(row.get("trade_id")) == tid],
                [row for row in all_exits if str(row.get("trade_id")) == tid],
            ))
        if trade_summaries:
            st.markdown("#### 포지션 현황")
            table = pd.DataFrame(trade_summaries)
            label_status = {"OPEN": "OPEN · 보유", "PARTIAL": "PARTIAL · 부분청산", "CLOSED": "CLOSED · 전량청산"}
            table["status"] = table["status"].map(label_status)
            table = table.rename(columns={
                "ticker": "종목", "status": "상태", "initial_entry_date": "최초 진입일",
                "initial_entry_stage": "최초 진입 Stage", "initial_entry_price": "최초 진입가",
                "total_entered_shares": "총 매수 수량", "total_exited_shares": "총 청산 수량",
                "remaining_shares": "잔여 수량", "average_remaining_cost": "잔여 수량 가중평균단가",
                "realized_pnl": "실현손익 ($)", "realized_return_pct": "실현수익률 (%)",
                "r_multiple": "R 배수", "adds_count": "추가매수 횟수", "exits_count": "청산 횟수",
            })
            show_cols = ["종목", "상태", "최초 진입일", "최초 진입 Stage", "최초 진입가", "총 매수 수량", "총 청산 수량", "잔여 수량", "잔여 수량 가중평균단가", "실현손익 ($)", "실현수익률 (%)", "R 배수", "추가매수 횟수", "청산 횟수"]
            st.dataframe(table[[col for col in show_cols if col in table]], use_container_width=True, hide_index=True)

            st.markdown("#### 선택 포지션 관리 — 추가매수 / 청산 기록")
            trade_labels = {}
            trade_by_label = {}
            summary_by_id = {str(row["trade_id"]): row for row in trade_summaries}
            for trade in trades:
                summ = summary_by_id[str(trade["id"])]
                label = f"{trade['ticker']} · {summ['status']} · 최초 {summ['initial_entry_date']} · {str(trade['id'])[:8]}"
                trade_labels[label] = str(trade["id"])
                trade_by_label[label] = trade
            selected_trade_label = st.selectbox("관리할 포지션", list(trade_labels.keys()), key="v2_pp_manage_trade")
            selected_trade = trade_by_label[selected_trade_label]
            selected_id = str(selected_trade["id"])
            selected_summary = summary_by_id[selected_id]
            st.caption(
                f"{selected_trade['ticker']} · 상태 {selected_summary['status']} · "
                f"잔여 {selected_summary['remaining_shares']:.6g}주 · "
                f"가중평균 잔여 단가 {_fmt_num(selected_summary['average_remaining_cost'])}"
            )
            add_col, exit_col = st.columns(2)
            with add_col:
                st.markdown("**추가매수 기록**")
                with st.form(f"v2_pp_add_form_{selected_id}"):
                    add_date = st.date_input("추가매수일", value=datetime.now(tz=LIVE_NY).date(), key=f"v2_pp_add_date_{selected_id}")
                    add_price = st.number_input("추가매수 가격", min_value=0.000001, value=float(selected_summary["average_remaining_cost"] or selected_trade["initial_entry_price"]), step=0.5, format="%.4f", key=f"v2_pp_add_price_{selected_id}")
                    add_shares = st.number_input("추가매수 수량", min_value=0.000001, value=1.0, step=1.0, format="%.6f", key=f"v2_pp_add_shares_{selected_id}")
                    add_weight = st.number_input("추가매수 비중 (%) · 선택", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.4f", key=f"v2_pp_add_weight_{selected_id}")
                    add_stage = st.selectbox("추가매수 Stage", ENTRY_STAGES, key=f"v2_pp_add_stage_{selected_id}")
                    add_stop = st.number_input("추가매수 당시 초기 손절선 · 선택", min_value=0.0, value=0.0, step=0.5, format="%.4f", key=f"v2_pp_add_stop_{selected_id}")
                    add_note = st.text_input("추가매수 메모", max_chars=1000, key=f"v2_pp_add_note_{selected_id}")
                    add_submit = st.form_submit_button("추가매수 기록 저장", use_container_width=True)
                if add_submit:
                    try:
                        add_pp_trade_add(
                            client, user_id, selected_trade, entry_date=add_date,
                            entry_price=add_price, shares=add_shares,
                            portfolio_weight_pct=(add_weight if add_weight > 0 else None),
                            entry_stage=add_stage, initial_stop_price=(add_stop if add_stop > 0 else None), note=add_note,
                        )
                        st.success("추가매수를 기록했습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"추가매수 저장 실패: {type(exc).__name__}: {exc}")
            with exit_col:
                st.markdown("**부분청산 / 전량청산 기록**")
                remaining_now = float(selected_summary["remaining_shares"])
                with st.form(f"v2_pp_exit_form_{selected_id}"):
                    exit_date = st.date_input("청산일", value=datetime.now(tz=LIVE_NY).date(), key=f"v2_pp_exit_date_{selected_id}")
                    exit_price = st.number_input("청산 가격", min_value=0.000001, value=float(selected_summary["average_remaining_cost"] or selected_trade["initial_entry_price"]), step=0.5, format="%.4f", key=f"v2_pp_exit_price_{selected_id}")
                    exit_shares = st.number_input("청산 수량", min_value=0.000001, max_value=max(0.000001, remaining_now), value=max(0.000001, remaining_now), step=1.0, format="%.6f", key=f"v2_pp_exit_shares_{selected_id}", disabled=remaining_now <= 0)
                    reason_labels = {
                        "돌파 실패": "BREAKOUT_FAILURE", "초기 손절": "INITIAL_STOP",
                        "추적 손절": "TRAILING_STOP", "목표가/익절": "TAKE_PROFIT",
                        "수동 청산": "MANUAL", "기타": "OTHER",
                    }
                    reason_label = st.selectbox("청산 사유", list(reason_labels.keys()), key=f"v2_pp_exit_reason_{selected_id}")
                    exit_note = st.text_input("청산 메모", max_chars=1000, key=f"v2_pp_exit_note_{selected_id}")
                    exit_submit = st.form_submit_button("청산 기록 저장", use_container_width=True, disabled=remaining_now <= 0)
                if exit_submit:
                    try:
                        add_pp_trade_exit(
                            client, user_id, selected_trade,
                            exits=[row for row in all_exits if str(row.get("trade_id")) == selected_id],
                            adds=[row for row in all_adds if str(row.get("trade_id")) == selected_id],
                            exit_date=exit_date, exit_price=exit_price, shares=exit_shares,
                            reason_code=reason_labels[reason_label], note=exit_note,
                        )
                        st.success("청산 내역을 추가했습니다. 잔여 수량이 남아 있으면 포지션은 계속 열려 있습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"청산 기록 저장 실패: {type(exc).__name__}: {exc}")

            with st.expander("선택 포지션의 원본 진입/청산 내역"):
                selected_adds = [row for row in all_adds if str(row.get("trade_id")) == selected_id]
                selected_exits = [row for row in all_exits if str(row.get("trade_id")) == selected_id]
                fill_rows = [{
                    "유형": "최초 진입", "날짜": selected_trade.get("initial_entry_date"),
                    "가격": selected_trade.get("initial_entry_price"), "수량": selected_trade.get("initial_shares"),
                    "Stage/사유": selected_trade.get("initial_entry_stage"), "초기 손절선": selected_trade.get("initial_stop_price"),
                }]
                fill_rows += [{"유형": "추가매수", "날짜": row.get("entry_date"), "가격": row.get("entry_price"), "수량": row.get("shares"), "Stage/사유": row.get("entry_stage"), "초기 손절선": row.get("initial_stop_price")} for row in selected_adds]
                fill_rows += [{"유형": "청산", "날짜": row.get("exit_date"), "가격": row.get("exit_price"), "수량": row.get("shares"), "Stage/사유": row.get("reason_code"), "초기 손절선": None} for row in selected_exits]
                st.dataframe(pd.DataFrame(fill_rows), use_container_width=True, hide_index=True)
        else:
            st.info("아직 거래 기록이 없습니다. 위에서 최초 진입부터 기록해 주세요. PP 신호를 연결하지 않고 수동 거래를 기록할 수도 있습니다.")

    with tab_analytics:
        trade_summaries = []
        for trade in trades:
            tid = str(trade.get("id"))
            trade_summaries.append(summarize_trade(
                trade,
                [row for row in all_adds if str(row.get("trade_id")) == tid],
                [row for row in all_exits if str(row.get("trade_id")) == tid],
            ))
        stats = summarize_trades(trade_summaries)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("전체 거래", stats["trade_count"])
        m2.metric("OPEN / PARTIAL", f"{stats['open_count']} / {stats['partial_count']}")
        m3.metric("CLOSED", stats["closed_count"])
        m4.metric("승률 · 종료 거래", "—" if stats["win_rate_pct"] is None else f"{stats['win_rate_pct']:.1f}%")
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("누적 실현손익 ($)", f"${stats['total_realized_pnl']:,.2f}")
        m6.metric("평균 승리 수익률", "—" if stats["avg_win_pct"] is None else f"{stats['avg_win_pct']:+.2f}%")
        m7.metric("평균 손실 수익률", "—" if stats["avg_loss_pct"] is None else f"{stats['avg_loss_pct']:+.2f}%")
        m8.metric("Profit Factor", "—" if stats["profit_factor"] is None else f"{stats['profit_factor']:.2f}")
        m9, m10, m11 = st.columns(3)
        m9.metric("기대값 · 종료 거래 평균", "—" if stats["expectancy_pct"] is None else f"{stats['expectancy_pct']:+.2f}%")
        m10.metric("평균 R 배수", "—" if stats["avg_r_multiple"] is None else f"{stats['avg_r_multiple']:+.2f}R")
        m11.metric("PP Signal 누적", len(saved_signals))
        st.caption("승률·평균 승패·Profit Factor·기대값은 CLOSED 거래만 사용합니다. OPEN/PARTIAL은 성과 분모에서 제외합니다. R 배수는 해당 진입 건의 초기 손절선이 기록된 거래에서만 계산됩니다.")

        st.markdown("#### 저장 PP 신호의 후속 가격 움직임")
        if not saved_signals:
            st.caption("신호가 저장되면 완료 일봉의 이후 High/Low를 기준으로 MFE/MAE와 +10%/+20% 도달을 계산할 수 있습니다.")
        else:
            if st.button("후속 EOD 결과 재계산", key="v2_pp_revalue_outcomes"):
                outcomes = []
                errors = []
                unique_symbols = list(dict.fromkeys(str(sig.get("ticker", "")).upper() for sig in saved_signals if sig.get("ticker")))
                bars_cache: dict[str, pd.DataFrame] = {}
                for symbol in unique_symbols:
                    try:
                        bars_cache[symbol] = _load_yfinance_bars(symbol)
                    except Exception as exc:
                        errors.append((symbol, f"{type(exc).__name__}: {exc}"))
                for sig in saved_signals:
                    outcome = analyze_pp_signal_outcome(sig, bars_cache.get(str(sig.get("ticker", "")).upper(), pd.DataFrame()))
                    outcome["signal_price"] = sig.get("signal_price")
                    outcome["candidate_pivot"] = sig.get("candidate_pivot")
                    outcomes.append(outcome)
                st.session_state["v2_pp_signal_outcomes"] = outcomes
                st.session_state["v2_pp_signal_outcome_errors"] = errors
                st.rerun()

            outcomes = st.session_state.get("v2_pp_signal_outcomes", [])
            if outcomes:
                outcome_frame = pd.DataFrame(outcomes)
                outcome_frame = outcome_frame.rename(columns={
                    "ticker": "종목", "signal_date": "신호일", "signal_price": "신호 가격",
                    "candidate_pivot": "저장 당시 Candidate Pivot", "candidate_pivot_touched": "피봇 고가 도달",
                    "hit_plus_10_pct": "+10% 도달", "hit_plus_20_pct": "+20% 도달",
                    "mfe_pct": "MFE (%)", "mae_pct": "MAE (%)", "observed_bars": "후속 완료 일봉 수",
                    "outcome_status": "결과 상태",
                })
                wanted = ["종목", "신호일", "신호 가격", "저장 당시 Candidate Pivot", "피봇 고가 도달", "+10% 도달", "+20% 도달", "MFE (%)", "MAE (%)", "후속 완료 일봉 수", "결과 상태"]
                outcome_frame = outcome_frame[[col for col in wanted if col in outcome_frame.columns]]
                for col in ("신호 가격", "저장 당시 Candidate Pivot", "MFE (%)", "MAE (%)"):
                    if col in outcome_frame:
                        outcome_frame[col] = outcome_frame[col].map(lambda v: _fmt_num(v) if v is not None and pd.notna(v) else "—")
                st.dataframe(outcome_frame, use_container_width=True, hide_index=True)
                observed = [row for row in outcomes if row.get("outcome_status") == "OBSERVED_PRICE_EXCURSION"]
                if observed:
                    for label, key in (("저장 당시 피봇 고가 도달률", "candidate_pivot_touched"), ("+10% 도달률", "hit_plus_10_pct"), ("+20% 도달률", "hit_plus_20_pct")):
                        values = [row.get(key) for row in observed if row.get(key) is not None]
                        rate = sum(bool(x) for x in values) / len(values) * 100 if values else None
                        st.metric(label, "—" if rate is None else f"{rate:.1f}%", help=f"결과가 관찰된 {len(values)}개 신호 기준. 저장 당시 후보 피봇이 없거나 후속 일봉이 없는 신호는 해당 비율의 분모에서 제외됩니다.")
                st.caption("MFE/MAE는 신호일 다음 완료 일봉부터 계산합니다. 재베이스나 패턴 변경 이후에도 저장된 피봇선은 스냅샷으로 유지되며, 고가 도달을 공식 BREAKOUT으로 간주하지 않습니다.")
            outcome_errors = st.session_state.get("v2_pp_signal_outcome_errors", [])
            if outcome_errors:
                with st.expander(f"후속 데이터 조회 오류 ({len(outcome_errors)})", expanded=False):
                    st.dataframe(pd.DataFrame(outcome_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)




def _render_quality_recovery_page(symbols: list[str], source: str) -> None:
    """Render the legacy V30 Quality Recovery Radar on the V2 watchlist/EOD layer."""
    st.subheader("🧬 Quality Recovery Radar")
    st.caption("Watchlist가 Single Source of Truth입니다. 활성 관심종목만 평가하며, 이 화면은 공식 ENTRY STAGE나 주문을 생성·변경하지 않습니다.")
    st.info("진행 단계 참고: **접근중 → 테스트 → 지지 반등** 또는 **접근중 → 이탈 → 무너짐 → 회복 시도 → 회복 확정 → 리테스트 → 리테스트 확인 → 추세 회복**")
    st.caption("120MA = 중기 추세 회복 경로 · 200MA = 장기 추세 회복 경로. 이벤트 판정 방식은 동일하며, 200MA 이벤트를 더 중요하게 봅니다.")
    st.caption("RS Engine V30.5 호환 · QQQ 대비 21/63/126/252 거래일 상대강도 · Momentum Monitor 및 다른 엔진의 공식 Stage와 분리")

    if source == "연결 안 함 (기본)":
        st.info("왼쪽에서 Yahoo Finance (EOD)를 선택하면 기존 Quality Recovery 방식으로 분석합니다. 임의의 시세나 펀더멘털 판정은 표시하지 않습니다.")
        return
    if not symbols:
        st.warning("활성 관심종목이 없습니다. Watchlist에서 종목을 추가하거나 활성화하세요.")
        return

    result_rows: list[dict[str, Any]] = []
    raw_by_symbol: dict[str, pd.DataFrame] = {}
    adjusted_by_symbol: dict[str, pd.DataFrame] = {}
    errors: list[tuple[str, str]] = []
    benchmark_bars: pd.DataFrame | None = None
    benchmark_adjusted: pd.DataFrame | None = None

    if source == "Yahoo Finance (EOD)":
        try:
            benchmark_bars = _load_bars("QQQ", source)
        except Exception as exc:
            errors.append(("QQQ benchmark", f"{type(exc).__name__}: {exc}"))
        try:
            benchmark_adjusted = _load_adjusted_close_history("QQQ")
        except Exception as exc:
            errors.append(("QQQ adjusted-close RS", f"{type(exc).__name__}: {exc}"))
    elif source == "Bundled regression data (historical)":
        st.warning("고정된 과거 가격 데이터에는 현재 연간 재무제표와 현재 RS를 결합하지 않습니다. 펀더멘털 및 RS는 미평가로 표시됩니다.")

    with st.spinner(f"{len(symbols)}개 관심종목의 Quality Recovery를 분석 중…"):
        for symbol in symbols:
            try:
                bars = _load_bars(symbol, source)
                raw_by_symbol[symbol] = bars
                fundamental = fundamentals_from_income_statement(None)
                adjusted = pd.DataFrame(columns=["Date", "Close"])
                if source == "Yahoo Finance (EOD)":
                    try:
                        fundamental = _load_quality_fundamentals(symbol)
                        if fundamental.get("fundamental_error"):
                            errors.append((f"{symbol} fundamentals", str(fundamental["fundamental_error"])))
                    except Exception as exc:
                        errors.append((f"{symbol} fundamentals", f"{type(exc).__name__}: {exc}"))
                    try:
                        adjusted = _load_adjusted_close_history(symbol)
                    except Exception as exc:
                        errors.append((f"{symbol} adjusted-close RS", f"{type(exc).__name__}: {exc}"))
                adjusted_by_symbol[symbol] = adjusted
                row = analyze_legacy_quality_recovery(
                    symbol,
                    bars,
                    fundamentals=fundamental,
                    adjusted_close=adjusted,
                    benchmark_adjusted_close=benchmark_adjusted,
                    benchmark_bars=benchmark_bars,
                )
                result_rows.append(row)
            except Exception as exc:
                errors.append((symbol, f"Technical {type(exc).__name__}: {exc}"))

    if not result_rows:
        st.error("Quality Recovery 데이터를 계산할 수 없습니다. 데이터 공급원과 일봉 이력을 확인하세요.")
        if errors:
            with st.expander(f"데이터/분석 오류 ({len(errors)})", expanded=True):
                st.dataframe(pd.DataFrame(errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
        return

    result = pd.DataFrame(result_rows)
    display_table = quality_recovery_table(result_rows)
    st.dataframe(display_table, use_container_width=True, hide_index=True)
    st.caption("Quality Gate: EPS Growth > 0 + Sales Growth > 0 + 자동 감지된 Fundamental Damage 없음. Decline Reason은 참고용이며 매수 게이트가 아닙니다. Valuation / Analyst Target / Earnings Revision / Market Regime은 사용하지 않습니다.")
    st.caption("기존 V50 Quality Recovery와 결과 호환을 위해 'EPS Growth'는 기존 코드와 동일하게 연간 Net Income 증가율을 사용합니다(실제 주당 EPS 증가율은 아님). Fundamental 데이터가 없으면 PASS하지 않고 CHECK로 표시합니다.")

    st.markdown("### 🔎 선택 종목 상세")
    selected = st.selectbox("종목", result["ticker"].tolist(), key="quality_recovery_detail_symbol_v2")
    row = next(item for item in result_rows if item["ticker"] == selected)
    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("현재가", f"${row['price']:,.2f}")
    metric2.metric("EPS Growth", f"{row['eps_growth']:+.1f}%" if pd.notna(row["eps_growth"]) else "—")
    metric3.metric("Sales Growth", f"{row['sales_growth']:+.1f}%" if pd.notna(row["sales_growth"]) else "—")
    metric4.metric("RS", f"{row['rs']:.0f}" if pd.notna(row["rs"]) else "—", f"{row['rs_delta']:+.1f}" if pd.notna(row["rs_delta"]) else None)
    st.markdown(f"**현재 상태: {row['status']}** · Quality **{'PASS' if row['quality_gate'] else 'CHECK'}**")
    left, right = st.columns(2)
    for col, label, ma_key, event_key in (
        (left, "120MA", "ma120", "e120"),
        (right, "200MA", "ma200", "e200"),
    ):
        event = row[event_key]
        distance = event.get("distance")
        delta = event.get("delta")
        with col:
            st.markdown(f"**{label}**")
            st.write(
                f"현재 ${row[ma_key]:,.2f} · 이격 {distance:+.1f}% · "
                f"이격 변화 {delta:+.1f}%p · **{event['state']}**"
                if pd.notna(distance) and pd.notna(delta)
                else f"현재 ${row[ma_key]:,.2f} · 데이터 부족"
            )
    st.write(
        f"Correction {row['correction']:+.1f}% · Decline Reason(참고) {row['decline_reason']} · "
        f"Fundamental {row['fundamental_direction']} · 훼손 판정 {row['fundamental_damage_status']}"
    )

    bars = raw_by_symbol.get(selected)
    if bars is not None and not bars.empty:
        chart_bars = bars.copy()
        chart_bars["Date"] = pd.to_datetime(chart_bars["Date"], errors="coerce")
        chart_bars["Close"] = pd.to_numeric(chart_bars["Close"], errors="coerce")
        chart_bars = chart_bars.dropna(subset=["Date", "Close"]).sort_values("Date")
        chart = pd.DataFrame({"Close": chart_bars["Close"].to_numpy()}, index=chart_bars["Date"])
        for window in (20, 50, 120, 200):
            chart[f"SMA{window}"] = chart_bars["Close"].rolling(window, min_periods=window).mean().to_numpy()
        st.line_chart(chart.tail(260), height=340)
        st.caption("차트의 Close 및 20/50/120/200일선은 완료 일봉의 기술적 가격을 사용합니다. 조정 종가는 RS 계산에만 별도로 사용합니다.")

    if errors:
        with st.expander(f"데이터/분석 오류 ({len(errors)})", expanded=False):
            st.dataframe(pd.DataFrame(errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
            st.caption("가격 분석에 실패한 종목은 제외합니다. Yahoo 재무제표 또는 RS 조회 실패는 기술적 결과를 지우지 않으며 Quality는 CHECK로 유지합니다.")


supabase_client, auth_user = _require_v2_login()
user_id = str(auth_user.id)
try:
    watchlist_rows = list_watchlist(supabase_client, user_id)
except Exception as exc:
    st.title("Portfolio Dashboard V2")
    st.error(f"V2 관심종목 테이블을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
    st.info("새 Supabase 프로젝트에서 sql/v2_watchlist_schema.sql을 실행했고, 로그인 사용자가 생성되어 있는지 확인하세요.")
    st.stop()
symbols = active_tickers(watchlist_rows)

st.sidebar.title("Portfolio Dashboard V2")
st.sidebar.caption("V2 clean data · personal watchlist · shared EOD + isolated LIVE layer")
page = st.sidebar.radio("화면", PAGES, index=0)
st.sidebar.caption(f"관심종목 {len(watchlist_rows)}개 · 분석 활성 {len(symbols)}개")
st.sidebar.caption(st.session_state.get("_v2_supabase_user_email", getattr(auth_user, "email", "")))
if st.sidebar.button("로그아웃", key="v2_logout"):
    try:
        supabase_client.auth.sign_out()
    except Exception:
        pass
    _clear_auth_session()
    st.rerun()
st.sidebar.divider()
if page == "Live Monitor":
    source = "Yahoo Finance (EOD)"
    st.sidebar.markdown("**Live Monitor 데이터**")
    st.sidebar.info("별도 5분봉 잠정 공급원 + 완료 일봉 기준선. EOD 패턴/피봇 엔진과 장중 계산을 분리합니다.")
else:
    st.sidebar.markdown("**시장 데이터**")
    source = st.sidebar.selectbox(
        "데이터 공급원",
        ["연결 안 함 (기본)", "Yahoo Finance (EOD)", "Bundled regression data (historical)"],
        index=0,
        help="미국 거래소의 가장 최근 완료 일봉만 사용합니다. 패턴/피봇은 auto_adjust=False OHLC, 다기간 수익률과 RS는 split·배당 조정 Adj Close 기준입니다. 완료 일봉이 없거나 데이터가 불완전하면 해당 종목을 표시하지 않습니다.",
    )
    if source == "Yahoo Finance (EOD)":
        st.sidebar.info("EOD 정책 확정: auto_adjust=False OHLC로 패턴/피봇, Adj Close로 다기간 수익률·RS, RVOL은 당일 완료 거래량 ÷ 직전 20개 완료 일봉 평균. 미국 동부시간 16:15 전에는 당일 봉을 제외합니다.")
    elif source == "Bundled regression data (historical)":
        st.sidebar.info("고정된 과거 검증 데이터입니다. 현재 시세가 아닙니다.")

st.title("Portfolio Dashboard V2")
st.caption("기준 명세: Functional Spec v0.12 · 기존 앱/DB와 분리 · EOD/LIVE 읽기 전용 분석 + 명시적 PP Journal 기록")

if page == "Watchlist":
    try:
        _render_watchlist_page(supabase_client, user_id, watchlist_rows)
    except Exception as exc:
        st.error(f"관심종목 화면 오류: {type(exc).__name__}: {exc}")

elif page == "Live Monitor":
    _render_live_monitor_page(symbols)

elif page == "Quality Recovery":
    _render_quality_recovery_page(symbols, source)

elif page == "PP Journal":
    _render_pp_journal_page(supabase_client, user_id, symbols)

elif page in {"Momentum Monitor", "Pattern Scanner"}:
    if source == "연결 안 함 (기본)":
        st.subheader(page)
        st.info("왼쪽에서 데이터 공급원을 선택하면 공통 재베이스·패턴·피봇 엔진이 종목을 분석합니다. 기본 상태에서는 임의의 시세나 신호를 표시하지 않습니다.")
        st.markdown("**이 화면에서 사용하는 공통 엔진**")
        st.write("완료 일봉 → 날짜순 재베이스 상태 → 단일 주 패턴/후보 피봇 → 역사적 Breakout Pivot → EOD Stage → 거리/RVOL/모멘텀")
        st.caption("Yahoo Finance EOD 정책: 완료된 미국 일봉만 사용합니다. 장중 가격/누적 거래량은 별도 Live Monitor에서 분리해 확인합니다.")
    elif not symbols:
        st.warning("활성 관심종목이 없습니다. 화면 왼쪽의 Watchlist에서 종목을 추가하거나 비활성화 여부를 확인하세요.")
    else:
        with st.spinner(f"{len(symbols)}개 종목의 일봉과 공통 엔진 결과를 계산 중…"):
            result, raw, errors, elapsed = _run_scan(symbols, source)
        if result.empty:
            st.error("분석할 수 있는 종목이 없습니다. 데이터 공급원과 심볼을 확인하세요.")
        else:
            latest_date = result["as_of_date"].max()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("분석 종목", len(result))
            k2.metric("최신 기준일", latest_date)
            k3.metric("재베이스 진행", int(result["rebase_active"].sum()))
            k4.metric("계산 시간", f"{elapsed:.1f}s")
            if source == "Yahoo Finance (EOD)":
                st.warning("Yahoo Finance는 외부 공급원이며 과거 데이터가 정정될 수 있습니다. 이 화면은 읽기 전용 분석이며 자동 주문을 제출하지 않습니다.")
            if result["replay_provenance"].eq("ROLLING_300_BAR_REPLAY").any():
                st.warning("Yahoo EOD는 새 auto_adjust=False OHLC 기준을 사용하므로, 과거 auto_adjust=True 스냅샷과 혼합하지 않습니다. 과거 Breakout Pivot은 현재 OHLC에서 패턴과 실제 종가 돌파를 재검증한 경우에만 복원됩니다.")
            if result["validation_status"].eq("OBSERVED_NOT_VALIDATED").any():
                st.caption("엔진 출력은 관측 결과이며, 별도 라벨링된 정답 차트와 대조되지 않은 항목은 검증 완료로 간주하지 않습니다.")

            if page == "Momentum Monitor":
                st.subheader("Momentum Monitor")
                st.caption("패턴·Candidate Pivot·Breakout Pivot·20D Reference·Stage는 동일한 날짜순 엔진 결과에서 가져옵니다.")
                _render_scan_table(result)
            else:
                st.subheader("Pattern Scanner")
                st.caption("종목별 주 패턴과 활성 후보 피봇을 스캔합니다. 진단용 보조 탐지는 활성 피봇을 덮어쓰지 않습니다.")
                p1, p2 = st.columns(2)
                patterns = ["전체", *sorted(result["pattern"].dropna().astype(str).unique())]
                stages = ["전체", *sorted(result["stage"].dropna().astype(str).unique())]
                pattern_filter = p1.selectbox("주 패턴", patterns)
                stage_filter = p2.selectbox("Stage", stages)
                filtered = result.copy()
                if pattern_filter != "전체":
                    filtered = filtered[filtered["pattern"].astype(str) == pattern_filter]
                if stage_filter != "전체":
                    filtered = filtered[filtered["stage"].astype(str) == stage_filter]
                _render_scan_table(filtered, pattern_mode=True)
                st.caption(f"필터 결과 {len(filtered)} / {len(result)}개 종목")
            _render_symbol_detail(result, raw)

        if errors:
            with st.expander(f"데이터/분석 오류 ({len(errors)})", expanded=bool(errors)):
                st.dataframe(pd.DataFrame(errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)

elif page == "App Monitor":
    st.subheader("App Monitor — V2 구현 상태")
    status_rows = [
        ("공통 OHLCV 검증", "구현·테스트"),
        ("날짜순 재베이스/패턴/피봇 재생", "구현·테스트"),
        ("Momentum Monitor 공통 엔진 연결", "구현"),
        ("Pattern Scanner 공통 엔진 연결", "구현"),
        ("Live Monitor 장중 5분봉 + freshness guard", "구현·테스트 · Yahoo 잠정 공급원"),
        ("Yahoo Finance 공급원", "EOD 정책 적용 · 장중 데이터는 별도 잠정 공급원"),
        ("7개 회귀 종목 과거 스냅샷", "포함·정답 라벨은 미검증"),
        ("V2 전용 Supabase Watchlist + RLS", "연결됨 · 사용자별 RLS"),
        ("PP Signal / Trade / Add / Exit Journal", "V2 전용 append-only 테이블 · 수동 기록"),
        ("Quality Recovery Radar · legacy V30 rules", "V2 Watchlist + Yahoo annual fundamentals + 120/200MA recovery + V30.5 RS"),
        ("나머지 화면 전체 기능 이관", "후속 구현"),
        ("주문 제출/자동 매매", "미구현·비활성"),
    ]
    st.dataframe(pd.DataFrame(status_rows, columns=["기능", "상태"]), use_container_width=True, hide_index=True)
    st.info("관심종목과 PP Journal만 신규 V2 Supabase에 저장합니다. PP Signal과 실제 체결은 별도 테이블에 기록되며, 주문 제출/자동 매매는 구현하지 않았습니다.")

elif page == "Overview":
    st.subheader("Overview")
    st.write("V2는 새로 입력한 관심종목으로 시작합니다. 실제 보유 포지션과 거래 기록은 아직 별도 구현 대상입니다.")
    st.metric("핵심 화면 연결", "Momentum + Live + Pattern")
    st.caption("핵심 우선순위는 Momentum Monitor → Live Monitor → PP Journal입니다. Overview/Holdings 등은 후순위입니다.")
else:
    st.subheader(page)
    st.info("이 화면은 메뉴를 유지하고 있습니다. 기능 명세서에 따라 후속 단계에서 실제 데이터/기능을 연결합니다. 현재 화면은 아직 기능 이관이 완료되지 않았습니다.")

st.divider()
st.caption("운영 안전장치: 새 V2 전용 DB만 사용 · RLS 사용자별 분리 · 완료 일봉 EOD 분석과 장중 LIVE 분리 · 오래된 데이터는 신호 비활성 · 주문 제출 없음 · 기존 DB 데이터 미사용 · Yahoo 원천 데이터 정정 가능")
