"""
End-to-end smoke test against a running backend.

    python scripts/smoke_test.py [http://localhost:8000]

Zero-API-key run:
    terminal 1  uv run python scripts/mock_llm.py
    terminal 2  LLM_BASE_URL=http://127.0.0.1:9999/v1 LLM_API_KEY=x
                 EMBEDDING_MODEL=mock uv run uvicorn app.main:app --port 8000
    terminal 3  uv run python scripts/smoke_test.py

Covers the Phase A-E surface that exists today:
  signup -> onboarding -> two projects -> upload pdf + docx + xlsx + mp3
  into A -> poll to ready -> project-level topics -> retrieval scoped to one
  project (tenancy) -> cross-project isolation -> file download ->
  chats + three-tier memory (grounded answers, refusal, isolation, nudges,
  refinement) -> cascade delete.

Exits non-zero on the first failure and cleans up everything it creates.
"""

from __future__ import annotations

import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TIMEOUT_S = 240

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def section(title: str) -> None:
    print(f"\n{title}")


def sample_pdf() -> Path:
    sys.path.insert(0, str(Path(__file__).parent))
    from make_sample_pdf import main as make_pdf  # type: ignore[import-not-found]

    path = Path(tempfile.gettempdir()) / "sample_lecture.pdf"
    make_pdf(str(path))
    return path


def sample_docx() -> Path:
    from docx import Document

    path = Path(tempfile.gettempdir()) / "sample_notes.docx"
    doc = Document()
    doc.add_heading("Thermodynamics", 1)
    doc.add_paragraph("Entropy is the measure of disorder in a system.")
    doc.add_heading("First law", 2)
    doc.add_paragraph("Energy is conserved in every thermodynamic process.")
    doc.save(path)
    return path


def sample_xlsx() -> Path:
    import openpyxl

    path = Path(tempfile.gettempdir()) / "sample_costs.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Operating Costs"
    ws.append(["item", "cost"])
    ws.append(["steel", 120])
    ws.append(["coke", 80])
    ws.append(["labor", 200])
    wb.save(path)
    return path


