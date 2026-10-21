"""Pivot arithmetic and separately-labelled reference values."""
from __future__ import annotations
from math import isfinite
from .data_validation import normalize_ohlcv
from ..config.settings import DEFAULT_SETTINGS


def price_distance_pct(price: float | None, pivot: float | None) -> float | None:
    """(price / pivot - 1) * 100; invalid/nonpositive pivots yield None."""
    if price is None or pivot is None:
        return None
    if not isfinite(float(price)) or not isfinite(float(pivot)) or float(price) <= 0 or float(pivot) <= 0:
        return None
    return (float(price) / float(pivot) - 1.0) * 100.0


def is_pivot_approach(price: float, candidate_pivot: float | None, *, candidate_valid: bool, rebase_forming: bool = False, min_distance_pct: float = DEFAULT_SETTINGS.pivot_approach_min_pct) -> bool:
    """PIVOT APPROACH iff a valid candidate exists and -5% <= distance < 0%."""
    if not candidate_valid or candidate_pivot is None or rebase_forming:
        return False
    distance = price_distance_pct(price, candidate_pivot)
    return distance is not None and (distance >= min_distance_pct - 1e-9) and distance < 0.0


def twenty_day_reference(frame, *, as_of_date=None) -> float | None:
    """Highest High over the latest 20 eligible/completed bars, including last row."""
    if frame is None or len(frame) == 0:
        return None
    df = normalize_ohlcv(frame, as_of_date=as_of_date) if as_of_date is not None else frame.copy()
    if "High" not in df.columns or len(df) < 20:
        return None
    value = df["High"].tail(20).max()
    return float(value) if isfinite(float(value)) else None
