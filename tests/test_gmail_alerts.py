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

    def test_mail_without_job_links_gives_nothing(self):
        # welcome mails and verification codes stay unread
        _, jobs = parse_message(self.mail("Bayt <alerts@bayt.com>", "whatever", "<a href='https://www.bayt.com/en/help/'>Help</a>"))
        self.assertEqual(jobs, [])

    def test_link_parser_any_platform(self):
        html = ("<a href='https://www.gulftalent.com/candidates/home'>Alex</a>"
                "<a href='https://www.gulftalent.com/uae/jobs/flutter-developer-643017?utm=x'><b>Flutter Developer</b>"
                "<span>Acme</span><span>Dubai, UAE</span><span>3 days ago</span><span>Easy Apply</span></a>"
                "<a href='https://www.gulftalent.com/uae/jobs/flutter-developer-643017'>Flutter Developer</a>"
                "<a href='https://u1.ct.sendgrid.net/ls/click?upn=abc'><div>Senior Flutter Engineer</div><div>Beta</div>"
                "<div>Remote</div></a>"
                "<a href='https://www.gulftalent.com/unsubscribe'>here</a>")
        resolver = lambda u: "https://app.welcometothejungle.com/jobs/xyz?ref=mail"
        _, jobs = parse_message(self.mail("GulfTalent <jobs@gulftalent.com>", "", html), resolver=resolver)
        self.assertEqual([(j.title, j.company, j.location) for j in jobs],
                         [("Flutter Developer", "Acme", "Dubai, UAE"), ("Senior Flutter Engineer", "Beta", "Remote")])
        self.assertEqual(jobs[0].url, "https://www.gulftalent.com/uae/jobs/flutter-developer-643017")
        self.assertEqual(jobs[0].source, "email:gulftalent")
        self.assertTrue(jobs[0].posted_at)
        self.assertEqual(jobs[1].url, "https://app.welcometothejungle.com/jobs/xyz")
        self.assertTrue(jobs[1].remote)

    def test_html_only_linkedin_keeps_links(self):
        html = ("<table><tr><td>Flutter Developer</td></tr><tr><td>Acme</td></tr><tr><td>Dubai</td></tr>"
                "<tr><td><a href='https://www.linkedin.com/comm/jobs/view/123/?x=1'>View</a></td></tr></table>")
        m = MIMEText(html, "html")
        m["From"] = "jobalerts-noreply@linkedin.com"
        _, jobs = parse_message(m.as_bytes())
        self.assertEqual(jobs[0].url, "https://www.linkedin.com/jobs/view/123/")

    def test_indeed_sponsored_link_without_job_id(self):
        text = ("Indeed Job Alert\n1 new flutter developer vacancy in UAE\n\n"
                "Flutter Developer\nAcme - Dubai\nAED12,000 a month\nEasily apply\nBuild our app.\n3 days ago\n"
                "https://ae.indeed.com/pagead/clk/dl?mo=r&ad=abc123\n")
        jobs = parse_indeed(text)
        self.assertEqual([(j.title, j.company, j.location) for j in jobs], [("Flutter Developer", "Acme", "Dubai")])
        self.assertEqual(jobs[0].url, "https://ae.indeed.com/pagead/clk/dl?mo=r&ad=abc123")

    def test_seek_tracker_links_resolve_to_new_domain(self):
        html = ("<a href='https://email.s.seek.com.au/uni/ss/c/u001.abc'><span>Flutter Developer</span>"
                "<span>Acme</span><span>Sydney NSW (Hybrid)</span><span>Recently posted</span></a>")
        _, jobs = parse_message(self.mail("SEEK <noreply@s.seek.com.au>", "", html),
                                resolver=lambda u: "https://au.seek.com/job/95278039?token=x")
        self.assertEqual((jobs[0].source, jobs[0].url), ("email:seek", "https://au.seek.com/job/95278039"))


if __name__ == "__main__":
    unittest.main()
