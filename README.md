# ani-cli-ui (web)

Web interface for `ani-cli` with:
- anime search
- full-season download
- episode watch launch
- poster artwork cards (fetched online)

Default mode is `dub`.

## Run (port 9119)

```bash
python3 main.py
```

Then open:

```text
http://127.0.0.1:9119
```

## Requirements

- Python 3.10+
- `ani-cli` in `PATH`
- internet access (search + poster APIs)

## Notes

- `Download Season` starts `ani-cli -d` for `1-N` episodes of the selected result.
- `Watch Episode` launches playback for the specific episode number.
