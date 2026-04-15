"""FastAPI web application for QBR Portfolio Health Report."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from qbr.flags import aggregate_flags_by_project
from qbr.llm import HAIKU_MODEL, UsageTracker, create_client
from qbr.models import ExtractedItem  # noqa: TC001
from qbr.parser import parse_all_emails
from qbr.pipeline import run_pipeline_for_thread
from qbr.report import build_report_json, generate_report

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="QBR Portfolio Health Report", version="0.1.0")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.cache_size = 0  # disable template caching to avoid unhashable key issues

SAMPLE_DATA_DIR = Path(__file__).parent.parent.parent / "task" / "sample_data"

# In-memory job store
jobs: dict[str, dict[str, Any]] = {}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "jobs": list(jobs.items()),
            "has_sample_data": SAMPLE_DATA_DIR.exists(),
        },
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/analyze")
async def start_analysis(request: Request, files: list[UploadFile] | None = None):
    """Start an analysis job. Uses demo data if no files uploaded."""
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "progress": [],
        "result": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
    }

    # Determine input source
    if files and any(f.filename for f in files):
        # Save uploaded files to a temp directory
        upload_dir = Path(f"/tmp/qbr_uploads/{job_id}")
        upload_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.filename and f.filename.endswith(".txt"):
                content = await f.read()
                (upload_dir / f.filename).write_bytes(content)
        input_dir = upload_dir
    else:
        input_dir = SAMPLE_DATA_DIR

    # Run analysis in background
    asyncio.create_task(_run_analysis(job_id, input_dir))

    return {"job_id": job_id, "status": "queued"}


async def _run_analysis(job_id: str, input_dir: Path) -> None:
    """Run the full analysis pipeline as a background task."""
    job = jobs[job_id]
    job["state"] = "processing"

    try:
        provider = os.getenv("QBR_LLM_PROVIDER", "anthropic")
        tracker = UsageTracker()
        client = create_client(
            provider=provider,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            tracker=tracker,
        )

        # Step 1: Parse
        _log_progress(job, "Parsing emails...")
        threads = await asyncio.to_thread(parse_all_emails, input_dir)
        _log_progress(
            job,
            f"Parsed {len(threads)} threads across {len({t.project for t in threads})} projects",
        )

        # Step 2: Extract per thread
        all_items: dict[str, list[ExtractedItem]] = defaultdict(list)
        for i, thread in enumerate(threads):
            if not thread.messages:
                continue
            _log_progress(job, f"[{i + 1}/{len(threads)}] Processing: {thread.subject[:60]}...")
            try:
                items = await asyncio.to_thread(
                    run_pipeline_for_thread, thread, client, [], HAIKU_MODEL
                )
                project = thread.project or "Unknown"
                all_items[project].extend(items)
                open_count = sum(1 for it in items if it.status.value != "resolved")
                _log_progress(job, f"  → {len(items)} items ({open_count} open)")
            except Exception as e:
                _log_progress(job, f"  ⚠ Error: {e}")

        total_items = sum(len(v) for v in all_items.values())
        _log_progress(job, f"Extraction complete: {total_items} items total")

        # Step 3: Classify flags
        _log_progress(job, "Classifying Attention Flags...")
        flags_by_project = await asyncio.to_thread(aggregate_flags_by_project, all_items)
        total_flags = sum(len(f) for f in flags_by_project.values())
        _log_progress(job, f"{total_flags} flags triggered")

        # Step 4: Generate report
        _log_progress(job, "Generating report (Sonnet 4.6)...")
        report_md = await asyncio.to_thread(generate_report, flags_by_project, client)
        report_json = build_report_json(flags_by_project, report_md)

        job["state"] = "complete"
        job["result"] = {
            "report_markdown": report_md,
            "report_json": report_json,
            "usage": tracker.summary(),
        }
        _log_progress(
            job, f"Complete! {tracker.total_calls} LLM calls, ${tracker.total_cost_usd:.4f}"
        )

    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        _log_progress(job, f"Error: {e}")
        logger.exception("Analysis failed for job %s", job_id)


def _log_progress(job: dict[str, Any], message: str) -> None:
    """Add a progress message to a job."""
    job["progress"].append(
        {
            "timestamp": datetime.now().isoformat(),
            "message": message,
        }
    )


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    """SSE stream for job progress."""
    if job_id not in jobs:
        return {"error": "Job not found"}

    async def event_generator():
        last_idx = 0
        while True:
            job = jobs.get(job_id)
            if not job:
                break

            # Send new progress messages
            progress = job["progress"]
            while last_idx < len(progress):
                msg = progress[last_idx]
                yield {
                    "event": "progress",
                    "data": json.dumps(msg),
                }
                last_idx += 1

            if job["state"] == "complete":
                yield {
                    "event": "complete",
                    "data": json.dumps(
                        {
                            "report_markdown": job["result"]["report_markdown"][:500] + "...",
                            "usage": job["result"]["usage"],
                        }
                    ),
                }
                break
            elif job["state"] == "error":
                yield {
                    "event": "error",
                    "data": json.dumps({"error": job["error"]}),
                }
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    job = jobs.get(job_id)
    if not job:
        return HTMLResponse("<h1>Job not found</h1>", status_code=404)
    return templates.TemplateResponse(request, "job.html", context={"job": job})


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
async def job_report(request: Request, job_id: str):
    job = jobs.get(job_id)
    if not job or job["state"] != "complete":
        return HTMLResponse("<h1>Report not ready</h1>", status_code=404)
    return templates.TemplateResponse(
        request,
        "report.html",
        context={
            "job": job,
            "report_md": job["result"]["report_markdown"],
            "report_json": job["result"]["report_json"],
        },
    )
