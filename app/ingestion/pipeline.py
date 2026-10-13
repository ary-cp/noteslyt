"""
Ingestion pipeline: file -> units -> chunks -> vectors -> project topics

    parse_source()   registry: file type -> [SourceUnit] (locator-tagged)
    chunk_units()    chunks that never cross a locator boundary
    embed()          one batched call for all chunks
    extract_topics() one LLM call -> project-level topics, reused across sources
    assign_topics()  nearest-centroid tagging, reusing the chunk vectors
    store.upsert()   project_id + source_id + locator metadata

Two things changed fundamentally from the document-scoped version:

  1. **Both scope keys are explicit.** Every chunk carries `project_id` and
     `source_id`. Retrieval filters on `project_id`, so a project's corpus is
     self-contained by construction.

  2. **Topics are project-level, merged by slug.** Uploading a second source that
     covers the same concept reuses the topic row and *adds* its locators to
     `topic_sources`, instead of creating a near-duplicate. That is what keeps
     "which topic am I weak at" meaningful across a project's whole corpus.

Ingestion is asynchronous: `ingest_source` advances `sources.status` through
queued -> parsing -> embedding -> ready|failed, and never raises for an expected
failure — the reason is recorded on the row so the learner can read it.
"""

from __future__ import annotations

import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..llm import Embedder, LLMClient
from ..repo.models import SourceRecord
from ..repo.sources import SourceRepo
from ..repo.topics import TopicRepo
from ..storage import content_type_for
from ..vectorstore import VectorStore
from .chunker import Chunk, chunk_units
from .registry import ParserError, parse_source
from .topics import Topic, assign_topics, extract_topics, infer_prereq_edges, merge_locators
from .units import SourceUnit
from psycopg.errors import DataException

log = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    pass


# Fixed namespace so `chunk_id` is stable across processes and machines — the
# same source re-ingested produces the same chunk ids and upserts in place.
_CHUNK_NAMESPACE = uuid.UUID("6f1c9a7e-0d3b-5f42-9c81-1f0a2b3c4d5e")

# Image-chunk indices live far above text-chunk indices so the two families
# never share a deterministic chunk id, whatever the text chunk count is.
PAGE_IMAGE_INDEX_BASE = 1_000_000
# Audio-clip chunks live in their own index band (transcript + audio dual
# vectors, like PDF page images): re-ingests upsert the same rows.
AUDIO_CLIP_INDEX_BASE = 2_000_000

# Parsed-text cap for the viewer ("what the model read"). Beyond this the
# stored markdown is cut and flagged — chunks/embeddings are unaffected, only
# the preview stored in `source_contents` is trimmed.
MAX_PARSED_CHARS = 400_000


def chunk_id(source_id: str, index: int) -> str:
    """
    Deterministic chunk primary key.

    `uuid5` rather than f"{source_id}:{index}" because `chunks.id` is a real
    `uuid` column; and deterministic so re-ingesting a source replaces its
    chunks instead of accumulating duplicates.
    """
    return str(uuid.uuid5(_CHUNK_NAMESPACE, f"{source_id}:{index}"))


@dataclass
class IngestResult:
    source_id: str
    project_id: str
    status: str
    units: int = 0
    chunks: int = 0
    topics: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser: str = ""
    detail: str = ""
    elapsed_s: float = 0.0
    reused_parse: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ready"


