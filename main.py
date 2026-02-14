#!/usr/bin/env python3
"""Tkinter UI for searching anime via the same backend used by ani-cli."""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
import tkinter as tk
from tkinter import messagebox, ttk

ALLANIME_API = "https://api.allanime.day/api"
ALLANIME_REFERER = "https://allanime.to"
USER_AGENT = "ani-cli-ui/0.1"


@dataclass(frozen=True)
class AnimeResult:
    id: str
    name: str
    episodes: int


SEARCH_QUERY = (
    "query( $search: SearchInput $limit: Int $page: Int "
    "$translationType: VaildTranslationTypeEnumType "
    "$countryOrigin: VaildCountryOriginEnumType ) { "
    "shows( search: $search limit: $limit page: $page "
    "translationType: $translationType countryOrigin: $countryOrigin ) { "
    "edges { _id name availableEpisodes __typename } }}"
)


def search_anime(query: str, mode: str = "sub") -> list[AnimeResult]:
    if not query.strip():
        return []

    variables = {
        "search": {
            "allowAdult": False,
            "allowUnknown": False,
            "query": query,
        },
        "limit": 40,
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

    req = urllib.request.Request(
        f"{ALLANIME_API}?{params}",
        headers={
            "Referer": ALLANIME_REFERER,
            "User-Agent": USER_AGENT,
        },
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)

    edges = payload.get("data", {}).get("shows", {}).get("edges", [])
    results: list[AnimeResult] = []
    for edge in edges:
        anime_id = edge.get("_id")
        name = edge.get("name")
        available = edge.get("availableEpisodes", {})
        episodes = int(available.get(mode, 0) or 0)

        if not anime_id or not name or episodes < 1:
            continue

        results.append(AnimeResult(id=anime_id, name=name, episodes=episodes))

    return results


class AniCliUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ani-cli UI")
        self.root.geometry("920x560")

        self.results: list[AnimeResult] = []
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.query_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="sub")
        self.status_var = tk.StringVar(value="Ready")
        self.episode_var = tk.IntVar(value=1)
        self.episode_info_var = tk.StringVar(value="Select a result")

        self._build_layout()
        self.root.after(100, self._process_queue)

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        search_row = ttk.Frame(container)
        search_row.pack(fill=tk.X)

        ttk.Label(search_row, text="Search:").pack(side=tk.LEFT)
        self.query_entry = ttk.Entry(search_row, textvariable=self.query_var)
        self.query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        self.query_entry.bind("<Return>", lambda _: self.start_search())

        ttk.Label(search_row, text="Mode:").pack(side=tk.LEFT, padx=(4, 4))
        ttk.Combobox(
            search_row,
            textvariable=self.mode_var,
            values=("sub", "dub"),
            width=8,
            state="readonly",
        ).pack(side=tk.LEFT)

        self.search_btn = ttk.Button(search_row, text="Search", command=self.start_search)
        self.search_btn.pack(side=tk.LEFT, padx=(8, 0))

        list_frame = ttk.Frame(container)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        self.tree = ttk.Treeview(
            list_frame,
            columns=("idx", "title", "episodes"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("idx", text="#")
        self.tree.heading("title", text="Title")
        self.tree.heading("episodes", text="Episodes")
        self.tree.column("idx", width=50, anchor=tk.CENTER, stretch=False)
        self.tree.column("title", width=720)
        self.tree.column("episodes", width=120, anchor=tk.CENTER, stretch=False)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _: self.download_selected())

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        controls = ttk.Frame(container)
        controls.pack(fill=tk.X, pady=(10, 0))

        self.watch_btn = ttk.Button(
            controls,
            text="Download Selected",
            command=self.download_selected,
            state=tk.DISABLED,
        )
        self.watch_btn.pack(side=tk.LEFT)

        ttk.Label(controls, text="Episode:").pack(side=tk.LEFT, padx=(12, 4))
        self.episode_spinbox = ttk.Spinbox(
            controls,
            from_=1,
            to=1,
            textvariable=self.episode_var,
            width=7,
            state=tk.DISABLED,
        )
        self.episode_spinbox.pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.episode_info_var).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.RIGHT)

    def start_search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo("Search", "Enter an anime name first.")
            return

        self._set_busy(True)
        mode = self.mode_var.get().strip() or "sub"

        thread = threading.Thread(
            target=self._search_worker,
            args=(query, mode),
            daemon=True,
        )
        thread.start()

    def _search_worker(self, query: str, mode: str) -> None:
        try:
            results = search_anime(query=query, mode=mode)
            self.worker_queue.put(("search_ok", results))
        except Exception as exc:  # pragma: no cover - GUI surface
            self.worker_queue.put(("search_err", str(exc)))

    def _process_queue(self) -> None:
        try:
            while True:
                event, payload = self.worker_queue.get_nowait()
                if event == "search_ok":
                    self._render_results(payload)
                    self._set_busy(False)
                elif event == "search_err":
                    self._set_busy(False)
                    self.status_var.set("Search failed")
                    messagebox.showerror("Search failed", str(payload))
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._process_queue)

    def _set_busy(self, busy: bool) -> None:
        self.search_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.query_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if busy:
            self.status_var.set("Searching...")

    def _render_results(self, results: list[AnimeResult]) -> None:
        self.results = results
        self.tree.delete(*self.tree.get_children())

        for i, result in enumerate(results, start=1):
            self.tree.insert("", tk.END, iid=str(i), values=(i, result.name, result.episodes))

        self.watch_btn.configure(state=tk.NORMAL if results else tk.DISABLED)
        self._set_episode_limit(None)
        self.status_var.set(f"Found {len(results)} result(s)")

    def _on_tree_select(self, _: object) -> None:
        selected = self.tree.selection()
        if not selected:
            self._set_episode_limit(None)
            return

        idx = int(selected[0])
        result = self.results[idx - 1]
        self._set_episode_limit(result.episodes)

    def _set_episode_limit(self, max_episode: int | None) -> None:
        if max_episode is None or max_episode < 1:
            self.episode_var.set(1)
            self.episode_spinbox.configure(state=tk.DISABLED, from_=1, to=1)
            self.episode_info_var.set("Select a result")
            return

        self.episode_var.set(1)
        self.episode_spinbox.configure(state="normal", from_=1, to=max_episode)
        self.episode_info_var.set(f"1..{max_episode}")

    def download_selected(self) -> None:
        if not self.results:
            return

        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Download", "Select an anime first.")
            return

        idx = int(selected[0])
        result = self.results[idx - 1]
        query = self.query_var.get().strip()
        mode = self.mode_var.get().strip() or "sub"
        episode = self.episode_var.get()
        if episode < 1 or episode > result.episodes:
            messagebox.showerror("Episode", f"Pick an episode between 1 and {result.episodes}.")
            return

        cmd = ["ani-cli", "-d", "-S", str(idx), "-e", str(episode)]
        if mode == "dub":
            cmd.append("--dub")
        cmd.append(query)

        self.status_var.set(f"Starting download: {result.name} episode {episode}...")
        try:
            subprocess.Popen(cmd)
        except FileNotFoundError:
            messagebox.showerror("ani-cli missing", "ani-cli is not installed or not in PATH.")
            self.status_var.set("Download failed: ani-cli not found")
            return
        except Exception as exc:  # pragma: no cover - GUI surface
            messagebox.showerror("Download failed", str(exc))
            self.status_var.set("Download failed")
            return

        self.status_var.set(f"Download started: episode {episode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ani-cli tkinter UI")
    parser.add_argument("--cli-search", metavar="QUERY", help="Run one search in CLI mode and print results")
    parser.add_argument("--mode", choices=("sub", "dub"), default="sub", help="Translation mode for --cli-search")
    return parser.parse_args()


def run_cli_search(query: str, mode: str) -> int:
    try:
        results = search_anime(query=query, mode=mode)
    except Exception as exc:
        print(f"search failed: {exc}")
        return 1

    if not results:
        print("no results")
        return 0

    for i, result in enumerate(results, start=1):
        print(f"{i:>2}. {result.name} ({result.episodes} episodes)")
    return 0


def main() -> int:
    args = parse_args()

    if args.cli_search:
        return run_cli_search(query=args.cli_search, mode=args.mode)

    root = tk.Tk()
    AniCliUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
