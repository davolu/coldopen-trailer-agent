"""ColdOpen web UI (Streamlit): the main interface, the UI half of the spec's edge node.

    streamlit run app.py

1. Paste a YouTube URL or upload a video.  2. Claude proposes moments; you tick, re-order and approve
the exact list (human review, Ch49).  3. ColdOpen renders the 16:9 and 9:16 trailers for you to play and download.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()  # values are never displayed
except ImportError:
    pass

from coldopen.config import get_settings
from coldopen.guards import GuardError
from coldopen.models import TrailerRequest
from coldopen.pipeline import prepare, render
from coldopen.review import approve, load_moments

st.set_page_config(page_title="ColdOpen: AI trailer agent", page_icon="🎬", layout="wide")
settings = get_settings()
UPLOADS = settings.out_dir / "_uploads"


def fmt_t(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


st.title("🎬 ColdOpen")
st.caption("Turn a long video or podcast into a podcast-style cold-open trailer. "
           "Claude proposes the moments, you approve them, code does the rest.")

# ------------------------------------------------------------------ settings
with st.sidebar:
    st.header("Trailer settings")
    length = st.slider("Target length (seconds)", 30, 90, 60, step=5)
    clips = st.slider("Number of clips", 1, 6, (3, 6))
    widescreen = st.checkbox("16:9 widescreen", value=True)
    vertical = st.checkbox("9:16 vertical (Shorts / Reels)", value=True)
    vmode = st.selectbox("Vertical framing", ["auto", "fit", "crop"],
                         help="auto: crop on the speaker's face if one is found, otherwise fit the whole frame over a blurred background")
    captions = st.checkbox("Burn in captions", value=True)
    title = st.text_input("Title card (optional)", max_chars=80)
    music = st.file_uploader("Music bed (optional)", type=["mp3", "m4a", "wav"])
    st.divider()
    st.caption("Made by David Oluyale, Agentic AI Hub · designed with "
               "[Agentic Architect](https://github.com/davolu/agentic-architect-skill)")

# --------------------------------------------------------------------- step 1
st.subheader("1 · Choose a video")
tab_url, tab_file = st.tabs(["YouTube URL", "Upload a video"])
with tab_url:
    url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...")
with tab_file:
    upload = st.file_uploader("Video file", type=["mp4", "mov", "mkv", "webm", "m4v"])

if st.button("🔍 Find the best moments", type="primary", disabled=not (url or upload)):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    if upload is not None:
        source = UPLOADS / Path(upload.name).name
        source.write_bytes(upload.getbuffer())
        source = str(source)
    else:
        source = url.strip()
    music_path = None
    if music is not None:
        music_path = UPLOADS / Path(music.name).name
        music_path.write_bytes(music.getbuffer())
    try:
        req = TrailerRequest(source=source, target_seconds=length, min_clips=clips[0], max_clips=clips[1],
                             widescreen=widescreen, vertical=vertical, vertical_mode=vmode, captions=captions,
                             title=title or None, music=str(music_path) if music_path else None)
    except Exception as exc:  # pydantic validation
        st.error(f"Invalid settings: {exc}")
        st.stop()
    with st.status("Working on it…", expanded=True) as status:
        res = prepare(req, settings, progress=lambda m: st.write(f"• {m}"))
        if res["stop_reason"] == "AWAITING_APPROVAL":
            status.update(label="Moments ready for your review", state="complete")
        else:
            status.update(label=f"Stopped: {res['stop_reason']}", state="error")
    st.session_state["result"] = res
    st.session_state.pop("manifest", None)

res = st.session_state.get("result")
if res and res.get("stop_reason") != "AWAITING_APPROVAL":
    st.error(f"{res['stop_reason']}: {res.get('error', '')}")

# --------------------------------------------------------------------- step 2
if res and res.get("stop_reason") == "AWAITING_APPROVAL":
    job = Path(res["job_dir"])
    candidates = load_moments(job, "candidates.json")
    cut = {m.id: m.order for m in load_moments(job)}
    ledger = json.loads((job / "ledger.json").read_text()) if (job / "ledger.json").exists() else {}

    st.subheader("2 · Review the moments")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Source length", fmt_t(res["source"]["duration"]))
    c2.metric("Verified moments", len(candidates))
    c3.metric("Proposed cut", f"{res['clips']} clips · {res['cut_seconds']:.0f}s")
    c4.metric("API cost so far", f"${ledger.get('cost_usd', 0):.3f}", help="Whisper + Claude, from the job ledger")
    st.caption(f"**{res['source'].get('title', '')}** — tick the clips you want and set their order. "
               "Nothing is rendered until you approve this exact list.")

    rows = [{
        "keep": m.id in cut, "order": cut.get(m.id) or 0, "id": m.id,
        "time": f"{fmt_t(m.start)}–{fmt_t(m.end)}", "len (s)": round(m.duration, 1),
        "overall": m.overall, "hook": m.scores.hook, "emotion": m.scores.emotion,
        "surprise": m.scores.surprise, "clarity": m.scores.clarity, "role": m.role,
        "title": m.title, "line": m.text,
    } for m in candidates]
    edited = st.data_editor(
        rows, hide_index=True, use_container_width=True, key=f"editor_{job.name}",
        disabled=["id", "time", "len (s)", "overall", "hook", "emotion", "surprise", "clarity", "role", "title", "line"],
        column_config={
            "keep": st.column_config.CheckboxColumn("keep", width="small"),
            "order": st.column_config.NumberColumn("order", min_value=0, max_value=20, step=1, width="small"),
            "overall": st.column_config.ProgressColumn("overall", min_value=0, max_value=10, format="%.2f"),
            "line": st.column_config.TextColumn("line", width="large"),
        },
    )
    chosen = sorted((r for r in edited if r["keep"]), key=lambda r: (r["order"] or 99, -r["overall"]))
    total = sum(r["len (s)"] for r in chosen)
    st.write(f"**Your cut:** {len(chosen)} clips · {total:.1f}s → " + " → ".join(r["id"] for r in chosen))
    if total > length * 1.25:
        st.warning(f"That is {total:.0f}s, well over your {length}s target. Untick a clip or approve anyway.")

    if st.button("✅ Approve this cut and render", type="primary", disabled=not chosen):
        try:
            appr = approve(job, settings, keep=[r["id"] for r in chosen], approved_by="Streamlit reviewer")
        except GuardError as exc:
            st.error(str(exc))
            st.stop()
        st.info(f"Approved {len(appr.moment_ids)} clips · digest `{appr.digest[:12]}`")
        with st.status("Rendering trailers with ffmpeg…", expanded=True) as status:
            manifest = render(job, settings, progress=lambda m: st.write(f"• {m}"))
            status.update(label=f"Render: {manifest['stop_reason']}",
                          state="complete" if manifest["stop_reason"] == "DONE" else "error")
        st.session_state["manifest"] = manifest

# --------------------------------------------------------------------- step 3
manifest = st.session_state.get("manifest")
if manifest:
    st.subheader("3 · Your trailers")
    if manifest["stop_reason"] != "DONE":
        st.error(manifest.get("error", manifest["stop_reason"]))
    outs = manifest.get("outputs", [])
    cols = st.columns([16, 9] if len(outs) == 2 else [1])
    for col, o in zip(cols, outs):
        with col:
            label = "16:9 widescreen" if o["format"] == "16x9" else "9:16 vertical"
            st.markdown(f"**{label}** · {o['duration']:.1f}s · {o['width']}×{o['height']} · {o['size_mb']} MB")
            data = Path(o["path"]).read_bytes()
            st.video(data)
            st.download_button(f"⬇️ Download {label}", data, file_name=Path(o["path"]).name, mime="video/mp4")
    with st.expander("Clip list"):
        for c in manifest.get("cut", []):
            st.markdown(f"{c['order']}. `{c['id']}` {fmt_t(c['start'])}–{fmt_t(c['end'])} · overall {c['overall']} — {c['text']}")
