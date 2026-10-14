import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  getQuizAnswers,
  listStudioJobs,
  submitAnswer,
} from "../../lib/api";
import type { AnswerOut, QuizOut } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { MarkdownBody } from "../chat/ChatPanel";
import { QuizReport } from "./StudioPanel";

const EXAM_SECONDS_PER_QUESTION = 120;
const EXAM_TAB_PENALTY_S = 300;

type Picks = Record<string, number | string>;

interface ExamDraft {
  picks: Picks;
  deadline: number;
  violations: number;
}

const draftKey = (quizId: string) => `noteslyt:exam:${quizId}`;

/**
 * Full-page mock exam with deferred grading.
 *
 * Nothing is POSTed until the learner submits: picks live in local state (and
 * a localStorage draft), so BKT mastery / the learning profile cannot move
 * mid-exam and leak correctness. Answers stay changeable until submit; scores,
 * feedback and the assessment report unlock only afterwards.
 */
export default function ExamPage() {
  const { projectId, quizId } = useParams();
  const location = useLocation() as { state?: { quiz?: QuizOut } };
  const { push } = useToast();

  const [quiz, setQuiz] = useState<QuizOut | null>(location.state?.quiz ?? null);
  const [loadError, setLoadError] = useState("");

  const [started, setStarted] = useState(false);
  const [picks, setPicks] = useState<Picks>({});
  const [deadline, setDeadline] = useState(0);
  const [violations, setViolations] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const [i, setI] = useState(0);
  const [flagged, setFlagged] = useState<Set<string>>(new Set());
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [submittingAll, setSubmittingAll] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [done, setDone] = useState(false);
  const [answers, setAnswers] = useState<Record<string, AnswerOut>>({});

  // Load the paper: router state first, else the persisted Studio job payload.
  useEffect(() => {
    if (quiz || !projectId || !quizId) return;
    let live = true;
    listStudioJobs(projectId)
      .then((jobs) => {
        if (!live) return;
        const job = jobs.find(
          (j) => j.kind === "exam" && (j.payload as QuizOut | null)?.quiz_id === quizId,
        );
        const payload = job?.payload as QuizOut | undefined;
        if (payload && payload.questions) setQuiz(payload);
        else setLoadError("Exam paper not found. Regenerate it from the project's Studio.");
      })
      .catch((e) => {
        if (live) setLoadError(e instanceof Error ? e.message : "Could not load the exam paper");
      });
    return () => {
      live = false;
    };
  }, [quiz, projectId, quizId]);

  // Restore an in-progress draft (refresh-safe).
  useEffect(() => {
    if (!quizId) return;
    try {
      const raw = localStorage.getItem(draftKey(quizId));
      if (!raw) return;
      const draft = JSON.parse(raw) as ExamDraft;
      if (draft && typeof draft === "object") {
        if (draft.picks && typeof draft.picks === "object") setPicks(draft.picks);
        if (typeof draft.deadline === "number" && draft.deadline > Date.now()) {
          setDeadline(draft.deadline);
          setStarted(true);
        }
        if (typeof draft.violations === "number") setViolations(draft.violations);
      }
    } catch {
      /* corrupted draft — start fresh */
    }
  }, [quizId]);

  // Persist the draft while the exam is live.
  useEffect(() => {
    if (!quizId || !started || done) return;
    try {
      const draft: ExamDraft = { picks, deadline, violations };
      localStorage.setItem(draftKey(quizId), JSON.stringify(draft));
    } catch {
      /* private mode — in-memory exam still works for this session */
    }
  }, [quizId, started, done, picks, deadline, violations]);

  const clearDraft = useCallback(() => {
    if (!quizId) return;
    try {
      localStorage.removeItem(draftKey(quizId));
    } catch {
      /* ignore */
    }
  }, [quizId]);

  // Clock.
  useEffect(() => {
    if (!started || done) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [started, done]);

  const left = useMemo(
    () => (started && !done ? Math.max(0, Math.ceil((deadline - now) / 1000)) : 0),
    [started, done, deadline, now],
  );

  // Time-up auto-submits.
  useEffect(() => {
    if (started && !done && deadline > 0 && now >= deadline) {
      void finish(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [started, done, deadline, now]);

  // Leaving the tab costs 5 minutes. Nothing graded yet, so this only burns time.
  useEffect(() => {
    if (!started || done) return;
    const onVis = () => {
      if (document.hidden) {
        setDeadline((d) => d - EXAM_TAB_PENALTY_S * 1000);
        setViolations((v) => v + 1);
        push("Tab switch detected — 5:00 deducted", {
          detail: "Stay on this tab until you submit the exam.",
          tone: "warning",
        });
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [started, done, push]);

  // Reload/close guard while the paper is live.
  useEffect(() => {
    if (!started || done) return;
    const guard = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [started, done]);

  const start = () => {
    if (!quiz) return;
    setDeadline(Date.now() + quiz.questions.length * EXAM_SECONDS_PER_QUESTION * 1000);
    setNow(Date.now());
    setStarted(true);
  };

  const finish = useCallback(
    async (auto = false) => {
      if (!quiz || submittingAll || done) return;
      setConfirmEnd(false);
      setSubmittingAll(true);
      setSubmitError("");
      try {
        // Grade every answered question; unanswered ones stay skipped.
        for (const q of quiz.questions) {
          const given = picks[q.id];
          if (given === undefined || given === "") continue;
          try {
            const res = await submitAnswer(quiz.quiz_id, q.id, given);
            setAnswers((prev) => ({ ...prev, [q.id]: res }));
          } catch (e) {
            setSubmitError(
              `One answer could not be graded (${q.stem.slice(0, 60)}…): ${e instanceof Error ? e.message : String(e)}`,
            );
          }
        }
        // Re-read the server truth (includes the assessment id for the report).
        try {
          const saved = await getQuizAnswers(quiz.quiz_id);
          setAnswers(saved);
        } catch {
          /* keep what submit returned */
        }
        setDone(true);
        clearDraft();
        if (auto) push("Time up — exam submitted", { tone: "info" });
      } finally {
        setSubmittingAll(false);
      }
    },
    [quiz, picks, submittingAll, done, clearDraft, push],
  );

  if (!projectId || !quizId) {
    return (
      <main className="mx-auto w-full max-w-3xl px-5 py-8">
        <p className="text-sm text-[var(--danger)]">Missing exam reference.</p>
        <Link to="/" className="mt-2 inline-block text-sm text-[var(--accent)] hover:underline">
          ← Dashboard
        </Link>
      </main>
    );
  }
  if (loadError || (!quiz && !loadError)) {
    if (!quiz && !loadError) {
      return (
        <main className="mx-auto w-full max-w-3xl px-5 py-8">
          <p className="text-sm text-[var(--text-muted)]">Loading exam paper…</p>
        </main>
      );
    }
    return (
      <main className="mx-auto w-full max-w-3xl px-5 py-8">
        <p role="alert" className="text-sm text-[var(--danger)]">{loadError}</p>
        <Link to={`/project/${projectId}`} className="mt-2 inline-block text-sm text-[var(--accent)] hover:underline">
          ← Back to project
        </Link>
      </main>
    );
  }

  const questions = quiz?.questions ?? [];
  const q = questions[i];
  const answeredCount = questions.filter(
    (qq) => picks[qq.id] !== undefined && picks[qq.id] !== "",
  ).length;

  // ---- rules gate ----
  if (quiz && !started) {
    const totalMin = Math.round((questions.length * EXAM_SECONDS_PER_QUESTION) / 60);
    return (
      <main className="mx-auto w-full max-w-3xl px-5 py-8">
        <Link to={`/project/${projectId}`} className="text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)]">
          ← Back to project
        </Link>
        <h1 className="mt-2 text-xl font-semibold">Mock exam · {questions.length} questions</h1>
        <div className="mt-4 rounded-[8px] border border-[var(--accent-border)] bg-[var(--accent-soft)]/40 p-4">
          <p className="text-[15px] font-semibold">Exam conditions — {totalMin} minutes once you start</p>
          <ul className="mt-2 grid gap-1.5 text-[13px] text-[var(--text-secondary)]">
            <li>· Nothing is graded until you submit, so your learning profile cannot move mid-exam.</li>
            <li>· Change any answer freely until you submit.</li>
            <li>· Copying and right-click are disabled on the paper.</li>
            <li>· Switching tabs costs <span className="font-semibold text-[var(--text-primary)]">5:00</span> each time.</li>
            <li>· Scores and feedback unlock <span className="font-semibold text-[var(--text-primary)]">only after you submit</span>.</li>
          </ul>
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="primary" onClick={start}>Start exam</Button>
        </div>
      </main>
    );
  }

  // ---- results ----
  if (quiz && done) {
    const correct = Object.values(answers).filter((a) => a.correct).length;
    const pct = questions.length ? Math.round((correct / questions.length) * 100) : 0;
    const assessmentId =
      Object.values(answers).find((a) => a.assessment_id)?.assessment_id ?? null;
    return (
      <main className="mx-auto w-full max-w-3xl px-5 py-8">
        <Link to={`/project/${projectId}`} className="text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)]">
          ← Back to project
        </Link>
        <div className="mt-2 text-center">
          <p className="text-[13px] text-[var(--text-muted)]">
            Exam submitted{violations > 0 ? ` · ${violations} tab-switch penalt${violations > 1 ? "ies" : "y"}` : ""}
          </p>
          <p className="mt-1 text-[32px] font-semibold">{correct} / {questions.length}</p>
          <p className="text-[15px] text-[var(--text-secondary)]">{pct}%</p>
          {submitError && (
            <p role="alert" className="mx-auto mt-2 max-w-xl text-[13px] text-[var(--danger)]">{submitError}</p>
          )}
          {assessmentId && <QuizReport assessmentId={assessmentId} />}
          <div className="mt-4 grid gap-1.5 text-left">
            {questions.map((qq, idx) => {
              const a = answers[qq.id];
              return (
                <div key={qq.id} className="rounded-[7px] border border-[var(--border)] px-2.5 py-2 text-[13px]">
                  <div className="flex items-center gap-2">
                    <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${a?.correct ? "bg-[var(--accent)] text-white" : a ? "bg-[var(--danger)] text-white" : "bg-[var(--surface-secondary)] text-[var(--text-muted)]"}`}>
                      {idx + 1}
                    </span>
                    <div className="min-w-0 flex-1"><MarkdownBody text={qq.stem} /></div>
                    <span className="shrink-0 text-xs text-[var(--text-muted)]">
                      {a ? (a.correct ? "Correct" : "Review") : "Skipped"}
                    </span>
                  </div>
                  {a && (
                    <div className="mt-2 border-t border-[var(--border)] pt-2">
                      {!a.correct && (
                        <MarkdownBody text={`**Expected answer:** ${a.expected ?? "See explanation"}`} />
                      )}
                      <MarkdownBody
                        text={[a.feedback, a.feedback.includes(a.explanation) ? "" : a.explanation]
                          .filter(Boolean)
                          .join("\n\n")}
                      />
                      <p className="mt-1 text-xs text-[var(--text-muted)]">
                        {a.citation.source} · {a.citation.label}
                      </p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </main>
    );
  }

  if (!quiz || !q) {
    return (
      <main className="mx-auto w-full max-w-3xl px-5 py-8">
        <p className="text-sm text-[var(--text-muted)]">No questions in this paper.</p>
        <Link to={`/project/${projectId}`} className="mt-2 inline-block text-sm text-[var(--accent)] hover:underline">
          ← Back to project
        </Link>
      </main>
    );
  }

  const fmtLeft = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  const go = (idx: number) => setI(Math.min(questions.length - 1, Math.max(0, idx)));

  return (
    <main className="mx-auto w-full max-w-3xl px-5 py-8">
      <div
        onCopy={(e) => e.preventDefault()}
        onCut={(e) => e.preventDefault()}
        onContextMenu={(e) => e.preventDefault()}
        className="select-none"
      >
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-4 py-3">
          <span className="text-[13px] text-[var(--text-secondary)]">
            Question {i + 1} of {questions.length} · {answeredCount} answered
          </span>
          <span
            aria-live="polite"
            title="Time remaining"
            className={`font-mono text-2xl font-bold tabular-nums ${left < 300 ? "text-[var(--danger)]" : "text-[var(--accent)]"}`}
          >
            {fmtLeft}
          </span>
          <span className="flex items-center gap-2">
            {violations > 0 && (
              <span title="Tab-switch penalties" className="font-mono text-xs text-[var(--warning)]">
                −5:00 × {violations}
              </span>
            )}
            <button
              type="button"
              onClick={() =>
                setFlagged((prev) => {
                  const n = new Set(prev);
                  if (n.has(q.id)) n.delete(q.id);
                  else n.add(q.id);
                  return n;
                })
              }
              className={`cursor-pointer rounded px-2 py-0.5 text-xs ${flagged.has(q.id) ? "bg-[var(--warning-soft)] text-[var(--warning)]" : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"}`}
            >
              {flagged.has(q.id) ? "Flagged" : "Flag"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmEnd(true)}
              className="cursor-pointer rounded border border-[var(--border-strong)] px-2 py-0.5 text-xs font-medium hover:bg-[var(--surface-secondary)]"
            >
              End exam
            </button>
          </span>
        </div>

        <MarkdownBody text={q.stem} />

        {q.qtype === "mcq" ? (
          <div className="mt-3 grid gap-2">
            {q.options.map((opt, idx) => {
              const chosen = picks[q.id] === idx;
              return (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setPicks((prev) => ({ ...prev, [q.id]: idx }))}
                  aria-pressed={chosen}
                  className={`flex cursor-pointer items-center gap-2.5 rounded-[8px] border px-3 py-2.5 text-left text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
                    chosen
                      ? "border-[var(--accent)] bg-[var(--accent-soft)] font-medium shadow-[0_0_0_1px_var(--accent)]"
                      : "border-[var(--border)] hover:border-[var(--border-strong)]"
                  }`}
                >
                  <span
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] border font-mono text-xs ${
                      chosen ? "border-[var(--accent)] bg-[var(--accent)] text-white" : "border-[var(--border)]"
                    }`}
                  >
                    {String.fromCharCode(65 + idx)}
                  </span>
                  <span className="min-w-0 flex-1"><MarkdownBody text={opt} /></span>
                  {chosen && <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-[var(--accent)]" />}
                </button>
              );
            })}
          </div>
        ) : (
          <textarea
            value={typeof picks[q.id] === "string" ? (picks[q.id] as string) : ""}
            onChange={(e) => setPicks((prev) => ({ ...prev, [q.id]: e.target.value }))}
            rows={3}
            placeholder="Type your answer… (editable until you submit)"
            className="mt-3 w-full rounded-[8px] border border-[var(--border)] bg-[var(--input)] p-2.5 text-sm outline-none focus:border-[var(--accent)]"
          />
        )}

        <div className="mt-4 flex items-center justify-between">
          <Button variant="ghost" onClick={() => go(i - 1)} disabled={i === 0}>
            Back
          </Button>
          {i + 1 < questions.length ? (
            <Button variant="primary" onClick={() => go(i + 1)}>Next</Button>
          ) : (
            <Button variant="primary" onClick={() => setConfirmEnd(true)}>Submit exam</Button>
          )}
        </div>

        {questions.length > 6 && (
          <div className="mt-4 flex flex-wrap gap-1">
            {questions.map((qq, idx) => {
              const has = picks[qq.id] !== undefined && picks[qq.id] !== "";
              return (
                <button
                  key={qq.id}
                  type="button"
                  onClick={() => go(idx)}
                  aria-label={`Go to question ${idx + 1}${has ? " (answered)" : ""}`}
                  className={`h-7 w-7 cursor-pointer rounded-[6px] border font-mono text-xs ${idx === i ? "border-[var(--accent)]" : "border-[var(--border)]"} ${has ? "bg-[var(--accent-soft)]" : ""} ${flagged.has(qq.id) ? "ring-1 ring-[var(--warning)]" : ""}`}
                >
                  {idx + 1}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <Modal
        open={confirmEnd}
        onClose={() => setConfirmEnd(false)}
        title="Submit exam?"
        width={420}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmEnd(false)}>Keep writing</Button>
            <Button variant="primary" loading={submittingAll} onClick={() => void finish()}>
              Submit ({answeredCount}/{questions.length} answered)
            </Button>
          </>
        }
      >
        <p className="text-sm text-[var(--text-secondary)]">
          Unanswered questions count as skipped. Scores and feedback unlock after you submit.
        </p>
      </Modal>
    </main>
  );
}
