/**
 * Anonymous learner identity.
 *
 * The learner model is keyed by user id. For the prototype we mint a random id
 * once per browser and keep it in localStorage — swap for real auth later and
 * nothing else changes.
 */

const KEY = "study-companion:user-id";

export function getUserId(): string {
  try {
    const existing = localStorage.getItem(KEY);
    if (existing) return existing;
    const fresh = `learner-${crypto.randomUUID().slice(0, 8)}`;
    localStorage.setItem(KEY, fresh);
    return fresh;
  } catch {
    return "learner-anon";
  }
}
