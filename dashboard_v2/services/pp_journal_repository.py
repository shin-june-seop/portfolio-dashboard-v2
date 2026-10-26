"""User-scoped, append-only Supabase repository for V2 PP signals and trade fills.

Only the dedicated v2_pp_* tables are accessed. Legacy tables are never read or
written. Journal events are immutable; corrections require a future explicit
correction workflow instead of silent edits.
"""
from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Mapping, Sequence

from .watchlist_repository import normalize_ticker

PP_SIGNALS_TABLE = "v2_pp_signals"
PP_TRADES_TABLE = "v2_pp_trades"
PP_ADDS_TABLE = "v2_pp_trade_adds"
PP_EXITS_TABLE = "v2_pp_trade_exits"

ENTRY_STAGES = (
    "BASE", "BASE + PP", "PIVOT APPROACH", "BREAKOUT",
    "BREAKOUT CONFIRMED", "BREAKOUT FAILURE",
)
EXIT_REASONS = (
    "BREAKOUT_FAILURE", "INITIAL_STOP", "TRAILING_STOP", "TAKE_PROFIT",
    "MANUAL", "OTHER",
)


class PPJournalValidationError(ValueError):
    """Raised when a journal event is invalid or violates trade chronology."""


def _iso_date(value: Any, field: str) -> str:
    try:
        if isinstance(value, datetime):
            out = value.date()
        elif isinstance(value, date):
            out = value
        else:
            out = date.fromisoformat(str(value)[:10])
        return out.isoformat()
    except (TypeError, ValueError) as exc:
        raise PPJournalValidationError(f"{field} 날짜가 올바르지 않습니다.") from exc


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PPJournalValidationError(f"{field}은(는) 숫자로 입력해 주세요.") from exc
    if not math.isfinite(number) or number <= 0:
        raise PPJournalValidationError(f"{field}은(는) 0보다 커야 합니다.")
    return number


