"""End-to-end without any API: fake miner, synthetic video, real ffmpeg."""
import json
import shutil

import pytest

from coldopen.models import TrailerRequest
from coldopen.pipeline import prepare, render
from coldopen.render import build_ass
from coldopen.review import approve
from tests.conftest import FakeMiner, P


def _seed_cache(settings, video, transcript):
    """Put the synthetic transcript where the transcriber's cache would be (no Whisper call)."""
    from coldopen.ingest import source_key

    key = source_key("file", str(video))
    folder = settings.cache_dir / key
    folder.mkdir(parents=True)
    shutil.copyfile(video, folder / "source.mp4")
    (folder / "source.json").write_text(json.dumps({"key": key, "path": str(folder / "source.mp4"),
                                                    "duration": transcript.duration, "title": "test"}))
    transcript.source_key = key
    (folder / "transcript.json").write_text(transcript.model_dump_json())


def test_review_gate_then_render(settings, video, transcript):
    _seed_cache(settings, video, transcript)
    miner = FakeMiner([P(2, 3, role="opener", hook=9), P(5, 5, hook=10, title="injected"), P(6, 7),
                       P(8, 9, role="closer", surprise=9), P(40, 41)])
    req = TrailerRequest(source=str(video), target_seconds=30, min_clips=3, max_clips=4, title="Test")
    res = prepare(req, settings, miner=miner)
    assert res["stop_reason"] == "AWAITING_APPROVAL"
    job = res["job_dir"]

    # no approval yet: render is denied and nothing is written
    denied = render(job, settings)
    assert denied["stop_reason"] == "NOT_APPROVED"
    assert not list((settings.out_dir).rglob("trailer_*.mp4"))

    approve(job, settings, keep=["m0002-0003", "m0006-0007", "m0008-0009"])
    out = render(job, settings)
    assert out["stop_reason"] == "DONE", out
    fmts = {o["format"]: o for o in out["outputs"]}
    assert fmts["16x9"]["width"] == 1920 and fmts["9x16"]["height"] == 1920
    assert all(o["ok"] for o in out["outputs"])


def test_captions_are_clip_relative(transcript):
    s = transcript.sentences[3]
    ass = build_ass(transcript.words, s.start, s.end, "9x16")
    assert "PlayResX: 1080" in ass and "Dialogue: 0,0:00:00.00" in ass
    assert "BUDGET" in ass
