"""Where each user's own data lives: outside the repo, so it can never be committed.

Default ~/.job-pilot (override with JOBPILOT_HOME). Everything personal is here:
answers from `jobpilot setup`, secrets, the CV content, job folders and the database.
The repo itself only holds code, generic defaults (config.json) and a fictional example
user (examples/).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def home() -> Path:
    return Path(os.environ.get("JOBPILOT_HOME", "~/.job-pilot")).expanduser()


def env_file() -> Path:        # secrets: API keys, sheet URL/token, Gmail app password
    return home() / ".env"


def user_file() -> Path:       # who you are + what/where you're searching
    return home() / "user.json"


def skills_file() -> Path:     # skills you have / don't have (scoring + CV guard)
    return home() / "skills.json"


def applicant_file() -> Path:  # answers for application forms
    return home() / "applicant.json"


def cv_file() -> Path:         # CV content (experience, projects, education...)
    return home() / "cv.json"


def facts_file() -> Path:      # readable summary of the above, for Claude when tailoring
    return home() / "facts.md"


def jobs_dir() -> Path:        # one folder per prepared job (jd.txt, cv.html, PDFs...)
    return home() / "jobs"


def data_dir() -> Path:        # jobs.db, digests, browser profile, logs
    return home() / "data"
