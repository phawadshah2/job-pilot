import unittest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jobpilot.sources.gmail_alerts import parse_indeed, parse_linkedin, parse_message

FIX = Path(__file__).resolve().parent / "fixtures"


class IndeedAlertTest(unittest.TestCase):
    def test_jobs_extracted_with_clean_urls(self):
        jobs = parse_indeed((FIX / "indeed_alert.txt").read_text())
        self.assertEqual([j.title for j in jobs], ["Mobile App Developer", "Senior UI Web & Mobile App Developer", "Product Designer"])
        a = jobs[0]
        self.assertEqual((a.company, a.location), ("Appkasa", "Dubai"))
        self.assertEqual(a.url, "https://ae.indeed.com/viewjob?jk=d6cdadc4a520c388")
        self.assertEqual(a.salary, "AED2,700 - AED9,900 a month")
        self.assertIn("Connect apps to backends", a.description)
        self.assertNotIn("Easily apply", a.description)
        self.assertTrue(jobs[2].remote)


class LinkedInAlertTest(unittest.TestCase):
    def test_cards_extracted_header_and_footer_ignored(self):
        jobs = parse_linkedin((FIX / "linkedin_alert.txt").read_text())
        self.assertEqual([(j.title, j.company) for j in jobs],
                         [("Lead Software Engineer", "Virtusa"), ("Lead iOS Engineer", "Alexander Lyons Solutions")])
        self.assertEqual(jobs[0].location, "Dubai")
        self.assertEqual(jobs[1].url, "https://www.linkedin.com/jobs/view/4469107447/")


class MessageRoutingTest(unittest.TestCase):
    def mail(self, sender, text, html=None):
        m = MIMEMultipart("alternative")
        m["From"] = sender
        m.attach(MIMEText(text, "plain"))
        if html:
            m.attach(MIMEText(html, "html"))
        return m.as_bytes()

    def test_routes_by_sender(self):
        _, jobs = parse_message(self.mail("Indeed <donotreply@jobalert.indeed.com>", (FIX / "indeed_alert.txt").read_text()))
        self.assertEqual(len(jobs), 3)

    def test_unknown_sender_is_left_for_later(self):
        _, jobs = parse_message(self.mail("Bayt <alerts@bayt.com>", "whatever"))
        self.assertIsNone(jobs)

    def test_html_only_linkedin_keeps_links(self):
        html = ("<table><tr><td>Flutter Developer</td></tr><tr><td>Acme</td></tr><tr><td>Dubai</td></tr>"
                "<tr><td><a href='https://www.linkedin.com/comm/jobs/view/123/?x=1'>View</a></td></tr></table>")
        m = MIMEText(html, "html")
        m["From"] = "jobalerts-noreply@linkedin.com"
        _, jobs = parse_message(m.as_bytes())
        self.assertEqual(jobs[0].url, "https://www.linkedin.com/jobs/view/123/")


if __name__ == "__main__":
    unittest.main()
