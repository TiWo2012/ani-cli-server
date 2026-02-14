#!/usr/bin/env python3
"""Web UI for ani-cli with download-then-playback in browser."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
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
DOWNLOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class AnimeResult:
    id: str
    name: str
    episodes: int
    image_url: str


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


PAGE_HTML = """<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
<title>ani-cli browser player</title>
<style>
:root {
  --bg1:#0f172a;
  --bg2:#14213d;
  --panel:#f8fafc;
  --ink:#102a43;
  --muted:#5c6f82;
  --primary:#e63946;
  --primary2:#f77f00;
  --ok:#2a9d8f;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  color: #fff;
  font-family: "Trebuchet MS", "Segoe UI", sans-serif;
  background:
    radial-gradient(1000px 480px at 5% -10%, #2b4c7e, transparent 60%),
    radial-gradient(900px 420px at 100% 0%, #5a189a55, transparent 60%),
    linear-gradient(135deg, var(--bg2), var(--bg1));
}
.wrap { max-width: 1300px; margin: 0 auto; padding: 20px; }
h1 { margin: 0 0 8px; }
.sub { margin: 0 0 16px; color: #dbe7f3; }
.search {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 10px;
  margin-bottom: 14px;
}
input, select, button {
  border: 0;
  border-radius: 10px;
  padding: 11px 13px;
  font-size: 15px;
}
input, select { background: #f1f5f9; color: #0f172a; }
button {
  cursor: pointer;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(90deg, var(--primary), var(--primary2));
}
button.alt { background: linear-gradient(90deg, #1d3557, #457b9d); }
button.ok { background: linear-gradient(90deg, #1f7a8c, var(--ok)); }
#status { min-height: 22px; margin-bottom: 10px; color: #dbe7f3; }
.season-tab {
  display: none;
  grid-template-columns: 170px 1fr;
  gap: 14px;
  background: #f8fafc;
  border-radius: 12px;
  color: #0f172a;
  padding: 12px;
  margin-bottom: 16px;
  box-shadow: 0 12px 35px rgba(0, 0, 0, 0.35);
}
.season-tab.open { display: grid; }
.season-tab img {
  width: 100%;
  border-radius: 10px;
  height: 240px;
  object-fit: cover;
  background: #dde7f0;
}
.season-tab .head {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
}
.season-tab .head strong { font-size: 18px; }
.season-tab .count { color: #486581; font-size: 14px; }
.season-tab .ep-grid {
  display: grid;
  grid-template-columns: repeat(10, minmax(0, 1fr));
  gap: 6px;
  max-height: 190px;
  overflow: auto;
  margin-top: 8px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 14px;
}
.card {
  background: var(--panel);
  color: var(--ink);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 30px rgba(0,0,0,.28);
}
.poster-wrap { position: relative; cursor: pointer; }
.poster { width: 100%; height: 320px; object-fit: cover; display: block; background: #dde7f0; }
.tap-hint {
  position: absolute;
  left: 8px;
  bottom: 8px;
  background: rgba(16,42,67,.85);
  color: #fff;
  font-size: 12px;
  padding: 5px 8px;
  border-radius: 7px;
}
.meta { padding: 10px; }
.title { font-size: 14px; font-weight: 700; line-height: 1.35; min-height: 38px; margin-bottom: 4px; }
.eps { color: var(--muted); font-size: 13px; margin-bottom: 0; }
.ep-btn {
  border-radius: 8px;
  padding: 6px;
  font-size: 12px;
  background: #e2e8f0;
  color: #102a43;
  border: 0;
  cursor: pointer;
}
.actions { margin-top: 10px; display: grid; grid-template-columns: 1fr; gap: 6px; }
.modal {
  position: fixed;
  inset: 0;
  background: rgba(3, 10, 20, 0.8);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 999;
}
.modal.open { display: flex; }
.modal-panel {
  width: min(96vw, 980px);
  background: #000;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.45);
}
.modal-top {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  background: #0b1220;
  color: #dbe7f3;
  padding: 8px 10px;
}
.modal video {
  width: 100%;
  max-height: 78vh;
  background: #000;
  display: block;
}
.close-btn { padding: 8px 12px; }
@media (max-width: 700px) {
  .search { grid-template-columns: 1fr; }
  .season-tab { grid-template-columns: 1fr; }
  .season-tab .ep-grid { grid-template-columns: repeat(7, minmax(0, 1fr)); }
}
</style>
</head>
<body>
<div class=\"wrap\">
  <h1>ani-cli Browser Player</h1>
  <p class=\"sub\">Click any poster to open a season tab. Select an episode to download then play in a popup. Port 9119.</p>

  <div class=\"search\">
    <input id=\"query\" placeholder=\"Search anime\" />
    <select id=\"mode\"><option value=\"dub\" selected>dub</option><option value=\"sub\">sub</option></select>
    <button id=\"searchBtn\">Search</button>
  </div>

  <div id=\"status\">Ready.</div>

  <div id=\"seasonTab\" class=\"season-tab\">
    <img id=\"seasonPoster\" alt=\"season poster\" />
    <div>
      <div class=\"head\">
        <strong id=\"seasonTitle\">Season</strong>
        <button id=\"seasonClose\" class=\"alt\">Close</button>
      </div>
      <div id=\"seasonCount\" class=\"count\"></div>
      <div id=\"seasonEpisodes\" class=\"ep-grid\"></div>
      <div class=\"actions\">
        <button id=\"seasonDownload\" class=\"ok\">Download Full Season</button>
      </div>
    </div>
  </div>

  <div id=\"results\" class=\"grid\"></div>
</div>

<div id=\"playerModal\" class=\"modal\">
  <div class=\"modal-panel\">
    <div class=\"modal-top\">
      <div id=\"videoMeta\">No episode loaded.</div>
      <button id=\"modalClose\" class=\"close-btn alt\">Close</button>
    </div>
    <video id=\"video\" controls></video>
  </div>
</div>

<script>
const queryEl = document.getElementById('query');
const modeEl = document.getElementById('mode');
const searchBtn = document.getElementById('searchBtn');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const videoEl = document.getElementById('video');
const videoMetaEl = document.getElementById('videoMeta');
const seasonTabEl = document.getElementById('seasonTab');
const seasonPosterEl = document.getElementById('seasonPoster');
const seasonTitleEl = document.getElementById('seasonTitle');
const seasonCountEl = document.getElementById('seasonCount');
const seasonEpisodesEl = document.getElementById('seasonEpisodes');
const seasonDownloadEl = document.getElementById('seasonDownload');
const seasonCloseEl = document.getElementById('seasonClose');
const playerModalEl = document.getElementById('playerModal');
const modalCloseEl = document.getElementById('modalClose');
let selectedSeason = null;

function esc(s) {
  return (s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
function setStatus(msg) { statusEl.textContent = msg; }

async function post(path, payload) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || 'request failed');
  return data;
}

function openPopupPlayer(mediaUrl, metaText) {
  videoEl.src = mediaUrl;
  videoMetaEl.textContent = metaText;
  playerModalEl.classList.add('open');
  videoEl.play().catch(() => {});
}

function closePopupPlayer() {
  playerModalEl.classList.remove('open');
  videoEl.pause();
}

function buildSeasonTab(item) {
  selectedSeason = item;
  seasonPosterEl.src = item.image_url || 'https://placehold.co/600x900?text=No+Poster';
  seasonTitleEl.textContent = `#${item.index} ${item.name}`;
  seasonCountEl.textContent = `${item.episodes} episodes`;
  seasonEpisodesEl.innerHTML = '';
  for (let ep = 1; ep <= item.episodes; ep += 1) {
    const btn = document.createElement('button');
    btn.className = 'ep-btn';
    btn.textContent = String(ep);
    btn.onclick = async () => {
      try {
        setStatus(`Downloading ${item.name} episode ${ep}...`);
        const res = await post('/api/play_episode', {
          query: queryEl.value.trim(),
          mode: modeEl.value,
          index: item.index,
          episode: ep,
        });
        openPopupPlayer(res.media_url, `${item.name} - Episode ${ep} (${res.filename})`);
        setStatus(`Now playing ${item.name} episode ${ep}`);
      } catch (err) {
        setStatus(`Error: ${err.message}`);
      }
    };
    seasonEpisodesEl.appendChild(btn);
  }
  seasonTabEl.classList.add('open');
}

function render(items) {
  selectedSeason = null;
  seasonTabEl.classList.remove('open');
  resultsEl.innerHTML = '';
  if (!items.length) {
    resultsEl.innerHTML = '<div>No results.</div>';
    return;
  }

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'card';
    const title = esc(item.name);
    const imageUrl = item.image_url ? esc(item.image_url) : 'https://placehold.co/600x900?text=No+Poster';

    card.innerHTML = `
      <div class="poster-wrap" role="button" tabindex="0">
        <img class="poster" src="${imageUrl}" alt="${title}" />
        <div class="tap-hint">open season tab</div>
      </div>
      <div class="meta">
        <div class="title">#${item.index} ${title}</div>
        <div class="eps">${item.episodes} episodes</div>
      </div>`;

    const posterWrap = card.querySelector('.poster-wrap');
    posterWrap.onclick = () => buildSeasonTab(item);
    posterWrap.onkeydown = (evt) => {
      if (evt.key === 'Enter' || evt.key === ' ') {
        evt.preventDefault();
        buildSeasonTab(item);
      }
    };

    resultsEl.appendChild(card);
  }
}

seasonDownloadEl.onclick = async () => {
  if (!selectedSeason) return;
  try {
    setStatus(`Starting season download for ${selectedSeason.name}...`);
    const res = await post('/api/download_season', {
      query: queryEl.value.trim(),
      mode: modeEl.value,
      index: selectedSeason.index,
      episodes: selectedSeason.episodes,
    });
    setStatus(res.message);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  }
};

seasonCloseEl.onclick = () => {
  seasonTabEl.classList.remove('open');
  selectedSeason = null;
};

modalCloseEl.onclick = closePopupPlayer;
playerModalEl.onclick = (evt) => {
  if (evt.target === playerModalEl) closePopupPlayer();
};

async function doSearch() {
  const q = queryEl.value.trim();
  if (!q) {
    setStatus('Enter an anime title first.');
    return;
  }
  setStatus('Searching...');
  resultsEl.innerHTML = '';

  try {
    const params = new URLSearchParams({q, mode: modeEl.value});
    const resp = await fetch('/api/search?' + params.toString());
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'search failed');
    render(data.results || []);
    setStatus(`Found ${(data.results || []).length} result(s). Click a poster to pick episodes.`);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  }
}

searchBtn.onclick = doSearch;
queryEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
</script>
</body>
</html>
"""


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
        with target.open("rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            self._send_html(HTTPStatus.OK, PAGE_HTML)
            return

        if parsed.path.startswith("/media/"):
            self._serve_media(parsed.path.replace("/media/", "", 1))
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

        self._send_html(HTTPStatus.NOT_FOUND, "<h1>Not found</h1>")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/play_episode", "/api/download_season"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        query = str(payload.get("query") or "").strip()
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
            self._send_json(HTTPStatus.OK, {"message": msg})
            return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ani-cli web UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
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
