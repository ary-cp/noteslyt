/**
 * API contract.
 *
 * Two eras live here. The document/chat/quiz models below mirror the
 * *pre-tenancy* backend and are kept only so the legacy components still
 * compile while the backend is rebuilt (plan.md D6 / Phase C). The
 * project-scoped models at the bottom mirror the current `app/schemas.py` and
 * are the ones the test console actually uses.
 *
 * Nothing in the frontend knows about HTTP beyond `api.ts`.
 */

import { API_BASE } from "./api";

// ------------------------------------------------- current API (project-scoped)

/** Resolved provider for one capability. `app/schemas.py: EndpointOut` */
export interface EndpointOut {
  base_url: string;
  model: string;
}

/** `app/schemas.py: DatabaseOut` */
export interface DatabaseOut {
  target: string;
  server: string;
  migrations_pending: string[];
}

/** `app/schemas.py: StorageOut` */
export interface StorageOut {
  backend: string;
  path: string;
}

/** `app/schemas.py: HealthResponse` */
export interface HealthResponse {
  status: string;
  llm_base_url: string;
  llm_model: string;
  embeddings: string;
  transcription: EndpointOut;
  embedding_endpoint: EndpointOut;
  database: DatabaseOut;
  parser: string;
  storage: StorageOut;
  projects: number;
  frontend?: string;
  embedding_drift?: string;
  ffmpeg?: string;
}

/** `app/schemas.py: UserOut` */
export interface UserOut {
  id: string;
  email: string;
  display_name: string;
  is_admin?: boolean;
}

/** `app/schemas.py: TokenResponse` */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserOut;
}

/** `app/schemas.py: ProjectStats` */
export interface ProjectStats {
  sources: number;
  topics: number;
  chunks: number;
  chats: number;
}

/** `app/schemas.py: ProjectOut` */
export interface ProjectOut {
  id: string;
  title: string;
  subject: string;
  description: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

/** `app/schemas.py: ProjectListItem` */
export interface ProjectListItem extends ProjectOut {
  stats: ProjectStats;
}

/** Response shape of `GET|PATCH /profile` (dict, not a Pydantic model). */
export interface ProfileOut {
  user_id: string;
  capability_level: string;
  thinking_level: string;
  goals: string;
  study_pattern: Record<string, number>;
  quiz_progress: { answered: number; correct: number; topics_practiced: number; quizzes_practiced: number };
  strengths: string[];
}

/** `GET /profile/levels` */
export interface ProfileLevels {
  capability_levels: string[];
  thinking_levels: string[];
}

/** `GET /system/llama` — vendored embedding engine status (local mode). */
export interface LlamaStatus {
  mode: "local" | "cloud";
  initialized?: boolean;
  detail?: string;
  installed?: boolean;
  build_tag?: string;
  binary?: string;
  running?: boolean;
  url?: string;
  model_present?: boolean;
  model_path?: string;
  mmproj_present?: boolean;
  update_state?: string;
  update_message?: string;
}

// --------------------------------- admin settings + drift (experimental)

/** `app/schemas.py: SettingItemOut` — secrets never carry plaintext. */
export interface SettingItem {
  key: string;
  group: string;
  source: "env" | "db" | "default" | string;
  locked: boolean;
  secret: boolean;
  value: unknown | null;
  configured: boolean;
}

/** `GET /system/settings` (admin). */
export interface SettingsOut {
  settings: SettingItem[];
  is_admin: boolean;
}

/** `PATCH /system/settings` response. */
export interface SettingsPatchOut {
  applied: Record<string, string>;
  warnings: string[];
  needs_reembed: boolean;
}

/** `GET /system/settings/status` — drift ground truth for the banner. */
export interface EmbeddingStatus {
  embedding_model: string;
  resolved_dim: number | null;
  live_widths: Record<string, number | null>;
  probed_width: number | null;
  drift: boolean;
  detail: string;
  last_model: string | null;
  last_dim: number | null;
  revert_to: string | null;
}

/** `GET /system/setup/status` — first-run onboarding state. */
export interface SetupStatus {
  setup_completed: boolean;
  is_admin: boolean;
}

/** `GET /system/reembed/status` — background re-embed job progress. */
export interface ReembedJob {
  state: "idle" | "running" | "done" | "done_with_failures" | "failed" | string;
  total: number;
  processed: number;
  ok: number;
  failed: number;
  started_at: string;
  finished_at: string;
  message: string;
  errors: { filename: string; detail: string }[];
  detail?: string;
}

/** `app/schemas.py: SourceOut` */
export interface SourceOut {
  id: string;
  project_id: string;
  filename: string;
  source_type: string;
  status: string;
  status_detail: string;
  parser: string;
  units: number;
  duration_s: number | null;
  chunk_count: number;
  file_url: string | null;
  warnings: unknown[];
  created_at: string;
}

/** `app/schemas.py: SourceUploadAccepted` — ingestion runs in the background. */
export interface SourceUploadAccepted {
  accepted: SourceOut[];
  rejected: { filename: string; reason: string }[];
}

/** `app/schemas.py: TopicSummary` — a project-level topic and where it is covered. */
export interface TopicSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  subtopics: { slug: string; name: string; description: string; concepts: string[]; locators: { kind: string; value: string }[] }[];
  sources: {
    source_id: string;
    filename: string;
    locators: { kind: string; value: string }[];
  }[];
}

