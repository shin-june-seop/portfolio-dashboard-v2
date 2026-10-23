"""Portfolio Dashboard V2 — shared-engine UI milestone.

This app is intentionally read-only. Momentum Monitor and Pattern Scanner consume
one common analysis service; neither screen computes pattern/pivot/stage locally.
"""
from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from dashboard_v2.services.screening_service import analyze_symbol, load_seed_snapshots
from dashboard_v2.services.live_monitor_service import (
    NY as LIVE_NY,
    YFinanceIntradayProvider,
    assess_live_data_status,
    classify_live_state,
    crossed_pivot_this_session,
    live_session_metrics,
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
st.caption("기준 명세: Functional Spec v0.12 · 기존 앱/DB와 분리 · 읽기 전용 분석")

if page == "Watchlist":
    try:
        _render_watchlist_page(supabase_client, user_id, watchlist_rows)
    except Exception as exc:
        st.error(f"관심종목 화면 오류: {type(exc).__name__}: {exc}")

elif page == "Live Monitor":
    _render_live_monitor_page(symbols)

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
        ("나머지 화면 전체 기능 이관", "후속 구현"),
        ("주문 제출/자동 매매", "미구현·비활성"),
    ]
    st.dataframe(pd.DataFrame(status_rows, columns=["기능", "상태"]), use_container_width=True, hide_index=True)
    st.info("V2 전용 관심종목 CRUD만 신규 Supabase에 저장합니다. 기존 데이터는 가져오지 않으며, 보유종목·PP Journal 쓰기와 주문 제출은 아직 구현하지 않았습니다.")

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
