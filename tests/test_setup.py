import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jobpilot.setup import bullet_ids, build_skills, load_catalog, write_env
from tests.helpers import REPO


class BuildersTest(unittest.TestCase):
    def test_build_skills_uses_catalog_and_custom(self):
        s = build_skills(["flutter", "Our In-house SDK"], ["react native"], ["swift"], load_catalog())
        self.assertIn("Flutter", s["have"])
        self.assertEqual(s["have"]["Our In-house SDK"], ["our in-house sdk"])
        self.assertIn("react native", s["forbidden_in_cv"]["patterns"])
        self.assertIn("Swift", s["unverified"])

    def test_bullet_ids_readable_and_unique(self):
        ids = bullet_ids(["Built the app.", "Built the API.", "Led a team of 3."])
        self.assertEqual(list(ids), ["built", "built2", "led"])

    def test_write_env_merges_and_keeps_secrets(self):
        p = Path(tempfile.mkdtemp()) / ".env"
        p.write_text("A=1\n# comment\nB=keep\n")
        write_env({"A": "2", "B": "", "C": "3"}, p)
        self.assertEqual(p.read_text(), "A=2\n# comment\nB=keep\nC=3\n")
        self.assertEqual(oct(p.stat().st_mode)[-3:], "600")


class ScriptedSetupTest(unittest.TestCase):
    """A brand-new user answering every question in the terminal."""
    def test_full_setup_from_stdin(self):
        home = Path(tempfile.mkdtemp()) / "jp"
        answers = "\n".join([
            # about
            "Sam Rivera", "sam@example.com", "+44 7000 000000", "London", "UK", "", "", "",
            # role
            "Senior Backend Engineer", "backend engineer", "backend, golang", "platform engineer", "", "", "senior", "2018-06",
            # where
            "uk, remote, eu", "gb", "emea", "english, spanish", "y", "GBP", "6000", "n", "n",
            # skills
            "Go, PostgreSQL, Docker, AWS", "Java", "Kubernetes",
            # cv
            "SAM RIVERA", "", "Backend engineer with 8 years building APIs.",
            "Languages: Go, SQL", "",
            "y", "Senior Backend Engineer", "PayCo", "Built payment APIs handling 1M requests/day.", "", "London", "2021 – Present",
            "n", "n", "y", "BSc Computer Science", "2014 – 2017", "UCL, London", "n",
            # answers
            "3 months", "", "", "British", "Citizen", "No", "LinkedIn",
            # connect: JSearch key comes via getpass (no tty -> reads stdin), sheet? no, gmail? no
            "", "n", "n",
        ]) + "\n"
        env = dict(os.environ, JOBPILOT_HOME=str(home))
        p = subprocess.run([sys.executable, "-m", "jobpilot", "setup"], input=answers, text=True,
                           capture_output=True, cwd=REPO, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout[-800:] + p.stderr[-800:])
        user = json.loads((home / "user.json").read_text())
        self.assertEqual(user["role"]["keywords"], ["backend", "golang"])
        self.assertEqual(user["search"]["regions"], ["uk", "remote", "eu"])
        self.assertEqual(user["search"]["salary_floor"], {"currency": "GBP", "per_month": 6000})
        cv = json.loads((home / "cv.json").read_text())
        self.assertEqual(cv["experience"][0]["company"], "PayCo")
        self.assertEqual(list(cv["experience"][0]["bullets"]), ["built"])
        self.assertIn("Kubernetes", json.loads((home / "skills.json").read_text())["unverified"])
        self.assertEqual(json.loads((home / "applicant.json").read_text())["nationality"], "British")
        self.assertTrue((home / "facts.md").exists())
        self.assertEqual(oct(home.stat().st_mode)[-3:], "700")


if __name__ == "__main__":
    unittest.main()
