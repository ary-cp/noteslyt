"""
Phase A verification — the new data layer, end to end.

Runs against a real local Postgres (DATABASE_URL) and NO server process, so it
exercises the repositories directly rather than the HTTP surface:

    uv run python scripts/verify_data_layer.py

Checks, in order:
  1. migrations applied + embedding dim agrees with the SQL
  2. user registration, argon2 hashing, duplicate email
  3. project ownership (a second user cannot read another user's project)
  4. source ownership + status transitions
  5. project-level topics shared across sources
  6. chunk upsert + project-scoped retrieval
  7. TENANCY: project A's retrieval can never see project B's chunks
  8. cascade delete
  9. the retrieval gate treats a degenerate (zero) embedding as maximally distant

Exits non-zero on the first failure. Cleans up everything it creates.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.repo import (  # noqa: E402
    ProjectRepo,
    SourceRepo,
    TopicRepo,
    UserRepo,
    verify_password,
)
from app.vectorstore import VectorStore  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = PASS if condition else FAIL
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def unit_vector(dim: int) -> list[float]:
    v = [0.0] * dim
    v[0] = 1.0
    return v


def main() -> int:
    settings = get_settings()
    print("target       :", settings.db_target)

    db = Database(settings)
    health = db.health()
    print("server       :", health["server"])
    check("migrations applied", not health["migrations_pending"], str(health["migrations_pending"]))
    if settings.embedding_dim is None:
        from app.migration_runner import probe_model_dim

        settings.embedding_dim = probe_model_dim(settings) or db.embedding_dim()
        print(f"auto-dim       : resolved to {settings.embedding_dim}")
    check("embedding dim agrees with live column", settings.embedding_dim == db.embedding_dim(),
          f"EMBEDDING_DIM={settings.embedding_dim} chunks.embedding={db.embedding_dim()}")
    dim = settings.embedding_dim
    if dim is None:
        print("  [FAIL] embedding width unresolvable — start the endpoint or set EMBEDDING_DIM")
        return 1

    users, projects = UserRepo(db), ProjectRepo(db)
    sources, topics = SourceRepo(db), TopicRepo(db)
    store = VectorStore(db)

    tag = uuid.uuid4().hex[:8]

    # Only this run's randomized accounts may be removed, including on failure.
    try:
        # ---------------------------------------------------------------- users
        print("\nusers")
        email = f"owner-{tag}@test.co"
        user = users.create(email, "correct horse battery", "Priya")
        other = users.create(f"other-{tag}@test.co", "hunter2hunter2", "Other")
        check("register", user is not None and other is not None)
        dup = users.create(email, "x", "Dup")
        check("duplicate email rejected", dup is None)
        row = users.get_by_email_with_hash(email)
        check("password verifies", verify_password(row["password_hash"], "correct horse battery"))
        check("wrong password rejected", not verify_password(row["password_hash"], "nope"))

        # ------------------------------------------------------------- projects
        print("\nprojects (tenancy)")
        pa = projects.create(user.id, "Thermodynamics", "Physics")
        pb = projects.create(user.id, "Roman Empire", "History")
        check("create two projects", bool(pa and pb) and pa.id != pb.id)
        check("own project readable", projects.get_owned(pa.id, user.id) is not None)
        check("OTHER USER CANNOT READ IT", projects.get_owned(pa.id, other.id) is None)
        check("other user's delete fails", not projects.delete(pb.id, other.id))
        check("b still there after foreign delete", projects.get_owned(pb.id, user.id) is not None)

        # -------------------------------------------------------------- sources
        print("\nsources")
        sa = sources.create(pa.id, "lecture.pdf", "pdf")
        sb = sources.create(pb.id, "notes.docx", "docx")
        check("create queued source", sa.status == "queued", sa.status)
        sources.set_status(sa.id, "ready", "parsed", units=12, chunk_count=2, parser="local")
        check("status transition", sources.get_owned(sa.id, user.id).status == "ready")
        check("other user cannot read source", sources.get_owned(sa.id, other.id) is None)
        check("other user cannot delete source", not sources.delete_owned(sa.id, other.id))
        check("source ids never collide", sa.id != sb.id)

        # --------------------------------------------------------------- topics
        print("\ntopics (project-level)")
        t1 = topics.upsert(pa.id, "entropy", "Entropy", "Second law of thermodynamics")
        t2 = topics.upsert(pa.id, "engines", "Engines")
        again = topics.upsert(pa.id, "entropy", "Entropy", "Second law of thermodynamics")
        check("upsert by (project, slug)", t1.id == again.id and t1.id != t2.id)
        topics.link_source(t1.id, sa.id, [{"kind": "page", "value": 3}])
        linked = topics.sources_for(t1.id)
        check("topic -> source link", len(linked) == 1 and linked[0]["filename"] == "lecture.pdf")
        topics.set_edges(pa.id, [(t2.id, t1.id)])
        check("prerequisite edge", len(topics.edges(pa.id)) == 1)

        # ------------------------------------------------- chunks + scoped search
        print("\nchunks + retrieval")
        vec = unit_vector(dim)
        store.upsert(
            project_id=pa.id,
            source_id=sa.id,
            ids=[str(uuid.uuid4()) for _ in range(2)],
            embeddings=[vec, [0.0] * dim],
            texts=["Entropy rises in an isolated system.", "The Carnot cycle sets the efficiency limit."],
            metadatas=[
                {"topic_id": t1.id, "locator_kind": "page", "locator_value": "3", "page": 3, "heading": "Second law"},
                {"topic_id": t2.id, "locator_kind": "slide", "locator_value": "4"},
            ],
        )
        store.upsert(
            project_id=pb.id,
            source_id=sb.id,
            ids=[str(uuid.uuid4())],
            embeddings=[vec],
            texts=["HISTORY-ONLY-SENTINEL"],
            metadatas=[{"locator_kind": "section", "locator_value": "Ch1"}],
        )
        check("chunk counts per project", store.count(pa.id) == 2 and store.count(pb.id) == 1)

        hits = store.query(vec, project_id=pa.id, k=5)
        check("retrieval returns chunks", len(hits) == 2)
        check("top hit is the exact vector match", hits and hits[0].similarity > 0.999)
        check("locator preserved", bool(hits) and hits[0].locator_kind == "page" and hits[0].locator_value == "3")
        check("page convenience property", bool(hits) and hits[0].page == 3)
        check(
            "non-page locator has no page number",
            store.query(vec, project_id=pb.id, k=1)[0].page is None,
        )

        print("\nTENANCY (the guarantee)")
        leaked_a = any("HISTORY" in h.text for h in hits)
        leaked_b = any("Entropy" in h.text for h in store.query(vec, project_id=pb.id, k=5))
        check("project A cannot see B's chunks", not leaked_a)
        check("project B cannot see A's chunks", not leaked_b)
        check("empty source filter = no filter", len(store.query(vec, project_id=pa.id, source_ids=[], k=5)) == 2)
        check("narrow to own source", len(store.query(vec, project_id=pa.id, source_ids=[sa.id], k=5)) == 2)
        check("narrow to FOREIGN source returns nothing", len(store.query(vec, project_id=pa.id, source_ids=[sb.id], k=5)) == 0)
        check("topic filter", len(store.get_by_topic(t1.id, project_id=pa.id)) == 1)
        check("topic filter is project-scoped", len(store.get_by_topic(t1.id, project_id=pb.id)) == 0)

        # ----------------------------------------------------- degenerate vector
        print("\nretrieval gate")
        degenerate = store.query([0.0] * dim, project_id=pa.id, k=1)
        d = degenerate[0].distance if degenerate else 1.0
        check("zero vector is finite, not NaN", math.isfinite(d), f"distance={d}")
        check("zero vector refuses (distance > max_distance)", d > settings.max_distance, f"{d} > {settings.max_distance}")

        # --------------------------------------------------------------- cascade
        print("\ncascade delete")
        projects.delete(pa.id, user.id)
        check("project gone", projects.get_owned(pa.id, user.id) is None)
        check("its chunks gone", store.count(pa.id) == 0)
        check("its topics gone", topics.list_for_project(pa.id) == [])
        check("other project untouched", store.count(pb.id) == 1 and projects.get_owned(pb.id, user.id) is not None)
    finally:
        with db.transaction() as c:
            c.execute("delete from users where email = any(%s)",
                      ([f"owner-{tag}@test.co", f"other-{tag}@test.co"],))
        db.close()

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("DATA LAYER VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
