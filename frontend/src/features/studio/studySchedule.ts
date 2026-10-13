/**
 * Heuristic revision planner (frontend-only, labelled as such in the output):
 * weakest topics first, revisits on expanding gaps, capped by minutes/day.
 * This schedules practice — it never claims to measure forgetting curves.
 */
export function buildSchedule(items: { name: string; p: number }[], examDateISO: string, mins: number, now = new Date()): string {
  if (!items.length) return `# Study schedule\n\n_Add sources first — topics appear here once indexing extracts them._`;
  const ordered = [...items].sort((a, b) => a.p - b.p);
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const end = new Date(`${examDateISO}T00:00:00`);
  let days = Math.round((end.getTime() - today.getTime()) / 86400000) + 1;
  if (!Number.isFinite(days) || days < 1) throw new Error("Choose an exam date today or later.");
  const capped = days > 30;
  days = Math.min(days, 30);
  const perDay = Math.max(1, Math.min(3, Math.round(mins / 30)));
  const perTopic = Math.max(10, Math.floor(mins / perDay));
  const tasks = ["Learn", "Practice", "Revise"];
  const queue = ordered.map((t, i) => ({ ...t, due: i < perDay ? 0 : 1, round: 0 }));
  const lines = [
    `# Study schedule`,
    ``,
    `_Heuristic plan to ${end.toLocaleDateString()} ${capped ? " (first 30 days)" : ""} — weakest topics first, revisits on expanding gaps (${perTopic} min each, ${mins} min/day)._`,
    ``,
    `| Day | Date | Focus |`,
    `| --- | --- | --- |`,
  ];
  for (let d = 0; d < days; d++) {
    const date = new Date(today);
    date.setDate(date.getDate() + d);
    const dueNow = queue
      .filter((q) => q.due <= d)
      .sort((a, b) => a.p - b.p || a.round - b.round)
      .slice(0, perDay);
    const focus = date.getTime() === end.getTime()
      ? "Exam day — light review only"
      : dueNow.length
        ? dueNow.map((q) => {
            const task = tasks[Math.min(q.round, tasks.length - 1)];
            q.round += 1;
            q.due = d + (q.round === 1 ? 2 : 5);
            q.p = Math.min(0.95, q.p + 0.15);
            return `${task}: ${q.name}`;
          }).join("; ")
        : "Rest or catch up";
    lines.push(`| ${d + 1} | ${date.toLocaleDateString()} | ${focus} |`);
  }
  return lines.join("\n");
}
