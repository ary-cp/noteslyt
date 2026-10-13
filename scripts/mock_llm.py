"""
Offline mock of an OpenAI-compatible server — rehearse the whole app with NO
API keys, no LM Studio, no internet.

    python scripts/mock_llm.py            # listens on http://127.0.0.1:9999/v1
    MOCK_LLM_PORT=9998 python scripts/mock_llm.py   # a second, separate server

Then point the backend at it:

    LLM_BASE_URL=http://127.0.0.1:9999/v1  LLM_API_KEY=x  EMBEDDING_PROVIDER=openai  EMBEDDING_MODEL=mock

Each capability resolves its own base URL, so run one mock per provider and
mix them the way you would in production:

    LLM_BASE_URL=http://127.0.0.1:9999/v1              # chat  -> "Groq"
    EMBEDDING_BASE_URL=http://127.0.0.1:9998/v1        # vec   -> "OpenAI"
    TRANSCRIPTION_BASE_URL=http://127.0.0.1:9999/v1    # audio -> "Groq"

It fakes /v1/embeddings (hashed bag-of-words: similar text -> similar vectors)
and /v1/chat/completions (recognises the prompts the app sends: topic
extraction, query rewrite, summarization, chat-evidence classification,
profile refinement, quiz generation, grounded answer). Also fakes Firecrawl's
/v2/parse so the Firecrawl code path can be exercised offline.

NOTE: the BoW vectors are weak by construction (short-query/long-chunk cosine
dilution), so a zero-key run cannot exercise the retrieval distance gate —
start the backend with MAX_DISTANCE=2.0 (disabled) and rely on the LLM
refusal sentence for the refusal path. The gate logic itself is covered by
scripts/verify_data_layer.py's degenerate-vector check.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# Embedding width must match the `vector(...)` column and EMBEDDING_DIM.
# Defaults to 1536 (text-embedding-3-small). Override with
# MOCK_EMBEDDING_DIM if your schema was created at a different width.
DIM = int(os.environ.get("MOCK_EMBEDDING_DIM", "1536"))
REFUSAL = "I can't find this in your uploaded material."


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 8) % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def locators_in(text: str) -> list[tuple[str, str]]:
    """
    Pull locator labels out of a prompt, in every form the app emits.

    Ingestion outlines use `[kind:value]`; chat CONTEXT blocks use the
    canonical D19 labels (`[Page 14] [Slide 7] [12:34] [Sheet Costs] …).
    Returns lowercase `(kind, value)` tuples.
    """
    found: list[tuple[str, str]] = [
        (k.lower(), v) for k, v in re.findall(r"\[([a-z_]+):([^\]]+)\]", text)
    ]
    for m in re.finditer(
        r"\[\s*Page\s+([^\]]+?)\s*\]"
        r"|\[\s*Slide\s+([^\]]+?)\s*\]"
        r"|\[\s*Sheet\s+([^\]]+?)\s*\]"
        r"|\[\s*Section\s+([^\]]+?)\s*\]"
        r"|\[\s*Row\s+([^\]]+?)\s*\]"
        r"|\[\s*Image\s+([^\]]+?)\s*\]"
        r"|\[\s*Segment\s+([^\]]+?)\s*\]"
        r"|\[\s*(\d{1,3}:\d{2}(?::\d{2})?)\s*\]",
        text,
    ):
        groups = m.groups()
        kinds = ("page", "slide", "sheet", "section", "row", "image", "segment")
        for kind, value in zip(kinds, groups[:7]):
            if value:
                found.append((kind, value.strip()))
                break
        else:
            if groups[7]:
                found.append(("segment", groups[7].strip()))
    return found


def canonical_label(kind: str, value: str) -> str:
    """The D19 label the audit accepts for a `(kind, value)` pair."""
    if kind == "page":
        return f"[Page {value}]"
    if kind == "slide":
        return f"[Slide {value}]"
    if kind == "segment":
        return f"[{value}]" if re.fullmatch(r"\d{1,3}:\d{2}(?::\d{2})?", value) else f"[Segment {value}]"
    if kind == "sheet":
        return f"[Sheet {value}]"
    if kind == "row":
        return f"[Row {value}]"
    if kind == "section":
        return f"[Section {value}]"
    if kind == "image":
        return f"[Image {value}]"
    return f"[{kind} {value}]"


class Handler(BaseHTTPRequestHandler):
    # Per-process quiz counter so generated stems stay unique across calls
    # (the novelty filter would otherwise see every call as duplicates).
    _quiz_calls = 0

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

        if "/audio/transcriptions" in self.path:  # Whisper mock (multipart body — do NOT json-parse it)
            return self._json(200, {"text": "Mock lecture transcript.",
                                    "segments": [
                                        {"start": 0.0, "end": 20.0,
                                         "text": "Welcome to thermodynamics. Today we define entropy and heat."},
                                        {"start": 20.0, "end": 45.0,
                                         "text": "The first law is energy conservation in every process."},
                                        {"start": 45.0, "end": 75.0,
                                         "text": "The second law says entropy never decreases in isolation."},
                                    ]})

        req = json.loads(raw or b"{}")
        if self.path.endswith("/embeddings"):
            inputs = req["input"] if isinstance(req["input"], list) else [req["input"]]
            # Nested multimodal parts (llama.cpp shape) have no text to hash —
            # stringify deterministically so native-mode tests get stable vectors.
            flat = [t if isinstance(t, str) else json.dumps(t, sort_keys=True) for t in inputs]
            return self._json(200, {"object": "list", "model": req.get("model"), "usage": {},
                                    "data": [{"object": "embedding", "index": i, "embedding": embed(t)} for i, t in enumerate(flat)]})

        if self.path.endswith("/chat/completions"):
            msgs = req["messages"]

            def _text(m) -> str:
                c = m.get("content", "")
                if isinstance(c, list):  # vision request: content parts
                    return " ".join(
                        p.get("text", "") for p in c
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                return str(c)

            def _has_image(m) -> bool:
                c = m.get("content", "")
                return isinstance(c, list) and any(
                    isinstance(p, dict) and p.get("type") == "image_url" for p in c
                )

            system = _text(msgs[0])
            convo = "\n".join(_text(m) for m in msgs if m["role"] == "user")
            found = locators_in(convo)
            # Vision caption (Phase D.20): describe the image as study text.
            if "create study flashcards" in system:
                want = re.search(r"Generate (\d+) distinct flashcards", convo)
                n = int(want.group(1)) if want else 3
                parts = re.split(r"TOPIC (\S+) \(", convo)
                contexts = []
                for i in range(1, len(parts), 2):
                    loc = re.search(r"SOURCE (\S+) LOCATOR ([^:]+):([^\n]+)", parts[i + 1])
                    if loc:
                        contexts.append((parts[i], *loc.groups()))
                content = json.dumps({"cards": [
                    {"front": f"Review concept {i + 1}", "back": "This is deterministic mock study content.",
                     "topic_slug": contexts[i % len(contexts)][0],
                     "locator": {"source_id": contexts[i % len(contexts)][1],
                                 "kind": contexts[i % len(contexts)][2],
                                 "value": contexts[i % len(contexts)][3]}}
                    for i in range(n)
                ] if contexts else []})
            elif any(_has_image(m) for m in msgs) and "STUDENT QUESTION:" in convo:
                content = ("The uploaded image contains a study diagram. " + canonical_label(*found[0])) if found else REFUSAL
            elif any(_has_image(m) for m in msgs) and not any(
                task in system for task in ("instructional designer", "write quiz questions", "multiple-choice")
            ):
                content = ("The image shows a labeled study diagram. "
                           "Key parts: pressure gauge reading 101 kPa, a piston chamber, "
                           "and an entropy-temperature chart with two curves.")
            # Query rewrite (Phase E.24): echo the follow-up back standalone.
            elif "standalone question" in system:
                content = _text(msgs[-1]).split("FOLLOW-UP:")[-1].strip() or _text(msgs[-1])
            # Rolling summary (Phase E.23): fixed condensed text.
            elif "summarize earlier" in system:
                content = ("So far the student asked about entropy and the first law, "
                           "and the tutor explained both with citations.")
            # Chat-evidence classification (Phase E.26): signal the first topic
            # mentioned, as a correct restatement, so nudges are exercised.
            elif "learner signals" in system:
                slugs = re.findall(r"topic-\d+", convo)
                content = json.dumps({"signals": (
                    [{"topic_slug": slugs[0], "correct": True}] if slugs else [])})
            # Global-memory refinement (Phase E.25): one observational counter.
            elif "refine the learner profile" in system:
                content = json.dumps({"study_pattern_add": {"chat_sessions": 1},
                                      "strengths_add": [],
                                      "capability_level": None})
            # Topic extraction: echo back the locators the outline advertised,
            # in the `locators` shape the real prompt asks for.
            elif "instructional designer" in system:
                seen: list[tuple[str, str]] = []
                for loc in found:
                    if loc not in seen:
                        seen.append(loc)
                for i in range(4):
                    seen.append(("page", str(i + 1)))
                seen = seen[:6]
                content = json.dumps({"topics": [
                    {"id": f"topic-{i + 1}", "name": f"Mock topic {i + 1} ({k} {v})",
                     "description": f"Material covering {k} {v}.",
                     "locators": [{"kind": k, "value": v}]}
                    for i, (k, v) in enumerate(seen)]})
            elif "multiple-choice" in system:
                content = json.dumps({"questions": [
                    {"question": f"Mock question {i + 1} about {found[i % len(found)] if found else ('page', '1')}?",
                     "options": ["Correct answer", "Distractor A", "Distractor B", "Distractor C"],
                     "correct_index": 0, "explanation": "The source states this directly.",
                     "locator": {"kind": found[i % len(found)][0], "value": found[i % len(found)][1]} if found else {"kind": "page", "value": "1"}}
                    for i in range(3)]})
            # Quiz generation (Phase F): one of each type per call, citing the
            # first advertised locator. Stems carry a per-call counter so the
            # novelty filter sees distinct questions across calls.
            elif "write quiz questions" in system:
                Handler._quiz_calls += 1
                call = Handler._quiz_calls
                want = re.search(r"exactly (\d+)", convo)
                n = max(1, min(int(want.group(1)) if want else 3, 10))
                # One locator pool per TOPIC section — a question must cite a
                # locator from its own topic, or verification drops it.
                per_topic: dict[str, tuple[str, str]] = {}
                source_for_topic: dict[str, str] = {}
                parts = re.split(r"TOPIC (\S+)", convo)
                for i in range(1, len(parts), 2):
                    locs = locators_in(parts[i + 1]) if i + 1 < len(parts) else []
                    scoped = re.search(r"SOURCE (\S+) LOCATOR ([^:]+):([^\n]+)", parts[i + 1])
                    if scoped:
                        source_for_topic[parts[i]] = scoped.group(1)
                        per_topic[parts[i]] = (scoped.group(2), scoped.group(3).strip())
                    else:
                        per_topic[parts[i]] = locs[0] if locs else ("page", "1")
                if not per_topic:
                    per_topic = {"topic-1": found[0] if found else ("page", "1")}
                slugs = list(per_topic)
                asked = re.search(r"types \[(.*?)\]", convo)
                kinds = [t.strip() for t in asked.group(1).split(",")] if asked else []
                kinds = [k for k in kinds if k in ("mcq", "short_answer", "numerical")]
                if not kinds:
                    kinds = ["mcq", "short_answer", "numerical"]
                made = []
                for i in range(n):
                    t = kinds[i % len(kinds)]
                    slug = slugs[i % len(slugs)]
                    k, v = per_topic[slug]
                    loc = {"kind": k, "value": v}
                    if slug in source_for_topic:
                        loc["source_id"] = source_for_topic[slug]
                    stem = f"Mock {t} {call}.{i} on {slug} ({k} {v})?"
                    if t == "mcq":
                        made.append({"type": t, "topic_slug": slug, "stem": stem,
                                     "options": ["Correct answer", "Distractor A",
                                                 "Distractor B", "Distractor C"],
                                     "correct_index": 0,
                                     "explanation": "The source states this directly.",
                                     "locator": dict(loc)})
                    elif t == "short_answer":
                        made.append({"type": t, "topic_slug": slug, "stem": stem,
                                     "key_points": ["eigenvector satisfies Av = lambda v",
                                                    "lambda is the eigenvalue"],
                                     "sample_answer": "An eigenvector satisfies Av = lambda v.",
                                     "explanation": "The source defines it directly.",
                                     "locator": dict(loc)})
                    else:
                        made.append({"type": t, "topic_slug": slug, "stem": stem,
                                     "value": 4.0, "tolerance": 0.01, "unit": "",
                                     "explanation": "Computed from the source values.",
                                     "locator": dict(loc)})
                content = json.dumps({"questions": made})
            # Answer-key verification (Phase F.30): the cited locator must be
            # one the source excerpts actually carry.
            elif "verify the answer key" in system:
                src, _, q = convo.partition("QUESTION_JSON:")
                have = set(locators_in(src))
                m = re.search(r'"kind"\s*:\s*"([^"]+)"\s*,\s*"value"\s*:\s*"([^"]+)"', q)
                cited = (m.group(1), m.group(2)) if m else None
                content = json.dumps({"valid": bool(cited and cited in have),
                                      "problem": "" if cited and cited in have
                                      else f"cited locator {cited} is not in the excerpts"})
            # Rubric grading (Phase F.31): correct iff the learner shares a
            # content word with the sample answer.
            elif "grade the answer against the rubric" in system:
                samp = re.search(r"SAMPLE ANSWER:\s*(.*?)\s*LEARNER ANSWER:", convo, re.DOTALL)
                learn = re.search(r"LEARNER ANSWER:\s*(.*)", convo, re.DOTALL)
                sample_words = set(re.findall(r"[a-z0-9]{4,}", (samp.group(1) if samp else "").lower()))
                learner_words = set(re.findall(r"[a-z0-9]{4,}", (learn.group(1) if learn else "").lower()))
                shared = sorted(sample_words & learner_words)[:3]
                content = json.dumps({"correct": bool(shared), "matched": shared, "missed": [],
                                      "feedback": "You stated the key relationship." if shared
                                      else "Restate the definition from the material."})
            else:
                question = convo.split("STUDENT QUESTION:")[-1].lower()
                ctx = convo.split("STUDENT QUESTION:")[0]
                # Offline relevance gate: the real retrieval gate is disabled
                # in zero-key runs (BoW vectors are too weak for it), so the
                # mock refuses when the question shares no content words with
                # the retrieved context. Grounded eval questions are worded to
                # overlap; off-material ones do not.
                qw = set(re.findall(r"[a-z0-9]{4,}", question))
                cw = set(re.findall(r"[a-z0-9]{4,}", ctx.lower()))
                if not found or "world cup" in question or not (qw & cw):
                    content = REFUSAL
                else:
                    labels = [canonical_label(k, v) for k, v in found[:2]]
                    content = f"**Key idea.** The material covers this on {labels[0]}" + (
                        f" and continues on {labels[1]}." if len(labels) > 1 else ".")
                    # Quote the most question-relevant block (a reranker would do
                    # the same) so offline evals check content recall against
                    # real source words.
                    body = convo.split("STUDENT QUESTION:")[0]
                    # Split on the real block separator only: markdown tables
                    # contain "| --- |" lines that a bare "---" split shreds.
                    blocks = [b for b in body.split("\n\n---\n\n") if b.strip()] or [body]

                    def _words(block: str) -> set[str]:
                        return set(re.findall(r"[a-z0-9]{4,}", block.lower()))

                    best = max(blocks, key=lambda b: (len(qw & _words(b)), len(b)))
                    text_lines = [ln.strip() for ln in best.splitlines()
                                  if ln.strip() and not ln.strip().startswith(("[", "#"))
                                  and ln.strip() != "CONTEXT:"]
                    quote = " ".join(text_lines)[:300].strip()
                    if quote:
                        content += f' As stated: "{quote}"'
            if req.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    for token in re.findall(r"\S+\s*", content):
                        event = {"id": "mock", "object": "chat.completion.chunk", "created": 0,
                                 "model": req.get("model"), "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}]}
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                        time.sleep(0.02)
                    # Some providers append usage without a choices entry.
                    self.wfile.write(b'data: {"id":"mock","object":"chat.completion.chunk","created":0,"model":"mock","choices":[]}\n\ndata: [DONE]\n\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            return self._json(200, {"id": "mock", "object": "chat.completion", "model": req.get("model"), "usage": {},
                                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})

        self._json(404, {"error": "unknown route"})


if __name__ == "__main__":
    # MOCK_LLM_PORT lets you rehearse a *mixed* provider setup offline: run one
    # instance per port and point LLM_BASE_URL / EMBEDDING_BASE_URL /
    # TRANSCRIPTION_BASE_URL at different ports, exactly as you would with
    # Groq + OpenAI in production.
    port = int(os.environ.get("MOCK_LLM_PORT", "9999"))
    print(f"Mock OpenAI-compatible server on http://127.0.0.1:{port}/v1  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
