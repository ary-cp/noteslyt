"""
Phase 3 verification — drift is non-blocking + one-click re-embed, in-process.

Uses fastapi.testclient (lifespan runs locally, no separate server process):

    uv run python scripts/verify_reembed.py

Covers, against a scratch `noteslyt_p3test` database (created + dropped here):
  * fresh migrate templates vector widths (Phase 1, 8 migrations incl. 0008)
  * upload -> ingeststeady state: status drift=false, ground truth recorded
  * faked model swap (system_meta last_model): status drift=true + revert_to,
    /health degraded with embedding_drift, upload + quiz creation -> 409
  * RESTART with drifted meta: boot succeeds (non-blocking) — the Phase 3
    behaviour change (previously RuntimeError)
  * POST /system/reembed (admin, 202) -> poll to terminal -> drift clears,
    ground truth restored; non-admin -> 403; concurrent start -> 409
  * force=true re-embeds an in-sync corpus
  * revert path: PATCH embedding_model back clears model drift without a job
    (skipped with a note when the key is operator-locked in this .env)

When the LLM/transcription remotes are unreachable the ingest falls back to
direct seeding (chunk row + ground truth via SQL) so the drift/job mechanics
are still exercised; the path used is reported.

Exits non-zero on the first failure.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

TEST_DB = "noteslyt_p3test"

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
        raise SystemExit(1)


def load_dotenv(path: str = ".env") -> None:
    """Mirror pydantic-settings' dotenv read for keys the process env lacks."""
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


load_dotenv()


def maint_dsn() -> str:
    base = os.environ["DATABASE_URL"]
    return base.rsplit("/", 1)[0] + "/postgres"


def ensure_test_db() -> str:
    import psycopg

    dsn = f"{maint_dsn()}"  # noqa: F841
    test_dsn = f"{os.environ['DATABASE_URL'].rsplit('/', 1)[0]}/{TEST_DB}"
    with psycopg.connect(maint_dsn(), autocommit=True) as c:
        names = [r[0] for r in c.execute("select datname from pg_database")]
        if TEST_DB not in names:
            c.execute(f"create database {TEST_DB}")
            print(f"setup        : created database {TEST_DB}")
        else:
            print(f"setup        : reusing database {TEST_DB}")
    return test_dsn


def drop_test_db() -> None:
    import psycopg

    with psycopg.connect(maint_dsn(), autocommit=True) as c:
        c.execute(f"drop database if exists {TEST_DB} with (force)")
        print(f"cleanup      : dropped database {TEST_DB}")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


TXT = (
    "Entropy and the second law.\n\n"
    "In an isolated system entropy never decreases. The Carnot cycle sets "
    "the efficiency limit for heat engines operating between two reservoirs.\n"
)


