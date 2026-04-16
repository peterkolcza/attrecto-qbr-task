"""FastAPI web application for QBR Portfolio Health Report."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from qbr.flags import aggregate_flags_by_project
from qbr.llm import UsageTracker, create_hybrid_clients
from qbr.models import ExtractedItem  # noqa: TC001
from qbr.parser import parse_all_emails
from qbr.pipeline import run_pipeline_for_thread
from qbr.report import build_report_json, generate_report
from qbr.seed import get_demo_projects

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="QBR Portfolio Health Report", version="0.1.0")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.cache_size = 0  # disable template caching to avoid unhashable key issues


def _md_to_html(text: str) -> str:
    """Convert markdown to HTML safely (no raw LLM output as HTML)."""
    import bleach
    import markdown as md_lib

    raw_html = md_lib.markdown(text, extensions=["tables", "fenced_code"])
    # Sanitize: only allow safe HTML tags (prevent XSS from LLM output)
    return bleach.clean(
        raw_html,
        tags=[
            "h1",
            "h2",
            "h3",
            "h4",
            "p",
            "ul",
            "ol",
            "li",
            "strong",
            "em",
            "code",
            "pre",
            "blockquote",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "hr",
            "br",
            "a",
        ],
        attributes={"a": ["href"]},
    )


templates.env.filters["markdown"] = _md_to_html

SAMPLE_DATA_DIR = Path(__file__).parent.parent.parent / "task" / "sample_data"

# In-memory job store (evicts oldest when exceeding MAX_JOBS)
MAX_JOBS = 20
jobs: dict[str, dict[str, Any]] = {}


def _evict_old_jobs() -> None:
    """Remove oldest completed jobs if over MAX_JOBS limit."""
    if len(jobs) <= MAX_JOBS:
        return
    completed = [(jid, j) for jid, j in jobs.items() if j["state"] in ("complete", "error")]
    completed.sort(key=lambda x: x[1].get("created_at", ""))
    while len(jobs) > MAX_JOBS and completed:
        jid, _ = completed.pop(0)
        del jobs[jid]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "jobs": list(jobs.items()),
            "has_sample_data": SAMPLE_DATA_DIR.exists(),
            "projects": get_demo_projects(),
        },
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@app.post("/analyze")
async def start_analysis(request: Request, files: list[UploadFile] | None = None):
    """Start an analysis job. Uses demo data if no files uploaded.

    If a demo analysis is already running, redirects to it instead of starting a duplicate.
    """
    _evict_old_jobs()

    is_demo = not (files and any(f.filename for f in files))

    # Dedup: if a demo job is already running, redirect to it
    if is_demo:
        for existing_id, existing_job in jobs.items():
            if existing_job.get("source") == "demo" and existing_job["state"] in (
                "queued",
                "processing",
            ):
                return RedirectResponse(url=f"/jobs/{existing_id}", status_code=303)

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "source": "demo" if is_demo else "upload",
        "state": "queued",
        "progress": [],
        "result": None,
        "error": None,
        "created_at": datetime.now(UTC).isoformat(),
    }

    # Determine input source
    MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB per file
    MAX_FILES = 50
    if not is_demo:
        upload_dir = Path(f"/tmp/qbr_uploads/{job_id}")
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_count = 0
        for f in files or []:
            if not f.filename:
                continue
            safe_name = Path(f.filename).name
            if not safe_name.endswith(".txt"):
                continue
            content = await f.read(MAX_UPLOAD_SIZE + 1)
            if len(content) > MAX_UPLOAD_SIZE:
                continue
            (upload_dir / safe_name).write_bytes(content)
            file_count += 1
            if file_count >= MAX_FILES:
                break
        input_dir = upload_dir
    else:
        input_dir = SAMPLE_DATA_DIR

    # Rate limit: max 3 concurrent analyses
    active = sum(1 for j in jobs.values() if j["state"] in ("queued", "processing"))
    if active > 3:
        return HTMLResponse(
            "<h1>Too many concurrent analyses</h1><p>Please wait and try again.</p>"
            '<p><a href="/">Back to Dashboard</a></p>',
            status_code=429,
        )

    task = asyncio.create_task(_run_analysis(job_id, input_dir))
    jobs[job_id]["_task"] = task

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


async def _run_analysis(job_id: str, input_dir: Path) -> None:
    """Run the full analysis pipeline as a background task."""
    job = jobs[job_id]
    job["state"] = "processing"

    try:
        provider = os.getenv("QBR_LLM_PROVIDER", "ollama")
        tracker = UsageTracker()
        extraction_client, extraction_model, synthesis_client, synthesis_model = (
            create_hybrid_clients(
                extraction_provider=os.getenv("QBR_EXTRACTION_PROVIDER", provider),
                synthesis_provider=os.getenv("QBR_SYNTHESIS_PROVIDER", provider),
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:e2b"),
                tracker=tracker,
            )
        )

        # Step 1: Parse
        _log_progress(job, "Parsing emails...")
        threads = await asyncio.to_thread(parse_all_emails, input_dir)
        _log_progress(
            job,
            f"Parsed {len(threads)} threads across {len({t.project for t in threads})} projects",
        )

        # Load colleagues roster for role-based severity scoring
        from qbr.parser import parse_colleagues

        colleagues_path = input_dir / "Colleagues.txt"
        colleagues = parse_colleagues(colleagues_path) if colleagues_path.exists() else []

        # Step 2: Extract per thread
        all_items: dict[str, list[ExtractedItem]] = defaultdict(list)
        for i, thread in enumerate(threads):
            if not thread.messages:
                continue

            # Detailed per-email header
            first_msg = thread.messages[0]
            off_topic_count = sum(1 for m in thread.messages if m.is_off_topic)
            _log_progress(
                job, f'[{i + 1}/{len(threads)}] {thread.source_file} — "{thread.subject[:80]}"'
            )
            _log_progress(
                job,
                f"  From: {first_msg.sender_name} <{first_msg.sender_email}> | "
                f"Date: {first_msg.date.strftime('%Y-%m-%d %H:%M')}",
            )
            _log_progress(
                job,
                f"  Project: {thread.project or 'Unknown'} "
                f"({len(thread.messages)} msgs, {off_topic_count} off-topic)",
            )
            _log_progress(job, f"  Extracting with {extraction_model}...")

            try:
                items, metrics = await asyncio.to_thread(
                    run_pipeline_for_thread, thread, extraction_client, colleagues, extraction_model
                )
                project = thread.project or "Unknown"
                all_items[project].extend(items)

                # Stage A summary
                by_type = metrics["items_by_type"]
                _log_progress(
                    job,
                    f"  Stage A ({metrics['extraction_time_ms']}ms): {sum(by_type.values())} items "
                    f"({by_type['question']} questions, {by_type['commitment']} commitments, "
                    f"{by_type['risk']} risks, {by_type['blocker']} blockers)",
                )
                # Stage B summary
                rb = metrics["resolution_breakdown"]
                _log_progress(
                    job,
                    f"  Stage B ({metrics['resolution_time_ms']}ms): "
                    f"{rb['open']} open, {rb['ambiguous']} ambiguous, {rb['resolved']} resolved",
                )
                # Stage C summary (deterministic)
                sb = metrics["severity_breakdown"]
                _log_progress(
                    job,
                    f"  Stage C (severity): {sb['critical']} critical, {sb['high']} high, "
                    f"{sb['medium']} medium, {sb['low']} low",
                )

                total_sec = metrics["total_time_ms"] / 1000
                _log_progress(
                    job,
                    f"  ✓ Done in {total_sec:.1f}s — {len(items)} kept, "
                    f"{metrics['grounding_drops']} dropped by grounding",
                )
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
        _log_progress(job, f"Generating report ({synthesis_model})...")
        report_md = await asyncio.to_thread(
            generate_report, flags_by_project, synthesis_client, synthesis_model
        )
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
        job["error"] = "Analysis failed. Check server logs for details."
        _log_progress(job, "Error: analysis failed")
        logger.exception("Analysis failed for job %s: %s", job_id, e)
    finally:
        # Cleanup uploaded temp files
        import shutil

        if input_dir != SAMPLE_DATA_DIR and input_dir.exists():
            shutil.rmtree(input_dir, ignore_errors=True)


def _log_progress(job: dict[str, Any], message: str) -> None:
    """Add a progress message to a job."""
    job["progress"].append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "message": message,
        }
    )


@app.get("/api/jobs/{job_id}/progress")
async def job_progress(job_id: str):
    """JSON endpoint for polling job progress (used by the frontend JS)."""
    job = jobs.get(job_id)
    if not job:
        return {"error": "Job not found", "state": "unknown", "progress": []}
    result: dict[str, Any] = {
        "state": job["state"],
        "progress": job["progress"],
        "error": job.get("error"),
        "usage": job["result"]["usage"] if job.get("result") else None,
    }
    return result


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
