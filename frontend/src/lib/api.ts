/**
 * Typed HTTP client for the FastAPI backend.
 *
 * One function per endpoint; every function returns parsed JSON or throws an
 * `ApiError` carrying the backend's `detail` so the UI can show it verbatim.
 */

import type {
  AnswerResponse,
  ChatResponse,
  ChatTurn,
  DocumentInfo,
  LearnerResponse,
  QuizResponse,
} from "./types";

export const API_BASE: string = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
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
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(body: unknown, method = "POST"): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

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
