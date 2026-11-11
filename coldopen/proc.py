"""Run external tools as argument lists with a timeout. Never through a shell (Ch39)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ToolError(Exception):
    pass


def run(argv: list[str], timeout: float = 900, cwd: str | Path | None = None) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{argv[0]} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise ToolError(f"{argv[0]} is not installed") from exc
    if proc.returncode != 0:
        raise ToolError(f"{argv[0]} failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
    return proc.stdout


def ffprobe(path: str | Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
               "-of", "json", str(path)], timeout=60)
    return json.loads(out)


def media_duration(path: str | Path) -> float:
    return float(ffprobe(path)["format"].get("duration", 0) or 0)


def has_stream(info: dict, kind: str) -> bool:
    return any(s.get("codec_type") == kind for s in info.get("streams", []))
