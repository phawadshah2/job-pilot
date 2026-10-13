"""AI assessment, approval gate and apply outcomes (fictional example user)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import use_example_home

HOME = use_example_home()


def setUpModule():
    # other test modules may have pointed JOBPILOT_HOME elsewhere since import
    import os
    os.environ["JOBPILOT_HOME"] = str(HOME)

import jobpilot.__main__ as cli  # noqa: E402
from jobpilot.assess import extract_salary, link_status, load_assessments  # noqa: E402
from jobpilot.models import Job  # noqa: E402
from jobpilot.status import can_move, normalize  # noqa: E402

JD = "Senior Flutter Developer. Flutter, Dart, BLoC, Clean Architecture, REST APIs, Firebase. Dubai office."


def stored(store, company, score=80, **kw):
    j = Job(source="t", title="Senior Flutter Developer", company=company, url=f"https://example.com/{company}",
            location="Dubai, UAE", description=JD, work_mode="onsite-uae", region="uae", seniority="senior")
    store.add(j)
    store.update(j.key, score=score, **kw)
    store.commit()
    return j.key


class StatusTest(unittest.TestCase):
    def test_forward_only_with_side_moves(self):
        self.assertTrue(can_move("Ready", "Approved"))
        self.assertTrue(can_move("Failed", "Pending"))          # same rank: allowed
        self.assertTrue(can_move("Manual action required", "Submitted"))
        self.assertFalse(can_move("Submitted", "Pending"))
        self.assertEqual(normalize("Applied"), "Submitted")


class AssessHelpersTest(unittest.TestCase):
    def test_salary_extraction(self):
        self.assertEqual(extract_salary("Pay: AED 12,000 - 15,000 per month. Visa provided."), "AED 12,000 - 15,000 per month")
        self.assertEqual(extract_salary("Salary: $120k - $150k a year"), "$120k - $150k a year")
        self.assertEqual(extract_salary("Competitive package"), "")

    def test_link_status_never_fetches_restricted_sites_and_trusts_ats_feeds(self):
        with mock.patch("urllib.request.urlopen") as op:
            self.assertIsNone(link_status("email:linkedin", "https://www.linkedin.com/jobs/view/1/"))
            self.assertTrue(link_status("greenhouse:acme", "https://boards.greenhouse.io/acme/jobs/1"))
            op.assert_not_called()

    def test_link_status_detects_expired_page(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b"<h1>This job is no longer accepting applications</h1>"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertFalse(link_status("jsearch:x", "https://jobs.example.com/1"))

    def test_assessment_file_validation(self):
        p = Path(tempfile.mkdtemp()) / "a.json"
        p.write_text(json.dumps([{"key": "abcd1234", "score": 82, "why": "w", "missing": "m"}]))
        self.assertEqual(load_assessments(p)[0]["score"], 82)
        p.write_text(json.dumps([{"key": "abcd1234", "score": 140}]))
        with self.assertRaises(ValueError):
            load_assessments(p)


class AssessCommandTest(unittest.TestCase):
    def test_strong_prepared_middle_kept_weak_skipped(self):
        store = cli.open_store()
        strong, mid, weak = (stored(store, c) for c in ("StrongCo", "MidCo", "WeakCo"))
        f = Path(tempfile.mkdtemp()) / "a.json"
        f.write_text(json.dumps([
            {"key": strong, "score": 88, "why": "Exact Flutter + BLoC match in Dubai.", "missing": "GraphQL"},
            {"key": mid, "score": 68, "why": "Good stack, but lead role.", "missing": "8+ yrs"},
            {"key": weak, "score": 40, "why": "Mostly native iOS.", "missing": "Swift"}]))
        with mock.patch.object(cli, "sync", return_value=0):
            cli.cmd_assess(mock.Mock(file=str(f)))
        s = cli.open_store()
        self.assertEqual(s.get(strong)["status"], "Tailoring")
        self.assertTrue((Path(s.get(strong)["cv_dir"]) / "jd.txt").exists())
        self.assertEqual((s.get(strong)["ai_score"], s.get(strong)["missing"]), (88, "GraphQL"))
        self.assertEqual(s.get(mid)["status"], "New")
        self.assertEqual(s.get(weak)["status"], "Skipped")


class ApprovalGateTest(unittest.TestCase):
    def test_only_ready_can_be_approved_and_apply_needs_approval(self):
        store = cli.open_store()
        ready, new = stored(store, "ReadyCo", status="Ready"), stored(store, "NewCo")
        with mock.patch.object(cli, "sync", return_value=0):
            cli.cmd_approve(mock.Mock(keys=[ready, new]))
        s = cli.open_store()
        self.assertEqual(s.get(ready)["status"], "Approved")
        self.assertTrue(s.get(ready)["approved_at"])
        self.assertEqual(s.get(new)["status"], "New")


class OutcomeTest(unittest.TestCase):
    def outcome(self, choice, typed="", manual=False):
        store = cli.open_store()
        key = stored(store, f"Co{choice}{typed}{manual}", status="Approved")
        with mock.patch.object(cli, "sync", return_value=0), mock.patch("builtins.input", return_value=typed):
            cli._record(store, store.get(key), choice, manual=manual, why="LinkedIn")
        return cli.open_store().get(key)

    def test_submitted_only_with_confirmation(self):
        r = self.outcome("confirmed")
        self.assertEqual(r["status"], "Submitted")
        self.assertIn("confirmation page seen", r["notes"])
        self.assertTrue(r["applied_at"] and r["follow_up_at"])
        self.assertEqual(self.outcome("a", "y")["status"], "Submitted")
        self.assertEqual(self.outcome("a", "n")["status"], "Pending")      # not confirmed -> never Submitted

    def test_failed_pending_manual(self):
        self.assertEqual(self.outcome("f", "500 error")["status"], "Failed")
        self.assertEqual(self.outcome("l")["status"], "Pending")
        self.assertEqual(self.outcome("q", manual=True)["status"], "Manual action required")


if __name__ == "__main__":
    unittest.main()


class SheetUrlTest(unittest.TestCase):
    def test_bad_webhook_url_says_so(self):
        from jobpilot import sheet
        with self.assertRaisesRegex(RuntimeError, "isn't a URL"):
            sheet.reset("y", "token")

    def test_setup_rejects_non_exec_url(self):
        from unittest import mock
        from jobpilot import setup
        good = "https://script.google.com/macros/s/abc/exec"
        with mock.patch("builtins.input", side_effect=["y", good]), mock.patch("builtins.print"):
            self.assertEqual(setup.ask_webhook_url(""), good)
