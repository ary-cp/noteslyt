import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getProfileOverview } from "../lib/api";
import type { ProfileOverview } from "../lib/types";
import { useToast } from "../lib/toast";

function pct(p: number): string {
  return `${Math.round(p * 100)}%`;
}

export default function LearningProfilePage() {
  const { push } = useToast();
  const [data, setData] = useState<ProfileOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    getProfileOverview()
      .then((d) => {
        if (live) setData(d);
      })
      .catch((e) => {
        if (!live) return;
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        push("Could not load Learning Profile", { detail: msg, tone: "error" });
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [push]);

  if (loading) {
    return (
      <main className="mx-auto w-full max-w-4xl px-5 py-8">
        <p className="text-sm text-[var(--text-muted)]">Loading your learning profile…</p>
      </main>
    );
  }
  if (error || !data) {
    return (
      <main className="mx-auto w-full max-w-4xl px-5 py-8">
        <p role="alert" className="text-sm text-[var(--danger)]">{error || "Could not load profile."}</p>
        <Link to="/" className="mt-2 inline-block text-sm text-[var(--accent)] hover:underline">← Back to dashboard</Link>
      </main>
    );
  }

  const answered = data.quiz_progress?.answered ?? 0;
  const correct = data.quiz_progress?.correct ?? 0;

  return (
    <main className="mx-auto w-full max-w-4xl px-5 py-8">
      <Link to="/" className="text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)]">← Dashboard</Link>
      <h1 className="mt-2 text-xl font-semibold">Learning Profile</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        BKT mastery per topic plus your IRT ability — updated after every quiz, exam and conversation.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-secondary)] p-3">
          <p className="text-xs text-[var(--text-muted)]">Quiz accuracy</p>
          <p className="mt-1 text-lg font-semibold">
            {answered ? `${Math.round((correct / answered) * 100)}%` : "—"}
          </p>
          <p className="text-xs text-[var(--text-muted)]">{answered} answered · {data.quiz_progress?.topics_practiced ?? 0} topics</p>
        </div>
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-secondary)] p-3">
          <p className="text-xs text-[var(--text-muted)]">Ability (IRT)</p>
          <p className="mt-1 text-lg font-semibold">{data.irt_ability.toFixed(2)} · {data.irt_label}</p>
          <p className="text-xs text-[var(--text-muted)]">Rasch estimate across graded answers</p>
        </div>
        <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-secondary)] p-3">
          <p className="text-xs text-[var(--text-muted)]">How you learn</p>
          <p className="mt-1 text-[13px]">{data.learning_style_notes || "Not enough chats yet — the tutor writes this after a few questions."}</p>
        </div>
      </div>

      {data.goals && (
        <div className="mt-3 rounded-lg border border-[var(--border)] p-3">
          <p className="text-xs text-[var(--text-muted)]">Goals</p>
          <p className="mt-1 text-sm">{data.goals}</p>
        </div>
      )}

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <section className="rounded-lg border border-[var(--border)] p-3">
          <h2 className="text-sm font-semibold">Strong topics ({data.strong_topics.length})</h2>
          {data.strong_topics.length === 0 ? (
            <p className="mt-1 text-[13px] text-[var(--text-muted)]">Nothing mastered yet — keep practising.</p>
          ) : (
            <ul className="mt-2 grid gap-1">
              {data.strong_topics.map((t) => (
                <li key={`${t.project_id}-${t.topic_id}`} className="flex items-center justify-between text-[13px]">
                  <span>{t.name}{t.project_title ? <span className="text-[var(--text-muted)]"> · {t.project_title}</span> : null}</span>
                  <span className="font-mono text-xs">{pct(t.p_mastery)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="rounded-lg border border-[var(--border)] p-3">
          <h2 className="text-sm font-semibold">Weak topics ({data.weak_topics.length})</h2>
          {data.weak_topics.length === 0 ? (
            <p className="mt-1 text-[13px] text-[var(--text-muted)]">No weak topics — nice.</p>
          ) : (
            <ul className="mt-2 grid gap-1">
              {data.weak_topics.map((t) => (
                <li key={`${t.project_id}-${t.topic_id}`} className="flex items-center justify-between text-[13px]">
                  <span>{t.name}{t.project_title ? <span className="text-[var(--text-muted)]"> · {t.project_title}</span> : null}</span>
                  <span className="font-mono text-xs">{pct(t.p_mastery)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="mt-4 rounded-lg border border-[var(--border)] p-3">
        <h2 className="text-sm font-semibold">Areas of improvement</h2>
        {data.areas_of_improvement.length === 0 ? (
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">Answer a quiz or chat to get targeted next steps.</p>
        ) : (
          <ul className="mt-2 grid gap-1">
            {data.areas_of_improvement.map((a, i) => (
              <li key={i} className="text-[13px] text-[var(--text-secondary)]">• {a}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-4 rounded-lg border border-[var(--border)] p-3">
        <h2 className="text-sm font-semibold">All topics ({data.topics.length})</h2>
        {data.topics.length === 0 ? (
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">Upload sources to build topics.</p>
        ) : (
          <div className="mt-2 grid gap-1">
            {data.topics.map((t) => (
              <div key={`${t.project_id}-${t.topic_id}`} className="flex items-center gap-2 text-[13px]">
                <span className="w-24 shrink-0 font-mono text-[11px] text-[var(--text-muted)]">{t.label}</span>
                <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--surface-raised)]">
                  <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${Math.round(t.p_mastery * 100)}%` }} />
                </div>
                <span className="w-40 shrink-0 truncate">{t.name}</span>
                <span className="w-10 shrink-0 text-right font-mono text-[11px]">{pct(t.p_mastery)}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="mt-4 rounded-lg border border-[var(--border)] p-3">
        <h2 className="text-sm font-semibold">Personalised reports ({data.recent_reports.length})</h2>
        {data.recent_reports.length === 0 ? (
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">Reports appear here after quizzes, exams and conversations.</p>
        ) : (
          <div className="mt-2 grid gap-2">
            {data.recent_reports.map((r) => (
              <article key={r.id} className="rounded-md bg-[var(--surface-secondary)] p-2.5">
                <p className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                  {r.kind} · {new Date(r.created_at).toLocaleString()}
                </p>
                <p className="mt-1 text-[13px]">{r.summary}</p>
                {(r.strengths.length > 0 || r.weaknesses.length > 0) && (
                  <div className="mt-1 grid gap-1 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
                    {r.strengths.length > 0 && <p>✓ {r.strengths.join("; ")}</p>}
                    {r.weaknesses.length > 0 && <p>△ {r.weaknesses.join("; ")}</p>}
                  </div>
                )}
                {r.bkt?.mastery_delta && r.bkt.mastery_delta.length > 0 && (
                  <p className="mt-1 font-mono text-[11px] text-[var(--text-muted)]">
                    BKT: {r.bkt.mastery_delta.slice(0, 3).map((d) => `${d.name ?? d.slug} ${d.p_before ?? "?"}→${d.p_after ?? "?"}`).join(" · ")}
                    {r.irt?.ability_after !== undefined && r.irt?.ability_after !== null ? ` · IRT ${Number(r.irt.ability_after).toFixed(2)}` : ""}
                  </p>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
