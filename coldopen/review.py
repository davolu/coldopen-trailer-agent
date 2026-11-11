"""Human review step (spec: Human review node, Ch49).

Writes the ranked candidates and the proposed cut as moments.json plus a readable moments.md.
The creator approves the exact list (optionally re-ordered or trimmed to chosen IDs);
the approval is bound to a digest of that list, so any later edit needs a new approval.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config import Settings
from .guards import GuardError, cut_digest, make_approval
from .models import Approval, Moment


def _fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def write_review(job: Path, candidates: list[Moment], cut: list[Moment], meta: dict) -> None:
    (job / "candidates.json").write_text(json.dumps([m.model_dump() for m in candidates], indent=2))
    (job / "moments.json").write_text(json.dumps([m.model_dump() for m in cut], indent=2))
    total = sum(m.duration for m in cut)
    lines = [
        f"# ColdOpen review: {meta.get('title', '')}",
        "",
        f"Source length {_fmt(meta.get('duration', 0))}. Target {meta.get('target_seconds')}s. "
        f"Proposed cut: {len(cut)} clips, {total:.1f}s. Digest `{cut_digest(cut)[:12]}`.",
        "",
        "Approve as is: `coldopen approve <job_dir>`. Choose your own clips and order: "
        "`coldopen approve <job_dir> --keep m0012-0014,m0040-0041,...`. Then `coldopen render <job_dir>`.",
        "",
        "## Proposed cut (in trailer order)",
        "",
        "| # | id | time | len | hook | emo | surp | clar | overall | role | line |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in cut:
        s = m.scores
        lines.append(f"| {m.order} | {m.id} | {_fmt(m.start)}-{_fmt(m.end)} | {m.duration:.1f}s | {s.hook:g} | {s.emotion:g} | "
                     f"{s.surprise:g} | {s.clarity:g} | {m.overall:.2f} | {m.role} | {m.text[:140]} |")
    lines += ["", "## All verified candidates (ranked)", ""]
    for m in candidates:
        lines.append(f"- **{m.id}** {_fmt(m.start)}-{_fmt(m.end)} ({m.duration:.1f}s) overall {m.overall:.2f}: "
                     f"*{m.title}*. {m.reason}\n  > {m.text}")
    (job / "moments.md").write_text("\n".join(lines) + "\n")


def load_moments(job: Path, name: str = "moments.json") -> list[Moment]:
    return [Moment.model_validate(x) for x in json.loads((job / name).read_text())]


def load_approval(job: Path) -> Optional[Approval]:
    p = job / "approval.json"
    return Approval.model_validate_json(p.read_text()) if p.exists() else None


def approve(job: Path | str, settings: Settings, keep: Optional[list[str]] = None, approved_by: str = "creator",
            mode: str = "human") -> Approval:
    """Approve the proposed cut, or an exact list of candidate IDs in the given order."""
    job = Path(job)
    if keep:
        pool = {m.id: m for m in load_moments(job, "candidates.json")}
        missing = [k for k in keep if k not in pool]
        if missing:
            raise GuardError("NOT_APPROVED", f"unknown moment ids: {', '.join(missing)}")
        cut = [pool[k] for k in keep]
        for i, m in enumerate(cut):
            m.order, m.selected = i + 1, True
        (job / "moments.json").write_text(json.dumps([m.model_dump() for m in cut], indent=2))
    else:
        cut = load_moments(job)
    approval = make_approval(cut, approved_by, mode, settings.budgets.approval_ttl_hours)
    (job / "approval.json").write_text(approval.model_dump_json(indent=2))
    return approval
