"""Transcriber tool: word-level timestamps with OpenAI Whisper (spec: Tools layer).

Audio is extracted to small mono MP3 chunks so long podcasts stay under the API's file limit.
The result is cached next to the media, so a re-run never pays twice (Ch14, Ch52).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Settings
from .models import Sentence, Transcript, Word
from .proc import run

CHUNK_SECONDS = 20 * 60  # ~4.8 MB per chunk at 32 kbps mono


def extract_audio_chunks(media: Path, duration: float, folder: Path) -> list[tuple[Path, float]]:
    chunks = []
    start = 0.0
    i = 0
    while start < duration - 0.5:
        out = folder / f"audio_{i:03d}.mp3"
        if not out.exists():
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", str(CHUNK_SECONDS), "-i", str(media),
                 "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)], timeout=900)
        chunks.append((out, start))
        start += CHUNK_SECONDS
        i += 1
    return chunks


def split_sentences(
    words: list[Word], max_words: int = 30, pause: float = 0.7, segment_ends: set[float] | None = None
) -> list[Sentence]:
    """Group words into sentences on end punctuation, Whisper segment ends, pauses or a word cap."""
    sentences: list[Sentence] = []
    segment_ends = segment_ends or set()
    begin = 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        ends = bool(re.search(r"[.!?…]['\")\]]*$", w.text.strip()))
        gap = (words[i + 1].start - w.end) if not last else 0
        seg_end = round(w.end, 1) in segment_ends and (i - begin + 1) >= 4
        if last or ends or seg_end or gap >= pause or (i - begin + 1) >= max_words:
            seg = words[begin : i + 1]
            text = " ".join(x.text.strip() for x in seg).strip()
            text = re.sub(r"\s+([,.!?;:])", r"\1", text)
            sentences.append(Sentence(id=len(sentences), start=seg[0].start, end=seg[-1].end, text=text,
                                      word_start=begin, word_end=i + 1))
            begin = i + 1
    return sentences


def punctuate_from_text(words: list[dict], full_text: str) -> list[dict]:
    """Whisper's word list has no punctuation; copy it back from the segment text in order."""
    tokens = full_text.split()
    j = 0
    out = []
    norm = lambda s: re.sub(r"[^\w']", "", s.lower())
    for w in words:
        word = dict(w)
        target = norm(w["word"])
        for k in range(j, min(j + 6, len(tokens))):
            if norm(tokens[k]) == target and target:
                word["word"] = tokens[k]
                j = k + 1
                break
        out.append(word)
    return out


def transcribe(media_meta: dict, settings: Settings, log=None) -> tuple[Transcript, dict]:
    folder = Path(media_meta["path"]).parent
    cache = folder / "transcript.json"
    if cache.exists():
        return Transcript.model_validate_json(cache.read_text()), {"cached": True, "cost_usd": 0.0}

    from openai import OpenAI  # imported lazily so tests run without the SDK configured

    client = OpenAI(timeout=600, max_retries=2)
    words: list[Word] = []
    segment_ends: set[float] = set()
    language = None
    for audio, offset in extract_audio_chunks(Path(media_meta["path"]), media_meta["duration"], folder):
        with audio.open("rb") as fh:
            resp = client.audio.transcriptions.create(
                model=settings.whisper_model, file=fh, response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
                # A punctuated prompt nudges Whisper to punctuate, which gives cleaner sentence cuts.
                prompt="Hello, and welcome back. Today, we're building an agent. Let's get started!",
            )
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        language = language or data.get("language")
        raw = punctuate_from_text(data.get("words") or [], data.get("text") or "")
        segment_ends |= {round(seg["end"] + offset, 1) for seg in (data.get("segments") or [])}
        words += [Word(text=w["word"], start=round(w["start"] + offset, 3), end=round(w["end"] + offset, 3)) for w in raw]
    t = Transcript(source_key=media_meta["key"], duration=media_meta["duration"], language=language, words=words)
    t.sentences = split_sentences(words, segment_ends=segment_ends)
    cache.write_text(t.model_dump_json(indent=1))
    cost = round(media_meta["duration"] / 60 * settings.prices.whisper_per_minute, 4)
    return t, {"cached": False, "cost_usd": cost, "words": len(words), "sentences": len(t.sentences)}
