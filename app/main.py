import sys
import argparse
import asyncio
import logging
import uvicorn
from typing import Any
from app.downloader import ShiavoiceDownloader, DownloadConfig, TrackInfo

# Helper to map argparse to DownloadConfig
def args_to_config(args) -> DownloadConfig:
    return DownloadConfig(
        url=args.url if hasattr(args, "url") else "",
        output_dir=args.out,
        mode=args.mode,
        headless=not args.visible,
        timeout=args.timeout,
        concurrency=args.concurrency,
        delay_ms=args.delay_ms,
        retries=args.retries,
        resume=args.resume,
        cookies_file=args.cookies,
        dry_run=args.dry_run,
        max_items=args.max_items,
        sanitize_filenames=args.sanitize,
        genre=args.genre,
        tag=args.tag,
        cover=args.cover,
        covers_cache_dir=args.covers_cache,
        verbose=args.verbose
    )

async def run_cli(args):
    config = args_to_config(args)
    # Configure logging
    from app.downloader.utils import setup_logging
    setup_logging(args.verbose, args.log)
    
    # Callback to print progress
    def cli_callback(event: str, data: Any):
        if event == "start":
            print(f"Started processing: {data['url']}")
        elif event == "track_start":
            if isinstance(data, TrackInfo):
                print(f"Downloading: {data.filename or data.title}...")
            else:
                print(f"Downloading track...")
        elif event == "track_complete":
            print(f"[OK] {data.filename}")
        elif event == "track_failed":
            print(f"[ERROR] {data['url']} - {data['error']}")
        elif event == "finished":
            stats = data
            print("\n" + "="*40)
            print("SUMMARY")
            print(f"Found:      {stats.get('found', 0)}")
            print(f"Downloaded: {stats.get('downloaded', 0)}")
            print(f"Skipped:    {stats.get('skipped', 0)}")
            print(f"Failed:     {stats.get('failed', 0)}")
            print("="*40 + "\n")

    downloader = ShiavoiceDownloader(config, progress_callback=cli_callback)
    await downloader.run()

def run_web(args):
    # Determine absolute path for web app to avoid import issues
    sys.path.append(".") 
    # Launch uvicorn
    # process_workers=1 to avoid complexity with shared state if we used workers
    uvicorn.run("app.web.server:app", host=args.host, port=args.port, reload=False, workers=1)

def main():
    parser = argparse.ArgumentParser(description="Shiavoice.com Tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # --- Download Command ---
    dl_parser = subparsers.add_parser("download", help="CLI Downloader")
    dl_parser.add_argument("url", help="URL to download from")
    dl_parser.add_argument("--out", default="/music", help="Output directory")
    dl_parser.add_argument("--mode", choices=["auto", "track", "album", "artist"], default="auto", help="Force set mode")
    dl_parser.add_argument("--visible", action="store_true", help="Run browser visibly")
    dl_parser.add_argument("--timeout", type=int, default=30)
    dl_parser.add_argument("--concurrency", type=int, default=1)
    dl_parser.add_argument("--delay-ms", type=int, default=1000)
    dl_parser.add_argument("--retries", type=int, default=3)
    dl_parser.add_argument("--resume", action="store_true", default=True, help="Skip existing (default: True)")
    dl_parser.add_argument("--no-resume", action="store_false", dest="resume", help="Disable resume")
    dl_parser.add_argument("--cookies", help="Cookies file")
    dl_parser.add_argument("--log", help="Log file")
    dl_parser.add_argument("--dry-run", action="store_true")
    dl_parser.add_argument("--max-items", type=int)
    dl_parser.add_argument("--sanitize", action="store_true")
    dl_parser.add_argument("--verbose", action="store_true")
    dl_parser.add_argument("--genre")
    dl_parser.add_argument("--no-tag", action="store_false", dest="tag")
    dl_parser.add_argument("--no-cover", action="store_false", dest="cover")
    dl_parser.add_argument("--covers-cache")
    dl_parser.set_defaults(tag=True, cover=True)
    
    # --- Web Command ---
    web_parser = subparsers.add_parser("web", help="Web Server")
    web_parser.add_argument("--port", type=int, default=8080)
    web_parser.add_argument("--host", default="0.0.0.0")
    
    args = parser.parse_args()
    
    if args.command == "download":
        try:
            asyncio.run(run_cli(args))
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(130)
    elif args.command == "web":
        run_web(args)

if __name__ == "__main__":
    main()
