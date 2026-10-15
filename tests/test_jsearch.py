import unittest

from jobpilot.sources.jsearch import best_apply_link, endpoint, parse
from jobpilot.text import clean_location

JOB = {
    "job_title": "Senior Flutter Developer", "employer_name": "Acme", "job_publisher": "LinkedIn",
    "job_apply_link": "https://www.linkedin.com/jobs/view/1", "job_city": "Dubai", "job_country": "AE",
    "job_is_remote": False, "job_posted_at_datetime_utc": "2026-10-10T00:00:00.000Z",
    "job_description": "Flutter, Dart, BLoC.", "work_arrangement": "hybrid",
    "apply_options": [
        {"apply_link": "https://www.linkedin.com/jobs/view/1", "is_direct": False, "publisher": "LinkedIn"},
        {"apply_link": "https://bebee.com/x", "is_direct": False, "publisher": "BeBee"},
        {"apply_link": "https://job-boards.greenhouse.io/acme/jobs/9", "is_direct": True, "publisher": "Acme"},
    ],
}


class JSearchTest(unittest.TestCase):
    def test_parses_search_v2_shape(self):
        jobs = parse({"status": "OK", "data": {"jobs": [JOB], "cursor": "abc"}})
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual((j.title, j.company, j.location), ("Senior Flutter Developer", "Acme", "Dubai, United Arab Emirates"))
        self.assertEqual(j.notes, ["hybrid"])
        self.assertEqual(j.posted_at.year, 2026)

    def test_parses_legacy_list_shape(self):
        self.assertEqual(len(parse({"data": [JOB]})), 1)
        self.assertEqual(parse({}), [])

    def test_country_scoped_search_fills_missing_location(self):
        j = dict(JOB, job_city=None, job_country=None, job_location="Anywhere")
        self.assertEqual(parse({"data": {"jobs": [j]}}, default_country="ae")[0].location, "Anywhere, United Arab Emirates")
        self.assertEqual(parse({"data": {"jobs": [JOB]}}, default_country="ae")[0].location, "Dubai, United Arab Emirates")
        self.assertEqual(parse({"data": {"jobs": [j]}})[0].location, "Anywhere")

    def test_clean_location_arabic_and_via_suffix(self):
        cases = {
            "الشارقة - إمارة الشارقةّ     •  عبر Trabajo.org, United Arab Emirates": "Sharjah, United Arab Emirates",
            "دبي     •  عبر Indeed, United Arab Emirates": "Dubai, United Arab Emirates",
            "أبو ظبي, AE": "Abu Dhabi, United Arab Emirates",
            "الفجيرة - إمارة الفجيرة     •  عبر BeBee": "Fujairah, United Arab Emirates",
            "الرياض": "Riyadh, Saudi Arabia",
            "المملكة العربية السعودية": "Saudi Arabia",
            "Remote, Worldwide": "Remote, Worldwide",
            "": "",
        }
        for raw, want in cases.items():
            self.assertEqual(clean_location(raw), want, raw)

    def test_prefers_employer_ats_over_linkedin(self):
        self.assertEqual(best_apply_link(JOB), "https://job-boards.greenhouse.io/acme/jobs/9")

    def test_account_platform_beats_aggregator(self):
        j = dict(JOB, apply_options=JOB["apply_options"][:2])
        self.assertEqual(best_apply_link(j), "https://www.linkedin.com/jobs/view/1")
        self.assertEqual(best_apply_link(dict(JOB, apply_options=[])), "https://www.linkedin.com/jobs/view/1")

    def test_company_site_beats_account_platform(self):
        j = dict(JOB, apply_options=[JOB["apply_options"][0],
                                     {"apply_link": "https://careers.acme.ae/jobs/9", "is_direct": True}])
        self.assertEqual(best_apply_link(j), "https://careers.acme.ae/jobs/9")

    def test_aggregator_only_jobs_are_dropped(self):
        j = dict(JOB, job_apply_link="https://ae.jooble.org/jdp/1",
                 apply_options=[{"apply_link": "https://www.jobleads.com/ae/job/x"}, {"apply_link": "https://bebee.com/x"}])
        self.assertEqual(best_apply_link(j), "")
        self.assertEqual(parse({"data": {"jobs": [j, JOB]}})[0].company, "Acme")
        self.assertEqual(len(parse({"data": {"jobs": [j]}})), 0)

    def test_provider_choice(self):
        self.assertIn("openwebninja", endpoint({"OPENWEBNINJA_API_KEY": "k", "RAPIDAPI_KEY": "r"})[0])
        self.assertIn("rapidapi", endpoint({"RAPIDAPI_KEY": "r"})[0])
        self.assertIsNone(endpoint({}))


if __name__ == "__main__":
    unittest.main()
