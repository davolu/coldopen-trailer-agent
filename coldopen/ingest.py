"""Ingest tool: read-only download with yt-dlp, or a local file (spec: Tools layer)."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from .config import Settings
from .guards import check_duration
from .proc import ToolError, media_duration, run


def source_key(kind: str, value: str) -> str:
    if kind == "url":
        return "yt-" + hashlib.sha256(value.encode()).hexdigest()[:16]
    h = hashlib.sha256()
    with open(value, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "file-" + h.hexdigest()[:16]


def probe_url_duration(url: str) -> tuple[float, str]:
    """Check the length before downloading anything (budget guard first, Ch52)."""
    out = run([sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download", "--dump-json", url], timeout=120)
    meta = json.loads(out.splitlines()[0])
    return float(meta.get("duration") or 0), meta.get("title") or ""


def ingest(source: dict, settings: Settings) -> dict:
    """Return {"key", "path", "duration", "title"}; cached by source key (idempotent)."""
    key = source_key(source["kind"], source["value"])
    folder = settings.cache_dir / key
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / "source.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if Path(meta["path"]).exists():
            meta["cached"] = True
            return meta

    if source["kind"] == "url":
        duration, title = probe_url_duration(source["value"])
        check_duration(duration, settings.budgets)
        run([sys.executable, "-m", "yt_dlp", "--no-playlist", "-f", "bv*[height<=1080]+ba/b",
             "--merge-output-format", "mp4", "-o", str(folder / "source.%(ext)s"), source["value"]], timeout=1800)
        files = [p for p in folder.glob("source.*") if p.suffix != ".json"]
        if not files:
            raise ToolError("yt-dlp produced no file")
        path = files[0]
    else:
        title = Path(source["value"]).stem
        path = folder / ("source" + Path(source["value"]).suffix.lower())
        if not path.exists():
            shutil.copyfile(source["value"], path)

    duration = media_duration(path)
    check_duration(duration, settings.budgets)
    meta = {"key": key, "path": str(path), "duration": duration, "title": title}
    meta_path.write_text(json.dumps(meta, indent=2))
    meta["cached"] = False
    return meta
