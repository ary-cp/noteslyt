"""
Phase 5 verification — first-run setup flag + endpoints, in-process.

Uses fastapi.testclient against a scratch `noteslyt_sutest` database with
cloud/no-op AI endpoints (pinned dim, no downloads, no port conflicts):

    uv run python scripts/verify_setup.py

Covers:
  * fresh DB has no setup flag (migration 0009 writes nothing without users)
  * first signup is admin; setup status needs auth (401 anonymous)
  * admin sees setup_completed=False; second user sees False + is_admin=False
  * POST /system/setup/complete: non-admin -> 403, admin -> 200 + persisted
  * status flips to completed; settings PATCH still works afterwards
  * migration 0009 backfill: exact SQL file sets the flag when users exist,
    writes nothing on an empty database

Exits non-zero on the first failure. Drops the scratch databases afterwards.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

TEST_DB = "noteslyt_sutest"
TEST_DB2 = "noteslyt_sutest2"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def load_dotenv(path: str = ".env") -> None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().strip("'\""), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


def pg_dsn() -> str:
    """Maint DSN for scratch databases. Needs Postgres — pass a DSN explicitly,
    e.g. $env:DATABASE_URL='postgresql://postgres:...@localhost:5432/StudyCompanion'."""
    base = os.environ.get("DATABASE_URL")
    if not base or base.strip().lower().startswith("sqlite"):
        raise SystemExit(
            "verify_setup needs a PostgreSQL DSN in DATABASE_URL "
            "(it creates + drops scratch databases)."
        )
    return base


def maint_dsn() -> str:
    return pg_dsn().rsplit("/", 1)[0] + "/postgres"


def test_dsn(name: str) -> str:
    return pg_dsn().rsplit("/", 1)[0] + f"/{name}"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    import psycopg  # noqa: E402

    for name in (TEST_DB, TEST_DB2):
        with psycopg.connect(maint_dsn(), autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
            c.execute(f"create database {name}")
    print(f"setup        : created databases {TEST_DB}, {TEST_DB2}")

    os.environ["DATABASE_URL"] = test_dsn(TEST_DB)
    os.environ["EMBEDDING_MODE"] = "cloud"
    os.environ["EMBEDDING_DIM"] = "768"
    os.environ["TRANSCRIPTION_MODE"] = "cloud"

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.main import app  # noqa: E402 — after env override
    from fastapi.testclient import TestClient  # noqa: E402

    with TestClient(app) as c:
        print("fresh install flow")
        a = c.post("/auth/signup", json={"email": f"a-{tag}@t.co",
                                         "password": "correct horse battery",
                                         "display_name": "A"}).json()
        u = c.post("/auth/signup", json={"email": f"u-{tag}@t.co",
                                         "password": "correct horse battery",
                                         "display_name": "U"}).json()
        admin_tok, user_tok = a["access_token"], u["access_token"]
        check("first user admin", a["user"]["is_admin"] is True)

        r = c.get("/system/setup/status")
        check("status needs auth", r.status_code == 401, str(r.status_code))
        st = c.get("/system/setup/status", headers=auth(admin_tok)).json()
        check("fresh setup not completed", st["setup_completed"] is False, str(st))
        check("status reports admin", st["is_admin"] is True, str(st))
        st2 = c.get("/system/setup/status", headers=auth(user_tok)).json()
        check("second user not admin", st2["is_admin"] is False, str(st2))

        r = c.post("/system/setup/complete", headers=auth(user_tok))
        check("non-admin complete -> 403", r.status_code == 403, str(r.status_code))
        r = c.post("/system/setup/complete", headers=auth(admin_tok))
        check("admin complete -> 200", r.status_code == 200 and r.json().get("completed") is True,
              r.text[:160])
        st = c.get("/system/setup/status", headers=auth(admin_tok)).json()
        check("status flips to completed", st["setup_completed"] is True, str(st))

        print("\nsettings still work after setup")
        items = c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]
        unlocked = next((i for i in items
                         if not i["locked"] and not i["secret"] and isinstance(i["value"], int)),
                        None)
        check("an unlocked int key exists", unlocked is not None)
        key, old = unlocked["key"], unlocked["value"]
        r = c.patch("/system/settings", headers=auth(admin_tok), json={key: old + 1})
        check("PATCH 200", r.status_code == 200, r.text[:160])
        c.patch("/system/settings", headers=auth(admin_tok), json={key: None})

    print("\nmigration 0009 backfill (exact SQL file)")
    sql = Path(__file__).resolve().parents[1] / "migrations" / "0009_setup_flag.sql"
    check("0009 file exists", sql.is_file())
    body = sql.read_text(encoding="utf-8")
    with psycopg.connect(test_dsn(TEST_DB2)) as conn:
        with conn.transaction():
            conn.execute("create table if not exists users (id text primary key)")
            conn.execute("create table if not exists system_meta (key text primary key, "
                         "value text not null)")
        _flag = lambda: conn.execute(  # noqa: E731
            "select value from system_meta where key = 'setup.completed'").fetchone()
        with conn.transaction():
            conn.execute(body)
        check("empty db: no flag written", _flag() is None)
        with conn.transaction():
            conn.execute("insert into users (id) values ('u1')")
            conn.execute(body)
        row = _flag()
        check("users present: flag backfilled", row is not None and row[0] == "true",
              str(row))
        with conn.transaction():
            conn.execute(body)
        n = conn.execute("select count(*) from system_meta "
                         "where key = 'setup.completed'").fetchone()[0]
        check("re-run idempotent", n == 1, str(n))

    print("\nSETUP VERIFIED")
    return 0


if __name__ == "__main__":
    load_dotenv()
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001 — always report, then clean up
        print(f"ERROR: {type(exc).__name__}: {exc}")
        code = 1
    finally:
        try:
            import psycopg as _pg

            with _pg.connect(maint_dsn(), autocommit=True) as _c:
                for _name in (TEST_DB, TEST_DB2):
                    _c.execute(f"drop database if exists {_name} with (force)")
            print("cleanup      : dropped scratch databases")
        except Exception as exc:  # noqa: BLE001
            print(f"  cleanup warning: {exc}")
    raise SystemExit(code)
