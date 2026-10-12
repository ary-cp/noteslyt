import { useCallback, useEffect, useMemo, useState } from "react";
import { DRIFT_EVENT, getAdminSettings, getEmbeddingStatus, getReembedStatus, patchAdminSettings, startReembed } from "../../lib/api";
import type { EmbeddingStatus, ReembedJob, SettingItem } from "../../lib/types";
import { useAuth } from "../../lib/auth";
import { useToast } from "../../lib/toast";
import { Button, Spinner } from "../ui/Button";
import { Dropdown } from "../ui/Menu";
import { TextInput, Toggle } from "../ui/Controls";

export const ADMIN_GROUPS = ["chat", "embeddings", "transcription", "media", "parsing", "retrieval", "memory", "learner", "uploads"] as const;

export const ADMIN_GROUP_TITLES: Record<string, string> = {
  chat: "Chat",
  embeddings: "Embeddings",
  transcription: "Transcription",
  media: "Media",
  parsing: "Parsing",
  retrieval: "Retrieval",
  memory: "Memory",
  learner: "Learner model",
  uploads: "Uploads",
};

// Sidebar tabs (admin nav). "chat" lives under Preferences instead, and the
// whole "retrieval" group folds into the Embeddings tab — one embedding home.
export const ADMIN_NAV_TABS = ["embeddings", "transcription", "media", "parsing", "memory", "learner", "uploads"] as const;

// Download sources are fixed — nobody should change them, so they stay out
// of the UI entirely (the API still accepts them). Same for the transcription
// download directory.
const HIDDEN_KEYS = new Set([
  "embedding_local_repo",
  "transcription_local_repo",
  "transcription_local_model_repo",
  "transcription_local_dir",
  "llama_github_repo",
]);

// Mode switches pin to the top of their tab.
const PINNED_TOP: Record<string, string[]> = {
  embeddings: ["embedding_mode", "audio_embed_mode", "video_embed_mode"],
  transcription: ["transcription_mode"],
};

// The two native-mode switches live with the embedding mode, not in Media.
const EMBED_MODE_KEYS = new Set(["audio_embed_mode", "video_embed_mode"]);

// Cloud tabs show only the endpoint trio (+ width/chunking for embeddings);
// local-only knobs would be dead controls. Local tabs hide the ignored remote trio.
const EMBEDDING_CLOUD_KEYS = new Set([
  "embedding_mode", "embedding_base_url", "embedding_api_key", "embedding_model", "embedding_dim",
]);
const CHUNK_KEYS = new Set(["chunk_size", "chunk_overlap"]);
const TRANSCRIPTION_CLOUD_KEYS = new Set([
  "transcription_mode", "transcription_base_url", "transcription_api_key", "transcription_model",
]);
const IGNORED_IN_LOCAL = new Set(["embedding_base_url", "embedding_api_key", "transcription_base_url", "transcription_api_key"]);

export type AdminTab = (typeof ADMIN_GROUPS)[number];

const TAB_GROUPS: Record<string, string[]> = {
  // One embedding home: vectors + chunking + retrieval tuning.
  embeddings: ["embeddings", "retrieval"],
  chat: ["chat"],
  transcription: ["transcription"],
  media: ["media"],
  parsing: ["parsing"],
  memory: ["memory"],
  learner: ["learner"],
  uploads: ["uploads"],
};

export function tabItems(all: SettingItem[], tab: AdminTab): SettingItem[] {
  const groups = TAB_GROUPS[tab] ?? [tab];
  return all.filter((item) => {
    if (HIDDEN_KEYS.has(item.key)) return false;
    if (EMBED_MODE_KEYS.has(item.key)) return tab === "embeddings";
    return groups.includes(item.group);
  });
}

function orderedRows(all: SettingItem[], tab: AdminTab, texts: Record<string, string>): SettingItem[] {
  const rows = visibleItems(all, tab, texts);
  const pinned = PINNED_TOP[tab] ?? [];
  const rank = new Map(pinned.map((key, i) => [key, i]));
  return [...rows].sort((a, b) => (rank.get(a.key) ?? 999) - (rank.get(b.key) ?? 999));
}

function effectiveMode(all: SettingItem[], texts: Record<string, string>, key: string, fallback: string): string {
  // Draft first: flipping the mode dropdown reshapes the tab immediately,
  // before anything is saved.
  const draft = (texts[key] ?? "").trim().toLowerCase();
  if (draft) return draft;
  const loaded = all.find((i) => i.key === key)?.value;
  return typeof loaded === "string" ? loaded.toLowerCase() : fallback;
}

