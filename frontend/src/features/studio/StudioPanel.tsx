import type * as React from "react";
import { useEffect, useMemo, useState } from "react";
import {
  Brain,
  ClipboardList,
  FileText,
  GraduationCap,
  Layers,
  Loader2,
  Map,
  // Mic,
  // Presentation,
  Sparkles,
  // TableProperties,
} from "lucide-react";
import { createExam, createFlashcards, createQuiz, fetchLearner, listStudioJobs, listTopics, saveStudioArtifact, submitAnswer } from "../../lib/api";
import type { LearnerOut, QuizOut, SourceOut, StudioJob, TopicSummary } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/Feedback";
import { Modal } from "../../components/ui/Modal";
import { Dropdown } from "../../components/ui/Menu";
import { Field, RadioGroup, Slider } from "../../components/ui/Controls";
import { MarkdownBody } from "../chat/ChatPanel";
import { timeAgo } from "../../lib/utils";

export type StudioTool = "summary" | "flashcards" | "quiz" | "exam" | "guide" | "mindmap" | "podcast" | "slides" | "sheet";

const TOOLS: { id: StudioTool; title: string; desc: string; icon: React.ReactNode; ready: boolean }[] = [
  { id: "summary", title: "Summary", desc: "Key points from your sources", icon: <FileText size={16} />, ready: true },
  { id: "quiz", title: "Quiz", desc: "Test yourself with feedback", icon: <Brain size={16} />, ready: true },
  { id: "flashcards", title: "Flashcards", desc: "Review key concepts", icon: <Layers size={16} />, ready: true },
  { id: "exam", title: "Mock exam", desc: "Timed, exam-style practice", icon: <GraduationCap size={16} />, ready: true },
  { id: "guide", title: "Study guide", desc: "Structured revision document", icon: <ClipboardList size={16} />, ready: true },
  { id: "mindmap", title: "Mind map", desc: "See how ideas connect", icon: <Map size={16} />, ready: true },
  // { id: "podcast", title: "Audio overview", desc: "Listen on the go", icon: <Mic size={16} />, ready: false },
  // { id: "slides", title: "Presentation", desc: "Slides from sources", icon: <Presentation size={16} />, ready: false },
  // { id: "sheet", title: "Cheat sheet", desc: "One-page revision", icon: <TableProperties size={16} />, ready: false },
];

interface Artifact {
  id: string;
  kind: StudioTool;
  title: string;
  createdAt: string;
  payload: QuizOut | { cards: { front: string; back: string }[] } | { markdown: string; toc: string[] } | { mermaid: string };
}

function jobArtifact(job: StudioJob): Artifact {
  return { id: job.id, kind: job.kind as StudioTool, title: job.title, createdAt: job.created_at,
    payload: (job.payload ?? {}) as Artifact["payload"] };
}

