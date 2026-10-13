"""Live RAG evidence capture on NASA/NIST documents in a disposable database.

Run: uv run python eval/run_real_documents.py [--limit 4] [--pause 15]
Uses configured real providers; requires local PostgreSQL + pgvector. Downloads
hash-pinned public PDFs, indexes their full text (page images disabled), saves
answers and exact context for review, and drops ONLY its own scratch database.
Does not assign optimistic automatic accuracy scores or alter production data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pause", type=float, default=15)
    parser.add_argument("--holdout", action="store_true", help="Run the six held-out questions instead of development cases")
    args = parser.parse_args()
    if args.limit < 0 or args.pause < 0:
        parser.error("limit and pause must be non-negative")
    os.chdir(ROOT)
    from app.config import get_settings
    settings = get_settings()
    if settings.db_backend != "postgres":
        parser.error("This isolated benchmark requires PostgreSQL.")
    dataset = json.loads((ROOT / "eval/real_document_testset.json").read_text())
    run_id = uuid.uuid4().hex
    out = ROOT / "data/rag-benchmarks" / run_id
    out.mkdir(parents=True)
    corpus = ROOT / "data/rag-benchmarks/corpus"
    corpus.mkdir(exist_ok=True)
    paths = {}
    for doc in dataset["documents"]:
        path = corpus / (doc["id"] + ".pdf")
        if not path.exists():
            response = httpx.get(doc["url"], follow_redirects=True, timeout=90)
            response.raise_for_status()
            path.write_bytes(response.content)
        if hashlib.sha256(path.read_bytes()).hexdigest() != doc["sha256"]:
            raise RuntimeError(f"{doc['id']} changed: review document and benchmark references before rerunning")
        paths[doc["id"]] = [path]
    from smoke_test import sample_pdf, sample_xlsx
    paths["regression"] = [sample_pdf(), sample_xlsx()]
    admin = make_conninfo(**{**conninfo_to_dict(settings.database_url), "dbname": "postgres"})
    name = "noteslyt_benchmark_" + run_id
    endpoint = urlsplit(settings.database_url)
    env = {
        "DATABASE_URL": urlunsplit((endpoint.scheme, endpoint.netloc, "/" + name, endpoint.query, endpoint.fragment)),
        "AUTH_SECRET_KEY": "benchmark-only-" + run_id,
        "DATA_DIR": str(out / "runtime"), "PDF_PAGE_IMAGES": "false",
        "AUDIO_EMBED_MODE": "transcript", "VIDEO_EMBED_MODE": "transcript", "PARSER": "local",
    }
    old_env = {key: os.environ.get(key) for key in env}
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        os.environ.update(env)
        get_settings.cache_clear()
        from fastapi.testclient import TestClient
        from app.main import app
        import app.chat as chat_module
        with TestClient(app, raise_server_exceptions=False) as client:
            token = client.post("/auth/signup", json={"email": run_id + "@test.co",
                "password": run_id, "display_name": "RAG benchmark"}).json()["access_token"]
            headers = {"Authorization": "Bearer " + token}
            projects = {}
            for doc, files in paths.items():
                project = client.post("/projects", json={"title": "Benchmark " + doc}, headers=headers)
                project.raise_for_status()
                pid = project.json()["id"]
                projects[doc] = pid
                response = client.post(f"/projects/{pid}/sources", headers=headers,
                    files=[("files", (path.name, path.read_bytes())) for path in files])
                response.raise_for_status()
                state = client.get(f"/projects/{pid}/ingest-status", headers=headers).json()
                (out / (doc + "-ingestion.json")).write_text(json.dumps(state, indent=2))
                if not state["settled"] or state["counts"].get("failed"):
                    raise RuntimeError("Ingestion incomplete; inspect saved status")
                print(doc, state["counts"], flush=True)
            trace = {}
            original_format = chat_module.format_context
            def capture(chunks, **kwargs):
                text = original_format(chunks, **kwargs)
                trace.update(text=text, chunks=[{"source_id": str(c.source_id),
                    "kind": c.locator_kind, "value": c.locator_value, "text": c.text,
                    "distance": c.distance} for c in chunks])
                return text
            chat_module.format_context = capture
            rows = []
            try:
                selected_cases = dataset["holdout_items"] if args.holdout else dataset["items"]
                cases = selected_cases[:args.limit] if args.limit else selected_cases
                for case in cases:
                    time.sleep(args.pause)
                    trace.clear()
                    chat = client.post(f"/projects/{projects[case['doc']]}/chats", json={}, headers=headers).json()
                    started = time.monotonic()
                    response = client.post(f"/chats/{chat['id']}/messages", headers=headers,
                        json={"content": case["question"]})
                    result = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"error": response.status_code}
                    rows.append({**case, "http": response.status_code, "response": result,
                        "seconds": round(time.monotonic() - started, 2), "context": dict(trace)})
                    (out / "answers.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                    print(case["id"], response.status_code, flush=True)
            finally:
                chat_module.format_context = original_format
            (out / "method.json").write_text(json.dumps({"documents": dataset["documents"],
                "llm": settings.llm_model, "embedding": settings.embedding_model,
                "page_images": False, "pause_seconds": args.pause,
                "grading": "Manual source-grounded review required; HTTP success is not correctness."}, indent=2))
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
    print("Evidence saved:", out)


if __name__ == "__main__":
    main()
