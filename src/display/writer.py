"""Ephemeral HTML + JSON display writer (writes into gitignored output/)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.display.model import MAX_MILESTONES, build_display_data
from src.display.template import render_display_html
from src.planner.models import StudyPlan

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


@dataclass
class DisplayOutput:
    html_path: Path
    data_path: Path
    data: dict[str, Any]


def write_display(
    plan: StudyPlan,
    config: dict[str, Any],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    today: date | None = None,
    completed_units: dict[str, float] | None = None,
    completed_days: set[date] | None = None,
    background: str | Path | None = None,
    max_milestones: int = MAX_MILESTONES,
) -> DisplayOutput:
    data = build_display_data(
        plan,
        config,
        today=today,
        completed_units=completed_units,
        completed_days=completed_days,
        background=background,
        max_milestones=max_milestones,
    )
    html_doc = render_display_html(data)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "index.html"
    data_path = output_dir / "plan.json"
    html_path.write_text(html_doc, encoding="utf-8")
    data_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return DisplayOutput(html_path=html_path, data_path=data_path, data=data)