function visibleItems(all: SettingItem[], tab: AdminTab, texts: Record<string, string>): SettingItem[] {
  return tabItems(all, tab).filter((item) => {
    if (tab === "embeddings") {
      if (effectiveMode(all, texts, "embedding_mode", "cloud") === "cloud") {
        return EMBEDDING_CLOUD_KEYS.has(item.key) || CHUNK_KEYS.has(item.key) || EMBED_MODE_KEYS.has(item.key);
      }
      return !IGNORED_IN_LOCAL.has(item.key);
    }
    if (tab === "transcription") {
      if (effectiveMode(all, texts, "transcription_mode", "cloud") === "cloud") {
        return TRANSCRIPTION_CLOUD_KEYS.has(item.key);
      }
      return !IGNORED_IN_LOCAL.has(item.key);
    }
    return true;
  });
}

const ENUM_OPTIONS: Record<string, { value: string; label: string }[]> = {
  embedding_mode: [
    { value: "local", label: "Local server" },
    { value: "cloud", label: "Cloud endpoint" },
  ],
  embedding_protocol: [
    { value: "auto", label: "Auto" },
    { value: "llama_cpp", label: "llama.cpp shape" },
    { value: "jina", label: "Jina native" },
    { value: "gemini", label: "Gemini native" },
  ],
  transcription_mode: [
    { value: "cloud", label: "Cloud endpoint" },
    { value: "local", label: "Local server" },
  ],
  audio_embed_mode: [
    { value: "transcript", label: "Transcript" },
    { value: "native", label: "Native audio" },
  ],
  video_embed_mode: [
    { value: "transcript", label: "Transcript" },
    { value: "native", label: "Native video" },
  ],
  parser: [
    { value: "auto", label: "Auto" },
    { value: "firecrawl", label: "Firecrawl" },
    { value: "local", label: "Local" },
  ],
};

