"""
RAGAS-style eval over `testset.json` â€” Phase G.32/G.33.

    uv run python eval/run_ragas.py [http://localhost:8000]

No `ragas` package needed: the four metrics are computed with this repo's own
LLM client + embedder, so the eval runs offline against `scripts/mock_llm.py`
exactly like the rest of the suite:

  * source faithfulness — LLM-judged claims against the actual fixture text.
    This is source grounding, not a claim that the exact retrieved context was
    sufficient. An unavailable judge is reported as unavailable, never replaced
    by a keyword score. Mock runs are smoke checks only.
  * answer_relevancy â€” cosine(question, answer) via the configured embedder.
  * context_precision / context_recall â€” retrieved labels vs the testset's
    expected locators (locator-level, deterministic).
  * refusal accuracy â€” off-material items must return the exact refusal,
    ungrounded. (Plan target: 100%.)

Gates (plan.md Phase G): faithfulness >= 0.8 and refusal accuracy == 100%.
Everything else is reported, not gated. Each item runs in a fresh chat so
turns never leak context into each other.

Needs a running server + model endpoints (or the mock), same as smoke_test.py.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app.chat import REFUSAL  # noqa: E402
from app.citations import citation_label  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm import LLMClient, build_embedder  # noqa: E402
from smoke_test import auth, sample_docx, sample_pdf, sample_xlsx, wait_until_settled  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
TIMEOUT_S = 240
FAITHFULNESS_MIN = 0.8

JUDGE_SYSTEM = (
    "You judge faithfulness as JSON. Given source excerpts and an answer, split "
    "the answer into atomic factual claims and output "
    '{"claims": [{"text": "...", "supported": true/false}]}. A claim is supported '
    "only if the excerpts state it (the quoted passage counts). Ignore citations "
    "and formatting when splitting."
)

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" â€” {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def fixtures() -> dict[str, tuple[str, bytes, str]]:
    pdf, docx, xlsx = sample_pdf(), sample_docx(), sample_xlsx()
    return {
        "linear_pdf": (pdf.name, pdf.read_bytes(), "application/pdf"),
        "notes_docx": (docx.name, docx.read_bytes(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "costs_xlsx": (xlsx.name, xlsx.read_bytes(),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    }


def expected_labels(item: dict) -> list[str]:
    return [citation_label(loc["kind"], loc["value"]) for loc in item.get("expected", [])]


def fixture_texts() -> dict[str, str]:
    """Reference text from the same fixture generators used for upload."""
    from make_sample_pdf import SLIDES
    return {
        "linear_pdf": "\n".join(f"Page {i}: {title}\n{body}" for i, (title, body) in enumerate(SLIDES, 1)),
        "notes_docx": "Thermodynamics: Entropy is the measure of disorder in a system.\nFirst law: Energy is conserved in every thermodynamic process.",
        "costs_xlsx": "Operating Costs: item | cost\nsteel | 120\ncoke | 80\nlabor | 200",
    }


def judge_faithfulness(llm: LLMClient, answer: str, source_text: str) -> tuple[float | None, str]:
    """No evidence-free judge or optimistic keyword fallback."""
    if not source_text.strip():
        return None, "unavailable (no source text)"
    try:
        data = llm.complete_json(
            JUDGE_SYSTEM,
            f"SOURCE EXCERPTS:\n{source_text}\n\nANSWER:\n{answer}",
            max_tokens=2048,
        )
        claims = data.get("claims")
        if not isinstance(claims, list) or not claims or any(
            not isinstance(cl, dict) or type(cl.get("supported")) is not bool for cl in claims
        ):
            raise ValueError("invalid judge claims")
        score = sum(cl["supported"] for cl in claims) / len(claims)
        return round(score, 3), f"llm source-grounding ({len(claims)} claims)"
    except Exception:
        return None, "unavailable (judge failed; keyword presence is not faithfulness)"


def cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def main() -> int:
    settings = get_settings()
    llm = LLMClient(settings)
    embedder = build_embedder(settings)
    testset = json.loads((Path(__file__).parent / "testset.json").read_text(encoding="utf-8"))
    files = fixtures()
    references = fixture_texts()
    project_text = {p["key"]: "\n\n".join(references[k] for k in p["fixtures"]) for p in testset["projects"]}
    tag = uuid.uuid4().hex[:8]
    c = httpx.Client(base_url=BASE, timeout=TIMEOUT_S)

    print("setup")
    health = c.get("/health").json()
    check("server healthy", health["status"] == "ok")
    token = c.post("/auth/signup", json={
        "email": f"eval-{tag}@test.co", "password": "eval-password", "display_name": "Eval",
    }).json()["access_token"]
    h = auth(token)
    projects: dict[str, str] = {}
    for proj in testset["projects"]:
        pid = c.post("/projects", json={"title": f"eval-{proj['key']}-{tag}"}, headers=h).json()["id"]
        projects[proj["key"]] = pid
        c.post(f"/projects/{pid}/sources",
               files=[("files", files[name]) for name in proj["fixtures"]], headers=h)
    for key, pid in projects.items():
        status = wait_until_settled(c, h, pid)
        check(f"{key} ingested", status["settled"] and status["counts"].get("failed", 0) == 0,
              str(status["counts"]))

    print("\nitems")
    rows = []
    for item in testset["items"]:
        pid = projects[item["project"]]
        chat = c.post(f"/projects/{pid}/chats", json={}, headers=h).json()
        r = c.post(f"/chats/{chat['id']}/messages", json={"content": item["question"]}, headers=h)
        assert r.status_code == 200, r.text
        ans = r.json()
        if item.get("must_refuse"):
            ok = ans["grounded"] is False and ans["answer"] == REFUSAL and ans["citations"] == []
            check(f"{item['id']} refused", ok, ans["answer"][:60])
            rows.append({"id": item["id"], "refused": ok})
            continue
        exp = expected_labels(item)
        got = ans["retrieved"]
        inter = set(got) & set(exp)
        precision = len(inter) / len(set(got)) if got else 0.0
        recall = len(inter) / len(set(exp)) if exp else 1.0
        keywords = [w for w in item.get("must_contain", []) if w.lower() in ans["answer"].lower()]
        faith, method = judge_faithfulness(llm, ans["answer"], project_text[item["project"]])
        rel = round(cosine(*embedder.embed([item["question"], ans["answer"]])), 3)
        print(f"  [info] {item['id']}: faith={faith} ({method}) rel={rel} "
              f"P={precision:.2f} R={recall:.2f} kw={len(keywords)}/{len(item.get('must_contain', []))}")
        rows.append({"id": item["id"], "faith": faith, "rel": rel,
                     "precision": precision, "recall": recall})

    print("\naggregate")
    grounded = [r for r in rows if "faith" in r]
    refused = [r for r in rows if "refused" in r]
    judged = [r for r in grounded if r["faith"] is not None]
    faith = sum(r["faith"] for r in judged) / len(judged) if judged else None
    refusal = sum(r["refused"] for r in refused) / len(refused) if refused else 0.0
    if len(judged) == len(grounded) and faith is not None:
        check("source faithfulness >= 0.8", faith >= FAITHFULNESS_MIN, f"{faith:.3f}")
    elif settings.llm_model == "mock":
        print("  [info] Faithfulness UNAVAILABLE: this mock run only checks plumbing.")
    else:
        check("all answers have a valid source-grounding judgment", False, f"{len(judged)}/{len(grounded)} judged")
    check("refusal accuracy == 100%", refusal == 1.0, f"{refusal:.0%}")
    if grounded:
        print(f"  [info] mean relevancy {sum(r['rel'] for r in grounded) / len(grounded):.3f}, "
              f"mean context P/R "
              f"{sum(r['precision'] for r in grounded) / len(grounded):.2f}/"
              f"{sum(r['recall'] for r in grounded) / len(grounded):.2f}")

    print("\ncleanup")
    for pid in projects.values():
        c.delete(f"/projects/{pid}", headers=h)
    c.close()

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("EVAL PASSED" + (" (mock smoke checks only; not real accuracy)" if settings.llm_model == "mock" else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