class IngestionPipeline:
    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        embedder: Embedder,
        store: VectorStore,
        sources: SourceRepo,
        topics: TopicRepo,
    ):
        self.settings = settings
        self.llm = llm
        self.embedder = embedder
        self.store = store
        self.sources = sources
        self.topics = topics

    # ================================================================== main
    def ingest_source(self, source: SourceRecord, path: Path, *,
                      reuse_parsed: bool = False) -> IngestResult:
        """
        Ingest one stored file. Never raises for an expected failure — the source
        row is moved to `failed` with a readable reason instead.

        `reuse_parsed` (re-embed path only) rebuilds units from the stored
        parse instead of re-parsing the file — no re-transcription, no
        re-captioning, no parser bills. Falls back to a fresh parse when the
        stored text is missing, truncated or unparseable. New uploads always
        parse the file (nothing stored yet).
        """
        t0 = time.perf_counter()
        result = IngestResult(
            source_id=source.id,
            project_id=source.project_id,
            status="failed",
        )

        try:
            if not path.is_file():
                raise IngestionError(f"Stored file is missing on disk: {path}")
            if self._abort_if_deleted(source, result):
                return result

            # 1. Parse -> units --------------------------------------------
            self.sources.set_status(source.id, "parsing", f"parsing {source.filename}")
            parsed = self._parse_with_reuse(source, path, result, reuse_parsed=reuse_parsed)
            result.parser = parsed.parser
            result.warnings.extend(parsed.warnings)
            result.units = parsed.unit_count
            if parsed.is_empty:
                raise IngestionError(
                    "No extractable text found (scanned images or an empty file?)."
                )

            # 1b. Video keyframes -> vision captions (same units, same locators)
            # Skipped for reused parses: their captions are already in the text.
            if source.source_type == "video" and not result.reused_parse:
                self._caption_video_frames(source, path, parsed.units, result)

            # 2. Chunk (never crosses a locator boundary) -------------------
            chunks = chunk_units(
                parsed.units, self.settings.chunk_size, self.settings.chunk_overlap
            )
            if not chunks:
                raise IngestionError("No chunks produced from this file.")
            result.chunks = len(chunks)

            # 2b. Persist the parsed markdown for the viewer ------------------
            # Best-effort: the learner should see what the model ingested even
            # if a later step fails, and a content write must never fail the
            # ingest itself. Skipped when the row is gone (deleted mid-ingest).
            if self._abort_if_deleted(source, result):
                return result
            try:
                markdown, truncated = render_parsed_markdown(
                    source.filename, parsed.units, MAX_PARSED_CHARS,
                    parser=parsed.parser,
                )
                self.sources.save_content(source.id, markdown, truncated)
            except Exception:  # noqa: BLE001 — preview is auxiliary, chunks are not
                log.exception("Could not store parsed text for %s", source.filename)

            # 2c. Page/slide renders -> visual chunks ------------------------
            # Every PDF page is embedded as an image *in addition* to its text
            # chunks, so charts and diagrams stay retrievable. Kept separate
            # from `chunks` until after text embedding (section 4), then
            # combined 1:1 with their vectors. Never fails the ingest:
            # render/encode problems become warnings, text still lands.
            image_chunks, image_vecs = self._embed_page_images(source, path, parsed.units, result)
            result.chunks = len(chunks) + len(image_chunks)

            # 3. Topics (project-level, merged by slug) ---------------------
            self.sources.set_status(
                source.id, "embedding", f"embedding {len(chunks)} chunks"
            )
            if self._abort_if_deleted(source, result):
                return result
            existing = self.topics.list_for_project(source.project_id)
            image_url = None
            if source.source_type == "image":
                mime = content_type_for(source.filename).split(";")[0]
                image_url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            proposed = extract_topics(
                self.llm, parsed.units, self.settings.max_topics,
                existing_slugs=[t.slug for t in existing], image_url=image_url,
            )
            topic_by_slug = self._persist_topics(source, proposed, existing)
            slugs = [t.slug for t in proposed if t.slug in topic_by_slug]

            # 4. Embed --------------------------------------------------------
            audio_chunks: list[Chunk] = []
            audio_vecs: list[list[float]] = []
            if source.source_type == "image":
                embed_image = getattr(self.embedder, "embed_image", None)
                if embed_image is None:
                    raise IngestionError(
                        "The configured embedding provider does not support image inputs. "
                        "Serve a joint model (e.g. embeddinggemma-2-GGUF + mmproj) from "
                        "llama.cpp — LM Studio's embedding endpoint is text-only."
                    )
                try:
                    embeddings = [embed_image(path.read_bytes(), content_type_for(source.filename))]
                except Exception as exc:
                    raise IngestionError(f"Image embedding failed: {exc}") from exc
                if len(embeddings[0]) != self.settings.embedding_dim:
                    raise IngestionError(
                        f"Image embedding width {len(embeddings[0])} does not match "
                        f"EMBEDDING_DIM={self.settings.embedding_dim}."
                    )
            elif (
                source.source_type == "video"
                and self.settings.video_embed_mode == "native"
            ):
                # Direct visual vectors: one keyframe embedding per segment.
                # Chunk texts stay transcript+caption text (viewer, topics, chat
                # grounding); only the vector space is visual.
                embeddings = self._embed_video_native(path, chunks, source, result.warnings)
            elif (
                source.source_type == "audio"
                and self.settings.audio_embed_mode == "native"
            ):
                # Dual-signal audio RAG (mirrors video transcript+clip): every
                # chunk keeps its transcript-text vector, and one extra chunk
                # per ~3-min part carries the embedded audio clip of the same
                # timeframe — retrieval matches what was said AND the sound.
                embeddings = self._embed_with_batches([c.text for c in chunks], source)
                audio_chunks, audio_vecs = self._embed_audio_clips(
                    path, chunks, source, result.warnings
                )
            elif (
                source.source_type in ("audio", "video")
                and self.settings.audio_embed_mode == "native"
            ):
                # Direct audio vectors: one embedding per segment slice. Chunk
                # texts stay transcript text (viewer, topics, chat grounding);
                # only the vector space is acoustic.
                embeddings = self._embed_audio_native(path, chunks, source, result.warnings)
            else:
                embeddings = self._embed_with_batches([c.text for c in chunks], source)

            # 4b. Join the visual page vectors with their chunks (1:1) -------
            # Audio-clip chunks join the same way (transcript + audio dual
            # vectors); both families upsert stably via their index bands.
            chunks = [*chunks, *audio_chunks, *image_chunks]
            embeddings = [*embeddings, *audio_vecs, *image_vecs]
            if audio_chunks:
                result.warnings.append(
                    f"Embedded {len(audio_chunks)} audio clips (~3 min each) "
                    "alongside the transcript."
                )
            result.chunks = len(chunks)

            # 5. Tag chunks with their topic ---------------------------------
            chunk_locators = [(c.locator_kind, c.locator_value) for c in chunks]
            assigned = (
                assign_topics(embeddings, chunk_locators, proposed, self.embedder)
                if slugs
                else [""] * len(chunks)
            )
            id_to_slug = {t.slug: topic_by_slug[t.slug] for t in proposed if t.slug in topic_by_slug}

            # 6. Store --------------------------------------------------------
            # Chunk ids are derived with uuid5 rather than "<uuid>:<index>" so
            # they are real UUIDs (the column type) while staying deterministic:
            # re-ingesting a source overwrites its chunks instead of duplicating.
            # Skipped when the row is gone: chunk rows FK-reference it, and the
            # vector store would otherwise keep orphans for a deleted source.
            if self._abort_if_deleted(source, result):
                return result
            try:
                self.store.upsert(
                    project_id=source.project_id,
                    source_id=source.id,
                    ids=[chunk_id(source.id, c.chunk_index) for c in chunks],
                    embeddings=embeddings,
                    texts=[c.text for c in chunks],
                    metadatas=[
                        {
                            "topic_id": id_to_slug.get(slug),
                            "locator_kind": c.locator_kind,
                            "locator_value": c.locator_value,
                            "page": c.page,
                            "start_ts": c.start_ts,
                            "end_ts": c.end_ts,
                            "chunk_index": c.chunk_index,
                            "heading": c.heading or "",
                        }
                        for c, slug in zip(chunks, assigned, strict=True)
                    ],
                )
            except DataException as exc:
                # The model-vs-config width check passed but Postgres disagrees:
                # the embedding model was swapped without re-embedding. Never
                # surface the raw dimensionality error to the learner.
                raise IngestionError(
                    f"Vector width {sorted({len(v) for v in embeddings})} does not fit "
                    f"the database column ({exc}). The embedding model was swapped "
                    f"without re-embedding: set EMBEDDING_DIM to the model's width "
                    f"and run uv run python scripts/re_embed.py (use --check first)."
                ) from exc

            self.sources.mark_ready(
                source.id,
                units=result.units,
                chunk_count=result.chunks,
                parser=parsed.parser,
                warnings=result.warnings,
                duration_s=self._duration(parsed.units),
            )
            result.status = "ready"
            result.topics = [
                {"slug": t.slug, "name": t.name, "locators": t.locators} for t in proposed
            ]
            # 3c. Drift ground truth (best-effort) -------------------------
            # Remember what produced these vectors so a later model swap is
            # detectable even when widths coincide. Never fails the ingest.
            try:
                from ..drift import record_ingest_ground_truth

                record_ingest_ground_truth(
                    self.sources.db, self.settings.embedding_model,
                    self.settings.embedding_dim,
                )
            except Exception:  # noqa: BLE001 — auxiliary, never fails ingest
                log.warning("Ground-truth refresh skipped for project %s", source.project_id)
            # 3b. Prerequisite graph (best-effort) -------------------------
            # Topics just changed, so the dependency map may be stale. One LLM
            # call; any failure keeps the previous graph and only logs.
            try:
                self.refresh_prereq_edges(source.project_id)
            except Exception as exc:  # noqa: BLE001 — auxiliary, never fails ingest
                log.warning("Prerequisite refresh skipped for project %s: %s", source.project_id, exc)

        except (ParserError, IngestionError) as exc:
            result.detail = str(exc)
            self.sources.mark_failed(source.id, result.detail)
            log.warning("Ingest %s failed: %s", source.filename, exc)
        except Exception as exc:  # noqa: BLE001 — never let a worker thread die silently
            result.detail = f"{type(exc).__name__}: {exc}"
            self.sources.mark_failed(source.id, result.detail)
            log.exception("Ingest %s raised unexpectedly", source.filename)

        result.elapsed_s = round(time.perf_counter() - t0, 2)
        if result.ok:
            log.info(
                "Ingested %s: %d units -> %d chunks, %d topics (%s) in %.2fs",
                source.filename, result.units, result.chunks, len(result.topics),
                result.parser, result.elapsed_s,
            )
        return result

    # ================================================================ helpers
    def _source_alive(self, source_id: str) -> bool:
        """False when the source row was deleted mid-ingest.

        Every write below FK-references the row (`source_contents`,
        `topic_sources`, `chunks`): without this check a deleted source
        crashes with `IntegrityError` instead of aborting quietly.
        """
        try:
            return self.sources.get(source_id) is not None
        except Exception:  # noqa: BLE001 — a read failure is not a deletion
            log.exception("Liveness check failed for source %s", source_id)
            return True

    def _abort_if_deleted(self, source: SourceRecord, result: IngestResult) -> bool:
        """Stop quietly when the learner deleted the source mid-ingest."""
        if self._source_alive(source.id):
            return False
        log.info("Ingest %s aborted: source was deleted during ingestion", source.filename)
        result.status = "failed"
        result.detail = "Source was deleted during ingestion."
        return True
    def _parse_with_reuse(self, source: SourceRecord, path: Path,
                          result: IngestResult, *, reuse_parsed: bool):
        """Parse step with stored-parse reuse for re-embeds (see ingest_source)."""
        from .units import ParsedSource, units_from_markdown

        if reuse_parsed and source.source_type != "image":
            try:
                row = self.sources.get_content(source.id)
            except Exception:  # noqa: BLE001 — e.g. table predates migration 0004
                row = None
            if row and (row.get("markdown") or "").strip() and not row.get("truncated"):
                units, stored_parser = units_from_markdown(row["markdown"])
                if units:
                    log.info("Reusing stored parse for %s (%d units, via %s) — "
                             "no re-transcription.", source.filename, len(units),
                             stored_parser or "unknown parser")
                    result.reused_parse = True
                    result.warnings.append(
                        "Reused the stored transcript/parse instead of re-parsing "
                        f"({stored_parser or 'unknown parser'}). Re-run with a fresh "
                        "parse to redo it."
                    )
                    return ParsedSource(units=units, parser=stored_parser or "stored")
                log.info("Stored parse for %s unusable; parsing the file fresh.",
                         source.filename)
        return parse_source(path, source.source_type, self.settings)

    def _caption_video_frames(self, source, path: Path, units: list, result) -> None:
        """Caption one frame per Nth video segment unit with the vision model.

        Best-effort by design: transcript-only ingestion is the safe fallback,
        so a text-only chat model, missing Pillow/ffmpeg, or a frame failure
        records a warning and never fails the ingest. Captions join the unit
        text as `[Visual M:SS] ...`, so they flow into chunks, topic outlines
        and citations untouched.
        """
        if not self.settings.video_captions:
            return
        every = max(1, self.settings.video_frame_every_n)
        candidates = [
            u for i, u in enumerate(units)
            if i % every == 0 and (u.end_ts or 0.0) > (u.start_ts or 0.0)
        ]
        if not candidates:
            return
        try:
            from .video_frames import FRAME_CAPTION_SYSTEM, ahash, extract_frame, frame_distance
        except ImportError as exc:
            result.warnings.append(f"Video frame captions skipped: {exc}.")
            return
        kept = 0
        prev_hash: int | None = None
        for u in candidates:
            if kept >= self.settings.video_max_frames:
                result.warnings.append(
                    f"Caption budget reached ({self.settings.video_max_frames} frames); "
                    "remaining segments are transcript-only."
                )
                break
            mid = ((u.start_ts or 0.0) + (u.end_ts or 0.0)) / 2.0
            try:
                frame = extract_frame(path, mid, self.settings.video_frame_width)
                digest = ahash(frame)
            except Exception as exc:  # noqa: BLE001 — one bad frame skips itself
                log.warning("Video frame at %.1fs skipped for %s: %s", mid, source.filename, exc)
                continue
            if prev_hash is not None and frame_distance(digest, prev_hash) < self.settings.video_frame_hamming:
                continue  # static slide — the transcript already covers it
            data_url = "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")
            try:
                caption = self.llm.complete(
                    FRAME_CAPTION_SYSTEM,
                    [{"type": "text", "text": f"Video frame at {mid:.0f}s from '{source.filename}'."},
                     {"type": "image_url", "image_url": {"url": data_url}}],
                    max_tokens=300,
                ).strip()
            except Exception as exc:  # noqa: BLE001 — text-only model: stop, keep transcript
                result.warnings.append(
                    f"Video frame captions stopped: vision model unavailable ({exc}). "
                    "Transcript only."
                )
                break
            if not caption or caption.strip() == "NO_CONTENT":
                prev_hash = digest
                continue
            u.text = f"{u.text}\n\n[Visual {self._mmss(mid)}] {caption}"
            prev_hash = digest
            kept += 1
        if kept:
            result.warnings.append(f"Captioned {kept} video frames.")

    @staticmethod
    def _mmss(seconds: float) -> str:
        total = int(max(0.0, seconds))
        return f"{total // 60}:{total % 60:02d}"

    def _persist_topics(
        self,
        source: SourceRecord,
        proposed: list[Topic],
        existing: list,
    ) -> dict[str, str]:
        """
        Upsert each proposed topic and link it to this source.

        Returns `{slug: topic_id}`. A slug that already exists in the project
        reuses that row and *merges* locators rather than replacing them.
        """
        by_slug = {t.slug: t for t in existing}
        out: dict[str, str] = {}
        for topic in proposed:
            record = self.topics.upsert(
                source.project_id, topic.slug, topic.name, topic.description,
                subtopics=topic.subtopics,
            )
            out[topic.slug] = record.id
            if not topic.locators:
                continue
            prior = self.topics.sources_for(record.id)
            prior_locators = next((row["locators"] for row in prior if str(row["source_id"]) == source.id), None)
            # Only this source's contribution is replaced; other sources' locators stay.
            merged = merge_locators(prior_locators, topic.locators)
            self.topics.link_source(record.id, source.id, merged)
            if topic.slug in by_slug:
                continue
        return out

    def topic_graph(self, project_id: str) -> dict:
        """Nodes + prerequisite edges for a project, keyed by topic slug."""
        rows = self.topics.list_for_project(project_id)
        by_id = {str(r.id): r for r in rows}
        nodes = [{"id": str(r.id), "slug": r.slug, "name": r.name} for r in rows]
        edges = []
        for row in self.topics.edges(project_id):
            topic, prereq = by_id.get(str(row["topic_id"])), by_id.get(str(row["prereq_id"]))
            if topic is None or prereq is None:
                continue
            edges.append({"topic_slug": topic.slug, "prereq_slug": prereq.slug})
        return {"nodes": nodes, "edges": edges}

    def refresh_prereq_edges(self, project_id: str) -> dict:
        """Re-infer the project's prerequisite graph from its current topics.

        Raises on LLM failure — ingestion calls this best-effort, the refresh
        endpoint converts to a readable error.
        """
        rows = self.topics.list_for_project(project_id)
        pairs = infer_prereq_edges(
            self.llm,
            [Topic(slug=r.slug, name=r.name, description=r.description or "") for r in rows],
        )
        by_slug = {r.slug: str(r.id) for r in rows}
        self.topics.set_edges(project_id, [(by_slug[t], by_slug[p]) for t, p in pairs])
        return self.topic_graph(project_id)

    def _embed_audio_native(
        self, path: Path, chunks: list, source: SourceRecord, warnings: list
    ) -> list[list[float]]:
        """
        One audio-slice vector per chunk, cut to the chunk's `[start_ts, end_ts]`.

        Chunks without timestamps — or whose slice holds no samples (segment
        timestamps running past the file) — fall back to a text embedding so
        the vector list always aligns with the chunk list. Every fallback is
        recorded loudly; an empty slice has no acoustic content to retrieve.
        Width is verified exactly like the text path, because an audio-capable
        model swap has the same re-embed consequences.
        """
        from .parsers_media import slice_audio_wav

        embed_audio = getattr(self.embedder, "embed_audio", None)
        if embed_audio is None:
            raise IngestionError(
                "The configured embedding provider does not support audio inputs. "
                "Use a joint audio-text embedding model, or keep AUDIO_EMBED_MODE=transcript."
            )
        vectors: list[list[float]] = []
        for c in chunks:
            label = f"{c.locator_kind} {c.locator_value}"
            try:
                if c.start_ts is not None and c.end_ts is not None and c.end_ts > c.start_ts:
                    try:
                        wav = slice_audio_wav(path, c.start_ts, c.end_ts)
                    except ParserError as exc:
                        warnings.append(f"{label}: {exc} Text vector used.")
                        vectors.append(self.embedder.embed([c.text])[0])
                        continue
                    vectors.append(embed_audio(wav, "audio/wav"))
                else:
                    vectors.append(self.embedder.embed([c.text])[0])
            except (ParserError, IngestionError):
                raise
            except Exception as exc:
                raise IngestionError(f"Audio embedding failed at {label}: {exc}") from exc
        expected = self.settings.embedding_dim
        wrong = {len(v) for v in vectors}
        if wrong and wrong != {expected}:
            raise IngestionError(
                f"Audio embedding width {sorted(wrong)} does not match EMBEDDING_DIM={expected}. "
                f"Set EMBEDDING_DIM to your model's output width, then re-embed the "
                f"corpus with uv run python scripts/re_embed.py (use --check first)."
            )
        return vectors

    def _embed_audio_clips(
        self, path: Path, chunks: list, source: SourceRecord, warnings: list
    ) -> tuple[list, list[list[float]]]:
        """One extra audio-clip chunk per ~3-min part (dual-signal audio RAG).

        Text chunks keep their transcript vectors (computed by the caller); each
        unique `[start_ts, end_ts]` window in the chunk list gets one additional
        chunk whose vector is the embedded audio clip of that same timeframe —
        the audio mirror of video's transcript+clip parts. Chunk texts carry a
        transcript excerpt so the viewer/topics stay readable; only the vector
        space is acoustic.

        Never raises: an unsliceable or unembeddable window is skipped with a
        warning (its transcript vector already covers the timeframe). Chunk
        indices sit above AUDIO_CLIP_INDEX_BASE so re-ingests upsert stably.
        """
        from .parsers_media import slice_audio_wav
        from .units import format_timestamp

        embed_audio = getattr(self.embedder, "embed_audio", None)
        if embed_audio is None:
            warnings.append(
                "Audio clips skipped: the embedding provider has no audio input. "
                "Serve a joint model (e.g. embeddinggemma-2-GGUF + mmproj) from llama.cpp."
            )
            return [], []
        # One clip per unique timeframe (several chunks can share a 3-min part
        # when the transcript text splits across the char budget).
        windows: dict[tuple[float, float], list] = {}
        for c in chunks:
            if c.start_ts is None or c.end_ts is None or c.end_ts <= c.start_ts:
                continue
            key = (round(float(c.start_ts), 2), round(float(c.end_ts), 2))
            windows.setdefault(key, []).append(c)
        if not windows:
            return [], []
        max_clip_s = max(float(getattr(self.settings, "audio_segment_seconds", 180.0) or 180.0), 15.0)
        out_chunks: list = []
        out_vecs: list[list[float]] = []
        skipped = 0
        for i, ((start, end), members) in enumerate(sorted(windows.items())):
            span = end - start
            clip_end = min(end, start + max_clip_s)
            label = f"{format_timestamp(start)}–{format_timestamp(clip_end)}"
            try:
                wav = slice_audio_wav(path, start, clip_end)
            except Exception as exc:  # noqa: BLE001 — transcript already covers it
                skipped += 1
                log.warning("Audio clip %s skipped for %s: %s", label, source.filename, exc)
                continue
            try:
                vec = embed_audio(wav, "audio/wav")
            except Exception as exc:  # noqa: BLE001 — transcript already covers it
                skipped += 1
                warnings.append(f"Audio clip [{label}] not embedded ({exc}); transcript still indexed.")
                continue
            if len(vec) != self.settings.embedding_dim:
                skipped += 1
                warnings.append(
                    f"Audio clip [{label}] width {len(vec)} != "
                    f"EMBEDDING_DIM={self.settings.embedding_dim}; transcript still indexed."
                )
                continue
            excerpt = " ".join(m.text for m in members)[:400].strip()
            out_chunks.append(Chunk(
                text=f"Audio clip [{label}] ({span:.0f}s of sound). {excerpt}",
                chunk_index=AUDIO_CLIP_INDEX_BASE + i,
                heading=f"Audio {label}",
                locator_kind=members[0].locator_kind,
                locator_value=members[0].locator_value,
                start_ts=start,
                end_ts=clip_end,
            ))
            out_vecs.append([float(x) for x in vec])
        if skipped:
            warnings.append(f"{skipped} audio clip(s) skipped; transcript still indexed.")
        return out_chunks, out_vecs

    def _embed_page_images(
        self, source: SourceRecord, path: Path, units: list[SourceUnit], result: IngestResult
    ) -> tuple[list[Chunk], list[list[float]]]:
        """Render document pages/slides/sheets to PNGs and embed one image chunk each.

        Text chunks are untouched — retrieval matches both what a page says
        and what it shows (charts, diagrams, figures). Never raises: render
        or encode problems become warnings and the source ingests text-only.
        PNGs persist via FileStorage (auto-cleaned with the source); only
        vectors reach pgvector. Chunk indices sit above PAGE_IMAGE_INDEX_BASE
        so re-ingests upsert the same rows whatever the text chunk count is.

        Office documents (.docx/.xlsx) render through LibreOffice: their
        units carry section/sheet locators (not page ordinals), so the whole
        converted PDF renders up to the page-image budget as generic
        `image N` chunks.
        """
        empty: tuple[list[Chunk], list[list[float]]] = ([], [])
        if not self.settings.pdf_page_images or source.source_type not in ("pdf", "pptx", "docx", "xlsx"):
            return empty
        label = "page" if source.source_type in ("pdf", "docx", "xlsx") else "slide"
        numbers: list[int] | None = None
        if source.source_type in ("pdf", "pptx"):
            want = "page" if source.source_type == "pdf" else "slide"
            collected: list[int] = []
            for u in units:
                if u.locator_kind != want:
                    continue
                try:
                    collected.append(int(u.locator_value))
                except (TypeError, ValueError):
                    continue
            numbers = sorted(set(collected))
            if not numbers:
                return empty
            max_pages = self.settings.pdf_page_image_max_pages
            if max_pages > 0 and len(numbers) > max_pages:
                result.warnings.append(
                    f"Page-image budget reached ({max_pages} pages); remaining pages are text-only."
                )
                numbers = numbers[:max_pages]
        else:
            # Office docs: cap the render inside the renderer (converted-PDF
            # ordinals, not unit locators). Warn when the budget truncates is
            # handled by comparing rendered count to the converter total.
            max_pages = self.settings.pdf_page_image_max_pages
            if max_pages > 0:
                numbers = list(range(1, max_pages + 1))
            # numbers=None (budget 0 = all) renders every converted page.
        try:
            from .page_images import render_pages
            rendered = render_pages(path, source.source_type, numbers)
        except Exception as exc:  # noqa: BLE001 — text still ingests
            result.warnings.append(f"Page images skipped: {exc}")
            return empty
        if not rendered:
            result.warnings.append("Page rendering produced no images; text-only ingest.")
            return empty
        embed_image = getattr(self.embedder, "embed_image", None)
        if embed_image is None:
            result.warnings.append(
                "Page images skipped: the embedding provider has no image input. "
                "Serve a joint model (e.g. embeddinggemma-2-GGUF + mmproj) from llama.cpp."
            )
            return empty
        from ..storage import FileStorage
        storage = FileStorage(self.settings.uploads_dir)
        chunks: list[Chunk] = []
        vectors: list[list[float]] = []
        skipped: list[int] = []
        consec_fail = 0
        for page_no, png, width, height in rendered:
            try:
                storage.write_page_image(source.project_id, source.id, page_no, png)
                vec = embed_image(png, "image/png")
            except Exception as exc:  # noqa: BLE001 — one bad page skips itself
                skipped.append(page_no)
                consec_fail += 1
                log.warning("Page image %d skipped for %s: %s", page_no, source.filename, exc)
                if consec_fail >= 3:
                    result.warnings.append(
                        "Page images stopped after 3 consecutive failures "
                        "(the endpoint may not support image inputs); text still indexed."
                    )
                    break
                continue
            if len(vec) != self.settings.embedding_dim:
                skipped.append(page_no)
                consec_fail += 1
                log.warning("Page image %d width %d != EMBEDDING_DIM=%d; skipped.",
                            page_no, len(vec), self.settings.embedding_dim)
                if consec_fail >= 3:
                    result.warnings.append(
                        "Page images stopped after 3 consecutive width mismatches; text still indexed."
                    )
                    break
                continue
            consec_fail = 0
            chunks.append(Chunk(
                text=f"Visual rendering of {label} {page_no} ({width} × {height} px). "
                     f"Charts, diagrams and figures on this {label} are visible in the attached image.",
                chunk_index=PAGE_IMAGE_INDEX_BASE + page_no,
                heading=f"{label.title()} {page_no}",
                locator_kind="image",
                locator_value=str(page_no),
            ))
            vectors.append([float(x) for x in vec])
        if skipped:
            shown = ", ".join(str(n) for n in skipped[:10])
            more = f" (+{len(skipped) - 10} more)" if len(skipped) > 10 else ""
            result.warnings.append(f"Page images skipped for {label} {shown}{more}; text still indexed.")
        return chunks, vectors

    def _embed_video_native(
        self, path: Path, chunks: list, source: SourceRecord, warnings: list
    ) -> list[list[float]]:
        """
        One video-clip vector per chunk, covering the chunk's own timeframe
        (10 s parts by default) — transcript text stays the chunk text, so
        retrieval matches what was said and what was shown. A set
        VIDEO_CLIP_SECONDS narrows the clip to a middle slice instead.

        With VIDEO_CLIP_SECONDS set, a middle clip of that length is embedded
        as video instead (needs a video-capable endpoint); anything the clip
        path cannot handle — missing method, cut failure, batch overflow —
        falls back to the still with a warning, so enabling it is safe.

        Chunks without timestamps — or whose frame cannot be read (midpoint
        past the file duration) — fall back to a text embedding, recorded
        loudly. Requires a joint vision-text model served by a byte-encoding
        engine — prove it first with `uv run python scripts/probe_multimodal.py`,
        then set VIDEO_EMBED_MODE=native and re-ingest the corpus.
        """
        from .video_frames import extract_frame, slice_clip

        embed_image = getattr(self.embedder, "embed_image", None)
        if embed_image is None:
            raise IngestionError(
                "The configured embedding provider does not support image inputs. "
                "Use a joint vision-text embedding model, or keep VIDEO_EMBED_MODE=transcript."
            )
        embed_video = getattr(self.embedder, "embed_video", None)
        clip_seconds = float(self.settings.video_clip_seconds or 0.0)
        if clip_seconds > 0 and embed_video is None:
            log.warning("VIDEO_CLIP_SECONDS is set but the embedder has no video input; still frames used.")
            clip_seconds = 0.0
        vectors: list[list[float]] = []
        clip_tries = 0
        clip_fallbacks = 0
        for c in chunks:
            label = f"{c.locator_kind} {c.locator_value}"
            try:
                if c.start_ts is not None and c.end_ts is not None and c.end_ts > c.start_ts:
                    mid = (c.start_ts + c.end_ts) / 2.0
                    # True video RAG: the whole ~10 s part is embedded as one
                    # clip (transcript text stays the chunk text). A set
                    # VIDEO_CLIP_SECONDS narrows it to a middle slice instead.
                    if clip_seconds > 0:
                        start = max(mid - clip_seconds / 2.0, 0.0)
                        duration = clip_seconds
                    else:
                        start = max(c.start_ts, 0.0)
                        duration = c.end_ts - c.start_ts
                    clip_tries += 1
                    try:
                        clip = slice_clip(path, start, duration,
                                          self.settings.video_clip_width)
                        vectors.append(embed_video(clip, "video/mp4"))
                        continue
                    except Exception as exc:  # noqa: BLE001 — still frame covers it
                        clip_fallbacks += 1
                        log.warning("Video clip at %.1fs skipped for %s: %s", mid, source.filename, exc)
                    try:
                        frame = extract_frame(path, mid, self.settings.video_frame_width)
                    except Exception as exc:
                        warnings.append(f"{label}: {exc} Text vector used.")
                        vectors.append(self.embedder.embed([c.text])[0])
                        continue
                    vectors.append(embed_image(frame, "image/jpeg"))
                else:
                    vectors.append(self.embedder.embed([c.text])[0])
            except (ParserError, IngestionError):
                raise
            except Exception as exc:
                raise IngestionError(f"Video frame embedding failed at {label}: {exc}") from exc
        expected = self.settings.embedding_dim
        wrong = {len(v) for v in vectors}
        if wrong and wrong != {expected}:
            raise IngestionError(
                f"Video embedding width {sorted(wrong)} does not match EMBEDDING_DIM={expected}. "
                f"Set EMBEDDING_DIM to your model's output width, then re-embed the "
                f"corpus with uv run python scripts/re_embed.py (use --check first)."
            )
        if clip_tries and clip_fallbacks == clip_tries:
            warnings.append(
                f"All {clip_tries} video clips fell back to still frames "
                "(clip too long for the server batch, or no video input) — "
                "lower VIDEO_CLIP_SECONDS or raise --ubatch-size."
            )
        return vectors

    def _embed_with_batches(self, texts: list[str], source: SourceRecord) -> list[list[float]]:
        """
        Embed every chunk, then verify the width before anything is written.

        The model output must match EMBEDDING_DIM; the database column is
        checked separately at boot and at insert time, because a model swap
        without re-embedding only shows up as a Postgres DataException
        ("expected 1536 dimensions, not 768"). Checking here turns the
        model-vs-config half into an actionable message the learner can read
        on the source row.
        """
        vectors = self.embedder.embed(texts)
        if len(vectors) != len(texts):
            raise IngestionError(
                f"Embedder returned {len(vectors)} vectors for {len(texts)} chunks."
            )
        expected = self.settings.embedding_dim
        if expected is None:
            # Fresh install that booted before setup collected an endpoint:
            # adopt whatever width the endpoint emits from here on.
            seen = {len(v) for v in vectors}
            if len(seen) == 1:
                self.settings.embedding_dim = seen.pop()
                return vectors
            raise IngestionError(
                "Embedder returned inconsistent widths — complete setup first, "
                "then re-queue this source."
            )
        wrong = {len(v) for v in vectors}
        if wrong and wrong != {expected}:
            raise IngestionError(
                f"Embedding width {sorted(wrong)} does not match EMBEDDING_DIM={expected}. "
                f"Set EMBEDDING_DIM to your model's output width, then re-embed the "
                f"corpus with uv run python scripts/re_embed.py (use --check first)."
            )
        return vectors

    @staticmethod
    def _duration(units: list[SourceUnit]) -> float | None:
        ends = [u.end_ts for u in units if u.end_ts is not None]
        return max(ends) if ends else None


