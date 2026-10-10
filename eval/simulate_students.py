"""
Synthetic learners across separate projects — Phase G.34.

    uv run python eval/simulate_students.py [http://localhost:8000]

Three profiles (advanced/mid/novice) each get their own project over the same
material and take a quiz, answering each question correctly with the profile's
probability (seeded RNG, so the run is reproducible). Reports:

  * mastery gain per profile (mean p_after - p_before over graded answers)
  * question repetition rate: fraction of generated stems within cosine >= 0.9
    of an earlier stem by the same learner (< 5% target)
  * cross-project leakage (the key result): the same probe vector retrieves
    disjoint chunk sets per project, and no learner can touch another's
    chats/quizzes/learner state over HTTP (404s)

Needs a running server + model endpoints (or the mock), same as smoke_test.py.
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app.config import get_settings  # noqa: E402
from app.llm import build_embedder  # noqa: E402
from smoke_test import auth, sample_pdf, wait_until_settled  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TIMEOUT_S = 240
REPETITION_MAX = 0.05
RNG = random.Random(42)

PROFILES = [
    {"name": "Adv", "capability": "advanced", "thinking": "analytical", "p_correct": 0.9},
    {"name": "Mid", "capability": "intermediate", "thinking": "concrete", "p_correct": 0.65},
    {"name": "Nov", "capability": "beginner", "thinking": "concrete", "p_correct": 0.4},
]

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def main() -> int:
    embedder = build_embedder(get_settings())
    tag = uuid.uuid4().hex[:8]
    c = httpx.Client(base_url=BASE, timeout=TIMEOUT_S)
    pdf = sample_pdf()

    print("enrollment")
    students = []
    for prof in PROFILES:
        token = c.post("/auth/signup", json={
            "email": f"sim-{prof['name']}-{tag}@test.co",
            "password": "sim-password", "display_name": f"Sim {prof['name']}",
        }).json()["access_token"]
        h = auth(token)
        c.post("/auth/onboarding", json={
            "capability_level": prof["capability"], "thinking_level": prof["thinking"],
            "goals": "pass the assessment"}, headers=h)
        pid = c.post("/projects", json={"title": f"{prof['name']} physics"}, headers=h).json()["id"]
        with pdf.open("rb") as fh:
            up = c.post(f"/projects/{pid}/sources",
                        files={"files": (pdf.name, fh, "application/pdf")}, headers=h)
        assert up.status_code == 202, up.text
        students.append({**prof, "headers": h, "project_id": pid})
    for s in students:
        status = wait_until_settled(c, s["headers"], s["project_id"])
        check(f"{s['name']} ingested", status["settled"] and not status["counts"].get("failed"),
              str(status["counts"]))

    print("\nassessment")
    results: dict[str, dict] = {}
    for s in students:
        h, pid = s["headers"], s["project_id"]
        queued = c.post(f"/projects/{pid}/quiz", json={"n_questions": 3}, headers=h)
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["id"]
        deadline = time.monotonic() + TIMEOUT_S
        while True:
            jobs = c.get(f"/projects/{pid}/studio-jobs", headers=h).json()
            job = next(row for row in jobs if row["id"] == job_id)
            if job["status"] in {"ready", "failed"}:
                break
            assert time.monotonic() < deadline, "quiz job did not finish"
            time.sleep(0.5)
        assert job["status"] == "ready", job.get("error")
        quiz = job["payload"]
        stems = [q["stem"] for q in quiz["questions"]]
        deltas, correct = [], 0
        for q in quiz["questions"]:
            hit = RNG.random() < s["p_correct"]
            if q["qtype"] == "mcq":
                idx = next(i for i, o in enumerate(q["options"]) if "Correct" in o)
                given: object = idx if hit else (idx + 1) % len(q["options"])
            elif q["qtype"] == "short_answer":
                given = "An eigenvector satisfies Av = lambda v" if hit else "I do not know this at all"
            else:
                given = 4.0 if hit else 999.0
            ans = c.post(f"/quizzes/{quiz['quiz_id']}/answer",
                         json={"question_id": q["id"], "answer": given}, headers=h).json()
            assert ans["correct"] is hit, (q["qtype"], given, ans)
            deltas.append(ans["p_after"] - ans["p_before"])
            correct += ans["correct"]
        gain = sum(deltas) / len(deltas)
        results[s["name"]] = {"gain": gain, "correct": correct, "stems": stems,
                              "quiz_id": quiz["quiz_id"]}
        print(f"  [info] {s['name']}: {correct}/3 correct, mean mastery gain {gain:+.4f}")
        check(f"{s['name']} quiz completed", len(deltas) == 3)

    print("\nrepetition")
    reps, pairs = 0, 0
    vecs = {name: embedder.embed(r["stems"]) for name, r in results.items()}
    for name, embs in vecs.items():
        for i in range(len(embs)):
            for j in range(i):
                pairs += 1
                if cosine(embs[i], embs[j]) >= 0.9:
                    reps += 1
    rate = reps / pairs if pairs else 0.0
    check("repetition < 5%", rate < REPETITION_MAX, f"{reps}/{pairs} = {rate:.1%}")

    print("\nleakage (the key result)")
    probe = embedder.embed(["What is an eigenvector?"])[0]
    seen: dict[str, set[str]] = {}
    # Direct vector check: the same probe must retrieve disjoint chunk sets.
    from app.db import Database
    from app.vectorstore import VectorStore
    db = Database(get_settings())
    try:
        store = VectorStore(db)
        for s in students:
            seen[s["name"]] = {h.id for h in store.query(probe, project_id=s["project_id"], k=6)}
        names = list(seen)
        disjoint = all(not (seen[a] & seen[b]) for i, a in enumerate(names) for b in names[i + 1:])
        check("chunk sets disjoint across projects", disjoint,
              " vs ".join(f"{n}={len(seen[n])}" for n in names))
    finally:
        db.close()
    # API tenancy across the simulated learners.
    a, b = students[0], students[1]
    other_quiz = results[b["name"]]["quiz_id"]
    check("cannot answer another's quiz",
          c.post(f"/quizzes/{other_quiz}/answer",
                 json={"question_id": "00000000-0000-0000-0000-000000000000", "answer": 0},
                 headers=a["headers"]).status_code == 404)
    check("cannot read another's learner",
          c.get(f"/projects/{b['project_id']}/learner", headers=a["headers"]).status_code == 404)

    print("\ncleanup")
    for s in students:
        c.delete(f"/projects/{s['project_id']}", headers=s["headers"])
    c.close()

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("SIMULATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
