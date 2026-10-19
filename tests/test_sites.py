import unittest

from jobpilot.sites import apply_via, classify, is_aggregator, open_only


class SitesTest(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "https://job-boards.greenhouse.io/acme/jobs/9": "form",
            "https://jobs.lever.co/acme/1/apply": "form",
            "https://acme.recruitee.com/o/flutter": "form",
            "https://careers.acme.ae/jobs/9": "company",
            "https://www.linkedin.com/jobs/view/1/": "account",
            "https://ae.indeed.com/viewjob?jk=1": "account",
            "https://www.bayt.com/en/uae/jobs/x-1/": "account",
            "https://www.gulftalent.com/uae/jobs/software-engineer-643017": "account",
            "https://www.founditgulf.com/job/x": "account",
            "https://wellfound.com/jobs/1-flutter": "account",
            "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/1": "portal",
            "https://career5.successfactors.eu/careers?company=x": "portal",
            "https://himalayas.app/companies/x/jobs/y": "board",
            "https://au.seek.com/job/95278039": "account",
            "https://ae.jooble.org/jdp/1": "aggregator",
            "https://www.jobaaj.com/job/x-1": "aggregator",
            "https://www.jobleads.com/ae/job/x": "aggregator",
            "https://www.upwork.com/freelance-jobs/apply/x": "aggregator",
        }
        for url, kind in cases.items():
            self.assertEqual(classify(url)[0], kind, url)

    def test_labels_and_rules(self):
        self.assertEqual(apply_via("https://jobs.lever.co/a/1"), "Lever form (no account)")
        self.assertEqual(apply_via("https://himalayas.app/x", "https://acme.wd1.myworkdayjobs.com/x"), "Workday (needs account)")
        self.assertEqual(apply_via("https://www.bayt.com/en/uae/jobs/x-1/"), "Bayt (your account)")
        self.assertTrue(open_only("https://www.bayt.com/en/uae/jobs/x-1/"))
        self.assertTrue(open_only("https://acme.taleo.net/careersection/x"))
        self.assertFalse(open_only("https://careers.acme.ae/jobs/9"))
        self.assertTrue(is_aggregator("https://ae.trabajo.org/job-1"))
        self.assertFalse(is_aggregator("https://notjooble.org.example.com/x"))


if __name__ == "__main__":
    unittest.main()
