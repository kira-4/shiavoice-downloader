#!/usr/bin/env python3
"""
Shiavoice.com Downloader
A robust CLI tool to download audio tracks from Shiavoice.com using Playwright.

Usage:
    python3 downloader.py <url> [options]
"""

import os
import sys
import re
import time
import asyncio
import argparse
import logging
from dataclasses import dataclass
from typing import List, Optional, Set
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, Page, BrowserContext, Playwright
import aiohttp

# Mutagen imports for tagging
try:
    import mutagen
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, APIC, ID3NoHeaderError
    from mutagen.mp3 import MP3, EasyMP3
    from mutagen.mp4 import MP4, MP4Cover, MP4Tags
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# --- Configuration & Constants ---
DEFAULT_TIMEOUT = 30000  # 30 seconds
DEFAULT_DELAY = 1000     # 1 second
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Logger setup
logger = logging.getLogger("shiavoice_dl")

@dataclass
class TrackInfo:
    title: str
    artist: str
    url: str  # Page URL of the track
    filename: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    cover_url: Optional[str] = None
    cover_art: Optional[bytes] = None

    year: Optional[str] = None
    track_num: Optional[int] = None
    total_tracks: Optional[int] = None

class ShiavoiceDownloader:
    def __init__(self, args):
        self.args = args
        self.base_url = "https://shiavoice.com"
        self.download_dir = os.path.abspath(self.args.out)
        self.processed_urls: Set[str] = set()
        self.semaphore = asyncio.Semaphore(args.concurrency)
        
        # Mapping to convert Arabic digits to Western
        self.arabic_digits_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        
        # Stats
        self.stats = {
            "found": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0
        }

    async def run(self):
        """Main execution flow."""
        os.makedirs(self.download_dir, exist_ok=True)
        self._setup_logging()
        
        logger.info(f"Starting downloader for URL: {self.args.url}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=not self.args.visible,
                slow_mo=50  # Slight delay for human-like behavior
            )
            
            # Context with persistent storage if cookies file provided
            # For now simple context, can extend for cookies later
            context = await browser.new_context(
                user_agent=USER_AGENT,
                accept_downloads=True
            )
            
            if self.args.cookies and os.path.exists(self.args.cookies):
                # TODO: Load cookies
                pass

            try:
                page = await context.new_page()
                await page.goto(self.args.url, timeout=self.args.timeout * 1000)
                
                mode = self.detect_mode(page)
                logger.info(f"Detected mode: {mode}")
                
                if mode == "track":
                    await self.process_single_track(page)
                elif mode in ["album", "artist"]:
                    await self.process_list_page(context, page)
                else:
                    logger.error("Unknown page type. Could not detect track, album, or artist content.")
                    
            except Exception as e:
                logger.error(f"Fatal error: {e}", exc_info=self.args.verbose)
            finally:
                await context.close()
                await browser.close()
                self._print_summary()

    def _setup_logging(self):
        level = logging.DEBUG if self.args.verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        if self.args.log:
            file_handler = logging.FileHandler(self.args.log)
            file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logging.getLogger().addHandler(file_handler)

    def extract_metadata_from_page(self, page: Page, track_data: dict) -> dict:
        """Extract metadata (Album, Cover URL) from the page."""
        meta = {
            "title": track_data.get("name"), # Will be refined
            "artist": track_data.get("artist"),
            "album": None,
            "cover_url": None,
            "genre": self.args.genre # Default from CLI
        }
        
        # Album name usually in breadcrumb or title
        # Shiavoice structure: documentTitle data-title="Track - Artist" 
        # But we want Album. 
        # Look for nav breadcrumb: Home > Category > Artist > Album
        return meta

    async def _fetch_cover_art(self, url: str) -> Optional[bytes]:
        if not url or not self.args.cover:
            return None
            
        cache_dir = self.args.covers_cache
        if not cache_dir:
            cache_dir = os.path.join(self.download_dir, ".covers")
        
        os.makedirs(cache_dir, exist_ok=True)
        
        # Simple hash for filename
        import hashlib
        filename = hashlib.md5(url.encode()).hexdigest() + ".jpg" # Assume jpg/png from headers later
        cache_path = os.path.join(cache_dir, filename)
        
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()
                
        # Download
        try:
            # FIX: User requested to ignore SSL errors for cover downloads (self-signed/local issuer issues)
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        with open(cache_path, "wb") as f:
                            f.write(data)
                        return data
        except Exception as e:
            logger.warning(f"Failed to fetch cover art: {e}")
            
        return None

    def tag_file(self, filepath: str, meta: dict, cover_data: Optional[bytes]):
        if not MUTAGEN_AVAILABLE:
            logger.warning("Mutagen not installed. Skipping tagging.")
            return
            
        if not self.args.tag:
            return

        try:
            logger.info(f"Tagging {os.path.basename(filepath)}...")
            ext = os.path.splitext(filepath)[1].lower()
            
            if ext == ".mp3":
                self._tag_mp3(filepath, meta, cover_data)
            elif ext in [".m4a", ".mp4"]:
                self._tag_m4a(filepath, meta, cover_data)
                
            if self.args.print_tags:
                self._print_file_tags(filepath)
                
        except Exception as e:
            logger.error(f"Failed to tag file {filepath}: {e}")

    def _tag_mp3(self, filepath: str, meta: dict, cover_data: Optional[bytes]):
        try:
            audio = MP3(filepath, ID3=ID3)
        except ID3NoHeaderError:
            audio = MP3(filepath)
            audio.add_tags()
            
        # Basic Tags
        if meta.get("title"):
            audio.tags.add(TIT2(encoding=3, text=meta["title"]))
        if meta.get("artist"):
            audio.tags.add(TPE1(encoding=3, text=meta["artist"]))
        if meta.get("album"):
            audio.tags.add(TALB(encoding=3, text=meta["album"]))
        if meta.get("genre"):
            audio.tags.add(TCON(encoding=3, text=meta["genre"]))
        if meta.get("year"):
            # TYER is deprecated in ID3v2.4 but widely used. TDRC is standard. 
            # Mutagen handles TDRC usually. Let's try TDRC for date.
            from mutagen.id3 import TDRC
            audio.tags.add(TDRC(encoding=3, text=str(meta["year"])))
            
        if meta.get("track_num"):
            # TRCK frame: "num/total" or just "num"
            trck_str = str(meta["track_num"])
            if meta.get("total_tracks"):
                trck_str += f"/{meta['total_tracks']}"
            from mutagen.id3 import TRCK
            audio.tags.add(TRCK(encoding=3, text=trck_str))
            
        # Cover Art
        if cover_data:
            audio.tags.add(
                APIC(
                    encoding=3, # 3 is UTF-8
                    mime='image/jpeg', # Detect? Assuming usually jpeg/png
                    type=3, # 3 is front cover
                    desc=u'Cover',
                    data=cover_data
                )
            )
        audio.save()

    def _tag_m4a(self, filepath: str, meta: dict, cover_data: Optional[bytes]):
        audio = MP4(filepath)
        
        if meta.get("title"):
            audio.tags["\xa9nam"] = meta["title"]
        if meta.get("artist"):
            audio.tags["\xa9ART"] = meta["artist"]
        if meta.get("album"):
            audio.tags["\xa9alb"] = meta["album"]
        if meta.get("genre"):
            audio.tags["\xa9gen"] = meta["genre"]
        if meta.get("year"):
            audio.tags["\xa9day"] = str(meta["year"])
            
        if meta.get("track_num"):
            total = meta.get("total_tracks", 0)
            # trkn is a list of tuples: [(track_num, total_tracks)]
            audio.tags["trkn"] = [(meta["track_num"], total)]
            
        if cover_data:
            audio.tags["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            
        audio.save()

    def _print_file_tags(self, filepath):
        try:
            f = mutagen.File(filepath)
            if f and f.tags:
                print(f"--- Tags for {os.path.basename(filepath)} ---")
                print(f.tags.pprint())
                print("-------------------------------------------")
        except Exception:
            pass

    def detect_mode(self, page: Page) -> str:
        """Detect if the page is a single track or a list (album/artist)."""
        if self.args.mode != "auto":
            return self.args.mode
            
        url = page.url
        # Single track pages usually contain 'play-' in the URL
        if "play-" in url:
            return "track"
        
        return "artist" # Default to list mode (artist/album are similar lists) 

    async def process_single_track(self, page: Page, track_num: int = 1, total_tracks: int = 1):
        """Handle a page that represents a single track."""
        try:
            # Wait for download button
            download_btn = page.locator(".downloadTrack").first
            await download_btn.wait_for(state="visible", timeout=self.args.timeout * 1000)
            
            # --- Metadata Extraction (User Specified Selectors) ---
            
            # 1. Title: From h4.font-bold (as previously requested, verified)
            page_title_element = await page.query_selector("h4.font-bold[title]")
            page_title_text = await page_title_element.get_attribute("title") if page_title_element else None
            
            # 2. Breadcrumbs: Genre, Artist, Album
            # Selector: .card-header nav a
            # Index 0: Genre
            # Index 1: Artist
            # Index 2: Album (if present)
            breadcrumbs = await page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('.card-header nav a'));
                return links.map(link => link.innerText.trim());
            }""")
            
            bc_genre = breadcrumbs[0] if len(breadcrumbs) > 0 else None
            bc_artist = breadcrumbs[1] if len(breadcrumbs) > 1 else None
            bc_album = breadcrumbs[2] if len(breadcrumbs) > 2 else None
            
            # 3. Artwork: .containerTrack img
            cover_url = await page.evaluate("""() => {
                const img = document.querySelector('.containerTrack img');
                return img ? img.src : null;
            }""")
            
            # 4. Date/Year: Extract from specified small element
            # Selector: small.mx-2.pr-md-1 (contains date string like "٣/ربيع الثاني/١٤٢٦ هـ")
            date_text = await page.evaluate("""() => {
                // Try to find the date element containing the hijri date
                const smalls = Array.from(document.querySelectorAll('small.mx-2.pr-md-1'));
                // Usually it's the one under the artist name
                for (let s of smalls) {
                    if (s.innerText.includes('هـ')) return s.innerText;
                }
                return null;
            }""")
            
            gregorian_year = None
            if date_text:
                gregorian_year = self._parse_hijri_year(date_text)
            
            # Fallback values from data attributes
            data_title = await download_btn.get_attribute("data-track-title")
            raw_name = await download_btn.get_attribute("name")
            data_artist_attr = await download_btn.get_attribute("data-artist") # fallback

            # Construct Final Metadata
            # Genre: User override > Breadcrumb > Default
            final_genre = self.args.genre if self.args.genre else bc_genre
            
            final_title = page_title_text if page_title_text else (data_title if data_title else raw_name)
            
            # Artist: Breadcrumb > Page Element > Attribute > Unknown
            final_artist = bc_artist if bc_artist else (data_artist_attr if data_artist_attr else "Unknown Artist")
            
            final_album = bc_album # Can be None

            if not raw_name:
                raw_name = await page.title()
            
            meta = {
                "title": final_title,
                "artist": final_artist,
                "album": final_album,
                "genre": final_genre,
                "year": gregorian_year,
                "track_num": track_num,
                "total_tracks": total_tracks,
                "cover_url": cover_url
            }
            
            # Sanitize filename
            filename = self._sanitize_filename(final_title)
            
            if not filename.lower().endswith(".mp3"):
                filename += ".mp3"
                
            # Organize by Album if available
            target_dir = self.download_dir
            if meta["album"]:
                sanitized_album = self._sanitize_filename(meta["album"])
                if sanitized_album:
                    target_dir = os.path.join(self.download_dir, sanitized_album)
            
            os.makedirs(target_dir, exist_ok=True)
            output_path = os.path.join(target_dir, filename)
            
            if self.args.resume and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"Skipping existing file: {filename}")
                self.stats["skipped"] += 1
                return

            if self.args.dry_run:
                logger.info(f"[Dry Run] Would download to: {target_dir} | File: {filename} | Meta: {meta}")
                self.stats["downloaded"] += 1 # Count as success for dry-run
                return

            logger.info(f"Downloading: {filename} to {target_dir}")
            
            # Setup download listener
            async with page.expect_download(timeout=self.args.timeout * 1000) as download_info:
                # Click logic: sometimes standard click works, sometimes JS click needed
                try:
                    await download_btn.click()
                except Exception:
                    await download_btn.evaluate("element => element.click()")
            
            download = await download_info.value
            await download.save_as(output_path)
            
            logger.info(f"Downloaded: {filename}. Tagging...")
            
            # Fetch cover if needed
            cover_bytes = None
            if meta["cover_url"] and self.args.cover:
                cover_bytes = await self._fetch_cover_art(meta["cover_url"])
                
            self.tag_file(output_path, meta, cover_bytes)
            
            self.stats["downloaded"] += 1
            
            # Polite delay
            await asyncio.sleep(self.args.delay_ms / 1000)
            
        except Exception as e:
            logger.error(f"Failed to download track from {page.url}: {e}")
            self.stats["failed"] += 1

    async def process_list_page(self, context: BrowserContext, page: Page):
        """Handle artist or album pages.
        - Artists usually list 'releaseItem' (Albums).
        - Albums list 'trackItem' (Tracks) in '.filterItems'.
        """
        
        # Check for Albums first (Artist Page)
        # Selector: .contentCats .releaseItem a (link to album)
        # We only look inside .contentCats to avoid sidebars
        album_links = await page.evaluate("""() => {
            const links = [];
            // Artist page: Albums are in .contentCats .releaseItem
            const items = document.querySelectorAll('.contentCats .releaseItem a');
            items.forEach(a => {
                if (a.href && !links.includes(a.href)) links.push(a.href);
            });
            return links;
        }""")
        
        if album_links:
            logger.info(f"Found {len(album_links)} ALBUMS. Processing each album...")
            for album_url in album_links:
                logger.info(f"Navigating to Album: {album_url}")
                await self._download_task(context, album_url) # _download_task calls process_single_track?? No, wait. 
                # _download_task calls process_single_track by default in my old code.
                # I need to fix _download_task to define logic based on url/content.
                # Actually, recursively calling process_list_page is risky if detecting logic is weak.
                # Let's create a helper that navigates and decides.
            return

        # If no albums, look for Tracks (Album Page)
        # STRICT SELECTOR: ul.filterItems li.trackItem
        track_urls = []
        
        while True:
            # Scrape tracks STRICTLY from the main filter/list area
            new_urls = await page.evaluate("""() => {
                const links = [];
                // Album/Category page: Tracks are usually in .filterItems
                // We STRICTLY avoid 'last added' or sidebar items
                const validContainers = document.querySelectorAll('ul.filterItems, #contentBody .card-body ul.list-unstyled'); 
                // Note: .filterItems is best. Fallback to generic if careful.
                // In album example: ul.filterItems. In artist example: no tracks in main.
                
                const containers = document.querySelectorAll('ul.filterItems');
                containers.forEach(container => {
                    container.querySelectorAll('li.trackItem .media-body a[href^="play-"]').forEach(a => {
                         links.push(a.href);
                    });
                });
                return links;
            }""")
            
            if not new_urls:
                # If no tracks found in .filterItems, maybe it's a different layout?
                # But user specifically complained about "suggested songs" -> implies my previous lax selector found them.
                # So finding NOTHING is better than finding WRONG things.
                # Log warning if 0 tracks
                pass

            for url in new_urls:
                if url not in self.processed_urls:
                    self.processed_urls.add(url)
                    track_urls.append(url)
            
            logger.info(f"Found {len(track_urls)} tracks so far...")
            self.stats["found"] = len(track_urls)
            
            if self.args.max_items and len(track_urls) >= self.args.max_items:
                break
                
            # Pagination logic
            # Refined usage to avoid 'Strict Mode' violation with "has-text" on names like Al-Muzidi
            # We use .first to be safe if multiple exist, and avoid broad text scraping for "More" if strictly not button.
            # Prefer classes: .pagination .next a, .loadMore (if exists)
            
            # Using specific button selectors or 'next' classes
            next_btn = page.locator("ul.pagination li.next a, .loadMore, #loadMoreBtn").first
            
            # Optional: Check exact text "التالي" if specific button logic fails, but scope it
            # if not await next_btn.is_visible():
            #    next_btn = page.locator("a:text-is('التالي')").first
            
            if await next_btn.is_visible():
                try:
                    await next_btn.click()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2) 
                except Exception:
                    break
            else:
                break 
        
        logger.info(f"Processing {len(track_urls)} tracks...")
        
        tasks = []
        total = len(track_urls)
        for i, url in enumerate(track_urls, start=1):
            if self.args.max_items and len(tasks) >= self.args.max_items:
                break
            tasks.append(self._download_task(context, url, track_num=i, total_tracks=total))
            
        await asyncio.gather(*tasks)

    async def _download_task(self, context: BrowserContext, url: str, track_num: int = 1, total_tracks: int = 1):
        async with self.semaphore:
            page = await context.new_page()
            try:
                await page.goto(url, timeout=self.args.timeout * 1000)
                
                # Check what kind of page this is
                # If it's a track page, download it
                # If it's an album page (from artist page recursion), process list
                
                mode = self.detect_mode(page)
                if mode == "track":
                    await self.process_single_track(page, track_num, total_tracks)
                else:
                    # Recursive for album pages found inside artist page
                    await self.process_list_page(context, page)
                    
            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                self.stats["failed"] += 1
            finally:
                await page.close()

    def _parse_hijri_year(self, text: str) -> Optional[str]:
        """Parse Hijri year from text like '٣/ربيع الثاني/١٤٢٦ هـ' and convert to Gregorian."""
        try:
            # Normalize arabic digits
            text = text.translate(self.arabic_digits_map)
            # Find year pattern: 4 digits
            match = re.search(r'(\d{4})', text)
            if match:
                hijri = int(match.group(1))
                # Approx conversion: G = (H * 0.970224) + 621.5774
                gregorian = int((hijri * 0.970224) + 621.5774)
                return str(gregorian)
        except Exception:
            pass
        return None

    def _sanitize_filename(self, name: str) -> str:
        # Sanitize for filesystem
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = name.strip()
        if self.args.sanitize:
            # stricter sanitization if requested
            name = re.sub(r'[^\w\s\.-]', '', name)
        return name

    def _print_summary(self):
        print("\n" + "="*40)
        print("DOWNLOAD SUMMARY")
        print("="*40)
        print(f"Total Found:      {self.stats['found']}")
        print(f"Downloaded:     {self.stats['downloaded']}")
        print(f"Skipped:        {self.stats['skipped']}")
        print(f"Failed:         {self.stats['failed']}")
        print("="*40 + "\n")

def parse_arguments():
    parser = argparse.ArgumentParser(description="Shiavoice.com Downloader")
    parser.add_argument("url", help="URL to download from")
    parser.add_argument("--out", default="./downloads", help="Output directory")
    parser.add_argument("--mode", choices=["auto", "track", "album", "artist"], default="auto", help="Force processing mode")
    # Headless default as requested ("make headless is the default")
    parser.add_argument("--visible", action="store_true", help="Run browser visibly (default: Headless)")
    
    parser.add_argument("--timeout", type=int, default=30, help="Navigation timeout in seconds")
    parser.add_argument("--concurrency", type=int, default=1, help="Max concurrent downloads")
    parser.add_argument("--delay-ms", type=int, default=1000, help="Delay between actions in ms")
    parser.add_argument("--retries", type=int, default=3, help="Number of retries for failed downloads")
    parser.add_argument("--resume", action="store_true", help="Skip existing files")
    parser.add_argument("--cookies", help="File to load/save cookies")
    parser.add_argument("--log", help="Log file path")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, do not download")
    parser.add_argument("--max-items", type=int, help="Limit number of downloads")
    parser.add_argument("--sanitize", action="store_true", help="Sanitize filenames")
    parser.add_argument("--template", help="Filename template (e.g. '{artist}/{album}/{title}.mp3')")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    
    # Tagging options
    parser.add_argument("--genre", help="Default genre to set")
    parser.add_argument("--no-tag", action="store_false", dest="tag", help="Disable tagging")
    parser.add_argument("--no-cover", action="store_false", dest="cover", help="Disable cover art embedding")
    parser.add_argument("--covers-cache", help="Directory for cover art cache")
    parser.add_argument("--print-tags", action="store_true", help="Print tags after writing for verification")
    parser.set_defaults(tag=True, cover=True)
    
    return parser.parse_args()

async def main():
    args = parse_arguments()
    # Parse arguments
    # Note: Headless is default (True). User must pass --visible to see browser.
    
    downloader = ShiavoiceDownloader(args)
    await downloader.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
