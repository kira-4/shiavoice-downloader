import os
import asyncio
import logging
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional

from app.web.manager import manager, Job

logger = logging.getLogger("shiavoice.web.server")

# Initialize API
app = FastAPI(title="Shiavoice Web UI")

# Mount Static & Templates
# We assume we run from root
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
templates = Jinja2Templates(directory="app/web/templates")

# Models for API
class JobCreate(BaseModel):
    url: str
    output_dir: Optional[str] = "/music"
    mode: str = "auto"
    genre: Optional[str] = None
    resume: bool = True
    delay: int = 1000
    retries: int = 3
    tag: bool = True
    cover: bool = True
    headless: bool = True
    max_items: Optional[int] = None
    dry_run: bool = False

# Background Worker Startup
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(manager.start_worker())

# Routes
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/jobs")
async def list_jobs():
    jobs = manager.list_jobs()
    return {"jobs": [j.to_dict() for j in jobs]}

@app.post("/api/jobs")
async def create_job(job_in: JobCreate):
    options = job_in.dict()
    # Sanitize: Enforce restricted output paths if needed?
    # For now trust user as requested (Personal LXC)
    url = options.pop("url")
    job = manager.create_job(url, options)
    await manager._emit_event("job_created", job.to_dict())
    return job.to_dict()

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    manager.cancel_job(job_id)
    return {"status": "ok"}

@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    manager.delete_job(job_id)
    return {"status": "ok"}

@app.get("/api/events")
async def sse_events():
    async def event_generator():
        q = await manager.subscribe()
        try:
            while True:
                data = await q.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            manager.unsubscribe(q)
        except Exception as e:
            logger.error(f"SSE Error: {e}")
            manager.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Add a healthcheck
@app.get("/health")
def health():
    return {"status": "ok"}
