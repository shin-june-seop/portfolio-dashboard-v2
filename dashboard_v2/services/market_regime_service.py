"""Legacy-compatible EOD market regime score for the V2 Momentum Monitor.

This module ports the V25 broad-market score logic used by the legacy dashboard.
It is deliberately separate from individual-stock pattern, pivot, Entry Stage,
Pocket Pivot, and LIVE-state engines. It consumes completed daily bars only.

The previous dashboard fetched OHLCV with yfinance auto_adjust=True. V2's provider
returns both split-normalized traded OHLC (auto_adjust=False) and Adj Close; for
market-regime trend calculations we prefer Adj Close to retain compatibility with
the legacy market-regime calculation. No stock pattern/pivot engine consumes this
series.
"""
from __future__ import annotations

from typing import Mapping, Any
import math

import numpy as np
import pandas as pd

MARKET_UNIVERSE = (
    "QQQ", "SPY", "IWM", "DIA",
    "XLK", "XLF", "XLI", "XLE", "XLV", "XLC", "XLY", "XLP", "XLU",
)
GROWTH_LEADERS = ("QQQ", "XLK", "SOXX", "IGV")
MARKET_SYMBOLS = tuple(dict.fromkeys((*MARKET_UNIVERSE, *GROWTH_LEADERS)))


