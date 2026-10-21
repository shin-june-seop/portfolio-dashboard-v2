"""Market-data boundary. Provider choice/completion semantics are not guessed."""
from __future__ import annotations
from datetime import date
import pandas as pd
from ..engines.data_validation import normalize_ohlcv


class MarketDataProviderNotConfigured(RuntimeError):
    pass


class MarketDataService:
    """Wrap a supplied provider so all downstream engines receive canonical bars."""
    def __init__(self, provider=None):
        self._provider = provider

    def daily_bars(self, symbol: str, *, as_of_date: date | str | None = None) -> pd.DataFrame:
        if self._provider is None:
            raise MarketDataProviderNotConfigured(
                "Price source and adjusted-OHLCV policy must be approved before live data is enabled"
            )
        raw = self._provider.daily_bars(symbol.upper().strip(), as_of_date=as_of_date)
        return normalize_ohlcv(raw, as_of_date=as_of_date)
