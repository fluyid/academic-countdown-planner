"""Ephemeral HTML display package (derived view only; rebuilt from Calendar + Tasks)."""

from src.display.model import MAX_MILESTONES, build_display_data, format_day_heading, format_full_date, format_short_date
from src.display.template import render_display_html
from src.display.writer import DEFAULT_OUTPUT_DIR, DisplayOutput, write_display

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DisplayOutput",
    "MAX_MILESTONES",
    "build_display_data",
    "format_day_heading",
    "format_full_date",
    "format_short_date",
    "render_display_html",
    "write_display",
]
