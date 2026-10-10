/**
 * Typed HTTP client for the FastAPI backend.
 *
 * One function per endpoint; every function returns parsed JSON or throws an
 * `ApiError` carrying the backend's `detail` so the UI can show it verbatim.
 *
 * The bearer token lives in a module-level variable (mirrored into
 * localStorage by the caller) and is attached to every request automatically.
 */

import type {
  AnswerOut,
  AnswerResponse,
  AssessmentReportOut,
  ChatDetailOut,
  ChatOut,
  ChatResponse,
  ChatTurn,
  DocumentInfo,
  HealthResponse,
  IngestStatus,
  LearnerOut,
  LearnerResponse,
  ParsedContent,
  ProfileLevels,
  ProfileOut,
  ProjectListItem,
  ProjectMemory,
  ProjectOut,
  QuizResponse,
  SendMessageResponse,
  SourceOut,
  SourceUploadAccepted,
  StudioJob,
  TokenResponse,
  TopicSummary,
  UserOut,
} from "./types";

/**
 * Where the API lives. `VITE_API_URL` wins when set at build time; an empty
 * string means "same origin" (the backend serves the built bundle on its own
 * port); unset falls back to the local dev backend for `npm run dev`.
 */
function resolveApiBase(): string {
  const raw = import.meta.env.VITE_API_URL as string | undefined;
  if (raw !== undefined) return raw.replace(/\/$/, "");
  return import.meta.env.DEV ? "http://localhost:8000" : "";
}

export const API_BASE: string = resolveApiBase();

function apiOrigin(): string {
  return API_BASE || (typeof window !== "undefined" ? window.location.origin : "same origin");
}

export const TOKEN_KEY = "study-companion:token";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

let bearer: string | null = typeof localStorage === "undefined" ? null : localStorage.getItem(TOKEN_KEY);

export function setToken(token: string | null): void {
  bearer = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode — in-memory token still works for this session */
  }
}

export function getToken(): string | null {
  return bearer;
}

/** One entry in the test console's request log. */
export interface RequestLogEntry {
  method: string;
  path: string;
  status: number;
  ms: number;
  ok: boolean;
  at: string;
}

type RequestListener = (entry: RequestLogEntry) => void;

const listeners = new Set<RequestListener>();

/** Test console subscribes here to render a live HTTP log. */
export function addRequestListener(fn: RequestListener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function emit(entry: RequestLogEntry): void {
  for (const fn of listeners) {
    try {
      fn(entry);
    } catch {
      /* a broken listener must never break the request */
    }
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const started = performance.now();
  const method = (init?.method ?? "GET").toUpperCase();
  let response: Response;
  const headers = new Headers(init?.headers);
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    emit({ method, path, status: 0, ms: Math.round(performance.now() - started), ok: false, at: new Date().toISOString() });
    throw new ApiError(`Cannot reach the backend at ${apiOrigin()}. Is the server running?`, 0);
  }
  const ms = Math.round(performance.now() - started);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body — keep statusText */
    }
    emit({ method, path, status: response.status, ms, ok: false, at: new Date().toISOString() });
    throw new ApiError(detail, response.status);
  }
  emit({ method, path, status: response.status, ms, ok: true, at: new Date().toISOString() });
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(body: unknown, method = "POST"): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// ============================================================ current API
export const getHealth = (): Promise<HealthResponse> => request("/health");

// ------------------------------------------------------------------- auth
export const signup = (email: string, password: string, displayName: string): Promise<TokenResponse> =>
  request("/auth/signup", json({ email, password, display_name: displayName }));

export const login = (email: string, password: string): Promise<TokenResponse> =>
  request("/auth/login", json({ email, password }));

export const whoami = (): Promise<UserOut> => request("/auth/me");

export const onboarding = (capability: string, thinking: string, goals: string): Promise<void> =>
  request("/auth/onboarding", json({ capability_level: capability, thinking_level: thinking, goals }));

// ---------------------------------------------------------------- profile
export const getProfile = (): Promise<ProfileOut> => request("/profile");

export const updateProfile = (body: {
  capability_level?: string;
  thinking_level?: string;
  goals?: string;
}): Promise<ProfileOut> => request("/profile", json(body, "PATCH"));

export const getProfileLevels = (): Promise<ProfileLevels> => request("/profile/levels");

