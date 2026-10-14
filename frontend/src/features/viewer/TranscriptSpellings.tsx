import { useEffect, useState } from "react";
import { correctTranscriptSpellings, getSource } from "../../lib/api";

export function TranscriptSpellings({ sourceId, ready, onUpdated }: { sourceId: string; ready: boolean; onUpdated: () => void }) {
  const [heard, setHeard] = useState("");
  const [correct, setCorrect] = useState("");
  const [pending, setPending] = useState(false);
  const [polling, setPolling] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!polling) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const source = await getSource(sourceId);
        if (!active) return;
        if (source.status === "ready" || source.status === "failed") {
          setPolling(false); setPending(false);
          setMessage(source.status === "ready" ? "Spelling updated in transcript and search." : source.status_detail || "Update failed. Retry from Sources.");
          onUpdated();
          window.dispatchEvent(new Event("noteslyt:sources-updated"));
          return;
        }
      } catch { /* Keep checking through a temporary connection failure. */ }
      if (active) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [polling, sourceId, onUpdated]);
  return <details className="mb-3 rounded-lg border border-[var(--border)] p-3 text-xs">
    <summary className="cursor-pointer text-[var(--text-secondary)]">Correct a name or spelling</summary>
    <p className="my-2 text-[var(--text-muted)]">Use a confirmed spelling. Replaces all matching words in this source. Original audio and timestamps stay unchanged. New answers use the corrected spelling.</p>
    <form className="space-y-2" onSubmit={async (event) => {
      event.preventDefault(); setPending(true); setMessage("");
      try {
        await correctTranscriptSpellings(sourceId, { [heard.trim()]: correct.trim() });
        setMessage("Updating transcript and search…"); setPolling(true);
        window.dispatchEvent(new Event("noteslyt:sources-updated"));
      } catch (error) { setMessage(error instanceof Error ? error.message : "Update failed"); setPending(false); }
    }}>
      <label className="block">Current spelling<input required maxLength={100} value={heard} onChange={e => setHeard(e.target.value)} placeholder="Myra" className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-secondary)] p-2" /></label>
      <label className="block">Correct spelling<input required maxLength={100} value={correct} onChange={e => setCorrect(e.target.value)} placeholder="Mira" className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-secondary)] p-2" /></label>
      <button disabled={pending || !ready || !heard.trim() || !correct.trim()} className="rounded border border-[var(--border)] px-3 py-2 disabled:opacity-50">{pending ? "Updating…" : "Update spelling"}</button>
      <p role="status" className="text-[var(--text-secondary)]">{message}</p>
    </form>
  </details>;
}
