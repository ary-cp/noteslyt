export type ReviewGrade = "Again" | "Hard" | "Good" | "Easy";

export function advanceReview(queue: number[], grade: ReviewGrade): number[] {
  const [current, ...rest] = queue;
  if (current === undefined) return [];
  if (grade === "Again") rest.splice(Math.min(1, rest.length), 0, current);
  if (grade === "Hard") rest.splice(Math.min(3, rest.length), 0, current);
  return rest;
}

export function readReview(raw: string | null, count: number): number[] {
  const fresh = () => Array.from({ length: count }, (_, i) => i);
  try {
    const value = JSON.parse(raw ?? "null");
    if (value?.count !== count || !Array.isArray(value.queue)) return fresh();
    const queue: unknown[] = value.queue;
    if (queue.length > count || new Set(queue).size !== queue.length ||
        queue.some((n) => typeof n !== "number" || !Number.isInteger(n) || n < 0 || n >= count)) return fresh();
    return queue as number[];
  } catch { return fresh(); }
}
