import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Pause, Volume2, VolumeX, Maximize, Minimize, SkipBack, SkipForward, Headphones, Captions, PictureInPicture2, Search, SlidersHorizontal, ChevronDown } from "lucide-react";
import { formatDuration } from "../../lib/utils";

import { transcriptSegments } from "./transcript";

export default function MediaPane({ title, active, url, video, time, stopAt, seekKey, jumpTick, markdown, snippet }: {
  title: string; active: boolean; url: string; video: boolean; time: number | null; stopAt: number | null;
  seekKey: number; jumpTick: number; markdown: string; snippet?: string;
}) {
  const ref = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const player = useRef<HTMLDivElement>(null);
  const previousVolume = useRef(1);
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const update = () => {
      const entered = document.fullscreenElement === player.current;
      setIsFullscreen(entered);
      if (entered) player.current?.focus({ preventScroll: true });
    };
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);
  const activeRef = useRef(active);
  activeRef.current = active;
  const transcript = useRef<HTMLDivElement>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(true);
  const [remaining, setRemaining] = useState(false);
  const [captions, setCaptions] = useState(false);
  const [follow, setFollow] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [buffered, setBuffered] = useState(0);
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
  async function pictureInPicture() {
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await ref.current?.requestPictureInPicture();
    } catch { setError("Picture-in-picture is unavailable for this video."); }
  }
  function toggleMute() {
    if (volume > 0) { previousVolume.current = volume; changeVolume(0); }
    else changeVolume(previousVolume.current || 1);
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

  const activeIndex = segments.findIndex((segment, i) => position >= segment.start && position < (segment.end ?? segments[i + 1]?.start ?? duration));
  useEffect(() => {
    if (!follow || query) return;
    const box = transcript.current;
    const row = box?.querySelector<HTMLElement>('[aria-current="true"]');
    if (box && row) box.scrollTo({ top: row.offsetTop - box.offsetTop - box.clientHeight / 3, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [activeIndex, follow, query]);

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
      seek(time, activeRef.current);
    };
    if (el.readyState >= 1) apply();
    else el.addEventListener("loadedmetadata", apply, { once: true });
    return () => el.removeEventListener("loadedmetadata", apply);
  }, [url, time, validEnd, seekKey, jumpTick]);

  useEffect(() => { if (!active) ref.current?.pause(); }, [active]);

  const mediaProps = {
    ref, src: url, controls: false, preload: "metadata", className: "max-h-[40vh] w-full rounded-lg bg-black object-contain",
    onPlay: () => { setPlaying(true); setError(""); },
    onVolumeChange: () => setVolume(ref.current?.muted ? 0 : ref.current?.volume ?? 1),
    onRateChange: () => setSpeed(ref.current?.playbackRate ?? 1),
    onProgress: () => { const el = ref.current; if (el?.buffered.length) setBuffered(el.buffered.end(el.buffered.length - 1)); },
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
      const target = e.target as HTMLElement;
      if (e.altKey || e.ctrlKey || e.metaKey || target.closest("input:not([type=range]),select,textarea,[contenteditable=true]")) return;
      // Keep native slider adjustment, but don't lose player shortcuts after seeking.
      if (target.closest('input[type=range]') && e.key.startsWith("Arrow")) return;
      if (e.key === " " && target.closest("button")) return;
      if (e.repeat) return;
      if (e.key === " " || e.key.toLowerCase() === "k") { e.preventDefault(); togglePlayback(); }
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); fullPlayback(); seek(position + (e.key === "ArrowLeft" ? -5 : 5)); }
      if (video && e.key.toLowerCase() === "f") { e.preventDefault(); fullscreen(); }
      if (e.key.toLowerCase() === "m") { e.preventDefault(); toggleMute(); }
    }}>
      {!video && <div className="audio-cover">
        <span className={`audio-disc ${playing ? "is-playing" : ""}`}><Headphones size={32} /></span>
        <span className="text-[10px] font-semibold uppercase tracking-[.2em] text-emerald-300">Audio source</span>
        <h3 className="max-w-full break-words text-center text-base font-semibold">{title}</h3>
        <span className="font-mono text-2xl tabular-nums text-white/90">{formatDuration(position)} <span className="text-sm text-white/40">/ {formatDuration(duration)}</span></span>
        <p className="text-xs text-white/50">{waiting ? "Buffering…" : playing ? "Now playing" : "Ready to listen"}</p>
      </div>}
      <div className="media-stage relative">
        {video ? <video {...mediaProps} playsInline /> : <audio {...mediaProps} className="hidden" />}
        {video && captions && activeIndex >= 0 && <div className="pointer-events-none absolute inset-x-3 bottom-3 text-center"><span className="inline-block max-h-28 overflow-hidden rounded-md bg-black/80 px-3 py-2 text-sm leading-relaxed text-white">{segments[activeIndex].text}</span></div>}
        {video && !playing && <button type="button" aria-label="Play video" onClick={togglePlayback} className="absolute left-1/2 top-1/2 flex h-14 w-14 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/30 bg-black/60 text-white shadow-lg"><Play size={24} fill="currentColor" /></button>}
        {waiting && <span role="status" className="absolute right-3 top-3 rounded bg-black/70 px-2 py-1 text-xs">Buffering…</span>}
      </div>
      <div className="media-controls">
      {error && <p role="alert" className="p-2 text-sm text-[var(--danger)]">{error}</p>}
      <div className="relative mx-1 mt-3 h-5" title={time !== null ? `Highlighted citation starts at ${formatDuration(time)}` : "Seek through source"}>
        <div className="absolute inset-x-0 top-2 h-1 rounded bg-[var(--border)]" />
        {duration > 0 && <div className="absolute left-0 top-2 h-1 rounded bg-white/20" style={{ width: `${Math.min(100, buffered / duration * 100)}%` }} />}
        {time !== null && duration > 0 && <div aria-label="Cited range" className="absolute top-1 h-3 rounded bg-[var(--accent)]/50" style={{ left: `${Math.min(time / duration, 1) * 100}%`, width: `${Math.max(0.5, (Math.min(validEnd ?? time + 1, duration) - Math.min(time, duration)) / duration * 100)}%` }} />}
        <input aria-label="Playback position" aria-valuetext={`${formatDuration(position)} of ${formatDuration(duration)}`} type="range" min={0} max={duration || 1} step={0.1} value={position} disabled={!duration}
          onChange={(e) => { fullPlayback(); seek(Number(e.target.value)); }} className="media-timeline absolute inset-0 w-full cursor-pointer" style={{ background: `linear-gradient(to right, #34d399 ${duration ? position / duration * 100 : 0}%, transparent 0)` }} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <button type="button" aria-label={playing ? "Pause playback" : "Play playback"} onClick={togglePlayback} className="rounded-lg bg-white/10 p-2 hover:bg-white/20">{playing ? <Pause size={18} /> : <Play size={18} />}</button>
        <button type="button" aria-label={remaining ? "Show elapsed time" : "Show remaining time"} onClick={() => setRemaining((v) => !v)} className="mr-auto rounded px-1 font-mono tabular-nums text-white/65">{remaining ? `−${formatDuration(Math.max(0, duration - position))}` : formatDuration(position)} / {formatDuration(duration)}</button>
        <button type="button" disabled={!duration} onClick={() => { fullPlayback(); seek(position - 10); }} className="rounded border border-[var(--border)] px-2 py-1" aria-label="Back ten seconds" title="Back 10 seconds"><SkipBack size={15} /></button>
        <button type="button" disabled={!duration} onClick={() => { fullPlayback(); seek(position + 10); }} className="rounded border border-[var(--border)] px-2 py-1" aria-label="Forward ten seconds" title="Forward 10 seconds"><SkipForward size={15} /></button>
        <button type="button" aria-label={volume ? "Mute" : "Unmute"} onClick={() => toggleMute()} className="rounded p-1.5">{volume ? <Volume2 size={16} /> : <VolumeX size={16} />}</button>
        <button type="button" aria-label="Playback settings" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((v) => !v)} className="rounded p-1.5"><SlidersHorizontal size={16} /></button>
        {video && <button type="button" aria-label="Toggle captions" aria-pressed={captions} disabled={!segments.length} onClick={() => setCaptions((v) => !v)} className="rounded p-1.5 disabled:opacity-30"><Captions size={17} /></button>}
        {video && document.pictureInPictureEnabled && <button type="button" aria-label="Picture in picture" onClick={() => void pictureInPicture()} className="rounded p-1.5"><PictureInPicture2 size={17} /></button>}
        {video && <button type="button" aria-label={isFullscreen ? "Exit video fullscreen" : "Fullscreen video"} onClick={fullscreen} className="rounded p-1.5">{isFullscreen ? <Minimize size={16} /> : <Maximize size={16} />}</button>}
      </div>
      {settingsOpen && <div className="viewer-reveal mt-3 space-y-3 border-t border-white/10 pt-3">
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs"><span className="text-white/50">Volume & speed</span>
        <input aria-label="Volume" title="Volume" type="range" min={0} max={1} step={.05} value={volume} onChange={(e) => changeVolume(Number(e.target.value))} className="w-16 accent-emerald-400" />
        <select aria-label="Playback speed" value={speed} onChange={(e) => { if (ref.current) ref.current.playbackRate = Number(e.target.value); }} className="rounded border border-white/15 bg-[#101216] px-2 py-1">
          {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}
        </select>
        </div>
        <p className="text-[10px] text-white/40">Space / K: play · ← →: seek · M: mute{video ? " · F: fullscreen" : ""}</p>
      </div>}
      </div>
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
    <section aria-label="Source transcript" className="rounded-xl border border-[var(--border)] p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><button type="button" aria-expanded={transcriptOpen} onClick={() => setTranscriptOpen((v) => !v)} className="flex items-center gap-2 text-sm font-medium">Transcript <span className="text-xs text-[var(--text-muted)]">{segments.length}</span><ChevronDown size={14} className={`transition-transform ${transcriptOpen ? "" : "-rotate-90"}`} /></button>
      <button type="button" aria-pressed={follow} onClick={() => setFollow((v) => !v)} className={`rounded-full border px-2 py-1 text-[11px] ${follow ? "border-[var(--accent-border)] text-[var(--accent)]" : "border-[var(--border)] text-[var(--text-muted)]"}`}>Follow playback</button></div>
      <div hidden={!transcriptOpen}>
      {segments.length > 0 ? <>
        <label className="relative block"><Search size={14} className="absolute left-3 top-3 text-[var(--text-muted)]" /><input aria-label="Search transcript" placeholder="Search transcript…" value={query} onChange={(e) => setQuery(e.target.value)} className="mb-2 w-full rounded-lg border border-[var(--border)] bg-[var(--input)] py-2 pr-3 pl-9 text-sm" /></label>
        <div ref={transcript} className="relative max-h-[360px] space-y-1 overflow-y-auto">{segments.map((segment, index) => {
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
      </div>
    </section>
  </div>;
}
