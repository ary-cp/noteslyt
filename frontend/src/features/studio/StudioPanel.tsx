import { buildSchedule } from "./studySchedule";
import type * as React from "react";
import { useEffect, useMemo, useState } from "react";
import {
  ClipboardList,
  FileText,
  GraduationCap,
  Loader2,
  PanelRight,
  // Mic,
  Sparkles,
  // TableProperties,
} from "lucide-react";
import { MdQuiz } from "react-icons/md";
import { PiCardsThreeBold } from "react-icons/pi";
import { RiMindMap } from "react-icons/ri";
import { createExam, createFlashcards, createQuiz, fetchLearner, getQuizAnswers, getReport, getTopicGraph, listStudioJobs, listTopics, refreshTopicGraph, saveStudioArtifact, submitAnswer } from "../../lib/api";
import type { AnswerOut, AssessmentReportOut, LearnerOut, QuizOut, SourceOut, StudioJob, TopicGraph, TopicSummary } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { advanceReview, readReview, type ReviewGrade } from "./flashcardReview";
import { Button, IconButton } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/Feedback";
import { Modal } from "../../components/ui/Modal";
import { Dropdown } from "../../components/ui/Menu";
import { Field, RadioGroup, Slider } from "../../components/ui/Controls";
import { MarkdownBody } from "../chat/ChatPanel";
import { timeAgo } from "../../lib/utils";
import { TimeAgo } from "../../components/TimeAgo";

export type StudioTool = "summary" | "flashcards" | "quiz" | "exam" | "guide" | "mindmap" | "podcast" | "slides" | "sheet" | "schedule";

export const STUDIO_TOOLS: { id: StudioTool; title: string; desc: string; icon: React.ReactNode; ready: boolean }[] = [
  { id: "summary", title: "Summary", desc: "Key points from your sources", icon: <FileText size={16} />, ready: true },
  { id: "quiz", title: "Quiz", desc: "Test yourself with feedback", icon: <MdQuiz size={16} />, ready: true },
  { id: "flashcards", title: "Flashcards", desc: "Review key concepts", icon: <PiCardsThreeBold size={16} />, ready: true },
  { id: "exam", title: "Mock exam", desc: "Timed, exam-style practice", icon: <GraduationCap size={16} />, ready: true },
  { id: "guide", title: "Study guide", desc: "Structured revision document", icon: <ClipboardList size={16} />, ready: true },
  { id: "mindmap", title: "Mind map", desc: "See how ideas connect", icon: <RiMindMap size={16} />, ready: true },
  // { id: "podcast", title: "Audio overview", desc: "Listen on the go", icon: <Mic size={16} />, ready: false },
  // { id: "sheet", title: "Cheat sheet", desc: "One-page revision", icon: <TableProperties size={16} />, ready: false },
];

/** @deprecated Use STUDIO_TOOLS — kept so existing imports keep working. */
const TOOLS = STUDIO_TOOLS;

interface Artifact {
  id: string;
  kind: StudioTool;
  title: string;
  createdAt: string;
  payload: QuizOut | { cards: { front: string; back: string }[] } | { markdown: string; toc: string[] } | { mermaid: string } | { slides: { title: string; bullets: string[] }[] };
}

function jobArtifact(job: StudioJob): Artifact {
  return { id: job.id, kind: job.kind as StudioTool, title: job.title, createdAt: job.created_at,
    payload: (job.payload ?? {}) as Artifact["payload"] };
}

