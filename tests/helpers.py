from __future__ import annotations
from datetime import date, timedelta
import pandas as pd


def bars_from_closes(closes, *, volumes=None, highs=None, lows=None, start=date(2026, 1, 1)):
    rows = []
    if volumes is None:
        volumes = [100.0] * len(closes)
    for i, close in enumerate(closes):
        c = float(close)
        h = float(highs[i]) if highs is not None else c + 1.0
        l = float(lows[i]) if lows is not None else c - 1.0
        rows.append({
            "Date": start + timedelta(days=i),
            "Open": c,
            "High": max(h, c),
            "Low": min(l, c),
            "Close": c,
            "Volume": float(volumes[i]),
        })
    return pd.DataFrame(rows)
