"""Teamtailor, Recruitee, Breezy and We Work Remotely parsers (offline, fixtures inline)."""
import unittest
from unittest import mock

from jobpilot.sources import ats_boards, remote_boards

CFG = {"companies": {"teamtailor": ["acme"], "recruitee": ["acme"], "breezy": ["acme"]}, "search_terms": ["flutter"]}

TT_RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>
<title>Acme</title><item><title>Senior Flutter Engineer</title><description>&lt;p&gt;Flutter, Dart&lt;/p&gt;</description>
<pubDate>Tue, 14 Oct 2026 17:36:35 +0400</pubDate><link>https://acme.teamtailor.com/jobs/1-senior-flutter</link>
<remoteStatus>hybrid</remoteStatus><company_name>Acme</company_name>
<tt:locations><tt:location><tt:city>Dubai</tt:city><tt:country>United Arab Emirates</tt:country></tt:location></tt:locations>
</item></channel></rss>"""

WWR_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><item><title>Beta: Senior Flutter Developer</title>
<region>Anywhere in the World</region><description>Flutter</description><pubDate>Tue, 14 Oct 2026 10:00:00 +0000</pubDate>
<link>https://weworkremotely.com/remote-jobs/beta-senior-flutter</link></item></channel></rss>"""


class NewSourcesTest(unittest.TestCase):
    def test_teamtailor(self):
        with mock.patch.object(ats_boards, "get_text", return_value=TT_RSS):
            j = ats_boards.teamtailor(CFG)[0]
        self.assertEqual((j.title, j.company, j.location), ("Senior Flutter Engineer", "Acme", "Dubai, United Arab Emirates"))
        self.assertEqual(j.notes, ["hybrid"])
        self.assertEqual(j.posted_at.year, 2026)
        self.assertIn("Flutter", j.description)

    def test_recruitee(self):
        offer = {"title": "Flutter Developer", "company_name": "Acme", "careers_url": "https://acme.recruitee.com/o/flutter",
                 "careers_apply_url": "https://acme.recruitee.com/o/flutter/c/new", "location": "Riyadh, Saudi Arabia",
                 "remote": False, "hybrid": True, "published_at": "2026-10-11 09:26:09 UTC", "description": "<p>Dart</p>"}
        with mock.patch.object(ats_boards, "get_json", return_value={"offers": [offer]}):
            j = ats_boards.recruitee(CFG)[0]
        self.assertEqual((j.apply_url, j.location, j.notes), (offer["careers_apply_url"], "Riyadh, Saudi Arabia", ["hybrid"]))
        self.assertEqual(j.posted_at.day, 11)

    def test_breezy(self):
        item = {"name": "Mobile Engineer", "url": "https://acme.breezy.hr/p/1", "published_date": "2026-09-18T05:08:12.246Z",
                "type": {"name": "Full-Time"}, "location": {"city": "Dubai", "country": {"name": "United Arab Emirates"}}}
        with mock.patch.object(ats_boards, "get_json", return_value=[item]):
            j = ats_boards.breezy(CFG)[0]
        self.assertEqual((j.title, j.location, j.employment_type), ("Mobile Engineer", "Dubai, United Arab Emirates", "Full-Time"))

    def test_weworkremotely(self):
        with mock.patch.object(remote_boards, "get_text", return_value=WWR_RSS):
            jobs = remote_boards.weworkremotely(CFG)
        self.assertEqual(len(jobs), 1)  # same item in both feeds -> once
        self.assertEqual((jobs[0].company, jobs[0].title, jobs[0].location), ("Beta", "Senior Flutter Developer", "Worldwide"))

    def test_broken_board_does_not_stop_the_run(self):
        with mock.patch.object(ats_boards, "get_text", side_effect=TimeoutError("slow")), mock.patch("builtins.print"):
            self.assertEqual(ats_boards.teamtailor(CFG), [])


if __name__ == "__main__":
    unittest.main()
