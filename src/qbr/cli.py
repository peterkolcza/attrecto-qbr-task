"""CLI entry point — wires the full QBR pipeline with rich verbose/debug output."""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from qbr import __version__
from qbr.flags import aggregate_flags_by_project
from qbr.llm import UsageTracker, create_hybrid_clients
from qbr.models import ExtractedItem  # noqa: TC001 — used as type annotation at runtime
from qbr.parser import parse_all_emails, parse_colleagues
from qbr.pipeline import run_pipeline_for_thread
from qbr.report import build_report_json, generate_report, save_report

load_dotenv()
console = Console()

app = typer.Typer(
    name="qbr",
    help="QBR Portfolio Health Report — AI-driven email analysis",
)


def _print_banner(provider: str, debug: bool) -> None:
    """Print the startup technology summary."""
    console.print(
        Panel.fit(
            f"[bold]QBR Portfolio Health Analyzer v{__version__}[/bold]\n"
            f"{'─' * 40}\n"
            f"Provider:    {provider.capitalize()} "
            f"({'Haiku 4.5 → Sonnet 4.6' if provider == 'anthropic' else 'Local model'})\n"
            f"Pipeline:    3-stage extraction + 2 Attention Flags\n"
            f"Security:    Spotlighting + dual-LLM quarantine\n"
            f"Caching:     {'Prompt caching enabled' if provider == 'anthropic' else 'N/A'}\n"
            f"Debug:       {'ON — full prompt/response traces' if debug else 'OFF'}",
            title="[cyan]QBR[/cyan]",
            border_style="cyan",
        )
    )


def _print_usage_summary(tracker: UsageTracker) -> None:
    """Print token usage and cost summary."""
    summary = tracker.summary()
    table = Table(title="Token Usage Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Total LLM calls", str(summary["total_calls"]))
    table.add_row("Input tokens", f"{summary['total_input_tokens']:,}")
    table.add_row("Output tokens", f"{summary['total_output_tokens']:,}")
    table.add_row("Estimated cost", f"${summary['total_cost_usd']:.4f}")
    console.print(table)


@app.command()
def run(
    input: str = typer.Option("task/sample_data", help="Path to email directory"),
    output: str = typer.Option("reports/", help="Output directory for reports"),
    provider: str = typer.Option(
        None, help="LLM provider: anthropic or ollama (default: from QBR_LLM_PROVIDER env)"
    ),
    debug: bool = typer.Option(False, help="Enable debug mode with full prompt/response traces"),
) -> None:
    """Analyze project emails and generate a Portfolio Health Report."""
    # Resolve provider from env if not specified
    if provider is None:
        provider = os.getenv("QBR_LLM_PROVIDER", "anthropic")

    # Set up logging
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(name)s | %(message)s" if debug else "%(message)s",
        stream=sys.stderr,
    )
    if not debug:
        logging.getLogger("qbr").setLevel(logging.WARNING)

    _print_banner(provider, debug)

    # Create hybrid LLM clients (extraction + synthesis)
    tracker = UsageTracker()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    extraction_provider = os.getenv("QBR_EXTRACTION_PROVIDER", provider)
    synthesis_provider = os.getenv("QBR_SYNTHESIS_PROVIDER", provider)
    ollama_model = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

    extraction_client, extraction_model, synthesis_client, synthesis_model = create_hybrid_clients(
        extraction_provider=extraction_provider,
        synthesis_provider=synthesis_provider,
        api_key=api_key,
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=ollama_model,
        tracker=tracker,
    )

    if extraction_provider != synthesis_provider:
        console.print(
            f"[cyan]Hybrid mode:[/cyan] extraction={extraction_provider} ({extraction_model}), "
            f"synthesis={synthesis_provider} ({synthesis_model})"
        )

    input_path = Path(input)
    if not input_path.exists():
        console.print(f"[red]Error: input directory not found: {input_path}[/red]")
        raise typer.Exit(code=1)

    # Load colleagues roster
    colleagues_path = input_path / "Colleagues.txt"
    colleagues = parse_colleagues(colleagues_path) if colleagues_path.exists() else []

    # Step 1: Parse emails
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[1/4] Parsing emails...", total=None)
        threads = parse_all_emails(input_path)
        progress.update(task, completed=True)

    console.print(
        f"[green]✓[/green] Parsed {len(threads)} threads across "
        f"{len({t.project for t in threads})} projects"
    )

    # Step 2: Extract items per thread
    all_items: dict[str, list[ExtractedItem]] = defaultdict(list)
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task(
            f"[2/4] Extracting items ({extraction_model})...", total=len(threads)
        )
        for thread in threads:
            if not thread.messages:
                progress.advance(task)
                continue
            try:
                items, _metrics = run_pipeline_for_thread(
                    thread,
                    extraction_client,
                    colleagues=colleagues,
                    extraction_model=extraction_model,
                )
                project = thread.project or "Unknown"
                all_items[project].extend(items)
            except Exception as e:
                console.print(f"[yellow]⚠ Error processing {thread.source_file}: {e}[/yellow]")
                if debug:
                    console.print_exception()
            progress.advance(task)

    total_items = sum(len(items) for items in all_items.values())
    open_items = sum(
        1 for items in all_items.values() for i in items if i.status.value != "resolved"
    )
    console.print(f"[green]✓[/green] Extracted {total_items} items, {open_items} open")

    for project, items in sorted(all_items.items()):
        project_open = sum(1 for i in items if i.status.value != "resolved")
        console.print(f"      → {project}: {len(items)} items ({project_open} open)")

    # Step 3: Classify flags
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[3/4] Classifying Attention Flags...", total=None)
        flags_by_project = aggregate_flags_by_project(all_items)
        progress.update(task, completed=True)

    total_flags = sum(len(f) for f in flags_by_project.values())
    flag1_count = sum(
        1
        for flags in flags_by_project.values()
        for f in flags
        if f.flag_type.value == "unresolved_action"
    )
    flag2_count = sum(
        1
        for flags in flags_by_project.values()
        for f in flags
        if f.flag_type.value == "risk_blocker"
    )
    console.print(f"[green]✓[/green] {total_flags} flags triggered")
    console.print(f"      → Flag 1 (Unresolved Actions): {flag1_count} items")
    console.print(f"      → Flag 2 (Risks/Blockers): {flag2_count} items")

    # Step 4: Generate report
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task(f"[4/4] Generating report ({synthesis_model})...", total=None)
        report_md = generate_report(flags_by_project, synthesis_client, model=synthesis_model)
        report_json = build_report_json(flags_by_project, report_md)
        md_path, json_path = save_report(report_md, report_json, output)
        progress.update(task, completed=True)

    console.print("[green]✓[/green] Report saved:")
    console.print(f"      → Markdown: {md_path}")
    console.print(f"      → JSON: {json_path}")

    # Usage summary
    console.print()
    _print_usage_summary(tracker)

    # Debug: dump full report to stdout
    if debug:
        console.print()
        console.print(Panel(report_md, title="Generated Report", border_style="green"))


