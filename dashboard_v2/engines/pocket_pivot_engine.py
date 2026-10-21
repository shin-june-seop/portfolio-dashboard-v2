"""Pocket Pivot detector with strict down-day volume comparison and context gate."""
from __future__ import annotations
from datetime import date
import pandas as pd
from .data_validation import normalize_ohlcv
from ..config.settings import DEFAULT_SETTINGS
from ..models.schemas import PocketPivotResult


def detect_pocket_pivot(frame: pd.DataFrame, *, constructive_context: bool, as_of_date: date | str | None = None) -> PocketPivotResult:
    """Evaluate a signal on the final supplied completed daily bar.

    Fixed MA-distance cutoffs are deliberately absent. Price must be up on the
    signal day, beat the maximum volume of down days in the prior 10 bars, and
    show support/reclaim behavior at the 10D or 50D moving average. The caller
    supplies the broader uptrend/base-context validation explicitly.
    """
    try:
        df = normalize_ohlcv(frame, as_of_date=as_of_date)
    except (ValueError, TypeError) as exc:
        return PocketPivotResult(None, False, (f"INVALID_OHLCV:{type(exc).__name__}",))
    if len(df) < DEFAULT_SETTINGS.pocket_pivot_lookback + 2:
        last_date = df["Date"].iloc[-1].date() if not df.empty else None
        return PocketPivotResult(last_date, False, ("INSUFFICIENT_HISTORY_FOR_PP_VOLUME_REFERENCE",))

    signal = df.iloc[-1]
    previous = df.iloc[-2]
    prior = df.iloc[-(DEFAULT_SETTINGS.pocket_pivot_lookback + 1):-1].copy()
    # First reference bar's daily direction needs its preceding close too.
    prior_with_prev = df.iloc[-(DEFAULT_SETTINGS.pocket_pivot_lookback + 2):-1].copy()
    prior_with_prev["previous_close"] = prior_with_prev["Close"].shift(1)
    prior_down = prior_with_prev.iloc[1:]
    prior_down = prior_down.loc[prior_down["Close"] < prior_down["previous_close"]]
    if prior_down.empty:
        return PocketPivotResult(signal["Date"].date(), False, ("PP_VOLUME_REFERENCE_UNAVAILABLE",), float(signal["Volume"]), None, None, bool(constructive_context))

    max_down_volume = float(prior_down["Volume"].max())
    reasons: list[str] = []
    if float(signal["Close"]) <= float(previous["Close"]):
        reasons.append("SIGNAL_DAY_NOT_UP_BAR")
    if float(signal["Volume"]) <= max_down_volume:
        reasons.append("SIGNAL_VOLUME_NOT_ABOVE_PRIOR_10D_DOWN_DAY_MAX")

    close = df["Close"]
    ma10 = close.rolling(10, min_periods=10).mean()
    ma50 = close.rolling(50, min_periods=50).mean()
    # The reference period bars map to the matching rolling averages.
    prev_idx, current_idx = len(df) - 2, len(df) - 1
    supported_ma: str | None = None
    for name, mas in (("MA10", ma10), ("MA50", ma50)):
        m_now = mas.iloc[current_idx]
        m_prev = mas.iloc[prev_idx]
        if pd.isna(m_now):
            continue
        support_or_reclaim = float(signal["Low"]) <= float(m_now) < float(signal["Close"])
        if pd.notna(m_prev):
            support_or_reclaim = support_or_reclaim or (float(previous["Close"]) <= float(m_prev) and float(signal["Close"]) > float(m_now))
            support_or_reclaim = support_or_reclaim or (float(previous["Low"]) <= float(m_prev) < float(previous["Close"]) and float(signal["Close"]) > float(m_now))
        if support_or_reclaim:
            supported_ma = name
            break
    if supported_ma is None:
        reasons.append("NO_MA10_MA50_SUPPORT_OR_RECLAIM_BEHAVIOR")
    if not constructive_context:
        reasons.append("PP_CONTEXT_INVALID")

    return PocketPivotResult(
        as_of_date=signal["Date"].date(),
        valid_signal=not reasons,
        reason_codes=tuple(reasons or ["POCKET_PIVOT_VALID"]),
        signal_volume=float(signal["Volume"]),
        max_prior_down_day_volume=max_down_volume,
        reference_ma=supported_ma,
        context_valid=bool(constructive_context),
    )
