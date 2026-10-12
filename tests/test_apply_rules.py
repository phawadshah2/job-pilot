"""Fill decisions on field lists captured from real Greenhouse / Ashby / Lever /
Workable application forms (tests/fixtures, public form structure only)."""
import json
import unittest
from pathlib import Path

from jobpilot.apply.rules import build_plan, pick_option
from jobpilot.apply.runner import application_url, ats_of

FIX = Path(__file__).resolve().parent / "fixtures"
APPLICANT = {
    "first_name": "Test", "last_name": "Person", "full_name": "Test Person", "email": "t@example.com",
    "phone": "+971 50 000 0000", "city": "Dubai", "country": "United Arab Emirates",
    "linkedin": "https://linkedin.com/in/test", "github": "https://github.com/test", "portfolio": None,
    "current_company": "Acme", "current_title": "Senior Flutter Developer", "years_experience": "5",
    "salary_expected": None, "visa_status": None, "how_heard": None,
    "_resume_path": "/tmp/cv.pdf",
}


def plan_for(ats, answers=None, **over):
    fields = json.loads((FIX / f"{ats}_fields.json").read_text())
    return fields, build_plan(fields, dict(APPLICANT, **over), answers or {})


def by_why(plan):
    return {a.why: a for a in plan.actions}


class GreenhouseTest(unittest.TestCase):
    def test_core_fields(self):
        _, p = plan_for("greenhouse")
        w = by_why(p)
        for key in ("first_name", "last_name", "email", "phone", "linkedin", "current_company", "current_title"):
            self.assertIn(key, w)
        self.assertEqual(w["resume"].kind, "upload")

    def test_typeahead_dropdowns_use_combobox(self):
        fields, p = plan_for("greenhouse")
        kinds = {a.kind for a in p.actions if a.why in ("country", "city")}
        self.assertEqual(kinds, {"combobox"})

    def test_gender_question_never_touched(self):
        _, p = plan_for("greenhouse")
        self.assertTrue(any("gender" in s.lower() for s in p.skipped_sensitive))

    def test_no_unlabelled_questions(self):
        _, p = plan_for("greenhouse")
        self.assertNotIn("?", [u["question"] for u in p.unanswered])

    def test_salary_left_for_human_then_answers_json_fills_it(self):
        _, p = plan_for("greenhouse")
        q = next(u["question"] for u in p.unanswered if "salary expectations" in u["question"])
        _, p2 = plan_for("greenhouse", answers={q: "AED 25,000"})
        self.assertIn("AED 25,000", [a.value for a in p2.actions])
        self.assertNotIn(q, [u["question"] for u in p2.unanswered])


class AshbyTest(unittest.TestCase):
    def test_resume_goes_to_resume_input_not_autofill_box(self):
        fields, p = plan_for("ashby")
        up = by_why(p)["resume"]
        target = next(f for f in fields if f["id"] == up.field_id)
        self.assertEqual(target["domId"], "_systemfield_resume")

    def test_name_email_phone(self):
        _, p = plan_for("ashby")
        self.assertTrue({"full_name", "email", "phone"} <= set(by_why(p)))


class LeverTest(unittest.TestCase):
    def test_years_range_option(self):
        _, p = plan_for("lever")
        self.assertEqual(by_why(p)["years_experience"].value, "4-6")

    def test_pronouns_and_consent_untouched(self):
        fields, p = plan_for("lever")
        checked = {a.field_id for a in p.actions if a.kind == "check"}
        for f in fields:
            if f["name"] in ("pronouns",) or "consent" in f["name"]:
                self.assertNotIn(f["id"], checked)

    def test_urls(self):
        _, p = plan_for("lever")
        self.assertTrue({"linkedin", "github", "full_name", "current_company"} <= set(by_why(p)))


class WorkableTest(unittest.TestCase):
    def test_unlabelled_inputs_matched_by_name(self):
        _, p = plan_for("workable")
        w = by_why(p)
        self.assertEqual(w["city"].value, "Dubai")
        self.assertEqual(w["country"].value, "United Arab Emirates")

    def test_criminal_record_question_never_auto_answered(self):
        _, p = plan_for("workable", answers={})
        self.assertTrue(any("criminal" in s.lower() for s in p.skipped_sensitive))

    def test_iqama_radio_left_when_visa_unknown(self):
        _, p = plan_for("workable")
        self.assertIn("Iqama Status", [u["question"] for u in p.unanswered])


class HelpersTest(unittest.TestCase):
    def test_pick_option(self):
        self.assertEqual(pick_option(["0-4", "4-6", "6-9", "9+"], "5", years=5), "4-6")
        self.assertEqual(pick_option(["0-4", "4-6", "6-9", "9+"], "10", years=10), "9+")
        self.assertEqual(pick_option(["Yes", "No"], "yes"), "Yes")
        self.assertEqual(pick_option(["I saw it on LinkedIn", "Other"], "LinkedIn"), "I saw it on LinkedIn")

    def test_application_urls(self):
        self.assertEqual(application_url("https://jobs.lever.co/acme/123"), "https://jobs.lever.co/acme/123/apply")
        self.assertEqual(application_url("https://jobs.ashbyhq.com/acme/abc"), "https://jobs.ashbyhq.com/acme/abc/application")
        self.assertEqual(application_url("https://apply.workable.com/acme/j/XYZ/"), "https://apply.workable.com/acme/j/XYZ/apply/")
        self.assertEqual(ats_of("https://job-boards.greenhouse.io/careem/jobs/1"), "greenhouse")


if __name__ == "__main__":
    unittest.main()
