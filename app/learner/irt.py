"""Item-Response-Theory ability estimate (1PL / Rasch, BKT companion).

BKT tracks per-topic P(learned); IRT tracks one global number per learner:
``ability`` (theta, in logits).  A question of difficulty ``d`` in [0, 1] is
mapped to ``b = (d - 0.5) * 4`` logits, then::

    P(correct | theta, b) = sigmoid(theta - b)

After each graded answer we take one small gradient step on the log-loss::

    theta <- theta + lr * (correct - P)

``lr`` defaults to 0.35 — large enough to move within a quiz, small enough
that one lucky guess cannot teleport a beginner to advanced.  Ability is
clamped to [-3, 3] (≈ 5th–95th percentile in logit space).

Pure functions only: persistence lives in ``user_profiles.irt_ability`` via
``ProfileRepo``.  No numpy, no new deps.
"""

from __future__ import annotations

import math

MIN_THETA = -3.0
MAX_THETA = 3.0


def difficulty_to_logit(difficulty: float) -> float:
    """Map a 0..1 difficulty to a Rasch item difficulty in logits."""
    try:
        d = min(max(float(difficulty), 0.0), 1.0)
    except (TypeError, ValueError):
        d = 0.5
    return (d - 0.5) * 4.0


def prob_correct(ability: float, difficulty: float) -> float:
    """P(correct) for a learner of ``ability`` on an item of ``difficulty``."""
    try:
        theta = float(ability)
    except (TypeError, ValueError):
        theta = 0.0
    b = difficulty_to_logit(difficulty)
    z = theta - b
    # Numerically stable sigmoid.
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def update_ability(ability: float, correct: bool, difficulty: float, lr: float = 0.35) -> float:
    """One gradient step toward the observed outcome, clamped to [-3, 3]."""
    try:
        theta = float(ability)
    except (TypeError, ValueError):
        theta = 0.0
    observed = 1.0 if correct else 0.0
    expected = prob_correct(theta, difficulty)
    theta = theta + lr * (observed - expected)
    return round(min(max(theta, MIN_THETA), MAX_THETA), 4)


def ability_label(ability: float) -> str:
    """Human band for the UI (mirrors BKT's weak/developing/strong)."""
    try:
        theta = float(ability)
    except (TypeError, ValueError):
        theta = 0.0
    if theta < -0.5:
        return "weak"
    if theta < 0.8:
        return "developing"
    return "strong"
