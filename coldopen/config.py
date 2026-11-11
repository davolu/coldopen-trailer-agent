"""Budgets and settings. Secrets come only from the environment (Ch5)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Budgets:
    """Hard limits enforced by the budget guard (spec D12, Ch14/Ch52)."""

    max_source_minutes: float = float(os.getenv("COLDOPEN_MAX_SOURCE_MINUTES", "180"))
    max_file_mb: float = float(os.getenv("COLDOPEN_MAX_FILE_MB", "4000"))
    max_llm_input_tokens: int = int(os.getenv("COLDOPEN_MAX_LLM_INPUT_TOKENS", "400000"))
    max_llm_calls: int = int(os.getenv("COLDOPEN_MAX_LLM_CALLS", "12"))
    max_cost_usd: float = float(os.getenv("COLDOPEN_MAX_COST_USD", "3.00"))
    approval_ttl_hours: int = 72


@dataclass(frozen=True)
class Prices:
    """USD rates used for the cost ledger. Override with env vars if your rates differ."""

    whisper_per_minute: float = float(os.getenv("COLDOPEN_PRICE_WHISPER_PER_MIN", "0.006"))
    llm_input_per_mtok: float = float(os.getenv("COLDOPEN_PRICE_LLM_IN_PER_MTOK", "3.0"))
    llm_output_per_mtok: float = float(os.getenv("COLDOPEN_PRICE_LLM_OUT_PER_MTOK", "15.0"))


@dataclass(frozen=True)
class Settings:
    llm_model: str = os.getenv("COLDOPEN_LLM_MODEL", "claude-sonnet-5-5")
    whisper_model: str = os.getenv("COLDOPEN_WHISPER_MODEL", "whisper-1")
    cache_dir: Path = Path(os.getenv("COLDOPEN_CACHE_DIR", ".coldopen_cache"))
    out_dir: Path = Path(os.getenv("COLDOPEN_OUT_DIR", "outputs"))
    budgets: Budgets = field(default_factory=Budgets)
    prices: Prices = field(default_factory=Prices)
    allowed_hosts: tuple[str, ...] = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com")
    video_exts: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")


def get_settings() -> Settings:
    return Settings()
