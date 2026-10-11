"""
Project-scoped chat over the three memory tiers — Phase E.

One turn, in order:

    persist user message → rewrite follow-up (E.24) → project-scoped
    retrieval + gate → assemble memory blocks (E.27) → LLM → citation
    audit (strip-if-not-retrieved) → persist answer → BKT nudges from chat
    evidence (E.26) → rolling summary past the threshold (E.23) →
    observational counters.

The client never sends `history`: `chats` + `chat_messages` are server-side
(E.22). Every failure path degrades — rewrite falls back to the raw question,
classification/summary/refinement failures are logged and skipped — so a turn
never 500s because an auxiliary LLM call misbehaved.
"""

from __future__ import annotations

import base64
import logging
from typing import Callable

from .citations import audit_citations, citation_label, format_context
from .config import Settings
from .learner.bkt import BKTParams
from .llm import Embedder, LLMClient
from .repo.chats import ChatRepo
from .repo.memory import ProjectMemoryRepo
from .repo.profile import ProfileRepo
from .repo.sources import SourceRepo
from .repo.topics import TopicRepo
from .storage import FileStorage, content_type_for
from .vectorstore import VectorStore

log = logging.getLogger(__name__)

REFUSAL = "I can't find this in your uploaded material."

SYSTEM_PROMPT = f"""You are a precise study tutor. The student has uploaded lecture material, and retrieved excerpts are provided as CONTEXT. Each excerpt starts with a numbered reference such as [1], followed by its locator. Retrieved image sources may also be attached directly to the latest user message; match each attached image to its numbered CONTEXT entry. Retrieved lecture slices may likewise be attached as audio; match each attached clip to its numbered CONTEXT entry by its [m:ss] label.
You also receive MEMORY: who the learner is, what they struggle with, and a summary of this conversation so far. Adapt your explanation depth to their capability level.

Rules you must follow without exception:
1. Answer ONLY from the CONTEXT. For an image source, the attached original image is part of the CONTEXT even if its text excerpt only says that its visual embedding was indexed. For an audio source, an attached clip is part of the CONTEXT alongside its transcript excerpt. Do not use outside knowledge, even if you are confident.
2. After every factual statement, cite the matching CONTEXT number in square brackets, such as [1] or [2]. Use only numbers present in the CONTEXT. The app will render the matching source details below your answer; do not write filenames or locator labels directly.
3. If the CONTEXT does not contain what is needed to answer, reply with exactly this sentence and nothing else:
{REFUSAL}
4. Never guess, never pad, never apologise. Be concise and use Markdown (short paragraphs, lists, **bold** key terms, LaTeX with $...$ for maths if the material uses it).
5. Explain like a good tutor: define, then give the key idea, then (if present in the context) an example."""

REWRITE_SYSTEM = (
    "You rewrite follow-up questions as standalone questions. "
    "Given the recent turns and a follow-up, output ONE line: the standalone question "
    "with pronouns and references resolved. If the follow-up is already standalone, "
    "repeat it verbatim. No quotes, no preamble."
)

SUMMARY_SYSTEM = (
    "You summarize earlier chat turns. Given the previous summary (which may be empty) "
    "and the older turns, write a compact replacement summary of at most 2000 characters: "
    "facts established, topics discussed, open threads. Plain prose, no citations."
)

CLASSIFY_SYSTEM = (
    "You classify learner signals as JSON. Given the topic list and one exchange "
    "(the learner's question and the cited answer), output "
    '{"signals": [{"topic_slug": ..., "correct": true/false}]} — one entry per topic the '
    "learner's QUESTION shows understanding or misunderstanding of. A correct restatement "
    "of the cited material is true; confusion or contradiction of it is false; no evidence "
    "means omit the topic. Use only slugs from the list."
)

REFINE_SYSTEM = (
    "You refine the learner profile as JSON. Given the learner's recent messages, output "
    '{"study_pattern_add": {counter_name: small_int}, '
    '"strengths_add": [at most 3 short phrases], '
    '"capability_level": "beginner"|"intermediate"|"advanced" or null}. '
    "Counters observe habits (chat_sessions, followup_questions, …). "
    "Set capability_level ONLY on confident evidence of vocabulary depth, else null."
)


class ChatNotFoundError(RuntimeError):
    pass