def _safe_float(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def slope_pct(series: pd.Series, lookback: int = 20) -> float:
    """Percentage change in an MA over the requested lookback window."""
    values = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    if len(values) < lookback + 1:
        return 0.0
    first, last = float(values.iloc[-lookback - 1]), float(values.iloc[-1])
    return (last / first - 1.0) * 100.0 if first else 0.0


def market_metrics_from_bars(bars: pd.DataFrame | None) -> dict[str, Any] | None:
    """Calculate the legacy V25 market metrics from completed daily bars.

    `Adj Close` is used when provided because the old dashboard fetched adjusted
    OHLC with auto_adjust=True. Raw Close remains supported for deterministic
    test fixtures and compatible external data frames.
    """
    if bars is None or bars.empty:
        return None
    x = bars.copy()
    close_col = "Adj Close" if "Adj Close" in x.columns else "Close"
    if close_col not in x.columns:
        return None

    close = pd.to_numeric(x[close_col], errors="coerce")
    if "Volume" in x.columns:
        volume = pd.to_numeric(x["Volume"], errors="coerce").fillna(0.0)
    else:
        volume = pd.Series(0.0, index=x.index)
    valid = close.notna() & np.isfinite(close) & (close > 0)
    x = x.loc[valid].copy()
    close = close.loc[valid].astype(float).reset_index(drop=True)
    volume = volume.loc[valid].astype(float).reset_index(drop=True)
    if len(close) < 210:
        return None

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    price = float(close.iloc[-1])
    m20 = float(ma20.iloc[-1])
    m50 = float(ma50.iloc[-1])
    m200 = float(ma200.iloc[-1])
    slope50 = slope_pct(ma50, 20)
    slope200 = slope_pct(ma200, 20)
    ret63 = float(close.iloc[-1] / close.iloc[-64] - 1.0) * 100.0 if len(close) > 64 else math.nan

    daily_return = close.pct_change() * 100.0
    volume_up = volume > volume.shift(1)
    distribution_mask = (daily_return <= -0.2) & volume_up
    distribution25 = int(distribution_mask.tail(25).sum())

    if "Date" in x.columns:
        date_value = pd.to_datetime(x["Date"].iloc[-1], errors="coerce")
    else:
        date_value = pd.to_datetime(x.index[-1], errors="coerce")
    date_text = date_value.strftime("%Y-%m-%d") if pd.notna(date_value) else None

    return {
        "price": price,
        "ma20": m20,
        "ma50": m50,
        "ma200": m200,
        "price_vs_200_pct": (price / m200 - 1.0) * 100.0 if m200 else math.nan,
        "price_vs_50_pct": (price / m50 - 1.0) * 100.0 if m50 else math.nan,
        "ma50_slope": slope50,
        "ma200_slope": slope200,
        "above50": bool(price > m50),
        "above200": bool(price > m200),
        "ma50_above200": bool(m50 > m200),
        "ret63": ret63,
        "distribution_25": distribution25,
        "data_date": date_text,
    }


def trend_points(metrics: Mapping[str, Any] | None, weight: int) -> int:
    """Legacy V25 QQQ/SPY trend score contribution."""
    if not metrics:
        return 0
    price = _safe_float(metrics.get("price"))
    ma200 = _safe_float(metrics.get("ma200"))
    slope200 = _safe_float(metrics.get("ma200_slope"), 0.0)
    if not all(math.isfinite(v) for v in (price, ma200)) or ma200 <= 0:
        return 0
    if price > ma200 and slope200 > 0:
        return int(weight)
    if price > ma200:
        return int(round(weight * 0.72))
    if price >= ma200 * 0.97:
        return int(round(weight * 0.35))
    return 0


def regime_label(score: int) -> str:
    if score >= 80:
        return "🟢 상승장 / 적극"
    if score >= 65:
        return "🟡 상승장 / 주의"
    if score >= 45:
        return "🟠 조정 / 방어"
    return "🔴 하락장 / 신규매수 제한"


def regime_action(score: int) -> str:
    if score >= 80:
        return "신규 Momentum 매수 가능 · 돌파/거래량 확인 시 정상 진입"
    if score >= 65:
        return "신규 매수 가능하나 선별 강화 · 추격매수 금지 · 돌파 실패 관찰"
    if score >= 45:
        return "신규 매수 비중 축소 · 기존 강한 리더 중심 · 현금 비중 확대 고려"
    return "신규 Momentum 매수 보류 · 기존 포지션 리스크 관리 우선"


def _breadth_from_etfs(metrics: Mapping[str, Mapping[str, Any]]) -> tuple[float, float, int] | None:
    rows = [metrics[t] for t in MARKET_UNIVERSE if metrics.get(t) is not None]
    if not rows:
        return None
    return (
        sum(bool(m["above50"]) for m in rows) / len(rows) * 100.0,
        sum(bool(m["above200"]) for m in rows) / len(rows) * 100.0,
        len(rows),
    )


def breadth_from_closes(closes: pd.DataFrame | None, expected_date: str | None = None) -> tuple[float, float, int] | None:
    """Legacy S&P 500 + Nasdaq-100 breadth proxy, when the full set is available."""
    if closes is None or closes.empty or len(closes) < 200:
        return None
    x = closes.copy()
    x = x.apply(pd.to_numeric, errors="coerce")
    try:
        last_date = pd.Timestamp(x.index[-1]).date().isoformat()
    except Exception:
        last_date = None
    if expected_date and last_date != expected_date:
        return None
    last = x.iloc[-1]
    ma50 = x.tail(50).mean(axis=0)
    ma200 = x.tail(200).mean(axis=0)
    eligible = last.notna() & ma200.notna()
    n = int(eligible.sum())
    if n < 100:
        return None
    above50 = (last > ma50) & last.notna() & ma50.notna()
    above200 = (last > ma200) & last.notna() & ma200.notna()
    return float(above50.sum() / n * 100.0), float(above200.sum() / n * 100.0), n


def compute_market_regime(
    metrics: Mapping[str, Mapping[str, Any] | None],
    *,
    broad_breadth: tuple[float, float, int] | None = None,
) -> dict[str, Any]:
    """Compose the existing V25 100-point regime, without changing its weights.

    If broad S&P 500 + Nasdaq-100 breadth is unavailable, the legacy fallback is
    the fraction of the 13 ETF market proxy above its 50/200-day moving averages.
    """
    usable = {str(k).upper(): v for k, v in metrics.items() if v is not None}
    q = usable.get("QQQ")
    sp = usable.get("SPY")
    if q is None or sp is None:
        return {
            "available": False,
            "score": None,
            "label": "⚪ 시장 데이터 부족",
            "action": "QQQ/SPY 데이터를 불러오지 못했습니다.",
            "reason": "QQQ_OR_SPY_DATA_UNAVAILABLE",
            "components": {},
            "metrics": usable,
        }

    q_date, sp_date = q.get("data_date"), sp.get("data_date")
    if not q_date or not sp_date or q_date != sp_date:
        return {
            "available": False,
            "score": None,
            "label": "⚪ 시장 데이터 기준일 불일치",
            "action": "QQQ와 SPY의 완료 일봉 기준일이 일치하지 않아 레짐 점수를 표시하지 않습니다.",
            "reason": "QQQ_SPY_SESSION_MISMATCH",
            "components": {},
            "metrics": usable,
        }

    q_score = trend_points(q, 20)
    sp_score = trend_points(sp, 15)
    structure = 0.0
    for m in (q, sp):
        if m["ma50_above200"] and m["ma50_slope"] > 0 and m["ma200_slope"] > 0:
            structure += 3.75
        elif m["ma50_above200"] and m["ma200_slope"] >= 0:
            structure += 2.75
        elif m["price"] >= m["ma200"] * 0.97:
            structure += 1.25
    structure_score = int(round(structure))

    breadth = broad_breadth if broad_breadth is not None and broad_breadth[2] >= 100 else _breadth_from_etfs(usable)
    breadth_source = "S&P500+Nasdaq100" if broad_breadth is not None and broad_breadth[2] >= 100 else "ETF 대체"
    if breadth is None:
        above50_pct = above200_pct = breadth_avg = math.nan
        breadth_score = 0.0
        breadth_n = 0
        breadth_source = "데이터 부족"
    else:
        above50_pct, above200_pct, breadth_n = breadth
        breadth_avg = float(np.nanmean([above50_pct, above200_pct]))
        breadth_score = 20.0 * breadth_avg / 100.0

    dist_q = int(q.get("distribution_25", 0))
    dist_sp = int(sp.get("distribution_25", 0))
    distribution_max = max(dist_q, dist_sp)
    if distribution_max <= 2:
        distribution_score = 15
    elif distribution_max <= 4:
        distribution_score = 10
    elif distribution_max <= 6:
        distribution_score = 5
    else:
        distribution_score = 0

    leader_metrics = [usable[t] for t in GROWTH_LEADERS if usable.get(t) is not None]
    leader_above50 = (sum(bool(m["above50"]) for m in leader_metrics) / len(leader_metrics) * 100.0) if leader_metrics else math.nan
    leader_above200 = (sum(bool(m["above200"]) for m in leader_metrics) / len(leader_metrics) * 100.0) if leader_metrics else math.nan
    leader_returns = [_safe_float(m.get("ret63")) for m in leader_metrics]
    leader_returns = [v for v in leader_returns if math.isfinite(v)]
    spy_ret = _safe_float(sp.get("ret63"))
    leader_relative = (float(np.mean(leader_returns)) - spy_ret) if leader_returns and math.isfinite(spy_ret) else math.nan
    if not math.isfinite(leader_above50):
        leadership_score = 0
    elif leader_above50 >= 75 and leader_above200 >= 75 and leader_relative >= 3:
        leadership_score = 15
    elif leader_above50 >= 50 and leader_above200 >= 50 and leader_relative >= 0:
        leadership_score = 11
    elif leader_above50 >= 50:
        leadership_score = 7
    else:
        leadership_score = 2

    score = int(round(max(0, min(100, q_score + sp_score + structure_score + breadth_score + distribution_score + leadership_score))))
    components = {
        "QQQ 장기추세": q_score,
        "S&P500 장기추세": sp_score,
        "50/200MA 구조": structure_score,
        "시장 참여도": int(round(breadth_score)),
        "분산매도": distribution_score,
        "성장 리더십": leadership_score,
    }
    return {
        "available": True,
        "score": score,
        "label": regime_label(score),
        "action": regime_action(score),
        "components": components,
        "metrics": usable,
        "breadth_above50_pct": above50_pct,
        "breadth_above200_pct": above200_pct,
        "breadth_avg_pct": breadth_avg,
        "breadth_source": breadth_source,
        "breadth_n": breadth_n,
        "distribution_qqq": dist_q,
        "distribution_spy": dist_sp,
        "distribution_max": distribution_max,
        "leader_above50_pct": leader_above50,
        "leader_above200_pct": leader_above200,
        "leader_relative_3m_pct": leader_relative,
        "data_date": q_date,
    }
