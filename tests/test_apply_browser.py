"""End-to-end fill against a local mock form in headless Chrome.
Runs only with the venv:  .venv/bin/python -m unittest tests.test_apply_browser"""
import tempfile
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # core tests run on plain python3 without Playwright
    sync_playwright = None

from jobpilot.apply import runner

MOCK = (Path(__file__).resolve().parent / "fixtures" / "mock_form.html").as_uri()


@unittest.skipIf(sync_playwright is None, "Playwright not installed (use .venv/bin/python)")
class MockFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)
        cls.cv = Path(tempfile.mkdtemp()) / "Acme - Senior Flutter Developer.pdf"
        cls.cv.write_bytes(b"%PDF-1.4 test")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_fill(self, answers=None):
        page = self.browser.new_page()
        fields = runner.open_form(page, MOCK)
        applicant = {"first_name": "Test", "last_name": "Person", "email": "t@example.com", "phone": "+971500000000",
                     "country": "United Arab Emirates", "years_experience": "5", "notice_period": "1 month",
                     "_resume_path": str(self.cv)}
        res = runner.fill(page, fields, applicant, answers or {})
        runner.highlight(page, fields, res, "Acme - Senior Flutter Developer")
        return page, res

    def test_fills_everything_it_should(self):
        page, res = self.run_fill()
        self.assertEqual(res.failed, [])
        self.assertEqual(page.input_value("#first_name"), "Test")
        self.assertEqual(page.input_value("#email"), "t@example.com")
        self.assertEqual(page.eval_on_selector("#country", "e => e.dataset.picked"), "United Arab Emirates")
        self.assertEqual(page.eval_on_selector("#notice", "e => e.value"), "1 month")
        self.assertTrue(page.is_checked("input[name=yrs][value=b]"))  # 4-6 for 5 years
        self.assertEqual(page.eval_on_selector("#resume", "e => e.files[0].name"), self.cv.name)
        self.assertEqual(page.eval_on_selector("#cover", "e => e.files.length"), 0)

    def test_cover_letter_attached_when_one_exists(self):
        cover = self.cv.with_name("Acme - Senior Flutter Developer - Cover Letter.pdf")
        cover.write_bytes(b"%PDF-1.4 cover")
        page = self.browser.new_page()
        fields = runner.open_form(page, MOCK)
        res = runner.fill(page, fields, {"_resume_path": str(self.cv), "_cover_path": str(cover)}, {})
        self.assertEqual(page.eval_on_selector("#resume", "e => e.files[0].name"), self.cv.name)
        self.assertEqual(page.eval_on_selector("#cover", "e => e.files[0].name"), cover.name)

    def test_never_submits_or_consents_or_answers_demographics(self):
        page, _ = self.run_fill()
        self.assertFalse(page.evaluate("window.submitted"))
        self.assertFalse(page.is_checked("#consent"))
        self.assertFalse(page.is_checked("input[name=g][value=m]"))
        self.assertFalse(page.is_checked("input[name=g][value=f]"))

    def test_open_question_highlighted_then_filled_from_answers(self):
        page, res = self.run_fill()
        q = next(u["question"] for u in res.plan.unanswered if "join us" in u["question"])
        self.assertEqual(page.eval_on_selector("#why", "e => e.hasAttribute('data-jp-todo')"), True)
        self.assertTrue(page.locator("#jp-banner").is_visible())
        page2, _ = self.run_fill({q: "Because of the Flutter work."})
        self.assertEqual(page2.input_value("#why"), "Because of the Flutter work.")


    def test_wait_for_form_finds_form_in_a_new_tab(self):
        ctx = self.browser.new_context()
        listing = ctx.new_page()
        listing.set_content("<h1>Job listing</h1><a href='#'>Apply</a>")  # aggregator page, no form
        self.assertFalse(runner._has_form(runner.discover(listing)))
        form_tab = ctx.new_page()                                          # what the human's click opens
        form_tab.goto(MOCK)
        page, fields = runner.wait_for_form(ctx, timeout_s=10, poll_s=0.2)
        self.assertEqual(page, form_tab)
        self.assertTrue(runner._has_form(fields))
        ctx.close()

    def test_refill_keeps_what_the_human_typed(self):
        page, _ = self.run_fill()
        page.fill("#why", "My own words.")
        page.fill("#email", "changed@example.com")
        res = runner.fill(page, runner.discover(page), {"email": "t@example.com", "_resume_path": str(self.cv)}, {})
        self.assertEqual(page.input_value("#why"), "My own words.")
        self.assertEqual(page.input_value("#email"), "changed@example.com")
        self.assertNotIn("resume", [a.why for a in res.plan.actions])  # already attached: no re-upload


    def test_never_touches_linkedin_even_if_open_in_the_window(self):
        ctx = self.browser.new_context()
        html = (Path(__file__).resolve().parent / "fixtures" / "mock_form.html").read_text()
        # Fake LinkedIn served locally (no network): a page with a fillable form on it.
        ctx.route("https://www.linkedin.com/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        li = ctx.new_page()
        li.goto("https://www.linkedin.com/jobs/view/123/apply")
        page, fields = runner.wait_for_form(ctx, timeout_s=1, poll_s=0.2)
        self.assertIsNone(page)                                    # ignored, not picked up
        with self.assertRaises(runner.RestrictedSite):
            runner.fill(li, runner.discover(li), {"email": "t@example.com"}, {})
        self.assertEqual(li.input_value("#email"), "")             # nothing typed
        with self.assertRaises(runner.RestrictedSite):
            runner.open_form(li, "https://www.linkedin.com/jobs/view/123")
        ctx.close()


    def test_confirmation_page_detected_only_after_form_is_gone(self):
        page = self.browser.new_page()
        runner.open_form(page, MOCK)
        form_url = page.url
        # Text alone next to a still-open form must not count
        page.evaluate("document.body.insertAdjacentHTML('beforeend', '<p>Application received by recruiters within 2 days.</p>')")
        self.assertFalse(runner.submitted(page, form_url))
        # After submit the site shows a thank-you page without the form
        page.set_content("<h1>Thank you for applying!</h1><p>We have received your application.</p>")
        self.assertTrue(runner.submitted(page, form_url))


if __name__ == "__main__":
    unittest.main()
