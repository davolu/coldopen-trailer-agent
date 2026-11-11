"""ffmpeg renderer tool (spec: Tools layer). Writes only inside the job directory.

Per clip: trim, frame for 16:9 or 9:16, burn word-highlighted captions, fade in/out.
Then concatenate, add an optional title card and an optional music bed.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from .models import Moment, Transcript, Word
from .proc import ffprobe, has_stream, run
from .sequencer import FADE

FORMATS = {
    "16x9": {"w": 1920, "h": 1080, "font": 60, "margin": 70},
    "9x16": {"w": 1080, "h": 1920, "font": 74, "margin": 430},
}
FPS = 30
TITLE_SECONDS = 2.0


# ----------------------------------------------------------------- captions
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_chunks(words: list[Word], max_words: int = 4) -> list[list[Word]]:
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.!?,;:]$", w.text.strip()):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], clip_start: float, clip_end: float, fmt: str) -> str:
    """One ASS event per word: the chunk is shown with the spoken word highlighted."""
    f = FORMATS[fmt]
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {f['w']}\nPlayResY: {f['h']}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,DejaVu Sans,{f['font']},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,5,2,2,80,80,{f['margin']},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    lines = []
    chunks = caption_chunks(inside)
    for ci, chunk in enumerate(chunks):
        next_chunk = chunks[ci + 1][0].start if ci + 1 < len(chunks) else clip_end
        for i, w in enumerate(chunk):
            start = max(w.start, clip_start) - clip_start
            # hold the last word on screen through short pauses so captions do not flicker
            nxt = chunk[i + 1].start if i + 1 < len(chunk) else min(next_chunk, w.end + 0.8)
            end = min(max(nxt, w.end), clip_end) - clip_start
            parts = []
            for j, x in enumerate(chunk):
                t = _esc(x.text.strip()).upper()
                parts.append("{\\c&H0035D7FF&}" + t + "{\\c&H00FFFFFF&}" if j == i else t)
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    return head + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- framing
def detect_face_center(src: Path, start: float, end: float, samples: int = 6) -> Optional[float]:
    """Median face centre (0..1 of width) across sampled frames, or None. Needs opencv (optional)."""
    try:
        import cv2  # type: ignore

        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except (ImportError, AttributeError):  # no opencv, or a build without Haar cascades: fall back to fit
        return None
    cap = cv2.VideoCapture(str(src))
    xs, hits = [], 0
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.1, 6, minSize=(gray.shape[1] // 20, gray.shape[1] // 20))
        if len(faces):
            hits += 1
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            xs.append((x + w / 2) / frame.shape[1])
    cap.release()
    if hits < max(2, samples // 2):
        return None
    xs.sort()
    return xs[len(xs) // 2]


def video_filter(fmt: str, mode: str, face_x: Optional[float]) -> str:
    """Return a filter graph from [0:v] to [v] (before captions)."""
    W, H = FORMATS[fmt]["w"], FORMATS[fmt]["h"]
    if fmt == "9x16" and (mode == "crop" or (mode == "auto" and face_x is not None)):
        cx = 0.5 if face_x is None else face_x
        # crop a 9:16 window centred on the speaker, clamped to the frame
        return (f"[0:v]crop=w='min(iw,ih*9/16)':h=ih:x='max(0,min(iw-ow,iw*{cx:.3f}-ow/2))':y=0,"
                f"scale={W}:{H},setsar=1,fps={FPS}[v]")
    # fit: whole frame over a blurred, filled copy of itself (keeps slides and screens readable)
    return (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.12[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[v]")


# ----------------------------------------------------------------- render
def _font_file() -> str:
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"):
        if Path(cand).exists():
            return cand
    return "DejaVu Sans"


def _encode_args(out: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)]


def render_clip(src: Path, m: Moment, words: list[Word], fmt: str, mode: str, captions: bool,
                work: Path, idx: int, has_audio: bool = True) -> Path:
    dur = m.end - m.start
    face_x = detect_face_center(src, m.start, m.end) if fmt == "9x16" and mode != "fit" else None
    graph = video_filter(fmt, mode, face_x)
    if captions:
        ass = work / f"cap_{fmt}_{idx:02d}.ass"
        ass.write_text(build_ass(words, m.start, m.end, fmt), encoding="utf-8")
        graph = graph[: -len("[v]")] + f"[vb];[vb]subtitles=filename={ass.name}[v]"
    fo = max(0.0, dur - FADE)
    graph += f";[v]fade=t=in:st=0:d={FADE},fade=t=out:st={fo:.3f}:d={FADE}[vout]"
    out = work / f"clip_{fmt}_{idx:02d}.mp4"
    argv = ["ffmpeg", "-v", "error", "-y", "-ss", f"{m.start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    if has_audio:
        graph += f";[0:a]afade=t=in:st=0:d={FADE},afade=t=out:st={fo:.3f}:d={FADE}[aout]"
    else:
        argv += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    argv += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]" if has_audio else "1:a"]
    run(argv + _encode_args(out), timeout=900, cwd=work)
    return out


def render_title(title: str, fmt: str, work: Path) -> Path:
    W, H = FORMATS[fmt]["w"], FORMATS[fmt]["h"]
    out = work / f"title_{fmt}.mp4"
    txt = work / f"title_{fmt}.txt"
    txt.write_text(title, encoding="utf-8")  # textfile= avoids escaping user text into the filter
    size = 84 if fmt == "16x9" else 72
    vf = (f"drawtext=fontfile={_font_file()}:textfile={txt.name}:fontcolor=white:fontsize={size}:"
          f"x=(w-text_w)/2:y=(h-text_h)/2,fade=t=in:st=0:d=0.3,fade=t=out:st={TITLE_SECONDS-0.3}:d=0.3")
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x0d0d12:s={W}x{H}:d={TITLE_SECONDS}:r={FPS}",
         "-f", "lavfi", "-t", str(TITLE_SECONDS), "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf,
         "-map", "0:v", "-map", "1:a"] + _encode_args(out), timeout=120, cwd=work)
    return out


def render_trailer(src: Path, cut: list[Moment], transcript: Transcript, fmt: str, out_path: Path,
                   mode: str = "auto", captions: bool = True, title: Optional[str] = None,
                   music: Optional[Path] = None, progress: Optional[Callable[[str], None]] = None) -> Path:
    out_path, src = Path(out_path).resolve(), Path(src).resolve()
    work = out_path.parent / f"_work_{fmt}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    has_audio = has_stream(ffprobe(src), "audio")
    parts: list[Path] = []
    if title:
        parts.append(render_title(title, fmt, work))
    for i, m in enumerate(cut):
        if progress:
            progress(f"{fmt}: clip {i + 1}/{len(cut)}")
        parts.append(render_clip(src, m, transcript.words, fmt, mode, captions, work, i, has_audio))
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    joined = work / "joined.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", joined.name],
        timeout=300, cwd=work)
    if music:
        total = float(ffprobe(joined)["format"]["duration"])
        run(["ffmpeg", "-v", "error", "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(Path(music).resolve()),
             "-filter_complex",
             f"[1:a]volume=0.12,atrim=0:{total:.3f},afade=t=out:st={max(0, total-1.5):.3f}:d=1.5[m];"
             f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", str(out_path)], timeout=300)
    else:
        shutil.move(str(joined), out_path)
    shutil.rmtree(work, ignore_errors=True)
    return out_path


def check_output(path: Path, fmt: str, expected_seconds: float, tolerance: float = 1.5) -> dict:
    """Output guard: the file must exist with both streams, right size, right length (spec: ffprobe node)."""
    info = ffprobe(path)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    dur = float(info["format"]["duration"])
    W, H = FORMATS[fmt]["w"], FORMATS[fmt]["h"]
    problems = []
    if v is None or not has_stream(info, "audio"):
        problems.append("missing video or audio stream")
    elif (v["width"], v["height"]) != (W, H):
        problems.append(f"size {v['width']}x{v['height']} != {W}x{H}")
    if abs(dur - expected_seconds) > tolerance:
        problems.append(f"duration {dur:.2f}s vs expected {expected_seconds:.2f}s")
    return {"path": str(path), "format": fmt, "duration": round(dur, 2), "width": v and v["width"],
            "height": v and v["height"], "size_mb": round(int(info["format"]["size"]) / 1e6, 2), "ok": not problems,
            "problems": problems}
