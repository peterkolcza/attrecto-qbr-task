"""Portfolio Health Report generator — final LLM synthesis step.

Takes prioritized flags and produces a structured Markdown + JSON report
with full source attribution.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime  # noqa: TC003 — used at runtime
from pathlib import Path
from typing import Any

from qbr.llm import SONNET_MODEL, LLMClient
from qbr.models import AttentionFlag  # noqa: TC001 — used at runtime for serialization

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _flags_to_json(flags_by_project: dict[str, list[AttentionFlag]]) -> str:
    """Serialize flags to JSON for the synthesis prompt."""
    data: dict[str, list[dict[str, Any]]] = {}
    for project, flags in flags_by_project.items():
        data[project] = []
        for f in flags:
            data[project].append(
                {
                    "flag_type": f.flag_type.value,
                    "title": f.title,
                    "severity": f.severity,
                    "age_days": f.age_days,
                    "status": f.status,
                    "evidence_summary": f.evidence_summary,
                    "sources": [
                        {
                            "person": s.person,
                            "email": s.email,
                            "role": s.role,
                            "timestamp": s.timestamp.isoformat(),
                            "source_ref": s.source_ref,
                            "quoted_text": s.quoted_text,
                        }
                        for s in f.sources
                    ],
                    "conflicts": [
                        {
                            "description": c.description,
                            "source_a": {"person": c.source_a.person, "ref": c.source_a.source_ref},
                            "source_b": {"person": c.source_b.person, "ref": c.source_b.source_ref},
                        }
                        for c in f.conflicts
                    ],
                }
            )
    return json.dumps(data, indent=2, ensure_ascii=False)


def generate_report(
    flags_by_project: dict[str, list[AttentionFlag]],
    client: LLMClient,
    model: str = SONNET_MODEL,
) -> str:
    """Generate the Portfolio Health Report using LLM synthesis.

    Returns the report as a Markdown string.
    """
    prompt_template = (PROMPTS_DIR / "synthesis.md").read_text(encoding="utf-8")
    flags_json = _flags_to_json(flags_by_project)

    prompt = prompt_template.format(flags_json=flags_json)

    report = client.complete(
        system=(
            "You are a senior engineering consultant. "
            "Generate a Portfolio Health Report in clean Markdown format. "
            "Be concise, specific, and evidence-based."
        ),
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.1,
        max_tokens=8192,
    )

    if isinstance(report, dict):
        report = json.dumps(report, indent=2, ensure_ascii=False)

    logger.info("Report generated: %d characters", len(report))
    return report


def build_report_json(
    flags_by_project: dict[str, list[AttentionFlag]],
    report_markdown: str,
) -> dict[str, Any]:
    """Build the JSON report structure for the web UI / dashboard."""
    total_flags = sum(len(flags) for flags in flags_by_project.values())
    critical_count = sum(
        1 for flags in flags_by_project.values() for f in flags if f.severity == "critical"
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "projects_analyzed": len(flags_by_project),
        "total_flags": total_flags,
        "critical_flags": critical_count,
        "flags_by_project": {
            project: [f.model_dump(mode="json") for f in flags]
            for project, flags in flags_by_project.items()
        },
        "report_markdown": report_markdown,
    }


def save_report(
    report_markdown: str,
    report_json: dict[str, Any],
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """Save report to both Markdown and JSON files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    md_path = output_dir / f"portfolio_{timestamp}.md"
    md_path.write_text(report_markdown, encoding="utf-8")

    json_path = output_dir / f"portfolio_{timestamp}.json"
    json_path.write_text(json.dumps(report_json, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Report saved: %s, %s", md_path, json_path)
    return md_path, json_path