const DESCRIPTIONS: Record<string, string> = {
  llm_base_url: "Endpoint serving the chat model (OpenAI-compatible).",
  llm_api_key: "Key sent to the chat endpoint.",
  llm_model: "Chat model id.",
  llm_temperature: "Lower stays faithful to your sources; higher gets creative.",
  llm_max_tokens: "Maximum tokens per reply.",
  embedding_mode: "Vendored local server, or a remote embedding endpoint.",
  embedding_base_url: "Embedding endpoint. Inherits the chat URL when empty.",
  embedding_api_key: "Key sent to the embedding endpoint.",
  embedding_model: "Embedding model id. Changing it requires a re-embed.",
  embedding_protocol: "Wire shape for image/audio/video vectors. Text always uses the OpenAI-compatible endpoint.",
  embedding_dim: "Vector width. Empty means auto-detect from the model.",
  embedding_batch_size: "Texts embedded per request.",
  embedding_max_input_chars: "Longer inputs are split on sentences and mean-pooled.",
  embedding_local_dir: "Vendored engine + weights directory.",
  embedding_local_repo: "Download source for the engine and weights.",
    embedding_local_model_file: "Weight filename inside the repo.",
  embedding_local_mmproj_file: "Vision projector filename for image input.",
  embedding_local_model_path: "Use your own weights instead of the download.",
  embedding_local_mmproj_path: "Use your own projector instead of the download.",
  embedding_idle_unload_s: "Idle seconds before the server unloads (0 keeps it up).",
  llama_github_repo: "Source for engine updates.",
  audio_embed_mode: "Transcript text, or raw-audio vectors (needs a joint model).",
  audio_segment_seconds: "Seconds of audio per embedded part.",
  video_embed_mode: "Transcript text, or per-part video vectors.",
  video_segment_seconds: "Seconds of video per embedded part.",
  video_clip_seconds: "Middle slice embedded instead of the full part (0 = full part).",
  video_clip_width: "Maximum clip width in pixels (downscales, never upscales).",
  transcription_mode: "Cloud endpoint, or the vendored local server.",
  transcription_base_url: "Speech-to-text endpoint (inherits chat URL when empty).",
  transcription_api_key: "Key sent to the speech-to-text endpoint.",
  transcription_model: "Speech-to-text model id.",
  transcription_chunk_s: "Longest audio slice per request — must fit the STT encoder window.",
  transcription_language: "Language hint (e.g. en). Empty means auto-detect.",
  transcription_local_dir: "Vendored engine + weights directory.",
  transcription_local_repo: "Download source for the engine.",
  transcription_local_model_repo: "Download source for the ASR weights.",
  transcription_local_model_file: "ASR weight filename.",
  transcription_local_model_path: "Use your own weights instead of the download.",
  transcription_local_model_id: "Model id served by the local server.",
  transcription_local_family: "Model family for the local server config.",
  transcription_idle_unload_s: "Idle seconds before the server unloads (0 keeps it up).",
  video_captions: "Caption one frame per Nth segment with the vision model.",
  video_frame_every_n: "Caption every Nth segment unit.",
  video_frame_width: "Caption frame width in pixels.",
  video_frame_hamming: "Below this hash distance a frame counts as a duplicate.",
  video_max_frames: "Caption budget per source.",
  firecrawl_api_key: "Firecrawl key. Empty means local parsers only.",
  firecrawl_api_url: "Firecrawl endpoint.",
  firecrawl_max_pages: "Maximum pages parsed per document (1 credit each).",
  parser: "Auto uses Firecrawl when keyed, otherwise local parsers.",
  top_k: "Chunks pulled into the prompt.",
  max_distance: "Refuse when the best chunk is farther than this (2.0 disables).",
  chunk_size: "Characters per chunk (never crosses a page boundary).",
  chunk_overlap: "Characters shared between neighboring chunks.",
  pdf_page_images: "Also embed every PDF page as an image, so charts stay searchable.",
  pdf_page_image_max_pages: "Per-source render budget (0 renders all pages).",
  history_turns: "Prior messages forwarded to the model.",
  memory_chat_window: "Recent messages kept verbatim.",
  memory_summarize_threshold: "Fold older turns into a summary past this many messages.",
  memory_max_facts_global: "Lines in the global (profile) block.",
  memory_max_topics: "Weakest topics shown in the project block.",
  memory_context_chars: "Cap on retrieved context text.",
  memory_rewrite: "Rewrite follow-up questions before embedding.",
  memory_refine_every: "Profile refinement pass every N user turns.",
  bkt_p_init: "Initial mastery estimate for unseen topics.",
  bkt_p_transit: "Chance of learning a topic per attempt.",
  bkt_p_slip: "Chance of missing a known topic.",
  bkt_p_guess: "Chance of guessing an unknown topic (4 options ≈ 0.25).",
  quiz_questions: "Questions generated per quiz.",
  max_topics: "Maximum topics per study plan.",
  quiz_similarity_max: "Regenerate stems at or above this similarity.",
  quiz_regen_rounds: "Extra generation rounds for near-duplicates.",
  quiz_default_tolerance: "Fallback tolerance for numerical answers.",
  max_upload_mb: "Documents, images and spreadsheets.",
  max_video_mb: "Video uploads (transcription + embedding cost).",
  max_audio_mb: "Audio uploads (transcription + embedding cost).",
};

function prettyLabel(key: string): string {
  const words = key.replace(/_/g, " ").split(" ");
  return words
    .map((w, i) => {
      const up = w.toUpperCase();
      if (["LLM", "BKT", "STT", "MB", "PX", "URL", "API", "PDF", "OCR"].includes(up)) return up;
      if (i === 0) return w.charAt(0).toUpperCase() + w.slice(1);
      return w;
    })
    .join(" ");
}

function displayValue(item: SettingItem): string {
  if (item.secret) return item.configured ? "Set" : "Not set";
  if (item.value === null || item.value === undefined) return "—";
  return String(item.value);
}

function matches(item: SettingItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    item.key.toLowerCase().includes(q) ||
    prettyLabel(item.key).toLowerCase().includes(q) ||
    (DESCRIPTIONS[item.key] ?? "").toLowerCase().includes(q)
  );
}

/**
 * Admin-only server configuration (layered: env > database > default).
 *
 * Open WebUI-style rows: label left with the control right, description
 * underneath, section per group. Secrets are write-only (the API never
 * returns plaintext); env-pinned keys render locked.
 */
