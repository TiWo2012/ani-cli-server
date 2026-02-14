# ani-cli-ui (web)

Web app for `ani-cli` on port `9119`.

## What it does

- Search anime (`dub` default, optional `sub`)
- Click a poster to open episode selection
- Click an episode to **download first**, then play it **inside the browser**
- Optional full-season download button

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
- Browser playback support depends on the downloaded container/codec.
