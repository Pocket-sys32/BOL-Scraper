# BOL-Scraper (OCR + LLM + Google Miles)

Extract pickup/delivery locations + dates, total rate, compute route miles, and calculate rate-per-mile from invoice/BOL PDFs.

## Requirements

- **Python**: 3.10+
- **Tesseract OCR** installed on Windows
  - Download: <https://github.com/UB-Mannheim/tesseract/wiki>
  - Make sure `tesseract.exe` is on PATH, or set `TESSERACT_CMD` in `.env`.
- **Google Maps API key** with these enabled:
  - Geocoding API
  - Directions API
- **LLM API key** (OpenAI by default in this project)

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .
```

Create a `.env` file (not committed):

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
GOOGLE_MAPS_API_KEY=...
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

## Usage

Default (project `input/` folder):

```bash
bol-scrape --out out.json --out-csv out.csv --debug-dir .\debug
```

This will scan `.\input` (or the folder set in `BOL_SCRAPER_INPUT_DIR` in `.env`) for all `*.pdf`.

Process one PDF:

```bash
bol-scrape "C:\path\to\file.pdf" --out out.json --out-csv out.csv --debug-dir .\debug
```

Process a directory of PDFs:

```bash
bol-scrape "C:\path\to\Archive" --out out.json --out-csv out.csv --debug-dir .\debug
```

## Output

- **JSON**: rich per-document output including evidence snippets and routing summary
- **CSV**: flattened rows for spreadsheets

## Notes

- PDFs that already contain selectable text still go through the same pipeline; the extractor prefers embedded text when it exists and falls back to OCR per page when needed.
- A sqlite cache is used to avoid re-billing Google for the same routes/geocodes.