export function AdminSettings({
  query, tab, onMatchCounts,
}: {
  query: string;
  tab: AdminTab;
  onMatchCounts?: (counts: Record<string, number>) => void;
}) {
  const { user } = useAuth();
  const { push } = useToast();
  const [items, setItems] = useState<SettingItem[] | null>(null);
  const [status, setStatus] = useState<EmbeddingStatus | null>(null);
  const [job, setJob] = useState<ReembedJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [bools, setBools] = useState<Record<string, boolean>>({});
  const [cleared, setCleared] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [jobBusy, setJobBusy] = useState(false);
  const [probeBusy, setProbeBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [s, st] = await Promise.all([getAdminSettings(), getEmbeddingStatus()]);
      setItems(s.settings);
      setStatus(st);
      const initial: Record<string, string> = {};
      const initialBools: Record<string, boolean> = {};
      for (const item of s.settings) {
        if (item.secret) initial[item.key] = "";
        else if (typeof item.value === "boolean") initialBools[item.key] = item.value;
        else initial[item.key] = item.value === null || item.value === undefined ? "" : String(item.value);
      }
      setTexts(initial);
      setBools(initialBools);
      setCleared({});
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Could not load server settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user?.is_admin) void refresh();
  }, [user, refresh]);

  const groups = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const g of ADMIN_GROUPS) {
      counts[g] = tabItems(items ?? [], g).filter((item) => matches(item, query)).length;
    }
    return counts;
  }, [items, query]);

  useEffect(() => {
    if (onMatchCounts) onMatchCounts(groups);
  }, [groups, onMatchCounts]);

  const refreshJob = useCallback(async () => {
    try {
      const next = await getReembedStatus();
      setJob(next);
      if (next.state === "running") {
        window.setTimeout(() => void refreshJob(), 3000);
      } else {
        const st = await getEmbeddingStatus().catch(() => null);
        if (st) setStatus(st);
        try {
          window.dispatchEvent(new CustomEvent(DRIFT_EVENT));
        } catch {
          /* ignore */
        }
      }
    } catch {
      /* keep last-known job state */
    }
  }, []);

  const dirtyKeys = useMemo(() => {
    if (!items) return [];
    return items.filter((item) => {
      if (item.locked) return false;
      if (cleared[item.key]) return true;
      if (item.secret) return (texts[item.key] ?? "") !== "";
      if (typeof item.value === "boolean") return bools[item.key] !== item.value;
      const current = item.value === null || item.value === undefined ? "" : String(item.value);
      return (texts[item.key] ?? "") !== current;
    });
  }, [items, texts, bools, cleared]);

  const save = async () => {
    if (!items || dirtyKeys.length === 0) {
      push("No changes to save", { tone: "info" });
      return;
    }
    setSaving(true);
    setSaveError("");
    const body: Record<string, unknown> = {};
    for (const item of dirtyKeys) {
      if (cleared[item.key]) {
        body[item.key] = null;
        continue;
      }
      if (typeof item.value === "boolean") {
        body[item.key] = bools[item.key];
        continue;
      }
      const text = texts[item.key] ?? "";
      if (item.secret) {
        if (text !== "") body[item.key] = text;
        continue;
      }
      if (typeof item.value === "number") {
        if (text.trim() === "") body[item.key] = null;
        else {
          const num = Number(text);
          if (!Number.isFinite(num)) {
            setSaveError(`${prettyLabel(item.key)}: "${text}" is not a number.`);
            setSaving(false);
            return;
          }
          body[item.key] = num;
        }
        continue;
      }
      body[item.key] = text === "" && item.value === null ? null : text;
    }
    try {
      const result = await patchAdminSettings(body);
      for (const warning of result.warnings) push("Heads up", { detail: warning, tone: "warning" });
      if (!result.needs_reembed) push("Server settings saved", { tone: "success" });
      else push("Server settings saved — re-embed needed", { detail: result.warnings[0], tone: "warning" });
      await refresh();
      try {
        window.dispatchEvent(new CustomEvent(DRIFT_EVENT));
      } catch {
        /* ignore */
      }
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onReembed = async (force: boolean) => {
    setJobBusy(true);
    try {
      const started = await startReembed(force);
      setJob(started);
      push("Re-embed started", { detail: "Uploads and chat resume when it lands.", tone: "info" });
      void refreshJob();
    } catch (e) {
      push("Could not start re-embed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setJobBusy(false);
    }
  };

  const onProbe = async () => {
    setProbeBusy(true);
    try {
      setStatus(await getEmbeddingStatus(true));
    } catch (e) {
      push("Probe failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setProbeBusy(false);
    }
  };

  const onRevert = async (model: string) => {
    setSaving(true);
    try {
      await patchAdminSettings({ embedding_model: model });
      push("Reverted embedding model", { detail: `Back to ${model} — no re-embed needed.`, tone: "success" });
      await refresh();
      try {
        window.dispatchEvent(new CustomEvent(DRIFT_EVENT));
      } catch {
        /* ignore */
      }
    } catch (e) {
      push("Revert failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  if (!user?.is_admin) return null;

  const statusBlock = (
    <>
      {loading || !status ? (
          <p className="mt-2 flex items-center gap-2 text-sm text-[var(--text-muted)]">
            {loadError ? <span role="alert" className="text-[var(--danger)]">{loadError}</span> : <><Spinner size={14} /> Loading server settings…</>}
          </p>
        ) : (
          <div className="mt-2 grid gap-3">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[13px] font-medium">
                  <span className="font-mono">{status.embedding_model}</span>
                  {status.resolved_dim !== null ? ` · ${status.resolved_dim}-d` : ""}
                </p>
                <p className="mt-1 text-[13px] text-[var(--text-muted)]">
                  {status.drift ? status.detail : "Vectors match the configured model."}
                  {status.last_model && status.last_model !== status.embedding_model ? ` Last ingested with ${status.last_model}.` : ""}
                  {status.probed_width !== null && status.probed_width !== undefined ? ` Endpoint emits ${status.probed_width}-d.` : ""}
                </p>
                <button
                  type="button"
                  onClick={() => void onProbe()}
                  disabled={probeBusy}
                  title="Ask the live endpoint for its width. Wakes the embedding server, which pauses any running transcription."
                  className="mt-1 cursor-pointer text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:cursor-wait disabled:opacity-60"
                >
                  {probeBusy ? "Probing endpoint…" : "Probe endpoint width"}
                </button>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {status.drift ? (
                  <>
                    <Button variant="primary" loading={jobBusy} disabled={jobBusy || job?.state === "running"} onClick={() => void onReembed(false)}>
                      {job?.state === "running" ? "Re-embedding…" : "Re-embed now"}
                    </Button>
                    {status.revert_to && (
                      <Button variant="secondary" disabled={saving} onClick={() => void onRevert(status.revert_to!)}>
                        Revert to {status.revert_to}
                      </Button>
                    )}
                  </>
                ) : (
                  <Button variant="secondary" loading={jobBusy} disabled={jobBusy || job?.state === "running"} onClick={() => void onReembed(true)}>
                    Force re-embed
                  </Button>
                )}
              </div>
            </div>
            {job?.state === "running" && (
              <p className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                <Spinner size={12} /> {job.processed}/{job.total}{job.message ? ` · ${job.message}` : ""}
              </p>
            )}
            {job && job.state !== "running" && job.state !== "idle" && (
              <p className="text-xs text-[var(--text-secondary)]">
                Last job {job.state}: {job.message}
                {job.errors.length > 0 && ` (${job.errors.slice(0, 3).map((e) => e.filename).join(", ")}${job.errors.length > 3 ? ", …" : ""})`}
              </p>
            )}
          </div>
        )}
        {loadError && !loading && (
          <div className="mt-2"><Button variant="secondary" onClick={() => void refresh()}>Retry</Button></div>
        )}
    </>
  );

  const rows = items ? orderedRows(items, tab, texts).filter((item) => matches(item, query)) : [];
  const isCloudEmbeddings = tab === "embeddings" && items !== null && effectiveMode(items ?? [], texts, "embedding_mode", "cloud") === "cloud";
  const isLocalEmbeddings = tab === "embeddings" && items !== null && !isCloudEmbeddings;
  const isCloudTranscription = tab === "transcription" && items !== null && effectiveMode(items ?? [], texts, "transcription_mode", "cloud") === "cloud";
  const isLocalTranscription = tab === "transcription" && items !== null && !isCloudTranscription;

  return (
    <div>
      <h3 className="text-[15px] font-semibold">{ADMIN_GROUP_TITLES[tab] ?? tab}</h3>
      {tab === "embeddings" && (
        <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--surface-secondary)] p-3">
          {statusBlock}
        </div>
      )}
      {isCloudEmbeddings && (
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Cloud mode — vectors come from the endpoint above. Switch to Local to manage downloads, model files and batch sizes.
        </p>
      )}
      {isLocalEmbeddings && (
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Local mode — the vendored server ignores the remote base URL and key.
        </p>
      )}
      {isCloudTranscription && (
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Cloud mode — transcripts come from the endpoint above. Switch to Local for the vendored server.
        </p>
      )}
      {isLocalTranscription && (
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Local mode — the vendored server ignores the remote base URL and key.
        </p>
      )}
      {loading ? (
        <p className="mt-2 flex items-center gap-2 text-sm text-[var(--text-muted)]"><Spinner size={14} /> Loading server settings…</p>
      ) : loadError ? (
        <div className="mt-2 grid gap-2">
          <p role="alert" className="text-sm text-[var(--danger)]">{loadError}</p>
          <div><Button variant="secondary" onClick={() => void refresh()}>Retry</Button></div>
        </div>
      ) : rows.length === 0 ? (
        <p className="mt-2 text-[13px] text-[var(--text-muted)]">No settings match “{query.trim()}” in this tab.</p>
      ) : (
        <div className="mt-1">
          {rows.map((item) => (
            <SettingRow
              key={item.key}
              item={item}
              text={texts[item.key] ?? ""}
              boolValue={bools[item.key] ?? false}
              cleared={!!cleared[item.key]}
              onText={(v) => {
                setTexts((prev) => ({ ...prev, [item.key]: v }));
                setCleared((prev) => ({ ...prev, [item.key]: false }));
              }}
              onBool={(v) => {
                setBools((prev) => ({ ...prev, [item.key]: v }));
                setCleared((prev) => ({ ...prev, [item.key]: false }));
              }}
              onToggleClear={() => setCleared((prev) => ({ ...prev, [item.key]: !prev[item.key] }))}
            />
          ))}
        </div>
      )}

      {items && !loading && !loadError && (
        <div className="sticky bottom-0 flex items-center justify-end gap-3 border-t border-[var(--border)] bg-[var(--modal)] py-3">
          {saveError && (
            <p role="alert" className="mr-auto text-sm text-[var(--danger)]">{saveError}</p>
          )}
          {dirtyKeys.length > 0 && (
            <Button
              variant="ghost"
              disabled={saving}
              onClick={() => {
                setCleared({});
                void refresh();
              }}
            >
              Discard
            </Button>
          )}
          <Button variant="primary" loading={saving} disabled={saving || dirtyKeys.length === 0} onClick={() => void save()}>
            Save{dirtyKeys.length > 0 ? ` (${dirtyKeys.length})` : ""}
          </Button>
        </div>
      )}
    </div>
  );
}

function SettingRow({
  item, text, boolValue, cleared, onText, onBool, onToggleClear,
}: {
  item: SettingItem;
  text: string;
  boolValue: boolean;
  cleared: boolean;
  onText: (v: string) => void;
  onBool: (v: boolean) => void;
  onToggleClear: () => void;
}) {
  const options = ENUM_OPTIONS[item.key];
  const description = DESCRIPTIONS[item.key] ?? "";
  const isBool = typeof item.value === "boolean" && !item.secret;

  return (
    <div className="border-b border-[var(--border)] py-3 last:border-b-0">
      <div className="flex items-center justify-between gap-4">
        <p className="text-[13px] font-medium">{prettyLabel(item.key)}</p>
        {isBool ? (
          <div className={item.locked ? "pointer-events-none opacity-60" : ""}>
            <Toggle checked={boolValue} label={prettyLabel(item.key)} onChange={onBool} />
          </div>
        ) : options ? (
          <Dropdown
            value={text || (typeof item.value === "string" ? item.value : "")}
            ariaLabel={item.key}
            onChange={(v) => { if (!item.locked) onText(v); }}
            options={options.map((o) => ({ ...o, disabled: item.locked }))}
          />
        ) : null}
      </div>
      {!isBool && !options && (
        <div className="mt-2 flex items-center gap-2">
          <TextInput
            type={item.secret ? "password" : typeof item.value === "number" ? "number" : "text"}
            value={text}
            placeholder={
              item.secret
                ? item.configured
                  ? "••••••"
                  : "Not set"
                : item.value === null || item.value === undefined
                  ? "Auto / inherit"
                  : displayValue(item)
            }
            disabled={item.locked}
            aria-label={item.key}
            onChange={(e) => onText(e.target.value)}
          />
          {!item.locked && ((item.source === "db" && !item.secret) || cleared) && (
            <Button variant="ghost" onClick={onToggleClear}>
              {cleared ? "Undo" : "Reset"}
            </Button>
          )}
          {!item.locked && item.secret && item.configured && !cleared && (
            <Button variant="ghost" onClick={() => { onText(""); onToggleClear(); }}>
              Clear
            </Button>
          )}
        </div>
      )}
      {description && <p className="mt-1 text-[13px] text-[var(--text-muted)]">{description}</p>}
      <p className="mt-1 font-mono text-[11px] text-[var(--text-muted)]">
        {item.key} · {cleared ? "resetting" : item.source === "env" ? "server (.env)" : item.source === "db" ? "database" : "default"}
        {item.locked ? " · locked" : ""}
      </p>
    </div>
  );
}
