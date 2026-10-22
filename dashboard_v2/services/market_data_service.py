"""Market-data boundary with a canonical OHLCV series and optional Adj Close.

The pattern/pivot engine consumes normalized OHLCV. If the provider supplies a
separate Adj Close, retain it as an additional date-aligned field for total-return
metrics; it is never substituted for technical OHLC values.
"""
from __future__ import annotations
from datetime import date
import math
import pandas as pd
from ..engines.data_validation import normalize_ohlcv


class MarketDataProviderNotConfigured(RuntimeError):
    pass


class MarketDataService:
    """Wrap a supplied provider and preserve a separate adjusted-close series."""
    def __init__(self, provider=None):
        self._provider = provider

    def daily_bars(self, symbol: str, *, as_of_date: date | str | None = None) -> pd.DataFrame:
        if self._provider is None:
            raise MarketDataProviderNotConfigured(
                "Price source and adjusted-OHLCV policy must be configured before live data is enabled"
            )
        raw = self._provider.daily_bars(symbol.upper().strip(), as_of_date=as_of_date)
        normalized = normalize_ohlcv(raw, as_of_date=as_of_date)
        adj_col = next(
            (col for col in raw.columns if "".join(str(col).strip().lower().replace("_", " ").split()) == "adjclose"),
            None,
        )
        if adj_col is None:
            return normalized
        date_col = next(
            (col for col in raw.columns if str(col).strip().lower() in {"date", "datetime", "timestamp"}),
            None,
        )
        date_values = raw[date_col] if date_col is not None else raw.index
        adjusted = pd.DataFrame({
            "Date": pd.to_datetime([pd.Timestamp(value).date() for value in date_values]),
            "Adj Close": pd.to_numeric(raw[adj_col], errors="coerce").to_numpy(),
        }).reset_index(drop=True)
        if adjusted["Date"].duplicated().any():
            raise ValueError("Adj Close contains duplicate daily dates")
        valid_adjusted = adjusted["Adj Close"].map(lambda x: pd.notna(x) and math.isfinite(float(x)) and float(x) > 0)
        if not valid_adjusted.all():
            raise ValueError("Adj Close is missing or invalid; refusing incomplete return data")
        result = normalized.merge(adjusted, on="Date", how="left", validate="one_to_one")
        if result["Adj Close"].isna().any():
            raise ValueError("Adj Close does not align to OHLCV dates")
        return result