export function StudioPanel({
  projectId,
  sources,
  selectedSources,
}: {
  projectId: string;
  sources: SourceOut[];
  selectedSources: SourceOut[];
}) {
  const { push } = useToast();
  const [topics, setTopics] = useState<TopicSummary[]>([]);
  const [learner, setLearner] = useState<LearnerOut | null>(null);
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const [genTool, setGenTool] = useState<StudioTool | null>(null);
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState(10);
  const [difficulty, setDifficulty] = useState<"easy" | "medium" | "hard">("medium");
  const [topicId, setTopicId] = useState("");
  const [focus, setFocus] = useState("");
  const [viewing, setViewing] = useState<Artifact | null>(null);

  useEffect(() => {
    let active = true;
    setJobs([]);
    listStudioJobs(projectId).then((rows) => { if (active) setJobs(rows); }).catch(() => undefined);
    listTopics(projectId).then(setTopics).catch(() => undefined);
    fetchLearner(projectId).then(setLearner).catch(() => undefined);
    return () => { active = false; };
  }, [projectId]);

  const hasPendingJobs = jobs.some((j) => j.status === "queued" || j.status === "generating");
  useEffect(() => {
    if (!hasPendingJobs) return;
    let active = true;
    const waitForCompletion = async () => {
      while (active) {
        try {
          const rows = await listStudioJobs(projectId, 25);
          if (!active) return;
          setJobs(rows);
          if (!rows.some((job) => job.status === "queued" || job.status === "generating")) return;
        } catch {
          await new Promise((resolve) => window.setTimeout(resolve, 5000));
        }
      }
    };
    void waitForCompletion();
    return () => { active = false; };
  }, [projectId, hasPendingJobs]);

  const persist = async (art: Artifact) => {
    const saved = await saveStudioArtifact(projectId, { kind: art.kind, title: art.title,
      payload: art.payload as Record<string, unknown> });
    setJobs((prev) => [saved, ...prev.filter((j) => j.id !== saved.id)]);
    return saved;
  };

  const readySources = useMemo(() => sources.filter((s) => s.status === "ready"), [sources]);
  const selectedIdSet = useMemo(() => new Set(selectedSources.map((source) => source.id)), [selectedSources]);
  const scopedTopics = useMemo(
    () => topics.filter((topic) => topic.sources.some((source) => selectedIdSet.has(source.source_id))),
    [topics, selectedIdSet],
  );
  useEffect(() => {
    if (topicId && !scopedTopics.some((topic) => topic.id === topicId)) setTopicId("");
  }, [topicId, scopedTopics]);
  const scopeLabel = selectedSources.length > 0
    ? `${selectedSources.length} selected source${selectedSources.length > 1 ? "s" : ""}`
    : "no sources selected";

  const generate = async () => {
    if (!genTool || busy) return;
    if (readySources.length === 0) {
      push("Add a source first", { tone: "warning" });
      return;
    }
    if (selectedSources.length === 0) {
      push("Select a source", { detail: "Choose at least one ready source in the Sources panel.", tone: "warning" });
      return;
    }
    setBusy(true);
    try {
      if (genTool === "quiz" || genTool === "exam") {
        const n = genTool === "exam" ? Math.min(count, 30) : Math.min(count, 10);
        const create = genTool === "exam" ? createExam : createQuiz;
        const job = await create(projectId, {
          topic_id: topicId || undefined,
          n_questions: n,
          qtypes: ["mcq", "short_answer"],
          source_ids: selectedSources.map((source) => source.id),
        });
        setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
        push("Generation started", { detail: "You can keep using the project while it runs.", tone: "success" });
      } else if (genTool === "flashcards") {
        const job = await createFlashcards(projectId, {
          topic_id: topicId || undefined,
          n_cards: Math.min(count, 20),
          source_ids: selectedSources.map((source) => source.id),
        });
        setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
        push("Flashcard generation started", { tone: "success" });
      } else if (genTool === "summary" || genTool === "guide" || genTool === "sheet") {
        const names = selectedSources.map((s) => s.filename).join(", ");
        const scopedTopicIds = new Set(scopedTopics.map((topic) => topic.id));
        const weak = (learner?.weakest ?? []).filter((topic) => scopedTopicIds.has(topic.topic_id));
        const md = [
          `# ${genTool === "guide" ? "Study guide" : genTool === "sheet" ? "Cheat sheet" : "Summary"}`,
          ``,
          `_From ${scopeLabel} · ${difficulty}${focus ? ` · focus: ${focus}` : ""}_`,
          ``,
          `## What this covers`,
          ``,
          `Sources: ${names || "—"}.`,
          scopedTopics.length ? `Topics detected: ${scopedTopics.map((t) => t.name).join(", ")}.` : `Topics are extracted during indexing.`,
          ``,
          `## Key points`,
          ``,
          ...(scopedTopics.slice(0, 8).map((t, i) => `${i + 1}. **${t.name}** — ${t.description || `covered in ${t.sources.filter((s) => selectedIdSet.has(s.source_id)).map((s) => s.filename).join(", ") || "your sources"}`}`)),
          ...(scopedTopics.length === 0 ? [`1. Upload completes → key points appear here, each linked to its source.`] : []),
          ``,
          ...(weak.length ? [`## Topics to review`, ``, ...weak.slice(0, 5).map((w) => `- **${w.name}** — ${Math.round(w.p_mastery * 100)}% mastery${w.attempts ? ` (${w.attempts} attempts)` : " (not practiced yet)"}`), ``, `Generate a focused quiz to practice one of these topics.`, ``] : []),
          `## Next step`,
          ``,
          `Generate a quiz from the same scope to check recall, or ask the chat to explain any point above.`,
        ].join("\n");
        const art: Artifact = {
          id: `doc-${Date.now()}`,
          kind: genTool,
          title: `${genTool === "guide" ? "Study guide" : genTool === "sheet" ? "Cheat sheet" : "Summary"} · ${new Date().toLocaleDateString()}`,
          createdAt: new Date().toISOString(),
          payload: { markdown: md, toc: ["What this covers", "Key points", "Next step"] },
        };
        await persist(art);
        setViewing(art);
        push("Document ready", { tone: "success" });
      } else if (genTool === "mindmap") {
        const nodes = scopedTopics.slice(0, 10);
        const edges = nodes.slice(1).map((t, i) => `  main --> ${sanitize(nodes[i % Math.max(1, nodes.length - 1)]?.name ?? "Topic")}_${i}[${t.name.slice(0, 24)}]`);
        const mermaid = ["graph TD", `  main[${((selectedSources[0]?.filename ?? "Project").slice(0, 28))}]`, ...edges].join("\n");
        const art: Artifact = {
          id: `mm-${Date.now()}`,
          kind: "mindmap",
          title: `Mind map · ${new Date().toLocaleDateString()}`,
          createdAt: new Date().toISOString(),
          payload: { mermaid },
        };
        await persist(art);
        setViewing(art);
        push("Mind map ready", { tone: "success" });
      }
    } catch (e) {
      push("Generation failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
      setGenTool(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-[var(--border)] px-3.5 pt-3 pb-2.5">
        <h2 className="text-[13px] font-semibold tracking-wide">Studio</h2>
        <p className="mt-0.5 text-xs text-[var(--text-secondary)]">Turn your sources into learning materials.</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <p className="mb-2 text-[11px] font-medium tracking-wide text-[var(--text-muted)] uppercase">Create from {scopeLabel}</p>
        <div className="grid grid-cols-2 gap-2">
          {TOOLS.map((t) => (
            <button
              key={t.id}
              type="button"
              disabled={!t.ready}
              onClick={() => setGenTool(t.id)}
              title={t.ready ? t.desc : "Coming soon"}
              className={`flex cursor-pointer flex-col gap-1 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] p-3 text-left transition-colors duration-150 hover:border-[var(--accent-border)] disabled:cursor-not-allowed disabled:opacity-45 ${
                !t.ready ? "" : ""
              }`}
            >
              <span className="text-[var(--text-secondary)]">{t.icon}</span>
              <span className="text-[13px] font-medium">{t.title}</span>
              <span className="text-xs leading-snug text-[var(--text-muted)]">{t.ready ? t.desc : "Coming soon"}</span>
            </button>
          ))}
        </div>

        {learner && learner.weakest.length > 0 && (
          <div className="mt-4 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] p-3">
            <p className="text-xs font-medium text-[var(--text-muted)]">WEAKEST TOPICS</p>
            <div className="mt-1.5 grid gap-1.5">
              {learner.weakest.slice(0, 3).map((w) => (
                <div key={w.topic_id} className="flex min-w-0 items-center gap-2 text-[13px]">
                  <span className="min-w-0 flex-1 truncate">{w.name}</span>
                  {w.attempts > 0 ? <span className="shrink-0 font-mono text-xs text-[var(--text-muted)]">{Math.round(w.p_mastery * 100)}%</span> : <span className="shrink-0 text-[11px] text-[var(--text-muted)]">Not practiced</span>}
                  <span className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-[var(--border)]">
                    {w.attempts > 0 && <span className="block h-full max-w-full rounded-full bg-[var(--accent)]" style={{ width: `${Math.max(0, Math.min(100, Math.round(w.p_mastery * 100)))}%` }} />}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-4">
          <p className="mb-1.5 text-[11px] font-medium tracking-wide text-[var(--text-muted)] uppercase">Recent</p>
          {jobs.length === 0 ? (
            <EmptyState
              icon={<Sparkles size={18} />}
              title="No materials yet"
              body="Generate a summary, quiz or flashcards from your sources."
            />
          ) : (
            <div className="grid gap-1.5">
              {jobs.slice(0, 12).map((job) => (
                <button
                  key={job.id}
                  disabled={job.status !== "ready"}
                  onClick={() => job.status === "ready" && setViewing(jobArtifact(job))}
                  title={job.status === "failed" ? job.error : undefined}
                  className="flex items-center gap-2.5 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-2 text-left text-[13px] enabled:cursor-pointer enabled:hover:border-[var(--border-strong)] disabled:cursor-default"
                >
                  <span className="text-[var(--text-secondary)]">{job.status === "queued" || job.status === "generating" ? <Loader2 size={16} className="animate-spin" /> : TOOLS.find((t) => t.id === job.kind)?.icon}</span>
                  <span className="min-w-0 flex-1 truncate">{job.title}</span>
                  <span className={`shrink-0 text-[10px] ${job.status === "failed" ? "text-[var(--danger)]" : "text-[var(--text-muted)]"}`}>{job.status === "ready" ? timeAgo(job.created_at) : job.status}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* generation dialog */}
      <Modal
        open={genTool !== null}
        onClose={() => !busy && setGenTool(null)}
        title={genTool ? `Generate ${TOOLS.find((t) => t.id === genTool)?.title}` : ""}
        subtitle={`From ${scopeLabel}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setGenTool(null)} disabled={busy}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={selectedSources.length === 0} onClick={() => void generate()}>Generate</Button>
          </>
        }
      >
        <div className="grid gap-3.5">
          <Field label="Topic">
            <Dropdown
              value={topicId}
              onChange={setTopicId}
              ariaLabel="Topic"
              options={[{ value: "", label: "Mixed (weakest first)" }, ...scopedTopics.map((t) => ({ value: t.id, label: t.name }))]}
            />
          </Field>
          {(genTool === "quiz" || genTool === "exam" || genTool === "flashcards") && (
            <Field label={genTool === "flashcards" ? `Flashcards · ${count}` : `${genTool === "exam" ? "Questions" : "Quiz questions"} · ${count}`} hint={`Choose from 5 to ${genTool === "exam" ? 30 : genTool === "flashcards" ? 20 : 10}.`}>
              <Slider value={count} min={5} max={genTool === "exam" ? 30 : genTool === "flashcards" ? 20 : 10} onChange={setCount} ariaLabel={genTool === "exam" ? "Number of exam questions" : genTool === "flashcards" ? "Number of flashcards" : "Number of quiz questions"} />
              <div className="mt-1 flex justify-between font-mono text-[11px] text-[var(--text-muted)]"><span>5</span><span>{genTool === "exam" ? 30 : genTool === "flashcards" ? 20 : 10}</span></div>
            </Field>
          )}
          <Field label="Difficulty">
            <RadioGroup value={difficulty} onChange={setDifficulty} ariaLabel="Difficulty" options={[
              { value: "easy", label: "Easy" },
              { value: "medium", label: "Medium" },
              { value: "hard", label: "Hard" },
            ]} />
          </Field>
          <Field label="Focus (optional)" hint="Narrow the generation to part of your material.">
            <input
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="e.g. only chapter 4"
              className="h-9 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-sm outline-none focus:border-[var(--accent)]"
            />
          </Field>
          {busy && <p className="text-[13px] text-[var(--text-secondary)]">Generating… this calls your project corpus.</p>}
        </div>
      </Modal>

      <ArtifactViewer artifact={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

function sanitize(s: string) {
  return s.replace(/[^a-zA-Z0-9 ]/g, "").slice(0, 24) || "Topic";
}

/* ---------------- artifact viewer ---------------- */

function ArtifactViewer({ artifact, onClose }: { artifact: Artifact | null; onClose: () => void }) {
  if (!artifact) return null;
  return (
    <Modal open onClose={onClose} title={artifact.title} subtitle={`${timeAgo(artifact.createdAt)}`} width={720}>
      {artifact.kind === "quiz" || artifact.kind === "exam" ? (
        <QuizRunner quiz={artifact.payload as QuizOut} exam={artifact.kind === "exam"} />
      ) : artifact.kind === "flashcards" ? (
        <Flashcards cards={(artifact.payload as { cards: { front: string; back: string }[] }).cards} />
      ) : artifact.kind === "mindmap" ? (
        <MindMapView mermaid={(artifact.payload as { mermaid: string }).mermaid} />
      ) : (
        <DocView markdown={(artifact.payload as { markdown: string }).markdown} toc={(artifact.payload as { toc: string[] }).toc} />
      )}
    </Modal>
  );
}

function DocView({ markdown, toc }: { markdown: string; toc: string[] }) {
  const copy = () => {
    navigator.clipboard.writeText(markdown).catch(() => undefined);
  };
  return (
    <div className="grid gap-4 md:grid-cols-[160px_1fr]">
      <nav aria-label="Contents" className="hidden md:block">
        <p className="mb-1.5 text-[11px] font-medium tracking-wide text-[var(--text-muted)] uppercase">Contents</p>
        {toc.map((t) => (
          <p key={t} className="py-0.5 text-[13px] text-[var(--text-secondary)]">{t}</p>
        ))}
        <button type="button" onClick={copy} className="mt-3 cursor-pointer rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--surface-secondary)]">
          Copy
        </button>
      </nav>
      <MarkdownBody text={markdown} />
    </div>
  );
}

function MindMapView({ mermaid }: { mermaid: string }) {
  const [M, setM] = useState<React.ComponentType<{ code: string }> | null>(null);
  useEffect(() => {
    import("../chat/MermaidBlock").then((mod) => setM(() => mod.default));
  }, []);
  if (!M) return <p className="text-sm text-[var(--text-muted)]">Loading diagram…</p>;
  return <M code={mermaid} />;
}

/* ---------------- flashcards ---------------- */

function Flashcards({ cards }: { cards: { front: string; back: string }[] }) {
  const [i, setI] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [grades, setGrades] = useState<Record<number, string>>({});
  if (cards.length === 0) return <p className="text-sm text-[var(--text-muted)]">No cards in this set.</p>;
  const card = cards[Math.min(i, cards.length - 1)];

  const grade = (g: string) => {
    setGrades((prev) => ({ ...prev, [i]: g }));
    setFlipped(false);
    setI((v) => Math.min(v + 1, cards.length - 1));
  };

  return (
    <div>
      <p className="mb-2 text-center font-mono text-xs text-[var(--text-muted)]">{Math.min(i + 1, cards.length)} / {cards.length}</p>
      <div className="h-1 overflow-hidden rounded-full bg-[var(--border)]">
        <div className="h-full bg-[var(--accent)]" style={{ width: `${((i + 1) / cards.length) * 100}%` }} />
      </div>
      <button
        type="button"
        onClick={() => setFlipped((f) => !f)}
        onKeyDown={(e) => {
          if (e.key === " ") {
            e.preventDefault();
            setFlipped((f) => !f);
          }
        }}
        aria-label={flipped ? "Show question" : "Show answer"}
        className="mt-3 block min-h-[180px] w-full cursor-pointer [perspective:800px]"
      >
        <span className={`flip-inner ${flipped ? "flipped" : ""} relative block min-h-[180px]`}>
          <span className="flip-face absolute inset-0 flex items-center justify-center rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-6 text-center text-[15px]">
            {card.front}
          </span>
          <span className="flip-face flip-back absolute inset-0 flex items-center justify-center rounded-[10px] border border-[var(--accent-border)] bg-[var(--accent-soft)]/40 p-6 text-center text-[15px]">
            {card.back}
          </span>
        </span>
      </button>
      <p className="mt-2 text-center text-xs text-[var(--text-muted)]">Click or press Space to flip</p>
      <div className="mt-3 flex justify-center gap-2">
        {(["Again", "Hard", "Good", "Easy"] as const).map((g) => (
          <button
            key={g}
            onClick={() => grade(g)}
            className={`h-8 cursor-pointer rounded-[7px] border px-3 text-[13px] font-medium ${grades[i] === g ? "border-[var(--accent-border)] bg-[var(--accent-soft)]" : "border-[var(--border)] hover:bg-[var(--surface-secondary)]"}`}
          >
            {g}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------------- quiz / exam ---------------- */

function QuizRunner({ quiz, exam }: { quiz: QuizOut; exam: boolean }) {
  const { push } = useToast();
  const [i, setI] = useState(0);
  const [answers, setAnswers] = useState<Record<string, { correct: boolean; explanation: string }>>({});
  const [picked, setPicked] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [flagged, setFlagged] = useState<Set<string>>(new Set());
  const [done, setDone] = useState(false);
  const [left, setLeft] = useState(exam ? 42 * 60 + 13 : 0);

  useEffect(() => {
    if (!exam) return;
    const t = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(t);
  }, [exam]);

  const q = quiz.questions[i];
  const correct = Object.values(answers).filter((a) => a.correct).length;

  if (!q) return <p className="text-sm text-[var(--text-muted)]">No questions generated.</p>;

  const submit = async () => {
    const given = q.options.length > 0 ? (picked ?? "") : text;
    if (given === "" || given === null) return;
    try {
      const res = await submitAnswer(quiz.quiz_id, q.id, given as number | string);
      setAnswers((prev) => ({ ...prev, [q.id]: { correct: res.correct, explanation: res.explanation || res.feedback } }));
      if (!res.correct) push("Not quite", { detail: res.explanation || res.feedback, tone: "error" });
      else push("Correct", { tone: "success" });
    } catch (e) {
      push("Grading failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    }
  };

  if (done) {
    const pct = Math.round((correct / quiz.questions.length) * 100);
    return (
      <div className="text-center">
        <p className="text-[13px] text-[var(--text-muted)]">Score</p>
        <p className="mt-1 text-[32px] font-semibold">{correct} / {quiz.questions.length}</p>
        <p className="text-[15px] text-[var(--text-secondary)]">{pct}%</p>
        <div className="mt-4 grid gap-1.5 text-left">
          {quiz.questions.map((qq, idx) => (
            <div key={qq.id} className="flex items-center gap-2 rounded-[7px] border border-[var(--border)] px-2.5 py-2 text-[13px]">
              <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${answers[qq.id]?.correct ? "bg-[var(--accent)] text-white" : answers[qq.id] ? "bg-[var(--danger)] text-white" : "bg-[var(--surface-secondary)] text-[var(--text-muted)]"}`}>
                {idx + 1}
              </span>
              <span className="min-w-0 flex-1 truncate">{qq.stem}</span>
              <span className="text-xs text-[var(--text-muted)]">{answers[qq.id] ? (answers[qq.id].correct ? "Correct" : "Review") : "Skipped"}</span>
            </div>
          ))}
        </div>
        <div className="mt-4">
          <Button variant="secondary" onClick={() => { setDone(false); setI(0); setAnswers({}); }}>Retry</Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
        <span>Question {i + 1} of {quiz.questions.length}</span>
        {exam && <span className="font-mono">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</span>}
        {exam && (
          <button
            type="button"
            onClick={() => setFlagged((prev) => { const n = new Set(prev); if (n.has(q.id)) n.delete(q.id); else n.add(q.id); return n; })}
            className={`cursor-pointer rounded px-2 py-0.5 text-xs ${flagged.has(q.id) ? "bg-[var(--warning-soft)] text-[var(--warning)]" : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"}`}
          >
            {flagged.has(q.id) ? "Flagged" : "Flag"}
          </button>
        )}
      </div>
      <p className="text-[15px] font-medium">{q.stem}</p>
      {q.options.length > 0 ? (
        <div className="mt-3 grid gap-2">
          {q.options.map((opt, idx) => (
            <button
              key={idx}
              onClick={() => setPicked(idx)}
              className={`flex cursor-pointer items-center gap-2.5 rounded-[8px] border px-3 py-2.5 text-left text-sm transition-colors ${
                picked === idx ? "border-[var(--accent-border)] bg-[var(--accent-soft)]/50" : "border-[var(--border)] hover:border-[var(--border-strong)]"
              }`}
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] border border-[var(--border)] font-mono text-xs">
                {String.fromCharCode(65 + idx)}
              </span>
              {opt}
            </button>
          ))}
        </div>
      ) : (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="Type your answer…"
          className="mt-3 w-full rounded-[8px] border border-[var(--border)] bg-[var(--input)] p-2.5 text-sm outline-none focus:border-[var(--accent)]"
        />
      )}
      {answers[q.id] && (
        <p className={`mt-3 rounded-[8px] border p-2.5 text-[13px] ${answers[q.id].correct ? "border-[var(--accent-border)] bg-[var(--accent-soft)]/40" : "border-[var(--danger)]/30 bg-[var(--danger-soft)]"}`}>
          {answers[q.id].explanation || (answers[q.id].correct ? "Correct." : "Incorrect.")}
        </p>
      )}
      <div className="mt-4 flex items-center justify-between">
        <Button variant="ghost" onClick={() => { setI((v) => Math.max(0, v - 1)); setPicked(null); setText(""); }} disabled={i === 0}>Back</Button>
        <div className="flex gap-2">
          {!answers[q.id] ? (
            <Button variant="primary" onClick={() => void submit()} disabled={(q.options.length > 0 && picked === null) || (q.options.length === 0 && !text.trim())}>
              Check
            </Button>
          ) : i + 1 < quiz.questions.length ? (
            <Button variant="primary" onClick={() => { setI((v) => v + 1); setPicked(null); setText(""); }}>Next</Button>
          ) : (
            <Button variant="primary" onClick={() => setDone(true)}>See results</Button>
          )}
        </div>
      </div>
      {exam && quiz.questions.length > 6 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {quiz.questions.map((qq, idx) => (
            <button
              key={qq.id}
              onClick={() => { setI(idx); setPicked(null); setText(""); }}
              className={`h-7 w-7 cursor-pointer rounded-[6px] border font-mono text-xs ${idx === i ? "border-[var(--accent)]" : "border-[var(--border)]"} ${answers[qq.id] ? (answers[qq.id].correct ? "bg-[var(--accent-soft)]" : "bg-[var(--danger-soft)]") : ""} ${flagged.has(qq.id) ? "ring-1 ring-[var(--warning)]" : ""}`}
            >
              {idx + 1}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
