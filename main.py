#!/usr/bin/env python3
"""Web UI for ani-cli search/download/watch on port 9119."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_REFERER = "https://allanime.to"
JIKAN_API = "https://api.jikan.moe/v4/anime"
USER_AGENT = "ani-cli-web-ui/1.0"

SEARCH_QUERY = (
    "query( $search: SearchInput $limit: Int $page: Int "
    "$translationType: VaildTranslationTypeEnumType "
    "$countryOrigin: VaildCountryOriginEnumType ) { "
    "shows( search: $search limit: $limit page: $page "
    "translationType: $translationType countryOrigin: $countryOrigin ) { "
    "edges { _id name availableEpisodes __typename } }}"
)


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
        "limit": 24,
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
    base_results: list[tuple[str, str, int]] = []
    for edge in edges:
        anime_id = edge.get("_id")
        name = edge.get("name")
        episodes = int((edge.get("availableEpisodes", {}) or {}).get(mode, 0) or 0)
        if anime_id and name and episodes > 0:
            base_results.append((anime_id, name, episodes))

    return [AnimeResult(id=i, name=n, episodes=e, image_url=find_cover_image(n)) for i, n, e in base_results]


def find_cover_image(title: str) -> str:
    query = urllib.parse.urlencode({"q": title, "limit": 1, "sfw": "true"})
    url = f"{JIKAN_API}?{query}"
    try:
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    except Exception:
        return ""

    items = payload.get("data") or []
    if not items:
        return ""

    images = (items[0].get("images") or {}).get("jpg") or {}
    return images.get("image_url") or ""


def run_ani_cli(query: str, mode: str, search_index: int, episode_range: str, download: bool) -> tuple[bool, str]:
    cmd = ["ani-cli", "-S", str(search_index), "-e", episode_range]
    if download:
        cmd.insert(1, "-d")
    if mode == "dub":
        cmd.append("--dub")
    cmd.append(query)

    try:
        subprocess.Popen(cmd)
    except FileNotFoundError:
        return False, "ani-cli is not installed or not in PATH"
    except Exception as exc:
        return False, str(exc)

    action = "download" if download else "watch"
    return True, f"Started {action}: {episode_range}"


PAGE_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>ani-cli web</title>
  <style>
    :root {
      --bg:#0d1b2a;
      --bg2:#1b263b;
      --card:#ffffff;
      --ink:#102a43;
      --muted:#486581;
      --accent:#ef476f;
      --accent2:#06d6a0;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      color: #fff;
      background: radial-gradient(circle at 10% 10%, #415a77, var(--bg) 60%), linear-gradient(130deg, var(--bg2), #0f172a);
      min-height: 100vh;
    }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0 0 4px; letter-spacing: 0.5px; }
    .sub { margin: 0 0 18px; color: #c8d6e5; }
    .bar {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 10px;
      margin-bottom: 18px;
    }
    input, select, button {
      border-radius: 10px;
      border: none;
      padding: 12px 14px;
      font-size: 15px;
    }
    input, select { background: #f7fafc; color: #102a43; }
    button {
      cursor: pointer;
      font-weight: 700;
      color: #fff;
      background: linear-gradient(90deg, #ef476f, #f78c6b);
    }
    #status { margin-bottom: 14px; color: #d9e2ec; min-height: 24px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 14px;
    }
    .card {
      background: var(--card);
      color: var(--ink);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 8px 30px rgba(0,0,0,.25);
      display: grid;
      grid-template-rows: 300px auto;
    }
    .poster { width: 100%; height: 100%; object-fit: cover; background: #d9e2ec; }
    .meta { padding: 10px 10px 12px; }
    .title { font-size: 14px; line-height: 1.35; min-height: 40px; margin-bottom: 6px; font-weight: 700; }
    .eps { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
    .row { display: grid; grid-template-columns: 1fr; gap: 6px; }
    .btn2 { background: linear-gradient(90deg, #118ab2, #073b4c); }
    .watch {
      display: grid;
      grid-template-columns: 70px 1fr;
      gap: 6px;
    }
    .tiny { padding: 8px 10px; font-size: 13px; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>ani-cli Web</h1>
    <p class=\"sub\">Search, download full season, and watch episodes. Server port: 9119.</p>
    <div class=\"bar\">
      <input id=\"query\" placeholder=\"Search anime title\" />
      <select id=\"mode\"><option value=\"dub\" selected>dub</option><option value=\"sub\">sub</option></select>
      <button id=\"searchBtn\">Search</button>
    </div>
    <div id=\"status\">Ready.</div>
    <div id=\"results\" class=\"grid\"></div>
  </div>
<script>
const queryEl = document.getElementById('query');
const modeEl = document.getElementById('mode');
const searchBtn = document.getElementById('searchBtn');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');

function setStatus(msg) { statusEl.textContent = msg; }

function esc(s) {
  return (s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
}

async function action(payload) {
  const resp = await fetch(payload.download ? '/api/download' : '/api/watch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || 'request failed');
  return data;
}

function render(items) {
  resultsEl.innerHTML = '';
  if (!items.length) {
    resultsEl.innerHTML = '<div>No results.</div>';
    return;
  }

  for (const it of items) {
    const card = document.createElement('div');
    card.className = 'card';
    const img = it.image_url ? esc(it.image_url) : 'https://placehold.co/600x900?text=No+Poster';
    const title = esc(it.name);
    card.innerHTML = `
      <img class="poster" src="${img}" alt="${title}">
      <div class="meta">
        <div class="title">#${it.index} ${title}</div>
        <div class="eps">${it.episodes} episodes</div>
        <div class="row">
          <button class="tiny btn2" data-kind="download">Download Season</button>
          <div class="watch">
            <input class="tiny" type="number" min="1" max="${it.episodes}" value="1" />
            <button class="tiny" data-kind="watch">Watch Episode</button>
          </div>
        </div>
      </div>`;

    const btnDownload = card.querySelector('button[data-kind="download"]');
    const btnWatch = card.querySelector('button[data-kind="watch"]');
    const epInput = card.querySelector('input');

    btnDownload.onclick = async () => {
      try {
        setStatus(`Starting season download: ${it.name}`);
        const res = await action({query: queryEl.value.trim(), mode: modeEl.value, index: it.index, episodes: `1-${it.episodes}`, download: true});
        setStatus(res.message);
      } catch (e) {
        setStatus(`Error: ${e.message}`);
      }
    };

    btnWatch.onclick = async () => {
      const ep = Number(epInput.value || '1');
      if (ep < 1 || ep > it.episodes) {
        setStatus(`Episode must be between 1 and ${it.episodes}`);
        return;
      }
      try {
        setStatus(`Starting watch: ${it.name} E${ep}`);
        const res = await action({query: queryEl.value.trim(), mode: modeEl.value, index: it.index, episodes: `${ep}`, download: false});
        setStatus(res.message);
      } catch (e) {
        setStatus(`Error: ${e.message}`);
      }
    };

    resultsEl.appendChild(card);
  }
}

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
    setStatus(`Found ${(data.results || []).length} result(s).`);
  } catch (e) {
    setStatus(`Error: ${e.message}`);
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTTPStatus.OK, PAGE_HTML)
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
                        "id": item.id,
                        "name": item.name,
                        "episodes": item.episodes,
                        "image_url": item.image_url,
                    }
                    for i, item in enumerate(results, start=1)
                ]
            }
            self._send_json(HTTPStatus.OK, payload)
            return

        self._send_html(HTTPStatus.NOT_FOUND, "<h1>Not found</h1>")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/download", "/api/watch"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        query = str(payload.get("query") or "").strip()
        mode = str(payload.get("mode") or "dub").strip()
        episodes = str(payload.get("episodes") or "1").strip()
        try:
            search_index = int(payload.get("index"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid index"})
            return

        if not query:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "query required"})
            return
        if mode not in {"dub", "sub"}:
            mode = "dub"
        if search_index < 1:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "index must be >= 1"})
            return

        ok, msg = run_ani_cli(
            query=query,
            mode=mode,
            search_index=search_index,
            episode_range=episodes,
            download=(parsed.path == "/api/download"),
        )
        if not ok:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return

        self._send_json(HTTPStatus.OK, {"message": msg})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ani-cli web UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=9119, help="bind port (default: 9119)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AniHandler)
    print(f"Serving ani-cli web UI at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
