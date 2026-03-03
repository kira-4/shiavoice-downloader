import os
import asyncio
import logging
import aiohttp
from typing import Optional, Set, Callable, Any

from playwright.async_api import async_playwright, Page, BrowserContext

# Mutagen
try:
    import mutagen
    from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TCON, APIC, ID3NoHeaderError, TDRC, TRCK
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

from .models import DownloadConfig, TrackInfo
from .utils import sanitize_filename, parse_hijri_year, get_covers_cache_path

logger = logging.getLogger("shiavoice.core")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class ShiavoiceDownloader:
    def __init__(self, config: DownloadConfig, progress_callback: Optional[Callable[[str, Any], None]] = None):
        self.config = config
        self.callback = progress_callback
        
        self.base_url = "https://shiavoice.com"
        self.processed_urls: Set[str] = set()
        self.semaphore = asyncio.Semaphore(config.concurrency)
        
        self.stats = {
            "found": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0
        }
        self._http_session: Optional[aiohttp.ClientSession] = None
    
    async def _emit(self, event: str, data: Any):
        if self.callback:
            if asyncio.iscoroutinefunction(self.callback):
                await self.callback(event, data)
            else:
                self.callback(event, data)

    async def run(self):
        """Main execution entry point."""
        os.makedirs(self.config.output_dir, exist_ok=True)
        if self.config.covers_cache_dir:
            os.makedirs(self.config.covers_cache_dir, exist_ok=True)
            
        logger.info(f"Starting downloader for: {self.config.url}")
        await self._emit("start", {"url": self.config.url})

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            self._http_session = session
            await self._run_with_playwright()
            self._http_session = None

    async def _run_with_playwright(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.config.headless,
                slow_mo=50
            )
            
            context = await browser.new_context(
                user_agent=USER_AGENT,
                accept_downloads=True
            )
            
            # TODO: Load cookies if needed
            
            try:
                page = await context.new_page()
                await page.goto(self.config.url, timeout=self.config.timeout * 1000)
                
                mode = self._detect_mode(page)
                logger.info(f"Detected mode: {mode}")
                
                if mode == "track":
                    await self._process_single_track(page)
                elif mode in ["album", "artist"]:
                    await self._process_list_page(context, page)
                else:
                    msg = "Unknown page type"
                    logger.error(msg)
                    await self._emit("error", {"message": msg})
                    
            except Exception as e:
                logger.error(f"Fatal error: {e}", exc_info=True)
                await self._emit("error", {"message": str(e)})
            finally:
                await context.close()
                await browser.close()
                await self._emit("finished", self.stats)

    def _detect_mode(self, page: Page) -> str:
        if self.config.mode != "auto":
            return self.config.mode
        url = page.url
        if "play-" in url:
            return "track"
        return "artist" # Default assumption for list pages

    async def _process_single_track(self, page: Page, track_num: int = 1, total_tracks: int = 1):
        try:
            download_btn = page.locator(".downloadTrack").first
            await download_btn.wait_for(state="visible", timeout=self.config.timeout * 1000)
            
            # Immediate found update (only if not already discovered via list)
            if self.stats["found"] == 0:
                self.stats["found"] = 1
                await self._emit("found_count", {"found": 1})
            
            # Extract Metadata
            meta = await self._extract_metadata(page, download_btn)
            meta.track_num = track_num
            meta.total_tracks = total_tracks
            
            # Filename & Path
            strict = self.config.sanitize_filenames
            safe_artist = sanitize_filename(meta.artist or "Unknown Artist", strict)
            safe_album  = sanitize_filename(meta.album  or "Unknown Album",  strict)
            filename    = sanitize_filename(meta.title,                      strict)
            if not filename.lower().endswith(".mp3"):
                filename += ".mp3"
            target_dir = os.path.join(self.config.output_dir, safe_artist, safe_album)
            os.makedirs(target_dir, exist_ok=True)
            output_path = os.path.join(target_dir, filename)
            meta.filename = filename
            
            # Check Resume
            if self.config.resume and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"Skipping existing: {filename}")
                self.stats["skipped"] += 1
                await self._emit("track_skipped", meta)
                return

            if self.config.dry_run:
                logger.info(f"[Dry Run] {filename}")
                self.stats["downloaded"] += 1
                await self._emit("track_complete", meta)
                return
            
            # Download
            logger.info(f"Downloading: {filename}")
            await self._emit("track_start", meta)
            
            async with page.expect_download(timeout=self.config.timeout * 1000) as download_info:
                try:
                    await download_btn.click()
                except Exception:
                    await download_btn.evaluate("element => element.click()")
            
            download = await download_info.value
            if download is None:
                raise RuntimeError("Download did not start — no file received from browser")
            await download.save_as(output_path)
            
            # Tagging
            if self.config.tag or self.config.cover:
                logger.info(f"Tagging: {filename}")
                cover_data = None
                if self.config.cover and meta.cover_url:
                    cover_data = await self._fetch_cover_art(meta.cover_url)
                
                self._tag_file(output_path, meta, cover_data)
            
            self.stats["downloaded"] += 1
            await self._emit("track_complete", meta)
            
            await asyncio.sleep(self.config.delay_ms / 1000)
            
        except Exception as e:
            logger.error(f"Track failed: {e}")
            self.stats["failed"] += 1
            await self._emit("track_failed", {"url": page.url, "error": str(e)})

    async def _extract_metadata(self, page: Page, download_btn) -> TrackInfo:
        # 1. Title
        page_title_el = await page.query_selector("h4.font-bold[title]")
        page_title_text = await page_title_el.get_attribute("title") if page_title_el else None
        
        # 2. Breadcrumbs
        breadcrumbs = await page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('.card-header nav a'));
            return links.map(link => link.innerText.trim());
        }""")
        
        bc_genre = breadcrumbs[0] if len(breadcrumbs) > 0 else None
        bc_artist = breadcrumbs[1] if len(breadcrumbs) > 1 else None
        bc_album = breadcrumbs[2] if len(breadcrumbs) > 2 else None
        
        # 3. Cover
        cover_url = await page.evaluate("""() => {
            const img = document.querySelector('.containerTrack img');
            return img ? img.src : null;
        }""")
        
        # 4. Date
        date_text = await page.evaluate("""() => {
            const smalls = Array.from(document.querySelectorAll('small.mx-2.pr-md-1'));
            for (let s of smalls) {
                if (s.innerText.includes('هـ')) return s.innerText;
            }
            return null;
        }""")
        year = parse_hijri_year(date_text) if date_text else None
        
        # Fallbacks
        raw_name = await download_btn.get_attribute("name")
        data_title = await download_btn.get_attribute("data-track-title")
        
        final_title = page_title_text or data_title or raw_name or "Unknown Track"
        final_artist = bc_artist or "Unknown Artist"
        final_genre = self.config.genre or bc_genre
        
        return TrackInfo(
            title=final_title,
            artist=final_artist,
            url=page.url,
            album=bc_album,
            genre=final_genre,
            cover_url=cover_url,
            year=year
        )

    async def _process_list_page(self, context: BrowserContext, page: Page):
        # 1. Check for Albums (Artist Page)
        album_links = await page.evaluate("""() => {
            const links = [];
            document.querySelectorAll('.contentCats .releaseItem a').forEach(a => {
                if (a.href && !links.includes(a.href)) links.push(a.href);
            });
            return links;
        }""")
        
        if album_links:
            logger.info(f"Found {len(album_links)} albums.")
            await self._emit("found_albums", len(album_links))
            for album_url in album_links:
                if self._check_stop(): break
                await self._download_task(context, album_url)
            return

        # 2. Check for Tracks (Album Page)
        track_urls = []
        while True:
            if self._check_stop(): break
            
            new_urls = await page.evaluate("""() => {
                const links = [];
                const containers = document.querySelectorAll('ul.filterItems');
                containers.forEach(container => {
                    container.querySelectorAll('li.trackItem .media-body a[href^="play-"]').forEach(a => {
                         links.push(a.href);
                    });
                });
                return links;
            }""")
            
            found_new = False
            for url in new_urls:
                if url not in self.processed_urls:
                    self.processed_urls.add(url)
                    track_urls.append(url)
                    found_new = True
            
            self.stats["found"] = len(track_urls)
            await self._emit("found_count", {"found": len(track_urls)})
            
            if self.config.max_items and len(track_urls) >= self.config.max_items:
                break
                
            # Pagination
            next_btn = page.locator("ul.pagination li.next a, .loadMore, #loadMoreBtn").first
            if await next_btn.is_visible():
                try:
                    await next_btn.click()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)
                except Exception:
                    break
            else:
                break
        
        total = len(track_urls)
        tasks = []
        for i, url in enumerate(track_urls, start=1):
            if self._check_stop(): break
            
            if self.config.max_items and len(tasks) >= self.config.max_items:
                break
                
            tasks.append(self._download_task(context, url, i, total))
            
        await asyncio.gather(*tasks)

    async def _download_task(self, context: BrowserContext, url: str, track_num: int=1, total_tracks: int=1):
        async with self.semaphore:
            page = await context.new_page()
            try:
                await page.goto(url, timeout=self.config.timeout * 1000)
                mode = self._detect_mode(page)
                if mode == "track":
                    await self._process_single_track(page, track_num, total_tracks)
                else:
                    await self._process_list_page(context, page)
            except Exception as e:
                logger.error(f"Task error {url}: {e}")
            finally:
                await page.close()

    async def _fetch_cover_art(self, url: str) -> Optional[bytes]:
        cache_dir = self.config.covers_cache_dir or os.path.join(self.config.output_dir, ".covers")
        cache_path = get_covers_cache_path(url, cache_dir)
        
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()

        if self._http_session is None:
            logger.warning("No HTTP session available for cover art fetch")
            return None
        try:
            async with self._http_session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if cache_path:
                        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                        with open(cache_path, "wb") as f:
                            f.write(data)
                    return data
        except Exception as e:
            logger.warning(f"Cover art fetch failed for {url}: {e}")
        return None

    def _tag_file(self, filepath: str, meta: TrackInfo, cover_data: Optional[bytes]):
        if not MUTAGEN_AVAILABLE or not self.config.tag:
            return
            
        try:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".mp3":
                self._tag_mp3(filepath, meta, cover_data)
            elif ext in [".m4a", ".mp4"]:
                self._tag_m4a(filepath, meta, cover_data)
        except Exception as e:
            logger.error(f"Tagging error: {e}")

    def _tag_mp3(self, filepath: str, meta: TrackInfo, cover_data: Optional[bytes]):
        try:
            audio = MP3(filepath, ID3=ID3)
        except ID3NoHeaderError:
            audio = MP3(filepath)
            audio.add_tags()
            
        if meta.title: audio.tags.add(TIT2(encoding=3, text=meta.title))
        if meta.artist:
            audio.tags.add(TPE1(encoding=3, text=meta.artist))
            audio.tags.add(TPE2(encoding=3, text=meta.artist))
        if meta.album: audio.tags.add(TALB(encoding=3, text=meta.album))
        if meta.genre: audio.tags.add(TCON(encoding=3, text=meta.genre))
        if meta.year: audio.tags.add(TDRC(encoding=3, text=str(meta.year)))
        
        if meta.track_num:
            trck = str(meta.track_num)
            if meta.total_tracks: trck += f"/{meta.total_tracks}"
            audio.tags.add(TRCK(encoding=3, text=trck))
            
        if cover_data:
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc=u'Cover', data=cover_data))
        
        audio.save()

    def _tag_m4a(self, filepath: str, meta: TrackInfo, cover_data: Optional[bytes]):
        audio = MP4(filepath)
        if meta.title: audio.tags["\xa9nam"] = meta.title
        if meta.artist:
            audio.tags["\xa9ART"] = meta.artist
            audio.tags["aART"] = meta.artist
        if meta.album: audio.tags["\xa9alb"] = meta.album
        if meta.genre: audio.tags["\xa9gen"] = meta.genre
        if meta.year: audio.tags["\xa9day"] = str(meta.year)
        if meta.track_num:
            audio.tags["trkn"] = [(meta.track_num, meta.total_tracks or 0)]
        if cover_data:
            audio.tags["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        
    def _check_stop(self):
        # Hook for stop signal if needed
        return False
