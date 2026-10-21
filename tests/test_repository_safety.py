import pytest
from dashboard_v2.repositories.supabase_repository import RepositoryDisabled, SupabaseReadOnlyRepository


def test_supabase_access_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("V2_ENABLE_SUPABASE_READ_ONLY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    with pytest.raises(RepositoryDisabled, match="disabled"):
        SupabaseReadOnlyRepository.from_environment()


def test_supabase_access_requires_credentials_when_opted_in(monkeypatch):
    monkeypatch.setenv("V2_ENABLE_SUPABASE_READ_ONLY", "true")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    with pytest.raises(RepositoryDisabled, match="SUPABASE_URL"):
        SupabaseReadOnlyRepository.from_environment()


def test_table_identifier_and_limit_are_validated_before_query():
    class FakeClient:
        def table(self, _):
            raise AssertionError("Query must not execute for invalid arguments")
    repo = SupabaseReadOnlyRepository(FakeClient())
    with pytest.raises(ValueError, match="identifier"):
        repo.select_rows("bad table;delete")
    with pytest.raises(ValueError, match="limit"):
        repo.select_rows("transactions", limit=0)
