import json
import unittest

from jobpilot.cv.render import SpecError, head, render
from jobpilot.tailor import check_style_unchanged
from tests.helpers import EXAMPLE

CV = json.loads((EXAMPLE / "cv.json").read_text())


class RenderTest(unittest.TestCase):
    def test_default_cv_has_all_sections_and_design(self):
        out = render(CV)
        for h in ("PROFESSIONAL SUMMARY", "CORE COMPETENCIES", "PROFESSIONAL EXPERIENCE", "KEY PROJECTS", "EDUCATION"):
            self.assertIn(f"<h2>{h}</h2>", out)
        self.assertTrue(check_style_unchanged(out, head()))
        self.assertIn("ALEX DOE", out)

    def test_spec_changes_text_only(self):
        spec = {"headline": "Senior Flutter Engineer | Payments & Fintech", "summary": "Tailored <b>summary</b> & more.",
                "skills": ["fw", {"key": "lang", "text": "Dart"}, {"label": "Payments", "text": "Stripe, Apple Pay"}],
                "experience": {"acmeshop": ["integrated", "A reworded bullet."]},
                "project_order": ["acmeshopapp"]}
        out = render(CV, spec)
        self.assertTrue(check_style_unchanged(out, render(CV)))
        self.assertIn("Payments &amp; Fintech", out)
        self.assertIn("&lt;b&gt;summary&lt;/b&gt;", out)
        self.assertEqual(out.count('<p class="skill">'), 3)
        self.assertLess(out.index(CV["experience"][0]["bullets"]["integrated"][:25]), out.index("A reworded bullet."))
        self.assertIn(CV["experience"][1]["bullets"]["delivered"][:25], out)   # untouched job keeps its order

    def test_unknown_ids_rejected(self):
        for spec in ({"skills": ["nope"]}, {"experience": {"acmeshop": ["nope"]}}, {"experience": {"ghost": ["x y z"]}},
                     {"project_order": ["ghost"]}):
            with self.assertRaises(SpecError):
                render(CV, spec)


if __name__ == "__main__":
    unittest.main()
