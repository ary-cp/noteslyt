import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import {
  DRIFT_EVENT,
  getEmbeddingStatus,
  getReembedStatus,
  getToken,
  startReembed,
} from "../../lib/api";
import type { EmbeddingStatus, ReembedJob } from "../../lib/types";
import { useAuth } from "../../lib/auth";
import { useToast } from "../../lib/toast";
import { Button, Spinner } from "../ui/Button";

/**
 * Re-embed banner: visible on every page while embedding drift is active.
 *
 * Vector routes (upload, chat, quiz generation, grading) return 409 in drift,
 * so without this the app would look mysteriously broken. The banner polls
 * `GET /system/settings/status` (30 s + on mount + immediately after any
 * drift-409) and offers the two fixes: re-embed (admin), or revert to the
 * previous model when the backend names one. Non-admins see the state and
 * are pointed at their admin. While a job runs it shows live progress.
 */
export function DriftBanner() {
  const { user, ready } = useAuth();
  const { push } = useToast();
  const [status, setStatus] = useState<EmbeddingStatus | null>(null);
  const [job, setJob] = useState<ReembedJob | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const jobTimer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setStatus(null);
      return;
    }
    try {
      setStatus(await getEmbeddingStatus());
    } catch {
      /* backend down — the request layer already toasts; stay silent */
    }
  }, []);

  useEffect(() => {
    if (!ready || !user) return;
    void refresh();
    const onEvent = () => void refresh();
    window.addEventListener(DRIFT_EVENT, onEvent);
    timer.current = window.setInterval(() => void refresh(), 30_000);
    return () => {
      window.removeEventListener(DRIFT_EVENT, onEvent);
      window.clearInterval(timer.current);
    };
  }, [ready, user, refresh]);

  const refreshJob = useCallback(async () => {
    try {
      const next = await getReembedStatus();
      setJob(next);
      if (next.state === "running") {
        jobTimer.current = window.setTimeout(() => void refreshJob(), 3000);
      } else {
        void refresh();
        if (next.state === "done") push("Re-embed complete", { tone: "success" });
        else if (next.state === "done_with_failures") {
          push("Re-embed finished with failures", { detail: next.message, tone: "warning" });
        } else if (next.state === "failed") {
          push("Re-embed failed", { detail: next.message, tone: "error" });
        }
      }
    } catch {
      /* keep last-known job state */
    }
  }, [push, refresh]);

  useEffect(() => () => window.clearTimeout(jobTimer.current), []);

  // Ask once on drift whether a job is already running (e.g. started elsewhere).
  useEffect(() => {
    if (status?.drift && job === null) void refreshJob();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.drift]);

  if (!status?.drift) return null;

  const isAdmin = user?.is_admin === true;
  const running = job?.state === "running";
  const progress = job && job.total > 0 ? Math.round((job.processed / job.total) * 100) : null;

  const onReembed = async (force: boolean) => {
    setBusy(true);
    try {
      const started = await startReembed(force);
      setJob(started);
      push("Re-embed started", { detail: "Reads stay available; uploads and chat resume when it lands.", tone: "info" });
      void refreshJob();
    } catch (e) {
      push("Could not start re-embed", { detail: e instanceof Error ? e.message : String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="alert"
      className="flex items-start gap-3 border-b border-[var(--warning-border,#7c5a00)] bg-[var(--warning-bg,#2b2305)] px-4 py-2.5 text-[13px] text-[var(--text-primary)]"
    >
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[var(--warning,#e3a008)]" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">Search is paused — the embedding model changed.</p>
        <p className="mt-0.5 break-words text-[var(--text-secondary)]">{status.detail}</p>
        {running && job ? (
          <div className="mt-1.5">
            <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
              <Spinner size={12} />
              <span>
                Re-embedding{progress !== null ? ` ${progress}%` : ""} — {job.processed}/{job.total}
                {job.message ? ` · ${job.message}` : ""}
              </span>
            </div>
            <div className="mt-1 h-1 overflow-hidden rounded bg-[var(--surface-secondary)]">
              <div
                className="h-full rounded bg-[var(--warning,#e3a008)] transition-[width]"
                style={{ width: `${progress ?? 5}%` }}
              />
            </div>
          </div>
        ) : (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <>
                <Button variant="primary" disabled={busy} loading={busy} onClick={() => void onReembed(false)}>
                  Re-embed now
                </Button>
                {status.revert_to && (
                  <span className="text-xs text-[var(--text-secondary)]">
                    or revert to <span className="font-mono">{status.revert_to}</span> in Settings → AI &amp; retrieval
                  </span>
                )}
              </>
            ) : (
              <p className="text-xs text-[var(--text-secondary)]">
                An admin can re-embed or revert in Settings. Reading and history keep working.
              </p>
            )}
          </div>
        )}
        {job && job.state !== "running" && job.state !== "idle" && job.errors.length > 0 && (
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Last job {job.state}: {job.errors.slice(0, 3).map((e) => e.filename).join(", ")}
            {job.errors.length > 3 ? ` +${job.errors.length - 3} more` : ""}
          </p>
        )}
      </div>
    </div>
  );
}
