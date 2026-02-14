#!/usr/bin/env python3
"""Web UI for ani-cli with download-then-playback in browser."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_REFERER = "https://allanime.to"
JIKAN_API = "https://api.jikan.moe/v4/anime"
USER_AGENT = "ani-cli-web-ui/2.0"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}

SEARCH_QUERY = (
    "query( $search: SearchInput $limit: Int $page: Int "
    "$translationType: VaildTranslationTypeEnumType "
    "$countryOrigin: VaildCountryOriginEnumType ) { "
    "shows( search: $search limit: $limit page: $page "
    "translationType: $translationType countryOrigin: $countryOrigin ) { "
    "edges { _id name availableEpisodes __typename } }}"
)

DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
POSTER_DIR = Path(__file__).resolve().parent / "posters"
POSTER_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_LOCK = threading.Lock()
HISTORY_FILE = Path(__file__).resolve().parent / "history.json"
HISTORY_LOCK = threading.Lock()
EPISODE_NAME_RE = re.compile(r"^(?P<title>.+?)\s+Episode\s+(?P<ep>\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class AnimeResult:
    id: str
    name: str
    episodes: int
    image_url: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return data[-10:]


def save_history(items: list[dict]) -> None:
    HISTORY_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def append_history(event: str, details: dict) -> None:
    with HISTORY_LOCK:
        items = load_history()
        items.append({"time": utc_now_iso(), "event": event, "details": details})
        items = items[-10:]
        save_history(items)


def latest_history(limit: int = 10) -> list[dict]:
    with HISTORY_LOCK:
        items = load_history()
    return list(reversed(items[-limit:]))


def history_summaries(limit: int = 10) -> list[dict]:
    items = latest_history(limit=limit)
    output: list[dict] = []
    for item in items:
        event = str(item.get("event") or "event")
        details = item.get("details") or {}
        anime = str(details.get("anime") or details.get("query") or "")
        episode = details.get("episode")
        if episode is None:
            filename = str(details.get("filename") or "")
            match = EPISODE_NAME_RE.match(Path(filename).stem)
            if match:
                episode = int(match.group("ep"))
        if event == "play_episode":
            summary = f"Played {anime} episode {episode}"
        elif event == "play_downloaded_file":
            if episode is not None:
                summary = f"Played downloaded {anime or details.get('filename')} episode {episode}"
            else:
                summary = f"Played downloaded {anime or details.get('filename')}"
        elif event == "download_season":
            episodes = details.get("episodes")
            if episodes:
                summary = f"Started season download for {anime} (1-{episodes})"
            else:
                summary = f"Started season download for {anime}"
        else:
            summary = event.replace("_", " ").capitalize()
        output.append({"event": event, "summary": summary, "details": details})
    return output


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def search_anime(query: str, mode: str = "dub") -> list[AnimeResult]:
    if not query.strip():
        return []

    variables = {
        "search": {"allowAdult": False, "allowUnknown": False, "query": query},
        "limit": 20,
        "page": 1,
        "translationType": mode,
        "countryOrigin": "ALL",
    }

    params = urllib.parse.urlencode(
        {
            "variables": json.dumps(variables, separators=(",", ":")),
            "query": SEARCH_QUERY,
        }
    )

    payload = fetch_json(
        f"{ALLANIME_API}?{params}",
        headers={"Referer": ALLANIME_REFERER, "User-Agent": USER_AGENT},
        timeout=20,
    )

    edges = payload.get("data", {}).get("shows", {}).get("edges", [])
    raw_results: list[tuple[str, str, int]] = []
    for edge in edges:
        anime_id = edge.get("_id")
        name = edge.get("name")
        episodes = int((edge.get("availableEpisodes", {}) or {}).get(mode, 0) or 0)
        if anime_id and name and episodes > 0:
            raw_results.append((anime_id, name, episodes))

    results: list[AnimeResult] = []
    for anime_id, name, episodes in raw_results:
        results.append(AnimeResult(id=anime_id, name=name, episodes=episodes, image_url=find_cover_image(name)))
    return results


def find_cover_image(title: str) -> str:
    params = urllib.parse.urlencode({"q": title, "limit": 1, "sfw": "true"})
    try:
        payload = fetch_json(f"{JIKAN_API}?{params}", headers={"User-Agent": USER_AGENT}, timeout=8)
    except Exception:
        return ""
    entries = payload.get("data") or []
    if not entries:
        return ""
    return (((entries[0].get("images") or {}).get("jpg") or {}).get("image_url")) or ""


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def best_search_match(query: str, results: list[AnimeResult]) -> AnimeResult | None:
    if not results:
        return None
    wanted = normalize_title(query)
    for item in results:
        if normalize_title(item.name) == wanted:
            return item
    for item in results:
        name = normalize_title(item.name)
        if wanted in name or name in wanted:
            return item
    return results[0]


def infer_total_episodes(title: str) -> int:
    cached = 0
    with HISTORY_LOCK:
        for item in load_history():
            details = item.get("details") or {}
            anime = str(details.get("anime") or "")
            episodes = int(details.get("episodes") or 0)
            if normalize_title(anime) == normalize_title(title) and episodes > cached:
                cached = episodes
    if cached > 0:
        return cached

    for mode in ("dub", "sub"):
        try:
            results = search_anime(title, mode=mode)
        except Exception:
            continue
        match = best_search_match(title, results)
        if match is not None:
            return match.episodes
    return 0


def ext_for_content_type(content_type: str) -> str:
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def ensure_local_poster(title: str, image_url: str = "") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-") or "poster"
    base = f"{slug}-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}"

    existing = next(POSTER_DIR.glob(f"{base}.*"), None)
    if existing is not None and existing.is_file():
        return "/poster/" + urllib.parse.quote(existing.name)

    src = image_url or find_cover_image(title)
    if not src:
        return ""

    try:
        req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
            ext = ext_for_content_type((resp.headers.get("Content-Type") or "").lower())
    except Exception:
        return ""

    target = POSTER_DIR / f"{base}{ext}"
    try:
        target.write_bytes(data)
    except Exception:
        return ""
    return "/poster/" + urllib.parse.quote(target.name)


def build_ani_cmd(query: str, mode: str, search_index: int, episode_expr: str, download: bool) -> list[str]:
    cmd = ["ani-cli", "-S", str(search_index), "-e", episode_expr]
    if download:
        cmd.insert(1, "-d")
    if mode == "dub":
        cmd.append("--dub")
    cmd.append(query)
    return cmd


def media_snapshot() -> dict[Path, float]:
    snap: dict[Path, float] = {}
    for item in DOWNLOAD_DIR.iterdir():
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
            snap[item] = item.stat().st_mtime
    return snap


def detect_downloaded_file(before: dict[Path, float], started_at: float) -> Path | None:
    after = media_snapshot()
    new_files = [p for p in after if p not in before]
    if new_files:
        return max(new_files, key=lambda p: after[p])

    updated = [p for p, mtime in after.items() if p in before and mtime > before[p]]
    if updated:
        return max(updated, key=lambda p: after[p])

    recent = [p for p, mtime in after.items() if mtime >= started_at - 1]
    if recent:
        return max(recent, key=lambda p: after[p])

    return None


def download_episode_for_browser(query: str, mode: str, search_index: int, episode: int) -> tuple[bool, str, Path | None]:
    cmd = build_ani_cmd(query, mode, search_index, str(episode), download=True)
    before = media_snapshot()
    started = time.time()

    env = os.environ.copy()
    env["ANI_CLI_DOWNLOAD_DIR"] = str(DOWNLOAD_DIR)

    try:
        completed = subprocess.run(cmd, cwd=str(DOWNLOAD_DIR), env=env, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "ani-cli is not installed or not in PATH", None
    except Exception as exc:
        return False, str(exc), None

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "download failed").strip()
        return False, err, None

    video_file = detect_downloaded_file(before, started)
    if video_file is None:
        return False, "download finished but output file was not detected", None

    return True, f"Downloaded episode {episode}", video_file


def start_background_season_download(query: str, mode: str, search_index: int, episodes: int) -> tuple[bool, str]:
    cmd = build_ani_cmd(query, mode, search_index, f"1-{episodes}", download=True)
    env = os.environ.copy()
    env["ANI_CLI_DOWNLOAD_DIR"] = str(DOWNLOAD_DIR)
    try:
        subprocess.Popen(cmd, cwd=str(DOWNLOAD_DIR), env=env)
    except FileNotFoundError:
        return False, "ani-cli is not installed or not in PATH"
    except Exception as exc:
        return False, str(exc)
    return True, f"Started full season download (1-{episodes})"


def start_background_season_download_by_title(title: str, mode: str = "dub") -> tuple[bool, str, int]:
    query = title.strip()
    if not query:
        return False, "title required", 0
    try:
        results = search_anime(query, mode=mode)
    except Exception as exc:
        return False, f"search failed: {exc}", 0
    if not results:
        return False, f"no search results for {query}", 0
    match = best_search_match(query, results)
    if match is None:
        return False, f"no usable match for {query}", 0
    episodes = match.episodes
    ok, msg = start_background_season_download(query=title, mode=mode, search_index=results.index(match) + 1, episodes=episodes)
    if not ok:
        return False, msg, 0
    return True, msg, episodes


def list_library_groups() -> list[dict]:
    history_items = latest_history(limit=300)
    poster_by_title: dict[str, str] = {}
    image_by_title: dict[str, str] = {}
    for entry in history_items:
        details = entry.get("details") or {}
        anime = str(details.get("anime") or "").strip()
        image_url = str(details.get("image_url") or "").strip()
        poster_url = str(details.get("poster_url") or "").strip()
        if anime and poster_url and anime not in poster_by_title:
            poster_by_title[anime] = poster_url
        if anime and image_url and anime not in image_by_title:
            image_by_title[anime] = image_url

    groups: dict[str, dict] = {}
    for item in DOWNLOAD_DIR.iterdir():
        if not item.is_file() or item.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        stem = item.stem
        match = EPISODE_NAME_RE.match(stem)
        if match:
            title = match.group("title").strip()
            episode = int(match.group("ep"))
        else:
            title = stem
            episode = 1

        group = groups.setdefault(
            title,
            {
                "title": title,
                "downloaded_episodes": [],
                "files_by_episode": {},
                "latest_mtime": 0.0,
            },
        )
        group["downloaded_episodes"].append(episode)
        group["files_by_episode"][str(episode)] = {
            "filename": item.name,
            "media_url": "/media/" + urllib.parse.quote(item.name),
        }
        group["latest_mtime"] = max(group["latest_mtime"], item.stat().st_mtime)

    result: list[dict] = []
    for title, group in groups.items():
        downloaded_sorted = sorted(set(int(ep) for ep in group["downloaded_episodes"]))
        image_url = image_by_title.get(title, "")
        poster_url = poster_by_title.get(title) or ensure_local_poster(title, image_url=image_url)
        total_episodes = infer_total_episodes(title)
        if total_episodes < (max(downloaded_sorted) if downloaded_sorted else 1):
            total_episodes = max(downloaded_sorted) if downloaded_sorted else 1

        result.append(
            {
                "title": title,
                "poster_url": poster_url,
                "total_episodes": total_episodes,
                "downloaded_episodes": downloaded_sorted,
                "files_by_episode": group["files_by_episode"],
                "downloaded_count": len(downloaded_sorted),
                "latest_mtime": group["latest_mtime"],
            }
        )

    result.sort(key=lambda x: x["latest_mtime"], reverse=True)
    return result


class AniHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, status: int, body: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static_file(self, filename: str, content_type: str) -> None:
        target = (BASE_DIR / filename).resolve()
        if target.parent != BASE_DIR.resolve() or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        try:
            with target.open("rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_media(self, filename: str) -> None:
        safe_name = Path(urllib.parse.unquote(filename)).name
        target = (DOWNLOAD_DIR / safe_name).resolve()
        if target.parent != DOWNLOAD_DIR.resolve() or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Media not found")
            return

        ctype, _ = mimetypes.guess_type(str(target))
        if not ctype:
            ctype = "application/octet-stream"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            with target.open("rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_poster(self, filename: str) -> None:
        safe_name = Path(urllib.parse.unquote(filename)).name
        target = (POSTER_DIR / safe_name).resolve()
        if target.parent != POSTER_DIR.resolve() or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Poster not found")
            return

        ctype, _ = mimetypes.guess_type(str(target))
        if not ctype:
            ctype = "image/jpeg"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        try:
            with target.open("rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            self._serve_static_file("index.html", "text/html; charset=utf-8")
            return

        if parsed.path == "/style.css":
            self._serve_static_file("style.css", "text/css; charset=utf-8")
            return

        if parsed.path == "/script.js":
            self._serve_static_file("script.js", "application/javascript; charset=utf-8")
            return

        if parsed.path.startswith("/media/"):
            self._serve_media(parsed.path.replace("/media/", "", 1))
            return

        if parsed.path.startswith("/poster/"):
            self._serve_poster(parsed.path.replace("/poster/", "", 1))
            return

        if parsed.path == "/api/search":
            params = urllib.parse.parse_qs(parsed.query)
            query = (params.get("q") or [""])[0].strip()
            mode = (params.get("mode") or ["dub"])[0].strip()
            if mode not in {"dub", "sub"}:
                mode = "dub"
            if not query:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing q"})
                return

            try:
                results = search_anime(query, mode)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_GATEWAY, {"error": f"search failed: {exc}"})
                return

            payload = {
                "results": [
                    {
                        "index": i,
                        "id": r.id,
                        "name": r.name,
                        "episodes": r.episodes,
                        "image_url": r.image_url,
                    }
                    for i, r in enumerate(results, start=1)
                ]
            }
            self._send_json(HTTPStatus.OK, payload)
            return

        if parsed.path == "/api/library":
            self._send_json(HTTPStatus.OK, {"items": list_library_groups()})
            return

        if parsed.path == "/api/history":
            self._send_json(HTTPStatus.OK, {"items": history_summaries(limit=10)})
            return

        self._send_html(HTTPStatus.NOT_FOUND, "<h1>Not found</h1>")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/play_episode", "/api/download_season", "/api/history_event", "/api/download_all_by_title"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        if parsed.path == "/api/download_all_by_title":
            title = str(payload.get("title") or "").strip()
            mode = str(payload.get("mode") or "dub").strip()
            if mode not in {"dub", "sub"}:
                mode = "dub"
            image_url = str(payload.get("image_url") or "").strip()
            if not title:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "title required"})
                return
            ok, msg, episodes = start_background_season_download_by_title(title=title, mode=mode)
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
                return
            poster_url = ensure_local_poster(title, image_url)
            append_history(
                "download_season",
                {
                    "anime": title,
                    "query": title,
                    "episodes": episodes,
                    "image_url": image_url,
                    "poster_url": poster_url,
                },
            )
            self._send_json(HTTPStatus.OK, {"message": f"Download all started for {title} (1-{episodes})"})
            return

        if parsed.path == "/api/history_event":
            anime_for_poster = str(payload.get("anime") or "").strip()
            image_for_poster = str(payload.get("image_url") or "").strip()
            poster_url = ensure_local_poster(anime_for_poster, image_for_poster) if anime_for_poster else ""
            append_history(
                str(payload.get("event") or "event"),
                {
                    "anime": anime_for_poster,
                    "filename": str(payload.get("filename") or "").strip(),
                    "query": str(payload.get("query") or "").strip(),
                    "image_url": image_for_poster,
                    "poster_url": poster_url,
                },
            )
            self._send_json(HTTPStatus.OK, {"message": "history recorded"})
            return

        query = str(payload.get("query") or "").strip()
        anime = str(payload.get("anime") or query).strip()
        image_url = str(payload.get("image_url") or "").strip()
        mode = str(payload.get("mode") or "dub").strip()
        if mode not in {"dub", "sub"}:
            mode = "dub"
        try:
            index = int(payload.get("index"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid index"})
            return

        if not query:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "query required"})
            return
        if index < 1:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "index must be >= 1"})
            return

        if parsed.path == "/api/play_episode":
            try:
                episode = int(payload.get("episode"))
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid episode"})
                return
            if episode < 1:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "episode must be >= 1"})
                return

            with DOWNLOAD_LOCK:
                ok, msg, media_file = download_episode_for_browser(query, mode, index, episode)
            if not ok or media_file is None:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
                return

            media_name = media_file.name
            media_url = "/media/" + urllib.parse.quote(media_name)
            poster_url = ensure_local_poster(anime or query, image_url)
            append_history(
                "play_episode",
                {
                    "anime": anime or query,
                    "query": query,
                    "episode": episode,
                    "filename": media_name,
                    "image_url": image_url,
                    "poster_url": poster_url,
                },
            )
            self._send_json(
                HTTPStatus.OK,
                {"message": msg, "filename": media_name, "media_url": media_url, "episode": episode},
            )
            return

        if parsed.path == "/api/download_season":
            try:
                episodes = int(payload.get("episodes"))
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid episodes"})
                return
            if episodes < 1:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "episodes must be >= 1"})
                return

            ok, msg = start_background_season_download(query, mode, index, episodes)
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
                return
            poster_url = ensure_local_poster(anime or query, image_url)
            append_history(
                "download_season",
                {
                    "anime": anime or query,
                    "query": query,
                    "episodes": episodes,
                    "image_url": image_url,
                    "poster_url": poster_url,
                },
            )
            self._send_json(HTTPStatus.OK, {"message": msg})
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ani-cli web UI")
    parser.add_argument("--host", default="0.0.0.0", help="bind host")
    parser.add_argument("--port", type=int, default=9119, help="bind port (default: 9119)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(f"Serving ani-cli web UI at http://{args.host}:{args.port}")
    print(f"Download directory: {DOWNLOAD_DIR}")
    server = ThreadingHTTPServer((args.host, args.port), AniHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
