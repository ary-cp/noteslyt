export interface ExamClock {
  count: number;
  deadline: number;
  completed: boolean;
}

export function readExamClock(raw: string | null, count: number, now = Date.now()): ExamClock {
  try {
    const saved = JSON.parse(raw ?? "null");
    if (saved?.count === count && Number.isFinite(saved.deadline) && saved.deadline > 0 &&
        typeof saved.completed === "boolean") return saved;
  } catch { /* A malformed local entry must not prevent practice. */ }
  return { count, deadline: now + count * 120_000, completed: false };
}

export function examSecondsLeft(clock: ExamClock, now = Date.now()): number {
  return Math.max(0, Math.ceil((clock.deadline - now) / 1000));
}
