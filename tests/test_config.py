import json
import unittest

from jobpilot import config
from tests.helpers import EXAMPLE, REPO

DEFAULTS = json.loads((REPO / "config.json").read_text())


def user(**search):
    u = json.loads((EXAMPLE / "user.json").read_text())
    u["search"].update(search)
    return u


class BuildTest(unittest.TestCase):
    def test_searches_follow_region_priority(self):
        cfg = config.build(DEFAULTS, user(), {})
        q = cfg["jsearch"]["queries"]
        self.assertEqual(q[0], {"query": "flutter developer in United Arab Emirates", "country": "ae", "region": "uae"})
        self.assertEqual(len(q), 3 + 5 + 1 + 1 + 1 + 1)          # uae, gcc, remote, uk, us, au
        self.assertTrue(any(x.get("remote_only") for x in q))
        self.assertEqual(cfg["region_order"][0], "uae")
        self.assertGreater(cfg["region_points"]["uae"], cfg["region_points"]["gcc"])

    def test_other_role_other_regions(self):
        u = user(regions=["remote", "eu"], home_country="de", languages=["english", "german"])
        u["role"].update(target_title="Backend Engineer", search_title="backend engineer", keywords=["backend", "golang"])
        cfg = config.build(DEFAULTS, u, {})
        self.assertEqual(cfg["filters"]["title_must_match_any"], ["backend", "golang"])
        self.assertEqual(cfg["jsearch"]["queries"][0]["query"], "backend engineer remote")
        self.assertNotIn("German speaker required", cfg["filters"]["dealbreakers"])
        self.assertIn("Arabic speaker required", cfg["filters"]["dealbreakers"])

    def test_us_citizen_keeps_citizenship_jobs(self):
        self.assertIn("citizenship / clearance required", config.build(DEFAULTS, user(), {})["filters"]["dealbreakers"])
        self.assertNotIn("citizenship / clearance required",
                         config.build(DEFAULTS, user(us_citizen=True), {})["filters"]["dealbreakers"])

    def test_based_elsewhere_only_for_onsite(self):
        from jobpilot.filters import Filter
        from jobpilot.models import Job
        f = Filter(config.build(DEFAULTS, user(), {}))
        jd = "Our company is based in Germany."
        self.assertIsNone(f.classify(Job(source="t", title="Senior Flutter Developer", company="X", url="u",
                                         location="Remote", remote=True, description=jd)))
        self.assertEqual(f.classify(Job(source="t", title="Senior Flutter Developer", company="X", url="u",
                                        location="Dubai", description="This role is based in India.")),
                         "based outside target countries")

    def test_no_personal_data_in_defaults(self):
        import re
        text = (REPO / "config.json").read_text()
        emails = [e for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text) if "noreply" not in e]
        self.assertEqual(emails, [], "personal email address in config.json")
        self.assertIsNone(re.search(r"\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}", text), "phone number in config.json")
        self.assertEqual(json.loads(text)["sources"].get("gmail"), True)


if __name__ == "__main__":
    unittest.main()
