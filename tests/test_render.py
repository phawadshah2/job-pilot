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


class CoverAndVersionsTest(unittest.TestCase):
    def setUp(self):
        from tests.helpers import use_example_home
        self.home = use_example_home()

    def job(self):
        from jobpilot import config, paths
        from jobpilot.models import Job
        from jobpilot.store import Store
        from jobpilot.tailor import prepare
        s = Store(paths.data_dir() / "jobs.db")
        j = Job(source="t", title="Senior Flutter Developer", company="Example Corp", url="https://example.com/j",
                location="Dubai", description="Flutter, BLoC, Firebase, Stripe payments.")
        s.add(j)
        s.commit()
        return prepare(s.get(j.key), config.load_skills())

    def test_cover_letter_rendered_and_guarded(self):
        import json
        from jobpilot.tailor import build, check_boundaries
        from jobpilot import config
        d = self.job()
        spec = {"headline": "Senior Flutter Developer", "summary": "S.",
                "cover_letter": {"paragraphs": ["I build Flutter apps with BLoC.", "I'd love to help Example Corp."]}}
        (d / "tailor.json").write_text(json.dumps(spec))
        build(d)
        cover = (d / "cover.html").read_text()
        self.assertIn("Dear Example Corp Hiring Team,", cover)
        self.assertIn("Re: Senior Flutter Developer", cover)
        self.assertEqual(check_boundaries(cover, config.load_skills()), [])
        spec["cover_letter"]["paragraphs"].append("I also know React Native.")
        (d / "tailor.json").write_text(json.dumps(spec))
        build(d)
        self.assertTrue(check_boundaries((d / "cover.html").read_text(), config.load_skills()))
        del spec["cover_letter"]
        (d / "tailor.json").write_text(json.dumps(spec))
        build(d)
        self.assertFalse((d / "cover.html").exists())

    def test_versions_kept_not_overwritten(self):
        from jobpilot.tailor import _archive
        d = self.job()
        (d / "A.pdf").write_text("v1")
        (d / "tailor.final.json").write_text("{}")
        _archive([d / "A.pdf", d / "A - Cover Letter.pdf", d / "tailor.final.json"], d)
        (d / "A.pdf").write_text("v2")
        _archive([d / "A.pdf"], d)
        self.assertEqual(sorted(p.name for p in (d / "versions").iterdir()),
                         ["A v1.pdf", "A v2.pdf", "tailor.final v1.json"])
        self.assertEqual((d / "versions" / "A v1.pdf").read_text(), "v1")
