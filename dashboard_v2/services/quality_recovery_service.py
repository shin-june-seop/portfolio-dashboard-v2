"""Legacy-compatible Quality Recovery Radar for V2.

Ports the working V30 Quality Recovery behavior from the user's legacy dashboard.
Technical recovery is calculated from completed daily OHLC bars. Annual fundamental
metrics come from Yahoo Finance income statements (not SEC EDGAR), and RS uses
adjusted close series versus QQQ, mirroring the legacy V30.5 calculation.

This module is read-only. It does not alter official stage/pivot/PP state or place
orders. Missing fundamental data cannot produce a quality PASS.
"""
from __future__ import annotations

from typing import Any, Callable
import math

import pandas as pd


APPROACH_PCT = 5.0
RETEST_PCT = 3.0
BOUNCE_LOOKBACK = 7
MIN_TECHNICAL_BARS = 200

FUNDAMENTAL_CANDIDATES = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "earnings": ("Net Income", "Net Income Common Stockholders"),
}


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _growth_series(values: list[float]) -> list[float]:
    """Match the legacy annual statement calculation, including its ordering."""
    result: list[float] = []
    for index in range(min(3, max(0, len(values) - 1))):
        current, previous = values[index], values[index + 1]
        if previous == 0:
            result.append(float("nan"))
        else:
            result.append((current / previous - 1.0) * 100.0)
    return result


def fundamentals_from_income_statement(income_stmt: pd.DataFrame | None) -> dict[str, Any]:
    """Calculate the annual income/revenue gate using the legacy V30 rules.

    Compatibility note: despite the legacy UI label "EPS Growth", the legacy
    implementation uses annual Net Income growth, not per-share EPS. That exact
    behavior is preserved so V2 matches the old dashboard's gate/results.
    """
    out: dict[str, Any] = {
        "revenue_growth": float("nan"),
        "earnings_growth": float("nan"),
        "revenue_growth_prev": float("nan"),
        "earnings_growth_prev": float("nan"),
        "fundamental_direction": "데이터 부족",
        "fundamental_damage": False,
        "quality_gate": False,
        "fundamental_data_status": "UNAVAILABLE",
        "fundamental_error": None,
    }
    if not isinstance(income_stmt, pd.DataFrame) or income_stmt.empty:
        out["fundamental_error"] = "YFINANCE_INCOME_STATEMENT_EMPTY"
        return out

    stmt = income_stmt.copy()
    # yfinance returns recent fiscal periods first. Sort by date descending where
    # possible, but preserve provider order for period labels that cannot parse.
    try:
        parsed = pd.to_datetime(pd.Index(stmt.columns), errors="raise")
        order = sorted(range(len(parsed)), key=lambda i: parsed[i], reverse=True)
        stmt = stmt.iloc[:, order]
    except Exception:
        pass

    def values_for(candidates: tuple[str, ...]) -> list[float]:
        for name in candidates:
            if name not in stmt.index:
                continue
            series = pd.to_numeric(stmt.loc[name], errors="coerce").dropna()
            if len(series) >= 2:
                return [float(v) for v in series.tolist()]
        return []

    revenue_values = values_for(FUNDAMENTAL_CANDIDATES["revenue"])
    earnings_values = values_for(FUNDAMENTAL_CANDIDATES["earnings"])
    revenue_growths = _growth_series(revenue_values)
    earnings_growths = _growth_series(earnings_values)

    if revenue_growths:
        out["revenue_growth"] = revenue_growths[0]
    if earnings_growths:
        out["earnings_growth"] = earnings_growths[0]
    if len(revenue_growths) >= 2:
        out["revenue_growth_prev"] = revenue_growths[1]
    if len(earnings_growths) >= 2:
        out["earnings_growth_prev"] = earnings_growths[1]

    deltas: list[float] = []
    for current, previous in (
        (out["revenue_growth"], out["revenue_growth_prev"]),
        (out["earnings_growth"], out["earnings_growth_prev"]),
    ):
        if pd.notna(current) and pd.notna(previous):
            deltas.append(float(current) - float(previous))
    if deltas:
        delta = sum(deltas) / len(deltas)
        out["fundamental_direction"] = "가속" if delta > 5 else "둔화" if delta < -5 else "유지"

    valid_revenue = pd.notna(out["revenue_growth"])
    valid_earnings = pd.notna(out["earnings_growth"])
    out["fundamental_damage"] = bool(
        (valid_revenue and float(out["revenue_growth"]) <= 0)
        or (valid_earnings and float(out["earnings_growth"]) <= 0)
    )
    out["quality_gate"] = bool(
        valid_revenue
        and valid_earnings
        and float(out["revenue_growth"]) > 0
        and float(out["earnings_growth"]) > 0
        and not out["fundamental_damage"]
    )
    if valid_revenue and valid_earnings:
        out["fundamental_data_status"] = "AVAILABLE"
    else:
        out["fundamental_data_status"] = "PARTIAL"
        out["fundamental_error"] = "YFINANCE_REQUIRED_INCOME_FACTS_MISSING"
    return out


