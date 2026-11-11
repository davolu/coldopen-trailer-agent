"""ColdOpen pipeline: the code-owned workflow (spec: Orchestration layer, Ch24).

prepare(): guard -> ingest -> transcribe -> mine (Claude) -> verify -> sequence -> pause for review
render():  render gate -> ffmpeg 16:9 and 9:16 -> output check -> manifest
Each stage is checkpointed in the job directory, so review can pause and resume for free.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Optional

from .config import Settings
from .guards import GuardError, check_render_gate, check_source, verify_proposals
from .ingest import ingest
from .miner import ClaudeMiner
from .models import MomentProposal, StopReason, TrailerRequest, Transcript
from .render import TITLE_SECONDS, check_output, render_trailer
from .review import approve, load_approval, load_moments, write_review
from .proc import ToolError
from .runlog import RunLog
from .sequencer import build_cut, cut_length
from .transcribe import transcribe

Progress = Optional[Callable[[str], None]]


def job_id(source_key: str, req: TrailerRequest) -> str:
    knobs = json.dumps([req.target_seconds, req.min_clips, req.max_clips], separators=(",", ":"))
    return f"{source_key}-{hashlib.sha256(knobs.encode()).hexdigest()[:6]}"


def _say(progress: Progress, msg: str) -> None:
    if progress:
        progress(msg)


def prepare(req: TrailerRequest, settings: Settings, progress: Progress = None, miner=None, fresh: bool = False,
            remine: bool = False) -> dict:
    """Run everything up to the human review. Returns a result dict with a stop_reason.

    fresh: rebuild the review from saved proposals (no model call). remine: ask the model again.
    """
    fresh = fresh or remine
    t_start = time.time()
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        source = check_source(req.source, settings)
    except GuardError as exc:
        return {"stop_reason": exc.stop_reason, "error": str(exc)}

    tmp_log = RunLog(settings.out_dir / "_last_rejections.jsonl")
    try:
        _say(progress, "Downloading / loading the video")
        media = ingest(source, settings)
    except GuardError as exc:
        tmp_log.event("ingest", "denied", error=str(exc))
        return {"stop_reason": exc.stop_reason, "error": str(exc)}

    job = settings.out_dir / job_id(media["key"], req)
    job.mkdir(parents=True, exist_ok=True)
    log = RunLog(job / "events.jsonl")
    (job / "request.json").write_text(req.model_dump_json(indent=2))
    log.event("ingest", "ok", source_kind=source["kind"], duration=media["duration"], cached=media.get("cached"))

    if (job / "moments.json").exists() and not fresh:
        _say(progress, "Reusing saved moments (checkpoint)")
        return _summary(job, media, StopReason.AWAITING_APPROVAL, log, t_start)

    with log.stage("transcribe") as info:
        _say(progress, "Transcribing with word-level timestamps")
        transcript, tinfo = transcribe(media, settings)
        info.update(tinfo)
        log.add_cost(tinfo.get("cost_usd", 0.0))
    (job / "transcript_ref.json").write_text(json.dumps({"path": str(Path(media["path"]).parent / "transcript.json")}))

    try:
        with log.stage("mine") as info:
            cached = job / "proposals.json"
            if cached.exists() and not remine:  # checkpoint: never pay twice for the same request
                proposals = [MomentProposal.model_validate(p) for p in json.loads(cached.read_text())]
                info.update(cached=True)
            else:
                miner = miner or ClaudeMiner(settings)
                proposals = miner.mine(transcript, media.get("title", ""), log, on_progress=progress,
                                       target=req.target_seconds)
                cached.write_text(json.dumps([p.model_dump() for p in proposals], indent=2))
            info.update(proposals=len(proposals))
        with log.stage("verify") as info:
            _say(progress, "Verifying timestamps and sentence boundaries")
            candidates, rejected = verify_proposals(proposals, transcript.sentences)
            info.update(accepted=len(candidates), rejected=len(rejected), reasons=[r["why"] for r in rejected])
        if len(candidates) < 1:
            raise GuardError("NO_GOOD_MOMENTS", "no proposal survived verification")
        with log.stage("sequence") as info:
            title_s = TITLE_SECONDS if req.title else 0.0
            cut = build_cut(candidates, req.target_seconds, req.min_clips, req.max_clips, title_s)
            info.update(clips=len(cut), cut_seconds=cut_length(cut, title_s))
    except GuardError as exc:
        log.event("stop", exc.stop_reason, error=str(exc))
        return {"stop_reason": exc.stop_reason, "error": str(exc), "job_dir": str(job)}

    meta = {"title": media.get("title"), "duration": media["duration"], "target_seconds": req.target_seconds}
    write_review(job, candidates, cut, meta)
    log.event("review", "awaiting", cost_usd=log.cost_usd, llm_input_tokens=log.llm_input_tokens,
              llm_output_tokens=log.llm_output_tokens)
    ledger = ledger_from_events(job / "events.jsonl")
    ledger["prepare_seconds"] = round(time.time() - t_start, 1)
    (job / "ledger.json").write_text(json.dumps(ledger, indent=2))
    return _summary(job, media, StopReason.AWAITING_APPROVAL, log, t_start)


def ledger_from_events(path: Path) -> dict:
    """Total spend for the job across every run, derived from the event log (never double-counted)."""
    tot = {"cost_usd": 0.0, "whisper_usd": 0.0, "llm_calls": 0, "llm_input_tokens": 0, "llm_output_tokens": 0}
    from .config import get_settings

    prices = get_settings().prices
    for line in path.read_text().splitlines():
        e = json.loads(line)
        if e["stage"] == "transcribe" and e["status"] == "ok":
            tot["whisper_usd"] += e.get("cost_usd", 0.0)
        if e["stage"] == "mine_call" and e["status"] == "ok":
            tot["llm_calls"] += 1
            tot["llm_input_tokens"] += e.get("input_tokens", 0)
            tot["llm_output_tokens"] += e.get("output_tokens", 0)
    llm = tot["llm_input_tokens"] / 1e6 * prices.llm_input_per_mtok + tot["llm_output_tokens"] / 1e6 * prices.llm_output_per_mtok
    tot["cost_usd"] = round(tot["whisper_usd"] + llm, 4)
    return tot


def _summary(job: Path, media: dict, reason: StopReason, log: RunLog, t_start: float) -> dict:
    cut = load_moments(job)
    return {"stop_reason": reason.value, "job_dir": str(job), "source": media, "clips": len(cut),
            "cut_seconds": cut_length(cut), "candidates": len(load_moments(job, "candidates.json")),
            "review_file": str(job / "moments.md"), "seconds": round(time.time() - t_start, 1)}


def render(job: Path, settings: Settings, progress: Progress = None) -> dict:
    job = Path(job).resolve()
    log = RunLog(job / "events.jsonl")
    req = TrailerRequest.model_validate_json((job / "request.json").read_text())
    cut = load_moments(job)
    try:
        check_render_gate(cut, load_approval(job))  # deny by default (spec trust boundary 3)
    except GuardError as exc:
        log.event("render_gate", "denied", error=str(exc))
        return {"stop_reason": exc.stop_reason, "error": str(exc), "job_dir": str(job)}
    log.event("render_gate", "ok", clips=len(cut))
    tpath = Path(json.loads((job / "transcript_ref.json").read_text())["path"])
    transcript = Transcript.model_validate_json(tpath.read_text())
    src = next(p for p in tpath.parent.glob("source.*") if p.suffix != ".json")
    title_s = TITLE_SECONDS if req.title else 0.0
    expected = cut_length(cut, title_s)
    outputs = []
    formats = [f for f, on in (("16x9", req.widescreen), ("9x16", req.vertical)) if on]
    t0 = time.time()
    try:
        for fmt in formats:
            with log.stage(f"render_{fmt}") as info:
                out = render_trailer(src, cut, transcript, fmt, job / f"trailer_{fmt}.mp4", mode=req.vertical_mode,
                                     captions=req.captions, title=req.title,
                                     music=Path(req.music) if req.music else None, progress=progress)
                check = check_output(out, fmt, expected)
                info.update(check)
                outputs.append(check)
    except ToolError as exc:  # errors and timeouts never count as success (Ch48)
        log.event("stop", "RENDER_FAILED", error=str(exc))
        return {"stop_reason": "RENDER_FAILED", "error": str(exc)[-600:], "job_dir": str(job)}
    ok = all(o["ok"] for o in outputs)
    manifest = {"stop_reason": "DONE" if ok else "RENDER_FAILED", "job_dir": str(job), "outputs": outputs,
                "expected_seconds": expected, "render_seconds": round(time.time() - t0, 1),
                "cut": [{"order": m.order, "id": m.id, "start": m.start, "end": m.end, "overall": m.overall,
                         "text": m.text} for m in cut]}
    (job / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.event("done", manifest["stop_reason"])
    return manifest


def run(req: TrailerRequest, settings: Settings, auto_approve: bool = False, progress: Progress = None,
        fresh: bool = False, remine: bool = False) -> dict:
    res = prepare(req, settings, progress, fresh=fresh, remine=remine)
    if res["stop_reason"] != StopReason.AWAITING_APPROVAL.value or not auto_approve:
        return res
    job = Path(res["job_dir"])
    approve(job, settings, approved_by="auto-approve flag", mode="auto")
    RunLog(job / "events.jsonl").event("approval", "auto", note="creator opted in with --auto-approve")
    out = render(job, settings, progress)
    out["prepare"] = res
    return out