def render_parsed_markdown(
    filename: str, units: list[SourceUnit], limit: int = MAX_PARSED_CHARS,
    parser: str = "",
) -> tuple[str, bool]:
    """Parser output as markdown: what the model actually ingested.

    One `## <locator>` section per unit, in reading order. Returns
    `(markdown, truncated)`. The `<!-- parser: … -->` comment records which
    parser produced the units so `units_from_markdown` can round-trip the
    name — invisible when rendered, one line in the raw view.
    """
    parts = [f"# {filename}", ""]
    if parser:
        parts.append(f"<!-- parser: {parser} -->")
        parts.append("")
    for unit in units:
        text = (unit.text or "").strip()
        if not text:
            continue
        parts.append(f"## {unit.citation_label()}")
        parts.append("")
        parts.append(text)
        parts.append("")
    markdown = "\n".join(parts).strip() + "\n"
    if len(markdown) > limit:
        cut = markdown[:limit].rstrip()
        return cut + f"\n\n…(truncated — {len(markdown) - len(cut)} more characters in the full parse)", True
    return markdown, False
    for unit in units:
        text = (unit.text or "").strip()
        if not text:
            continue
        parts.append(f"## {unit.citation_label()}")
        parts.append("")
        parts.append(text)
        parts.append("")
    markdown = "\n".join(parts).strip() + "\n"
    if len(markdown) > limit:
        cut = markdown[:limit].rstrip()
        return cut + f"\n\n…(truncated — {len(markdown) - len(cut)} more characters in the full parse)", True
    return markdown, False


__all__ = ["IngestionPipeline", "IngestionError", "IngestResult", "Chunk", "render_parsed_markdown"]