def fetch_yfinance_fundamentals(
    ticker: str,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Fetch the legacy annual income statement. Injectable for deterministic tests."""
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        result = fundamentals_from_income_statement(None)
        result["fundamental_error"] = "SYMBOL_EMPTY"
        return result
    try:
        if ticker_factory is None:
            import yfinance as yf
            ticker_factory = yf.Ticker
        instrument = ticker_factory(symbol)
        income_stmt = getattr(instrument, "income_stmt", None)
        result = fundamentals_from_income_statement(income_stmt)
        result["fundamental_source"] = "YAHOO_FINANCE_ANNUAL_INCOME_STATEMENT"
        if result.get("fundamental_error") is None:
            result["fundamental_error"] = None
        return result
    except Exception as exc:
        result = fundamentals_from_income_statement(None)
        result["fundamental_error"] = f"YFINANCE_FUNDAMENTALS_FETCH_FAILED:{type(exc).__name__}"
        result["fundamental_source"] = "YAHOO_FINANCE_ANNUAL_INCOME_STATEMENT"
        return result


def _clean_price_bars(symbol: str, bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        raise ValueError("NO_DAILY_BARS")
    required = {"Date", "Close", "Low"}
    if not required.issubset(bars.columns):
        raise ValueError("MISSING_DAILY_COLUMNS:" + ",".join(sorted(required - set(bars.columns))))
    frame = bars.loc[:, ["Date", "Close", "Low"]].copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    frame["Low"] = pd.to_numeric(frame["Low"], errors="coerce")
    frame = frame.dropna(subset=["Date", "Close", "Low"])
    frame = frame.sort_values("Date", kind="stable").drop_duplicates("Date", keep="last").reset_index(drop=True)
    frame = frame[(frame["Close"] > 0) & (frame["Low"] > 0)]
    if len(frame) < MIN_TECHNICAL_BARS:
        raise ValueError(f"INSUFFICIENT_DAILY_BARS:{len(frame)}<{MIN_TECHNICAL_BARS}")
    return frame


def _close_series(data: pd.DataFrame | pd.Series | None) -> pd.Series:
    if data is None:
        return pd.Series(dtype=float)
    if isinstance(data, pd.DataFrame):
        if data.empty:
            return pd.Series(dtype=float)
        if "Date" in data.columns:
            date_values = pd.to_datetime(data["Date"], errors="coerce")
            field = "Adj Close" if "Adj Close" in data.columns else "Close"
            if field not in data.columns:
                return pd.Series(dtype=float)
            values = pd.to_numeric(data[field], errors="coerce").to_numpy()
            series = pd.Series(values, index=date_values)
        else:
            field = "Adj Close" if "Adj Close" in data.columns else "Close"
            if field not in data.columns:
                return pd.Series(dtype=float)
            series = pd.to_numeric(data[field], errors="coerce")
    else:
        series = pd.to_numeric(data, errors="coerce")
    try:
        index = pd.to_datetime(series.index, errors="coerce")
        # Daily date keys are normalized to the exchange date, without UTC shift.
        if getattr(index, "tz", None) is not None:
            index = index.tz_localize(None)
        series = pd.Series(pd.to_numeric(series.to_numpy(), errors="coerce"), index=index)
        series = series[~series.index.isna()]
        series.index = series.index.normalize()
        series = series[~series.index.duplicated(keep="last")].sort_index().dropna()
        return series[series > 0]
    except Exception:
        return pd.Series(dtype=float)


def _rs_component(excess_pct: float) -> float:
    if pd.isna(excess_pct):
        return float("nan")
    if excess_pct >= 20:
        return 100.0
    if excess_pct >= 10:
        return 90.0 + (excess_pct - 10) / 10 * 10
    if excess_pct >= 5:
        return 80.0 + (excess_pct - 5) / 5 * 10
    if excess_pct >= 0:
        return 70.0 + excess_pct / 5 * 10
    if excess_pct >= -5:
        return 50.0 + (excess_pct + 5) / 5 * 20
    if excess_pct >= -10:
        return 30.0 + (excess_pct + 10) / 5 * 20
    if excess_pct >= -20:
        return 10.0 + (excess_pct + 20) / 10 * 20
    return 0.0


def _rs_rating(stock: pd.Series, benchmark: pd.Series) -> float:
    periods = ((21, 0.15), (63, 0.35), (126, 0.25), (252, 0.25))
    components: list[tuple[float, float]] = []
    for n, weight in periods:
        if len(stock) > n and len(benchmark) > n:
            stock_return = (float(stock.iloc[-1]) / float(stock.iloc[-n - 1]) - 1) * 100
            benchmark_return = (float(benchmark.iloc[-1]) / float(benchmark.iloc[-n - 1]) - 1) * 100
            components.append((_rs_component(stock_return - benchmark_return), weight))
    if not components:
        return float("nan")
    score = sum(value * weight for value, weight in components) / sum(weight for _, weight in components)
    return float(max(0.0, min(100.0, score)))


def canonical_legacy_rs(stock_data: pd.DataFrame | pd.Series | None, benchmark_data: pd.DataFrame | pd.Series | None) -> dict[str, float]:
    """Port of legacy V30.5 RS: aligned adjusted closes, QQQ, 21/63/126/252 days."""
    stock = _close_series(stock_data)
    benchmark = _close_series(benchmark_data)
    if stock.empty or benchmark.empty:
        return {"rs": float("nan"), "rs_prev": float("nan"), "rs_delta": float("nan")}
    aligned = pd.concat([stock.rename("stock"), benchmark.rename("benchmark")], axis=1, join="inner").dropna().sort_index()
    aligned = aligned[~aligned.index.duplicated(keep="last")]
    # Exact legacy V30.5 guard: do not show an RS value until there is a full year.
    if len(aligned) < 253:
        return {"rs": float("nan"), "rs_prev": float("nan"), "rs_delta": float("nan")}
    current = _rs_rating(aligned["stock"], aligned["benchmark"])
    previous = _rs_rating(aligned["stock"].iloc[:-20], aligned["benchmark"].iloc[:-20])
    delta = current - previous if pd.notna(current) and pd.notna(previous) else float("nan")
    return {
        "rs": float(current) if pd.notna(current) else float("nan"),
        "rs_prev": float(previous) if pd.notna(previous) else float("nan"),
        "rs_delta": float(delta) if pd.notna(delta) else float("nan"),
    }


def ma_recovery_event(close: pd.Series, low: pd.Series, ma: pd.Series, label: str) -> dict[str, Any]:
    """Legacy close-based support/recovery state machine for one moving average."""
    close = pd.Series(close, dtype=float).reset_index(drop=True)
    low = pd.Series(low, dtype=float).reset_index(drop=True)
    ma = pd.Series(ma, dtype=float).reset_index(drop=True)
    distance = (close / ma - 1) * 100
    if len(close) < 15 or pd.isna(distance.iloc[-1]):
        return {"state": "데이터 부족", "distance": float("nan"), "delta": float("nan")}
    dist = float(distance.iloc[-1])
    delta = float(distance.iloc[-1] - distance.iloc[-6]) if len(distance) >= 6 and pd.notna(distance.iloc[-6]) else float("nan")
    above2 = close.tail(2).to_numpy() > ma.tail(2).to_numpy()
    below2 = close.tail(2).to_numpy() < ma.tail(2).to_numpy()
    recent_below = False
    recovery_age = None
    start = max(0, len(close) - 40)
    for i in range(start + 1, len(close)):
        if close.iloc[i - 1] < ma.iloc[i - 1] and close.iloc[i] < ma.iloc[i]:
            recent_below = True
            recovery_age = len(close) - 1 - i
    if bool(below2[-1]) and bool(below2[-2]):
        state = "무너짐"
    elif close.iloc[-1] > ma.iloc[-1] and close.iloc[-2] <= ma.iloc[-2] and recent_below:
        state = "회복 시도"
    elif bool(above2[-1]) and bool(above2[-2]) and recent_below and recovery_age is not None and recovery_age < 35:
        near = abs(dist) <= RETEST_PCT
        if near:
            if close.iloc[-1] >= close.iloc[-2] and low.iloc[-1] <= ma.iloc[-1] * 1.03:
                state = "리테스트 확인"
            else:
                state = "리테스트 중"
        else:
            state = "회복 확정"
    else:
        recent_low = float(low.tail(BOUNCE_LOOKBACK).min())
        touched = recent_low <= float(ma.iloc[-1]) * 1.03 and recent_low >= float(ma.iloc[-1]) * 0.97
        tail_n = min(10, len(close))
        no_break = int((close.tail(tail_n).to_numpy() < ma.tail(tail_n).to_numpy()).sum()) < 2
        bounce = (float(close.iloc[-1]) / recent_low - 1) * 100 if recent_low > 0 else 0.0
        if touched and no_break and bounce >= 1.5 and close.iloc[-1] > ma.iloc[-1]:
            state = "지지 반등"
        elif abs(dist) <= RETEST_PCT:
            state = "테스트 중"
        elif abs(dist) <= APPROACH_PCT and delta < 0:
            state = "접근중 ↑" if dist > 0 else "접근중 ↓"
        else:
            state = "정상"
    return {"state": f"{label} {state}", "distance": dist, "delta": delta}


def analyze_legacy_quality_recovery(
    ticker: str,
    bars: pd.DataFrame,
    *,
    fundamentals: dict[str, Any] | None = None,
    adjusted_close: pd.DataFrame | pd.Series | None = None,
    benchmark_adjusted_close: pd.DataFrame | pd.Series | None = None,
    benchmark_bars: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Calculate the legacy Quality Recovery output from V2 completed EOD bars."""
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        raise ValueError("SYMBOL_EMPTY")
    frame = _clean_price_bars(symbol, bars)
    close = frame["Close"].astype(float).reset_index(drop=True)
    low = frame["Low"].astype(float).reset_index(drop=True)
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma120 = close.rolling(120).mean()
    ma200 = close.rolling(200).mean()
    price = float(close.iloc[-1])
    q = dict(fundamentals or fundamentals_from_income_statement(None))
    q.setdefault("quality_gate", False)
    q.setdefault("fundamental_damage", False)
    q.setdefault("fundamental_direction", "데이터 부족")
    q.setdefault("fundamental_data_status", "UNAVAILABLE")
    q.setdefault("fundamental_error", "FUNDAMENTAL_RESULT_MISSING")

    adjusted_rs = canonical_legacy_rs(adjusted_close, benchmark_adjusted_close)
    rs = adjusted_rs["rs"]
    rs_delta = adjusted_rs["rs_delta"]
    recent_high = float(close.tail(252).max())
    correction = (price / recent_high - 1) * 100 if recent_high > 0 else float("nan")

    decline_reason = "미확인"
    try:
        if isinstance(benchmark_bars, pd.DataFrame) and not benchmark_bars.empty and "Close" in benchmark_bars:
            bench = pd.to_numeric(benchmark_bars["Close"], errors="coerce").dropna()
            if len(bench) >= 20:
                qqq_dd = (float(bench.iloc[-1]) / float(bench.tail(252).max()) - 1) * 100
                if qqq_dd < -8 and correction < -8:
                    decline_reason = "시장 조정 동반 가능"
                elif correction < -8:
                    decline_reason = "기업/산업 요인 가능 · 확인 필요"
    except Exception:
        pass

    e120 = ma_recovery_event(close, low, ma120, "120MA")
    e200 = ma_recovery_event(close, low, ma200, "200MA")
    active_states = ("무너짐", "회복 시도", "회복 확정", "리테스트 중", "리테스트 확인", "지지 반등", "테스트 중", "접근중 ↑", "접근중 ↓")
    status = "QUALITY WATCH"
    if bool(q.get("quality_gate")):
        for event in (e200, e120):  # Long-term 200MA event remains the higher priority.
            if any(token in str(event["state"]) for token in active_states):
                status = str(event["state"])
                break
    elif bool(q.get("fundamental_damage")):
        status = "QUALITY GATE FAIL"

    if q.get("fundamental_data_status") in ("UNAVAILABLE", "PARTIAL"):
        damage_status = "REVIEW"
    elif q.get("fundamental_damage"):
        damage_status = "DETECTED"
    else:
        damage_status = "CLEAR"
    return {
        "ticker": symbol,
        "as_of_date": frame["Date"].iloc[-1].date().isoformat(),
        "price": price,
        "quality_gate": bool(q.get("quality_gate")),
        "quality": "PASS" if bool(q.get("quality_gate")) else "CHECK",
        "eps_growth": q.get("earnings_growth", float("nan")),
        "sales_growth": q.get("revenue_growth", float("nan")),
        "earnings_growth_prev": q.get("earnings_growth_prev", float("nan")),
        "sales_growth_prev": q.get("revenue_growth_prev", float("nan")),
        "fundamental_direction": q.get("fundamental_direction", "데이터 부족"),
        "fundamental_damage": bool(q.get("fundamental_damage")),
        "fundamental_damage_status": damage_status,
        "fundamental_data_status": q.get("fundamental_data_status", "UNAVAILABLE"),
        "fundamental_error": q.get("fundamental_error"),
        "correction": correction,
        "decline_reason": decline_reason,
        "ma20": float(ma20.iloc[-1]),
        "ma50": float(ma50.iloc[-1]),
        "ma120": float(ma120.iloc[-1]),
        "ma200": float(ma200.iloc[-1]),
        "e120": e120,
        "e200": e200,
        "rs": rs,
        "rs_delta": rs_delta,
        "status": status,
    }


def quality_recovery_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Format/sort rows to match the legacy screenshot's primary table."""
    if not rows:
        return pd.DataFrame()

    def fmt_pct(value: Any) -> str:
        return f"{float(value):+.1f}%" if value is not None and pd.notna(value) else "—"

    formatted = []
    for row in rows:
        formatted.append({
            "종목": row.get("ticker", ""),
            "Quality": "🟢 PASS" if row.get("quality_gate") else "🟡 CHECK",
            "EPS Growth": fmt_pct(row.get("eps_growth")),
            "Sales Growth": fmt_pct(row.get("sales_growth")),
            "Fundamental": row.get("fundamental_direction", "데이터 부족"),
            "Correction": fmt_pct(row.get("correction")),
            "120MA": f"{fmt_pct(row.get('e120', {}).get('distance'))} · {row.get('e120', {}).get('state', '데이터 부족')}",
            "200MA": f"{fmt_pct(row.get('e200', {}).get('distance'))} · {row.get('e200', {}).get('state', '데이터 부족')}",
            "RS": f"{float(row['rs']):.0f}" if row.get("rs") is not None and pd.notna(row.get("rs")) else "—",
            "RS 변화": f"{float(row['rs_delta']):+.1f}" if row.get("rs_delta") is not None and pd.notna(row.get("rs_delta")) else "—",
            "현재 상태": row.get("status", "QUALITY WATCH"),
            "_correction": row.get("correction", float("nan")),
        })
    frame = pd.DataFrame(formatted)
    priority = {
        "무너짐": 1, "회복 시도": 2, "회복 확정": 3, "리테스트 중": 4,
        "리테스트 확인": 5, "지지 반등": 6, "접근중": 7, "테스트 중": 8,
        "QUALITY WATCH": 9, "QUALITY GATE FAIL": 99,
    }

    def rank(value: Any) -> int:
        text = str(value)
        for key, number in priority.items():
            if key in text:
                return number
        return 50

    frame["_priority"] = frame["현재 상태"].map(rank)
    return frame.sort_values(["_priority", "_correction"], ascending=[True, True], kind="stable").drop(columns=["_priority", "_correction"]).reset_index(drop=True)
