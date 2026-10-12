"""Pushes rows to a Google Sheet through a tiny Apps Script web app
(apps_script/Code.gs). Free, no Google Cloud project or service account."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from .http import post_json


def _post(webhook_url: str, body: dict):
    """Apps Script answers with an HTML error page (e.g. 'Script function not found: doPost')
    when the deployment has no code or an old version. Turn that into a readable error."""
    try:
        return post_json(webhook_url, body)
    except ValueError as e:  # not JSON
        raise RuntimeError(
            "the web app answered with a Google error page instead of JSON. Usually the deployed "
            "version doesn't contain Code.gs: paste apps_script/Code.gs, save, then Deploy > Manage "
            "deployments > pencil > New version > Deploy (the URL stays the same)."
        ) from e

COLUMNS = [
    "Date found", "Company", "Role", "Location", "Work mode", "Seniority", "Source",
    "URL", "Posted", "Match score", "Gaps", "CV path", "Status", "Applied date",
    "Follow-up date", "Notes", "Key",
]


def to_row(r: sqlite3.Row) -> list[str]:
    found = datetime.fromisoformat(r["first_seen"]).strftime("%Y-%m-%d")
    posted = (r["posted_at"] or "")[:10]
    return [
        found, r["company"], r["title"], r["location"], r["work_mode"], r["seniority"], r["source"],
        r["url"], posted, "" if r["score"] is None else r["score"], r["gaps"] or "", r["cv_dir"] or "",
        r["status"], r["applied_at"] or "", r["follow_up_at"] or "", r["notes"] or "", r["key"],
    ]


def push(webhook_url: str, token: str, rows: list[sqlite3.Row]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    resp = _post(webhook_url, {
        "token": token, "columns": COLUMNS, "rows": [to_row(r) for r in rows],
        # Existing rows: only these cells are refreshed; everything you typed stays.
        "update_columns": ["Match score", "Gaps", "CV path", "Status", "Applied date", "Follow-up date"],
    })
    if not resp or not resp.get("ok"):
        raise RuntimeError(f"Sheet rejected the push: {resp}")
    return int(resp.get("added", 0)), int(resp.get("updated", 0))


def pull(webhook_url: str, token: str) -> list[dict]:
    resp = _post(webhook_url, {"token": token, "action": "pull"})
    if not resp or not resp.get("ok"):
        raise RuntimeError(f"Sheet rejected the pull: {resp}")
    return resp.get("rows", [])


def reset(webhook_url: str, token: str) -> None:
    resp = _post(webhook_url, {"token": token, "action": "reset", "columns": COLUMNS})
    if not resp or not resp.get("ok"):
        raise RuntimeError(f"Sheet rejected the reset: {resp}")
