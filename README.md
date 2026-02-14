# ani-cli-ui (web)

Web app for `ani-cli` on port `9119`.

## What it does

- Search anime (`dub` default, optional `sub`)
- Launch with no search query to browse downloaded library immediately
- Click a poster to open a season tab with episode buttons
- Library view is grouped by anime title
- Posters are downloaded locally and reused from `posters/`
- In library season tabs, episodes not downloaded yet are shown in grey
- Click an episode to **download first**, then play it in a **popup video player**
- Optional full-season download button
- Keeps local watch/download history in `history.json`

## Run

```bash
python3 main.py
```

Open:

```text
http://127.0.0.1:9119
```

## Requirements

- Python 3.10+
- `ani-cli` installed in `PATH`
- internet access (anime search + poster lookup)

## Notes

- Downloaded video files are stored in `downloads/`.
- Downloaded poster files are stored in `posters/`.
- Startup view shows items from `downloads/` when no query is entered.
- Browser playback support depends on the downloaded container/codec.
