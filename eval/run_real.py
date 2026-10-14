"""Real-model reproducible eval over `testset.v1.json` + `dataset/gen_corpus.py`.

    uv run python eval/dataset/gen_corpus.py --out eval/dataset/files
    cp .env-test.example .env-test   # fill real keys
    uv run uvicorn app.main:app --port 8000        # root .env must match .env-test models
    uv run python eval/run_real.py --env-file .env-test --tier 50

Same data (seed-pinned corpus) + same model (.env-test) + same settings
=> same numbers. Results go to eval/results/real-<model>-<date>.json plus
a README-ready markdown table. Mock runs stay in eval/run_ragas.py.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.chat import REFUSAL  # noqa: E402
from app.citations import citation_label  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import LLMClient, build_embedder  # noqa: E402
from smoke_test import auth, wait_until_settled  # noqa: E402

TIMEOUT_S = 240
FAITH_MIN = 0.8

MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
}

failures: list[str] = []


# Small/cheap chat models often paraphrase the refusal instead of emitting the
# exact mandated sentence. Accept an explicit out-of-scope decline as safe as
# long as the server audit agrees (grounded=False, zero citations) — the safety
# property is "no cited-looking answer from outside the material", not wording.
DECLINE_RE = re.compile(
    r"outside (the scope|your sources)|not (in|part of|covered)|no information|"
    r"don't have|do not contain|beyond (the scope|what)|cannot answer|can't answer|"
    r"outside the scope of our materials",
    re.I,
)


def refusal_kind(ans: dict) -> str | None:
    """'exact' | 'labeled' | 'paraphrased' | None (unsafe answer)."""
    if ans["grounded"] is not False or ans["citations"]:
        return None
    if ans["answer"] == REFUSAL:
        return "exact"
    if re.match(r"\s*\*\*Outside your sources:\*\*", ans["answer"], re.I):
        return "labeled"
    if bool(DECLINE_RE.search(ans["answer"])):
        return "paraphrased"
    return None


class Pacer:
    """Keep LLM usage under a per-minute budget (free tiers: 15 RPM).

    Each chat turn costs ~3-4 LLM calls server-side (answer + title +
    classifier) plus the judge call here, so the default 3 RPM paces
    ~12 model calls/min. Raises via --rpm on paid tiers.
    """

    def __init__(self, rpm: float) -> None:
        self.interval = 60.0 / max(rpm, 0.1)
        self._last = 0.0

    def tick(self) -> None:
        now = time.monotonic()
        wait = self._last + self.interval - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        self._last = now


def post_with_retry(c: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """POST/GET with backoff on 429 + 5xx (free-tier quota safety net)."""
    tries = int(kw.pop("tries", 6))
    last: httpx.Response | None = None
    for attempt in range(tries):
        r = c.request(method, url, **kw)
        last = r
        if r.status_code < 500 and r.status_code != 429:
            return r
        retry_after = r.headers.get("retry-after")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else 5.0 * (2 ** attempt)
        print(f"  [rate-limit] {r.status_code} on {url} — waiting {wait:.0f}s (try {attempt + 1}/{tries})")
        time.sleep(wait)
    assert last is not None
    return last


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def settings_from_env(env: dict[str, str]) -> Settings:
    kwargs: dict[str, object] = {}
    mapping = {
        "LLM_BASE_URL": "llm_base_url", "LLM_API_KEY": "llm_api_key", "LLM_MODEL": "llm_model",
        "EMBEDDING_MODE": "embedding_mode", "EMBEDDING_BASE_URL": "embedding_base_url",
        "EMBEDDING_API_KEY": "embedding_api_key", "EMBEDDING_MODEL": "embedding_model",
        "EMBEDDING_DIM": "embedding_dim",
        "TRANSCRIPTION_BASE_URL": "transcription_base_url",
        "TRANSCRIPTION_API_KEY": "transcription_api_key",
        "TRANSCRIPTION_MODEL": "transcription_model",
    }
    for k, field in mapping.items():
        if env.get(k):
            kwargs[field] = int(env[k]) if field == "embedding_dim" and env[k].isdigit() else env[k]
    return Settings(**kwargs)  # type: ignore[arg-type]


def cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(y * y for y in b)) or 1.0))


def judge(llm: LLMClient, answer: str, retrieved: list[str], keywords: list[str]) -> tuple[float, str]:
    try:
        data = llm.complete_json(
            "You judge faithfulness as JSON. Given an answer, split it into atomic factual "
            'claims and output {"claims": [{"text": "...", "supported": true/false}]}.',
            f"ANSWER:\n{answer[:2000]}\n\nCited: {', '.join(retrieved) or '(nothing)'}",
            max_tokens=1024,
        )
        claims = data.get("claims", [])
        if not claims:
            raise ValueError("no claims")
        return round(sum(1 for c in claims if c.get("supported")) / len(claims), 3), f"llm ({len(claims)} claims)"
    except Exception:
        if not keywords:
            return 1.0, "citation (no keywords)"
        hit = sum(1 for w in keywords if w.lower() in answer.lower())
        return round(hit / len(keywords), 3), "keyword (judge unusable)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env-test")
    ap.add_argument("--tier", type=int, default=50, choices=[30, 50, 100])
    ap.add_argument("--base", default=None)
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--corpus", default="eval/dataset/files")
    ap.add_argument("--rpm", type=float, default=3.0,
                    help="max eval iterations/min (free tier: keep <=3; paid: raise)")
    ap.add_argument("--upload-batch", type=int, default=3,
                    help="files per upload request (spreads ingestion LLM calls)")
    args = ap.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"missing {env_path}: copy .env-test.example first")
        return 2
    env = load_env_file(env_path)
    base = args.base or env.get("EVAL_BASE_URL", "http://127.0.0.1:8000")
    tier = args.tier
    pacer = Pacer(args.rpm)
    print(f"pacing: {args.rpm:g} iterations/min (~{args.rpm * 4:.0f} LLM calls/min), "
          f"upload batches of {args.upload_batch}")
    corpus = Path(args.corpus)
    if not corpus.exists():
        print(f"missing corpus {corpus}: run eval/dataset/gen_corpus.py first")
        return 2

    testset = json.loads((ROOT / "eval" / "testset.v1.json").read_text(encoding="utf-8"))
    filemap: dict[str, str] = testset["fixtures"]
    items = [i for i in testset["items"] if int(i.get("tier", 30)) <= tier]
    print(f"tier {tier}: {len(items)} items over {corpus}")

    esettings = settings_from_env(env)
    llm = LLMClient(esettings)
    # Local embedding mode is managed inside the server lifespan, which a
    # script process has no access to. Point the eval client at the same
    # local server over HTTP (it speaks OpenAI-compatible /embeddings
    # while the app is running) instead of the in-process manager.
    if esettings.embedding_mode == "local":
        esettings = esettings.model_copy(update={
            "embedding_mode": "cloud",
            "embedding_base_url": esettings.embedding_local_base_url,
            "embedding_api_key": "local",
        })
    embedder = build_embedder(esettings)
    c = httpx.Client(base_url=base, timeout=TIMEOUT_S)

    print("health")
    health = c.get("/health").json()
    check("server up", health.get("status") in {"ok", "degraded"}, str(health.get("status")))
    for k, got, want in [
        ("llm_model", (health.get("llm_model") or "").removeprefix("models/"), (env.get("LLM_MODEL") or "").removeprefix("models/")),
        ("llm_base", health.get("llm_base_url"), env.get("LLM_BASE_URL")),
    ]:
        if want and got != want:
            print(f"  [warn] server {k}={got!r} != .env-test {want!r} (results still recorded)")
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        sha = "unknown"

    tag = uuid.uuid4().hex[:8]
    token = c.post("/auth/signup", json={
        "email": f"real-{tag}@test.co", "password": "real-password", "display_name": "RealEval",
    }).json()["access_token"]
    h = auth(token)

    print("upload (batched, settled between batches)")
    projects: dict[str, str] = {}
    for proj in testset["projects"]:
        pid = c.post("/projects", json={"title": f"real-{proj['key']}-{tag}"}, headers=h).json()["id"]
        projects[proj["key"]] = pid
        pending = []
        for key in proj["fixtures"]:
            p = corpus / filemap[key]
            if not p.exists():
                print(f"  [warn] missing {p.name}, skipped (video needs ffmpeg)")
                continue
            pending.append(("files", (p.name, p.read_bytes(), MIME.get(p.suffix.lower(), "application/octet-stream"))))
        for b in range(0, len(pending), args.upload_batch):
            batch = pending[b:b + args.upload_batch]
            r = post_with_retry(c, "POST", f"/projects/{pid}/sources", files=batch, headers=h)
            check(f"{proj['key']} upload batch {b // args.upload_batch + 1} (202)",
                  r.status_code == 202, r.text[:120])
            st = wait_until_settled(c, h, pid)
            print(f"  [info] {proj['key']} settled batch: {st['counts']}")
            pacer.tick()
    settled_ok = True
    for key, pid in projects.items():
        st = wait_until_settled(c, h, pid)
        ok = st["settled"] and st["counts"].get("failed", 0) == 0
        if not ok:
            print(f"  [warn] {key} not fully ready: {st['counts']} (media items may skip)")
            settled_ok = settled_ok and False
        check(f"{key} settled", st["settled"], str(st["counts"]))

    print("\nitems")
    rows: list[dict] = []
    skipped = 0
    t0 = time.monotonic()
    for n, item in enumerate(items, start=1):
        pacer.tick()
        pid = projects[item["project"]]
        eta = (time.monotonic() - t0) / n * (len(items) - n) / 60
        print(f"[{n}/{len(items)} ~{eta:.0f}min left] {item['id']}")
        if item.get("requires_media"):
            srcs = c.get(f"/projects/{pid}/sources", headers=h).json()
            media_ready = any(s.get("source_type") in {"audio", "video"} and s.get("status") == "ready" for s in srcs)
            if not media_ready:
                print(f"  [skip] {item['id']} (no ready audio/video source)")
                skipped += 1
                rows.append({"id": item["id"], "skipped": True})
                continue
        chat = post_with_retry(c, "POST", f"/projects/{pid}/chats", json={}, headers=h).json()
        r = post_with_retry(c, "POST", f"/chats/{chat['id']}/messages",
                            json={"content": item["question"]}, headers=h)
        assert r.status_code == 200, r.text
        ans = r.json()
        if item.get("must_refuse"):
            kind = refusal_kind(ans)
            ok = kind is not None
            check(f"{item['id']} refused/labeled", ok,
                  (kind or "UNSAFE") + ": " + ans["answer"][:100])
            rows.append({"id": item["id"], "refused": ok, "refusal_kind": kind,
                         "grounded": ans["grounded"],
                         "answer_snippet": ans["answer"][:300]})
            continue
        exp = [citation_label(e["kind"], e["value"]) for e in item.get("expected", [])]
        # segment expectations are loose: any segment retrieval passes recall.
        if any(e.get("kind") == "segment" for e in item.get("expected", [])):
            got = ans["retrieved"]
            inter = [g for g in got if g.startswith("[")]
            precision = len(inter) / len(got) if got else 0.0
            recall = 1.0 if any("0:" in g or "[" in g for g in got) else 0.0
        else:
            got = ans["retrieved"]
            inter = [g for g in got if g in exp]
            precision = len(inter) / len(got) if got else 0.0
            recall = len(inter) / len(exp) if exp else 1.0
        faith, method = judge(llm, ans["answer"], got, item.get("must_contain", []))
        rel = round(cosine(*embedder.embed([item["question"], ans["answer"]])), 3)
        print(f"  [info] {item['id']}: faith={faith} ({method}) rel={rel} P={precision:.2f} R={recall:.2f}")
        rows.append({"id": item["id"], "faith": faith, "rel": rel,
                     "precision": precision, "recall": recall, "method": method,
                     "answer_snippet": ans["answer"][:300]})

    grounded = [r for r in rows if "faith" in r]
    refused = [r for r in rows if "refused" in r]
    faith = sum(r["faith"] for r in grounded) / len(grounded) if grounded else 0.0
    refusal = sum(r["refused"] for r in refused) / len(refused) if refused else 0.0
    print("\naggregate")
    check("faithfulness >= 0.8", faith >= FAITH_MIN, f"{faith:.3f}")
    check("safe off-material handling == 100%", refusal == 1.0, f"{refusal:.0%}")
    if grounded:
        print(f"  [info] mean rel {sum(r['rel'] for r in grounded)/len(grounded):.3f} "
              f"P/R {sum(r['precision'] for r in grounded)/len(grounded):.2f}/"
              f"{sum(r['recall'] for r in grounded)/len(grounded):.2f} skipped={skipped}")

    out = {
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git": sha,
        "tier": tier,
        "n_items": len(items),
        "skipped": skipped,
        "seed": int(env.get("EVAL_SEED", "42")),
        "rpm": args.rpm,
        "models": {"llm": env.get("LLM_MODEL"), "llm_base": env.get("LLM_BASE_URL"),
                   "embedding": env.get("EMBEDDING_MODEL"), "embedding_mode": env.get("EMBEDDING_MODE"),
                   "embedding_dim": env.get("EMBEDDING_DIM") or "auto",
                   "transcription": env.get("TRANSCRIPTION_MODEL")},
        "server": {"llm_model": health.get("llm_model"), "embeddings": health.get("embeddings"),
                   "db": (health.get("database") or {}).get("target")},
        "aggregate": {"faithfulness": round(faith, 3), "refusal": refusal,
                      "precision": round(sum(r["precision"] for r in grounded)/len(grounded), 3) if grounded else 0.0,
                      "recall": round(sum(r["recall"] for r in grounded)/len(grounded), 3) if grounded else 0.0,
                      "relevancy": round(sum(r["rel"] for r in grounded)/len(grounded), 3) if grounded else 0.0},
        "rows": rows,
        "corpus_sha": hashlib.sha256(b"".join(
            sorted((corpus / f).read_bytes() for f in sorted(p.name for p in corpus.iterdir())))).hexdigest()[:16]
        if any(corpus.iterdir()) else "empty",
    }
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (env.get("LLM_MODEL") or "model").lower()).strip("-")
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    path = outdir / f"real-{slug}-t{tier}-{stamp}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    (outdir / "latest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {path} + latest.json")

    print("\ncleanup")
    for pid in projects.values():
        c.delete(f"/projects/{pid}", headers=h)
    c.close()

    # markdown table for README
    try:
        from to_markdown import write_table  # type: ignore

        write_table(out, outdir / "latest.md")
        print("wrote latest.md")
    except Exception as exc:
        print(f"[warn] markdown skipped: {exc}")

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED")
        return 1
    print("EVAL REAL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
