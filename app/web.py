"""Flask web app: search transcribed videos and show timestamped matches."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from flask import Flask, render_template, request
from markupsafe import Markup, escape

from . import config, db


def _fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _youtube_link(youtube_id: str, start: float) -> str:
    return f"https://www.youtube.com/watch?v={youtube_id}&t={int(round(start))}s"


def _render_snippet(snippet: str) -> Markup:
    """Escape FTS snippet text, then turn the \x01/\x02 match markers into <mark>."""
    safe = str(escape(snippet)).replace("\x01", "<mark>").replace("\x02", "</mark>")
    return Markup(safe)


def create_app() -> Flask:
    root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.jinja_env.autoescape = True
    db.init_db()

    @app.route("/")
    def index():
        query = (request.args.get("q") or "").strip()
        grouped: list[dict] = []
        match_count = 0
        if query:
            rows = db.search_segments(query)
            videos: OrderedDict[int, dict] = OrderedDict()
            for r in rows:
                vid = r["video_id"]
                if vid not in videos:
                    videos[vid] = {
                        "youtube_id": r["youtube_id"],
                        "title": r["title"],
                        "upload_date": r["upload_date"],
                        "matches": [],
                    }
                match = {
                    "start": r["start"],
                    "end": r["end"],
                    "time": _fmt_time(r["start"]),
                    "link": _youtube_link(r["youtube_id"], r["start"]),
                    "snippet_html": _render_snippet(r["snippet"]),
                }
                videos[vid]["matches"].append(match)
                match_count += 1
            grouped = list(videos.values())
        s = db.stats()
        return render_template(
            "index.html",
            channel=config.CHANNEL_NAME,
            query=query,
            results=grouped,
            match_count=match_count,
            video_count=len(grouped),
            stats=s,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False)
