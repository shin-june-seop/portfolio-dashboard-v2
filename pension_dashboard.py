"""Data parsing and calculations for the pension portfolio dashboard."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
import math
import re
from typing import Any

import pandas as pd

HOLDING_COLUMNS = [
    "asset_group", "name", "ticker", "target_weight", "monthly_investment",
    "avg_cost", "quantity", "last_known_price", "sort_order",
]
MONTHLY_COLUMNS = ["record_month", "invested_amount", "total_balance", "profit", "return_pct", "return_change"]
WEEKLY_COLUMNS = ["record_date", "semi_return", "dow_return", "sp500_return", "nasdaq_return", "semi_change", "dow_change", "sp500_change", "nasdaq_change"]

ETF_NAMES = {
    "390390": "KODEX 미국반도체",
    "402970": "ACE 미국다우존스",
    "360750": "TIGER S&P500",
    "367380": "ACE 미국나스닥100",
}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _float(value: Any, default: float | None = None) -> float | None:
    value = _clean(value)
    if value is None or value == "":
        return default
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    value = _float(value)
    return int(value) if value is not None else default


def _ticker(value: Any) -> str:
    value = _clean(value)
    if value is None:
        return ""
    try:
        return str(int(float(value))).zfill(6)
    except (ValueError, TypeError):
        return re.sub(r"\s+", "", str(value)).upper()


def _month_number(value: Any) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    match = re.search(r"(\d{1,2})", str(value))
    if not match:
        return None
    number = int(match.group(1))
    return number if 1 <= number <= 12 else None


def _year_number(value: Any) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    match = re.search(r"(20\d{2})", str(value))
    return int(match.group(1)) if match else None


def _date_string(value: Any) -> str | None:
    value = _clean(value)
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return pd.Timestamp(value).date().isoformat()
        if isinstance(value, (int, float)):
            # Excel serial date; 1899-12-30 is the compatibility epoch.
            return (pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")).date().isoformat()
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return pd.Timestamp(parsed).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def import_excel_bytes(file_bytes: bytes) -> dict[str, list[dict[str, Any]]]:
    """Read the user's existing Korean workbook using cached formula results.

    No account number is imported. GoogleFinance prices are imported only as
    fallback snapshots; live prices are queried separately by the app.
    """
    sheets = pd.read_excel(BytesIO(file_bytes), sheet_name=None, header=None, engine="openpyxl")
    pension = sheets.get("연저펀")
    weekly = sheets.get("연저펀주간수익률")
    if pension is None or weekly is None:
        raise ValueError("엑셀에서 '연저펀'과 '연저펀주간수익률' 시트를 찾지 못했습니다.")

    holdings: list[dict[str, Any]] = []
    for idx in range(5, min(9, len(pension))):
        row = pension.iloc[idx]
        name = _clean(row.iloc[5] if len(row) > 5 else None)
        ticker = _ticker(row.iloc[16] if len(row) > 16 else None)
        if not name or not ticker:
            continue
        holdings.append({
            "asset_group": str(_clean(row.iloc[1]) or "기타"),
            "name": str(name),
            "ticker": ticker,
            "target_weight": _float(row.iloc[3] if len(row) > 3 else None, 0.0),
            "monthly_investment": _float(row.iloc[4] if len(row) > 4 else None, 0.0),
            "avg_cost": _float(row.iloc[8] if len(row) > 8 else None, 0.0),
            "quantity": _int(row.iloc[9] if len(row) > 9 else None),
            "last_known_price": _float(row.iloc[11] if len(row) > 11 else None, None),
            "sort_order": len(holdings),
        })
    if not holdings:
        raise ValueError("연저펀 시트의 보유 종목을 찾지 못했습니다.")

    monthly: list[dict[str, Any]] = []
    current_year: int | None = None
    last_return: float | None = None
    # Row 15 (zero-based 14) is the first monthly record.
    for idx in range(14, len(pension)):
        row = pension.iloc[idx]
        year = _year_number(row.iloc[1] if len(row) > 1 else None)
        if year:
            current_year = year
        month = _month_number(row.iloc[2] if len(row) > 2 else None)
        invested = _float(row.iloc[3] if len(row) > 3 else None)
        balance = _float(row.iloc[4] if len(row) > 4 else None)
        if month is None or current_year is None or invested is None or balance is None:
            continue
        record_month = f"{current_year:04d}-{month:02d}-01"
        return_pct = _float(row.iloc[0] if len(row) > 0 else None)
        if return_pct is None and invested:
            return_pct = balance / invested - 1
        profit = _float(row.iloc[5] if len(row) > 5 else None)
        if profit is None:
            profit = balance - invested
        return_change = _float(row.iloc[6] if len(row) > 6 else None)
        if return_change is None and return_pct is not None and last_return is not None:
            return_change = return_pct - last_return
        monthly.append({
            "record_month": record_month,
            "invested_amount": invested,
            "total_balance": balance,
            "profit": profit,
            "return_pct": return_pct,
            "return_change": return_change,
        })
        last_return = return_pct

    weekly_rows: list[dict[str, Any]] = []
    last_returns: dict[str, float | None] = {"semi_return": None, "dow_return": None, "sp500_return": None, "nasdaq_return": None}
    for idx in range(1, len(weekly)):
        row = weekly.iloc[idx]
        record_date = _date_string(row.iloc[0] if len(row) > 0 else None)
        if not record_date:
            continue
        values = {
            "semi_return": _float(row.iloc[1] if len(row) > 1 else None),
            "dow_return": _float(row.iloc[2] if len(row) > 2 else None),
            "sp500_return": _float(row.iloc[3] if len(row) > 3 else None),
            "nasdaq_return": _float(row.iloc[4] if len(row) > 4 else None),
            "semi_change": _float(row.iloc[7] if len(row) > 7 else None),
            "dow_change": _float(row.iloc[8] if len(row) > 8 else None),
            "sp500_change": _float(row.iloc[9] if len(row) > 9 else None),
            "nasdaq_change": _float(row.iloc[10] if len(row) > 10 else None),
        }
        if not any(value is not None for value in values.values()):
            continue
        weekly_rows.append({"record_date": record_date, **values})
        last_returns = values

    return {"holdings": holdings, "monthly": monthly, "weekly": weekly_rows}


def enrich_holdings(holdings: list[dict[str, Any]], prices: dict[str, dict[str, Any]] | None = None) -> pd.DataFrame:
    """Calculate portfolio values and the spreadsheet's allocation/rebalance fields."""
    prices = prices or {}
    interim: list[dict[str, Any]] = []
    for item in sorted(holdings, key=lambda x: int(x.get("sort_order", 0) or 0)):
        ticker = _ticker(item.get("ticker"))
        quote = prices.get(ticker, {}) or {}
        live_price = _float(quote.get("price"))
        stored_price = _float(item.get("last_known_price"))
        price = live_price if live_price is not None and live_price > 0 else stored_price
        price_source = str(quote.get("source") or ("엑셀 저장 시세" if stored_price else "시세 없음"))
        avg_cost = _float(item.get("avg_cost"), 0.0) or 0.0
        quantity = _int(item.get("quantity"))
        target_weight = _float(item.get("target_weight"), 0.0) or 0.0
        monthly_investment = _float(item.get("monthly_investment"), 0.0) or 0.0
        invested = avg_cost * quantity
        market_value = price * quantity if price is not None else None
        unrealized = market_value - invested if market_value is not None else None
        return_pct = market_value / invested - 1 if market_value is not None and invested > 0 else None
        interim.append({
            "자산군": item.get("asset_group", ""),
            "종목명": item.get("name", ETF_NAMES.get(ticker, ticker)),
            "티커": ticker,
            "목표비중": target_weight,
            "월투자금": monthly_investment,
            "매수단가": avg_cost,
            "수량": quantity,
            "현재가": price,
            "시세기준": quote.get("asof") or "",
            "시세출처": price_source,
            "투자금": invested,
            "평가금": market_value,
            "평가손익": unrealized,
            "수익률": return_pct,
        })
    total_value = sum(row["평가금"] or 0 for row in interim)
    total_invested = sum(row["투자금"] for row in interim)
    estimated_values: list[float] = []
    for row in interim:
        price = row["현재가"]
        if price is None or price <= 0:
            add_shares = None
            estimated_value = None
        else:
            # Match Excel's approach: invested capital × target weight / price − shares.
            desired_shares = math.trunc((total_invested * row["목표비중"]) / price)
            add_shares = desired_shares - row["수량"]
            estimated_value = price * (row["수량"] + add_shares)
        row["현재비중"] = (row["평가금"] / total_value) if total_value > 0 and row["평가금"] is not None else None
        row["목표비중차이"] = row["목표비중"] - row["현재비중"] if row["현재비중"] is not None else None
        row["리밸런싱금액"] = row["목표비중차이"] * total_value if row["목표비중차이"] is not None else None
        row["월매수량"] = row["월투자금"] / price if price and price > 0 else None
        row["추가주문수"] = add_shares
        row["예상평가액"] = estimated_value
        estimated_values.append(estimated_value or 0)
    estimated_total = sum(estimated_values)
    for row in interim:
        row["예상비중"] = (row["예상평가액"] / estimated_total) if estimated_total > 0 and row["예상평가액"] is not None else None
    return pd.DataFrame(interim)


def portfolio_summary(holdings_df: pd.DataFrame) -> dict[str, float]:
    if holdings_df.empty:
        return {"market_value": 0.0, "invested": 0.0, "profit": 0.0, "return_pct": 0.0}
    invested = float(holdings_df["투자금"].fillna(0).sum())
    market_value = float(holdings_df["평가금"].fillna(0).sum())
    profit = market_value - invested
    return {"market_value": market_value, "invested": invested, "profit": profit, "return_pct": profit / invested if invested else 0.0}
