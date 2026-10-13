"""
Bayesian Knowledge Tracing (Corbett & Anderson, 1995).

For every (student, topic) we keep ONE number: P(L) — the probability that the
student has *learned* the topic. Each answer updates that belief in two steps:

  1. Bayesian evidence update — how surprising was this answer?
       correct :  P(L|obs) = P(L)(1-slip) / [ P(L)(1-slip) + (1-P(L)) guess ]
       wrong   :  P(L|obs) = P(L) slip     / [ P(L) slip     + (1-P(L))(1-guess) ]
  2. Learning transition — practice itself teaches:
       P(L)' = P(L|obs) + (1 - P(L|obs)) * transit

Parameters (all in .env):
  p_init    prior that the topic is already known before any question
  p_transit chance of learning the topic from one attempt
  p_slip    chance of a wrong answer despite knowing the topic
  p_guess   chance of a right answer without knowing (0.25 for 4-option MCQ)

It is tiny, interpretable and fully explainable on a slide — which is exactly
why it beats a black-box model in a hackathon pitch.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BKTParams:
    p_init: float = 0.30
    p_transit: float = 0.15
    p_slip: float = 0.10
    p_guess: float = 0.25


def calibrate(qtype: str, n_options: int, difficulty: float, params: BKTParams) -> tuple[float, float]:
    """Per-item evidence strength as an (guess, slip) pair.

    Format sets the guess rate: an MCQ is guessable at 1-in-N, while a
    constructed response is not (a fraction of the base guess rate).
    Difficulty tempers the slip rate: a hard item missed says less about
    the learner than an easy one, so its negative evidence is discounted.
    At difficulty 0.5 with 4-option MCQ this reproduces the base params
    exactly, so existing histories keep their meaning."""
    try:
        difficulty = min(max(float(difficulty), 0.0), 1.0)
    except (TypeError, ValueError):
        difficulty = 0.5
    if (qtype or '').strip().lower() == 'mcq':
        guess = 1.0 / max(2, int(n_options or 4))
    else:
        guess = round(params.p_guess * 0.4, 4)
    slip = round(min(max(params.p_slip + 0.15 * (difficulty - 0.5), 0.02), 0.5), 4)
    return guess, slip


def update(
    p_mastery: float,
    correct: bool,
    params: BKTParams,
    *,
    guess: float | None = None,
    slip: float | None = None,
) -> float:
    """Return the posterior P(learned) after observing one answer."""
    p = min(max(p_mastery, 1e-6), 1 - 1e-6)           # keep the odds finite
    guess = params.p_guess if guess is None else min(max(guess, 1e-6), 1 - 1e-6)
    slip = params.p_slip if slip is None else min(max(slip, 1e-6), 1 - 1e-6)
    if correct:
        num = p * (1 - slip)
        den = num + (1 - p) * guess
    else:
        num = p * slip
        den = num + (1 - p) * (1 - guess)
    posterior = num / den if den else p
    learned = posterior + (1 - posterior) * params.p_transit
    return round(min(max(learned, 0.0), 1.0), 4)


def label(p_mastery: float) -> str:
    """Human-readable band for the UI."""
    if p_mastery < 0.45:
        return "weak"
    if p_mastery < 0.80:
        return "developing"
    return "strong"