// --------------------------------------------------------------- projects
export const listProjects = (): Promise<ProjectListItem[]> => request("/projects");

export const createProject = (body: { title: string; subject?: string; description?: string }): Promise<ProjectOut> =>
  request("/projects", json(body));

export const getProject = (id: string): Promise<ProjectOut> => request(`/projects/${id}`);

export const updateProject = (
  id: string,
  body: { title?: string; subject?: string; description?: string; archived?: boolean },
): Promise<ProjectOut> => request(`/projects/${id}`, json(body, "PATCH"));

export const deleteProject = (id: string): Promise<void> => request(`/projects/${id}`, { method: "DELETE" });

// ---------------------------------------------------------------- sources
export const listSources = (projectId: string): Promise<SourceOut[]> =>
  request(`/projects/${projectId}/sources`);

/**
 * Upload one or more files. Returns 202 with a queued row per accepted file —
 * ingestion then runs in the background, so poll `getSource` until it settles.
 */
export const uploadSources = (projectId: string, files: File[]): Promise<SourceUploadAccepted> => {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  return request(`/projects/${projectId}/sources`, { method: "POST", body: form });
};

export const getSource = (id: string): Promise<SourceOut> => request(`/sources/${id}`);

export const getSourceContent = (id: string): Promise<ParsedContent> =>
  request(`/sources/${id}/content`);

export const deleteSource = (id: string): Promise<void> => request(`/sources/${id}`, { method: "DELETE" });

export const listTopics = (projectId: string): Promise<TopicSummary[]> =>
  request(`/projects/${projectId}/topics`);

export const ingestStatus = (projectId: string): Promise<IngestStatus> =>
  request(`/projects/${projectId}/ingest-status`);

// ------------------------------------------------------------------ chats
export const listChats = (projectId: string): Promise<ChatOut[]> =>
  request(`/projects/${projectId}/chats`);

export const createChat = (projectId: string, title?: string): Promise<ChatOut> =>
  request(`/projects/${projectId}/chats`, json(title ? { title } : {}));

export const getChat = (id: string, limit = 50, offset = 0): Promise<ChatDetailOut> =>
  request(`/chats/${id}?limit=${limit}&offset=${offset}`);

export const renameChat = (id: string, title: string): Promise<ChatOut> =>
  request(`/chats/${id}`, json({ title }, "PATCH"));

export const deleteChat = (id: string): Promise<void> =>
  request(`/chats/${id}`, { method: "DELETE" });

/** Send one message; the answer is grounded, cited, and persisted server-side. */
export const sendMessage = (id: string, content: string): Promise<SendMessageResponse> =>
  request(`/chats/${id}/messages`, json({ content }));

export async function streamMessage(
  id: string,
  content: string,
  onToken: (token: string) => void,
  responseStyle: "default" | "precise" | "explanatory" = "default",
  sourceIds?: string[],
): Promise<SendMessageResponse> {
  const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  const response = await fetch(`${API_BASE}/chats/${id}/messages/stream`, {
    method: "POST", headers, body: JSON.stringify({ content, response_style: responseStyle, source_ids: sourceIds }),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* keep HTTP status text */ }
    throw new ApiError(detail, response.status);
  }
  if (!response.body) throw new ApiError("The server did not open a response stream.", response.status);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let final: SendMessageResponse | null = null;
  while (true) {
    const { value, done } = await reader.read();
    pending += decoder.decode(value, { stream: !done });
    const events = pending.split("\n\n");
    pending = events.pop() ?? "";
    for (const event of events) {
      const dataLine = event.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      const message = JSON.parse(dataLine.slice(6)) as { type: string; value: string | SendMessageResponse };
      if (message.type === "token" && typeof message.value === "string") onToken(message.value);
      else if (message.type === "final") final = message.value as SendMessageResponse;
      else if (message.type === "error") throw new ApiError(String(message.value), 500);
    }
    if (done) break;
  }
  if (!final) throw new ApiError("The response stream ended before the answer was finalized.", 500);
  return final;
}

// ------------------------------------------------------------------ memory
/** Tier-2 state: per-topic mastery, weakest topics, coverage, study streak. */
export const getMemory = (projectId: string): Promise<ProjectMemory> =>
  request(`/projects/${projectId}/memory`);