def _optional_positive_number(value: Any, field: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return _positive_number(value, field)


def _optional_finite_number(value: Any, field: str) -> float | None:
    """Optional finite numeric snapshot value; missing analytics never blocks a signal."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PPJournalValidationError(f"{field}은(는) 숫자로 입력해 주세요.") from exc
    if not math.isfinite(number):
        raise PPJournalValidationError(f"{field}은(는) 유한한 숫자여야 합니다.")
    return number


def _snapshot_metrics(row: Mapping[str, Any]) -> dict[str, Any]:
    rs = _optional_finite_number(row.get("rs_rating"), "RS")
    momentum = _optional_finite_number(row.get("momentum_score"), "Momentum")
    pivot_dist = _optional_finite_number(row.get("pivot_dist_pct"), "Pivot 거리")
    if rs is not None and not 0 <= rs <= 100:
        raise PPJournalValidationError("RS는 0~100 범위여야 합니다.")
    if momentum is not None and not 0 <= momentum <= 100:
        raise PPJournalValidationError("Momentum은 0~100 범위여야 합니다.")
    quality = str(row.get("pp_quality") or "").strip().upper() or None
    if quality is not None and quality not in {"A", "B", "C"}:
        raise PPJournalValidationError("PP Quality는 A/B/C 중 하나여야 합니다.")
    market_regime = _text(row.get("market_regime"), "Market Regime", 100) or None
    return {
        "rs_rating": rs,
        "momentum_score": momentum,
        "pp_quality": quality,
        "market_regime": market_regime,
        "pivot_dist_pct": pivot_dist,
    }


def _nonnegative_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PPJournalValidationError(f"{field}은(는) 숫자로 입력해 주세요.") from exc
    if not math.isfinite(number) or number < 0:
        raise PPJournalValidationError(f"{field}은(는) 0 이상이어야 합니다.")
    return number


def _optional_weight(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PPJournalValidationError("포트폴리오 비중은 숫자로 입력해 주세요.") from exc
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise PPJournalValidationError("각 매수 비중은 0~100% 범위여야 합니다.")
    return number


def _text(value: Any, field: str, max_length: int) -> str:
    result = str(value or "").strip()
    if len(result) > max_length:
        raise PPJournalValidationError(f"{field}은(는) {max_length}자 이내로 입력해 주세요.")
    return result


def normalize_pp_signal(row: Mapping[str, Any], *, user_id: str) -> dict[str, Any]:
    """Validate a *valid* PP event snapshot before persistence."""
    ticker = normalize_ticker(row.get("ticker"))
    valid_signal = row.get("valid_signal", True)
    if not bool(valid_signal):
        raise PPJournalValidationError("유효하지 않은 PP 진단은 신호 일지에 저장할 수 없습니다.")
    reference_ma = row.get("reference_ma")
    if reference_ma not in (None, "", "MA10", "MA50"):
        raise PPJournalValidationError("참조 이동평균선은 MA10 또는 MA50이어야 합니다.")
    candidate = _optional_positive_number(row.get("candidate_pivot"), "Candidate Pivot")
    breakout = _optional_positive_number(row.get("breakout_pivot"), "Breakout Pivot")
    signal_volume = _positive_number(row.get("signal_volume"), "신호일 거래량")
    max_down = _nonnegative_number(row.get("max_prior_down_day_volume"), "직전 하락일 최대 거래량")
    context_valid = bool(row.get("context_valid", True))
    if not context_valid:
        raise PPJournalValidationError("건설적 추세/베이스 문맥을 통과하지 못한 PP는 저장할 수 없습니다.")
    snapshot_metrics = _snapshot_metrics(row)
    return {
        "user_id": str(user_id),
        "ticker": ticker,
        "signal_date": _iso_date(row.get("signal_date"), "신호일"),
        "signal_price": _positive_number(row.get("signal_price"), "신호일 가격"),
        "signal_volume": signal_volume,
        "max_prior_down_day_volume": max_down,
        "reference_ma": reference_ma or None,
        "pattern_name": _text(row.get("pattern_name"), "패턴", 100) or "BASE FORMING / NO VALID PATTERN",
        "entry_stage": _text(row.get("entry_stage"), "Stage", 40) or "BASE",
        "candidate_pivot": candidate,
        "breakout_pivot": breakout,
        "context_valid": bool(row.get("context_valid", True)),
        "reason_codes": list(row.get("reason_codes") or ["POCKET_PIVOT_VALID"]),
        **snapshot_metrics,
    }


def save_pp_signals(client: Any, user_id: str, signals: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Insert missing (user, ticker, date) signals only; never overwrite a snapshot."""
    normalized = [normalize_pp_signal(row, user_id=user_id) for row in signals]
    submitted_count = len(normalized)
    # Protect against duplicates inside the submitted batch before querying PostgREST.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in normalized:
        unique[(row["ticker"], row["signal_date"])] = row
    normalized = list(unique.values())
    if not normalized:
        return {"inserted": 0, "duplicates": 0}

    existing_keys: set[tuple[str, str]] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in normalized:
        groups.setdefault(row["signal_date"], []).append(row)
    for signal_date, rows in groups.items():
        tickers = list(dict.fromkeys(row["ticker"] for row in rows))
        response = (
            client.table(PP_SIGNALS_TABLE)
            .select("ticker,signal_date")
            .eq("user_id", str(user_id))
            .eq("signal_date", signal_date)
            .in_("ticker", tickers)
            .execute()
        )
        for item in (getattr(response, "data", None) or []):
            existing_keys.add((str(item.get("ticker", "")).upper(), str(item.get("signal_date", ""))[:10]))

    missing = [row for row in normalized if (row["ticker"], row["signal_date"]) not in existing_keys]
    if missing:
        try:
            response = client.table(PP_SIGNALS_TABLE).insert(missing).execute()
        except Exception as exc:
            message = str(exc).lower()
            snapshot_columns = ("rs_rating", "momentum_score", "pp_quality", "market_regime", "pivot_dist_pct")
            is_snapshot_schema_mismatch = "schema cache" in message or "does not exist" in message or (
                "column" in message and any(column in message for column in snapshot_columns)
            )
            if not is_snapshot_schema_mismatch:
                raise
            legacy_missing = [
                {key: value for key, value in row.items() if key not in snapshot_columns}
                for row in missing
            ]
            response = client.table(PP_SIGNALS_TABLE).insert(legacy_missing).execute()
        returned = getattr(response, "data", None)
        inserted = len(returned) if returned is not None else len(missing)
    else:
        inserted = 0
    return {"inserted": inserted, "duplicates": submitted_count - inserted}


def list_pp_signals(client: Any, user_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    if not 1 <= int(limit) <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    base_columns = (
        "id,user_id,ticker,signal_date,signal_price,signal_volume,max_prior_down_day_volume,"
        "reference_ma,pattern_name,entry_stage,candidate_pivot,breakout_pivot,context_valid,reason_codes,created_at"
    )
    try:
        response = (
            client.table(PP_SIGNALS_TABLE)
            .select(base_columns + ",rs_rating,momentum_score,pp_quality,market_regime,pivot_dist_pct")
            .eq("user_id", str(user_id))
            .order("signal_date", desc=True)
            .order("ticker")
            .limit(int(limit))
            .execute()
        )
        rows = [dict(row) for row in (getattr(response, "data", None) or [])]
    except Exception as exc:
        message = str(exc).lower()
        snapshot_columns = ("rs_rating", "momentum_score", "pp_quality", "market_regime", "pivot_dist_pct")
        if not ("schema cache" in message or "does not exist" in message or ("column" in message and any(col in message for col in snapshot_columns))):
            raise
        response = (
            client.table(PP_SIGNALS_TABLE)
            .select(base_columns)
            .eq("user_id", str(user_id))
            .order("signal_date", desc=True)
            .order("ticker")
            .limit(int(limit))
            .execute()
        )
        rows = [dict(row) for row in (getattr(response, "data", None) or [])]
        for row in rows:
            row.update({"rs_rating": None, "momentum_score": None, "pp_quality": None, "market_regime": None, "pivot_dist_pct": None})
    return rows


def _normalize_entry(
    *, entry_date: Any, entry_price: Any, shares: Any,
    portfolio_weight_pct: Any = None, entry_stage: Any = "BASE",
    initial_stop_price: Any = None, note: Any = "",
) -> dict[str, Any]:
    stage = str(entry_stage or "BASE").strip().upper()
    if stage not in ENTRY_STAGES:
        raise PPJournalValidationError("최초 진입 Stage를 목록에서 선택해 주세요.")
    price = _positive_number(entry_price, "진입 가격")
    stop = _optional_positive_number(initial_stop_price, "초기 손절선")
    if stop is not None and stop >= price:
        raise PPJournalValidationError("초기 손절선은 해당 진입 가격보다 낮아야 합니다.")
    return {
        "entry_date": _iso_date(entry_date, "진입일"),
        "entry_price": price,
        "shares": _positive_number(shares, "수량"),
        "portfolio_weight_pct": _optional_weight(portfolio_weight_pct),
        "entry_stage": stage,
        "initial_stop_price": stop,
        "note": _text(note, "메모", 1000),
    }


def create_pp_trade(
    client: Any, user_id: str, *, ticker: Any, pp_signal_id: Any = None,
    initial_entry_date: Any, initial_entry_price: Any, initial_shares: Any,
    initial_weight_pct: Any = None, initial_entry_stage: Any = "BASE",
    initial_stop_price: Any = None, notes: Any = "",
) -> dict[str, Any]:
    key = normalize_ticker(ticker)
    entry = _normalize_entry(
        entry_date=initial_entry_date, entry_price=initial_entry_price,
        shares=initial_shares, portfolio_weight_pct=initial_weight_pct,
        entry_stage=initial_entry_stage, initial_stop_price=initial_stop_price,
    )
    signal_id = str(pp_signal_id).strip() if pp_signal_id else None
    if signal_id:
        signal_response = (
            client.table(PP_SIGNALS_TABLE)
            .select("id,ticker")
            .eq("user_id", str(user_id))
            .eq("id", signal_id)
            .limit(1)
            .execute()
        )
        signal_rows = getattr(signal_response, "data", None) or []
        if not signal_rows:
            raise PPJournalValidationError("연결할 PP 신호를 찾지 못했거나 접근 권한이 없습니다.")
        if str(signal_rows[0].get("ticker", "")).upper() != key:
            raise PPJournalValidationError("거래 종목과 연결할 PP 신호의 종목이 다릅니다.")

    payload = {
        "user_id": str(user_id), "ticker": key, "pp_signal_id": signal_id,
        "initial_entry_date": entry["entry_date"],
        "initial_entry_price": entry["entry_price"],
        "initial_shares": entry["shares"],
        "initial_weight_pct": entry["portfolio_weight_pct"],
        "initial_entry_stage": entry["entry_stage"],
        "initial_stop_price": entry["initial_stop_price"],
        "notes": _text(notes, "거래 메모", 1500),
    }
    response = client.table(PP_TRADES_TABLE).insert(payload).select(
        "id,user_id,ticker,pp_signal_id,initial_entry_date,initial_entry_price,initial_shares,"
        "initial_weight_pct,initial_entry_stage,initial_stop_price,notes,created_at"
    ).execute()
    data = getattr(response, "data", None) or []
    if not data:
        raise RuntimeError("초기 진입 기록의 저장 결과를 확인할 수 없습니다.")
    return dict(data[0])


def list_pp_trades(client: Any, user_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    if not 1 <= int(limit) <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    response = (
        client.table(PP_TRADES_TABLE)
        .select(
            "id,user_id,ticker,pp_signal_id,initial_entry_date,initial_entry_price,initial_shares,"
            "initial_weight_pct,initial_entry_stage,initial_stop_price,notes,created_at"
        )
        .eq("user_id", str(user_id))
        .order("initial_entry_date", desc=True)
        .order("created_at", desc=True)
        .limit(int(limit))
        .execute()
    )
    return [dict(row) for row in (getattr(response, "data", None) or [])]


def list_trade_adds(client: Any, user_id: str, trade_ids: Sequence[str]) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(str(x) for x in trade_ids if x))
    if not ids:
        return []
    response = (
        client.table(PP_ADDS_TABLE)
        .select("id,user_id,trade_id,entry_date,entry_price,shares,portfolio_weight_pct,entry_stage,initial_stop_price,note,created_at")
        .eq("user_id", str(user_id))
        .in_("trade_id", ids)
        .order("entry_date")
        .order("created_at")
        .execute()
    )
    return [dict(row) for row in (getattr(response, "data", None) or [])]


def list_trade_exits(client: Any, user_id: str, trade_ids: Sequence[str]) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(str(x) for x in trade_ids if x))
    if not ids:
        return []
    response = (
        client.table(PP_EXITS_TABLE)
        .select("id,user_id,trade_id,exit_date,exit_price,shares,reason_code,note,created_at")
        .eq("user_id", str(user_id))
        .in_("trade_id", ids)
        .order("exit_date")
        .order("created_at")
        .execute()
    )
    return [dict(row) for row in (getattr(response, "data", None) or [])]


def add_pp_trade_add(
    client: Any, user_id: str, trade: Mapping[str, Any], *,
    entry_date: Any, entry_price: Any, shares: Any,
    portfolio_weight_pct: Any = None, entry_stage: Any = "BASE",
    initial_stop_price: Any = None, note: Any = "",
) -> dict[str, Any]:
    entry = _normalize_entry(
        entry_date=entry_date, entry_price=entry_price, shares=shares,
        portfolio_weight_pct=portfolio_weight_pct, entry_stage=entry_stage,
        initial_stop_price=initial_stop_price, note=note,
    )
    if entry["entry_date"] < str(trade.get("initial_entry_date", ""))[:10]:
        raise PPJournalValidationError("추가매수일은 최초 진입일보다 빠를 수 없습니다.")
    payload = {"user_id": str(user_id), "trade_id": str(trade["id"]), **entry}
    response = client.table(PP_ADDS_TABLE).insert(payload).select(
        "id,user_id,trade_id,entry_date,entry_price,shares,portfolio_weight_pct,entry_stage,initial_stop_price,note,created_at"
    ).execute()
    data = getattr(response, "data", None) or []
    if not data:
        raise RuntimeError("추가매수 기록을 저장했는지 확인할 수 없습니다.")
    return dict(data[0])


def _entries_as_of(trade: Mapping[str, Any], adds: Sequence[Mapping[str, Any]], cutoff: str) -> list[tuple[str, float]]:
    entries: list[tuple[str, float]] = []
    initial_date = str(trade.get("initial_entry_date", ""))[:10]
    if initial_date and initial_date <= cutoff:
        entries.append((initial_date, float(trade.get("initial_shares", 0))))
    for row in adds:
        entry_date = str(row.get("entry_date", ""))[:10]
        if entry_date and entry_date <= cutoff:
            entries.append((entry_date, float(row.get("shares", 0))))
    return entries


def add_pp_trade_exit(
    client: Any, user_id: str, trade: Mapping[str, Any], *,
    exits: Sequence[Mapping[str, Any]], adds: Sequence[Mapping[str, Any]],
    exit_date: Any, exit_price: Any, shares: Any, reason_code: Any = "MANUAL", note: Any = "",
) -> dict[str, Any]:
    event_date = _iso_date(exit_date, "청산일")
    price = _positive_number(exit_price, "청산 가격")
    quantity = _positive_number(shares, "청산 수량")
    reason = str(reason_code or "MANUAL").strip().upper()
    if reason not in EXIT_REASONS:
        raise PPJournalValidationError("청산 사유를 목록에서 선택해 주세요.")
    if event_date < str(trade.get("initial_entry_date", ""))[:10]:
        raise PPJournalValidationError("청산일은 최초 진입일보다 빠를 수 없습니다.")

    # Enforce available shares as-of the exit date, not merely total lifetime shares.
    entry_rows = _entries_as_of(trade, adds, event_date)
    prior_exit_rows = [row for row in exits if str(row.get("exit_date", ""))[:10] <= event_date]
    acquired = sum(qty for _, qty in entry_rows)
    sold = sum(float(row.get("shares", 0)) for row in prior_exit_rows)
    available = max(0.0, acquired - sold)
    tolerance = max(1e-8, acquired * 1e-8)
    if quantity > available + tolerance:
        raise PPJournalValidationError(
            f"청산 수량({quantity:g})이 해당 날짜 기준 잔여 수량({available:g})보다 많습니다."
        )

    payload = {
        "user_id": str(user_id), "trade_id": str(trade["id"]),
        "exit_date": event_date, "exit_price": price, "shares": quantity,
        "reason_code": reason, "note": _text(note, "청산 메모", 1000),
    }
    response = client.table(PP_EXITS_TABLE).insert(payload).select(
        "id,user_id,trade_id,exit_date,exit_price,shares,reason_code,note,created_at"
    ).execute()
    data = getattr(response, "data", None) or []
    if not data:
        raise RuntimeError("청산 기록을 저장했는지 확인할 수 없습니다.")
    return dict(data[0])
