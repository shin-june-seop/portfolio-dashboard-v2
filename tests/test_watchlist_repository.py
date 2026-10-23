import pytest

from dashboard_v2.services.watchlist_repository import (
    WatchlistValidationError,
    active_tickers,
    normalize_ticker,
    normalize_watchlist_fields,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" nvda ", "NVDA"), ("BRK-B", "BRK-B"), ("brk.b", "BRK.B"), ("^gspc", "^GSPC"), ("es=f", "ES=F")],
)
def test_normalize_ticker_accepts_supported_symbols(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "NVDA, MU", "A B", "../../x", "A" * 16, "NVDA;DELETE"])
def test_normalize_ticker_rejects_invalid_input(raw):
    with pytest.raises(WatchlistValidationError):
        normalize_ticker(raw)


def test_normalize_watchlist_fields_defaults_and_normalizes():
    row = normalize_watchlist_fields(ticker=" goog ", sector="", classification="core", note=" long-term ", enabled=True)
    assert row == {
        "ticker": "GOOG",
        "sector": "미분류",
        "classification": "CORE",
        "note": "long-term",
        "enabled": True,
    }


def test_normalize_watchlist_fields_rejects_invalid_classification_and_long_text():
    with pytest.raises(WatchlistValidationError):
        normalize_watchlist_fields(ticker="GOOG", classification="BUY")
    with pytest.raises(WatchlistValidationError):
        normalize_watchlist_fields(ticker="GOOG", note="x" * 501)
    with pytest.raises(WatchlistValidationError):
        normalize_watchlist_fields(ticker="GOOG", sector="x" * 81)


def test_active_tickers_uses_only_enabled_unique_items():
    rows = [
        {"ticker": "nvda", "enabled": True},
        {"ticker": "MU", "enabled": False},
        {"ticker": "NVDA", "enabled": True},
        {"ticker": "GOOG", "enabled": True},
    ]
    assert active_tickers(rows) == ["NVDA", "GOOG"]


def test_repository_queries_are_scoped_to_authenticated_user():
    from dashboard_v2.services.watchlist_repository import list_watchlist, delete_watchlist_item

    calls = []

    class Response:
        data = [{"ticker": "NVDA", "user_id": "user-123", "enabled": True}]

    class Query:
        def __init__(self):
            self.filters = []
        def select(self, columns):
            calls.append(("select", columns)); return self
        def eq(self, key, value):
            self.filters.append((key, value)); calls.append(("eq", key, value)); return self
        def order(self, key):
            calls.append(("order", key)); return self
        def delete(self):
            calls.append(("delete",)); return self
        def execute(self):
            calls.append(("execute", tuple(self.filters))); return Response()

    class FakeClient:
        def table(self, name):
            calls.append(("table", name)); return Query()

    rows = list_watchlist(FakeClient(), "user-123")
    assert rows[0]["ticker"] == "NVDA"
    assert ("eq", "user_id", "user-123") in calls

    calls.clear()
    delete_watchlist_item(FakeClient(), "user-123", "nvda")
    assert ("eq", "user_id", "user-123") in calls
    assert ("eq", "ticker", "NVDA") in calls


def test_sql_schema_uses_rls_and_never_grants_anon_access():
    from pathlib import Path
    schema = (Path(__file__).resolve().parents[1] / "sql" / "v2_watchlist_schema.sql").read_text()
    assert "enable row level security" in schema.lower()
    assert "auth.uid() = user_id" in schema
    assert "to authenticated" in schema
    assert "to anon" not in schema.lower()
