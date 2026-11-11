"""Deterministic guardrails (spec layer "Policy and human approval", Ch48/Ch49).

Every function here denies by default: success has to be earned.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .config import Budgets, Settings
from .models import Approval, Moment, MomentProposal, Scores, Sentence


class GuardError(Exception):
    """Raised when a guard denies. Carries the stop reason to report."""

    def __init__(self, stop_reason: str, message: str):
        super().__init__(message)
        self.stop_reason = stop_reason


# ---------------------------------------------------------------- input guard
def check_source(source: str, settings: Settings) -> dict:
    """Allow a YouTube URL on the host allowlist or an existing local video file."""
    source = (source or "").strip()
    if not source:
        raise GuardError("REJECTED_INPUT", "empty source")
    if source.startswith(("http://", "https://")):
        host = (urlparse(source).hostname or "").lower()
        if host not in settings.allowed_hosts:
            raise GuardError("REJECTED_INPUT", f"host not allowed: {host or '?'} (YouTube only)")
        return {"kind": "url", "value": source}
    path = Path(source).expanduser()
    if not path.is_file():
        raise GuardError("REJECTED_INPUT", f"file not found: {path}")
    if path.suffix.lower() not in settings.video_exts:
        raise GuardError("REJECTED_INPUT", f"unsupported file type: {path.suffix}")
    size_mb = path.stat().st_size / 1e6
    if size_mb > settings.budgets.max_file_mb:
        raise GuardError("REJECTED_INPUT", f"file too large: {size_mb:.0f} MB")
    return {"kind": "file", "value": str(path.resolve())}


# --------------------------------------------------------------- budget guard
def check_duration(seconds: float, budgets: Budgets) -> None:
    if seconds <= 0:
        raise GuardError("REJECTED_INPUT", "source has no duration")
    if seconds / 60 > budgets.max_source_minutes:
        raise GuardError("BUDGET_EXCEEDED", f"source is {seconds/60:.0f} min; limit {budgets.max_source_minutes:.0f} min")


def check_llm_budget(calls: int, input_tokens: int, cost_usd: float, budgets: Budgets) -> None:
    if calls >= budgets.max_llm_calls:
        raise GuardError("BUDGET_EXCEEDED", f"LLM call budget reached ({budgets.max_llm_calls})")
    if input_tokens >= budgets.max_llm_input_tokens:
        raise GuardError("BUDGET_EXCEEDED", f"LLM token budget reached ({budgets.max_llm_input_tokens})")
    if cost_usd >= budgets.max_cost_usd:
        raise GuardError("BUDGET_EXCEEDED", f"cost budget reached (${budgets.max_cost_usd:.2f})")


# ------------------------------------------------------------ moment verifier
WEIGHTS = {"hook": 0.35, "surprise": 0.25, "emotion": 0.2, "clarity": 0.2}


def overall_score(s: Scores) -> float:
    return round(sum(getattr(s, k) * w for k, w in WEIGHTS.items()), 2)


def verify_proposals(
    proposals: Iterable[MomentProposal],
    sentences: list[Sentence],
    min_len: float = 3.0,
    max_len: float = 21.0,  # the miner prompt asks for <= 20 s; code enforces it
    pad_start: float = 0.15,
    pad_end: float = 0.35,
) -> tuple[list[Moment], list[dict]]:
    """Turn model proposals into verified moments, or reject them with a reason.

    The model may only cite sentence IDs; timestamps are looked up here, so a clip can never
    start or end mid-sentence or point outside the source (spec trust boundary 3).
    """
    by_id = {s.id: s for s in sentences}
    accepted: list[Moment] = []
    rejected: list[dict] = []
    # strongest first, so "overlaps a stronger moment" is literally true
    for p in sorted(proposals, key=lambda p: overall_score(p.scores), reverse=True):
        a, b = by_id.get(p.start_sentence), by_id.get(p.end_sentence)
        if a is None or b is None:
            rejected.append({"proposal": p.model_dump(), "why": "sentence id does not exist"})
            continue
        if b.id < a.id:
            rejected.append({"proposal": p.model_dump(), "why": "end before start"})
            continue
        # breathing room at both edges, never bleeding into the neighbouring sentence
        prev_end = by_id[a.id - 1].end if (a.id - 1) in by_id else 0.0
        next_start = by_id[b.id + 1].start if (b.id + 1) in by_id else b.end + pad_end
        start = max(prev_end, a.start - pad_start, 0.0)
        end = max(b.end, min(next_start, b.end + pad_end))
        dur = end - start
        if dur < min_len or dur > max_len:
            rejected.append({"proposal": p.model_dump(), "why": f"duration {dur:.1f}s outside {min_len}-{max_len}s"})
            continue
        if any(not (b.id < m.start_sentence or a.id > m.end_sentence) for m in accepted):
            rejected.append({"proposal": p.model_dump(), "why": "overlaps a stronger moment"})
            continue
        text = " ".join(by_id[i].text for i in range(a.id, b.id + 1) if i in by_id)
        accepted.append(
            Moment(
                id=f"m{a.id:04d}-{b.id:04d}",
                start=round(start, 3),
                end=round(end, 3),
                start_sentence=a.id,
                end_sentence=b.id,
                text=text,
                title=p.title,
                reason=p.reason,
                role=p.role,
                scores=p.scores,
                overall=overall_score(p.scores),
            )
        )
    accepted.sort(key=lambda m: m.overall, reverse=True)
    return accepted, rejected


# ------------------------------------------------------- approval and render gate
def cut_digest(moments: list[Moment]) -> str:
    """SHA-256 over the exact ordered cut: ids, spans and order. Any edit changes it."""
    payload = [[m.id, round(m.start, 3), round(m.end, 3)] for m in moments]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def make_approval(moments: list[Moment], approved_by: str, mode: str, ttl_hours: int) -> Approval:
    now = datetime.now(timezone.utc)
    return Approval(
        digest=cut_digest(moments),
        approved_by=approved_by,
        mode=mode,  # type: ignore[arg-type]
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(hours=ttl_hours)).isoformat(),
        moment_ids=[m.id for m in moments],
    )


def check_render_gate(moments: list[Moment], approval: Approval | None) -> None:
    """Render only an approved, unexpired, digest-matched cut. Deny otherwise."""
    if approval is None:
        raise GuardError("NOT_APPROVED", "no approval.json: review and approve the moments first")
    if not moments:
        raise GuardError("NOT_APPROVED", "approved cut is empty")
    if approval.digest != cut_digest(moments):
        raise GuardError("NOT_APPROVED", "approval does not match the current cut (it was edited after approval)")
    if datetime.fromisoformat(approval.expires_at) < datetime.now(timezone.utc):
        raise GuardError("NOT_APPROVED", "approval expired: approve again")
