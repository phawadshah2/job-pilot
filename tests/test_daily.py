import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import json

import jobpilot.__main__ as cli
from jobpilot.models import Job
from jobpilot.store import Store
from tests.helpers import use_example_home

HOME = use_example_home()

CFG = {"env": {"SHEET_WEBHOOK_URL": "https://script.google.com/macros/s/x/exec", "SHEET_TOKEN": "t"},
       "follow_up_days": 7}


def store_with(*jobs):
    s = Store(Path(tempfile.mkdtemp()) / "t.db")
    for j in jobs:
        s.add(j)
    s.commit()
    return s


def job(company="Acme", title="Senior Flutter Developer"):
    return Job(source="t", title=title, company=company, url="https://x", work_mode="onsite-gulf", seniority="senior")


class PullTest(unittest.TestCase):
    def test_sheet_status_moves_forward_only(self):
        a, b = job("Acme"), job("Beta")
        s = store_with(a, b)
        s.update(b.key, status="Interview")
        s.commit()
        rows = [{"Key": a.key, "Status": "Applied", "Applied date": "", "Follow-up date": ""},
                {"Key": b.key, "Status": "Applied", "Applied date": "", "Follow-up date": ""}]   # backwards for b
        with mock.patch.object(cli.sheet, "pull", return_value=rows), mock.patch.object(cli, "load_cfg", return_value=CFG):
            self.assertEqual(cli.pull(CFG, s), 1)
        ra, rb = s.get(a.key), s.get(b.key)
        self.assertEqual(ra["status"], "Applied")
        self.assertEqual(ra["applied_at"], date.today().isoformat())     # stamped when the sheet had none
        self.assertEqual(ra["follow_up_at"], (date.today() + timedelta(days=7)).isoformat())
        self.assertEqual(rb["status"], "Interview")

    def test_sheet_dates_kept(self):
        a = job()
        s = store_with(a)
        rows = [{"Key": a.key, "Status": "Applied", "Applied date": "2026-10-01", "Follow-up date": "2026-10-08"}]
        with mock.patch.object(cli.sheet, "pull", return_value=rows):
            cli.pull(CFG, s)
        self.assertEqual((s.get(a.key)["applied_at"], s.get(a.key)["follow_up_at"]), ("2026-10-01", "2026-10-08"))

    def test_unconfigured_sheet_is_a_noop(self):
        s = store_with(job())
        self.assertEqual(cli.pull({"env": {"SHEET_WEBHOOK_URL": "https://docs.google.com/x"}}, s), 0)


class SyncTest(unittest.TestCase):
    def test_skipped_before_reaching_sheet_stays_local(self):
        spam, real, was_pushed = job("vmysmartpros"), job("Careem"), job("Tabby")
        s = store_with(spam, real, was_pushed)
        s.mark_synced([was_pushed.key])                    # already in the sheet
        s.update(spam.key, status="Skipped")
        s.update(was_pushed.key, status="Skipped")         # you skipped it later: sheet must hear about it
        s.commit()
        with mock.patch.object(cli.sheet, "push", return_value=(1, 1)) as push:
            cli.sync(CFG, s)
        sent = {r["key"] for r in push.call_args[0][2]}
        self.assertEqual(sent, {real.key, was_pushed.key})
        self.assertEqual(s.get(spam.key)["synced"], 1)
        self.assertEqual(s.get(spam.key)["in_sheet"], 0)
        self.assertEqual(s.get(real.key)["in_sheet"], 1)


class SheetErrorTest(unittest.TestCase):
    def test_google_html_error_page_becomes_clear_message_and_rows_stay_queued(self):
        s = store_with(job("Careem"))
        with mock.patch("jobpilot.sheet.post_json", side_effect=ValueError("Expecting value")):
            self.assertEqual(cli.sync(CFG, s), 1)
            self.assertEqual(cli.pull(CFG, s), 0)
        self.assertEqual(len(s.unsynced()), 1)


class CompactSummaryTest(unittest.TestCase):
    def test_uae_first_then_score(self):
        a, b, c = job("UK Co"), job("Dubai Co"), job("Riyadh Co")
        s = store_with(a, b, c)
        for j, region, score in ((a, "uk", 95), (b, "uae", 80), (c, "gcc", 90)):
            s.update(j.key, status="Tailoring", region=region, score=score, description="x" * 700)
        s.commit()
        lines = [l for l in cli.compact_summary(s).splitlines() if l[:8] in (a.key[:8], b.key[:8], c.key[:8])]
        self.assertEqual([l.split()[3] for l in lines], ["Dubai", "Riyadh", "UK"])


class PrepareOrderTest(unittest.TestCase):
    def test_uae_jobs_prepared_before_higher_scoring_us_jobs(self):
        rows = [{"region": "us", "score": 95}, {"region": "uae", "score": 72}, {"region": "gcc", "score": 90},
                {"region": "uae", "score": 88}, {"region": None, "score": 99}]
        self.assertEqual([(r["region"], r["score"]) for r in cli.by_priority(rows)],
                         [("uae", 88), ("uae", 72), ("gcc", 90), ("us", 95), (None, 99)])


class FollowupTest(unittest.TestCase):
    def test_due_only_when_applied_and_past_date(self):
        a, b, c = job("A"), job("B"), job("C")
        s = store_with(a, b, c)
        s.update(a.key, status="Applied", follow_up_at=(date.today() - timedelta(days=1)).isoformat())
        s.update(b.key, status="Applied", follow_up_at=(date.today() + timedelta(days=3)).isoformat())
        s.update(c.key, status="Interview", follow_up_at=(date.today() - timedelta(days=1)).isoformat())
        s.commit()
        self.assertEqual([r["company"] for r in cli.due_followups(s)], ["A"])


class PreviousApplicationsTest(unittest.TestCase):
    def test_finds_legacy_folder_and_other_applied_role(self):
        legacy = Path(tempfile.mkdtemp())
        (legacy / "02-widgetco").mkdir()                          # a folder from before job-pilot
        user_p = HOME / "user.json"
        original = user_p.read_text()
        user = json.loads(original)
        user["legacy_cv_dirs"] = [str(legacy)]
        user_p.write_text(json.dumps(user))
        try:
            old, new = job("Widgetco LLC", "Flutter Engineer"), job("Widgetco LLC", "Remote Flutter Senior Engineer")
            s = store_with(old, new)
            s.update(old.key, status="Applied")
            s.commit()
            prior = cli.previous_applications(s, s.get(new.key), HOME / "jobs" / "widgetco-llc")
        finally:
            user_p.write_text(original)
        self.assertIn("02-widgetco", prior)
        self.assertIn("Flutter Engineer (Applied)", prior)


class DigestTest(unittest.TestCase):
    def test_digest_sections(self):
        a, b = job("A"), job("B")
        s = store_with(a, b)
        s.update(a.key, status="Tailoring", score=90, cv_dir="/x/a")
        s.update(b.key, status="Ready", score=80)
        s.commit()
        text = cli.write_digest(s).read_text()
        self.assertIn("To tailor (status Tailoring): 1", text)
        self.assertIn("## Ready to apply", text)
        self.assertIn(a.key[:8], text)


if __name__ == "__main__":
    unittest.main()