class ChatService:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        embedder: Embedder,
        store: VectorStore,
        chats: ChatRepo,
        memory: ProjectMemoryRepo,
        topics: TopicRepo,
        profiles: ProfileRepo,
        sources: SourceRepo,
        storage: FileStorage,
    ):
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.store = store
        self.chats = chats
        self.memory = memory
        self.topics = topics
        self.profiles = profiles
        self.sources = sources
        self.storage = storage
        self.bkt = BKTParams(
            p_init=settings.bkt_p_init,
            p_transit=settings.bkt_p_transit,
            p_slip=settings.bkt_p_slip,
            p_guess=settings.bkt_p_guess,
        )

    # ================================================================== turn
    def send(
        self, user_id: str, chat_id: str, content: str,
        on_chunk: Callable[[str], None] | None = None,
        response_style: str = "default",
        source_ids: list[str] | None = None,
    ) -> dict:
        """One grounded turn. Raises `ChatNotFoundError` for foreign ids."""
        chat = self.chats.get_owned(chat_id, user_id)
        if chat is None:
            raise ChatNotFoundError(chat_id)
        question = content.strip()

        prior = self.chats.recent_messages(chat_id, limit=self.settings.memory_chat_window)
        self.chats.add_message(chat_id, "user", question, [], None)

        # 1. Rewrite follow-ups ("what about that?" → standalone) before embedding.
        rewritten, did_rewrite = self._rewrite(question, prior)

        # 2. Retrieve within ONE project, READY sources only; gate before spending an LLM call.
        # Uploads ingest in the background, so a project usually holds sources
        # that are still queued/parsing — they must never answer. `ready_ids`
        # is the boundary: no ready source means nothing to retrieve from, and
        # the turn refuses without an embedding call.
        ready_ids = self.sources.ready_ids(chat.project_id)
        if source_ids is not None:
            selected = set(source_ids)
            ready_ids = [source_id for source_id in ready_ids if source_id in selected]
        if not ready_ids:
            log.info("Refusing in chat %s: no selected ready sources in project", chat_id)
            self.chats.add_message(chat_id, "assistant", REFUSAL, [], False)
            self._after_turn(user_id, chat, question, REFUSAL, [], nudge=False)
            return self._payload(chat_id, chat.title, REFUSAL, [], False, [], None)
        query_vec = self.embedder.embed([rewritten])[0]
        chunks = self.store.query(
            query_vec, project_id=chat.project_id, k=self.settings.top_k,
            source_ids=ready_ids,
        )
        best = chunks[0].distance if chunks else None
        if not chunks or (best is not None and best > self.settings.max_distance):
            log.info("Refusing in chat %s: best distance %s > %s", chat_id, best, self.settings.max_distance)
            self.chats.add_message(chat_id, "assistant", REFUSAL, [], False)
            self._after_turn(user_id, chat, question, REFUSAL, [], nudge=False)
            return self._payload(chat_id, chat.title, REFUSAL, [], False, [], best)

        # 3. Assemble memory with an explicit budget, then generate + audit.
        global_block, project_block = self._blocks(user_id, chat.project_id)
        context = format_context(chunks, numbered=True)[: self.settings.memory_context_chars]
        messages = self._prompt(global_block, project_block, chat.summary, prior, context, question)
        messages = self._attach_retrieved_images(messages, chunks)
        messages = self._attach_retrieved_audio(messages, chunks)
        if response_style == "precise":
            messages.append({"role": "system", "content": "Response style: be precise and concise. Lead with the direct answer, include only necessary explanation, and retain citations."})
        elif response_style == "explanatory":
            messages.append({"role": "system", "content": "Response style: explain the reasoning step by step in learner-friendly language. Define important terms and give a short example when useful. Retain citations."})
        raw = self.llm.chat(messages, on_chunk=on_chunk)
        if _is_refusal(raw):
            self.chats.add_message(chat_id, "assistant", REFUSAL, [], False)
            self._after_turn(user_id, chat, question, REFUSAL, [], nudge=False)
            return self._payload(chat_id, chat.title, REFUSAL, [], False,
                                 _retrieved_labels(chunks), best)

        result = audit_citations(raw, chunks)
        if not result.citations:
            log.info("No valid citations in chat %s — retrying with reminder", chat_id)
            retry = self.llm.chat(
                messages
                + [{"role": "assistant", "content": raw},
                   {"role": "user", "content": "Your answer had no valid citations. Rewrite it, citing the CONTEXT label after every statement. If the context does not support the answer, reply with the exact refusal sentence."}]
            )
            if _is_refusal(retry):
                self.chats.add_message(chat_id, "assistant", REFUSAL, [], False)
                self._after_turn(user_id, chat, question, REFUSAL, [], nudge=False)
                return self._payload(chat_id, chat.title, REFUSAL, [], False,
                                      _retrieved_labels(chunks), best)
            result = audit_citations(retry, chunks)

        if not result.answer.strip() or not result.citations:
            log.warning("Chat model returned no usable answer for %s", chat_id)
            self.chats.add_message(chat_id, "assistant", REFUSAL, [], False)
            self._after_turn(user_id, chat, question, REFUSAL, [], nudge=False)
            return self._payload(chat_id, chat.title, REFUSAL, [], False,
                                 _retrieved_labels(chunks), best)

        stored = [self._citation_dict(c) for c in result.citations]
        self.chats.add_message(chat_id, "assistant", result.answer, stored, bool(stored))

        # First exchange names the chat (cheap, no LLM call).
        title = chat.title
        if title == "New chat":
            title = self._generate_title(question, result.answer)
            self.chats.rename(chat_id, user_id, title)

        if did_rewrite:
            log.debug("Rewrote %r → %r", question, rewritten)
        self._after_turn(user_id, chat, question, result.answer, stored, nudge=True)
        return self._payload(
            chat_id, title, result.answer, stored, bool(stored),
            [citation_label(c.locator_kind, c.locator_value,
                            getattr(self._chunk_by_label(chunks, c), "start_ts", None))
             for c in result.citations],
            best,
        )

    def _generate_title(self, question: str, answer: str) -> str:
        """Name a new conversation from its first exchange, with a local fallback."""
        fallback = question[:60].strip().rstrip(" .?!") or "New chat"
        try:
            title = self.llm.complete(
                "Create a concise title for this study conversation. Return only 3 to 7 words, no quotes or punctuation.",
                f"Student question: {question[:800]}\nTutor answer: {answer[:1200]}",
                max_tokens=32,
            ).strip().strip("\"'`*# ")
            title = " ".join(title.split())
            return title[:60] if title and not _is_refusal(title) else fallback
        except Exception as exc:  # noqa: BLE001 — naming must not fail a turn
            log.warning("Chat title generation failed; using question text: %s", exc)
            return fallback

    # ------------------------------------------------------- background E.25
    def refine_profile(self, user_id: str) -> dict:
        """Bounded LLM pass over recent utterances. Never raises — logs and skips."""
        out = {"counters": {}, "strengths": [], "capability": (False, "unknown")}
        try:
            messages = self.chats.recent_user_messages(user_id, limit=20)
            if not messages:
                return out
            data = self.llm.complete_json(
                REFINE_SYSTEM,
                "Recent learner messages:\n" + "\n".join(f"- {m[:300]}" for m in messages),
            )
            add = data.get("study_pattern_add") or {}
            counters = {str(k)[:40]: int(v) for k, v in add.items() if int(v or 0) > 0}
            if counters:
                self.profiles.observe_study_pattern(user_id, **counters)
            out["counters"] = counters
            strengths = [str(s) for s in (data.get("strengths_add") or [])][:3]
            if strengths:
                out["strengths"] = self.profiles.add_strengths(user_id, strengths)
            out["capability"] = self.profiles.maybe_upgrade_capability(
                user_id, data.get("capability_level")
            )
        except Exception as exc:  # noqa: BLE001 — refinement must never break chat
            log.warning("Profile refinement skipped for %s: %s", user_id, exc)
        return out

    # -------------------------------------------------------------- internals
    def _rewrite(self, question: str, prior: list) -> tuple[str, bool]:
        """Standalone question for embedding. Falls back to the raw text."""
        if not self.settings.memory_rewrite or not prior:
            return question, False
        try:
            turns = "\n".join(f"{m.role.upper()}: {m.content[:500]}" for m in prior[-6:])
            out = self.llm.complete(REWRITE_SYSTEM, f"{turns}\nFOLLOW-UP: {question}",
                                    max_tokens=256).strip().splitlines()
            rewritten = (out[0] if out else question).strip().strip("\"'") or question
            return rewritten, rewritten != question
        except Exception as exc:  # noqa: BLE001
            log.warning("Query rewrite failed, using raw question: %s", exc)
            return question, False

    def _blocks(self, user_id: str, project_id: str) -> tuple[str, str]:
        """Tier-1 + tier-2 blocks, always truncated (memory never crowds out context)."""
        profile = self.profiles.get(user_id)
        lines = []
        if profile is not None:
            lines.append(f"Capability: {profile.capability_level}; thinking: {profile.thinking_level}.")
            if profile.goals:
                lines.append(f"Goals: {profile.goals[:200]}")
            for s in (profile.strengths or [])[: self.settings.memory_max_facts_global]:
                lines.append(f"Strength: {s}")
            counters = sorted((profile.study_pattern or {}).items())[: self.settings.memory_max_facts_global]
            for k, v in counters:
                lines.append(f"Habit {k}: {v}")
        global_block = "\n".join(lines[: self.settings.memory_max_facts_global + 2])[:1500]

        rows = self.memory.list_for_project(user_id, project_id)
        weak = sorted(rows, key=lambda r: (r.p_mastery, r.name))[: self.settings.memory_max_topics]
        project_block = "\n".join(
            f"- {r.name} (mastery {r.p_mastery:.2f}, {r.coverage_status})" for r in weak
        )[:1500]
        return global_block, project_block

    def _prompt(
        self, global_block: str, project_block: str, summary: str,
        prior: list, context: str, question: str,
    ) -> list[dict[str, str]]:
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if global_block:
            messages.append({"role": "system", "content": "LEARNER MEMORY:\n" + global_block})
        if project_block:
            messages.append({"role": "system", "content": "PROJECT MASTERY (weakest first):\n" + project_block})
        if summary:
            messages.append({"role": "system", "content": "CONVERSATION SO FAR:\n" + summary[:2000]})
        for m in prior[-self.settings.memory_chat_window :]:
            if m.role in ("user", "assistant") and m.content:
                messages.append({"role": m.role, "content": m.content[:2000]})
        messages.append({"role": "user", "content": f"CONTEXT:\n{context}\n\nSTUDENT QUESTION: {question}"})
        return messages

    def _attach_retrieved_images(self, messages: list[dict], chunks: list) -> list[dict]:
        """Pass retrieved original images to the vision-capable chat model."""
        image_parts: list[dict] = []
        seen: set[str] = set()
        for chunk in chunks:
            if chunk.locator_kind != "image":
                continue
            key = f"{chunk.source_id}:{chunk.locator_value}"
            if key in seen:
                continue
            seen.add(key)
            source = self.sources.get(chunk.source_id)
            if source is None:
                continue
            from .storage import read_image_chunk_bytes
            found = read_image_chunk_bytes(
                self.storage, source.project_id, source, chunk.locator_kind, chunk.locator_value
            )
            if found is None:
                log.warning("Retrieved image %s is missing from storage", key)
                continue
            data, mime = found
            data_url = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            image_parts.extend([
                {"type": "text", "text": f"Retrieved image context labeled [Image {chunk.locator_value}]:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ])
            if len(image_parts) >= 4:
                break
        if image_parts and messages:
            last = messages[-1]
            prompt_text = str(last.get("content", ""))
            last["content"] = [{"type": "text", "text": prompt_text}, *image_parts]
        return messages

    def _attach_retrieved_audio(self, messages: list[dict], chunks: list) -> list[dict]:
        """Pass retrieved lecture slices to the audio-capable chat model.

        Each `segment` chunk's `[start_ts, end_ts]` is cut from the stored
        original and attached as an `input_audio` part, so the model hears the
        exact cited timeframe. Failures degrade to text-only context — a turn
        never 500s because an audio slice or a text-only chat model balked.
        """
        from .ingestion.parsers_media import slice_audio_wav

        audio_parts: list[dict] = []
        seen: set[str] = set()
        for chunk in chunks:
            if chunk.locator_kind != "segment":
                continue
            if chunk.start_ts is None or chunk.end_ts is None or chunk.end_ts <= chunk.start_ts:
                continue
            key = f"{chunk.source_id}:{chunk.locator_value}"
            if key in seen:
                continue
            seen.add(key)
            try:
                source = self.sources.get(chunk.source_id)
                if source is None:
                    continue
                path = self.storage.open_for_read(source.project_id, source.id, source.filename)
                if path is None:
                    log.warning("Retrieved audio source %s is missing from storage", source.id)
                    continue
                wav = slice_audio_wav(path, chunk.start_ts, chunk.end_ts)
                if len(wav) > 2_000_000:  # ~60 s at 16 kHz mono; skip giants
                    log.info("Skipped audio attach for %s: slice too large (%d bytes)", key, len(wav))
                    continue
                audio_parts.extend([
                    {"type": "text", "text": f"Retrieved audio context [{chunk.locator_value}]:"},
                    {"type": "input_audio",
                     "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"}},
                ])
            except Exception as exc:  # noqa: BLE001 — text context still answers
                log.warning("Skipped audio attach for %s: %s", key, exc)
                continue
            if len(audio_parts) >= 4:  # at most 2 clips per turn
                break
        if audio_parts and messages:
            last = messages[-1]
            existing = last.get("content", "")
            if isinstance(existing, list):
                last["content"] = [*existing, *audio_parts]
            else:
                last["content"] = [{"type": "text", "text": str(existing)}, *audio_parts]
        return messages

    def _after_turn(
        self, user_id: str, chat, question: str, answer: str,
        citations: list[dict], *, nudge: bool,
    ) -> None:
        """Nudges, summary rollover and counters. Failures are logged, never raised."""
        try:
            self.profiles.observe_study_pattern(user_id, chat_turns=1)
            if nudge:
                self._apply_chat_nudges(user_id, chat, question, answer)
            self._maybe_summarize(chat, user_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Post-turn bookkeeping skipped for chat %s: %s", chat.id, exc)

    def _apply_chat_nudges(self, user_id: str, chat, question: str, answer: str) -> None:
        topics = self.topics.list_for_project(chat.project_id)
        if not topics:
            return
        by_slug = {t.slug: t for t in topics}
        listing = "\n".join(f"- {t.slug}: {t.name}" for t in topics)
        try:
            data = self.llm.complete_json(
                CLASSIFY_SYSTEM,
                f"Topics:\n{listing}\n\nQUESTION: {question[:1000]}\nANSWER: {answer[:2000]}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Chat classification skipped: %s", exc)
            return
        for signal in (data.get("signals") or [])[:8]:
            topic = by_slug.get(str(signal.get("topic_slug", "")))
            if topic is None:
                continue
            self.memory.apply_nudge(
                user_id, chat.project_id, topic.id, bool(signal.get("correct", False))
            )

    def _maybe_summarize(self, chat, user_id: str) -> None:
        """Fold turns older than the window into `chats.summary` past the threshold."""
        total = self.chats.count_messages(chat.id)
        if total <= self.settings.memory_summarize_threshold:
            return
        window = self.settings.memory_chat_window
        fresh = self.chats.get_owned(chat.id, user_id)
        older = self.chats.unsummarized_messages(chat.id, fresh.summarized_through if fresh else None, window)
        if not older:
            return
        previous = (fresh.summary if fresh else "") or ""
        selected, lines, size = [], [], 0
        for message in older:
            line = f"{message.role.upper()}: {message.content[:800]}"
            if size + len(line) + 1 > 6000:
                break
            selected.append(message)
            lines.append(line)
            size += len(line) + 1
        turns = "\n".join(lines)
        try:
            summary = self.llm.complete(
                SUMMARY_SYSTEM,
                f"Previous summary:\n{previous[:2000] or '(none)'}\n\nOlder turns:\n{turns[:6000]}",
            ).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("Chat summarization skipped: %s", exc)
            return
        if summary:
            self.chats.set_summary(chat.id, summary, selected[-1].id)
            log.info("Summarized chat %s through %s (%d msgs)", chat.id, selected[-1].id, total)

    # --------------------------------------------------------------- payload
    @staticmethod
    def _payload(chat_id, title, answer, citations, grounded, retrieved, best) -> dict:
        return {
            "answer": answer,
            "citations": citations,
            "grounded": grounded,
            "retrieved": retrieved,
            "best_distance": best,
            "chat_id": chat_id,
            "chat_title": title,
        }

    @staticmethod
    def _citation_dict(c) -> dict:
        return {
            "locator_kind": c.locator_kind,
            "locator_value": c.locator_value,
            "page": c.page,
            "source_id": c.source_id,
            "heading": c.heading,
            "snippet": c.snippet,
            "end_ts": c.end_ts,
        }

    @staticmethod
    def _chunk_by_label(chunks, citation):
        for c in chunks:
            if (c.source_id, c.locator_kind, c.locator_value) == (citation.source_id, citation.locator_kind, citation.locator_value):
                return c
        return None


def _is_refusal(text: str) -> bool:
    """Tolerates trailing punctuation / quotes around the mandated sentence."""
    import re

    norm = re.sub(r"[^a-z ]", "", text.strip().lower())
    return norm.startswith(re.sub(r"[^a-z ]", "", REFUSAL.lower()))


def _retrieved_labels(chunks) -> list[str]:
    """Canonical labels for retrieved chunks, order-preserving, capped."""
    out: list[str] = []
    for c in chunks:
        label = citation_label(c.locator_kind, c.locator_value, getattr(c, "start_ts", None))
        if label not in out:
            out.append(label)
        if len(out) >= 20:
            break
    return out
