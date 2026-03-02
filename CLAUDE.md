# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shiavoice Downloader is an async Python app that scrapes and downloads audio from shiavoice.com. It runs in two modes: a CLI and a FastAPI web server with SSE-based real-time progress. Designed for deployment alongside Navidrome on Proxmox LXC.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run web server locally
python -m app.main web --host 0.0.0.0 --port 8080

# Run CLI download
python -m app.main download "https://shiavoice.com/..." --out ./downloads --verbose

# Docker: start service
docker-compose up -d

# Docker: run CLI in container
docker-compose run --rm shiavoice python -m app.main download "URL" --genre "Latmiya"

# Docker: rebuild image
docker build -t shiavoice-downloader .
```

No test framework or linter is configured.

## Architecture

### Dual-mode entry point (`app/main.py`)
- Parses `download` or `web` subcommand via argparse
- `download` → builds `DownloadConfig`, runs `ShiavoiceDownloader.run()` directly
- `web` → starts uvicorn with the FastAPI app from `app/web/server.py`

### Core downloader (`app/downloader/core.py`)
`ShiavoiceDownloader` does all the heavy lifting:
- Uses **Playwright** (headless Chromium) to navigate and scrape shiavoice.com
- Detects page type (track / album / artist) from URL pattern
- Extracts metadata from breadcrumbs + DOM (title, artist, album, genre, cover URL, Hijri year)
- Downloads audio via Playwright's download API
- Tags output files with **mutagen** (ID3v2.4 for MP3, MP4 atoms for M4A)
- Fetches and embeds cover art (cached by MD5 hash of URL)
- Accepts a `progress_callback` so both CLI and web can receive live updates

### Web layer (`app/web/`)
- `server.py` — FastAPI app: REST endpoints + SSE stream at `/api/events`
- `manager.py` — `JobManager` with a persistent JSONL database (`data/jobs.jsonl`), async worker queue, and per-job status/stats tracking

### Data models (`app/downloader/models.py`)
- `DownloadConfig` — all download options (URL, output dir, concurrency, retries, tagging flags, etc.)
- `TrackInfo` — per-track metadata and download status

### Utilities (`app/downloader/utils.py`)
- Logging setup, filename sanitization, Hijri→Gregorian year conversion, cover-art caching helpers

## Key Conventions

- **Output structure**: `Artist/Album/Track.mp3` inside `OUTPUT_DIR` (default `/music` for Navidrome)
- **Persistence**: Job state is stored in `DATA_DIR/jobs.jsonl` (one JSON object per line)
- **Concurrency**: Controlled by a semaphore; default is 1 (sequential). Increase with `--concurrency`.
- **Resume**: Skip already-existing files by default (`resume=True`)

## CI/CD & Releases

- GitHub Actions (`.github/workflows/docker-publish.yml`) publishes to `ghcr.io/akbaralhashim/shiavoice-downloader`
- Push to `main` → `:latest` tag; push a `v*.*.*` tag → versioned + `:latest`
- Production uses `docker-compose pull && docker-compose up -d` — no local builds on the server