export function StudioPanel({
  projectId,
  sources,
  selectedSources,
  onHide,
  openRequest,
}: {
  projectId: string;
  sources: SourceOut[];
  selectedSources: SourceOut[];
  onHide: () => void;
  openRequest?: { tool: StudioTool; nonce: number } | null;
}) {
  const { push } = useToast();
  const [topics, setTopics] = useState<TopicSummary[]>([]);
  const [learner, setLearner] = useState<LearnerOut | null>(null);
  const [graph, setGraph] = useState<TopicGraph | null>(null);
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const [genTool, setGenTool] = useState<StudioTool | null>(null);
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState(10);
  const [topicId, setTopicId] = useState("");
  const [mapStyle, setMapStyle] = useState<"star" | "flow">("star");
  const [graphBusy, setGraphBusy] = useState(false);
  const [focus, setFocus] = useState("");
  const [examDate, setExamDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    return d.toISOString().slice(0, 10);
  });
  const [minsPerDay, setMinsPerDay] = useState(30);
  const [viewing, setViewing] = useState<Artifact | null>(null);

  // Collapsed-rail shortcut: clicking a tool icon in the slim right rail
  // expands Studio and opens that tool's dialog.
  useEffect(() => {
    if (openRequest) setGenTool(openRequest.tool);
  }, [openRequest]);

  useEffect(() => {
    let active = true;
    setJobs([]);
    listStudioJobs(projectId).then((rows) => { if (active) setJobs(rows); }).catch(() => undefined);
    listTopics(projectId).then((rows) => { if (active) setTopics(rows); }).catch(() => undefined);
    fetchLearner(projectId).then((row) => { if (active) setLearner(row); }).catch(() => undefined);
    getTopicGraph(projectId).then((g) => { if (active) setGraph(g); }).catch(() => undefined);
    return () => { active = false; };
  }, [projectId]);

  const sourceRevision = sources.map((s) => `${s.id}:${s.status}`).join(',');
  useEffect(() => {
    let active = true;
    Promise.all([listTopics(projectId), fetchLearner(projectId), getTopicGraph(projectId)]).then(([t, l, g]) => {
      if (active) { setTopics(t); setLearner(l); setGraph(g); }
    }).catch(() => undefined);
    return () => { active = false; };
  }, [projectId, sourceRevision]);
  const refreshLearner = () => {
    void fetchLearner(projectId).then(setLearner).catch(() => push("Progress could not refresh", { tone: "error" }));
    void listTopics(projectId).then(setTopics).catch(() => undefined);
    void getTopicGraph(projectId).then(setGraph).catch(() => undefined);
  };

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
          if (!rows.some((job) => job.status === "queued" || job.status === "generating")) {
            const [nextTopics, nextLearner] = await Promise.all([listTopics(projectId), fetchLearner(projectId)]);
            if (active) { setTopics(nextTopics); setLearner(nextLearner); }
            return;
          }
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

  // "Learn X first" for study guides: the weakest in-scope topic's prerequisite,
  // when the graph knows one. Names only — no mastery numbers leave this card.
  const learnFirstLine = (): string[] => {
    const weakInScope = (learner?.weakest ?? []).find((w) => scopedTopics.some((t) => t.id === w.topic_id));
    if (!weakInScope || !graph) return [];
    const prereq = graph.edges.find((e) => e.topic_slug === weakInScope.slug);
    if (!prereq || !scopedTopics.some((t) => t.slug === prereq.prereq_slug)) return [];
    const name = graph.nodes.find((n) => n.slug === prereq.prereq_slug)?.name ?? prereq.prereq_slug;
    return [`Learn **${name}** first — it unlocks ${weakInScope.name}.`, ``];
  };

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
          qtypes: ["mcq", "short_answer", "numerical"],
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
        const filter = focus.trim().toLocaleLowerCase();
        const documentTopics = filter ? scopedTopics.filter((t) => `${t.name} ${t.description ?? ""}`.toLocaleLowerCase().includes(filter)) : scopedTopics;
        if (filter && documentTopics.length === 0) {
          push("No matching topics", { detail: "Try a word from a topic name or description.", tone: "warning" });
          return;
        }
        const names = selectedSources.map((s) => s.filename).join(", ");
        const md = [
          `# ${genTool === "guide" ? "Study guide" : genTool === "sheet" ? "Cheat sheet" : "Summary"}`,
          ``,
          `_Topic overview from ${scopeLabel}${filter ? ` · topic filter: ${focus.trim()}` : ""}_`,
          ``,
          `## What this covers`,
          ``,
          `Sources: ${names || "—"}.`,
          ...(scopedTopics.length === 0 ? [`Topics are still being extracted during indexing.`] : []),
          ``,
          `## Key points`,
          ``,
          ...(documentTopics.slice(0, 8).map((t, i) => `${i + 1}. **${t.name}** — ${t.description || `covered in ${t.sources.filter((s) => selectedIdSet.has(s.source_id)).map((s) => s.filename).join(", ") || "your sources"}`}`)),
          ...(scopedTopics.length === 0 ? [`1. Upload completes → key points appear here, each linked to its source.`] : []),
          ``,
          ...(filter ? [] : learnFirstLine()),
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
        const inScope = new Set(nodes.map((t) => t.slug));
        const idBySlug = new Map(nodes.map((t, i) => [t.slug, `${mermaidId(t.name, "topic")}_${i}`] as const));
        const flowEdges = (graph?.edges ?? []).filter((e) => inScope.has(e.topic_slug) && inScope.has(e.prereq_slug));
        let mermaid: string;
        let titleSuffix = "";
        if (mapStyle === "flow" && flowEdges.length > 0) {
          // Prerequisite flow: prereq --> topic, isolated topics still shown.
          const lines = ["graph TD"];
          nodes.forEach((t) => {
            lines.push(`  ${idBySlug.get(t.slug)}[${mermaidLabel(t.name, 26)}]`);
          });
          flowEdges.forEach((e) => {
            lines.push(`  ${idBySlug.get(e.prereq_slug)} --> ${idBySlug.get(e.topic_slug)}`);
          });
          mermaid = lines.join("\n");
          titleSuffix = " flow";
        } else {
          const rootName = (selectedSources[0]?.filename ?? "Project").replace(/\.[^.]+$/, "");
          const lines = ["graph TD", `  main[${mermaidLabel(rootName)}]`];
          nodes.forEach((t, i) => {
            lines.push(`  main --> ${mermaidId(t.name, "topic")}_${i}[${mermaidLabel(t.name, 26)}]`);
          });
          mermaid = lines.join("\n");
        }
        const art: Artifact = {
          id: `mm-${Date.now()}`,
          kind: "mindmap",
          title: `Mind map${titleSuffix} · ${new Date().toLocaleDateString()}`,
          createdAt: new Date().toISOString(),
          payload: { mermaid },
        };
        await persist(art);
        setViewing(art);
        push("Mind map ready", { tone: "success" });
      } else if (genTool === "slides") {
        const deck = buildSlides(
          selectedSources.length > 0 ? selectedSources[0].filename : "Project",
          scopedTopics.slice(0, 8),
          selectedIdSet,
        );
        const art: Artifact = {
          id: `sl-${Date.now()}`,
          kind: "slides",
          title: `Slides · ${new Date().toLocaleDateString()}`,
          createdAt: new Date().toISOString(),
          payload: { slides: deck },
        };
        await persist(art);
        setViewing(art);
        push("Slides ready", { tone: "success" });
      } else if (genTool === "schedule") {
        const masteryById = new Map((learner?.topics ?? []).map((t) => [t.topic_id, t.p_mastery]));
        const md = buildSchedule(
          scopedTopics.map((t) => ({ name: t.name, p: masteryById.get(t.id) ?? 0.3 })),
          examDate,
          minsPerDay,
        );
        const art: Artifact = {
          id: `sch-${Date.now()}`,
          kind: "schedule",
          title: `Study schedule · ${new Date().toLocaleDateString()}`,
          createdAt: new Date().toISOString(),
          payload: { markdown: md, toc: ["Plan"] },
        };
        await persist(art);
        setViewing(art);
        push("Schedule ready", { tone: "success" });
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
        <div className="flex items-center justify-between">
          <h2 className="text-[13px] font-semibold tracking-wide">Studio</h2>
          <IconButton label="Hide studio" onClick={onHide}>
            <PanelRight size={16} />
          </IconButton>
        </div>
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

        {learner && learner.topics.length > 0 && (
          <LearningProfileCard
            learner={learner}
            onStartDiagnostic={() => setGenTool("quiz")}
          />
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
                <div key={job.id} className="overflow-hidden rounded-[8px] border border-[var(--border)] bg-[var(--surface)]">
                  <button
                    type="button"
                    disabled={job.status !== "ready"}
                    onClick={() => setViewing(jobArtifact(job))}
                    className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-[13px] enabled:cursor-pointer enabled:hover:bg-[var(--surface-secondary)] disabled:cursor-default"
                  >
                    <span className="text-[var(--text-secondary)]">{job.status === "queued" || job.status === "generating" ? <Loader2 size={16} className="animate-spin" /> : TOOLS.find((t) => t.id === job.kind)?.icon}</span>
                    <span className="min-w-0 flex-1 truncate">{job.status === "ready" ? job.title : TOOLS.find((t) => t.id === job.kind)?.title ?? job.kind}</span>
                    <span className={`shrink-0 text-[11px] ${job.status === "failed" ? "text-[var(--danger)]" : "text-[var(--text-muted)]"}`}>
                      {job.status === "ready" ? <TimeAgo iso={job.created_at} /> : job.status === "failed" ? "Failed" : job.status === "queued" ? "Queued" : "Generating…"}
                    </span>
                  </button>
                  {job.status === "failed" && (
                    <div className="border-t border-[var(--border)] px-3 pb-3 pt-2">
                      <p className="break-words text-xs leading-relaxed text-[var(--text-secondary)]">{job.error || "Generation could not finish. Please try again."}</p>
                      <button type="button" onClick={() => setGenTool(job.kind as StudioTool)}
                        className="mt-2 cursor-pointer rounded-md border border-[var(--border-strong)] px-2.5 py-1 text-xs font-medium hover:bg-[var(--surface-secondary)]">
                        Try again
                      </button>
                    </div>
                  )}
                </div>
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
          {(genTool === "quiz" || genTool === "exam" || genTool === "flashcards") && (
            <Field label="Topic" hint="Blank = automatic: a spread across your material for new learners, weakest areas first once you've practiced.">
              <Dropdown
                value={topicId}
                onChange={setTopicId}
                ariaLabel="Topic"
                options={[{ value: "", label: genTool === "flashcards" ? "All selected sources" : "Mixed (weakest first)" }, ...scopedTopics.map((t) => ({ value: t.id, label: t.name }))]}
              />
            </Field>
          )}
          {(genTool === "quiz" || genTool === "exam" || genTool === "flashcards") && (
            <Field label={genTool === "flashcards" ? `Flashcards · ${count}` : `${genTool === "exam" ? "Questions" : "Quiz questions"} · ${count}`} hint={`Choose from 5 to ${genTool === "exam" ? 30 : genTool === "flashcards" ? 20 : 10}.`}>
              <Slider value={count} min={5} max={genTool === "exam" ? 30 : genTool === "flashcards" ? 20 : 10} onChange={setCount} ariaLabel={genTool === "exam" ? "Number of exam questions" : genTool === "flashcards" ? "Number of flashcards" : "Number of quiz questions"} />
              <div className="mt-1 flex justify-between font-mono text-[11px] text-[var(--text-muted)]"><span>5</span><span>{genTool === "exam" ? 30 : genTool === "flashcards" ? 20 : 10}</span></div>
            </Field>
          )}
          {genTool === "mindmap" && (
            <Field label="Map style">
              <RadioGroup value={mapStyle} onChange={setMapStyle} ariaLabel="Map style" options={[
                { value: "star", label: "Topics star" },
                { value: "flow", label: "Prerequisite flow" },
              ]} />
            </Field>
          )}
          {genTool === "mindmap" && (
            <div className="rounded-[7px] bg-[var(--surface-secondary)] px-2.5 py-2 text-[13px] text-[var(--text-secondary)]">
              {(graph?.edges.length ?? 0) > 0 ? (
                <>Prerequisite map ready ({graph?.edges.length} links). Pick “Prerequisite flow” above for a dependency map.</>
              ) : (
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span>No prerequisite links yet.</span>
                  <button
                    type="button"
                    disabled={graphBusy}
                    onClick={() => {
                      setGraphBusy(true);
                      refreshTopicGraph(projectId)
                        .then((g) => {
                          setGraph(g);
                          push(g.edges.length ? "Prerequisite map ready" : "No prerequisites found", {
                            detail: g.edges.length ? `${g.edges.length} dependency links.` : "Your topics look independent.",
                            tone: g.edges.length ? "success" : "info",
                          });
                        })
                        .catch((e: unknown) => push("Map build failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" }))
                        .finally(() => setGraphBusy(false));
                    }}
                    className="cursor-pointer font-medium text-[var(--accent)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {graphBusy ? "Building…" : "Build prerequisite map"}
                  </button>
                </span>
              )}
            </div>
          )}
          {genTool !== "quiz" && genTool !== "exam" && genTool !== "flashcards" && genTool !== "mindmap" && genTool !== "slides" && genTool !== "schedule" && <>
          <Field label="Topic filter (optional)" hint="Include topics whose name or description contains this text.">
            <input
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="e.g. entropy"
              className="h-9 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-sm outline-none focus:border-[var(--accent)]"
            />
          </Field>
          </>}
          {genTool === "schedule" && (
            <>
              <Field label="Exam date" hint="The plan runs from today to this date (max 30 days).">
                <input
                  type="date"
                  value={examDate}
                  onChange={(e) => setExamDate(e.target.value)}
                  className="h-9 w-full rounded-[7px] border border-[var(--border)] bg-[var(--input)] px-2.5 text-sm outline-none focus:border-[var(--accent)]"
                />
              </Field>
              <Field label={`Minutes per day · ${minsPerDay}`}>
                <Slider value={minsPerDay} min={10} max={120} onChange={setMinsPerDay} ariaLabel="Minutes per day" />
                <div className="mt-1 flex justify-between font-mono text-[11px] text-[var(--text-muted)]"><span>10</span><span>120</span></div>
              </Field>
            </>
          )}
          {busy && <p className="text-[13px] text-[var(--text-secondary)]">Generating… this calls your project corpus.</p>}
        </div>
      </Modal>

      <ArtifactViewer key={viewing?.id ?? "closed"} artifact={viewing} onAnswered={refreshLearner} onClose={() => setViewing(null)} />
    </div>
  );
}

