import asyncio
import json
import os
import uuid
import logging
import time
from typing import Dict, List, Optional
from datetime import datetime

from app.downloader import ShiavoiceDownloader, DownloadConfig, TrackInfo

logger = logging.getLogger("shiavoice.web.manager")

class Job:
    def __init__(self, url: str, options: dict, job_id: str = None):
        self.id = job_id or str(uuid.uuid4())
        self.url = url
        self.title = None  # Friendly title (e.g. Artist - Album)
        self.options = options
        self.status = "queued"  # queued, running, completed, failing, cancelled, paused
        self.created_at = time.time()
        self.updated_at = time.time()
        self.error = None
        self.stats = {"found": 0, "downloaded": 0, "failed": 0, "skipped": 0}
        self.progress = 0
        self.tracks: List[dict] = []  # List of TrackInfo dicts
        self.current_track: str = None
        self.cover_url: str = None
        self._task = None  # asyncio Task
        self._stop_event = asyncio.Event() 
        self._pause_event = asyncio.Event()
        self._pause_event.set() # Initially running (not paused)

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "stats": self.stats,
            "options": self.options,
            "tracks": self.tracks,
            "current_track": self.current_track,
            "cover_url": self.cover_url
        }

class JobManager:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = data_dir
        self.start_time = None
        self.jobs: Dict[str, Job] = {}
        self.queue = asyncio.Queue()
        self.active_job: Optional[Job] = None
        self.db_path = os.path.join(data_dir, "jobs.jsonl")
        self.listeners: List[asyncio.Queue] = [] 
        
        os.makedirs(data_dir, exist_ok=True)
        self._load_jobs()

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self.listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.listeners:
            self.listeners.remove(q)

    async def _emit_event(self, event_type: str, data: dict):
        msg = json.dumps({"event": event_type, "data": data, "timestamp": time.time()})
        dead = []
        for q in self.listeners:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.listeners.remove(q)

    def _load_jobs(self):
        if not os.path.exists(self.db_path):
            return
        
        try:
            with open(self.db_path, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        job = Job(data["url"], data.get("options", {}), job_id=data["id"])
                        job.title = data.get("title")
                        job.status = data["status"]
                        job.created_at = data["created_at"]
                        job.updated_at = data.get("updated_at", time.time())
                        job.error = data.get("error")
                        job.stats = data.get("stats", {})
                        job.tracks = data.get("tracks", [])
                        job.cover_url = data.get("cover_url")
                        
                        # Reset stuck jobs
                        if job.status in ["running", "queued"]:
                            job.status = "failed"
                            job.error = "Interrupted by restart"
                        
                        self.jobs[job.id] = job
                    except Exception as e:
                        logger.error(f"Error loading job line: {e}")
        except Exception as e:
            logger.error(f"Failed to load jobs DB: {e}") 

    def _save_job_sync(self, job: Job):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            temp_path = self.db_path + ".tmp"
            with open(temp_path, "w") as f:
                for j in self.jobs.values():
                    f.write(json.dumps(j.to_dict()) + "\n")
            os.replace(temp_path, self.db_path)
        except Exception as e:
            logger.error(f"Failed to save jobs: {e}")

    async def _save_job(self, job: Job):
        await asyncio.to_thread(self._save_job_sync, job)

    def create_job(self, url: str, options: dict) -> Job:
        job = Job(url, options)
        self.jobs[job.id] = job
        self.queue.put_nowait(job.id)
        asyncio.create_task(self._save_job(job))
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[Job]:
        # Return sorted by created_at desc
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if job:
            if job.status in ["queued", "running"]:
                job.status = "cancelled"
                if job._task and not job._task.done():
                    job._task.cancel()
            asyncio.create_task(self._save_job(job))
            asyncio.create_task(self._emit_event("job_updated", job.to_dict()))

    def delete_job(self, job_id: str):
        if job_id in self.jobs:
            del self.jobs[job_id]
            self._save_job_sync(None)
            asyncio.create_task(self._emit_event("job_deleted", {"id": job_id}))

    async def start_worker(self):
        logger.info("Worker started")
        while True:
            try:
                job_id = await self.queue.get()
                job = self.jobs.get(job_id)
                if not job or job.status == "cancelled":
                    self.queue.task_done()
                    continue
                
                task = asyncio.create_task(self._process_job(job))
                job._task = task
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"Job {job.id} cancelled")
                self.queue.task_done()
                
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)

    async def _process_job(self, job: Job):
        logger.info(f"Processing job {job.id}: {job.url}")
        job.status = "running"
        job.updated_at = time.time()
        self.active_job = job
        await self._save_job(job)
        await self._emit_event("job_updated", job.to_dict())

        # Convert simple options dict to DownloadConfig
        # We need default values from somewhere? 
        opts = job.options
        config = DownloadConfig(
            url=job.url,
            output_dir=opts.get("output_dir", "./downloads"),
            mode=opts.get("mode", "auto"),
            headless=opts.get("headless", True),
            concurrency=opts.get("concurrency", 1),
            delay_ms=opts.get("delay", 1000),
            retries=opts.get("retries", 3),
            resume=opts.get("resume", True),
            dry_run=opts.get("dry_run", False),
            genre=opts.get("genre"),
            tag=opts.get("tag", True),
            cover=opts.get("cover", True),
            max_items=opts.get("max_items")
        )

        async def callback(event, data):
            # Update job state
            job.updated_at = time.time()
            save_needed = False
            
            if event == "found_albums":
                # Maybe update some stat?
                pass
            elif event == "found_count":
                # Ensure data.found is available
                if isinstance(data, dict):
                    count = data.get("found", 0)
                else:
                    count = data
                job.stats["found"] = count
                save_needed = True
                
            elif event == "track_start":
                if isinstance(data, TrackInfo):
                    job.current_track = data.title
                    if data.cover_url and not job.cover_url:
                        job.cover_url = data.cover_url
                        save_needed = True
                    # Attempt to set title if missing
                    if not job.title:
                        if data.album:
                             job.title = f"{data.album} - {data.artist}" if data.artist else data.album
                        else:
                             job.title = f"{data.title} - {data.artist}"
                        save_needed = True

            elif event == "track_complete":
                 job.current_track = None
                 job.stats["downloaded"] = job.stats.get("downloaded", 0) + 1
                 # We don't rely on downloader stats dict entirely to ensure real-time partial updates
                 # But downloader.stats is also updating.
                 
                 job.tracks.append({"title": data.title, "status": "done"})
                 save_needed = True

            elif event == "track_skipped":
                job.stats["skipped"] = job.stats.get("skipped", 0) + 1
                save_needed = True

            elif event == "track_failed":
                 job.stats["failed"] = job.stats.get("failed", 0) + 1
                 job.tracks.append({"title": "Unknown", "status": "failed", "error": data.get("error")})
                 save_needed = True
            
            elif event == "finished":
                job.stats = data # Final sync
                save_needed = True

            if save_needed:
                await self._save_job(job)
            
            # Broadcast update
            await self._emit_event("job_updated", job.to_dict())

        try:
            downloader = ShiavoiceDownloader(config, progress_callback=callback)
            await downloader.run()
            job.status = "completed"
            job.stats = downloader.stats # Update final stats
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            logger.error(f"Job failed: {e}")
        finally:
            self.active_job = None
            await self._save_job(job)
            await self._emit_event("job_updated", job.to_dict())
# Global Manager Instance
manager = JobManager()
