"""Admin-managed settings + machine-managed metadata (both key → text).

`app_settings` rows are written by admins via PATCH /system/settings and read
by `apply_db_overrides()` at boot. `system_meta` rows are written by the app
itself (drift ground truth); admins never write them directly.
"""

from __future__ import annotations

from .base import Repo


class AppSettingsRepo(Repo):
    def get_all(self) -> dict[str, str]:
        rows = self._fetch_all("select key, value from app_settings")
        return {r["key"]: r["value"] for r in rows}

    def get(self, key: str) -> str | None:
        row = self._fetch_one("select value from app_settings where key = %s", (key,))
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "insert into app_settings (key, value, updated_at) values (%s, %s, now()) "
                "on conflict (key) do update set value = excluded.value, updated_at = now()",
                (key, value),
            )

    def set_many(self, items: dict[str, str]) -> None:
        """Write several keys atomically — a PATCH never half-applies."""
        if not items:
            return
        with self.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into app_settings (key, value, updated_at) values (%s, %s, now()) "
                    "on conflict (key) do update set value = excluded.value, updated_at = now()",
                    [(k, v) for k, v in items.items()],
                )

    def delete(self, key: str) -> None:
        self._execute("delete from app_settings where key = %s", (key,))


class SystemMetaRepo(Repo):
    def get_all(self) -> dict[str, str]:
        rows = self._fetch_all("select key, value from system_meta")
        return {r["key"]: r["value"] for r in rows}

    def get(self, key: str) -> str | None:
        row = self._fetch_one("select value from system_meta where key = %s", (key,))
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "insert into system_meta (key, value, updated_at) values (%s, %s, now()) "
                "on conflict (key) do update set value = excluded.value, updated_at = now()",
                (key, value),
            )


__all__ = ["AppSettingsRepo", "SystemMetaRepo"]