/** Mermaid flowchart node id: letters and digits only (spaces break parsing). */
function mermaidId(s: string, fallback: string): string {
  return s.replace(/[^a-zA-Z0-9]/g, "").slice(0, 20) || fallback;
}

/** Mermaid flowchart label: quoted, with diagram-breaking characters stripped. */
function mermaidLabel(s: string, max = 28): string {
  const clean = s.replace(/["#<>`[\]{}()|\\]/g, "").replace(/\s+/g, " ").trim().slice(0, max) || "Topic";
  return `"${clean}"`;
}

/** Slide deck from already-fetched topics — deterministic, no LLM call. */
function buildSlides(
  sourceName: string,
  topics: TopicSummary[],
  selectedIds: Set<string>,
): { title: string; bullets: string[] }[] {
  const slides: { title: string; bullets: string[] }[] = [
    {
      title: `Study deck · ${sourceName.replace(/\.[^.]+$/, "")}`,
      bullets: topics.length ? [`${topics.length} key topics`, "Use ← → keys to present"] : ["Add sources to fill this deck"],
    },
  ];
  topics.forEach((t) => {
    const files = t.sources.filter((s) => selectedIds.has(s.source_id)).map((s) => s.filename);
    slides.push({
      title: t.name,
      bullets: [
        t.description || "Key concept from your sources",
        ...(files.length ? [`Sources: ${files.slice(0, 3).join(", ")}`] : []),
      ].filter(Boolean).slice(0, 4),
    });
  });
  slides.push({ title: "Next step", bullets: ["Generate a quiz to check recall", "Ask the chat to explain any slide"] });
  return slides;
}

/**
 * The learner sees one number only: overall mastery across the project's
 * topics. Per-topic names, attempt counts and coverage stay server-side —
 * they drive adaptive quizzes but are never disclosed.
 */
function LearningProfileCard({ learner, onStartDiagnostic }: { learner: LearnerOut; onStartDiagnostic: () => void }) {
  // Only topics with real evidence (a quiz attempt or chat signal) count.
  // Unseen topics carry the BKT prior (0.30) as an update seed — showing it
  // as "30% mastery" on a fresh project is misleading, so they are excluded
  // until the learner generates evidence.
  const assessed = learner.topics.filter((t) => t.coverage_status !== "not_started");
  const practiced = assessed.length > 0;
  const overall = practiced
    ? Math.round((assessed.reduce((sum, t) => sum + t.p_mastery, 0) / assessed.length) * 100)
    : null;
  return (
    <div className="mt-4 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] p-3">
      <p className="text-xs font-medium text-[var(--text-muted)]">YOUR LEARNING PROFILE</p>
      <div className="mt-2 flex items-center gap-3">
        <p className="text-[28px] font-semibold leading-none">{practiced ? `${overall}%` : "—"}</p>
        <div className="min-w-0 flex-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
            {practiced && <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${overall}%` }} />}
          </div>
          <p className="mt-1.5 text-xs text-[var(--text-muted)]">
            {practiced
              ? `Overall mastery across ${assessed.length} assessed topic${assessed.length > 1 ? "s" : ""} · updates after each quiz answer.`
              : "Not assessed yet · answer a quiz to measure mastery."}
          </p>
        </div>
      </div>
      {(learner.due_review ?? 0) > 0 && (
        <p className="mt-2 text-xs text-[var(--text-secondary)]">
          {learner.due_review} topic{learner.due_review > 1 ? "s" : ""} due for review — quiz them to keep mastery fresh.
        </p>
      )}
      {!practiced && (
        <div className="mt-3 rounded-[7px] bg-[var(--surface-secondary)] p-2.5">
          <p className="text-[13px] font-medium">New here? Start with a diagnostic.</p>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">A short mixed quiz across your sources finds your level.</p>          <button
            type="button"
            onClick={onStartDiagnostic}
            className="mt-2 inline-flex h-8 cursor-pointer items-center rounded-[7px] bg-[var(--accent)] px-3 text-[13px] font-medium text-white hover:bg-[var(--accent-hover)]"
          >
            Start diagnostic quiz
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------------- artifact viewer ---------------- */

function ArtifactViewer({ artifact, onClose, onAnswered }: { artifact: Artifact | null; onClose: () => void; onAnswered: () => void }) {
  const [finished, setFinished] = useState(false);
  const artId = artifact?.id ?? "closed";
  useEffect(() => { setFinished(false); }, [artId]);
  if (!artifact) return null;
  const isTest = artifact.kind === "quiz" || artifact.kind === "exam";
  return (
    <Modal
      open
      onClose={onClose}
      title={artifact.title}
      subtitle={`${timeAgo(artifact.createdAt)}${isTest && !finished ? " · in progress — backdrop clicks won't close this" : ""}`}
      width={720}
      dismissable={!isTest || finished}
    >
      {isTest ? (
        <QuizRunner quiz={artifact.payload as QuizOut} exam={artifact.kind === "exam"} onAnswered={onAnswered} onDoneChange={setFinished} />
      ) : artifact.kind === "flashcards" ? (
        <Flashcards key={artifact.id} reviewKey={artifact.id} cards={(artifact.payload as { cards: { front: string; back: string }[] }).cards ?? []} requested={(artifact.payload as { requested?: number }).requested} />
      ) : artifact.kind === "mindmap" ? (
        <MindMapView mermaid={(artifact.payload as { mermaid: string }).mermaid} />
      ) : artifact.kind === "slides" ? (
        <SlidesViewer slides={(artifact.payload as { slides: { title: string; bullets: string[] }[] }).slides ?? []} />
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

/* ---------------- slide deck ---------------- */

function SlidesViewer({ slides }: { slides: { title: string; bullets: string[] }[] }) {
  const [idx, setIdx] = useState(0);
  const n = Math.max(1, slides.length);
  const go = (dir: number) => setIdx((v) => Math.min(n - 1, Math.max(0, v + dir)));
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        go(1);
      } else if (e.key === "ArrowLeft") {
        go(-1);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [n]);
  const slide = slides[idx] ?? { title: "No slides", bullets: [] as string[] };
  return (
    <div>
      <p className="mb-2 text-center font-mono text-xs text-[var(--text-muted)]">{Math.min(idx + 1, n)} / {n}</p>
      <div className="min-h-[220px] rounded-[10px] border border-[var(--border)] bg-[var(--surface)] p-6">
        <p className="text-lg font-semibold">{slide.title}</p>
        <ul className="mt-3 grid gap-2">
          {slide.bullets.map((b, i) => (
            <li key={i} className="flex gap-2 text-[15px]">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
              <span className="min-w-0 flex-1"><MarkdownBody text={b} /></span>
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <Button variant="ghost" onClick={() => go(-1)} disabled={idx === 0}>Back</Button>
        <div className="flex gap-1">
          {slides.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setIdx(i)}
              aria-label={`Go to slide ${i + 1}`}
              className={`h-1.5 cursor-pointer rounded-full transition-colors ${i === idx ? "w-6 bg-[var(--accent)]" : "w-1.5 bg-[var(--border-strong)]"}`}
            />
          ))}
        </div>
        <Button variant="ghost" onClick={() => go(1)} disabled={idx >= n - 1}>Next</Button>
      </div>
    </div>
  );
}

/* ---------------- flashcards ---------------- */

function Flashcards({ cards, requested, reviewKey }: { cards: { front: string; back: string }[]; requested?: number; reviewKey: string }) {
  const storageKey = `noteslyt:flashcard-review:${reviewKey}`;
  const [queue, setQueue] = useState<number[]>(() => {
    try { return readReview(localStorage.getItem(storageKey), cards.length); }
    catch { return readReview(null, cards.length); }
  });
  const [flipped, setFlipped] = useState(false);
  const saveQueue = (next: number[]) => {
    setQueue(next);
    try { localStorage.setItem(storageKey, JSON.stringify({ count: cards.length, queue: next })); } catch { /* In-memory review remains available. */ }
  };
  const i = queue[0];
  if (cards.length === 0) return <p className="text-sm text-[var(--text-muted)]">No cards in this set.</p>;
  if (i === undefined) return (
    <div className="py-6 text-center">
      <p className="text-lg font-semibold">Set complete</p>
      <p className="mt-1 text-sm text-[var(--text-secondary)]">You reviewed all {cards.length} cards.</p>
      <Button className="mt-4" variant="secondary" onClick={() => { saveQueue(readReview(null, cards.length)); setFlipped(false); }}>Review again</Button>
    </div>
  );
  const card = cards[i];

  const grade = (g: ReviewGrade) => {
    setFlipped(false);
    saveQueue(advanceReview(queue, g));
  };

  return (
    <div>
      {requested && cards.length < requested && <p className="mb-3 rounded-lg bg-[var(--surface-secondary)] p-3 text-xs text-[var(--text-secondary)]">Created {cards.length} of {requested} requested cards from the available material.</p>}
      <p className="mb-2 text-center font-mono text-xs text-[var(--text-muted)]">{queue.length} remaining · {cards.length - queue.length} / {cards.length} complete</p>
      <div className="h-1 overflow-hidden rounded-full bg-[var(--border)]">
        <div className="h-full bg-[var(--accent)]" style={{ width: `${((cards.length - queue.length) / cards.length) * 100}%` }} />
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
            <MarkdownBody text={card.front} />
          </span>
          <span className="flip-face flip-back absolute inset-0 flex items-center justify-center rounded-[10px] border border-[var(--accent-border)] bg-[var(--accent-soft)]/40 p-6 text-center text-[15px]">
            <MarkdownBody text={card.back} />
          </span>
        </span>
      </button>
      <p className="mt-2 text-center text-xs text-[var(--text-muted)]">Click or press Space to flip</p>
      <p className="mt-1 text-center text-xs text-[var(--text-muted)]">Again: retry soon · Hard: retry later · Good/Easy: complete. Progress saved in this browser.</p>
      <div className="mt-3 flex justify-center gap-2">
        {(["Again", "Hard", "Good", "Easy"] as const).map((g) => (
          <button
            key={g}
            onClick={() => grade(g)}
            disabled={!flipped}
            className="h-8 cursor-pointer rounded-[7px] border border-[var(--border)] px-3 text-[13px] font-medium hover:bg-[var(--surface-secondary)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {g}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------------- post-assessment report ---------------- */

/**
 * The one place weak topics are named (3c): score deltas per topic, likely
 * misconceptions from persisted rubric misses, and what to study next.
 * Old reports without the new keys render their score + review list only.
 */
function QuizReport({ assessmentId }: { assessmentId: string }) {
  const [report, setReport] = useState<AssessmentReportOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setFailed(false);
    getReport(assessmentId)
      .then((r) => { if (live) setReport(r); })
      .catch(() => { if (live) setFailed(true); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [assessmentId]);

  if (loading) return <p className="mt-4 text-center text-[13px] text-[var(--text-muted)]">Building your report…</p>;
  if (failed || !report) return null;

  const deltas = [...(report.report.mastery_delta ?? [])]
    .sort((a, b) => (a.p_after ?? 1) - (b.p_after ?? 1));
  const misconceptions = report.report.misconceptions ?? [];
  const nextName = report.report.next_up_name ?? report.report.next_up;
  if (!deltas.length && !misconceptions.length && !nextName) return null;

  return (
    <div className="mt-4 rounded-[8px] border border-[var(--border)] bg-[var(--surface-secondary)]/50 p-3 text-left">
      <p className="text-[13px] font-semibold">Assessment report</p>
      {deltas.length > 0 && (
        <div className="mt-2 grid gap-2">
          {deltas.slice(0, 5).map((d) => (
            <div key={d.slug} className="flex items-center gap-2 text-[13px]">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{d.name || d.slug}</span>
                <span className="block text-[11px] text-[var(--text-muted)]">
                  mastery {d.p_before == null ? "—" : `${Math.round(d.p_before * 100)}%`} → {d.p_after == null ? "—" : `${Math.round(d.p_after * 100)}%`}
                </span>
              </span>
              <span className="h-1 w-20 shrink-0 overflow-hidden rounded-full bg-[var(--border)]">
                {d.p_after != null && <span className="block h-full rounded-full bg-[var(--accent)]" style={{ width: `${Math.max(0, Math.min(100, Math.round(d.p_after * 100)))}%` }} />}
              </span>
            </div>
          ))}
        </div>
      )}
      {misconceptions.length > 0 && (
        <div className="mt-3">
          <p className="text-[12px] font-semibold text-[var(--text-secondary)]">Likely misconceptions</p>
          <ul className="mt-1 grid gap-1">
            {misconceptions.slice(0, 4).map((m) => (
              <li key={m.slug} className="text-[13px]">
                <span className="font-medium">{m.name || m.slug}</span>
                <ul className="mt-0.5 grid gap-0.5">
                  {m.points.slice(0, 3).map((p, i) => (
                    <li key={i} className="text-[13px] text-[var(--text-secondary)]">· {p}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      )}
      {nextName && (
        <p className="mt-3 rounded-[7px] bg-[var(--accent-soft)]/50 px-2.5 py-2 text-[13px]">
          Study next: <span className="font-medium">{nextName}</span> — generate another quiz to practice it.
        </p>
      )}
    </div>
  );
}

/* ---------------- quiz / exam ---------------- */

const EXAM_SECONDS_PER_QUESTION = 120;
const EXAM_TAB_PENALTY_S = 300;

function QuizRunner({ quiz, exam, onAnswered, onDoneChange }: { quiz: QuizOut; exam: boolean; onAnswered: () => void; onDoneChange?: (done: boolean) => void }) {
  const { push } = useToast();
  const [i, setI] = useState(0);
  const [answers, setAnswers] = useState<Record<string, AnswerOut>>({});
  const [loadingAnswers, setLoadingAnswers] = useState(true);
  const [resumeError, setResumeError] = useState("");
  const [resumeVersion, setResumeVersion] = useState(0);
  useEffect(() => {
    let active = true;
    setLoadingAnswers(true); setResumeError("");
    getQuizAnswers(quiz.quiz_id).then((saved) => { if (active) setAnswers(saved); })
      .catch((e) => { if (active) setResumeError(e instanceof Error ? e.message : "Could not load saved answers"); })
      .finally(() => { if (active) setLoadingAnswers(false); });
    return () => { active = false; };
  }, [quiz.quiz_id, resumeVersion]);
  const [submitting, setSubmitting] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [flagged, setFlagged] = useState<Set<string>>(new Set());
  const [done, setDone] = useState(false);
  const [left, setLeft] = useState(exam ? quiz.questions.length * EXAM_SECONDS_PER_QUESTION : 0);
  const [started, setStarted] = useState(!exam);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [violations, setViolations] = useState(0);

  useEffect(() => { onDoneChange?.(done); }, [done, onDoneChange]);

  useEffect(() => {
    if (!exam || !started || done) return;
    const t = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(t);
  }, [exam, started, done]);
  useEffect(() => { if (exam && started && left === 0) setDone(true); }, [exam, started, left]);

  // Exam strictness: leaving the tab costs 5 minutes.
  useEffect(() => {
    if (!exam || !started || done) return;
    const onVis = () => {
      if (document.hidden) {
        setLeft((s) => Math.max(0, s - EXAM_TAB_PENALTY_S));
        setViolations((v) => v + 1);
        push("Tab switch detected — 5:00 deducted", { detail: "Stay on this tab until you submit the exam.", tone: "warning" });
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [exam, started, done, push]);

  const q = quiz.questions[i];
  const correct = Object.values(answers).filter((a) => a.correct).length;

  if (loadingAnswers) return <p className="text-sm text-[var(--text-muted)]">Loading saved answers…</p>;
  if (resumeError) return <div><p role="alert" className="mb-3 text-sm text-[var(--danger)]">{resumeError}</p><Button onClick={() => setResumeVersion((v) => v + 1)}>Try again</Button></div>;
  if (!q) return <p className="text-sm text-[var(--text-muted)]">No questions generated.</p>;

  const submit = async () => {
    if (submitting || answers[q.id]) return;
    const given = q.qtype === "mcq" ? (picked ?? "") : text.trim();
    if (given === "" || given === null) return;
    setSubmitting(true);
    try {
      const res = await submitAnswer(quiz.quiz_id, q.id, given as number | string);
      setAnswers((prev) => ({ ...prev, [q.id]: res }));
      onAnswered();
      // Exam mode stays silent: no per-question reveal — everything unlocks at the end.
      if (!exam) {
        if (!res.correct) push("Answer saved — review the explanation", { tone: "warning" });
        else push("Correct", { tone: "success" });
      }
    } catch (e) {
      push("Grading failed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally { setSubmitting(false); }
  };

  const answeredCount = Object.keys(answers).length;

  // Exam rules gate: the paper starts only from here, so the timer,
  // no-copy lock and tab penalty all begin together.
  if (exam && !started) {
    const totalMin = Math.round((quiz.questions.length * EXAM_SECONDS_PER_QUESTION) / 60);
    return (
      <div className="grid gap-3">
        <div className="rounded-[8px] border border-[var(--accent-border)] bg-[var(--accent-soft)]/40 p-4">
          <p className="text-[15px] font-semibold">Mock exam · {quiz.questions.length} questions · {totalMin} minutes</p>
          <p className="mt-1 text-[13px] text-[var(--text-secondary)]">Exam conditions apply once you start:</p>
          <ul className="mt-2 grid gap-1.5 text-[13px] text-[var(--text-secondary)]">
            <li>· This dialog stays open until you submit — backdrop clicks and Escape are disabled. The ✕ button ends the exam early.</li>
            <li>· Copying and right-click are disabled on the paper.</li>
            <li>· Switching tabs costs <span className="font-semibold text-[var(--text-primary)]">5:00</span> each time.</li>
            <li>· Answers and feedback unlock <span className="font-semibold text-[var(--text-primary)]">only after you submit</span> — nothing is revealed question-by-question.</li>
          </ul>
        </div>
        <div className="flex items-center justify-end gap-2">
          <Button variant="primary" onClick={() => setStarted(true)}>Start exam</Button>
        </div>
      </div>
    );
  }

  if (done) {
    const pct = Math.round((correct / quiz.questions.length) * 100);
    const assessmentId = Object.values(answers).find((a) => a.assessment_id)?.assessment_id ?? null;
    return (
      <div className="text-center">
        <p className="text-[13px] text-[var(--text-muted)]">{exam ? "Exam submitted" : "Score"}{exam && violations > 0 ? ` · ${violations} tab-switch penalt${violations > 1 ? "ies" : "y"}` : ""}</p>
        <p className="mt-1 text-[32px] font-semibold">{correct} / {quiz.questions.length}</p>
        <p className="text-[15px] text-[var(--text-secondary)]">{pct}%</p>
        {assessmentId && <QuizReport assessmentId={assessmentId} />}
        <div className="mt-4 grid gap-1.5 text-left">
          {quiz.questions.map((qq, idx) => {
            const a = answers[qq.id];
            return (
              <div key={qq.id} className="rounded-[7px] border border-[var(--border)] px-2.5 py-2 text-[13px]">
                <div className="flex items-center gap-2">
                  <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${a?.correct ? "bg-[var(--accent)] text-white" : a ? "bg-[var(--danger)] text-white" : "bg-[var(--surface-secondary)] text-[var(--text-muted)]"}`}>
                    {idx + 1}
                  </span>
                  <div className="min-w-0 flex-1"><MarkdownBody text={qq.stem} /></div>
                  <span className="shrink-0 text-xs text-[var(--text-muted)]">{a ? (a.correct ? "Correct" : "Review") : "Skipped"}</span>
                </div>
                {a && (
                  <div className="mt-2 border-t border-[var(--border)] pt-2">
                    {!a.correct && <MarkdownBody text={`**Expected answer:** ${a.expected ?? "See explanation"}`} />}
                    <MarkdownBody text={[a.feedback, a.feedback.includes(a.explanation) ? "" : a.explanation].filter(Boolean).join("\n\n")} />
                    <p className="mt-1 text-xs text-[var(--text-muted)]">{a.citation.source} · {a.citation.label}</p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="mt-4">
          <Button variant="secondary" onClick={() => { setDone(false); setI(0); }}>Review answers</Button>
        </div>
      </div>
    );
  }

  const fmtLeft = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  return (
    <div
      onCopy={exam ? (e) => e.preventDefault() : undefined}
      onCut={exam ? (e) => e.preventDefault() : undefined}
      onContextMenu={exam ? (e) => e.preventDefault() : undefined}
      className={exam ? "select-none" : undefined}
    >
      <div className="mb-3 flex items-center justify-between gap-2 text-[13px] text-[var(--text-secondary)]">
        <span>Question {i + 1} of {quiz.questions.length}{exam ? ` · ${answeredCount} saved` : ""}</span>
        {exam && (
          <span aria-live="polite" title="Time remaining" className={`font-mono text-xl font-bold tabular-nums ${left < 300 ? "text-[var(--danger)]" : "text-[var(--accent)]"}`}>
            {fmtLeft}
          </span>
        )}
        {exam && (
          <span className="flex items-center gap-2">
            {violations > 0 && <span title="Tab-switch penalties" className="font-mono text-xs text-[var(--warning)]">−5:00 × {violations}</span>}
            <button
              type="button"
              onClick={() => setFlagged((prev) => { const n = new Set(prev); if (n.has(q.id)) n.delete(q.id); else n.add(q.id); return n; })}
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
        )}
      </div>
      <MarkdownBody text={q.stem} />
      {q.qtype === "mcq" ? (
        <div className="mt-3 grid gap-2">
          {q.options.map((opt, idx) => {
            const saved = !!answers[q.id];
            const savedPicked = saved && Number(answers[q.id].given) === idx;
            const isPicked = !saved && picked === idx;
            return (
            <button
              key={idx}
              onClick={() => setPicked(idx)}
              disabled={submitting || saved}
              aria-pressed={(saved ? Number(answers[q.id].given) : picked) === idx}
              className={`flex cursor-pointer items-center gap-2.5 rounded-[8px] border px-3 py-2.5 text-left text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] ${
                exam
                  ? (savedPicked || isPicked
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] font-medium shadow-[0_0_0_1px_var(--accent)]"
                    : "border-[var(--border)] hover:border-[var(--border-strong)]")
                  : answers[q.id] && opt === answers[q.id].expected ? "border-[var(--accent-border)] bg-[var(--accent-soft)]/50" :
                    answers[q.id] && Number(answers[q.id].given) === idx ? "border-[var(--danger)] bg-[var(--danger-soft)]" :
                    isPicked ? "border-[var(--accent)] bg-[var(--accent-soft)] font-medium shadow-[0_0_0_1px_var(--accent)]" : "border-[var(--border)] hover:border-[var(--border-strong)]"
              }`}
            >
              <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-[6px] border font-mono text-xs ${
                (exam ? (savedPicked || isPicked) : isPicked) ? "border-[var(--accent)] bg-[var(--accent)] text-white" : "border-[var(--border)]"
              }`}>
                {String.fromCharCode(65 + idx)}
              </span>
              <span className="min-w-0 flex-1"><MarkdownBody text={opt} /></span>
              {(isPicked || (exam && savedPicked)) && <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-[var(--accent)]" />}
            </button>
            );
          })}
        </div>
      ) : (
        <textarea
          value={answers[q.id] ? String(answers[q.id].given) : text}
          disabled={submitting || !!answers[q.id]}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="Type your answer…"
          className="mt-3 w-full rounded-[8px] border border-[var(--border)] bg-[var(--input)] p-2.5 text-sm outline-none focus:border-[var(--accent)]"
        />
      )}
      {exam && answers[q.id] && (
        <p className="mt-3 rounded-[8px] border border-[var(--accent-border)] bg-[var(--accent-soft)]/40 p-2.5 text-[13px] font-medium">
          Saved — answers and feedback unlock when you submit the exam.
        </p>
      )}
      {!exam && answers[q.id] && (
        <div className={`mt-3 rounded-[8px] border p-2.5 text-[13px] ${answers[q.id].correct ? "border-[var(--accent-border)] bg-[var(--accent-soft)]/40" : "border-[var(--danger)]/30 bg-[var(--danger-soft)]"}`}>
          <p className="mb-2 font-semibold">{answers[q.id].correct ? "Correct" : "Not quite"}</p>
          {!answers[q.id].correct && <MarkdownBody text={`**Expected answer:** ${answers[q.id].expected ?? "See explanation"}`} />}
          <MarkdownBody text={[answers[q.id].feedback, answers[q.id].feedback.includes(answers[q.id].explanation) ? "" : answers[q.id].explanation].filter(Boolean).join("\n\n")} />
          <p className="mt-2 text-xs text-[var(--text-muted)]">{answers[q.id].citation.source} · {answers[q.id].citation.label}</p>
        </div>
      )}
      <div className="mt-4 flex items-center justify-between">
        <Button variant="ghost" onClick={() => { setI((v) => Math.max(0, v - 1)); setPicked(null); setText(""); }} disabled={i === 0 || submitting}>Back</Button>
        <div className="flex gap-2">
          {!answers[q.id] ? (
            <Button variant="primary" onClick={() => void submit()} loading={submitting} disabled={submitting || (q.options.length > 0 && picked === null) || (q.options.length === 0 && !text.trim())}>
              {exam ? "Save answer" : "Check"}
            </Button>
          ) : i + 1 < quiz.questions.length ? (
            <Button variant="primary" onClick={() => { setI((v) => v + 1); setPicked(null); setText(""); }}>Next</Button>
          ) : exam ? (
            <Button variant="primary" onClick={() => setConfirmEnd(true)}>Submit exam</Button>
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
              disabled={submitting}
              onClick={() => { setI(idx); setPicked(null); setText(""); }}
              aria-label={`Go to question ${idx + 1}${answers[qq.id] ? " (saved)" : ""}`}
              className={`h-7 w-7 cursor-pointer rounded-[6px] border font-mono text-xs ${idx === i ? "border-[var(--accent)]" : "border-[var(--border)]"} ${answers[qq.id] ? "bg-[var(--accent-soft)]" : ""} ${flagged.has(qq.id) ? "ring-1 ring-[var(--warning)]" : ""}`}
            >
              {idx + 1}
            </button>
          ))}
        </div>
      )}
      {exam && (
        <Modal
          open={confirmEnd}
          onClose={() => setConfirmEnd(false)}
          title="Submit exam?"
          width={420}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirmEnd(false)}>Keep writing</Button>
              <Button variant="primary" onClick={() => { setConfirmEnd(false); setDone(true); }}>
                Submit ({answeredCount}/{quiz.questions.length} saved)
              </Button>
            </>
          }
        >
          <p className="text-sm text-[var(--text-secondary)]">
            Unanswered questions count as skipped. Answers, scores and feedback unlock after you submit.
          </p>
        </Modal>
      )}
    </div>
  );
}
