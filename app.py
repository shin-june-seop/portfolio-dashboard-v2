"""Portfolio Dashboard V2 — shared-engine UI milestone.

This app is intentionally read-only. Momentum Monitor and Pattern Scanner consume
one common analysis service; neither screen computes pattern/pivot/stage locally.
"""
from __future__ import annotations

from pathlib import Path
import time

import pandas as pd
import streamlit as st

from dashboard_v2.services.screening_service import analyze_symbol, load_seed_snapshots

ROOT = Path(__file__).resolve().parent
DEFAULT_SYMBOLS = "AMAT, MU, SOXX, META, GOOG, STX, LRCX"
PAGES = [
    "Overview", "Holdings", "Market Regime", "Momentum Monitor", "App Monitor",
    "Quality Recovery", "Pattern Scanner", "ADD Monitor", "PP Journal",
    "Performance", "Allocation", "Chart", "Leaders",
]

st.set_page_config(page_title="Portfolio Dashboard V2", page_icon="📊", layout="wide")


@st.cache_data(ttl=900, show_spinner=False)
def _load_yfinance_bars(symbol: str) -> pd.DataFrame:
    from dashboard_v2.services.yfinance_provider import YFinanceDailyProvider
    return YFinanceDailyProvider(history_days=5 * 365).daily_bars(symbol)


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


st.sidebar.title("Portfolio Dashboard V2")
st.sidebar.caption("Cloud-ready · shared pattern/pivot/stage engine")
page = st.sidebar.radio("화면", PAGES, index=3)
st.sidebar.divider()
st.sidebar.markdown("**시장 데이터**")
source = st.sidebar.selectbox(
    "데이터 공급원",
    ["연결 안 함 (기본)", "Yahoo Finance (EOD)", "Bundled regression data (historical)"],
    index=0,
    help="미국 거래소의 가장 최근 완료 일봉만 사용합니다. 패턴/피봇은 auto_adjust=False OHLC, 다기간 수익률과 RS는 split·배당 조정 Adj Close 기준입니다. 완료 일봉이 없거나 데이터가 불완전하면 해당 종목을 표시하지 않습니다.",
)
symbol_text = st.sidebar.text_area("관심종목", value=DEFAULT_SYMBOLS, height=110, help="쉼표 또는 줄바꿈으로 구분")
symbols = list(dict.fromkeys(x.strip().upper() for x in symbol_text.replace("\n", ",").split(",") if x.strip()))
if source == "Yahoo Finance (EOD)":
    st.sidebar.info("EOD 정책 확정: auto_adjust=False OHLC로 패턴/피봇, Adj Close로 다기간 수익률·RS, RVOL은 당일 완료 거래량 ÷ 직전 20개 완료 일봉 평균. 미국 동부시간 16:15 전에는 당일 봉을 제외합니다.")
elif source == "Bundled regression data (historical)":
    st.sidebar.info("고정된 과거 검증 데이터입니다. 현재 시세가 아닙니다.")

st.title("Portfolio Dashboard V2")
st.caption("기준 명세: Functional Spec v0.12 · 기존 앱/DB와 분리 · 읽기 전용 분석")

if page in {"Momentum Monitor", "Pattern Scanner"}:
    if source == "연결 안 함 (기본)":
        st.subheader(page)
        st.info("왼쪽에서 데이터 공급원을 선택하면 공통 재베이스·패턴·피봇 엔진이 종목을 분석합니다. 기본 상태에서는 임의의 시세나 신호를 표시하지 않습니다.")
        st.markdown("**이 화면에서 사용하는 공통 엔진**")
        st.write("완료 일봉 → 날짜순 재베이스 상태 → 단일 주 패턴/후보 피봇 → 역사적 Breakout Pivot → EOD Stage → 거리/RVOL/모멘텀")
        st.caption("Yahoo Finance EOD 정책: 완료된 미국 일봉만 사용합니다. 당일 가격 변동이 필요한 기능은 추후 별도 Live Monitor로 분리합니다.")
    elif not symbols:
        st.warning("관심종목을 하나 이상 입력하세요.")
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
        ("Yahoo Finance 공급원", "EOD 데이터 정책 적용 · 외부 데이터 정정 가능"),
        ("7개 회귀 종목 과거 스냅샷", "포함·정답 라벨은 미검증"),
        ("Supabase 실스키마/RLS 연결", "미연결·기존 DB 보호"),
        ("나머지 화면 전체 기능 이관", "후속 구현"),
        ("주문 제출/자동 매매", "미구현·비활성"),
    ]
    st.dataframe(pd.DataFrame(status_rows, columns=["기능", "상태"]), use_container_width=True, hide_index=True)
    st.info("이 단계는 두 핵심 화면을 실제 분석 엔진에 연결합니다. Supabase 읽기/쓰기, 포지션/매매일지, 나머지 화면은 아직 연결된 것으로 표시하지 않습니다.")

elif page == "Overview":
    st.subheader("Overview")
    st.write("V2 핵심 분석 화면을 연결 중입니다. 실제 보유 포지션과 거래 기록은 아직 Supabase에 연결되지 않았습니다.")
    st.metric("핵심 화면 연결", "Momentum Monitor + Pattern Scanner")
    st.caption("다음 단계는 레거시 DB 스키마를 확인한 후 읽기 전용으로 Holdings/Overview 데이터를 연결하는 것입니다.")
else:
    st.subheader(page)
    st.info("이 화면은 메뉴를 유지하고 있습니다. 기능 명세서에 따라 후속 단계에서 실제 데이터/기능을 연결합니다. 현재 화면은 아직 기능 이관이 완료되지 않았습니다.")

st.divider()
st.caption("운영 안전장치: 완료 일봉 전용 · DB 쓰기 없음 · 주문 제출 없음 · 기존 대시보드 변경 없음 · Yahoo 원천 데이터 정정 가능")
