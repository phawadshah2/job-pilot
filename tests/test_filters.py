import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobpilot.filters import Filter
from jobpilot.models import Job

from tests.helpers import example_cfg

CFG = example_cfg()
NOW = datetime.now(timezone.utc)


def job(title, location="", remote=None, desc="We use Flutter.", days_old=1, notes=None):
    return Job(source="t", title=title, company="Acme", url="u", location=location, remote=remote,
               description=desc, posted_at=NOW - timedelta(days=days_old), notes=list(notes or []))


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.f = Filter(CFG)

    def keep(self, j):
        reason = self.f.classify(j)
        self.assertIsNone(reason, f"expected keep, dropped: {reason}")
        return j

    def drop(self, j, contains=""):
        reason = self.f.classify(j)
        self.assertIsNotNone(reason, "expected drop, was kept")
        self.assertIn(contains, reason)

    # --- role -------------------------------------------------------------
    def test_flutter_title_kept(self):
        self.assertEqual(self.keep(job("Senior Flutter Developer", "Worldwide", True)).seniority, "senior")

    def test_sr_abbrev_is_senior(self):
        self.assertEqual(self.keep(job("Sr. Flutter Engineer", "Dubai, UAE")).seniority, "senior")

    def test_mobile_title_needs_flutter_in_jd(self):
        self.keep(job("Senior Mobile Engineer", "Riyadh, Saudi Arabia"))
        self.drop(job("Senior Mobile Engineer", "Riyadh, Saudi Arabia", desc="Swift and Kotlin only"), "role keyword not in JD")

    def test_mobile_title_without_jd_kept_with_note(self):
        j = self.keep(job("Mobile Developer", "Dubai", desc=""))
        self.assertTrue(any("not confirmed" in n for n in j.notes))

    def test_excluded_titles(self):
        self.drop(job("Junior Flutter Developer", "Remote", True), "excluded")
        self.drop(job("Flutter Intern", "Dubai"), "excluded")
        self.drop(job("Flutter Tutor", "Remote", True), "excluded")

    def test_react_native_only_dropped_but_either_or_kept(self):
        self.drop(job("Senior React Native Developer", "Remote", True, desc="Flutter nice to have"), "other cross-platform")
        self.keep(job("Mobile App Developer (Flutter / React Native)", "Remote", True))

    def test_unrelated_title_dropped(self):
        self.drop(job("Backend Engineer", "Dubai"), "not a matching role")

    # --- location ---------------------------------------------------------
    def test_remote_plain_is_worldwide(self):
        self.assertEqual(self.keep(job("Flutter Developer", "Remote", True)).work_mode, "remote-worldwide")
        self.assertEqual(self.keep(job("Flutter Developer", "", True)).work_mode, "remote-worldwide")

    def test_remote_limited_to_another_country_is_dropped_unless_open_to_you(self):
        # the example user lives in the UAE: a US-remote job that spells out residency is dropped
        self.drop(job("Senior Flutter Developer", "Remote - US", True, desc="Flutter. Must be based in the US."),
                  "residents only (Must be based in)")
        self.drop(job("[US] Senior Flutter Developer", "United States", True), "residents only ([US])")
        unclear = self.keep(job("Senior Flutter Developer", "United States", True))   # a board's label only
        self.assertIn("listed as US-remote: check it's open to you", unclear.notes)
        anywhere = job("Senior Flutter Developer", "United States", True)
        anywhere.description = "Remote job with the freedom to work from anywhere in the world."
        self.assertEqual((self.keep(anywhere).work_mode, anywhere.region), ("remote-region", "us"))
        mena = job("Senior Flutter Developer", "Remote - UK", True)
        mena.description = "We hire across EMEA."
        self.keep(mena)
        self.assertEqual(self.keep(job("Senior Flutter Developer", "Remote - Dubai", True)).region, "uae")
        self.drop(job("Senior Flutter Developer", "Poland", True), "restricted")

    def test_remote_region_ok(self):
        self.assertEqual(self.keep(job("Flutter Dev", "EMEA", True)).work_mode, "remote-region")
        self.assertEqual(self.keep(job("Flutter Dev", "Middle East", True)).work_mode, "remote-region")

    def test_onsite_uae_and_gcc(self):
        self.assertEqual(self.keep(job("Senior Flutter Developer", "Abu Dhabi, United Arab Emirates", False)).work_mode, "onsite-uae")
        self.assertEqual(self.keep(job("Senior Flutter Developer", "Jeddah, SA", False)).work_mode, "onsite-gcc")
        self.assertEqual(self.keep(job("Senior Flutter Developer", "Doha, Qatar", False)).region, "gcc")

    def test_hybrid_gulf(self):
        j = self.keep(job("Senior Flutter Developer", "Dubai", notes=["hybrid"]))
        self.assertEqual(j.work_mode, "hybrid-uae")
        self.assertNotIn("hybrid", j.notes)

    def test_onsite_elsewhere_dropped(self):
        self.drop(job("Senior Flutter Developer", "Berlin, Germany", False), "onsite outside")
        self.drop(job("Senior Flutter Developer", "Lisbon, Portugal", False), "onsite outside")

    def test_uk_us_au_onsite_kept(self):
        for loc, region in [("London, UK", "uk"), ("Austin, TX, USA", "us"), ("Sydney NSW, Australia", "au")]:
            self.assertEqual(self.keep(job("Senior Flutter Developer", loc, False)).region, region)

    def test_usa_is_us_not_saudi(self):
        self.assertEqual(self.keep(job("Senior Flutter Developer", "USA", False)).region, "us")

    # --- freshness --------------------------------------------------------
    def test_old_jobs_dropped(self):
        self.drop(job("Senior Flutter Developer", "Remote", True, days_old=30), "too old")


