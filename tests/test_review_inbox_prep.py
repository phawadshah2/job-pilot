"""Reviewer pass, Gmail status sync, interview prep (fictional example user)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import use_example_home

HOME = use_example_home()


def setUpModule():
    import os
    os.environ["JOBPILOT_HOME"] = str(HOME)

import jobpilot.__main__ as cli  # noqa: E402
from jobpilot.inbox import classify, company_terms, match  # noqa: E402
from jobpilot.models import Job  # noqa: E402
from jobpilot.prep import md_to_html  # noqa: E402
from jobpilot.review import critique  # noqa: E402

FACTS = "Improved app startup time by 35%. Serving 20K+ active users. 5+ years. Mentored 2 junior developers."


def job_dir(cv_text, letter=None, keywords=("Senior Flutter Developer", "BLoC", "CI/CD"), gaps="GraphQL"):
    d = Path(tempfile.mkdtemp())
    (d / "job.json").write_text(json.dumps({"company": "Acme Labs LLC", "title": "Senior Flutter Developer",
                                            "keywords": list(keywords), "gaps": gaps, "label": "Acme - Dev"}))
    (d / "jd.txt").write_text("https://acme.example/jobs/1\nFlutter, BLoC, CI/CD. 3+ years.")
    (d / "cv.html").write_text(f"<html><body><p>{cv_text}</p></body></html>")
    (d / "tailor.json").write_text(json.dumps({"headline": "Senior Flutter Developer | iOS & Android",
                                               "summary": "Flutter developer."}))
    if letter:
        (d / "cover-letter.txt").write_text(letter)
    return d


class ReviewerTest(unittest.TestCase):
    def test_flags_numbers_and_omitted_keywords(self):
        items = critique(job_dir("Flutter with BLoC. Cut startup by 35% and memory by 40% for 20K+ users."), FACTS)
        fixes = [m for k, m in items if k == "fix"]
        self.assertTrue(any("CI/CD" in m for m in fixes))           # they have it, CV doesn't say it
        self.assertTrue(any('"40%"' in m for m in fixes))           # invented metric
        self.assertFalse(any('"35%"' in m or "20K+" in m for m in fixes))
        self.assertIn(("gap", "GraphQL: not on your CV (prepare an honest answer)"), items)

    def test_cover_letter_checks(self):
        letter = ("Dear Hiring Manager,\n\nI am writing to apply. I am a team player. " + "Flutter apps ship fast. " * 40
                  + "\n\nKind regards")
        kinds = [m for k, m in critique(job_dir("Flutter BLoC CI/CD", letter), FACTS) if k == "consider"]
        self.assertTrue(any("greeting doesn't name Acme Labs" in m for m in kinds))
        self.assertTrue(any("generic opener" in m for m in kinds))
        self.assertTrue(any("team player" in m for m in kinds))
        self.assertTrue(any("repeated phrase" in m for m in kinds))

    def test_clean_cv_has_no_fixes(self):
        items = critique(job_dir("Flutter, BLoC and CI/CD. 35% faster startup for 20K+ users."), FACTS)
        self.assertEqual([k for k, _ in items if k == "fix"], [])


class InboxTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("Your application", "Thank you for applying. Unfortunately we will not be moving forward.")[0], "rejected")
        self.assertEqual(classify("Next steps", "We'd love to schedule a call. Please share your availability.")[0], "interview")
        self.assertEqual(classify("Coding challenge", "Please complete the HackerRank test.")[0], "assessment")
        self.assertEqual(classify("Application received", "Thank you for your application to Acme.")[0], "received")
        self.assertEqual(classify("Great news", "We are pleased to offer you the role.")[0], "offer")
        self.assertIsNone(classify("Newsletter", "Our product update for October."))

    def test_matching(self):
        self.assertEqual(company_terms("Acme Labs LLC", "https://careers.acme.ae/jobs/1"), ["acme labs", "acme.ae"])
        self.assertEqual(company_terms("Hivex", "https://himalayas.app/companies/hivex/jobs/x"), ["hivex"])
        apps = [{"key": "k1", "company": "Hivex", "url": "https://himalayas.app/x"}]
        self.assertEqual(match(apps, "Hivex HR <hr@hivex.io>", "Interview", "Hi")["key"], "k1")
        self.assertIsNone(match(apps, "LinkedIn <jobs-noreply@linkedin.com>", "Hivex is hiring", "Hivex"))
        self.assertIsNone(match(apps, "Bob <bob@other.com>", "Hello", "Hivexample is a word"))

    def test_apply_only_what_you_approve(self):
        store = cli.open_store()
        j = Job(source="t", title="Senior Flutter Developer", company="InboxCo", url="https://inboxco.example/j/1")
        store.add(j)
        store.update(j.key, status="Submitted", applied_at="2026-10-17")
        for mid, kind in (("<a@x>", "interview"), ("<b@x>", "received"), ("<c@x>", "rejected")):
            store.db.execute("INSERT INTO inbox (msg_id, key, kind, subject, sender, sent, evidence, seen_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (mid, j.key, kind, f"{kind} mail", "hr@inboxco.example",
                                                          "2026-10-20", "...", "2026-10-20"))
        store.commit()
        ids = {r["kind"]: str(r["id"]) for r in cli.inbox_pending(store)}
        with mock.patch.object(cli, "sync", return_value=0), mock.patch("builtins.print"):
            cli.cmd_inbox(mock.Mock(apply=[ids["interview"], ids["received"]], dismiss=[ids["rejected"]]))
        s = cli.open_store()
        self.assertEqual(s.get(j.key)["status"], "Interview")             # interview approved
        self.assertIn("interview email", s.get(j.key)["notes"])
        self.assertIn("received email", s.get(j.key)["notes"])            # received: note only
        self.assertEqual(cli.inbox_pending(s), [])                        # rejection dismissed, never applied


class PrepTest(unittest.TestCase):
    def test_markdown_subset(self):
        out = md_to_html("# Acme\n## Questions\n1. Why **Flutter**?\n- point <x>\n\nText")
        self.assertIn('<p class="company">Acme</p>', out)
        self.assertIn("<ol>\n<li>Why <b>Flutter</b>?</li>\n</ol>", out)
        self.assertIn("<li>point &lt;x&gt;</li>", out)
        self.assertIn("<p>Text</p>", out)


if __name__ == "__main__":
    unittest.main()