// ------------------------------------------------------------------- quiz
export const createQuiz = (
  projectId: string,
  body: { topic_id?: string; n_questions?: number; qtypes?: string[]; source_ids?: string[] },
): Promise<StudioJob> => request(`/projects/${projectId}/quiz`, json(body));

export const createExam = (projectId: string, body: { topic_id?: string; n_questions: number; qtypes?: string[]; source_ids?: string[] }): Promise<StudioJob> =>
  request(`/projects/${projectId}/exam`, json(body));

export const createFlashcards = (projectId: string, body: { topic_id?: string; n_cards: number; source_ids?: string[] }): Promise<StudioJob> =>
  request(`/projects/${projectId}/flashcards`, json(body));

export const listStudioJobs = (projectId: string, waitSeconds = 0): Promise<StudioJob[]> =>
  request(`/projects/${projectId}/studio-jobs${waitSeconds ? `?wait_seconds=${waitSeconds}` : ""}`);
export async function waitForStudioJob(projectId: string, jobId: string): Promise<StudioJob> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const job = (await listStudioJobs(projectId)).find((row) => row.id === jobId);
    if (!job) throw new ApiError("Studio job was not found.", 404);
    if (job.status === "ready") return job;
    if (job.status === "failed") throw new ApiError(job.error || "Studio generation failed.", 500);
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
  }
  throw new ApiError("Studio generation is still running. Check the Studio sidebar.", 408);
}
export const saveStudioArtifact = (projectId: string, body: { kind: string; title: string; payload: Record<string, unknown> }): Promise<StudioJob> =>
  request(`/projects/${projectId}/studio-jobs`, json(body));

export const getQuizAnswers = (quizId: string): Promise<Record<string, AnswerOut>> =>
  request(`/quizzes/${quizId}/answers`);

export const submitAnswer = (
  quizId: string,
  questionId: string,
  answer: number | string,
): Promise<AnswerOut> => request(`/quizzes/${quizId}/answer`, json({ question_id: questionId, answer }));

export const fetchLearner = (projectId: string): Promise<LearnerOut> =>
  request(`/projects/${projectId}/learner`);

export const getReport = (assessmentId: string): Promise<AssessmentReportOut> =>
  request(`/assessments/${assessmentId}/report`);

/**
 * Download the stored original with auth. A plain `<a href>` cannot send the
 * bearer token, so the viewer fetches the bytes and renders a blob URL.
 */
export const fetchSourceBlob = async (id: string): Promise<Blob> => {
  const headers = new Headers();
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/sources/${id}/file`, { headers });
  } catch {
    throw new ApiError(`Cannot reach the backend at ${API_BASE}. Is uvicorn running?`, 0);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* binary error body — keep statusText */
    }
    throw new ApiError(detail, response.status);
  }
  return await response.blob();
};

// ============================================================ legacy API
// Pre-tenancy endpoints (document-scoped). Unused while the backend is
// rebuilt; kept so the legacy components still typecheck (plan.md D6).
// ------------------------------------------------------------------ documents
export const uploadPdf = (file: File): Promise<DocumentInfo> => {
  const form = new FormData();
  form.append("file", file, file.name);
  return request<DocumentInfo>("/upload", { method: "POST", body: form });
};

export const listDocuments = (): Promise<DocumentInfo[]> => request("/documents");

export const deleteDocument = (id: string): Promise<void> => request(`/documents/${id}`, { method: "DELETE" });

/** URL the PDF viewer streams the original file from. */
export const documentFileUrl = (id: string): string => `${API_BASE}/documents/${id}/file`;

// ----------------------------------------------------------------------- chat
export const askQuestion = (documentId: string, question: string, history: ChatTurn[]): Promise<ChatResponse> =>
  request("/chat", json({ document_id: documentId, question, history }));

// ----------------------------------------------------------------------- quiz
export const generateQuiz = (userId: string, documentId: string, topicId?: string): Promise<QuizResponse> =>
  request("/quiz", json({ user_id: userId, document_id: documentId, topic_id: topicId ?? null }));

export const answerQuestion = (
  userId: string,
  quizId: string,
  questionId: string,
  selectedIndex: number,
): Promise<AnswerResponse> =>
  request("/quiz/answer", json({ user_id: userId, quiz_id: quizId, question_id: questionId, selected_index: selectedIndex }));

export const getLearner = (userId: string, documentId: string): Promise<LearnerResponse> =>
  request(`/learner/${encodeURIComponent(userId)}/${documentId}`);
