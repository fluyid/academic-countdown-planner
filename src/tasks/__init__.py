"""Google Tasks sync package (truth of progress)."""

from src.tasks.client import (
    complete_task,
    create_task,
    delete_task,
    get_or_create_tasklist,
    get_tasks_service,
    list_tasks,
    update_task,
)
from src.tasks.codec import META_PREFIX, build_task_body, parse_task_meta, read_completed_progress, task_matches_body
from src.tasks.sync import SyncResult, format_sync_summary, sync_plan_to_tasks

__all__ = [
    "META_PREFIX",
    "SyncResult",
    "build_task_body",
    "complete_task",
    "create_task",
    "delete_task",
    "format_sync_summary",
    "get_or_create_tasklist",
    "get_tasks_service",
    "list_tasks",
    "parse_task_meta",
    "read_completed_progress",
    "sync_plan_to_tasks",
    "task_matches_body",
    "update_task",
]
