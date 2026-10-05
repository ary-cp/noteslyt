"""
Offline mock of an OpenAI-compatible server — rehearse the whole app with NO
API keys, no LM Studio, no internet.

    python scripts/mock_llm.py            # listens on http://127.0.0.1:9999/v1

Then point the backend at it:

    LLM_BASE_URL=http://127.0.0.1:9999/v1  LLM_API_KEY=x  EMBEDDING_PROVIDER=openai  EMBEDDING_MODEL=mock

It fakes /v1/embeddings (hashed bag-of-words: similar text -> similar vectors)
and /v1/chat/completions (recognises the three prompts the app sends: topic
extraction, quiz generation, grounded answer). Also fakes Firecrawl's
/v2/parse so the Firecrawl code path can be exercised offline.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

DIM = 128
REFUSAL = "I can't find this in your uploaded material."


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 8) % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def pages_in(text: str) -> list[int]:
    return sorted({int(p) for p in re.findall(r"\[Page (\d+)\]", text)})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # quiet
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        if self.path.endswith("/v2/parse"):  # Firecrawl mock
            return self._json(200, {"success": True, "data": {
                "markdown": "", "metadata": {"title": "Mock", "numPages": 2, "totalPages": 2},
                "pages": [{"pageNumber": 1, "markdown": "# Page one\n\nSome content."},
                          {"pageNumber": 2, "markdown": "# Page two\n\nMore content."}]}})

        req = json.loads(raw or b"{}")
        if self.path.endswith("/embeddings"):
            inputs = req["input"] if isinstance(req["input"], list) else [req["input"]]
            return self._json(200, {"object": "list", "model": req.get("model"), "usage": {},
                                    "data": [{"object": "embedding", "index": i, "embedding": embed(t)} for i, t in enumerate(inputs)]})

        if self.path.endswith("/chat/completions"):
            msgs = req["messages"]
            system = msgs[0]["content"]
            convo = "\n".join(m["content"] for m in msgs if m["role"] == "user")
            pages = pages_in(convo)
            if "instructional designer" in system:
                content = json.dumps({"topics": [
                    {"id": f"topic-{i + 1}", "name": f"Topic {i + 1} (pages {p})", "description": f"Material on page {p}.", "pages": [p]}
                    for i, p in enumerate(pages[:6] or [1])]})
            elif "multiple-choice" in system:
                content = json.dumps({"questions": [
                    {"question": f"Mock question {i + 1} about page {pages[i % len(pages)] if pages else 1}?",
                     "options": ["Correct answer", "Distractor A", "Distractor B", "Distractor C"],
                     "correct_index": 0, "explanation": "The slide states this directly.", "page": pages[i % len(pages)] if pages else 1}
                    for i in range(3)]})
            else:
                question = convo.split("STUDENT QUESTION:")[-1].lower()
                if not pages or "world cup" in question:
                    content = REFUSAL
                else:
                    content = f"**Key idea.** The material covers this on [Page {pages[0]}]" + (
                        f" and continues on [Page {pages[1]}]." if len(pages) > 1 else ".")
            return self._json(200, {"id": "mock", "object": "chat.completion", "model": req.get("model"), "usage": {},
                                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})

        self._json(404, {"error": "unknown route"})


if __name__ == "__main__":
    print("Mock OpenAI-compatible server on http://127.0.0.1:9999/v1  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", 9999), Handler).serve_forever()
