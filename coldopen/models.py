"""Typed contracts for every stage (spec section 7, Ch8/Ch11)."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class StopReason(str, Enum):
    """Named stop reasons (spec section 10, Ch14). Anything but DONE is incomplete work."""

    DONE = "DONE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REJECTED_INPUT = "REJECTED_INPUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    NO_GOOD_MOMENTS = "NO_GOOD_MOMENTS"
    RENDER_FAILED = "RENDER_FAILED"
    NOT_APPROVED = "NOT_APPROVED"


class TrailerRequest(BaseModel):
    """What the edge (CLI or Streamlit) hands to the pipeline."""

    source: str = Field(..., description="YouTube URL or path to a local video file")
    target_seconds: int = Field(60, ge=30, le=90)
    min_clips: int = Field(3, ge=1, le=6)
    max_clips: int = Field(6, ge=1, le=6)
    widescreen: bool = True
    vertical: bool = True
    vertical_mode: Literal["auto", "crop", "fit"] = "auto"
    title: Optional[str] = Field(None, max_length=80)
    music: Optional[str] = None
    captions: bool = True


class Word(BaseModel):
    text: str
    start: float
    end: float


class Sentence(BaseModel):
    id: int
    start: float
    end: float
    text: str
    word_start: int
    word_end: int  # exclusive


class Transcript(BaseModel):
    source_key: str
    duration: float
    language: Optional[str] = None
    words: list[Word]
    sentences: list[Sentence] = []


class Scores(BaseModel):
    hook: float = Field(..., ge=0, le=10)
    emotion: float = Field(..., ge=0, le=10)
    surprise: float = Field(..., ge=0, le=10)
    clarity: float = Field(..., ge=0, le=10, description="standalone clarity out of context")


class MomentProposal(BaseModel):
    """What the model is allowed to say: sentence IDs, never raw timestamps."""

    start_sentence: int
    end_sentence: int
    title: str = Field(..., max_length=80)
    reason: str = Field(..., max_length=300)
    role: Literal["opener", "build", "climax", "closer"]
    scores: Scores


class Moment(BaseModel):
    """A verified moment: timestamps come from the transcript, not the model."""

    id: str
    start: float
    end: float
    start_sentence: int
    end_sentence: int
    text: str
    title: str
    reason: str
    role: str
    scores: Scores
    overall: float
    selected: bool = False
    order: Optional[int] = None

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v, info):
        if "start" in info.data and v <= info.data["start"]:
            raise ValueError("end must be after start")
        return v


class Approval(BaseModel):
    digest: str
    approved_by: str
    mode: Literal["human", "auto"]
    approved_at: str
    expires_at: str
    moment_ids: list[str]
