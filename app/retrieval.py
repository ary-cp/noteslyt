"""Bounded context selection without cutting evidence or citation labels in half."""
from __future__ import annotations

import math
import re
from collections import Counter

from .citations import format_context

_STOP = frozenset("a an the of in on at to for from with and or is are was were be been do does what which how why when who this that these those it its as by can should must under according document uploaded explain name".split())


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[^\W_]+(?:[.-][^\W_]+)*", text.casefold())
            if t not in _STOP and (len(t) > 1 or t.isdigit())}


def select_context(candidates: list, question: str, *, limit: int, budget: int) -> list:
    """Fuse semantic and lexical ranks, remove duplicate text, pack whole chunks.

    Lexical ranks use candidate-local IDF, helping precise names/section numbers
    without another model call. Candidates have already been tenant/source scoped.
    """
    if not candidates or limit <= 0 or budget <= 0:
        return []
    tokens = [_terms(c.text) for c in candidates]
    counts = Counter(t for words in tokens for t in words)
    query = _terms(question)
    scores = [sum(math.log(1 + len(candidates) / counts[t]) for t in words & query)
              for words in tokens]
    lexical = sorted(range(len(candidates)), key=lambda i: (-scores[i], i))
    ranks = {index: rank for rank, index in enumerate(lexical)}
    # Semantic search supplies the candidate pool; lexical ranks recover exact
    # terms that broad thematic matches can otherwise crowd out.
    order = sorted(range(len(candidates)), key=lambda i: -(1 / (60 + i) +
                   (2 / (60 + ranks[i]) if scores[i] else 0)))
    selected, seen, per_locator = [], set(), Counter()
    for i in order:
        c = candidates[i]
        fingerprint = (c.source_id, re.sub(r"\s+", " ", c.text).strip())
        locator = (c.source_id, c.locator_kind, c.locator_value)
        if fingerprint in seen or per_locator[locator] >= 2:
            continue
        proposed = [*selected, c]
        if len(format_context(proposed, numbered=True)) > budget:
            continue
        selected.append(c)
        seen.add(fingerprint)
        per_locator[locator] += 1
        if len(selected) == limit:
            break
    return selected
