"""
Replays the exact HTTP sequence the frontend test console performs.

The console is a UI, so this drives the same calls in the same order against a
running server to prove each panel works. If this passes but the console looks
broken, the bug is in the React layer, not the API.

    uv run python scripts/verify_console_flow.py [http://localhost:8000]

Mirrors the sequence in frontend/src/harness/TestConsole.tsx:
  health -> signup -> whoami -> profile levels -> profile read -> goals-only
  patch -> project create -> list -> get -> patch -> upload -> poll -> topics
  -> tenancy probe -> chats (create/send/refusal/history/rename/delete) ->
  memory (topics/weakest/coverage/streak) -> quiz (create/answer/learner/report) -> delete
"""

from __future__ import annotations

import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ORIGIN = "http://localhost:5173"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def _sample_pdf() -> Path:
    sys.path.insert(0, str(Path(__file__).parent))
    from make_sample_pdf import main as make_pdf  # type: ignore[import-not-found]

    path = Path(tempfile.gettempdir()) / "console_flow.pdf"
    make_pdf(str(path))
    return path


def _wait(c: httpx.Client, headers: dict, project_id: str, tries: int = 90) -> dict:
    """Poll the ingest-status endpoint exactly as the console does."""
    for _ in range(tries):
        body = c.get(f"/projects/{project_id}/ingest-status", headers=headers).json()
        if body["settled"]:
            return body
        time.sleep(1.0)
    return body


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    c = httpx.Client(base_url=BASE, timeout=60, headers={"Origin": ORIGIN})
    auth: dict[str, str] = {}

    print("panel 1 · health")
    h = c.get("/health").json()
    check("health ok", h["status"] == "ok", f"status={h['status']}")
    check("reports database", bool(h["database"]["target"]), h["database"]["target"])
    check("no pending migrations", h["database"]["migrations_pending"] == [])
    check("reports storage", h["storage"]["backend"] == "local", h["storage"]["path"])

    print("\npanel 2 · identity")
    email = f"console-{tag}@test.co"
    r = c.post("/auth/signup", json={"email": email, "password": "console-password", "display_name": "Console"})
    check("signup 201", r.status_code == 201, str(r.status_code))
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    check("token attached to request", c.get("/auth/me", headers=auth).status_code == 200)
    check("whoami matches", c.get("/auth/me", headers=auth).json()["email"] == email)

    print("\npanel 3 · global memory")
    levels = c.get("/profile/levels").json()
    check("levels endpoint", "advanced" in levels["capability_levels"], str(levels["capability_levels"]))
    c.post(
        "/auth/onboarding",
        json={"capability_level": "intermediate", "thinking_level": "analytical", "goals": "pass thermodynamics"},
        headers=auth,
    )
    prof = c.get("/profile", headers=auth).json()
    check("onboarding stored", prof["capability_level"] == "intermediate", prof["capability_level"])

    patched = c.patch("/profile", json={"goals": "just survive"}, headers=auth).json()
    check(
        "goals-only patch keeps capability",
        patched["capability_level"] == "intermediate",
        f"capability={patched['capability_level']}",
    )
    check("goals updated", patched["goals"] == "just survive")
    check("profile has no secret", "password" not in patched)

    print("\npanel 4 · projects")
    created = c.post("/projects", json={"title": "Thermodynamics", "subject": "Physics"}, headers=auth)
    check("create 201", created.status_code == 201, str(created.status_code))
    pid = created.json()["id"]
    listing = c.get("/projects", headers=auth).json()
    check("appears in list", any(p["id"] == pid for p in listing), f"{len(listing)} project(s)")
    check("stats block present", "stats" in listing[0], str(listing[0].get("stats")))
    check("get one", c.get(f"/projects/{pid}", headers=auth).json()["title"] == "Thermodynamics")
    check(
        "patch applied",
        c.patch(f"/projects/{pid}", json={"subject": "Thermal Physics"}, headers=auth).json()["subject"]
        == "Thermal Physics",
    )

    print("\npanel 4b · sources + ingestion")
    pdf = _sample_pdf()
    with pdf.open("rb") as fh:
        up = c.post(
            f"/projects/{pid}/sources",
            files={"files": (pdf.name, fh, "application/pdf")},
            headers=auth,
        )
    check("upload 202", up.status_code == 202, str(up.status_code))
    payload = up.json()
    check("file accepted", len(payload["accepted"]) == 1, str(payload["rejected"]))
    sid = payload["accepted"][0]["id"]
    check("starts queued", payload["accepted"][0]["status"] == "queued")

    settled = _wait(c, auth, pid)
    check("ingestion settled", settled["settled"], str(settled["counts"]))
    check("no failures", settled["counts"].get("failed", 0) == 0, str(settled["counts"]))
    src = c.get(f"/sources/{sid}", headers=auth).json()
    check("source ready", src["status"] == "ready", f"{src['status']} / {src['status_detail']}")
    check("chunks stored", src["chunk_count"] > 0, f"{src['chunk_count']} chunks")

    listed = c.get(f"/projects/{pid}/sources", headers=auth).json()
    check("appears in source list", any(s["id"] == sid for s in listed))
    check("no auth header -> 401", c.get(f"/projects/{pid}/sources").status_code == 401)

    topics = c.get(f"/projects/{pid}/topics", headers=auth).json()
    check("topics created", len(topics) > 0, f"{len(topics)}: {[t['slug'] for t in topics]}")
    check("topic linked to the source", any(t["sources"] for t in topics))

    got = c.get(f"/sources/{sid}/file", headers=auth)
    check("file opens in a new tab", got.status_code == 200 and got.content[:5] == b"%PDF-",
          f"{len(got.content)} bytes")

    # a type with no registered parser must be refused with a reason
    rej = c.post(
        f"/projects/{pid}/sources",
        files={"files": ("clip.zip", b"PK\x03\x04junk", "application/zip")},
        headers=auth,
    )
    check("unregistered type refused", rej.status_code == 415, str(rej.status_code))

    print("\npanel 5 · tenancy probe (what the button does)")
    intruder = c.post(
        "/auth/signup",
        json={"email": f"intruder-{tag}@test.co", "password": "intruder-password", "display_name": "Intruder"},
    ).json()
    iauth = {"Authorization": f"Bearer {intruder['access_token']}"}
    intruder_list = c.get("/projects", headers=iauth).json()
    status = c.get(f"/projects/{pid}", headers=iauth).status_code
    check("intruder gets 404, not 403", status == 404, str(status))
    check("victim absent from intruder list", not any(p["id"] == pid for p in intruder_list))
    check("cannot read victim's sources", c.get(f"/projects/{pid}/sources", headers=iauth).status_code == 404)
    check("cannot download victim's file", c.get(f"/sources/{sid}/file", headers=iauth).status_code == 404)
    check("victim source survives", c.get(f"/sources/{sid}", headers=auth).status_code == 200)

    print("\npanel 8 · chats")
    ch = c.post(f"/projects/{pid}/chats", json={}, headers=auth)
    check("create 201", ch.status_code == 201, str(ch.status_code))
    cid = ch.json()["id"]
    msg = c.post(f"/chats/{cid}/messages", json={"content": "What is an eigenvector?"}, headers=auth)
    check("answered 200", msg.status_code == 200, str(msg.status_code))
    ans = msg.json()
    check("grounded with citations", ans["grounded"] is True and len(ans["citations"]) > 0,
          ans["answer"][:80])
    no = c.post(f"/chats/{cid}/messages", json={"content": "Who won the 2022 world cup?"}, headers=auth).json()
    check("off-material refused", no["grounded"] is False
          and no["answer"] == "I can't find this in your uploaded material.")
    detail = c.get(f"/chats/{cid}", headers=auth).json()
    check("history persisted", detail["total"] == 4, str(detail["total"]))
    check("auto-titled", "eigenvector" in detail["chat"]["title"].lower() and detail["chat"]["title"] != "New chat",
          detail["chat"]["title"])
    ch2 = c.post(f"/projects/{pid}/chats", json={"title": "Second"}, headers=auth).json()
    check("two chats listed",
          {x["id"] for x in c.get(f"/projects/{pid}/chats", headers=auth).json()} == {cid, ch2["id"]})
    check("rename",
          c.patch(f"/chats/{ch2['id']}", json={"title": "Renamed"}, headers=auth).json()["title"] == "Renamed")
    check("delete chat", c.delete(f"/chats/{ch2['id']}", headers=auth).status_code == 204)
    check("chat gone", c.get(f"/chats/{ch2['id']}", headers=auth).status_code == 404)
    check("intruder cannot read chat", c.get(f"/chats/{cid}", headers=iauth).status_code == 404)
    check("intruder cannot send", c.post(f"/chats/{cid}/messages",
          json={"content": "hi"}, headers=iauth).status_code == 404)
    check("empty message rejected", c.post(f"/chats/{cid}/messages",
          json={"content": ""}, headers=auth).status_code == 422)

    print("\npanel 9 · project memory")
    mem = c.get(f"/projects/{pid}/memory", headers=auth).json()
    check("topics listed", len(mem["topics"]) > 0, f"{len(mem['topics'])} topics")
    check("weakest ranked", len(mem["weakest"]) > 0)
    check("coverage adds up", sum(mem["coverage"].values()) == len(mem["topics"]), str(mem["coverage"]))
    check("streak reported", set(mem["streak"]) == {"current_days", "active_days_14"}, str(mem["streak"]))
    check("intruder cannot read memory",
          c.get(f"/projects/{pid}/memory", headers=iauth).status_code == 404)
    check("memory needs auth", c.get(f"/projects/{pid}/memory").status_code == 401)

    print("\npanel 10 · quiz")
    qz = c.post(f"/projects/{pid}/quiz", json={"n_questions": 1, "qtypes": ["numerical"]}, headers=auth)
    check("create queued job 202", qz.status_code == 202, str(qz.status_code))
    qz.raise_for_status()
    job_id = qz.json()["id"]
    for _ in range(120):
        jobs = c.get(f"/projects/{pid}/studio-jobs", headers=auth)
        jobs.raise_for_status()
        job = next(j for j in jobs.json() if j["id"] == job_id)
        if job["status"] in ("ready", "failed"):
            break
        time.sleep(1)
    check("quiz generation ready", job["status"] == "ready", job.get("error", ""))
    if job["status"] != "ready":
        c.delete(f"/projects/{pid}", headers=auth)
        return 1
    quiz = job["payload"]
    check("one numerical question", len(quiz["questions"]) == 1
          and quiz["questions"][0]["qtype"] == "numerical",
          str([(q["qtype"], q["topic_slug"]) for q in quiz["questions"]]))
    check("no keys leaked", set(quiz["questions"][0]) == {"id", "topic_id", "topic_slug", "stem",
          "qtype", "options", "locator", "difficulty"})
    q0 = quiz["questions"][0]
    a = c.post(f"/quizzes/{quiz['quiz_id']}/answer",
               json={"question_id": q0["id"], "answer": 4.0}, headers=auth).json()
    check("numerical correct", a["correct"] is True, a["feedback"][:80])
    check("bkt moved", a["p_after"] != a["p_before"], f"{a['p_before']} -> {a['p_after']}")
    check("cited", bool(a["citation"]["label"]), str(a["citation"]))
    check("assessment on completion", a["assessment_id"] is not None)
    rep = c.get(f"/assessments/{a['assessment_id']}/report", headers=auth).json()
    check("report 1/1", rep["report"]["score"] == {"correct": 1, "total": 1},
          str(rep["report"]["score"]))
    check("learner history",
          c.get(f"/projects/{pid}/learner", headers=auth).json()["recent_quizzes"][0]["quiz_id"]
          == quiz["quiz_id"])
    check("intruder cannot quiz", c.post(f"/projects/{pid}/quiz", json={}, headers=iauth).status_code == 404)
    check("intruder cannot answer", c.post(f"/quizzes/{quiz['quiz_id']}/answer",
          json={"question_id": q0["id"], "answer": 0}, headers=iauth).status_code == 404)
    check("intruder cannot see learner",
          c.get(f"/projects/{pid}/learner", headers=iauth).status_code == 404)
    check("intruder cannot see report",
          c.get(f"/assessments/{a['assessment_id']}/report", headers=iauth).status_code == 404)

    print("\nStudio artifact persistence")
    for kind, payload in (("slides", {"slides": [{"title": "Energy", "bullets": ["Energy is conserved"]}]}),
                          ("schedule", {"markdown": "# Study plan", "toc": ["Plan"]})):
        saved = c.post(f"/projects/{pid}/studio-jobs", headers=auth,
                       json={"kind": kind, "title": f"Test {kind}", "payload": payload})
        check(f"{kind} saved", saved.status_code == 201, str(saved.status_code))
        saved.raise_for_status()
        jobs = c.get(f"/projects/{pid}/studio-jobs", headers=auth).json()
        restored = next(j for j in jobs if j["id"] == saved.json()["id"])
        check(f"{kind} restored", restored["status"] == "ready" and restored["payload"] == payload)
    check("foreign user cannot save Studio artifact", c.post(f"/projects/{pid}/studio-jobs", headers=iauth,
          json={"kind": "slides", "title": "Foreign", "payload": {}}).status_code == 404)
    check("unknown Studio kind rejected", c.post(f"/projects/{pid}/studio-jobs", headers=auth,
          json={"kind": "unknown", "title": "Unknown", "payload": {}}).status_code == 422)

    print("\ncleanup")
    check("delete source", c.delete(f"/sources/{sid}", headers=auth).status_code == 204)
    check("source gone", c.get(f"/sources/{sid}", headers=auth).status_code == 404)
    check("delete project", c.delete(f"/projects/{pid}", headers=auth).status_code == 204)
    check("project gone", c.get(f"/projects/{pid}", headers=auth).status_code == 404)
    check("its chats are gone", c.get(f"/chats/{cid}", headers=auth).status_code == 404)
    check("its quizzes are gone", c.post(f"/quizzes/{quiz['quiz_id']}/answer",
          json={"question_id": q0["id"], "answer": 0}, headers=auth).status_code == 404)
    check("console-signed-out check", c.get("/projects").status_code == 401)

    c.close()
    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("CONSOLE FLOW VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
