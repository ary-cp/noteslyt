/**
 * Adaptive quiz state machine:
 *
 *   idle ──start()──▶ loading ──▶ question[i] ──answer()──▶ feedback ──next()──▶ question[i+1] … ──▶ summary
 *
 * `mastery` is the live learner model (one P(mastery) per topic) and is
 * refreshed from every server response, so the bars move the moment an answer
 * is graded.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, answerQuestion, generateQuiz, getLearner } from "../lib/api";
import type { AnswerResponse, QuizResponse, TopicMastery } from "../lib/types";

export function useQuiz(userId: string, documentId: string | null) {
  const [mastery, setMastery] = useState<TopicMastery[]>([]);
  const [quiz, setQuiz] = useState<QuizResponse | null>(null);
  const [index, setIndex] = useState(0);
  const [result, setResult] = useState<AnswerResponse | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [finished, setFinished] = useState(false);
  const [loading, setLoading] = useState(false);
  const [grading, setGrading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startMastery, setStartMastery] = useState<number | null>(null);

  // Load (or reset) the learner model whenever the document changes.
  useEffect(() => {
    setQuiz(null);
    setIndex(0);
    setResult(null);
    setSelected(null);
    setFinished(false);
    setError(null);
    if (!documentId) return;
    getLearner(userId, documentId)
      .then((r) => setMastery(r.mastery))
      .catch(() => setMastery([]));
  }, [userId, documentId]);

  const start = useCallback(
    async (topicId?: string) => {
      if (!documentId) return;
      setLoading(true);
      setError(null);
      setFinished(false);
      setResult(null);
      setSelected(null);
      try {
        const res = await generateQuiz(userId, documentId, topicId);
        setQuiz(res);
        setIndex(0);
        setMastery(res.mastery);
        setStartMastery(res.topic.p_mastery);
      } catch (e: unknown) {
        setError(e instanceof ApiError ? e.message : "Could not generate a quiz.");
      } finally {
        setLoading(false);
      }
    },
    [userId, documentId],
  );

  const answer = useCallback(
    async (selectedIndex: number) => {
      if (!quiz || result || grading) return;
      setGrading(true);
      setSelected(selectedIndex);
      try {
        const res = await answerQuestion(userId, quiz.quiz_id, quiz.questions[index].id, selectedIndex);
        setResult(res);
        setMastery(res.mastery);
      } catch (e: unknown) {
        setError(e instanceof ApiError ? e.message : "Could not grade the answer.");
      } finally {
        setGrading(false);
      }
    },
    [quiz, index, result, grading, userId],
  );

  const next = useCallback(() => {
    if (!quiz) return;
    if (index + 1 < quiz.questions.length) {
      setIndex((i) => i + 1);
      setResult(null);
      setSelected(null);
    } else {
      setFinished(true);
    }
  }, [quiz, index]);

  const reset = useCallback(() => {
    setQuiz(null);
    setIndex(0);
    setResult(null);
    setSelected(null);
    setFinished(false);
  }, []);

  const current = quiz ? quiz.questions[index] : null;
  const currentTopic = quiz ? (mastery.find((t) => t.topic_id === quiz.topic.topic_id) ?? quiz.topic) : null;

  return {
    mastery,
    quiz,
    index,
    current,
    currentTopic,
    startMastery,
    result,
    selected,
    finished,
    loading,
    grading,
    error,
    start,
    answer,
    next,
    reset,
  };
}
