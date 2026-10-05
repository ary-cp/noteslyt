/**
 * Chat state for one document.
 *
 * Optimistic UI: the user's message and a "pending" assistant placeholder are
 * appended immediately; the placeholder is replaced in place when `/chat`
 * resolves (or turned into an error row if it fails).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, askQuestion } from "../lib/api";
import type { ChatTurn, Message } from "../lib/types";

let counter = 0;
const nextId = () => `m${Date.now().toString(36)}-${counter++}`;

export function useChat(documentId: string | null) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  // A new document means a fresh conversation.
  useEffect(() => {
    setMessages([]);
  }, [documentId]);

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || !documentId || busy) return;

      const history: ChatTurn[] = messagesRef.current
        .filter((m) => !m.pending && !m.error)
        .map(({ role, content }) => ({ role, content }));

      const pendingId = nextId();
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "user", content: text },
        { id: pendingId, role: "assistant", content: "", pending: true },
      ]);
      setBusy(true);
      try {
        const res = await askQuestion(documentId, text, history);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId
              ? { ...m, content: res.answer, citations: res.citations, grounded: res.grounded, pending: false }
              : m,
          ),
        );
      } catch (e: unknown) {
        const detail = e instanceof ApiError ? e.message : "Something went wrong.";
        setMessages((prev) =>
          prev.map((m) => (m.id === pendingId ? { ...m, content: detail, error: true, pending: false } : m)),
        );
      } finally {
        setBusy(false);
      }
    },
    [documentId, busy],
  );

  const clear = useCallback(() => setMessages([]), []);

  return { messages, send, busy, clear };
}