class DealbreakerTest(unittest.TestCase):
    """All from real postings seen on 2026-10-12."""
    def setUp(self):
        self.f = Filter(CFG)

    def reason(self, title, desc="", loc="Dubai"):
        return self.f.classify(Job(source="t", title=title, company="X", url="u", location=loc, description=desc))

    def test_arabic_speaker_dropped_but_arabic_ui_kept(self):
        self.assertEqual(self.reason("Mobile Applications Developer (Flutter/Xamarin) ARABIC SPEAKING"), "Arabic speaker required")
        self.assertEqual(self.reason("Flutter Developer", "Fluent in Arabic is required."), "Arabic speaker required")
        self.assertIsNone(self.reason("Mobile App(Flutter)", "Strong support for Arabic UI/RTL and accessibility."))

    def test_based_elsewhere_dropped(self):
        self.assertEqual(self.reason("Senior Flutter Developer", "This is a full-time role based in India."), "based outside target countries")
        self.assertEqual(self.reason("Senior Flutter Engineer", "On-site roles available in Jaipur or Gandhinagar."), "based outside target countries")

    def test_other_stack_and_citizenship(self):
        self.assertEqual(self.reason("Flutter / Rust Developer"), "other primary stack")
        self.assertEqual(self.reason("Senior Flutter Developer", "Must be a US citizen.", "Austin, TX, US"), "citizenship / clearance required")

    def test_flutter_as_a_plus_dropped(self):
        self.assertEqual(self.reason("React.js & Node.js Developer (Flutter a plus) - REMOTE", loc="Remote - US"), "Flutter is not the main stack")
        self.assertEqual(self.reason("Flutter Flow / React Native developer", loc="Remote - US"), "other primary stack")
        self.assertIsNone(self.reason("Senior Flutter Developer"))

    def test_low_pay_flagged_not_dropped(self):
        j = Job(source="t", title="Flutter Mobile App Developer", company="X", url="u", location="Dubai",
                description="Pay: AED6,000.00 - AED7,000.00 per month")
        self.assertIsNone(self.f.classify(j))
        self.assertIn("low pay: AED 6,000–7,000/month", j.notes)


class BlocklistTest(unittest.TestCase):
    def test_blocklisted_company_source_or_url_dropped(self):
        f = Filter(CFG)
        self.assertEqual(f.classify(Job(source="jsearch:usa remote jobs mysmartpros", title="Senior Flutter Developer",
                                        company="X", url="u", remote=True)), "blocklisted")
        self.assertEqual(f.classify(Job(source="t", title="Senior Flutter Developer", company="HireClip Inc",
                                        url="u", remote=True)), "blocklisted")
        self.assertEqual(f.classify(Job(source="t", title="Senior Flutter Developer", company="Y",
                                        url="https://us.remotejobs.dedyn.io/job/1", remote=True)), "blocklisted")
        self.assertIsNone(f.classify(Job(source="jsearch:linkedin", title="Senior Flutter Developer", company="Careem",
                                         url="https://careem.com", location="Dubai", remote=False)))


class DedupKeyTest(unittest.TestCase):
    def test_same_job_on_two_boards_shares_key(self):
        a = Job(source="a", title="Sr. Flutter Developer (Remote)", company="Acme Inc.", url="1")
        b = Job(source="b", title="Senior Flutter Developer", company="ACME", url="2")
        self.assertEqual(a.key, b.key)

    def test_different_roles_differ(self):
        a = Job(source="a", title="Senior Flutter Developer", company="Acme", url="1")
        b = Job(source="a", title="Lead Flutter Developer", company="Acme", url="1")
        self.assertNotEqual(a.key, b.key)


if __name__ == "__main__":
    unittest.main()
