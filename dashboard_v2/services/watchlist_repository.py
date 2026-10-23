"""User-scoped CRUD for the clean V2 watchlist.

This module never reads or writes legacy tables. Supabase is imported lazily so
pure validation tests can run without network credentials or a DB connection.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

TABLE_NAME = "v2_watchlist"
CLASSIFICATIONS = ("WATCH", "CORE", "SATELLITE")
TICKER_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=\-]{0,14}$")


class WatchlistValidationError(ValueError):
    """Invalid user-provided watchlist data."""


def normalize_ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker:
        raise WatchlistValidationError("티커를 입력해 주세요.")
    if not TICKER_PATTERN.fullmatch(ticker):
        raise WatchlistValidationError(
            "티커 형식이 올바르지 않습니다. 예: NVDA, BRK-B, BRK.B, ^GSPC, ES=F"
        )
    return ticker


def normalize_watchlist_fields(
    *, ticker: Any, sector: Any = "", classification: Any = "WATCH",
    note: Any = "", enabled: Any = True,
) -> dict[str, Any]:
    normalized_ticker = normalize_ticker(ticker)
    sector_value = str(sector or "").strip() or "미분류"
    if len(sector_value) > 80:
        raise WatchlistValidationError("섹터는 80자 이내로 입력해 주세요.")
    classification_value = str(classification or "WATCH").strip().upper()
    if classification_value not in CLASSIFICATIONS:
        raise WatchlistValidationError("분류는 WATCH, CORE, SATELLITE 중에서 선택해 주세요.")
    note_value = str(note or "").strip()
    if len(note_value) > 500:
        raise WatchlistValidationError("메모는 500자 이내로 입력해 주세요.")
    return {
        "ticker": normalized_ticker,
        "sector": sector_value,
        "classification": classification_value,
        "note": note_value,
        "enabled": bool(enabled),
    }


def active_tickers(rows: list[Mapping[str, Any]]) -> list[str]:
    """Return unique enabled tickers in displayed order."""
    out: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not bool(row.get("enabled", True)):
            continue
        try:
            ticker = normalize_ticker(row.get("ticker"))
        except WatchlistValidationError:
            continue
        if ticker not in seen:
            out.append(ticker)
            seen.add(ticker)
    return out


def create_supabase_client(url: str, anon_key: str):
    """Create a client using only the public anon/publishable key, never service_role."""
    clean_url, clean_key = str(url or "").strip(), str(anon_key or "").strip()
    if not clean_url or not clean_key:
        raise RuntimeError("V2 Supabase URL/anon key가 설정되지 않았습니다.")
    from supabase import create_client  # lazy import by design
    return create_client(clean_url, clean_key)


def list_watchlist(client, user_id: str) -> list[dict[str, Any]]:
    response = (
        client.table(TABLE_NAME)
        .select("id,user_id,ticker,sector,classification,note,enabled,created_at,updated_at")
        .eq("user_id", str(user_id))
        .order("ticker")
        .execute()
    )
    data = getattr(response, "data", None) or []
    return [dict(row) for row in data]


def add_watchlist_item(client, user_id: str, **fields: Any) -> dict[str, Any]:
    payload = normalize_watchlist_fields(**fields)
    payload["user_id"] = str(user_id)
    response = client.table(TABLE_NAME).insert(payload).select(
        "id,user_id,ticker,sector,classification,note,enabled,created_at,updated_at"
    ).execute()
    data = getattr(response, "data", None) or []
    if not data:
        raise RuntimeError("관심종목 저장 결과를 확인할 수 없습니다. 새로고침 후 확인해 주세요.")
    return dict(data[0])


def update_watchlist_item(client, user_id: str, ticker: str, **fields: Any) -> dict[str, Any]:
    key = normalize_ticker(ticker)
    payload = normalize_watchlist_fields(ticker=key, **fields)
    payload.pop("ticker", None)  # ticker identity cannot be changed through an edit action
    response = (
        client.table(TABLE_NAME)
        .update(payload)
        .eq("user_id", str(user_id))
        .eq("ticker", key)
        .select("id,user_id,ticker,sector,classification,note,enabled,created_at,updated_at")
        .execute()
    )
    data = getattr(response, "data", None) or []
    if not data:
        raise RuntimeError("수정할 종목을 찾지 못했습니다. 목록을 새로고침해 주세요.")
    return dict(data[0])


def delete_watchlist_item(client, user_id: str, ticker: str) -> None:
    key = normalize_ticker(ticker)
    (
        client.table(TABLE_NAME)
        .delete()
        .eq("user_id", str(user_id))
        .eq("ticker", key)
        .execute()
    )
