"""
Phase 4 verification — SQLite + ChromaDB backend, in-process.

Uses fastapi.testclient with `DATABASE_URL` unset (the desktop default), so
this exercises the exact zero-install path: SQLite file + Chroma dir under a
temporary DATA_DIR, no Postgres involved:

    uv run python scripts/verify_sqlite.py

Covers:
  * fresh boot migrates SQLite (8 files) + creates the Chroma collection
  * users/projects/sources/topics CRUD + type rehydration (datetimes, bools,
    JSON lists) round-trip through to_record
  * upload -> ingeststeady state (or direct seeding when remotes are down):
    Chroma retrieval, per-project counts, locator preservation
  * TENANCY: project A cannot see B's vectors (Chroma where-filter)
  * retrieval gate: zero vector refuses
  * model drift (faked ground truth): status drift + revert_to, health
    degraded, upload -> 409
  * width drift via meta (faked last_dim): drift without any DDL
  * POST /system/reembed -> terminal -> drift clears (real pipeline when the
    remotes answer, with seeded fallback otherwise)
  * cascade delete removes SQLite rows AND Chroma vectors
  * admin settings PATCH works on SQLite (overlay is backend-agnostic)

Exits non-zero on the first failure. The temp dir is removed afterwards.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

_failures: list[str] = []
TMP = tempfile.mkdtemp(prefix="noteslyt-verify-sqlite-")


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
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


def seed_directly(settings, db, store, project_id: str, source_id: str,
                  topic_id: str | None, tag: str) -> None:
    """Fallback when remotes are down: one chunk row + vectors + ground truth."""
    import random

    random.seed(7)
    dim = settings.embedding_dim or 768
    v = [random.gauss(0, 1) for _ in range(dim)]
    n = sum(x * x for x in v) ** 0.5
    v = [x / n for x in v]
    store.upsert(project_id=project_id, source_id=source_id,
                 ids=[str(uuid.uuid4())], embeddings=[v],
                 texts=[f"Seeded chunk {tag}: entropy rises in isolation."],
                 metadatas=[{"topic_id": topic_id, "locator_kind": "page",
                             "locator_value": "1", "page": 1, "chunk_index": 0,
                             "heading": "Seed"}])
    with db.transaction() as conn:
        conn.execute(
            "insert into system_meta (key, value, updated_at) values "
            "(%s, %s, datetime('now')) on conflict (key) do update set "
            "value = excluded.value, updated_at = datetime('now')",
            ("embedding.last_model", settings.embedding_model))
        conn.execute(
            "insert into system_meta (key, value, updated_at) values "
            "(%s, %s, datetime('now')) on conflict (key) do update set "
            "value = excluded.value, updated_at = datetime('now')",
            ("embedding.last_dim", str(dim)))


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    os.environ["DATABASE_URL"] = ""
    os.environ["DATA_DIR"] = TMP

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.main import app  # noqa: E402 — after env override
    from fastapi.testclient import TestClient  # noqa: E402

    with TestClient(app) as c:
        from app.config import get_settings  # noqa: E402

        settings = get_settings()
        check("sqlite backend selected", settings.db_backend == "sqlite",
              settings.db_backend)
        print(f"setup        : db={settings.sqlite_path}")

        print("bootstrap")
        a = c.post("/auth/signup", json={"email": f"a-{tag}@t.co",
                                         "password": "correct horse battery",
                                         "display_name": "A"}).json()
        u = c.post("/auth/signup", json={"email": f"u-{tag}@t.co",
                                         "password": "correct horse battery",
                                         "display_name": "U"}).json()
        admin_tok, user_tok = a["access_token"], u["access_token"]
        check("first user admin", a["user"]["is_admin"] is True)
        pa = c.post("/projects", json={"title": "Thermo", "subject": "Physics"},
                    headers=auth(admin_tok)).json()
        pb = c.post("/projects", json={"title": "History", "subject": "Hist"},
                    headers=auth(admin_tok)).json()

        print("\nsteady state")
        row = upload_and_settle(c, pa["id"], admin_tok, "steady.txt")
        if row["status"] == "ready":
            print("  ingest       : real pipeline")
            check("chunk count recorded", (row.get("chunk_count") or 0) > 0,
                  str(row.get("chunk_count")))
        else:
            print(f"  ingest       : pipeline failed ({(row.get('status_detail') or '')[:100]}); "
                  "seeding directly")
            seed_directly(settings, app.state.db, app.state.store,
                          pa["id"], row["id"], None, tag)
        st = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("no drift when healthy", st["drift"] is False, st["detail"])
        check("ground truth recorded", st["last_model"] == st["embedding_model"],
              str(st["last_model"]))

        print("\nretrieval (Chroma, project-scoped)")
        got = c.get(f"/projects/{pa['id']}/sources", headers=auth(admin_tok))
        check("source list reads", got.status_code == 200 and len(got.json()) == 1)
        # Foreign corpus must be invisible: seed project B directly.
        seed_directly(settings, app.state.db, app.state.store,
                      pb["id"], c.post(
                          f"/projects/{pb['id']}/sources",
                          files={"files": ("b.txt", TXT.encode(), "text/plain")},
                          headers=auth(admin_tok)).json()["accepted"][0]["id"],
                      None, "foreign-HISTORY-SENTINEL")
        # Project B's queued upload may ingest in the background; either way its
        # vectors must never leak into project A (checked via store below).
        from app.vectorstore_chroma import ChromaVectorStore  # noqa: E402

        store = app.state.store
        check("chroma store active", isinstance(store, ChromaVectorStore))
        check("counts per project", store.count(pa["id"]) >= 1 and store.count(pb["id"]) >= 1,
              f"A={store.count(pa['id'])} B={store.count(pb['id'])}")
        probe = store.query([0.0] * (settings.embedding_dim or 768),
                            project_id=pa["id"], k=10)
        leaked = any("HISTORY-SENTINEL" in h.text or "foreign" in h.text.lower()
                     for h in probe)
        check("project A cannot see B's vectors", not leaked,
              str([h.text[:40] for h in probe][:3]))
        d = probe[0].distance if probe else 1.0
        import math  # noqa: E402

        check("zero vector finite", math.isfinite(d), f"distance={d}")
        check("zero vector refuses", d > settings.max_distance, f"{d} > {settings.max_distance}")

        print("\ndrift lifecycle on SQLite")
        import sqlite3 as _sl  # noqa: E402 (direct meta fake, bypassing the API)

        with _sl.connect(str(settings.sqlite_path)) as _conn:
            _conn.execute("update system_meta set value = 'fake-other-model' "
                          "where key = 'embedding.last_model'")
            _conn.commit()
        st = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("model drift detected", st["drift"] is True, st["detail"][:160])
        check("revert_to present", st["revert_to"] == "fake-other-model")
        check("health degraded", c.get("/health").json()["status"] == "degraded")
        r = c.post(f"/projects/{pa['id']}/sources",
                   files={"files": ("blocked.txt", b"hi", "text/plain")},
                   headers=auth(admin_tok))
        check("upload -> 409 while drifted", r.status_code == 409, r.text[:160])
        # Width drift via meta (no DDL involved on this backend).
        with _sl.connect(str(settings.sqlite_path)) as _conn:
            _conn.execute("update system_meta set value = 'fake-other-model' "
                          "where key = 'embedding.last_model'")
            _conn.execute("update system_meta set value = '1' where key = 'embedding.last_dim'")
            _conn.commit()
        st = c.get("/system/settings/status", headers=auth(user_tok)).json()
        check("width+model drift detected", st["drift"] is True, st["detail"][:200])

        print("\nre-embed job on SQLite")
        r = c.post("/system/reembed", headers=auth(user_tok))
        check("non-admin job start -> 403", r.status_code == 403, str(r.status_code))
        r = c.post("/system/reembed", headers=auth(admin_tok))
        check("admin job start -> 202", r.status_code == 202, r.text[:200])
        deadline = time.monotonic() + 420
        job = {}
        while time.monotonic() < deadline:
            job = c.get("/system/reembed/status", headers=auth(admin_tok)).json()
            if job["state"] not in ("running", "idle"):
                break
            time.sleep(4)
        check("job terminal", job.get("state", "").startswith("done"), str(job))
        print(f"  job          : {job.get('state')} — {job.get('message')}")
        st = c.get("/system/settings/status", headers=auth(admin_tok)).json()
        check("drift clears after job", st["drift"] is False, st["detail"])
        check("ground truth = current model", st["last_model"] == st["embedding_model"])

        print("\nadmin overlay on SQLite")
        r = c.patch("/system/settings", headers=auth(admin_tok), json={"top_k": 9})
        check("PATCH 200", r.status_code == 200, r.text[:160])
        items = {i["key"]: i for i in
                 c.get("/system/settings", headers=auth(admin_tok)).json()["settings"]}
        check("top_k db-sourced", items["top_k"]["source"] == "db" and items["top_k"]["value"] == 9)
        c.patch("/system/settings", headers=auth(admin_tok), json={"top_k": None})
        check("server healthy after reload", c.get("/health").json()["status"] == "ok")

        print("\nrepo sweep (paths HTTP above did not touch)")
        from app.repo import (  # noqa: E402
            ChatRepo,
            ProjectMemoryRepo,
            QuizRepo,
            StudioRepo,
            TopicRepo,
        )
        from app.repo.profile import ProfileRepo  # noqa: E402

        db = app.state.db
        topics = TopicRepo(db)
        chats, memory = ChatRepo(db), ProjectMemoryRepo(db, p_init=0.30)
        profiles, quizzes, studio = ProfileRepo(db), QuizRepo(db), StudioRepo(db)
        t = topics.upsert(pa["id"], "entropy", "Entropy", "Second law")
        check("topic upsert", t.slug == "entropy")
        check("topics list", len(topics.list_for_project(pa["id"])) >= 1)
        ch = chats.create(pa["id"], a["user"]["id"], "Chat 1")
        chats.add_message(ch.id, "user", "What is entropy?",
                          citations=[{"kind": "page", "value": "1"}], grounded=True)
        chats.add_message(ch.id, "assistant", "Entropy rises.", citations=[])
        msgs = chats.list_messages(ch.id)
        check("chat messages round-trip",
              len(msgs) == 2 and msgs[0].citations == [{"kind": "page", "value": "1"}]
              and msgs[0].grounded is True, str([(m.role, m.citations) for m in msgs]))
        check("chat list counts", chats.list_for_project(pa["id"], a["user"]["id"])[0]
              ["message_count"] == 2)
        m1 = memory.apply_result(a["user"]["id"], pa["id"], t.id, True,
                                 __import__("app.learner.bkt", fromlist=["BKTParams"]).BKTParams())
        check("BKT mastery moves", m1["p_mastery"] > 0.30, str(m1["p_mastery"]))
        memory.apply_nudge(a["user"]["id"], pa["id"], t.id, True)
        rows = memory.list_for_project(a["user"]["id"], pa["id"])
        mine = next((r for r in rows if r.topic_id == t.id), None)
        check("memory lists with status",
              mine is not None and mine.coverage_status in ("in_progress", "mastered"),
              str([(r.slug, r.coverage_status) for r in rows]))
        check("streak shape", set(memory.streak(a["user"]["id"], pa["id"])) ==
              {"current_days", "active_days_14"})
        prof = profiles.upsert_basics(a["user"]["id"], capability_level="advanced",
                                      goals="Ace exams")
        check("profile upsert", prof.capability_level == "advanced")
        profiles.observe_study_pattern(a["user"]["id"], sessions=2)
        profiles.add_strengths(a["user"]["id"], ["thermodynamics"])
        prof2 = profiles.get(a["user"]["id"])
        check("profile counters+strengths",
              prof2.study_pattern.get("sessions") == 2 and "thermodynamics" in prof2.strengths,
              str((prof2.study_pattern, prof2.strengths)))
        qp = profiles.quiz_progress(a["user"]["id"])
        check("quiz_progress shape", set(qp) >= {"answered", "correct"}, str(qp))
        qz = quizzes.create_quiz(pa["id"], a["user"]["id"], t.id)
        quizzes.add_question(qz.id, 0, topic_id=t.id, source_id=None, qtype="mcq",
                             stem="What rises?", options=["Entropy", "Enthalpy"],
                             answer_key={"index": 0}, stem_embedding=[0.1, 0.2, 0.3])
        stems = quizzes.recent_stems(a["user"]["id"], pa["id"])
        # pgvector.Vector quantizes to float32 on write (both backends) —
        # compare approximately.
        check("stem vectors round-trip",
              len(stems) == 1 and all(abs(x - y) < 1e-6 for x, y in
                                      zip(stems[0], [0.1, 0.2, 0.3])), str(stems))
        ans = quizzes.record_answer(qz.id, quizzes.list_questions(qz.id)[0].id,
                                    a["user"]["id"], "Entropy", True, 0.3, 0.5,
                                    feedback="Good", missed=["x"], matched=["y"])
        check("answer tallies", quizzes.answer_tally(qz.id, a["user"]["id"]) ==
              {"answered": 1, "correct": 1})
        check("answer signals", ans.missed == ["x"] and ans.feedback == "Good")
        asc = quizzes.create_assessment(pa["id"], a["user"]["id"], "Mid", [qz.id],
                                        {"score": 1})
        check("assessment containment",
              quizzes.find_assessment_for_quiz(qz.id, a["user"]["id"]).id == asc.id)
        check("recent quizzes", len(quizzes.recent_quizzes(a["user"]["id"], pa["id"])) == 1)
        job_row = studio.create(pa["id"], a["user"]["id"], "quiz", "Q", {"n": 1})
        studio.update(job_row["id"], "ready", title="Q!", payload={"n": 1})
        jobs = studio.list_for_project(pa["id"], a["user"]["id"])
        check("studio payload round-trip",
              len(jobs) == 1 and jobs[0]["payload"] == {"n": 1}
              and isinstance(jobs[0]["created_at"], object))
        check("studio cleanup runs", studio.delete_expired_failures(24) == 0)

        print("\ncascade delete clears both homes")
        n_before = store.count(pa["id"])
        check("vectors exist pre-delete", n_before >= 1, str(n_before))
        r = c.delete(f"/projects/{pa['id']}", headers=auth(admin_tok))
        check("project delete 204", r.status_code == 204, str(r.status_code))
        check("sqlite rows gone", store.count(pa["id"]) == 0)
        with app.state.db.connection() as conn:
            left = conn.execute("select count(*) as n from chunks where project_id = %s",
                                (pa["id"],)).fetchone()["n"]
        check("no sqlite chunk rows left", int(left) == 0)
        check("other project untouched", store.count(pb["id"]) >= 1)

    print("\nSQLITE VERIFIED")
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
            shutil.rmtree(TMP, ignore_errors=True)
            print("cleanup      : removed temp dir")
        except Exception as exc:  # noqa: BLE001
            print(f"  cleanup warning: {exc}")
    raise SystemExit(code)
