import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from jobpilot.scoring import Lexicon, Scorer, _years_required
from jobpilot.tailor import check_boundaries, check_style_unchanged, coverage, file_label

from tests.helpers import example_cfg, example_skills

PROFILE = example_skills()
CFG = example_cfg()


class ScorerTest(unittest.TestCase):
    def setUp(self):
        self.s = Scorer(PROFILE, today=date(2026, 10, 11), region_points=CFG["region_points"],
                        keywords=CFG["filters"]["title_must_match_any"])

    def test_strong_flutter_match_scores_high(self):
        jd = """We need a Senior Flutter Developer. Requirements: 5+ years of mobile experience,
        Dart, BLoC or Riverpod, Clean Architecture, RESTful APIs, Firebase, unit tests and widget tests,
        CI/CD with GitHub Actions, App Store and Google Play releases."""
        r = self.s.score("Senior Flutter Developer", jd, "remote-worldwide", "senior")
        self.assertGreaterEqual(r.total, 90)
        self.assertEqual(r.gaps, [])

    def test_react_native_requirement_is_a_hard_gap(self):
        jd = "Senior Mobile Engineer. Must have 4 years with React Native and TypeScript. Flutter knowledge helps."
        r = self.s.score("Senior Mobile Engineer", jd, "remote-worldwide", "senior")
        self.assertIn("React Native", r.gaps)
        self.assertIn("TypeScript", r.gaps)
        self.assertLess(r.total, 60)

    def test_nice_to_have_gap_is_softer(self):
        hard = self.s.score("Senior Flutter Dev", "Flutter, Dart. Docker required.", "remote-worldwide", "senior")
        soft = self.s.score("Senior Flutter Dev", "Flutter, Dart. Docker is a plus.", "remote-worldwide", "senior")
        self.assertIn("Docker (nice-to-have)", soft.gaps)
        self.assertGreater(soft.total, hard.total)

    def test_years_over_experience_penalised_and_reported(self):
        r = self.s.score("Senior Flutter Dev", "Flutter. Minimum 9 years of mobile development experience.", "onsite-gulf", "senior")
        self.assertEqual(r.breakdown["experience"], 0)
        self.assertEqual(r.years_required, 9)
        self.assertIn("9+ yrs asked", r.gaps_text())

    def test_unverified_native_skills_flagged_not_gapped(self):
        r = self.s.score("Senior Flutter Dev", "Flutter plus Swift for native modules.", "remote-worldwide", "senior")
        self.assertIn("Swift", r.unverified)
        self.assertNotIn("Swift", r.gaps)

    def test_gcc_onsite_gets_relocation_note(self):
        r = self.s.score("Senior Flutter Developer", "Office in Riyadh, Saudi Arabia.", "onsite-gcc", "senior", region="gcc")
        self.assertIn("GCC: relocation", r.notes)

    def test_uae_ranks_above_other_regions(self):
        jd = "Flutter, Dart, BLoC."
        pts = {r: self.s.score("Senior Flutter Developer", jd, f"onsite-{r}", "senior", region=r).breakdown["location"]
               for r in ("uae", "gcc", "uk", "us", "au")}
        self.assertEqual(max(pts, key=pts.get), "uae")
        self.assertGreater(pts["gcc"], pts["uk"])

    def test_empty_jd_noted(self):
        r = self.s.score("Senior Flutter Developer", "", "onsite-gulf", "senior")
        self.assertIn("no JD text: score is a guess", r.notes)


class LexiconPrecisionTest(unittest.TestCase):
    """Common English words must not count as skills (checks the shipped skill catalog)."""
    def setUp(self):
        from jobpilot.setup import load_catalog
        self.have = Lexicon(load_catalog())

    def test_rest_of_team_is_not_rest_api(self):
        self.assertNotIn("RESTful APIs", self.have.find("Work with the rest of the team."))
        self.assertIn("RESTful APIs", self.have.find("Integrate REST APIs."))

    def test_solid_experience_is_not_solid_principles(self):
        self.assertNotIn("SOLID", self.have.find("You have solid experience."))
        self.assertIn("SOLID", self.have.find("Apply SOLID principles."))

    def test_service_provider_is_not_provider_package(self):
        self.assertNotIn("Provider", self.have.find("A leading payments service provider in the UAE."))
        self.assertIn("Provider", self.have.find("State management: Provider, BLoC or Riverpod."))

    def test_get_it_package_not_english_phrase(self):
        self.assertIn("get_it (DI)", self.have.find("Dependency Injection (GetIt) and a feature-based structure."))
        self.assertNotIn("get_it (DI)", self.have.find("You will get it done fast."))

    def test_phrases_keep_jd_wording(self):
        self.assertEqual(self.have.phrases("We use RESTful services")["RESTful APIs"], "RESTful")


class YearsTest(unittest.TestCase):
    def test_patterns(self):
        self.assertEqual(_years_required("5+ years of experience in Flutter"), 5)
        self.assertEqual(_years_required("3-5 years experience"), 3)
        self.assertEqual(_years_required("at least 7 years' professional mobile development experience"), 7)
        self.assertIsNone(_years_required("We have been around for 10 years."))


class GuardTest(unittest.TestCase):
    def test_forbidden_skill_blocked(self):
        self.assertTrue(check_boundaries("<p>Skills: Flutter, React Native</p>", PROFILE))
        self.assertTrue(check_boundaries("<p>Docker and GraphQL</p>", PROFILE))

    def test_typescript_only_in_allowed_phrase(self):
        self.assertFalse(check_boundaries("<p>Dart (Expert, TypeScript-comparable)</p>", PROFILE))
        self.assertTrue(check_boundaries("<p>Languages: Dart, TypeScript</p>", PROFILE))

    def test_example_default_cv_passes(self):
        from jobpilot.cv.render import render
        cv = json.loads((Path(__file__).resolve().parent.parent / "examples/alex/cv.json").read_text())
        self.assertEqual(check_boundaries(render(cv), PROFILE), [])


class StyleGuardTest(unittest.TestCase):
    TPL = "<html><head><title>A</title><style>h2{color:#1F3864}</style></head><body><p>x</p></body></html>"

    def test_text_and_title_changes_allowed(self):
        cv = self.TPL.replace("<p>x</p>", "<p>tailored</p>").replace("<title>A</title>", "<title>B</title>")
        self.assertTrue(check_style_unchanged(cv, self.TPL))

    def test_style_changes_refused(self):
        self.assertFalse(check_style_unchanged(self.TPL.replace("#1F3864", "#000"), self.TPL))


class HelpersTest(unittest.TestCase):
    def test_file_label_strips_path_chars(self):
        self.assertEqual(file_label("Acme/Inc", "Senior Flutter: Mobile"), "Acme-Inc - Senior Flutter- Mobile")

    def test_coverage(self):
        _, old, new = coverage(["Flutter", "BLoC", "Riverpod"], "Flutter", "Flutter BLoC Riverpod")
        self.assertEqual((old, new), (33, 100))


if __name__ == "__main__":
    unittest.main()
