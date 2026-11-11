"""Run log and cost ledger: one JSONL event per stage (spec: observability, Ch43).

Telemetry records; it never enforces. Values are redacted before they are written.
"""
from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),          # OpenAI / Anthropic style keys
    re.compile(r"(?i)(api[_-]?key|authorization|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
]


_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|authorization|^token$|access[_-]?token|bearer)")


def redact(value: Any) -> Any:
    """Strip anything that looks like a credential, recursively."""
    if isinstance(value, str):
        out = value
        for pat in _SECRET_PATTERNS:
            out = pat.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class RunLog:
    def __init__(self, path: Path, on_event: Optional[Callable[[dict], None]] = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.on_event = on_event
        self.cost_usd = 0.0
        self.llm_input_tokens = 0
        self.llm_output_tokens = 0
        self.llm_calls = 0

    def event(self, stage: str, status: str, **attrs: Any) -> dict:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stage": stage, "status": status, **redact(attrs)}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.on_event:
            self.on_event(rec)
        return rec

    def add_cost(self, usd: float) -> None:
        self.cost_usd = round(self.cost_usd + usd, 6)

    @contextmanager
    def stage(self, name: str, **attrs: Any):
        t0 = time.time()
        self.event(name, "start", **attrs)
        info: dict = {}
        try:
            yield info
        except Exception as exc:  # recorded, then re-raised: errors never count as success
            self.event(name, "error", seconds=round(time.time() - t0, 2), error=str(exc)[:500])
            raise
        self.event(name, "ok", **{"seconds": round(time.time() - t0, 2), **info})
