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
from .topics import Topic, assign_topics, extract_topics, merge_locators
from .units import SourceUnit
from psycopg.errors import DataException

log = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    pass


# Fixed namespace so `chunk_id` is stable across processes and machines — the
# same source re-ingested produces the same chunk ids and upserts in place.
_CHUNK_NAMESPACE = uuid.UUID("6f1c9a7e-0d3b-5f42-9c81-1f0a2b3c4d5e")

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
    def ingest_source(self, source: SourceRecord, path: Path) -> IngestResult:
        """
        Ingest one stored file. Never raises for an expected failure — the source
        row is moved to `failed` with a readable reason instead.
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

            # 1. Parse -> units --------------------------------------------
            self.sources.set_status(source.id, "parsing", f"parsing {source.filename}")
            parsed = parse_source(path, source.source_type, self.settings)
            result.parser = parsed.parser
            result.warnings.extend(parsed.warnings)
            result.units = parsed.unit_count
            if parsed.is_empty:
                raise IngestionError(
                    "No extractable text found (scanned images or an empty file?)."
                )

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
            # ingest itself.
            try:
                markdown, truncated = render_parsed_markdown(
                    source.filename, parsed.units, MAX_PARSED_CHARS
                )
                self.sources.save_content(source.id, markdown, truncated)
            except Exception:  # noqa: BLE001 — preview is auxiliary, chunks are not
                log.exception("Could not store parsed text for %s", source.filename)

            # 3. Topics (project-level, merged by slug) ---------------------
            self.sources.set_status(
                source.id, "embedding", f"embedding {len(chunks)} chunks"
            )
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
            if source.source_type == "image":
                embed_image = getattr(self.embedder, "embed_image", None)
                if embed_image is None:
                    raise IngestionError(
                        "The configured embedding provider does not support image inputs. "
                        "Use a multimodal embedding endpoint such as LM Studio with EmbeddingGemma 2."
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
                source.source_type in ("audio", "video")
                and self.settings.audio_embed_mode == "native"
            ):
                # Direct audio vectors: one embedding per segment slice. Chunk
                # texts stay transcript text (viewer, topics, chat grounding);
                # only the vector space is acoustic.
                embeddings = self._embed_audio_native(path, chunks, source)
            else:
                embeddings = self._embed_with_batches([c.text for c in chunks], source)

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
                source.project_id, topic.slug, topic.name, topic.description
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

    def _embed_audio_native(
        self, path: Path, chunks: list, source: SourceRecord
    ) -> list[list[float]]:
        """
        One audio-slice vector per chunk, cut to the chunk's `[start_ts, end_ts]`.

        Chunks without timestamps fall back to a text embedding so the vector
        list always aligns with the chunk list. Width is verified exactly like
        the text path, because an audio-capable model swap has the same
        re-embed consequences.
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
                    wav = slice_audio_wav(path, c.start_ts, c.end_ts)
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
    filename: str, units: list[SourceUnit], limit: int = MAX_PARSED_CHARS
) -> tuple[str, bool]:
    """Parser output as markdown: what the model actually ingested.

    One `## <locator>` section per unit, in reading order. Returns
    `(markdown, truncated)`.
    """
    parts = [f"# {filename}", ""]
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