/** `app/schemas.py: TopicGraphOut` — prerequisite graph over project topics. */
export interface TopicGraph {
  nodes: { id: string; slug: string; name: string }[];
  edges: { topic_slug: string; prereq_slug: string }[];
}

/** `GET /projects/{id}/ingest-status` */
export interface IngestStatus {
  counts: Record<string, number>;
  total: number;
  settled: boolean;
  processing: SourceOut[];
  ready: SourceOut[];
  failed: SourceOut[];
}

/** `GET /sources/{id}/content` — Markdown produced by the parser. */
export interface ParsedContent {
  source_id: string;
  filename: string;
  source_type: string;
  parser: string;
  markdown: string;
  truncated: boolean;
  chars: number;
}

/** `GET /sources/{id}/segments` — timestamped transcript chunks, oldest first. */
export interface SourceSegment {
  locator_kind: string;
  locator_value: string;
  heading: string;
  text: string;
  start_ts: number | null;
  end_ts: number | null;
  chunk_index: number;
}

/** URL the browser streams an uploaded original from. */
export function sourceFileUrl(id: string): string {
  return `${API_BASE}/sources/${id}/file`;
}

// ------------------------------------------------- chats + memory (Phase E)

/** One citation as returned by POST /chats/{id}/messages. */
export interface ChatCitation {
  locator_kind: string;
  locator_value: string;
  page: number | null;
  source_id: string | null;
  heading: string;
  snippet: string;
  end_ts: number | null;
}

/** `app/schemas.py: ChatOut` */
export interface ChatOut {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  message_count: number;
  created_at: string;
  last_message_at: string;
}

/** `app/schemas.py: ChatMessageOut` */
export interface ChatMessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: ChatCitation[];
  grounded: boolean | null;
  created_at: string;
}

/** UI-only viewer target: a source plus the cited locator to jump to.
 * `nonce` bumps on every open so re-clicking the same citation re-seeks.
 * For segments, `end` bounds playback to the cited clip [start, end].
 * `snippet` is the cited chunk text, used to highlight the exact lines. */
export interface ViewerTarget {
  sourceId: string;
  kind?: string;
  value?: string;
  end?: number | null;
  snippet?: string;
  nonce: number;
}

/** Locator passed from a citation chip to the viewer. */
export interface CitationLocator {
  kind: string;
  value: string;
  end?: number | null;
  snippet?: string;
}

/** `app/schemas.py: ChatDetailOut` */
export interface ChatDetailOut {
  chat: ChatOut;
  messages: ChatMessageOut[];
  total: number;
}

/** `app/schemas.py: SendMessageResponse` */
export interface SendMessageResponse {
  answer: string;
  citations: ChatCitation[];
  grounded: boolean;
  retrieved: string[];
  best_distance: number | null;
  chat_id: string;
  chat_title: string;
}

/** `app/schemas.py: TopicMemoryOut` */
export interface TopicMemory {
  topic_id: string;
  slug: string;
  name: string;
  description: string;
  p_mastery: number;
  attempts: number;
  correct: number;
  coverage_status: string;
  label: string;
  last_practiced_at: string | null;
  easiness: number;
  repetitions: number;
  interval_days: number;
  due_at: string | null;
}

/** `app/schemas.py: ProjectMemoryOut` */
export interface ProjectMemory {
  project_id: string;
  topics: TopicMemory[];
  weakest: TopicMemory[];
  coverage: Record<string, number>;
  streak: Record<string, number>;
}