@app.command(name="smoke-test")
def smoke_test(
    provider: str = typer.Option(None, help="LLM provider to test"),
) -> None:
    """Run a quick smoke test against the configured LLM provider."""
    if provider is None:
        provider = os.getenv("QBR_LLM_PROVIDER", "anthropic")

    console.print(f"[cyan]Smoke test for provider: {provider}[/cyan]")

    try:
        from qbr.llm import create_client

        tracker = UsageTracker()
        client = create_client(
            provider=provider,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:e2b"),
            tracker=tracker,
        )

        result = client.complete(
            system="You are a test assistant.",
            messages=[{"role": "user", "content": "Reply with exactly: QBR_SMOKE_TEST_OK"}],
            max_tokens=50,
        )

        if "QBR_SMOKE_TEST_OK" in str(result):
            console.print("[green]✓ Smoke test passed[/green]")
            console.print(f"  Response: {result}")
        else:
            console.print(f"[yellow]⚠ Unexpected response: {result}[/yellow]")

        _print_usage_summary(tracker)

    except Exception as e:
        console.print(f"[red]✗ Smoke test failed: {e}[/red]")
        raise typer.Exit(code=1) from e


@app.command(name="hash-password")
def hash_password(
    password: str = typer.Argument(..., help="Plaintext password to hash"),
) -> None:
    """Generate a bcrypt hash for the QBR_AUTH_PASSWORD_HASH env var."""
    from qbr_web.auth import hash_password as _hash

    result = _hash(password)
    console.print(f"[cyan]Bcrypt hash:[/cyan]\n{result}\n")
    console.print("Add this to your .env file:")
    console.print(f'[green]QBR_AUTH_PASSWORD_HASH="{result}"[/green]')


@app.command(name="seed-demo")
def seed_demo() -> None:
    """Show demo project data that will be pre-loaded in the dashboard."""
    from qbr.seed import get_demo_projects

    projects = get_demo_projects()
    console.print(f"[cyan]Demo data: {len(projects)} projects[/cyan]\n")
    for proj in projects:
        table = Table(title=proj["name"], show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("PM", proj["pm"])
        table.add_row("Team size", str(proj["team_size"]))
        table.add_row("QBR date", proj["qbr_date"])
        table.add_row("Q3 focus", proj["q3_focus"])
        table.add_row("Known risks", proj["known_risks"])
        table.add_row("Email threads", str(proj["email_threads"]))
        console.print(table)
        console.print()


if __name__ == "__main__":
    app()