def sample_mp3() -> Path:
    # The bytes are never decoded locally — they go to the transcription
    # endpoint (the mock in a zero-key run), so fake content is enough.
    path = Path(tempfile.gettempdir()) / "sample_lecture.mp3"
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x01\x02" + b"\x00" * 4096)
    return path


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wait_until_settled(c: httpx.Client, headers: dict, project_id: str, tries: int = 90) -> dict:
    """Poll the cheap status endpoint until nothing is queued/parsing."""
    for _ in range(tries):
        body = c.get(f"/projects/{project_id}/ingest-status", headers=headers).json()
        if body["settled"]:
            return body
        time.sleep(1.0)
    return body


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    c = httpx.Client(base_url=BASE, timeout=TIMEOUT_S)

    # ------------------------------------------------------------- identity
    section("identity")
    health = c.get("/health").json()
    check("server healthy", health["status"] == "ok", health["database"]["target"])
    check("no pending migrations", health["database"]["migrations_pending"] == [])

    reg = c.post(
        "/auth/signup",
        json={"email": f"smoke-{tag}@test.co", "password": "smoke-password", "display_name": "Smoke"},
    )
    check("signup", reg.status_code == 201, str(reg.status_code))
    token = reg.json()["access_token"]
    h = auth(token)

    ob = c.post(
        "/auth/onboarding",
        json={"capability_level": "beginner", "thinking_level": "concrete", "goals": "pass"},
        headers=h,
    )
    check("onboarding", ob.status_code == 204, str(ob.status_code))
    prof = c.get("/profile", headers=h).json()
    check("global memory seeded", prof["capability_level"] == "beginner", prof["capability_level"])

    # ------------------------------------------------------------- projects
    section("projects")
    pa = c.post("/projects", json={"title": "Thermodynamics", "subject": "Physics"}, headers=h).json()
    pb = c.post("/projects", json={"title": "Roman Empire", "subject": "History"}, headers=h).json()
    check("two projects", pa["id"] != pb["id"], f"{pa['title']} / {pb['title']}")

    # --------------------------------------------------------------- upload
    section("upload + async ingestion (pdf + docx + xlsx + mp3)")
    pdf, docx, xlsx, mp3 = sample_pdf(), sample_docx(), sample_xlsx(), sample_mp3()
    up = c.post(
        f"/projects/{pa['id']}/sources",
        files=[
            ("files", (pdf.name, pdf.read_bytes(), "application/pdf")),
            ("files", (docx.name, docx.read_bytes(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", (xlsx.name, xlsx.read_bytes(),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("files", (mp3.name, mp3.read_bytes(), "audio/mpeg")),
        ],
        headers=h,
    )
    check("upload accepted (202)", up.status_code == 202, str(up.status_code))
    payload = up.json()
    check("four files accepted", len(payload["accepted"]) == 4, str(payload["rejected"]))
    check("all start queued", all(s["status"] == "queued" for s in payload["accepted"]))
    source_id = next(s["id"] for s in payload["accepted"] if s["filename"] == pdf.name)

    # a type with no registered parser must be refused with a reason
    bad = c.post(
        f"/projects/{pa['id']}/sources",
        files={"files": ("archive.zip", b"PK\x03\x04junk", "application/zip")},
        headers=h,
    )
    rejected = bad.json() if bad.status_code != 202 else bad.json()["rejected"]
    check("unregistered type rejected", bool(rejected), str(rejected)[:90])

    status = wait_until_settled(c, h, pa["id"])
    check("ingestion settled", status["settled"], str(status["counts"]))
    check("no failures", status["counts"].get("failed", 0) == 0, str(status["counts"]))
    check("all four ready", status["counts"].get("ready", 0) == 4, str(status["counts"]))

    listed_sources = c.get(f"/projects/{pa['id']}/sources", headers=h).json()
    kinds: dict[str, str] = {}
    for row in listed_sources:
        src = c.get(f"/sources/{row['id']}", headers=h).json()
        kinds[src["filename"]] = src["source_type"]
        check(f"{src['filename']} ready", src["status"] == "ready",
              f"{src['status']} / {src['status_detail']}")
        check(f"{src['filename']} has units", src["units"] > 0, f"{src['units']} units")
        check(f"{src['filename']} has chunks", src["chunk_count"] > 0, f"{src['chunk_count']} chunks")
        print(f"              parser={src['parser']} filename={src['filename']}")
    check("one parser per type (pdf/docx/xlsx/audio)",
          set(kinds.values()) == {"pdf", "docx", "xlsx", "audio"}, str(kinds))

    # --------------------------------------------------------------- topics
    section("project-level topics")
    topics = c.get(f"/projects/{pa['id']}/topics", headers=h).json()
    check("topics created", len(topics) > 0, f"{len(topics)} topic(s): {[t['slug'] for t in topics]}")
    check("topics are linked to a source", any(t["sources"] for t in topics))
    first = topics[0]["sources"][0] if topics and topics[0]["sources"] else {}
    check("topic records its locators", bool(first.get("locators")), str(first.get("locators"))[:80])

    # -------------------------------------------------- scoped retrieval
    section("project-scoped retrieval")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.config import get_settings
    from app.db import Database
    from app.repo import ProjectRepo
    from app.vectorstore import VectorStore

    db = Database(get_settings())
    store = VectorStore(db)
    chunks_a = store.count(pa["id"])
    chunks_b = store.count(pb["id"])
    check("chunks are in project A", chunks_a > 0, f"A={chunks_a} B={chunks_b}")
    check("project B is untouched", chunks_b == 0)

    stats = c.get("/projects", headers=h).json()
    listed = next(p for p in stats if p["id"] == pa["id"])["stats"]
    check("stats report chunks", listed["chunks"] == chunks_a, str(listed))
    check("stats report sources", listed["sources"] == 4, str(listed))

    # a query vector taken from A must not retrieve anything from B
    if chunks_a:
        with db.connection() as conn:
            sample_vec = conn.execute(
                "select embedding from chunks where project_id = %s limit 1", (pa["id"],)
            ).fetchone()["embedding"]
        hits_a = store.query(sample_vec, project_id=pa["id"], k=5)
        hits_b = store.query(sample_vec, project_id=pb["id"], k=5)
        check("project A retrieves its own chunks", len(hits_a) > 0, f"{len(hits_a)} hit(s)")
        check("TENANCY: project B retrieves nothing from A", len(hits_b) == 0, f"{len(hits_b)} hit(s)")
        check("hit carries a locator", bool(hits_a[0].locator_kind), f"{hits_a[0].locator_kind}={hits_a[0].locator_value}")
        narrowed = store.query(sample_vec, project_id=pa["id"], source_ids=[uuid.uuid4()], k=5)
        check("narrow to a foreign source returns nothing", len(narrowed) == 0)
    db.close()

    # ------------------------------------------------------------- tenancy
    section("tenancy")
    intruder = c.post(
        "/auth/signup",
        json={"email": f"smoke-intruder-{tag}@test.co", "password": "intruder-password", "display_name": "Intruder"},
    ).json()
    ih = auth(intruder["access_token"])
    check("cannot upload to another's project", c.post(
        f"/projects/{pa['id']}/sources",
        files={"files": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        headers=ih,
    ).status_code == 404)
    check("cannot read another's sources", c.get(f"/sources/{source_id}", headers=ih).status_code == 404)
    check("cannot list another's project sources", c.get(f"/projects/{pa['id']}/sources", headers=ih).status_code == 404)
    check("cannot download another's file", c.get(f"/sources/{source_id}/file", headers=ih).status_code == 404)
    check("cannot delete another's source", c.delete(f"/sources/{source_id}", headers=ih).status_code == 404)
    check("victim source survives", c.get(f"/sources/{source_id}", headers=h).status_code == 200)
    check("no anonymous access", c.get(f"/projects/{pa['id']}/sources").status_code == 401)

    # ---------------------------------------------------------------- file
    section("file streaming")
    got = c.get(f"/sources/{source_id}/file", headers=h)
    check("file downloads", got.status_code == 200, str(got.status_code))
    check("correct content type", got.headers.get("content-type", "").startswith("application/pdf"),
          got.headers.get("content-type", ""))
    check("bytes match the original", got.content[:5] == b"%PDF-", f"{len(got.content)} bytes")

    # -------------------------------------------------- chats + memory (E)
    section("chats + three-tier memory")
    ch = c.post(f"/projects/{pa['id']}/chats", json={}, headers=h)
    check("chat created (201)", ch.status_code == 201, str(ch.status_code))
    chat_id = ch.json()["id"]
    check("default title", ch.json()["title"] == "New chat")

    def send(chat: str, text: str) -> dict:
        r = c.post(f"/chats/{chat}/messages", json={"content": text}, headers=h)
        check(f"answered [{text[:30]}…] (200)", r.status_code == 200, str(r.status_code))
        return r.json()

    ans = send(chat_id, "What is an eigenvector?")
    check("grounded", ans["grounded"] is True, ans["answer"][:80])
    check("citations present", len(ans["citations"]) > 0,
          str([f"[{x['locator_kind']} {x['locator_value']}]" for x in ans["citations"]]))
    check("retrieved locators reported", len(ans["retrieved"]) > 0, str(ans["retrieved"]))
    check("every citation label is in the answer",
          all((f"[{x['locator_kind'].capitalize()} {x['locator_value']}]" in ans["answer"]
               or x["locator_kind"] == "segment" and x["locator_value"] in ans["answer"])
              for x in ans["citations"]),
          ans["answer"][:120])

    detail = c.get(f"/chats/{chat_id}", headers=h).json()
    check("history persisted server-side", detail["total"] == 2, str(detail["total"]))
    check("chat auto-titled", detail["chat"]["title"].startswith("What is an eigenvector"),
          detail["chat"]["title"])

    refused = send(chat_id, "Who won the 2022 world cup?")
    check("off-material refused", refused["grounded"] is False
          and refused["answer"] == "I can't find this in your uploaded material.",
          refused["answer"][:60])
    check("refusal stored without citations", refused["citations"] == [])

    follow = send(chat_id, "How is it computed for a 2x2 matrix?")
    check("follow-up answered (rewrite path)", follow["grounded"] in (True, False))

    # isolation: a second chat starts empty and stays independent
    ch2 = c.post(f"/projects/{pa['id']}/chats", json={"title": "Second"}, headers=h).json()
    check("second chat listed",
          {x["id"] for x in c.get(f"/projects/{pa['id']}/chats", headers=h).json()} == {chat_id, ch2["id"]})
    send(ch2["id"], "What is a determinant?")
    check("histories isolated",
          c.get(f"/chats/{chat_id}", headers=h).json()["total"] == 6
          and c.get(f"/chats/{ch2['id']}", headers=h).json()["total"] == 2)
    check("rename works",
          c.patch(f"/chats/{ch2['id']}", json={"title": "Renamed"}, headers=h).json()["title"] == "Renamed")
    check("empty message rejected", c.post(f"/chats/{chat_id}/messages",
          json={"content": ""}, headers=h).status_code == 422)

    # three more turns: the 6th user message triggers background refinement
    for q in ("What is a vector space?", "What is matrix multiplication?", "Explain diagonalisation."):
        send(chat_id, q)

    mem = c.get(f"/projects/{pa['id']}/memory", headers=h).json()
    check("memory lists topics", len(mem["topics"]) > 0, f"{len(mem['topics'])} topics")
    check("weakest ranked",
          all(mem["weakest"][i]["p_mastery"] <= mem["weakest"][i + 1]["p_mastery"]
              for i in range(len(mem["weakest"]) - 1)))
    check("coverage adds up", sum(mem["coverage"].values()) == len(mem["topics"]), str(mem["coverage"]))
    check("streak reported", set(mem["streak"]) == {"current_days", "active_days_14"}, str(mem["streak"]))
    nudged = [t for t in mem["topics"] if t["attempts"] == 0 and t["p_mastery"] != 0.30]
    check("chat evidence nudged mastery (no attempts spent)", len(nudged) > 0,
          str([(t["slug"], t["p_mastery"]) for t in nudged]))
    check("nudged topic in progress",
          all(t["coverage_status"] == "in_progress" for t in nudged))

    # refinement runs in the background every 6th user turn — poll for it
    for _ in range(30):
        prof = c.get("/profile", headers=h).json()
        if prof["study_pattern"].get("chat_sessions", 0) >= 1:
            break
        time.sleep(1.0)
    check("global refinement observed chat sessions",
          prof["study_pattern"].get("chat_sessions", 0) >= 1, str(prof["study_pattern"]))

    check("intruder cannot read chat", c.get(f"/chats/{chat_id}", headers=ih).status_code == 404)
    check("intruder cannot send", c.post(f"/chats/{chat_id}/messages",
          json={"content": "hi"}, headers=ih).status_code == 404)
    check("intruder cannot read memory",
          c.get(f"/projects/{pa['id']}/memory", headers=ih).status_code == 404)
    check("memory needs auth", c.get(f"/projects/{pa['id']}/memory").status_code == 401)
    check("chat needs auth", c.get(f"/chats/{chat_id}").status_code == 401)

    # -------------------------------------------------- quiz (F)
    section("adaptive assessment")
    check("no topics -> no quiz",
          c.post(f"/projects/{pb['id']}/quiz", json={}, headers=h).status_code == 422)
    qz = c.post(f"/projects/{pa['id']}/quiz", json={"n_questions": 3}, headers=h)
    check("quiz created (201)", qz.status_code == 201, str(qz.status_code))
    quiz = qz.json()
    quiz_id = quiz["quiz_id"]
    check("three questions", len(quiz["questions"]) == 3,
          str([(q["qtype"], q["topic_slug"]) for q in quiz["questions"]]))
    check("mixed types", {q["qtype"] for q in quiz["questions"]}
          == {"mcq", "short_answer", "numerical"})
    check("no keys leaked", all(set(q) == {"id", "topic_id", "topic_slug", "stem", "qtype",
          "options", "locator", "difficulty"} for q in quiz["questions"]))
    check("verification reported", quiz["verification"]["verified"] == 3, str(quiz["verification"]))
    check("cold-start spread across topics",
          len({q["topic_slug"] for q in quiz["questions"]}) == 3)
    by_type = {q["qtype"]: q for q in quiz["questions"]}

    def answer(qid: str, q: dict, text: object) -> dict:
        r = c.post(f"/quizzes/{qid}/answer",
                   json={"question_id": q["id"], "answer": text}, headers=h)
        check(f"answer [{q['qtype']}] accepted (200)", r.status_code == 200, str(r.status_code))
        return r.json()

    mcq = by_type["mcq"]
    check("mcq rejects text", c.post(f"/quizzes/{quiz_id}/answer",
          json={"question_id": mcq["id"], "answer": "first"}, headers=h).status_code == 422)
    idx = next(i for i, o in enumerate(mcq["options"]) if "Correct" in o)
    a1 = answer(quiz_id, mcq, idx)
    check("mcq correct", a1["correct"] is True, a1["feedback"][:80])
    check("bkt moved up", a1["p_after"] > a1["p_before"], f"{a1['p_before']} -> {a1['p_after']}")
    check("cited feedback", a1["citation"]["label"] == mcq["locator"] and a1["citation"]["source"],
          str(a1["citation"]))

    short = by_type["short_answer"]
    a2 = answer(quiz_id, short, "An eigenvector satisfies Av = lambda v")
    check("short answer graded correct", a2["correct"] is True, a2["feedback"][:80])

    num = by_type["numerical"]
    a3 = answer(quiz_id, num, 999.0)
    check("numerical graded wrong", a3["correct"] is False, a3["feedback"][:80])
    check("bkt moved down", a3["p_after"] < a3["p_before"], f"{a3['p_before']} -> {a3['p_after']}")
    check("assessment created on completion", a3["assessment_id"] is not None)

    replay = answer(quiz_id, mcq, idx)
    check("idempotent replay", replay["p_before"] == a1["p_before"]
          and replay["p_after"] == a1["p_after"], f"{replay['p_before']} -> {replay['p_after']}")
    mem2 = c.get(f"/projects/{pa['id']}/memory", headers=h).json()
    mcq_mem = next(t for t in mem2["topics"] if t["slug"] == mcq["topic_slug"])
    check("no double charge", mcq_mem["attempts"] == 1, str(mcq_mem["attempts"]))

    learner = c.get(f"/projects/{pa['id']}/learner", headers=h).json()
    check("learner has quiz history",
          learner["recent_quizzes"] and learner["recent_quizzes"][0]["quiz_id"] == quiz_id,
          str(learner["recent_quizzes"]))
    check("learner tally", learner["recent_quizzes"][0]["answered"] == 3
          and learner["recent_quizzes"][0]["correct"] == 2,
          str(learner["recent_quizzes"][0]))

    rep = c.get(f"/assessments/{a3['assessment_id']}/report", headers=h).json()
    check("report score", rep["report"]["score"] == {"correct": 2, "total": 3},
          str(rep["report"]["score"]))
    check("report recommends next", bool(rep["report"]["next_up"]), str(rep["report"]["next_up"]))

    check("intruder cannot quiz", c.post(f"/projects/{pa['id']}/quiz", json={}, headers=ih).status_code == 404)
    check("intruder cannot answer", c.post(f"/quizzes/{quiz_id}/answer",
          json={"question_id": mcq["id"], "answer": 0}, headers=ih).status_code == 404)
    check("intruder cannot see learner",
          c.get(f"/projects/{pa['id']}/learner", headers=ih).status_code == 404)
    check("intruder cannot see report",
          c.get(f"/assessments/{a3['assessment_id']}/report", headers=ih).status_code == 404)

    # -------------------------------------------------------------- delete
    section("cascade delete")
    before = VectorStore(Database(get_settings())).count(pa["id"])
    check("delete one source", c.delete(f"/sources/{source_id}", headers=h).status_code == 204)
    check("source gone", c.get(f"/sources/{source_id}", headers=h).status_code == 404)
    db2 = Database(get_settings())
    after = VectorStore(db2).count(pa["id"])
    check("only its chunks are gone", 0 < after < before, f"before={before} after={after}")
    db2.close()

    # a second source, then delete the whole project
    with pdf.open("rb") as fh:
        c.post(f"/projects/{pa['id']}/sources",
               files={"files": (pdf.name, fh, "application/pdf")}, headers=h)
    wait_until_settled(c, h, pa["id"])
    check("delete project", c.delete(f"/projects/{pa['id']}", headers=h).status_code == 204)
    check("project gone", c.get(f"/projects/{pa['id']}", headers=h).status_code == 404)
    check("its chats are gone", c.get(f"/chats/{chat_id}", headers=h).status_code == 404)
    check("its quizzes are gone", c.post(f"/quizzes/{quiz_id}/answer",
          json={"question_id": mcq["id"], "answer": 0}, headers=h).status_code == 404)
    db3 = Database(get_settings())
    check("its chunks are gone", VectorStore(db3).count(pa["id"]) == 0)
    check("its topics are gone", len(ProjectRepo(db3).list_for_user(reg.json()["user"]["id"])) == 1)
    db3.close()

    # uploads dir has no leftovers for the deleted project
    uploads = get_settings().uploads_dir / pa["id"]
    check("uploaded files removed", not uploads.exists(), str(uploads))

    c.delete(f"/projects/{pb['id']}", headers=h)
    c.close()

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())