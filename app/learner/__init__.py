"""
Learner model: Bayesian Knowledge Tracing.

The BKT math is stateless and source-agnostic, so it survived the move to
project-scoped mastery unchanged. Quiz generation and grading are rebuilt in
Phase F against `project_memory` (keyed by project, not document).
"""

from .bkt import BKTParams, label, update

__all__ = ["BKTParams", "label", "update"]