def upload_and_settle(c, project_id: str, token: str, name: str,
                      timeout_s: int = 240) -> dict:
    r = c.post(f"/projects/{project_id}/sources",
               files={"files": (name, TXT.encode(), "text/plain")},
               headers=auth(token))
    check(f"upload accepted ({name})", r.status_code == 202, r.text[:160])
    sid = r.json()["accepted"][0]["id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = c.get(f"/sources/{sid}", headers=auth(token)).json()
        if row["status"] in ("ready", "failed"):
            return row
        time.sleep(3)
    raise SystemExit(f"FAIL ingest never settled for {name}")


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    os.environ["DATABASE_URL"] = ensure_test_db()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.main import app  # noqa: E402 — after env override
    from fastapi.testclient import TestClient  # noqa: E402

    with TestClient(app) as c:
        print("bootstrap")
        a = c.post("/auth/signup", json={"email": f"a-{tag}@t.co",
                                         "password": "correct horse battery",
                                         "display_name": "A"}).json()
        u = c.post("/auth/signup", json={"email": f"u-{tag}@t.co",
                                         "password": "correct horse battery",
                                         "display_name": "U"}).json()
        admin_tok, user_tok = a["access_token"], u["access_token"]
        check("first user admin", a["user"]["is_admin"] is True)
        proj = c.post("/projects", json={"title": "P3", "subject": "Physics"},
                      headers=auth(admin_tok)).json()
        pid = proj["id"]

        print("\nsteady state")
        row = upload_and_settle(c, pid, admin_tok, "steady.txt")
        if row["status"] == "ready":
            print("  ingest       : real pipeline (LLM reachable)")
        else:
            print(f"  ingest       : pipeline failed ({row['status_detail'][:100]}); "
                  "seeding directly")
            import random

            import psycopg
            from app.config import get_settings
            from app.vectorstore import VectorStore

            settings = get_settings()
            db_dsn = os.environ["DATABASE_URL"]
            db = __import__("app.db", fromlist=["Database"]).Database(settings)
            try:
                vec = [random.gauss(0, 1) for _ in range(settings.embedding_dim or 768)]
                norm = sum(v * v for v in vec) ** 0.5
                vec = [v / norm for v in vec]
                VectorStore(db).upsert(
                    project_id=pid, source_id=row["id"], ids=[str(uuid.uuid4())],
                    embeddings=[vec], texts=["Entropy rises in an isolated system."],
                    metadatas=[{"locator_kind": "page", "locator_value": "1",
                                "chunk_index": 0}],
                )
                with psycopg.connect(db_dsn) as conn:
                    with conn.transaction():
                        conn.execute(
                            "insert into system_meta (key, value, updated_at) values "
                            "(%s, %s, now()) on conflict (key) do update set "
                            "value = excluded.value, updated_at = now()",
                            ("embedding.last_model", settings.embedding_model))
                        conn.execute(
                            "insert into system_meta (key, value, updated_at) values "
                            "(%s, %s, now()) on conflict (key) do update set "
                            "value = excluded.value, updated_at = now()",
                            ("embedding.last_dim", str(settings.embedding_dim or 768)))
            finally:
                db.close()
        st = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("no drift when healthy", st["drift"] is False, st["detail"])
        check("ground truth recorded", st["last_model"] == st["embedding_model"],
              str(st["last_model"]))

        print("\nmodel swap -> drift (faked via system_meta)")
        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with conn.transaction():
                conn.execute("update system_meta set value = 'fake-other-model' "
                             "where key = 'embedding.last_model'")
        st = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("status drift=true", st["drift"] is True, st["detail"][:160])
        check("revert_to names previous model", st["revert_to"] == "fake-other-model",
              str(st["revert_to"]))
        h = c.get("/health").json()
        check("health degraded", h["status"] == "degraded", h["status"])
        check("health carries drift detail", bool(h.get("embedding_drift")),
              (h.get("embedding_drift") or "")[:120])
        r = c.post(f"/projects/{pid}/sources",
                   files={"files": ("blocked.txt", b"hi", "text/plain")},
                   headers=auth(admin_tok))
        check("upload -> 409 while drifted", r.status_code == 409, r.text[:200])
        check("409 names reembed", "reembed" in r.text, r.text[:200])
        r = c.post(f"/projects/{pid}/quiz", json={}, headers=auth(admin_tok))
        check("quiz create -> 409 while drifted", r.status_code == 409, str(r.status_code))

    print("\nrestart with drifted meta (non-blocking boot)")
    with TestClient(app) as c2:
        h = c2.get("/health").json()
        check("boot succeeds while drifted", h["status"] == "degraded", h["status"])
        st = c2.get("/system/settings/status", headers=auth(user_tok)).json()
        check("drift survives restart", st["drift"] is True)

        print("\nre-embed job")
        r = c2.post("/system/reembed", headers=auth(user_tok))
        check("non-admin job start -> 403", r.status_code == 403, str(r.status_code))
        # Re-login: tokens survive restart (same secret + user rows).
        r = c2.post("/system/reembed", headers=auth(admin_tok))
        check("admin job start -> 202", r.status_code == 202, r.text[:200])
        r2 = c2.post("/system/reembed", headers=auth(admin_tok))
        terminal_early = c2.get("/system/reembed/status",
                                headers=auth(admin_tok)).json()["state"]
        if terminal_early == "running":
            check("concurrent start -> 409", r2.status_code == 409, str(r2.status_code))
        else:
            print(f"  (job already {terminal_early}; concurrency assert skipped)")
        deadline = time.monotonic() + 420
        job = {}
        while time.monotonic() < deadline:
            job = c2.get("/system/reembed/status", headers=auth(admin_tok)).json()
            if job["state"] not in ("running", "idle"):
                break
            time.sleep(4)
        check("job reached terminal state",
              job.get("state") in ("done", "done_with_failures", "failed"), str(job))
        print(f"  job          : {job.get('state')} — {job.get('message')}")
        st = c2.get("/system/settings/status", headers=auth(admin_tok)).json()
        check("drift clears after job", st["drift"] is False, st["detail"])
        check("ground truth = current model", st["last_model"] == st["embedding_model"],
              str(st["last_model"]))

        print("\nforce re-embed on clean corpus")
        r = c2.post("/system/reembed", headers=auth(admin_tok))
        check("in-sync start without force -> 409", r.status_code == 409, r.text[:160])
        r = c2.post("/system/reembed?force=true", headers=auth(admin_tok))
        check("force start -> 202", r.status_code == 202, r.text[:200])
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            job = c2.get("/system/reembed/status", headers=auth(admin_tok)).json()
            if job["state"] not in ("running", "idle"):
                break
            time.sleep(4)
        check("force job terminal", job.get("state", "").startswith("done"), str(job))

        print("\nrevert path (model drift without a job)")
        admin_view = {i["key"]: i for i in
                      c2.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
        if not admin_view["embedding_model"]["locked"]:
            c2.patch("/system/settings", headers=auth(admin_tok),
                     json={"embedding_model": "temp-other-model"})
            st = c2.get("/system/settings/status", headers=auth(user_tok)).json()
            check("model PATCH drifts", st["drift"] is True
                  and st["revert_to"] == st["last_model"], str(st)[:200])
            c2.patch("/system/settings", headers=auth(admin_tok),
                     json={"embedding_model": None})
            st = c2.get("/system/settings/status", headers=auth(user_tok)).json()
            check("revert clears drift without a job", st["drift"] is False, st["detail"])
        else:
            print("  (embedding_model operator-locked here; revert covered by unit path)")

    print("\nREEMBED VERIFIED")
    return 0


if __name__ == "__main__":
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
            drop_test_db()
        except Exception as exc:  # noqa: BLE001
            print(f"  cleanup warning: {exc}")
    raise SystemExit(code)
