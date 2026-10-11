import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Pause, Volume2, VolumeX, Maximize, SkipBack, SkipForward } from "lucide-react";
import { formatDuration } from "../../lib/utils";

import { transcriptSegments } from "./transcript";

export default function MediaPane({ active, url, video, time, stopAt, seekKey, jumpTick, markdown, snippet }: {
  active: boolean; url: string; video: boolean; time: number | null; stopAt: number | null;
  seekKey: number; jumpTick: number; markdown: string; snippet?: string;
}) {
  const ref = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const player = useRef<HTMLDivElement>(null);
  const clipEnd = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [volume, setVolume] = useState(1);
  const [loopClip, setLoopClip] = useState(false);
  function togglePlayback() {
    const el = ref.current;
    if (!el) return;
    if (el.paused) void el.play().catch(() => setError("Playback could not start. Try again or download the source."));
    else el.pause();
  }
  function fullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    else void player.current?.requestFullscreen().catch(() => setError("Fullscreen is unavailable in this browser."));
  }
  function changeVolume(value: number) {
    setVolume(value);
    if (ref.current) { ref.current.volume = value; ref.current.muted = value === 0; }
  }
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [clip, setClip] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const segments = useMemo(() => transcriptSegments(markdown), [markdown]);
  const validEnd = time !== null && stopAt !== null && stopAt > time ? stopAt : null;

  function seek(value: number, play = false) {
    const el = ref.current;
    if (!el || el.readyState < 1) return;
    el.currentTime = Math.max(0, Math.min(value, Number.isFinite(el.duration) ? el.duration : value));
    setPosition(el.currentTime);
    if (play) void el.play().catch(() => undefined);
  }
  function fullPlayback() { clipEnd.current = null; setClip(false); }

  useEffect(() => {
    const el = ref.current;
    if (!el || time === null) { fullPlayback(); return; }
    const apply = () => {
      clipEnd.current = validEnd;
      setClip(validEnd !== null);
      seek(time, true);
    };
    if (el.readyState >= 1) apply();
    else el.addEventListener("loadedmetadata", apply, { once: true });
    return () => el.removeEventListener("loadedmetadata", apply);
  }, [url, time, validEnd, seekKey, jumpTick]);

  useEffect(() => { if (!active) ref.current?.pause(); }, [active]);

  const mediaProps = {
    ref, src: url, controls: false, preload: "metadata", className: "max-h-[40vh] w-full rounded-lg bg-black object-contain",
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onWaiting: () => setWaiting(true),
    onCanPlay: () => setWaiting(false),
    onPlaying: () => setWaiting(false),
    onEnded: () => setPlaying(false),
    onClick: togglePlayback,
    onDoubleClick: fullscreen,
    onLoadedMetadata: () => setDuration(Number.isFinite(ref.current?.duration) ? ref.current!.duration : 0),
    onError: () => setError("This browser could not play the file. Download the original to use another player."),
    onSeeking: () => {
      const current = ref.current?.currentTime ?? 0;
      if (clipEnd.current !== null && (current < (time ?? 0) - 0.1 || current > clipEnd.current + 0.1)) fullPlayback();
    },
    onTimeUpdate: () => {
      const el = ref.current;
      if (!el) return;
      setPosition(el.currentTime);
      if (clipEnd.current !== null && el.currentTime >= clipEnd.current) {
        if (loopClip && time !== null) { el.currentTime = time; return; }
        const end = clipEnd.current;
        fullPlayback(); el.pause(); el.currentTime = end;
      }
    },
  };
  return <div className="space-y-4">
    <div ref={player} className="media-player overflow-hidden rounded-xl border border-[var(--border)] bg-[#101216] p-3 text-white" tabIndex={0} aria-label="Media player" onKeyDown={(e) => {
      if ((e.target as HTMLElement).matches("input,select,button")) return;
      if (e.key === " " || e.key.toLowerCase() === "k") { e.preventDefault(); togglePlayback(); }
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); fullPlayback(); seek(position + (e.key === "ArrowLeft" ? -5 : 5)); }
      if (e.key.toLowerCase() === "f") fullscreen();
      if (e.key.toLowerCase() === "m") changeVolume(volume ? 0 : 1);
    }}>
      <div className="relative">
        {video ? <video {...mediaProps} playsInline /> : <audio {...mediaProps} />}
        {video && !playing && <button type="button" aria-label="Play video" onClick={togglePlayback} className="absolute left-1/2 top-1/2 flex h-14 w-14 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/30 bg-black/60 text-white shadow-lg"><Play size={24} fill="currentColor" /></button>}
        {waiting && <span role="status" className="absolute right-3 top-3 rounded bg-black/70 px-2 py-1 text-xs">Buffering…</span>}
      </div>
      {error && <p role="alert" className="p-2 text-sm text-[var(--danger)]">{error}</p>}
      <div className="relative mx-1 mt-3 h-5" title={time !== null ? `Highlighted citation starts at ${formatDuration(time)}` : "Seek through source"}>
        <div className="absolute inset-x-0 top-2 h-1 rounded bg-[var(--border)]" />
        {time !== null && duration > 0 && <div aria-label="Cited range" className="absolute top-1 h-3 rounded bg-[var(--accent)]/50" style={{ left: `${Math.min(time / duration, 1) * 100}%`, width: `${Math.max(0.5, (Math.min(validEnd ?? time + 1, duration) - Math.min(time, duration)) / duration * 100)}%` }} />}
        <input aria-label="Playback position" type="range" min={0} max={duration || 1} step={0.1} value={position} disabled={!duration}
          onChange={(e) => { fullPlayback(); seek(Number(e.target.value)); }} className="media-timeline absolute inset-0 w-full cursor-pointer" style={{ background: `linear-gradient(to right, #34d399 ${duration ? position / duration * 100 : 0}%, transparent 0)` }} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <button type="button" aria-label={playing ? "Pause playback" : "Play playback"} onClick={togglePlayback} className="rounded-lg bg-white/10 p-2 hover:bg-white/20">{playing ? <Pause size={18} /> : <Play size={18} />}</button>
        <span className="mr-auto font-mono text-white/75">{formatDuration(position)} / {formatDuration(duration)}</span>
        <button type="button" disabled={!duration} onClick={() => { fullPlayback(); seek(position - 10); }} className="rounded border border-[var(--border)] px-2 py-1" aria-label="Back ten seconds" title="Back 10 seconds"><SkipBack size={15} /></button>
        <button type="button" disabled={!duration} onClick={() => { fullPlayback(); seek(position + 10); }} className="rounded border border-[var(--border)] px-2 py-1" aria-label="Forward ten seconds" title="Forward 10 seconds"><SkipForward size={15} /></button>
        <button type="button" aria-label={volume ? "Mute" : "Unmute"} onClick={() => changeVolume(volume ? 0 : 1)} className="rounded p-1.5">{volume ? <Volume2 size={16} /> : <VolumeX size={16} />}</button>
        <input aria-label="Volume" title="Volume" type="range" min={0} max={1} step={.05} value={volume} onChange={(e) => changeVolume(Number(e.target.value))} className="w-16 accent-emerald-400" />
        <select aria-label="Playback speed" defaultValue="1" onChange={(e) => { if (ref.current) ref.current.playbackRate = Number(e.target.value); }} className="rounded border border-white/15 bg-[#101216] px-2 py-1">
          {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}
        </select>
        {video && <button type="button" aria-label="Fullscreen video" onClick={fullscreen} className="rounded p-1.5"><Maximize size={16} /></button>}
      </div>
      <p className="mt-2 text-[10px] text-white/40">Space / K: play · ← →: seek · M: mute{video ? " · F: fullscreen" : ""}</p>
    </div>
    {time !== null && <div className="rounded-lg border border-[var(--accent-border)] bg-[var(--accent-soft)] p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <strong className="mr-auto">Cited section · {formatDuration(time)}{validEnd !== null ? `–${formatDuration(validEnd)}` : ""}</strong>
        <button type="button" onClick={() => { clipEnd.current = validEnd; setClip(validEnd !== null); seek(time, true); }} className="rounded border border-[var(--accent-border)] px-2 py-1 text-xs">Replay section</button>
        {validEnd !== null && <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={loopClip} onChange={(e) => setLoopClip(e.target.checked)} /> Loop section</label>}
        {clip && <button type="button" onClick={fullPlayback} className="rounded border border-[var(--border)] px-2 py-1 text-xs">Continue full playback</button>}
      </div>
      {snippet && <p className="mt-2 whitespace-pre-wrap text-[var(--text-secondary)]">{snippet}</p>}
      {clip && <p className="mt-2 text-xs text-[var(--text-muted)]">Playback pauses at the end of this section.</p>}
    </div>}
    <section aria-label="Video source transcript">
      <h3 className="mb-2 text-sm font-semibold">Source transcript</h3>
      {segments.length > 0 ? <>
        <input aria-label="Search transcript" placeholder="Search transcript…" value={query} onChange={(e) => setQuery(e.target.value)} className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--input)] px-3 py-2 text-sm" />
        <div className="space-y-1">{segments.map((segment, index) => {
          if (!segment.text.toLowerCase().includes(query.toLowerCase())) return null;
          const end = segment.end ?? segments[index + 1]?.start ?? duration;
          const active = position >= segment.start && position < end;
          const cited = time !== null && segment.start < (validEnd ?? time + 0.1) && end > time;
          return <button type="button" key={`${segment.start}-${index}`} aria-current={active ? "true" : undefined}
            onClick={() => { fullPlayback(); seek(segment.start, true); }}
            className={`flex w-full gap-3 rounded-lg border p-3 text-left text-sm transition-colors ${active || cited ? "border-[var(--accent-border)] bg-[var(--accent-soft)]" : "border-transparent hover:bg-[var(--surface-secondary)]"}`}>
            <span className="shrink-0 font-mono text-xs text-[var(--accent)]">{formatDuration(segment.start)}</span>
            <span className="whitespace-pre-wrap">{segment.text}</span>
          </button>;
        })}</div>
        {query && !segments.some((segment) => segment.text.toLowerCase().includes(query.toLowerCase())) && <p className="text-sm text-[var(--text-muted)]">No matching passages.</p>}
      </> : <p className="text-sm text-[var(--text-muted)]">{markdown ? "No timestamped transcript is available. Open Text content to read the extracted source." : "Transcript will appear once transcription is available."}</p>}
    </section>
  </div>;
}
