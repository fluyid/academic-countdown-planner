"""Thin Google Tasks v1 API client using the shared out-of-repo OAuth token."""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build

from src.google_auth import get_credentials


def get_tasks_service(service: Any = None) -> Any:
    return service or build("tasks", "v1", credentials=get_credentials())


def get_or_create_tasklist(service: Any, title: str) -> str:
    wanted = title.strip().lower()
    page_token: str | None = None
    while True:
        resp = service.tasklists().list(maxResults=100, pageToken=page_token).execute()
        for item in resp.get("items", []):
            if (item.get("title") or "").strip().lower() == wanted:
                return item["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    created = service.tasklists().insert(body={"title": title.strip()}).execute()
    return created["id"]


def list_tasks(service: Any, tasklist_id: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        # showHidden=True is required by Tasks API v1 to return items completed in the Google Tasks UI.
        resp = service.tasks().list(
            tasklist=tasklist_id,
            showCompleted=True,
            showHidden=True,
            maxResults=100,
            pageToken=page_token,
        ).execute()
        tasks.extend(t for t in resp.get("items", []) if not t.get("deleted"))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return tasks


def create_task(service: Any, tasklist_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return service.tasks().insert(tasklist=tasklist_id, body=body).execute()


def update_task(service: Any, tasklist_id: str, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return service.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()


def complete_task(service: Any, tasklist_id: str, task_id: str) -> dict[str, Any]:
    return update_task(service, tasklist_id, task_id, {"status": "completed"})


def delete_task(service: Any, tasklist_id: str, task_id: str) -> None:
    service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
