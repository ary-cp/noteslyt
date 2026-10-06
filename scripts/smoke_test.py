"""
End-to-end smoke test against a running backend (real or mock LLM).

    python scripts/smoke_test.py [http://localhost:8000] [path/to.pdf]

Uploads a PDF, asks a grounded and an out-of-scope question, runs a 3-question
adaptive quiz and checks that the learner model moved. Exits non-zero on failure.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def main() -> None:
    if len(sys.argv) > 2:
        pdf = Path(sys.argv[2])
    else:
        from make_sample_pdf import main as make_pdf  # type: ignore[import-not-found]

        pdf = Path(tempfile.gettempdir()) / "sample_lecture.pdf"
        make_pdf(str(pdf))

    c = httpx.Client(base_url=BASE, timeout=300)
    print("health      :", c.get("/health").json())

    with pdf.open("rb") as fh:
        doc = c.post("/upload", files={"file": (pdf.name, fh, "application/pdf")}).raise_for_status().json()
    print(f"upload      : {doc['total_pages']} pages, {doc['chunk_count']} chunks, {len(doc['topics'])} topics via {doc['parser']}")
    assert doc["chunk_count"] > 0 and doc["topics"]

    ans = c.post("/chat", json={"document_id": doc["id"], "question": "What is an eigenvalue?"}).raise_for_status().json()
    print(f"chat        : grounded={ans['grounded']} citations={[x['page'] for x in ans['citations']]}")
    print("              ", ans["answer"][:160].replace("\n", " "))
    assert ans["grounded"] and ans["citations"], "expected a cited answer"

    ref = c.post("/chat", json={"document_id": doc["id"], "question": "Who won the 2018 football world cup?"}).raise_for_status().json()
    print(f"refusal     : grounded={ref['grounded']} -> {ref['answer']}")
    assert not ref["grounded"], "expected a refusal for an off-topic question"

    quiz = c.post("/quiz", json={"user_id": "smoke", "document_id": doc["id"]}).raise_for_status().json()
    print(f"quiz        : topic={quiz['topic']['name']!r} p={quiz['topic']['p_mastery']} questions={len(quiz['questions'])}")
    before = quiz["topic"]["p_mastery"]
    last = None
    for q in quiz["questions"]:
        last = c.post("/quiz/answer", json={"user_id": "smoke", "quiz_id": quiz["quiz_id"], "question_id": q["id"], "selected_index": 0}).raise_for_status().json()
        print(f"  answer    : correct={last['correct']} mastery {last['p_before']:.2f} -> {last['p_after']:.2f} (page {last['page']})")
    assert last and last["p_after"] != before, "learner model did not update"

    c.delete(f"/documents/{doc['id']}").raise_for_status()
    print("cleanup     : document removed")
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
