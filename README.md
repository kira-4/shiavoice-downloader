# Shiavoice Downloader Service

A robust, dockerized downloader for Shiavoice.com with both a CLI and a Web UI. Designed for Proxmox LXC and Navidrome integration.

## Features

- **Web UI**: Dark mode, real-time progress, queue management.
- **CLI**: Fully featured command-line interface for scripts.
- **Dockerized**: easy deployment with `docker-compose`.
- **Navidrome Ready**: Organizes music by `Artist/Album/Track.mp3` with proper ID3 tags and cover art.
- **Resilient**: Retries, resume support, and "human-like" behavior using Playwright.

## Installation & Usage

### 1. Docker (Recommended for Proxmox/Server)

**Prerequisites**: Docker & Docker Compose installed in your LXC.

1.  **Clone & CD**:
    ```bash
    git clone ...
    cd shivoice-downloader
    ```

2.  **Configure Volumes**:
    Edit `docker-compose.yml` to point to your actual music directory.
    ```yaml
    volumes:
      - /mnt/navidrome_music:/music
      - ./data:/data
    ```

3.  **Start Service**:
    ```bash
    docker-compose up -d
    ```

4.  **Access Web UI**:
    Open `http://<LXC-IP>:8080` in your browser.

5.  **Run CLI commands inside container**:
    ```bash
    docker-compose run --rm shiavoice python -m app.main download "https://shiavoice.com/..." --genre "Latmiya"
    ```

### 2. Manual / Local Installation

**Prerequisites**: Python 3.11+, Playwright.

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

2.  **Run Web Server**:
    ```bash
    python -m app.main web --port 8080
    ```

3.  **Run CLI**:
    ```bash
    python -m app.main download "URL" --out ./downloads
    ```

## Web UI API

- `POST /api/jobs`: Start a download. JSON body: `{ "url": "..." }`
- `GET /api/jobs`: List all jobs.
- `GET /api/events`: SSE stream for real-time updates.

## Production Deployment (Proxmox LXC)

This project uses an image-based deployment workflow. No local build is required on the production server.

1.  **Initial Setup**:
    Clone the repository (or just copy `docker-compose.yml`) to your LXC:
    ```bash
    git clone https://github.com/akbaralhashim/shiavoice-downloader.git
    cd shiavoice-downloader
    ```

2.  **Configure environment**:
    Ensure your `docker-compose.yml` mounts the correct music directory on your host:
    ```yaml
    volumes:
      - /path/to/your/music:/music
    ```

3.  **Start the service**:
    ```bash
    docker compose up -d
    ```
    This will pull the latest image from `ghcr.io/akbaralhashim/shiavoice-downloader`.

4.  **Updating to the latest version**:
    To update the application code, simply run:
    ```bash
    docker compose pull
    docker compose up -d
    ```
    This fetches the latest built image and restarts the container using the new code. No `git pull` is required for application updates (only for docker-compose file changes).

## Release Workflow

Images are automatically built and published to GitHub Container Registry (GHCR) via GitHub Actions.

- **To create a new release**:
    1.  Push changes to `main` branch -> Builds `latest` tag.
    2.  Create a semantic version tag (e.g., `v1.0.0`):
        ```bash
        git tag v1.0.0
        git push origin v1.0.0
        ```
        This builds an image tagged `v1.0.0` and `latest`.

- **To roll back**:
    1.  Edit `docker-compose.yml` to use a specific version tag:
        ```yaml
        image: ghcr.io/akbaralhashim/shiavoice-downloader:v0.9.0
        ```
    2.  Run `docker compose up -d`.

## Development

- **Structure**:
    - `app/downloader/`: Core library (playwright logic).
    - `app/web/`: FastAPI backend and static assets.
    - `app/main.py`: Entrypoint.
- **Frontend**: Vanilla JS + CSS (located in `app/web/static`).

## Troubleshooting

- **Headless Errors**: Docker image uses `python-slim` but installs necessary deps. If you see browser launch errors, rebuild with `--no-cache`.
- **Permissions**: The container runs as `appuser (uid 1000)`. Ensure your host mounted directories are writable by uid 1000.
