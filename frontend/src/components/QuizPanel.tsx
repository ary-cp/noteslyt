/**
 * Left pane, quiz mode.
 *
 *   ┌ Learner model ─────────────┐   one MasteryBar per topic; click = quiz that topic
 *   ├ Question card ─────────────┤   4 options → immediate feedback + explanation
 *   └ "See Page N" chip ─────────┘   jumps the PDF to the slide the question came from
 *
 * No red/green: a correct option gets the accent, a wrong pick a dark outline.
 */

import { Check, RefreshCw, Sparkles, X } from "lucide-react";
import type { useQuiz } from "../hooks/useQuiz";
import type { DocumentInfo } from "../lib/types";
import { MasteryBar } from "./MasteryBar";

type QuizState = ReturnType<typeof useQuiz>;

interface Props {
  doc: DocumentInfo;
  quiz: QuizState;
  onCite: (page: number) => void;
}

const LETTERS = ["A", "B", "C", "D"];

export function QuizPanel({ doc, quiz, onCite }: Props) {
  const { mastery, current, currentTopic, index, result, selected, finished, loading, grading, error, start, answer, next, reset, startMastery } = quiz;
  const activeTopicId = quiz.quiz?.topic.topic_id;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      {/* Learner model ------------------------------------------------------ */}
      <section className="border-b border-line px-5 py-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-muted">Learner model</h2>
          <span className="text-[11px] text-ink-muted">Bayesian Knowledge Tracing</span>
        </div>
        <div className="mt-2 flex flex-col gap-0.5">
          {(mastery.length ? mastery : doc.topics.map((t) => ({ topic_id: t.id, name: t.name, description: t.description, p_mastery: 0.3, attempts: 0, correct: 0, label: "weak" as const }))).map((t) => (
            <MasteryBar key={t.topic_id} topic={t} active={t.topic_id === activeTopicId} onClick={loading || grading ? undefined : () => start(t.topic_id)} />
          ))}
        </div>
      </section>

      {/* Quiz --------------------------------------------------------------- */}
      <section className="flex flex-1 flex-col px-5 py-5">
        {error && <p className="mb-4 rounded-lg border border-line bg-surface-muted px-3 py-2 text-sm text-ink-muted">{error}</p>}

        {!quiz.quiz && !loading && (
          <div className="m-auto flex max-w-sm flex-col items-center text-center">
            <Sparkles size={22} strokeWidth={1.5} className="text-accent" />
            <h3 className="mt-3 text-base font-semibold tracking-tight">Adaptive quiz</h3>
            <p className="mt-1 text-sm text-ink-muted">
              Questions are written from the slides for your weakest topic. Each answer updates the model above.
            </p>
            <button
              type="button"
              onClick={() => start()}
              className="mt-5 rounded-full bg-ink px-5 py-2 text-sm font-medium text-surface hover:opacity-90"
            >
              Start adaptive quiz
            </button>
            <p className="mt-2 text-[11px] text-ink-muted">or click a topic above to target it</p>
          </div>
        )}

        {loading && (
          <div className="m-auto flex items-center gap-2 text-sm text-ink-muted">
            <RefreshCw size={14} className="animate-spin" /> Writing questions from your slides…
          </div>
        )}

        {quiz.quiz && current && currentTopic && !finished && !loading && (
          <div className="mx-auto w-full max-w-2xl">
            <div className="flex items-center justify-between text-[11px] uppercase tracking-wide text-ink-muted">
              <span className="whitespace-nowrap">
                Question {index + 1} of {quiz.quiz.questions.length}
              </span>
              <span className="truncate pl-3">Targeting · {currentTopic.name}</span>
            </div>

            <p className="mt-3 text-[15px] font-medium leading-7">{current.question}</p>

            <div className="mt-4 flex flex-col gap-2">
              {current.options.map((opt, i) => {
                const picked = result !== null && i === selected;
                const isCorrect = result !== null && i === result.correct_index;
                const base = "flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left text-sm leading-6 transition-colors";
                const tone = result
                  ? isCorrect
                    ? "border-accent bg-accent-soft"
                    : picked
                      ? "border-ink/60 bg-surface"
                      : "border-line opacity-60"
                  : "border-line hover:bg-surface-muted";
                return (
                  <button key={i} type="button" disabled={result !== null || grading} onClick={() => answer(i)} className={`${base} ${tone}`}>
                    <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line text-[11px] font-medium text-ink-muted">
                      {LETTERS[i]}
                    </span>
                    <span className="flex-1">{opt}</span>
                    {result && isCorrect && <Check size={16} className="mt-1 shrink-0 text-accent" />}
                    {result && picked && !isCorrect && <X size={16} className="mt-1 shrink-0 text-ink-muted" />}
                  </button>
                );
              })}
            </div>

            {result && (
              <div className="mt-4 rounded-xl border border-line bg-surface-muted px-4 py-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{result.correct ? "Correct" : "Not quite"}</span>
                  <span className="text-[11px] tabular-nums text-ink-muted">
                    mastery {Math.round(result.p_before * 100)}% → {Math.round(result.p_after * 100)}%
                  </span>
                </div>
                {result.explanation && <p className="mt-1.5 text-sm leading-6 text-ink">{result.explanation}</p>}
                <div className="mt-2 flex items-center justify-between">
                  <button type="button" className="cite" onClick={() => onCite(result.page)}>
                    See Page {result.page}
                  </button>
                  <button type="button" onClick={next} className="rounded-full bg-ink px-4 py-1.5 text-sm font-medium text-surface hover:opacity-90">
                    {index + 1 < quiz.quiz.questions.length ? "Next question" : "Finish"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {finished && currentTopic && (
          <div className="m-auto flex max-w-sm flex-col items-center text-center">
            <h3 className="text-base font-semibold tracking-tight">Set complete</h3>
            <p className="mt-1 text-sm text-ink-muted">
              {currentTopic.name}: mastery {Math.round((startMastery ?? 0) * 100)}% → {Math.round(currentTopic.p_mastery * 100)}%
            </p>
            <div className="mt-5 flex gap-2">
              <button type="button" onClick={() => start()} className="rounded-full bg-ink px-5 py-2 text-sm font-medium text-surface hover:opacity-90">
                Next adaptive set
              </button>
              <button type="button" onClick={reset} className="rounded-full border border-line px-5 py-2 text-sm font-medium hover:bg-surface-muted">
                Done
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
