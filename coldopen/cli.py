"""ColdOpen command line (the CLI half of the spec's edge node).

    coldopen <youtube_url_or_file> --length 60 [--vertical] [--auto-approve]
    coldopen approve <job_dir> [--keep m0001-0003,m0040-0042]
    coldopen render <job_dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import get_settings
from .models import TrailerRequest
from .pipeline import render as render_job
from .pipeline import run as run_pipeline
from .review import approve as approve_job

SUBCOMMANDS = {"run", "approve", "render"}


def _print(msg: str) -> None:
    print(f"  > {msg}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coldopen", description="Turn a long video into a podcast-style cold-open trailer.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="find moments (and render with --auto-approve)")
    r.add_argument("source", help="YouTube URL or local video file")
    r.add_argument("--length", type=int, default=60, help="target trailer length in seconds (30-90)")
    r.add_argument("--clips", default="3-6", help="min-max clips, e.g. 3-6")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--vertical", action="store_true", help="also render 9:16 for Shorts/Reels (default: both)")
    g.add_argument("--vertical-only", action="store_true")
    g.add_argument("--widescreen-only", action="store_true")
    r.add_argument("--vertical-mode", choices=["auto", "crop", "fit"], default="auto",
                   help="auto: crop on a detected face, else fit the full frame over a blurred background")
    r.add_argument("--title", help="optional title card text (2 s)")
    r.add_argument("--music", help="optional music bed file (mixed at low volume)")
    r.add_argument("--no-captions", action="store_true")
    r.add_argument("--auto-approve", action="store_true", help="skip the human review (explicit opt-in)")
    r.add_argument("--fresh", action="store_true", help="rebuild the review from saved proposals (no model call)")
    r.add_argument("--remine", action="store_true", help="ask the model for new moments (costs a call)")
    r.add_argument("--out", help="output directory (default: ./outputs)")

    a = sub.add_parser("approve", help="approve the proposed cut, or your own list of moment ids")
    a.add_argument("job_dir")
    a.add_argument("--keep", help="comma-separated moment ids in trailer order")
    a.add_argument("--by", default="creator", help="name recorded in approval.json")

    d = sub.add_parser("render", help="render an approved job")
    d.add_argument("job_dir")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "run")  # `coldopen <source> ...` is shorthand for `coldopen run <source> ...`
    args = build_parser().parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv()  # reads .env if present; values are never printed
    except ImportError:
        pass
    settings = get_settings()

    if args.cmd == "run":
        if args.out:
            settings = replace(settings, out_dir=Path(args.out))
        lo, hi = (int(x) for x in args.clips.split("-"))
        req = TrailerRequest(
            source=args.source, target_seconds=args.length, min_clips=lo, max_clips=hi,
            widescreen=not args.vertical_only, vertical=not args.widescreen_only,
            vertical_mode=args.vertical_mode, title=args.title,
            music=str(Path(args.music).resolve()) if args.music else None, captions=not args.no_captions,
        )
        res = run_pipeline(req, settings, auto_approve=args.auto_approve, progress=_print, fresh=args.fresh,
                           remine=args.remine)
    elif args.cmd == "approve":
        keep = [k.strip() for k in args.keep.split(",")] if args.keep else None
        appr = approve_job(Path(args.job_dir), settings, keep=keep, approved_by=args.by)
        res = {"stop_reason": "APPROVED", "digest": appr.digest[:12], "moments": appr.moment_ids,
               "next": f"coldopen render {args.job_dir}"}
    else:
        res = render_job(Path(args.job_dir), settings, progress=_print)

    print(json.dumps(res, indent=2, default=str))
    if res.get("stop_reason") == "AWAITING_APPROVAL":
        print(f"\nReview the moments in {res['review_file']}\nthen: coldopen approve {res['job_dir']} && coldopen render {res['job_dir']}")
    return 0 if res.get("stop_reason") in {"DONE", "AWAITING_APPROVAL", "APPROVED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
