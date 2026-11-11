"""Tension sequencer: deterministic selection and ordering (spec: Orchestration layer).

Picks 3-6 verified moments that fit the target length, then orders them like a cold open:
strongest hook first, rising intensity in the middle, the cliffhanger last.
"""
from __future__ import annotations

from .models import Moment

FADE = 0.25  # seconds of fade at each clip edge (kept inside the clip)
RESERVE_PER_CLIP = 5.0


def intensity(m: Moment) -> float:
    return m.scores.emotion * 0.5 + m.scores.surprise * 0.5


def select(moments: list[Moment], target: float, min_clips: int = 3, max_clips: int = 6,
           title_seconds: float = 0.0) -> list[Moment]:
    """Greedy by overall score, skipping clips that would blow the length budget (+10%)."""
    budget = target * 1.10 - title_seconds
    pool = sorted(moments, key=lambda m: m.overall, reverse=True)
    chosen: list[Moment] = []
    total = 0.0
    for m in pool:
        if len(chosen) >= max_clips:
            break
        # keep room for the clips still needed to reach min_clips (about 5 s each)
        reserve = max(0, min_clips - len(chosen) - 1) * RESERVE_PER_CLIP
        if total + m.duration > budget - reserve:
            continue
        chosen.append(m)
        total += m.duration
        if total >= target * 0.9 and len(chosen) >= min_clips:
            break
    # Too few clips (e.g. every candidate is long): fall back to the shortest strong ones.
    if len(chosen) < min_clips:
        extra = sorted((m for m in pool if m not in chosen), key=lambda m: m.duration)
        for m in extra:
            if len(chosen) >= min_clips or total + m.duration > budget:
                break
            chosen.append(m)
            total += m.duration
    return chosen


def order_for_tension(chosen: list[Moment]) -> list[Moment]:
    if len(chosen) <= 2:
        ordered = sorted(chosen, key=lambda m: -m.scores.hook)
        for i, m in enumerate(ordered):
            m.order, m.selected = i + 1, True
        return ordered
    rest = list(chosen)
    opener = max(rest, key=lambda m: (m.role == "opener", m.scores.hook + m.scores.clarity * 0.5))
    rest.remove(opener)
    closer = max(rest, key=lambda m: (m.role in ("closer", "climax"), intensity(m) + m.scores.hook * 0.3))
    rest.remove(closer)
    middle = sorted(rest, key=intensity)  # rising tension
    ordered = [opener, *middle, closer]
    for i, m in enumerate(ordered):
        m.order = i + 1
        m.selected = True
    return ordered


def build_cut(moments: list[Moment], target: float, min_clips: int = 3, max_clips: int = 6,
              title_seconds: float = 0.0) -> list[Moment]:
    for m in moments:
        m.selected, m.order = False, None
    return order_for_tension(select(moments, target, min_clips, max_clips, title_seconds))


def cut_length(cut: list[Moment], title_seconds: float = 0.0) -> float:
    return round(sum(m.duration for m in cut) + title_seconds, 2)
