"""Explicitly opt-in, read-only Supabase adapter. No write methods by design."""
from __future__ import annotations
import os
from typing import Any


class RepositoryDisabled(RuntimeError):
    pass


class SupabaseReadOnlyRepository:
    """Thin read-only adapter; table-specific mappings wait for schema inventory."""
    def __init__(self, client: Any):
        self._client = client

    @classmethod
    def from_environment(cls) -> "SupabaseReadOnlyRepository":
        enabled = os.getenv("V2_ENABLE_SUPABASE_READ_ONLY", "false").strip().lower() == "true"
        if not enabled:
            raise RepositoryDisabled("Supabase access is disabled; opt in only after schema inventory")
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_KEY", "").strip()
        if not url or not key:
            raise RepositoryDisabled("SUPABASE_URL and SUPABASE_KEY are required")
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RepositoryDisabled("Install the optional database dependency: pip install -e '.[database]'") from exc
        return cls(create_client(url, key))

    def select_rows(self, table: str, *, columns: str = "*", limit: int = 1000) -> list[dict[str, Any]]:
        """Read rows only; table allowlisting is added after DB inventory."""
        if not table or not table.replace("_", "").isalnum():
            raise ValueError("Invalid table identifier")
        if not 1 <= int(limit) <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        response = self._client.table(table).select(columns).limit(int(limit)).execute()
        data = getattr(response, "data", None)
        if data is None:
            raise RuntimeError(f"Supabase returned no data payload for read from {table}")
        return list(data)
