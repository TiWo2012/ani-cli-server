# ani-cli-ui

Python `tkinter` UI for searching anime using the same backend as `ani-cli`, with a download action that launches `ani-cli --download` for the selected result.

Default mode is `dub`.
Selecting a result and downloading grabs the full available episode range (entire season), not a single episode.

## Run

```bash
python3 main.py
```

## Requirements

- Python 3.10+
- `ani-cli` installed and available in `PATH`
- internet access (for search API)

## Headless search check

```bash
python3 main.py --cli-search "naruto" --mode sub
```
