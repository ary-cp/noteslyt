/**
 * API contract — mirrors `backend/app/schemas.py` one-to-one.
 * Keep the two files in sync; nothing else in the frontend knows about HTTP.
 */

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
