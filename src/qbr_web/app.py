"""FastAPI web application for QBR Portfolio Health Report."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.sessions import SessionMiddleware

from qbr.flags import aggregate_flags_by_project, classify_flags
from qbr.llm import UsageTracker, create_hybrid_clients
from qbr.models import ExtractedItem  # noqa: TC001
from qbr.parser import parse_all_emails
from qbr.pipeline import run_pipeline_for_thread
from qbr.report import build_report_json, generate_report
from qbr.seed import get_demo_projects
from qbr_web.auth import (
    auth_enabled,
    check_rate_limit,
    get_session_secret,
    is_public_path,
    record_login_attempt,
    verify_credentials,
)

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(title="QBR Portfolio Health Report", version="0.1.0")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Redirect unauthenticated requests to /login when auth is enabled.

    Must be registered BEFORE SessionMiddleware so session is available.
    In Starlette, middleware added later is wrapped INSIDE earlier ones,
    so auth_middleware runs AFTER SessionMiddleware populates request.session.
    """
    if auth_enabled() and not is_public_path(request.url.path) and not request.session.get("user"):
        next_url = request.url.path
        return RedirectResponse(url=f"/login?next={next_url}", status_code=303)
    return await call_next(request)


# SessionMiddleware MUST be added AFTER auth_middleware so it wraps it (becomes outer)
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    same_site="lax",
    https_only=False,  # set True in production behind HTTPS
)


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


def _strict_urlencode(value: str) -> str:
    """Like Jinja's `urlencode` but also escapes '/' — important for path params.

    Jinja's builtin leaves '/' untouched, which breaks FastAPI's default `{name}`
    path-param converter when a project name contains a slash.
    """
    from urllib.parse import quote

    return quote(str(value), safe="")


templates.env.filters["strict_urlencode"] = _strict_urlencode

SAMPLE_DATA_DIR = Path(__file__).parent.parent.parent / "task" / "sample_data"

# In-memory job store (evicts oldest when exceeding MAX_JOBS)
MAX_JOBS = 20
jobs: dict[str, dict[str, Any]] = {}

# In-memory live project health state — overlays seed data on the dashboard.
# Keyed by project name. Not persisted across server restart (matches jobs dict).
project_state: dict[str, dict[str, Any]] = {}


def _health_from_flags(flags: list[Any]) -> str:
    """Map a list of AttentionFlag (or dicts) to a health label.

    Rule:
    - any flag with severity=critical  → 'critical'
    - any flag with any other severity → 'warning'
    - empty flag list                  → 'good'
    """
    if not flags:
        return "good"
    for f in flags:
        sev = f.severity if hasattr(f, "severity") else f.get("severity")
        sev_str = str(sev)
        if sev_str == "critical" or sev_str.endswith(".CRITICAL"):
            return "critical"
    return "warning"


