from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class DownloadConfig:
    url: str
    output_dir: str = "/music"
    mode: str = "auto"  # auto, track, album, artist
    headless: bool = True
    timeout: int = 30
    concurrency: int = 1
    delay_ms: int = 1000
    retries: int = 3
    resume: bool = True
    cookies_file: Optional[str] = None
    dry_run: bool = False
    max_items: Optional[int] = None
    sanitize_filenames: bool = False
    genre: Optional[str] = None
    tag: bool = True
    cover: bool = True
    covers_cache_dir: Optional[str] = None
    verbose: bool = False

@dataclass
class TrackInfo:
    title: str
    artist: str
    url: str
    album: Optional[str] = None
    genre: Optional[str] = None
    cover_url: Optional[str] = None
    year: Optional[str] = None
    track_num: Optional[int] = None
    total_tracks: Optional[int] = None
    status: str = "pending"  # pending, running, finished, failed, skipped
    filename: Optional[str] = None
    error: Optional[str] = None
