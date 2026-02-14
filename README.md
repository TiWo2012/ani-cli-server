# ani-cli-ui

Python `tkinter` UI for searching anime using the same backend as `ani-cli`, with a watch action that launches `ani-cli` for the selected result.

You can select the exact episode in the UI before launching playback.

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
