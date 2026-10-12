"""Tests run against the fictional example user in examples/alex, never a real profile."""
import json
import os
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "alex"


def use_example_home() -> Path:
    """Copy examples/alex to a temp JOBPILOT_HOME and point the code at it."""
    home = Path(tempfile.mkdtemp(prefix="jobpilot-test-"))
    for f in EXAMPLE.iterdir():
        if f.is_file() and not f.name.startswith("."):
            shutil.copy(f, home / f.name)
    os.environ["JOBPILOT_HOME"] = str(home)
    return home


def example_cfg() -> dict:
    from jobpilot import config
    defaults = json.loads((REPO / "config.json").read_text())
    user = json.loads((EXAMPLE / "user.json").read_text())
    return config.build(defaults, user, {})


def example_skills() -> dict:
    skills = json.loads((EXAMPLE / "skills.json").read_text())
    skills.setdefault("career_start", json.loads((EXAMPLE / "user.json").read_text())["role"]["career_start"])
    return skills
