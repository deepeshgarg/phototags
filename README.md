# Photo Library

A local photo browser: tag your photo library with GPS/date metadata, then browse it
in a web UI with filtering by year/month/country/city and a full-screen lightbox slideshow.

## Components

- **`update_tags_ui.py`** — Tkinter GUI that scans your photo directories, extracts GPS
  coordinates and capture date/time from EXIF (falling back to filename patterns like
  `IMG_20180410_203647.jpg`), reverse-geocodes GPS to city/country via a GeoNames lookup,
  and writes `output.csv`.
- **`photoapp.py`** — FastAPI backend that reads `output.csv` and serves the web UI,
  including on-the-fly thumbnail generation.
- **`static/index.html`** — the browser frontend (filter chips, grid, map, lightbox).

## Setup

```bash
pip install -r requirements.txt
```

`update_tags_ui.py` uses Tkinter, which is part of the Python standard library but may
need a separate system package, e.g. on Debian/Ubuntu:

```bash
sudo apt install python3-tk
```

You'll also need a GeoNames "cities with population ≥ 1000" CSV for reverse geocoding.
Fetch it with:

```bash
./download_geonames.sh
```

Copy `config.example.json` to `config.json` and fill in:

```json
{
  "photo_directories": ["/path/to/your/photos"],
  "output_file": "output.csv",
  "geonames_file": "geonames-all-cities-with-a-population-1000.csv"
}
```

## Usage

1. Tag your photos (generates `output.csv`):
   ```bash
   python update_tags_ui.py
   ```
2. Run the viewer:
   ```bash
   uvicorn photoapp:app --host 0.0.0.0 --port 8000 --reload
   ```
3. Open `http://localhost:8000` in a browser.

`config.json`, `output.csv`, and the GeoNames CSV are gitignored since they're
generated locally and `output.csv` contains your personal file paths.
