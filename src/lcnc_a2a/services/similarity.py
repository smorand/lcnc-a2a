"""Cosine similarity over float-vector embeddings (FR-015)."""

from __future__ import annotations

from collections.abc import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the cosine similarity of two equal-length float vectors.

    Returns ``0.0`` when either vector is empty, of mismatched length, or
    has zero norm; otherwise the dot product divided by both norms.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a_sq += x * x
        norm_b_sq += y * y
    if norm_a_sq == 0.0 or norm_b_sq == 0.0:
        return 0.0
    return float(dot / ((norm_a_sq**0.5) * (norm_b_sq**0.5)))


__all__ = ["cosine_similarity"]