// ------------------------------------------------- quiz (Phase F)

/** `app/schemas.py: QuizQuestionOut` — keys stay server-side until grading. */
export interface QuizQuestionOut {
  id: string;
  topic_id: string | null;
  topic_slug: string;
  stem: string;
  qtype: string;
  options: string[];
  locator: string;
  difficulty: number;
}

/** `app/schemas.py: QuizOut` */
export interface QuizOut {
  quiz_id: string;
  topic: TopicMemory | null;
  questions: QuizQuestionOut[];
  verification: {
    requested: number;
    generated: number;
    verified: number;
    regenerated: number;
    dropped: number;
  };
}

export interface FlashcardsOut {
  flashcards_id: string;
  cards: { id: string; front: string; back: string; topic: string; locator_kind: string; locator_value: string; source_id: string | null }[];
  requested: number | null;
}

export interface StudioJob {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  status: "queued" | "generating" | "ready" | "failed";
  payload: Record<string, unknown> | null;
  error: string;
  created_at: string;
  updated_at: string;
}

/** `app/schemas.py: AnswerOut` */
export interface AnswerOut {
  correct: boolean;
  given: number | string;
  expected: number | string | null;
  explanation: string;
  feedback: string;
  citation: { label: string; source: string | null };
  p_before: number;
  p_after: number;
  topic: TopicMemory | null;
  assessment_id: string | null;
}

/** `app/schemas.py: RecentQuizOut` */
export interface RecentQuizOut {
  quiz_id: string;
  topic_id: string | null;
  topic_slug: string | null;
  n_questions: number;
  answered: number;
  correct: number;
  created_at: string;
}

/** `app/schemas.py: LearnerOut` */
export interface LearnerOut {
  project_id: string;
  topics: TopicMemory[];
  weakest: TopicMemory[];
  coverage: Record<string, number>;
  streak: Record<string, number>;
  due_review: number;
  recent_quizzes: RecentQuizOut[];
}

/** `app/schemas.py: AssessmentReportOut` */
export interface AssessmentReportOut {
  id: string;
  project_id: string;
  label: string;
  quiz_ids: string[];
  report: {
    score?: { correct: number; total: number };
    questions?: {
      question_id: string;
      stem: string;
      qtype: string;
      given: string;
      correct: boolean;
      citation: string;
    }[];
    mastery_delta?: { slug: string; name?: string; p_before: number | null; p_after: number | null }[];
    misconceptions?: { slug: string; name?: string; points: string[] }[];
    next_up?: string | null;
    next_up_name?: string | null;
  };
  created_at: string;
}

// ------------------------------------ legacy API (pre-tenancy, kept to compile)

export interface Topic {
  id: string;
  name: string;
  description: string;
  pages: number[];
}

export interface DocumentInfo {
  id: string;
  filename: string;
  total_pages: number;
  parser: "firecrawl" | "local" | string;
  chunk_count: number;
  topics: Topic[];
  created_at: string;
  warnings: string[];
  file_url?: string | null;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface Citation {
  page: number;
  heading: string;
  snippet: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  grounded: boolean;
  retrieved_pages: number[];
  best_distance: number | null;
}

/** A chat message as rendered in the UI (superset of the API turn). */
export interface Message extends ChatTurn {
  id: string;
  citations?: Citation[];
  grounded?: boolean;
  pending?: boolean;
  error?: boolean;
}

export type MasteryLabel = "weak" | "developing" | "strong";

export interface TopicMastery {
  topic_id: string;
  name: string;
  description: string;
  p_mastery: number;
  attempts: number;
  correct: number;
  label: MasteryLabel;
}

export interface QuizQuestion {
  id: string;
  question: string;
  options: string[];
  page: number;
}

export interface QuizResponse {
  quiz_id: string;
  topic: TopicMastery;
  questions: QuizQuestion[];
  mastery: TopicMastery[];
}

export interface AnswerResponse {
  correct: boolean;
  correct_index: number;
  explanation: string;
  page: number;
  topic: TopicMastery;
  p_before: number;
  p_after: number;
  mastery: TopicMastery[];
}

export interface LearnerResponse {
  user_id: string;
  document_id: string;
  mastery: TopicMastery[];
}

export interface TranscriptSpellingsRequest { replacements: Record<string, string>; }
