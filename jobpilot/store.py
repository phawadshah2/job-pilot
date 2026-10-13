"""SQLite memory of every job ever seen, so each job is shown exactly once
and the verbatim JD is kept for step 2 (scoring + CV tailoring)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key          TEXT PRIMARY KEY,
    first_seen   TEXT NOT NULL,
    source       TEXT, title TEXT, company TEXT, url TEXT, apply_url TEXT,
    location     TEXT, work_mode TEXT, seniority TEXT,
    posted_at    TEXT, employment_type TEXT, salary TEXT,
    description  TEXT, notes TEXT,
    status       TEXT NOT NULL DEFAULT 'New',
    synced       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_usage (
    api   TEXT NOT NULL,
    day   TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (api, day)
);
"""

# Columns added after step 1; applied to existing DBs on open.
MIGRATIONS = {
    "score": "INTEGER",
    "score_breakdown": "TEXT",
    "matched": "TEXT",
    "gaps": "TEXT",
    "cv_dir": "TEXT",
    "applied_at": "TEXT",
    "follow_up_at": "TEXT",
    "in_sheet": "INTEGER NOT NULL DEFAULT 0",   # has this row ever reached the sheet?
    "region": "TEXT",
    "ai_score": "INTEGER",          # Claude's assessment (0-100); used instead of the rule score once set
    "match_explanation": "TEXT",    # why it fits, in a sentence or two
    "missing": "TEXT",              # requirements the candidate doesn't meet
    "cv_pdf": "TEXT",
    "cover_pdf": "TEXT",
    "approved_at": "TEXT",
}


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for name, typ in MIGRATIONS.items():
            if name not in cols:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {typ}")

    def is_known(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM jobs WHERE key=?", (key,)).fetchone() is not None

    def add(self, job: Job) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO jobs (key, first_seen, source, title, company, url, apply_url,
               location, work_mode, seniority, posted_at, employment_type, salary, description, notes, region)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.key, datetime.now(timezone.utc).isoformat(timespec="seconds"), job.source, job.title,
             job.company, job.url, job.apply_url, job.location, job.work_mode, job.seniority,
             job.posted_at.isoformat() if job.posted_at else None, job.employment_type, job.salary,
             job.description, "; ".join(job.notes), job.region or None),
        )

    def get(self, key: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()

    def query(self, where: str = "1=1", params: tuple = (), order: str = "first_seen") -> list[sqlite3.Row]:
        return self.db.execute(f"SELECT * FROM jobs WHERE {where} ORDER BY {order}", params).fetchall()

    def update(self, key: str, **fields) -> None:
        """Any change re-queues the row for the sheet (synced=0 -> upsert)."""
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {sets}, synced=0 WHERE key=?", (*fields.values(), key))

    def unsynced(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM jobs WHERE synced=0 ORDER BY first_seen").fetchall()

    def mark_synced(self, keys: list[str], pushed: bool = True) -> None:
        self.db.executemany(f"UPDATE jobs SET synced=1{', in_sheet=1' if pushed else ''} WHERE key=?", [(k,) for k in keys])

    # --- API quota tracking (free tiers are per calendar month) ---
    def record(self, api: str, n: int = 1) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        self.db.execute("INSERT INTO api_usage (api, day, calls) VALUES (?,?,?) "
                        "ON CONFLICT(api, day) DO UPDATE SET calls = calls + excluded.calls", (api, day, n))
        self.db.commit()

    def month_count(self, api: str) -> int:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        r = self.db.execute("SELECT COALESCE(SUM(calls),0) FROM api_usage WHERE api=? AND day LIKE ?", (api, month + "%")).fetchone()
        return int(r[0])

    def commit(self) -> None:
        self.db.commit()
