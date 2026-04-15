"""CLI entry point — placeholder for issue #8."""

import typer

app = typer.Typer(
    name="qbr",
    help="QBR Portfolio Health Report — AI-driven email analysis",
)


@app.command()
def run(
    input: str = typer.Option("task/sample_data", help="Path to email directory"),
    output: str = typer.Option("reports/", help="Output directory for reports"),
    provider: str = typer.Option("anthropic", help="LLM provider: anthropic or ollama"),
    debug: bool = typer.Option(False, help="Enable debug mode with full prompt/response traces"),
) -> None:
    """Analyze project emails and generate a Portfolio Health Report."""
    typer.echo(f"QBR v0.1.0 — input={input}, provider={provider}")
    typer.echo("Pipeline not yet implemented. See issues #2–#7.")


@app.command(name="smoke-test")
def smoke_test(
    provider: str = typer.Option("anthropic", help="LLM provider to test"),
) -> None:
    """Run a quick smoke test against the configured LLM provider."""
    typer.echo(f"Smoke test for provider={provider} — not yet implemented (issue #3).")


@app.command(name="seed-demo")
def seed_demo() -> None:
    """Seed demo data for the web dashboard."""
    typer.echo("Demo seeding not yet implemented (issue #14).")


if __name__ == "__main__":
    app()