def _count_by_severity(flags: list[Any]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in flags:
        sev = f.severity if hasattr(f, "severity") else f.get("severity")
        sev_str = str(sev).split(".")[-1].lower()
        if sev_str in counts:
            counts[sev_str] += 1
    return counts


def _merge_incremental_flags(project: str, new_flags: list[Any], job_id: str) -> None:
    """Merge per-thread flags into project_state as extraction progresses (R6).

    Called during the per-thread loop in _run_analysis so dashboard flag counts
    rise incrementally. _finalize_project_state still runs at end of job to
    apply cross-project conflict detection and replace the final counts.
    """
    from qbr.models import AttentionFlag

    if not new_flags:
        # Ensure the project appears in state even before first flag lands
        # (so dashboard can show active flash + 0 flags). Also refresh
        # last_updated / latest_job_id so pre-existing entries don't keep
        # pointing at a prior run while a new one is in progress.
        entry = project_state.setdefault(
            project,
            {
                "health": "good",
                "flag_count": 0,
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
                "flags": [],
                "last_updated": datetime.now(UTC).isoformat(),
                "latest_job_id": job_id,
            },
        )
        entry["last_updated"] = datetime.now(UTC).isoformat()
        entry["latest_job_id"] = job_id
        return

    serialized_new = [
        f.model_dump(mode="json") if isinstance(f, AttentionFlag) else f for f in new_flags
    ]
    entry = project_state.setdefault(
        project,
        {
            "health": "good",
            "flag_count": 0,
            "critical_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "flags": [],
            "last_updated": datetime.now(UTC).isoformat(),
            "latest_job_id": job_id,
        },
    )
    entry["flags"].extend(serialized_new)
    entry["flag_count"] = len(entry["flags"])
    counts = _count_by_severity(entry["flags"])
    entry["critical_count"] = counts["critical"]
    entry["high_count"] = counts["high"]
    entry["medium_count"] = counts["medium"]
    entry["low_count"] = counts["low"]
    entry["health"] = _health_from_flags(entry["flags"])
    entry["last_updated"] = datetime.now(UTC).isoformat()
    entry["latest_job_id"] = job_id


def _finalize_project_state(flags_by_project: dict[str, list[Any]], job_id: str) -> None:
    """Write final state for every project that produced flags.

    Called at end of run after aggregate_flags_by_project. Merges with whatever
    Unit 2's per-thread classification already accumulated — does not clobber
    incremental progress if higher.
    """
    from qbr.models import AttentionFlag

    now_iso = datetime.now(UTC).isoformat()
    for project_name, flags in flags_by_project.items():
        serialized = [
            f.model_dump(mode="json") if isinstance(f, AttentionFlag) else f for f in flags
        ]
        final_counts = _count_by_severity(flags)
        final_count = len(flags)

        # The incremental path (Unit 2) can accumulate more raw flags than the
        # end-of-run prioritized list (which truncates to top 10 per project).
        # Trust the final prioritized list as the source of truth — mismatches
        # between flag_count and len(flags) would mislead the drill-down page.
        existing = project_state.get(project_name, {})
        existing_count = existing.get("flag_count", 0)
        if existing_count > final_count:
            logger.info(
                "project_state[%s]: incremental count %d trimmed to final %d after prioritization",
                project_name,
                existing_count,
                final_count,
            )

        project_state[project_name] = {
            "health": _health_from_flags(flags),
            "flag_count": final_count,
            "critical_count": final_counts["critical"],
            "high_count": final_counts["high"],
            "medium_count": final_counts["medium"],
            "low_count": final_counts["low"],
            "flags": serialized,
            "last_updated": now_iso,
            "latest_job_id": job_id,
        }


def _build_projects_state_payload() -> dict[str, Any]:
    """Build the dashboard state payload shared by GET / and GET /api/projects/state.

    Shape:
        {
          "is_running": bool,
          "active_project": str | None,
          "projects": {name: {health, flag_count, critical_count, high_count,
                              medium_count, low_count, last_updated}}
        }

    The per-project blob intentionally EXCLUDES 'flags' — that lives on the
    drill-down endpoint, not on the frequently-polled one.
    """
    is_running = any(j["state"] in ("queued", "processing") for j in jobs.values())
    active_project: str | None = None
    if is_running:
        for j in jobs.values():
            if j["state"] in ("queued", "processing") and j.get("active_project"):
                active_project = j["active_project"]
                break

    # Merge seed project names with live state. Live state wins. Projects in
    # state but not in seed (from uploaded emails) also appear.
    projects_payload: dict[str, dict[str, Any]] = {}
    seed_names = {p["name"] for p in get_demo_projects()}
    for name in seed_names:
        live = project_state.get(name)
        if live:
            projects_payload[name] = {k: v for k, v in live.items() if k != "flags"}
        else:
            projects_payload[name] = {"health": "unknown"}
    for name, live in project_state.items():
        if name not in seed_names:
            projects_payload[name] = {k: v for k, v in live.items() if k != "flags"}

    return {
        "is_running": is_running,
        "active_project": active_project,
        "projects": projects_payload,
    }


def _evict_old_jobs() -> None:
    """Remove oldest completed jobs if over MAX_JOBS limit."""
    if len(jobs) <= MAX_JOBS:
        return
    completed = [(jid, j) for jid, j in jobs.items() if j["state"] in ("complete", "error")]
    completed.sort(key=lambda x: x[1].get("created_at", ""))
    while len(jobs) > MAX_JOBS and completed:
        jid, _ = completed.pop(0)
        del jobs[jid]


@app.post("/reset")
async def reset_state(request: Request):
    """Clear all in-memory state (jobs + project_state) so the demo can be
    re-run from scratch. Cancels any in-flight analysis tasks first.
    """
    for job in jobs.values():
        task = job.get("_task")
        if task is not None and not task.done():
            task.cancel()
    jobs.clear()
    project_state.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    payload = _build_projects_state_payload()
    # Merge seed metadata (PM, team, q3_focus) with live state per project.
    seed_projects = get_demo_projects()
    seed_by_name = {p["name"]: p for p in seed_projects}
    projects_view = []
    for name, live in payload["projects"].items():
        base = seed_by_name.get(name, {"name": name, "team": [], "team_size": 0})
        merged = {**base, **live, "name": name}  # live overrides health
        projects_view.append(merged)
    # Preserve seed order for seed projects; uploaded-only projects follow.
    seed_order = {p["name"]: i for i, p in enumerate(seed_projects)}
    projects_view.sort(key=lambda p: seed_order.get(p["name"], 999))

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "jobs": list(jobs.items()),
            "has_sample_data": SAMPLE_DATA_DIR.exists(),
            "projects": projects_view,
            "is_running": payload["is_running"],
            "active_project": payload["active_project"],
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
        "active_project": None,
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
        extraction_provider = os.getenv("QBR_EXTRACTION_PROVIDER", provider)
        synthesis_provider = os.getenv("QBR_SYNTHESIS_PROVIDER", provider)
        claude_cli_model = os.getenv("QBR_CLAUDE_CLI_MODEL", "opus")
        claude_cli_timeout_s = int(os.getenv("QBR_CLAUDE_CLI_TIMEOUT_S", "60"))
        ollama_fallback_model = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

        if "claude-cli" in (extraction_provider, synthesis_provider):
            _log_progress(
                job,
                f"Using Claude {claude_cli_model} via CLI (OAuth subscription, "
                f"timeout {claude_cli_timeout_s}s, fallback: {ollama_fallback_model})",
            )

        extraction_client, extraction_model, synthesis_client, synthesis_model = (
            create_hybrid_clients(
                extraction_provider=extraction_provider,
                synthesis_provider=synthesis_provider,
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                ollama_model=ollama_fallback_model,
                claude_cli_model=claude_cli_model,
                claude_cli_timeout_s=claude_cli_timeout_s,
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
        ACTIVE_PROJECT_MIN_HOLD_S = 1.5
        for i, thread in enumerate(threads):
            if not thread.messages:
                continue

            # Detailed per-email header
            first_msg = thread.messages[0]
            off_topic_count = sum(1 for m in thread.messages if m.is_off_topic)
            project_for_flash = thread.project or "Unknown"
            job["active_project"] = project_for_flash
            t_start = time.monotonic()
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

                # Unit 2: classify this thread's flags immediately so dashboard
                # counts rise incrementally (R6). detect_conflicts is NOT run
                # per-thread — it needs the full item list, runs at end of job.
                per_thread_flags = await asyncio.to_thread(classify_flags, items, project)
                _merge_incremental_flags(project, per_thread_flags, job_id)

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
            finally:
                # Clear in finally so CancelledError (asyncio.BaseException)
                # and other non-Exception unwinds do not leave a stale flash.
                # The min-hold sleep only runs on normal/Exception paths —
                # a cancellation skips it and proceeds straight to clear.
                try:
                    elapsed = time.monotonic() - t_start
                    if elapsed < ACTIVE_PROJECT_MIN_HOLD_S:
                        await asyncio.sleep(ACTIVE_PROJECT_MIN_HOLD_S - elapsed)
                except (asyncio.CancelledError, Exception):
                    pass
                job["active_project"] = None

        # Defensive: unconditionally clear before classification/report generation.
        job["active_project"] = None

        total_items = sum(len(v) for v in all_items.values())
        _log_progress(job, f"Extraction complete: {total_items} items total")

        # Step 3: Classify flags
        _log_progress(job, "Classifying Attention Flags...")
        flags_by_project = await asyncio.to_thread(aggregate_flags_by_project, all_items)
        total_flags = sum(len(f) for f in flags_by_project.values())
        _log_progress(job, f"{total_flags} flags triggered")

        # Populate live dashboard state so cards reflect the new run even if
        # report generation fails downstream.
        _finalize_project_state(flags_by_project, job_id)

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
        job["active_project"] = None  # clear flash on fatal error
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


@app.get("/api/projects/state")
async def projects_state():
    """Live dashboard state for client-side polling.

    Returned every 3s while is_running=true. The per-project blob excludes
    the full flag list — drill-down data lives at /projects/{name}.
    """
    return _build_projects_state_payload()


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


@app.get("/projects/{name}", response_class=HTMLResponse)
async def project_detail(request: Request, name: str):
    """Drill-down page showing all flags for a single project."""
    seed_names = {p["name"]: p for p in get_demo_projects()}
    state = project_state.get(name)
    seed = seed_names.get(name)

    # 404 when the name is neither in seed nor in state (forgiving 200
    # would let the URL namespace be infinite).
    if seed is None and state is None:
        return HTMLResponse("<h1>Project not found</h1>", status_code=404)

    # Empty state #1: seed project, never analyzed
    if state is None:
        return templates.TemplateResponse(
            request,
            "project_detail.html",
            context={
                "name": name,
                "seed": seed or {},
                "state": None,
                "empty_state": "never_analyzed",
                "report_link": None,
                "status_counts": None,
            },
        )

    # Compute status counts from stored flags
    flags = state.get("flags", [])
    status_counts = {"open": 0, "needs_review": 0, "resolved": 0}
    for f in flags:
        st = f.get("status", "open")
        if st in status_counts:
            status_counts[st] += 1

    # Report link: usable only when the originating job is still in memory
    latest_job_id = state.get("latest_job_id")
    job = jobs.get(latest_job_id) if latest_job_id else None
    if job and job.get("state") == "complete":
        report_link = {"url": f"/jobs/{latest_job_id}/report", "available": True}
    else:
        report_link = {"url": None, "available": False}

    # Empty state #2: analyzed but zero flags
    if state.get("flag_count", 0) == 0:
        return templates.TemplateResponse(
            request,
            "project_detail.html",
            context={
                "name": name,
                "seed": seed or {},
                "state": state,
                "empty_state": "all_clear",
                "report_link": report_link,
                "status_counts": status_counts,
            },
        )

    return templates.TemplateResponse(
        request,
        "project_detail.html",
        context={
            "name": name,
            "seed": seed or {},
            "state": state,
            "empty_state": None,
            "report_link": report_link,
            "status_counts": status_counts,
        },
    )


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


# --- Authentication routes ---


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str | None = None):
    if not auth_enabled():
        return RedirectResponse(url=next, status_code=303)
    if request.session.get("user"):
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        context={"next": next, "error": error},
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        return HTMLResponse(
            "<h1>Too many login attempts</h1><p>Please wait 15 minutes.</p>",
            status_code=429,
        )

    if verify_credentials(username, password):
        request.session["user"] = username
        return RedirectResponse(url=next, status_code=303)

    record_login_attempt(client_ip)
    return templates.TemplateResponse(
        request,
        "login.html",
        context={"next": next, "error": "Invalid credentials"},
        status_code=401,
    )


@app.post("/logout")
@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
